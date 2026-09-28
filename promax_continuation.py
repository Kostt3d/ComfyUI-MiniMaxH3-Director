"""Native H3 latent continuation and AV latent persistence for MiniMax H3 Promax.

This module is intentionally self-contained inside Promax:
- no SatoDive/KJ/SageAttention dependency;
- continuation uses ComfyUI's native MiniMax H3 arbitrary-frame guide support;
- save/load handles H3 NestedTensor video+audio latents safely.
"""
from __future__ import annotations

import inspect
import os
from collections.abc import Mapping

import torch
import folder_paths
import node_helpers
from safetensors.torch import load_file, save_file

from comfy.nested_tensor import NestedTensor
from comfy_api.latest import io

FPS = 24
AUDIO_LATENT_FPS = 40
VIDEO_CHANNELS = 24
AUDIO_LATENT_CHANNELS = 32
AUDIO_CHANNELS = 2
MAX_NATIVE_WINDOW_FRAMES = 362
FORMAT_VERSION = "promax_h3_av_v1"


def _video_tokens_for_frames(frame_count: int) -> int:
    frame_count = int(frame_count)
    if frame_count < 5 or frame_count % 17 != 5:
        raise ValueError("Promax continuation: frame count must satisfy 17k+5 and be >= 5.")
    return 2 if frame_count == 5 else ((frame_count - 5) // 17) * 5 + 2


def _frames_for_video_tokens(video_tokens: int) -> int:
    video_tokens = int(video_tokens)
    if video_tokens < 2 or (video_tokens - 2) % 5:
        raise ValueError("Promax continuation: H3 video latent length must satisfy 5k+2.")
    return ((video_tokens - 2) // 5) * 17 + 5


def _audio_boundary(frame_index: int) -> int:
    if int(frame_index) < 0:
        raise ValueError("Promax continuation: negative audio boundary.")
    return round(int(frame_index) * AUDIO_LATENT_FPS / FPS)


def _parts(latent, name: str):
    if not isinstance(latent, Mapping) or "samples" not in latent:
        raise TypeError(f"{name} must be a LATENT mapping containing 'samples'.")
    samples = latent["samples"]
    if not getattr(samples, "is_nested", False):
        raise TypeError(f"{name} must be a MiniMax H3 nested AV latent.")
    streams = list(samples.unbind())
    if len(streams) != 2:
        raise ValueError(f"{name} must contain video and audio streams.")
    video, audio = streams
    if video.ndim != 5 or video.shape[0] != 1 or video.shape[1] != VIDEO_CHANNELS:
        raise ValueError(f"{name}: expected video [1,{VIDEO_CHANNELS},T,H,W], got {tuple(video.shape)}.")
    if audio.ndim != 4 or audio.shape[0] != 1 or audio.shape[1] != AUDIO_LATENT_CHANNELS or audio.shape[2] != AUDIO_CHANNELS:
        raise ValueError(
            f"{name}: expected audio [1,{AUDIO_LATENT_CHANNELS},{AUDIO_CHANNELS},T], got {tuple(audio.shape)}."
        )
    frames = _frames_for_video_tokens(video.shape[2])
    return video, audio, frames


def _validate_cumulative(latent, name: str):
    video, audio, frames = _parts(latent, name)
    expected = _audio_boundary(frames)
    if audio.shape[-1] != expected:
        raise ValueError(
            f"{name}: audio/video timeline mismatch, expected {expected} audio tokens for {frames} frames, "
            f"got {audio.shape[-1]}."
        )
    return video, audio, frames


def _require_native_guides():
    """Fail early on ComfyUI builds predating arbitrary-position H3 guides."""
    try:
        from comfy.ldm.minimax.model import PackedLayout
    except Exception as exc:
        raise RuntimeError("Promax continuation: MiniMax H3 native support is unavailable in this ComfyUI build.") from exc
    params = inspect.signature(PackedLayout.__init__).parameters
    if "frame_count" in params:
        raise RuntimeError(
            "Promax continuation requires a newer ComfyUI build with native arbitrary-frame MiniMax H3 guides "
            "(ComfyUI commit e01fb4c or newer)."
        )


def _existing_keyframes(positive):
    if not isinstance(positive, list) or not positive:
        raise TypeError("Promax continuation: positive must be a non-empty CONDITIONING.")
    first = positive[0]
    if not isinstance(first, (list, tuple)) or len(first) < 2 or not isinstance(first[1], Mapping):
        raise TypeError("Promax continuation: malformed CONDITIONING metadata.")
    raw = first[1].get("minimax_keyframes", [])
    if not isinstance(raw, (list, tuple)):
        raise TypeError("Promax continuation: minimax_keyframes must be a list.")
    return [dict(item) for item in raw]


def _continuation_layout(previous_frames: int, overlap_frames: int, extension_frames: int):
    overlap_frames = int(overlap_frames)
    extension_frames = int(extension_frames)
    if overlap_frames < 5 or overlap_frames % 17 != 5:
        raise ValueError("Promax continuation: overlap_frames must satisfy 17k+5 (5, 22, 39, ...).")
    if overlap_frames > previous_frames:
        raise ValueError("Promax continuation: overlap cannot exceed the previous clip.")
    if extension_frames < 17 or extension_frames % 17:
        raise ValueError("Promax continuation: extension_frames must be a positive multiple of 17.")
    window_frames = overlap_frames + extension_frames
    if window_frames > MAX_NATIVE_WINDOW_FRAMES:
        raise ValueError(
            f"Promax continuation: overlap + extension = {window_frames} frames. "
            f"Keep the continuation window <= {MAX_NATIVE_WINDOW_FRAMES} frames for the H3 trained range."
        )
    overlap_video = _video_tokens_for_frames(overlap_frames)
    new_video = extension_frames // 17 * 5
    if overlap_video + new_video != _video_tokens_for_frames(window_frames):
        raise ValueError("Promax continuation: invalid H3 temporal grid.")
    prev_audio_end = _audio_boundary(previous_frames)
    overlap_audio_start = _audio_boundary(previous_frames - overlap_frames)
    extended_audio_end = _audio_boundary(previous_frames + extension_frames)
    return {
        "window_frames": window_frames,
        "overlap_video": overlap_video,
        "new_video": new_video,
        "overlap_audio": prev_audio_end - overlap_audio_start,
        "new_audio": extended_audio_end - prev_audio_end,
    }


class MiniMaxH3PromaxContinue(io.ComfyNode):
    """Prepare a fresh H3 AV window and guide it with the previous generated latent tail."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxContinue",
            display_name="Promax · Latent Continuation",
            category="MiniMax H3/Promax",
            description=(
                "Native H3 AV continuation. Uses a hidden overlap from the previous sampled latent as a "
                "frame-zero guide, without decoding/re-encoding the previous clip."
            ),
            is_experimental=True,
            inputs=[
                io.Conditioning.Input("positive"),
                io.Latent.Input("previous_latent"),
                io.Int.Input(
                    "overlap_frames", default=22, min=5, max=345, step=17,
                    tooltip="22 frames (~0.92 s) is the recommended starting point for motion continuity."
                ),
                io.Int.Input(
                    "extension_frames", default=119, min=17, max=357, step=17,
                    tooltip="Visible new frames. 119 is ~4.96 s at 24 fps. overlap + extension must stay <= 362."
                ),
            ],
            outputs=[
                io.Conditioning.Output("positive"),
                io.Latent.Output("latent"),
                io.Int.Output("overlap_video_tokens"),
                io.Int.Output("overlap_audio_tokens"),
                io.Int.Output("visible_frames"),
                io.Int.Output("window_frames"),
                io.String.Output("report"),
            ],
        )

    @classmethod
    def execute(cls, positive, previous_latent, overlap_frames=22, extension_frames=119):
        _require_native_guides()
        prev_video, prev_audio, prev_frames = _validate_cumulative(previous_latent, "previous_latent")
        layout = _continuation_layout(prev_frames, overlap_frames, extension_frames)

        target_video = prev_video.new_zeros(
            (1, VIDEO_CHANNELS, layout["overlap_video"] + layout["new_video"],
             prev_video.shape[3], prev_video.shape[4])
        )
        target_audio = prev_audio.new_zeros(
            (1, AUDIO_LATENT_CHANNELS, AUDIO_CHANNELS,
             layout["overlap_audio"] + layout["new_audio"])
        )
        target = {"samples": NestedTensor((target_video, target_audio))}

        guide_frames = int(overlap_frames)
        keyframes = _existing_keyframes(positive)
        for item in keyframes:
            position = item.get("resolved_frame_index")
            if isinstance(position, (int, float)) and 0 <= float(position) < guide_frames:
                raise ValueError(
                    "Promax continuation: the next-shot conditioning already contains a guide inside the hidden "
                    "overlap. Use T2V for the continuation prompt and leave first_frame/last_frame disconnected."
                )
        keyframes.append({
            "resolved_frame_index": 0,
            "latent": prev_video[:, :, -layout["overlap_video"]:].clone(),
            "audio_latent": prev_audio[..., -layout["overlap_audio"]:].clone(),
        })
        keyframes.sort(key=lambda item: float(item["resolved_frame_index"]))
        guided = node_helpers.conditioning_set_values(positive, {"minimax_keyframes": keyframes})

        report = (
            f"Promax continuation | previous {prev_frames}f | hidden overlap {overlap_frames}f "
            f"| visible extension {extension_frames}f ({extension_frames / FPS:.3f}s) "
            f"| sampled window {layout['window_frames']}f"
        )
        return io.NodeOutput(
            guided, target, layout["overlap_video"], layout["overlap_audio"],
            int(extension_frames), layout["window_frames"], report
        )


class MiniMaxH3PromaxAppend(io.ComfyNode):
    """Drop the hidden overlap and append only new AV tokens to the cumulative latent."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxAppend",
            display_name="Promax · Append Continuation",
            category="MiniMax H3/Promax",
            is_experimental=True,
            inputs=[
                io.Latent.Input("previous_latent"),
                io.Latent.Input("sampled_window"),
                io.Int.Input("overlap_video_tokens", force_input=True, min=1),
                io.Int.Input("overlap_audio_tokens", force_input=True, min=1),
            ],
            outputs=[
                io.Latent.Output("cumulative_latent"),
                io.Latent.Output("sampled_window"),
                io.Int.Output("total_frames"),
                io.Int.Output("trim_overlap_frames"),
                io.String.Output("report"),
            ],
        )

    @classmethod
    def execute(cls, previous_latent, sampled_window, overlap_video_tokens, overlap_audio_tokens):
        prev_video, prev_audio, prev_frames = _validate_cumulative(previous_latent, "previous_latent")
        win_video, win_audio, _ = _parts(sampled_window, "sampled_window")
        ov = int(overlap_video_tokens)
        oa = int(overlap_audio_tokens)
        if ov <= 0 or ov >= win_video.shape[2]:
            raise ValueError("Promax continuation: invalid overlap_video_tokens.")
        if oa <= 0 or oa >= win_audio.shape[-1]:
            raise ValueError("Promax continuation: invalid overlap_audio_tokens.")
        overlap_frames = _frames_for_video_tokens(ov)
        if overlap_frames > prev_frames:
            raise ValueError("Promax continuation: overlap is longer than the previous latent.")
        expected_oa = _audio_boundary(prev_frames) - _audio_boundary(prev_frames - overlap_frames)
        if oa != expected_oa:
            raise ValueError("Promax continuation: video/audio overlap tokens are not synchronized.")

        # Keep the cumulative latent on the previous latent's device. A loaded latent is normally CPU-resident,
        # which avoids copying the entire history back into 12 GB of VRAM merely to append a few new tokens.
        win_video_for_append = win_video.to(device=prev_video.device, dtype=prev_video.dtype)
        win_audio_for_append = win_audio.to(device=prev_audio.device, dtype=prev_audio.dtype)
        new_video = win_video_for_append[:, :, ov:]
        new_audio = win_audio_for_append[..., oa:]
        if new_video.shape[2] <= 0 or new_video.shape[2] % 5:
            raise ValueError("Promax continuation: new video token count must be a positive multiple of 5.")

        merged_video = torch.cat((prev_video, new_video), dim=2)
        merged_audio = torch.cat((prev_audio, new_audio), dim=-1)
        total_frames = _frames_for_video_tokens(merged_video.shape[2])
        expected_audio = _audio_boundary(total_frames)
        if merged_audio.shape[-1] != expected_audio:
            raise ValueError(
                f"Promax continuation: appended audio timeline mismatch, expected {expected_audio}, "
                f"got {merged_audio.shape[-1]}."
            )
        cumulative = {"samples": NestedTensor((merged_video, merged_audio))}
        report = (
            f"Promax append | previous {prev_frames}f + new {total_frames - prev_frames}f "
            f"= {total_frames}f ({total_frames / FPS:.3f}s)"
        )
        return io.NodeOutput(cumulative, sampled_window, total_frames, overlap_frames, report)


class MiniMaxH3PromaxSaveLatent(io.ComfyNode):
    """Save a MiniMax H3 nested AV latent without core SaveLatent's NestedTensor limitation."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxSaveLatent",
            display_name="Promax · Save AV Latent",
            category="MiniMax H3/Promax",
            is_output_node=True,
            inputs=[
                io.Latent.Input("latent"),
                io.String.Input("filename_prefix", default="Promax/latents/h3"),
            ],
            outputs=[io.Latent.Output("latent"), io.String.Output("file_path")],
        )

    @classmethod
    def execute(cls, latent, filename_prefix="Promax/latents/h3"):
        video, audio, frames = _validate_cumulative(latent, "latent")
        full, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory()
        )
        file_name = f"{filename}_{counter:05}_.promaxlatent"
        full_path = os.path.join(full, file_name)
        save_file(
            {
                "video": video.detach().contiguous().cpu(),
                "audio": audio.detach().contiguous().cpu(),
            },
            full_path,
            metadata={
                "format": FORMAT_VERSION,
                "fps": str(FPS),
                "frames": str(frames),
            },
        )
        relative = os.path.join(subfolder, file_name).replace("\\", "/")
        return io.NodeOutput(latent, relative, ui={"text": [f"Saved {relative}"]})


def _resolve_saved_path(file_path: str):
    raw = str(file_path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("Promax load latent: file_path is empty.")
    if os.path.isabs(raw) or ".." in raw.split("/"):
        raise ValueError("Promax load latent: use a relative path inside ComfyUI input/ or output/.")
    candidates = [
        os.path.join(folder_paths.get_output_directory(), raw),
        os.path.join(folder_paths.get_input_directory(), raw),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(f"Promax load latent: '{raw}' was not found in ComfyUI output/ or input/.")


class MiniMaxH3PromaxLoadLatent(io.ComfyNode):
    """Reload a Promax H3 AV latent saved by Promax · Save AV Latent."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxLoadLatent",
            display_name="Promax · Load AV Latent",
            category="MiniMax H3/Promax",
            inputs=[
                io.String.Input(
                    "file_path",
                    default="Promax/latents/h3_00001_.promaxlatent",
                    tooltip="Relative to ComfyUI output/ or input/."
                )
            ],
            outputs=[io.Latent.Output("latent"), io.String.Output("report")],
        )

    @classmethod
    def execute(cls, file_path):
        path = _resolve_saved_path(file_path)
        tensors = load_file(path, device="cpu")
        if "video" not in tensors or "audio" not in tensors:
            raise ValueError("Promax load latent: file does not contain the expected H3 video/audio streams.")
        latent = {"samples": NestedTensor((tensors["video"], tensors["audio"]))}
        _, _, frames = _validate_cumulative(latent, "loaded_latent")
        report = f"Promax latent loaded | {frames}f ({frames / FPS:.3f}s) | {os.path.basename(path)}"
        return io.NodeOutput(latent, report)
