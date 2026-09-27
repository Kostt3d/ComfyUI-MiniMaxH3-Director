"""Bridge the Director storyboard to SatoDive's native AV segment engine.

The same bridge can now render both the first clip and later latent continuations:
- leave ``seed_latent`` unconnected for the first clip;
- connect a latent saved by SatoDive for clip 2 onward.

This deliberately delegates H3 conditioning/context planning to the installed SatoDive
extension. It does not decode and re-encode a previous clip.

Performance notes:
- use the Director's ``compile_only`` mode so Director does not run a duplicate
  Qwen/VAE/H3 conditioning pass before SatoDive;
- ``ref_image_size=match`` is the 12 GB default. It follows the output pixel area and
  avoids inflating every reference image to ~1 MP on a 480x864 render;
- optional low-VRAM cleanup releases Python/CUDA cache garbage before SatoDive builds
  the context;
- profiling logs timing, media count and CUDA memory so slowdowns are diagnosable.
"""

from __future__ import annotations

import gc
import logging
import re
import time

from comfy_api.latest import io

try:
    import torch
except Exception:  # pragma: no cover - ComfyUI always has torch; tests may stub it out
    torch = None


log = logging.getLogger(__name__)

H3Bundle = io.Custom("MINIMAX_H3_BUNDLE")
H3Context = io.Custom("MINIMAX_H3_CONTEXT")


_SHOT_RANGE_RE = re.compile(
    r"\[\s*\d+(?:\.\d+)?\s*s?\s*-\s*(\d+(?:\.\d+)?)\s*s?\s*\]",
    re.IGNORECASE,
)


def _director_scene_seconds(director_prompt):
    """Return the end time of the last timed shot in a compiled Director prompt."""
    ends = [float(value) for value in _SHOT_RANGE_RE.findall(str(director_prompt))]
    if not ends:
        raise ValueError(
            "Seconds is 0 (Director duration mode), but the Director prompt contains no "
            "timed [start-end] shot markers. Set seconds manually, or connect the compiled "
            "prompt output from MiniMax H3 Director."
        )
    seconds = max(ends)
    if seconds <= 0:
        raise ValueError("Director scene duration must be greater than zero.")
    return seconds


def _context_node():
    import nodes

    node_class = nodes.NODE_CLASS_MAPPINGS.get("MiniMaxH3EasyContextSegments_SatoDive")
    if node_class is None:
        raise RuntimeError(
            "Install and enable SatoDive/Minimax-H3-Latent-Continuation, then restart ComfyUI."
        )
    return node_class


def _cuda_memory_text():
    if torch is None or not getattr(torch, "cuda", None) or not torch.cuda.is_available():
        return "CUDA n/a"
    try:
        allocated = torch.cuda.memory_allocated() / (1024 ** 3)
        reserved = torch.cuda.memory_reserved() / (1024 ** 3)
        peak = torch.cuda.max_memory_allocated() / (1024 ** 3)
        return "VRAM %.2fG alloc / %.2fG reserved / %.2fG peak" % (allocated, reserved, peak)
    except Exception:
        return "CUDA stats unavailable"


def _low_vram_cleanup():
    """Release dead Python objects and unused CUDA cache without unloading live models."""
    gc.collect()
    if torch is None or not getattr(torch, "cuda", None) or not torch.cuda.is_available():
        return
    try:
        torch.cuda.empty_cache()
    except Exception:
        pass


