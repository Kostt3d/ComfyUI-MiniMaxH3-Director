"""Performance-oriented Director wrapper for continuation workflows.

The regular Director node is excellent for a first render, where its MODEL / CONDITIONING /
LATENT outputs are consumed directly. A latent-continuation workflow has a different need:
it only needs the compiled scene payload (prompt, timing, canvas and reference media), because
SatoDive builds the continuation conditioning itself.

Historically the Director still performed the complete H3 conditioning pass before emitting
that scene. On low-VRAM machines this meant references and Qwen/VAE work could be paid twice
for one continuation. This wrapper keeps the public node id and UI, adds an opt-in
"compile only" mode, and can prune subject reference images that the current scene never
mentions explicitly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from collections import OrderedDict

from comfy_api.latest import io

from . import minimax_media as media
from . import minimax_plan as plan
from .minimax_core import core
from .minimax_director import (
    MIN_CANVAS_EDGE,
    MODEL_FPS,
    MiniMaxH3Director as _BaseDirector,
    _UNCONNECTED,
    _grab_base_frame,
    _load_event_tensor,
    resolve_canvas,
    resolve_size,
    resolve_window,
)

log = logging.getLogger(__name__)

_REF_TAG_RE = re.compile(r"@(?:ref|char|character)([1-9])\b", re.IGNORECASE)
_REF_CACHE_MAX = 32
_REF_TENSOR_CACHE: "OrderedDict[str, object]" = OrderedDict()


def _walk_strings(value, *, skip_subjects=False):
    """Yield strings from timeline data without chewing through embedded subject images."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if skip_subjects and key == "subjects":
                continue
            yield from _walk_strings(item, skip_subjects=skip_subjects)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item, skip_subjects=skip_subjects)


def explicit_ref_slots(tdata, global_prompt=""):
    """Return explicitly mentioned @ref/@char slot numbers for the current scene.

    Subject-card descriptions are intentionally excluded: a card describing itself does not
    mean the current shot uses it. If no explicit slot is found the caller keeps every
    reference, preserving legacy behaviour for prompts that rely on implicit conditioning.
    """
    used = set()
    for text in (str(global_prompt or ""), *_walk_strings(tdata, skip_subjects=True)):
        used.update(int(match.group(1)) for match in _REF_TAG_RE.finditer(text))
    return used


def prune_unused_subject_images(tdata, used_slots):
    """Blank only the unused subject images while preserving stable slot numbering."""
    cloned = copy.deepcopy(tdata)
    subjects = cloned.get("subjects")
    if not isinstance(subjects, list) or not used_slots:
        return cloned, []

    pruned = []
    for index, subject in enumerate(subjects, 1):
        if index in used_slots or not isinstance(subject, dict):
            continue
        images = subject.get("images")
        if images:
            subject["images"] = []
            pruned.append(index)
    return cloned, pruned


def _image_cache_key(image):
    name = str(image.get("name") or "")
    b64 = str(image.get("b64") or "")
    sample = b64 if len(b64) <= 512 else (b64[:256] + b64[-256:])
    digest = hashlib.sha1((name + "\0" + str(len(b64)) + "\0" + sample).encode("utf-8", "ignore")).hexdigest()
    return digest


def _cached_subject_tensor(image):
    key = _image_cache_key(image)
    cached = _REF_TENSOR_CACHE.get(key)
    if cached is not None:
        _REF_TENSOR_CACHE.move_to_end(key)
        return cached

    tensor = media.load_image_source(image.get("b64", ""), image.get("name", ""))
    _REF_TENSOR_CACHE[key] = tensor
    _REF_TENSOR_CACHE.move_to_end(key)
    while len(_REF_TENSOR_CACHE) > _REF_CACHE_MAX:
        _REF_TENSOR_CACHE.popitem(last=False)
    return tensor


def _load_ref_image_tensors_cached(slots, fit, ref_images=None):
    tensors = []
    input_cursor = 0
    for slot in slots:
        source = slot["source"]
        if source == "char":
            tensors.append(_cached_subject_tensor(slot["image"]))
        elif source == "input":
            if ref_images is None:
                continue
            tensors.append(ref_images[input_cursor:input_cursor + 1])
            input_cursor += 1
        else:
            tensor = slot["event"]["tensor"]
            if slot.get("keyframe") == plan.ROLE_LAST:
                tensors.append(fit(tensor[-1:]))
            elif slot.get("keyframe"):
                tensors.append(fit(tensor[:1]))
            else:
                tensors.append(tensor[:1])
    return tensors


