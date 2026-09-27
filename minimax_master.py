"""Cumulative episode master chain for MiniMax H3 Director 12 GB workflows.

The latent chain solves model continuity between clips. This node solves the second half of
production: keeping a growing, watchable episode master on disk without manually wiring a
new stitch graph for every generation.

Workflow contract:
- clip 1: reset_chain=True -> current clip passes through as the first master;
- clip 2+: reset_chain=False -> newest saved master is loaded from ComfyUI/output and
  SatoDive's overlap-aware continuation stitch appends the current clip;
- the returned VIDEO is saved by the ordinary SaveVideo node. On the next execution that
  saved file becomes the previous master automatically.

No video is used for H3 continuation itself. The lossless AV latent remains the continuity
source. This master chain exists only for review/export/publishing.
"""

from __future__ import annotations

import logging
import os
from fractions import Fraction

import folder_paths
from comfy_api.latest import InputImpl, Types, io


log = logging.getLogger(__name__)

H3Context = io.Custom("MINIMAX_H3_CONTEXT")
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _master_files(prefix: str) -> list[str]:
    """Saved cumulative masters matching ``prefix``, newest first."""
    root = folder_paths.get_output_directory()
    needle = str(prefix or "").strip().lower()
    found = []
    for directory, _dirs, files in os.walk(root):
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() not in _VIDEO_EXTENSIONS:
                continue
            if needle and needle not in stem.lower():
                continue
            path = os.path.join(directory, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            found.append((mtime, path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in found]


def _stitch_node_class():
    import nodes

    node_class = nodes.NODE_CLASS_MAPPINGS.get(
        "MiniMaxH3EasyStitchContinuation_SatoDive"
    )
    if node_class is None:
        raise RuntimeError(
            "Install and enable SatoDive/Minimax-H3-Latent-Continuation, then restart ComfyUI."
        )
    return node_class


def _video_from_components(frames, audio, fps):
    frame_rate = Fraction(str(float(fps))).limit_denominator(100000)
    return InputImpl.VideoFromComponents(
        Types.VideoComponents(images=frames, audio=audio, frame_rate=frame_rate),
        bit_depth=8,
        color_space="sRGB",
    )


class MiniMaxH3DirectorMasterChain(io.ComfyNode):
    """Append the current continuation to the newest saved cumulative episode master."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3DirectorMasterChainCS",
            display_name="MiniMax H3 Director 12GB Master Chain",
            category="MiniMax H3 Director",
            description=(
                "Builds the growing episode master automatically. Clip 1: leave Reset Chain ON. "
                "Clip 2+: turn Reset Chain OFF. The node loads the newest master saved under the "
                "chosen prefix, trims Native Guide overlap with SatoDive, fixes small colour drift, "
                "preserves source audio through the overlap, and appends the current clip."
            ),
            inputs=[
                io.Video.Input(
                    "current_video",
                    tooltip="The freshly decoded H3 clip for this run."
                ),
                H3Context.Input(
                    "h3_context",
                    optional=True,
                    tooltip=(
                        "Connect H3 Director 12GB Engine -> h3_context. The actual continuation "
                        "window is read from it automatically, including context_length=5."
                    ),
                ),
                io.Boolean.Input(
                    "reset_chain",
                    default=True,
                    label_on="START NEW MASTER",
                    label_off="APPEND TO MASTER",
                    tooltip=(
                        "ON for GEN01: ignore older master files and pass this clip through as the "
                        "new master. OFF for GEN02+: append this clip to the newest saved master."
                    ),
                ),
                io.String.Input(
                    "master_prefix",
                    default="H3_Director_12GB_MASTER",
                    tooltip=(
                        "Only output videos whose filename contains this prefix are considered "
                        "previous masters. Use a different prefix for each episode/series if needed."
                    ),
                ),
                io.Int.Input(
                    "master_back",
                    default=0,
                    min=0,
                    max=50,
                    advanced=True,
                    tooltip=(
                        "0 = newest master, 1 = previous one, etc. Useful when redoing a clip after "
                        "a master for that clip was already saved."
                    ),
                ),
                io.Int.Input(
                    "overlap_frames",
                    default=5,
                    min=0,
                    max=600,
                    advanced=True,
                    tooltip="Fallback only. h3_context overrides this with the real context window."
                ),
                io.Int.Input(
                    "search_frames",
                    default=8,
                    min=0,
                    max=300,
                    advanced=True,
                    tooltip="Auto-alignment search radius. 8 is intentionally tight for context=5."
                ),
                io.Int.Input(
                    "match_window",
                    default=5,
                    min=1,
                    max=60,
                    advanced=True,
                    tooltip="Frames compared at each candidate join. 5 matches the 12 GB preset."
                ),
                io.Combo.Input(
                    "window_audio",
                    options=["from source", "from continuation"],
                    default="from source",
                    advanced=True,
                ),
                io.Combo.Input(
                    "color_match",
                    options=["off", "mean", "mean + contrast"],
                    default="mean + contrast",
                    advanced=True,
                ),
            ],
            outputs=[
                io.Video.Output(display_name="master_video"),
                io.String.Output(display_name="report"),
            ],
            not_idempotent=True,
        )

    @classmethod
    def execute(
        cls,
        current_video,
        reset_chain=True,
        master_prefix="H3_Director_12GB_MASTER",
        master_back=0,
        overlap_frames=5,
        search_frames=8,
        match_window=5,
        window_audio="from source",
        color_match="mean + contrast",
        h3_context=None,
    ):
        if bool(reset_chain):
            report = (
                "MASTER CHAIN RESET: current clip becomes the new episode master. "
                "After saving GEN01, switch reset_chain OFF for GEN02+."
            )
            log.info("[MiniMaxDirectorMaster] %s", report)
            return io.NodeOutput(current_video, report)

        masters = _master_files(master_prefix)
        index = int(master_back)
        if not masters:
            report = (
                "No previous master matching %r was found in ComfyUI/output; "
                "current clip was used as the master." % str(master_prefix)
            )
            log.warning("[MiniMaxDirectorMaster] %s", report)
            return io.NodeOutput(current_video, report)
        if index >= len(masters):
            raise ValueError(
                "master_back=%d requested, but only %d master file(s) matching %r exist."
                % (index, len(masters), str(master_prefix))
            )

        previous_path = masters[index]
        previous_video = InputImpl.VideoFromFile(previous_path)

        stitch_class = _stitch_node_class()
        stitcher = stitch_class()
        frames, audio, fps, cut_frame, stitch_report = stitcher.stitch(
            previous_video,
            current_video,
            "auto",
            int(overlap_frames),
            int(search_frames),
            int(match_window),
            color_match=str(color_match),
            window_audio=str(window_audio),
            h3_context=h3_context,
        )
        master = _video_from_components(frames, audio, fps)
        report = (
            "Previous master: %s\n"
            "master_back: %d\n"
            "%s"
            % (os.path.relpath(previous_path, folder_paths.get_output_directory()), index, stitch_report)
        )
        log.info(
            "[MiniMaxDirectorMaster] appended current clip to %s | cut=%d",
            os.path.basename(previous_path),
            int(cut_frame),
        )
        return io.NodeOutput(master, report)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3DirectorMasterChainCS": MiniMaxH3DirectorMasterChain,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3DirectorMasterChainCS": "MiniMax H3 Director 12GB Master Chain",
}