class MiniMaxH3DirectorContinuation(io.ComfyNode):
    """Compile a Director scene through SatoDive, optionally continuing a saved latent."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3DirectorContinuationCS",
            display_name="MiniMax H3 Director 12GB Engine",
            category="MiniMax H3 Director",
            description=(
                "Fast Director -> SatoDive execution bridge. Leave seed_latent unconnected "
                "for clip 1; connect a saved H3 latent for clip 2 onward. For best 12 GB "
                "performance enable Director compile_only + auto_prune_refs and connect scene."
            ),
            inputs=[
                H3Bundle.Input("h3_bundle"),
                io.String.Input("director_prompt", force_input=True),
                io.Int.Input("width", force_input=True, min=32, max=8192),
                io.Int.Input("height", force_input=True, min=32, max=8192),
                io.Float.Input(
                    "seconds", default=0.0, min=0.0, max=120.0,
                    tooltip=(
                        "0 = use this Director scene's timeline duration. Set above 0 only "
                        "to override Director timing."
                    ),
                ),
                io.Float.Input("fps", default=24.0, min=1.0, max=120.0),
                io.Int.Input(
                    "context_length", default=22, min=5, max=73, step=17,
                    tooltip=(
                        "Continuation only. Native H3 guide window. 22 is the production "
                        "default; use 5 only to diagnose memory pressure."
                    ),
                ),
                io.Combo.Input(
                    "ref_image_size",
                    options=["match", "1k", "1.5k", "2k", "original"],
                    default="match",
                    tooltip=(
                        "Reference resize policy. 'match' follows the output pixel area and "
                        "is strongly recommended on 12 GB GPUs."
                    ),
                ),
                io.Latent.Input(
                    "seed_latent", optional=True,
                    tooltip=(
                        "Leave unconnected for the first clip. For continuation, connect a "
                        "latent produced by MiniMax H3 Save Latent."
                    ),
                ),
                io.Video.Input("seed_video", optional=True),
                io.Custom("MINIMAX_H3_DIRECTOR_SCENE").Input(
                    "scene", optional=True,
                    tooltip=(
                        "Connect Director.scene to inherit exact prompt, duration, canvas, FPS "
                        "and auto-pruned refs. Enable Director compile_only to avoid duplicate "
                        "conditioning."
                    ),
                ),
                io.Boolean.Input(
                    "low_vram_cleanup", default=True, optional=True, advanced=True,
                    label_on="Cleanup before context",
                    label_off="No cleanup",
                    tooltip=(
                        "Runs Python GC + torch.cuda.empty_cache before SatoDive context setup. "
                        "It does not unload live models; it only releases dead/cache allocations."
                    ),
                ),
                io.Boolean.Input(
                    "profile", default=True, optional=True, advanced=True,
                    label_on="Profile",
                    label_off="Quiet",
                    tooltip="Log setup time, reference count and VRAM usage to the ComfyUI console.",
                ),
            ],
            outputs=[
                io.Model.Output(display_name="model"),
                H3Context.Output(display_name="h3_context"),
            ],
        )

    @classmethod
    def execute(
        cls, h3_bundle, director_prompt, width, height,
        seconds=0.0, fps=24.0, context_length=22, ref_image_size="match",
        seed_latent=None, seed_video=None, scene=None,
        low_vram_cleanup=True, profile=True,
    ):
        started = time.perf_counter()
        media_inputs = {}
        scene_compile_only = False
        active_refs = ()
        pruned_refs = ()

        if scene is not None:
            if not isinstance(scene, dict):
                raise ValueError("Director scene payload is invalid; reconnect Director.scene.")
            director_prompt = scene["prompt"]
            width, height = scene["width"], scene["height"]
            fps = scene["fps"]
            seconds = scene["seconds"]
            scene_compile_only = bool(scene.get("compile_only"))
            active_refs = tuple(scene.get("active_ref_slots") or ())
            pruned_refs = tuple(scene.get("pruned_ref_slots") or ())
            for index, (kind, value) in enumerate(scene.get("media", ()), 1):
                media_inputs[f"media_{index}"] = value
                media_inputs[f"media_type_{index}"] = kind
            if not scene.get("media"):
                media_inputs["first_frame"] = scene.get("first_frame")
                media_inputs["last_frame"] = scene.get("last_frame")

        if not str(director_prompt).strip():
            raise ValueError("Director prompt is empty; add a shot to the timeline.")
        if int(width) < 32 or int(height) < 32:
            raise ValueError("Connect Director width and height outputs.")

        resolved_seconds = float(seconds)
        if resolved_seconds <= 0:
            resolved_seconds = _director_scene_seconds(director_prompt)

        run_kind = "continuation" if seed_latent is not None else "first clip"
        if bool(profile):
            ref_bits = ""
            if active_refs:
                ref_bits += " active=" + ",".join("@ref%d" % n for n in active_refs)
            if pruned_refs:
                ref_bits += " pruned=" + ",".join("@ref%d" % n for n in pruned_refs)
            log.info(
                "[MiniMaxDirector12GB] %s | %.2fs %dx%d context=%d ref_size=%s media=%d "
                "compile_only=%s%s | %s",
                run_kind,
                resolved_seconds,
                int(width),
                int(height),
                int(context_length),
                str(ref_image_size),
                len(scene.get("media", ())) if isinstance(scene, dict) else 0,
                scene_compile_only,
                ref_bits,
                _cuda_memory_text(),
            )

        if bool(low_vram_cleanup):
            _low_vram_cleanup()
            if bool(profile):
                log.info("[MiniMaxDirector12GB] after cleanup | %s", _cuda_memory_text())

        context_class = _context_node()
        required = context_class.INPUT_TYPES()["required"]
        defaults = {
            name: spec[1].get("default")
            for name, spec in required.items()
            if len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]
        }
        defaults.update(
            h3_bundle=h3_bundle,
            mode="Context Segments",
            audio_mode="Default",
            prompt=str(director_prompt),
            resolution="custom",
            aspect_ratio="Custom",
            custom_ratio=f"{int(width)}:{int(height)}",
            width=int(width),
            height=int(height),
            seconds=resolved_seconds,
            segment_seconds="",
            context_length=int(context_length),
            continuity_mode="Native Guide",
            transition_seconds=0.0,
            advanced=False,
            fps=float(fps),
            keyframe_role="First frame priority",
            ref_image_size=str(ref_image_size),
            reference_mention_mode="By index",
            context_prompt_optimizer_mode="1",
            context_prompt_optimizer_concurrency=1,
            seed_latent=seed_latent,
            seed_video=seed_video,
        )
        defaults.update(media_inputs)

        setup_started = time.perf_counter()
        model, context = context_class.generate(**defaults)
        setup_elapsed = time.perf_counter() - setup_started

        if bool(profile):
            total = time.perf_counter() - started
            log.info(
                "[MiniMaxDirector12GB] context ready in %.2fs (total %.2fs) | %s",
                setup_elapsed,
                total,
                _cuda_memory_text(),
            )
            if scene is not None and not scene_compile_only:
                log.warning(
                    "[MiniMaxDirector12GB] Director scene was produced in FULL mode. Enable "
                    "Director -> compile_only to skip duplicate Qwen/VAE/H3 conditioning."
                )

        return io.NodeOutput(model, context)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3DirectorContinuationCS": MiniMaxH3DirectorContinuation,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3DirectorContinuationCS": "MiniMax H3 Director 12GB Engine",
}