def _compile_scene_only(kwargs, auto_prune_refs=True):
    """Compile the Director scene without loading Qwen, VAE conditioning or an H3 UNet."""
    timeline_data = kwargs.get("timeline_data", "")
    tdata = plan.parse_timeline(timeline_data)
    fps = float(kwargs.get("frame_rate", 24) or 24.0)

    global_prompt = kwargs.get("global_prompt", "") or ""
    used_slots = explicit_ref_slots(tdata, global_prompt) if auto_prune_refs else set()
    pruned_slots = []
    if used_slots:
        tdata, pruned_slots = prune_unused_subject_images(tdata, used_slots)
        if pruned_slots:
            log.info(
                "[MiniMaxDirectorPerf] compile-only ref prune: active=%s pruned=%s",
                ",".join("@ref%d" % n for n in sorted(used_slots)),
                ",".join("@ref%d" % n for n in pruned_slots),
            )
    elif auto_prune_refs:
        log.info("[MiniMaxDirectorPerf] no explicit @ref tags found; keeping all subject refs.")

    win_start, duration_frames = resolve_window(
        tdata,
        fps,
        kwargs.get("start_frame", 0),
        kwargs.get("duration_frames", 120),
        kwargs.get("start"),
        kwargs.get("end"),
        kwargs.get("duration"),
    )
    box_w, box_h = resolve_size(
        kwargs.get("custom_width", 0),
        kwargs.get("custom_height", 0),
        kwargs.get("width"),
        kwargs.get("height"),
    )

    ref_images = kwargs.get("ref_images")
    extra_refs = 0
    if ref_images is not None:
        try:
            extra_refs = int(ref_images.shape[0])
        except Exception:
            extra_refs = 0

    p = plan.plan_timeline(
        tdata,
        win_start,
        duration_frames,
        fps,
        global_prompt=global_prompt,
        use_custom_motion=kwargs.get("use_custom_motion", True),
        use_custom_audio=kwargs.get("use_custom_audio", False),
        override_audio=kwargs.get("override_audio", False),
        extra_ref_image_count=extra_refs,
        ref_image_notes=kwargs.get("ref_image_notes", "") or "",
    )

    for ev in p["events"]:
        ev["tensor"] = _load_event_tensor(ev, fps, win_start)

    retake = p["retake"]
    first_src = last_src = None
    if retake:
        first_src = _grab_base_frame(retake["video"], retake["start"] - 1, fps)
        tail_index = retake["start"] + retake["length"]
        if not retake["base_frames"] or tail_index < retake["base_frames"]:
            last_src = _grab_base_frame(retake["video"], tail_index, fps)
    else:
        for ev in p["events"]:
            if ev["role"] == plan.ROLE_FIRST:
                first_src = ev["tensor"][:1]
            elif ev["role"] == plan.ROLE_LAST:
                last_src = ev["tensor"][-1:]

    mm = core()
    canvas_src = first_src
    if canvas_src is None:
        canvas_src = p["events"][0]["tensor"] if p["events"] else None
    width, height = resolve_canvas(
        mm,
        box_w,
        box_h,
        int(kwargs.get("divisible_by", 32) or 32),
        kwargs.get("resize_method", "crop") or "crop",
        canvas_src,
    )
    if width < MIN_CANVAS_EDGE or height < MIN_CANVAS_EDGE:
        raise ValueError(
            "MiniMax H3 Director compile-only canvas came out %dx%d; H3 needs at least %dpx per side."
            % (width, height, MIN_CANVAS_EDGE)
        )

    divisible_by = int(kwargs.get("divisible_by", 32) or 32)
    resize_method = kwargs.get("resize_method", "crop") or "crop"
    img_compression = int(kwargs.get("img_compression", 0) or 0)

    def fit(tensor):
        out = media.resize_image(tensor, width, height, resize_method, divisible_by)
        if out.shape[1] != height or out.shape[2] != width:
            out = media.resize_image(out, width, height, "crop", divisible_by)
        if img_compression > 0:
            out = media.compress_image(out, img_compression)
        return out

    first_frame = fit(first_src) if first_src is not None else None
    last_frame = fit(last_src) if last_src is not None else None

    ref_image_tensors, ref_videos, ref_video_audios, ref_audios = [], {}, {}, {}
    if p["ref_mode_on"]:
        ref_image_tensors = _load_ref_image_tensors_cached(p["ref_image_slots"], fit, ref_images)

        for seg in p["ref_video_segs"]:
            idx = len(ref_videos)
            seg_start = float(seg.get("start", 0))
            seg_len = float(seg.get("length", 1))
            offset = max(0.0, win_start - seg_start)
            trim = float(seg.get("trimStart", 0)) + offset
            clip_sec = min(plan.REF_VIDEO_MAX_SEC, (seg_len - offset) / fps)
            short_edge = int(seg.get("refSize") or plan.REF_VIDEO_SHORT_EDGE)
            frames = media.load_video_tensor(
                seg["videoFile"],
                trim / fps,
                clip_sec,
                max_short_edge=short_edge,
                max_pixels=int(short_edge * short_edge * plan.REF_VIDEO_ASPECT_BUDGET),
            )
            if frames.shape[0] < 5:
                continue
            ref_videos["ref_video_%d" % idx] = frames
            if kwargs.get("override_audio", False):
                clip_audio = media.load_audio_segment(
                    {"audioFile": seg["videoFile"], "trimStart": trim, "length": clip_sec * fps},
                    fps,
                    file_key="audioFile",
                )
                if clip_audio is not None:
                    ref_video_audios["ref_video_audio_%d" % idx] = clip_audio

        for seg in p["ref_audio_segs"]:
            clip_audio = media.load_audio_segment(seg, fps)
            if clip_audio is not None:
                ref_audios["ref_audio_%d" % len(ref_audios)] = clip_audio

        if first_frame is not None or last_frame is not None:
            first_frame = last_frame = None

    prompt = p["prompt"]
    retake_info = ""
    if retake:
        retake_info = json.dumps({
            "base_video": retake["video"],
            "timeline_fps": fps,
            "start_frame": retake["start"],
            "length_frames": retake["length"],
            "base_frames": retake["base_frames"],
            "generated_frames": p["length"],
            "generated_fps": MODEL_FPS,
            "width": int(width),
            "height": int(height),
        })

    scene_media = (
        [("image", image) for image in ref_image_tensors]
        + [
            (
                "video",
                {
                    "images": video,
                    "audio": ref_video_audios.get("ref_video_audio_%d" % index),
                    "fps": MODEL_FPS,
                },
            )
            for index, video in enumerate(ref_videos.values())
        ]
        + [("audio", audio) for audio in ref_audios.values()]
    )

    scene = {
        "prompt": prompt,
        "seconds": p["actual_seconds"],
        "fps": MODEL_FPS,
        "width": int(width),
        "height": int(height),
        "mode": p["mode"],
        "media": tuple(scene_media),
        "first_frame": first_frame,
        "last_frame": last_frame,
        "compile_only": True,
        "active_ref_slots": tuple(sorted(used_slots)) if used_slots else (),
        "pruned_ref_slots": tuple(pruned_slots),
    }

    log.info(
        "[MiniMaxDirectorPerf] scene-only compiled | %dx%d | %.2fs | refs=%d img/%d vid/%d audio",
        width,
        height,
        p["actual_seconds"],
        len(ref_image_tensors),
        len(ref_videos),
        len(ref_audios),
    )

    return io.NodeOutput(
        None,
        None,
        None,
        None,
        MODEL_FPS,
        int(width),
        int(height),
        int(p["length"]),
        prompt,
        retake_info,
        scene,
    )


