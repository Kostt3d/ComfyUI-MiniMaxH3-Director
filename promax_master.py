"""Deterministic Take -> Commit master management for MiniMax H3 Promax.

A generated continuation is a TAKE until the user explicitly enables COMMIT.
Rejected takes never overwrite the series master and do not create numbered latent files.
"""
from __future__ import annotations

import os
import re

from safetensors.torch import load_file, save_file

import folder_paths
from comfy.nested_tensor import NestedTensor
from comfy_api.latest import io, ui, Types

from .promax_continuation import FPS, FORMAT_VERSION, _validate_cumulative

MASTER_ROOT = "Promax/masters"
TAKE_PREVIEW_ROOT = "Promax"


def _clean_master_name(value: str) -> str:
    raw = str(value or "MASTER").strip().replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return raw or "MASTER"


def _master_paths(master_name: str):
    name = _clean_master_name(master_name)
    relative = f"{MASTER_ROOT}/{name}.promaxlatent"
    full = os.path.join(folder_paths.get_output_directory(), *relative.split("/"))
    return full, relative


def _write_master_atomic(latent, master_name: str):
    video, audio, frames = _validate_cumulative(latent, "candidate_master")
    full_path, relative = _master_paths(master_name)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    tmp_path = f"{full_path}.tmp-{os.getpid()}"
    try:
        save_file(
            {
                "video": video.detach().contiguous().cpu(),
                "audio": audio.detach().contiguous().cpu(),
            },
            tmp_path,
            metadata={
                "format": FORMAT_VERSION,
                "fps": str(FPS),
                "frames": str(frames),
                "promax_role": "committed_master",
            },
        )
        os.replace(tmp_path, full_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return relative, frames


class MiniMaxH3PromaxCommitMaster(io.ComfyNode):
    """Commit a candidate latent to one deterministic MASTER file only when requested."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxCommitMaster",
            display_name="Promax · COMMIT Take → Master",
            category="MiniMax H3/Promax",
            description=(
                "Safe Take/Commit gate. COMMIT OFF leaves the existing Master untouched. "
                "COMMIT ON atomically replaces one deterministic MASTER.promaxlatent file."
            ),
            is_output_node=True,
            inputs=[
                io.Latent.Input("candidate_latent"),
                io.Boolean.Input(
                    "commit_take", default=False,
                    label_on="COMMIT TAKE", label_off="TAKE ONLY",
                    tooltip="Leave OFF while judging a take. Turn ON only for the take you want to keep."
                ),
                io.String.Input(
                    "master_name", default="MASTER",
                    tooltip="Single deterministic master name. Repeated commits replace this file instead of creating counters."
                ),
            ],
            outputs=[
                io.Latent.Output("candidate_latent"),
                io.String.Output("master_path"),
                io.String.Output("status"),
            ],
        )

    @classmethod
    def execute(cls, candidate_latent, commit_take=False, master_name="MASTER"):
        _, _, frames = _validate_cumulative(candidate_latent, "candidate_latent")
        _, relative = _master_paths(master_name)
        if not bool(commit_take):
            status = (
                f"TAKE ONLY | {frames}f candidate | Master untouched | "
                f"set COMMIT TAKE only after preview approval"
            )
            return io.NodeOutput(
                candidate_latent, relative, status,
                ui={"text": [status]},
            )

        relative, frames = _write_master_atomic(candidate_latent, master_name)
        status = f"COMMITTED | {frames}f ({frames / FPS:.3f}s) | {relative}"
        return io.NodeOutput(
            candidate_latent, relative, status,
            ui={"text": [status]},
        )


class MiniMaxH3PromaxLoadMaster(io.ComfyNode):
    """Load the one committed Promax MASTER latent for the next continuation."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxLoadMaster",
            display_name="Promax · Load Master",
            category="MiniMax H3/Promax",
            description="Loads the last explicitly committed Master. Rejected takes can never be loaded here.",
            inputs=[
                io.String.Input("master_name", default="MASTER"),
            ],
            outputs=[
                io.Latent.Output("master_latent"),
                io.String.Output("master_path"),
                io.String.Output("report"),
            ],
        )

    @classmethod
    def execute(cls, master_name="MASTER"):
        path, relative = _master_paths(master_name)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Promax Master '{relative}' does not exist yet. Render the first clip and COMMIT it first."
            )
        tensors = load_file(path, device="cpu")
        if "video" not in tensors or "audio" not in tensors:
            raise ValueError("Promax Master is not a valid H3 AV latent file.")
        latent = {"samples": NestedTensor((tensors["video"], tensors["audio"]))}
        _, _, frames = _validate_cumulative(latent, "master_latent")
        report = f"MASTER LOADED | {frames}f ({frames / FPS:.3f}s) | {relative}"
        return io.NodeOutput(latent, relative, report, ui={"text": [report]})


class MiniMaxH3PromaxPreviewTake(io.ComfyNode):
    """Preview one current Take in ComfyUI/temp, overwriting the same file every run."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxPreviewTake",
            display_name="Promax · Preview CURRENT TAKE",
            category="MiniMax H3/Promax",
            description=(
                "Temporary video preview. Every run overwrites the same CURRENT_TAKE.mp4 in ComfyUI/temp, "
                "so rejected Takes do not fill the output folder."
            ),
            is_output_node=True,
            inputs=[
                io.Video.Input("video"),
                io.String.Input("preview_name", default="CURRENT_TAKE"),
            ],
            outputs=[io.Video.Output("video")],
        )

    @classmethod
    def execute(cls, video, preview_name="CURRENT_TAKE"):
        name = _clean_master_name(preview_name)
        subfolder = TAKE_PREVIEW_ROOT
        folder = os.path.join(folder_paths.get_temp_directory(), subfolder)
        os.makedirs(folder, exist_ok=True)
        file_name = f"{name}.mp4"
        full_path = os.path.join(folder, file_name)
        video.save_to(
            full_path,
            format=Types.VideoContainer("mp4"),
            codec="auto",
            preset="ultrafast",
        )
        return io.NodeOutput(
            video,
            ui=ui.PreviewVideo([ui.SavedResult(file_name, subfolder, io.FolderType.temp)]),
        )
