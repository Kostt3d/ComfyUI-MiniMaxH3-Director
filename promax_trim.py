"""Decoded media helpers for MiniMax H3 Promax continuation."""
from __future__ import annotations

from collections.abc import Mapping

from comfy_api.latest import io

FPS = 24


class MiniMaxH3PromaxTrimMedia(io.ComfyNode):
    """Remove the hidden latent-continuation overlap after video/audio decoding."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxTrimMedia",
            display_name="Promax · Trim Continuation Media",
            category="MiniMax H3/Promax",
            inputs=[
                io.Image.Input("images"),
                io.Audio.Input("audio"),
                io.Int.Input("trim_frames", force_input=True, min=0),
            ],
            outputs=[io.Image.Output("images"), io.Audio.Output("audio")],
        )

    @classmethod
    def execute(cls, images, audio, trim_frames):
        trim = int(trim_frames)
        frame_count = int(images.shape[0])
        if trim < 0 or trim >= frame_count:
            raise ValueError(
                f"Promax continuation: trim_frames={trim} is invalid for {frame_count} decoded frames."
            )
        if not isinstance(audio, Mapping) or "waveform" not in audio or "sample_rate" not in audio:
            raise TypeError("Promax continuation: decoded audio must contain waveform and sample_rate.")
        sr = int(audio["sample_rate"])
        if sr <= 0:
            raise ValueError("Promax continuation: decoded audio sample rate must be positive.")
        cut = round(trim / FPS * sr)
        waveform = audio["waveform"]
        if cut >= waveform.shape[-1]:
            raise ValueError("Promax continuation: overlap trim would remove the whole decoded audio stream.")
        trimmed_audio = dict(audio)
        trimmed_audio["waveform"] = waveform[..., cut:]
        return io.NodeOutput(images[trim:], trimmed_audio)