class MiniMaxH3DirectorOptimized(_BaseDirector):
    """Drop-in Director with a low-VRAM compile-only path for continuation workflows."""

    @classmethod
    def define_schema(cls):
        schema = _BaseDirector.define_schema()
        for input_ in schema.inputs:
            if input_.id in {"model", "model_ref2va", "clip", "vae", "audio_vae"}:
                input_.lazy = True
                input_.optional = True
        schema.inputs.extend([
            io.Boolean.Input(
                "compile_only",
                default=False,
                optional=True,
                advanced=True,
                label_on="Scene only",
                label_off="Full render",
                tooltip=(
                    "Continuation optimization. ON compiles prompt/timing/reference media only and "
                    "skips Director's duplicate Qwen/VAE/H3 conditioning pass. Use this in the "
                    "Director + Latent Continuation workflow."
                ),
            ),
            io.Boolean.Input(
                "auto_prune_refs",
                default=True,
                optional=True,
                advanced=True,
                label_on="Prune unused refs",
                label_off="Keep all refs",
                tooltip=(
                    "Compile-only mode: if the scene explicitly uses @refN tags, subject images "
                    "not mentioned in this scene are omitted from the continuation payload. If no "
                    "@ref tags are found, all refs are kept for backwards compatibility."
                ),
            ),
        ])
        return schema

    @classmethod
    def check_lazy_status(
        cls,
        timeline_data="",
        model=_UNCONNECTED,
        model_ref2va=_UNCONNECTED,
        clip=_UNCONNECTED,
        vae=_UNCONNECTED,
        audio_vae=_UNCONNECTED,
        compile_only=False,
        **kwargs,
    ):
        if bool(compile_only):
            return []

        requested = list(_BaseDirector.check_lazy_status(
            timeline_data=timeline_data,
            model=model,
            model_ref2va=model_ref2va,
            **kwargs,
        ))
        for name, value in (("clip", clip), ("vae", vae), ("audio_vae", audio_vae)):
            if value is None and name not in requested:
                requested.append(name)
        return requested

    @classmethod
    def execute(cls, compile_only=False, auto_prune_refs=True, **kwargs):
        if bool(compile_only):
            return _compile_scene_only(kwargs, bool(auto_prune_refs))

        if kwargs.get("clip", _UNCONNECTED) in (_UNCONNECTED, None):
            raise ValueError("MiniMax H3 Director: connect the text encoder, or enable compile_only for continuation.")
        if kwargs.get("vae", _UNCONNECTED) in (_UNCONNECTED, None):
            raise ValueError("MiniMax H3 Director: connect the video VAE, or enable compile_only for continuation.")
        return _BaseDirector.execute(**kwargs)
