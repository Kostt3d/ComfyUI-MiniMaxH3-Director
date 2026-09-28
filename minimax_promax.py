"""MiniMax H3 Promax: native H3 Director with integrated Studio media bank.

The backend stays independent from the legacy Director node. Promax Studio can load media
inside the node UI and stores only input-relative file references in a hidden JSON widget.
External IMAGE/AUDIO sockets remain valid and override Studio media when connected.
"""
from __future__ import annotations

import json
import logging
import math
import re

from comfy_api.latest import io

from .minimax_core import core
from . import minimax_media as media
from .promax_plan import MODES, PRESETS, DEFAULT_SHOTS, compile_plan, render_prompt

log = logging.getLogger(__name__)
_UNWIRED = object()
REF_POLICIES = ["Auto (12GB)", "Manual", "Force all loaded"]
EMPTY_STUDIO = json.dumps({
    "version": 1,
    "first_frame": None,
    "last_frame": None,
    "refs": {},
    "reference_video": None,
    "reference_audio": None,
}, separators=(",", ":"))


def unpack(output):
    return output.args if hasattr(output, "args") else output


def fit_image(image, width, height, crop=True):
    import comfy.utils
    return comfy.utils.common_upscale(
        image[..., :3].movedim(-1, 1), width, height,
        "bilinear", "center" if crop else "disabled"
    ).movedim(1, -1)


def _parse_studio_media(raw: str) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _media_file(entry) -> str:
    return str(entry.get("file") or "").strip() if isinstance(entry, dict) else ""


def _studio_ref_entry(studio: dict, idx: int):
    refs = studio.get("refs") if isinstance(studio.get("refs"), dict) else {}
    return refs.get(str(idx)) or refs.get(idx) or None


def _parse_manual_refs(value: str) -> list[int]:
    picked = []
    for token in re.findall(r"\d+", str(value or "")):
        idx = int(token)
        if 1 <= idx <= 9 and idx not in picked:
            picked.append(idx)
    return picked


def _explicit_ref_indices(*texts: str) -> list[int]:
    found = []
    for text in texts:
        raw = str(text or "")
        for match in re.finditer(r"(?i)@ref([1-9])|<\s*Picture\s+([1-9])\s*>", raw):
            idx = int(match.group(1) or match.group(2))
            if idx not in found:
                found.append(idx)
    return found


def _select_ref_indices(wired: list[int], policy: str, manual_refs: str,
                        auto_ref_limit: int, *texts: str) -> list[int]:
    wired_set = set(wired)
    wired = [idx for idx in range(1, 10) if idx in wired_set]
    if policy == "Force all loaded":
        return wired
    if policy == "Manual":
        requested = _parse_manual_refs(manual_refs)
        selected = [idx for idx in requested if idx in wired]
        if requested and not selected:
            raise ValueError("Promax Manual refs: none of the requested reference slots is loaded or connected.")
        return selected

    explicit = [idx for idx in _explicit_ref_indices(*texts) if idx in wired]
    if explicit:
        return explicit
    limit = max(1, min(9, int(auto_ref_limit)))
    return wired[:limit]


def _remap_ref_tokens(text: str, bank_to_picture: dict[int, int]) -> str:
    raw = str(text or "")

    def replace(match):
        idx = int(match.group(1) or match.group(2))
        mapped = bank_to_picture.get(idx)
        if mapped is None:
            return match.group(0)
        return f"<Picture {mapped}>"

    return re.sub(r"(?i)@ref([1-9])|<\s*Picture\s+([1-9])\s*>", replace, raw)


def _internal_image(studio: dict, key: str):
    file_ref = _media_file(studio.get(key))
    if not file_ref:
        return None
    tensor = media.load_image_source("", file_ref)
    return tensor if tensor is not None else None


def _internal_ref_image(studio: dict, idx: int):
    file_ref = _media_file(_studio_ref_entry(studio, idx))
    if not file_ref:
        return None
    tensor = media.load_image_source("", file_ref)
    return tensor if tensor is not None else None


def _internal_video(studio: dict, width: int, height: int):
    entry = studio.get("reference_video")
    file_ref = _media_file(entry)
    if not file_ref:
        return None
    trim = max(0.0, float((entry or {}).get("trim_start") or 0.0))
    duration = float((entry or {}).get("duration") or 5.0)
    duration = min(5.0, max(5.0 / 24.0, duration))
    return media.load_video_tensor(
        file_ref, trim, duration, out_fps=24.0,
        max_short_edge=max(32, min(768, min(width, height))),
        max_pixels=max(32 * 32, width * height),
    )


def _internal_audio(studio: dict):
    entry = studio.get("reference_audio")
    file_ref = _media_file(entry)
    if not file_ref:
        return None
    trim = max(0.0, float((entry or {}).get("trim_start") or 0.0))
    duration = float((entry or {}).get("duration") or 15.0)
    duration = min(15.0, max(1.0 / 24.0, duration))
    return media.load_audio_segment({
        "audioFile": file_ref,
        "trimStart": int(round(trim * 24.0)),
        "length": int(round(duration * 24.0)),
    }, 24.0)


class MiniMaxH3Promax(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        inputs = [
            io.Model.Input("model_fl2va", optional=True, lazy=True),
            io.Model.Input("model_ref2va", optional=True, lazy=True),
            io.Clip.Input("clip", lazy=True),
            io.Vae.Input("vae", lazy=True),
            io.Combo.Input("mode", options=MODES, default="T2V"),
            io.Combo.Input("preset", options=list(PRESETS), default="Balanced portrait 480x864"),
            io.String.Input("global_prompt", multiline=True,
                            default="Photorealistic harbour at dawn. A fisherman aboard a small wooden boat. Natural light and plausible physical motion."),
            io.String.Input("summary", multiline=True, default=""),
            io.String.Input("soundscape", multiline=True,
                            default="Water, creaking wood, distant seabirds. No dialogue."),
            io.String.Input("music", multiline=True, default=""),
            io.String.Input("shots_json", multiline=True, default=DEFAULT_SHOTS),
            io.Float.Input("shift_video", default=12.0, min=0.1, max=30.0, step=0.1),
            io.Float.Input("shift_audio", default=3.0, min=0.1, max=30.0, step=0.1),
            io.Image.Input("first_frame", optional=True, lazy=True),
            io.Image.Input("last_frame", optional=True, lazy=True),
        ]
        for i in range(1, 10):
            inputs.extend([
                io.Image.Input(f"reference_{i}", optional=True, lazy=True),
                io.String.Input(f"ref{i}_description", multiline=True, default=""),
                io.String.Input(f"ref{i}_retained", default="appearance and identity"),
            ])
        inputs.extend([
            io.Combo.Input("reference_policy", options=REF_POLICIES, default="Auto (12GB)",
                           tooltip="Auto encodes only explicitly tagged refs, or the first auto_ref_limit loaded refs."),
            io.Int.Input("auto_ref_limit", default=4, min=1, max=9, step=1,
                         tooltip="Fallback cap when Auto mode finds no @refN or <Picture N> tags in the prompt."),
            io.String.Input("manual_refs", default="1,2,3",
                            tooltip="Manual bank slots, for example 1,4,7. Bank slots are compacted to <Picture 1..N> for H3."),
            io.Image.Input("reference_video", optional=True, lazy=True,
                           tooltip="Frames at 24 fps. At most 5 seconds."),
            io.String.Input("video_description", default="Scene and motion reference."),
            io.String.Input("video_retained", default="environment and visual style"),
            io.Audio.Input("reference_audio", optional=True, lazy=True),
            io.Vae.Input("audio_vae", optional=True, lazy=True),
            io.String.Input("audio_description", default="Sound reference."),
            io.String.Input("audio_retained", default="timbre and acoustic character"),
            io.String.Input("studio_media_json", multiline=False, default=EMPTY_STUDIO,
                            tooltip="Promax Studio internal media state. Managed by the custom UI."),
        ])
        return io.Schema(
            node_id="MiniMaxH3Promax",
            display_name="MiniMax H3 Promax · Director",
            category="MiniMax H3/Promax",
            inputs=inputs,
            outputs=[
                io.Model.Output("model"), io.Conditioning.Output("positive"),
                io.Latent.Output("latent"), io.Float.Output("fps"),
                io.String.Output("prompt"), io.String.Output("report"),
            ],
        )

    @classmethod
    def check_lazy_status(cls, mode, preset, shots_json, global_prompt, summary="",
                          soundscape="", music="", reference_policy="Auto (12GB)",
                          auto_ref_limit=4, manual_refs="1,2,3", studio_media_json=EMPTY_STUDIO,
                          **kwargs):
        compile_plan(mode, preset, shots_json, global_prompt, summary, soundscape, music)
        studio = _parse_studio_media(studio_media_json)
        model_key = "model_ref2va" if mode == "Ref2V" else "model_fl2va"
        if kwargs.get(model_key, _UNWIRED) is _UNWIRED:
            raise ValueError(f"Promax {mode}: connect {model_key}; no fallback to the wrong model family.")
        needed = [model_key, "clip", "vae"]

        if mode == "FL2V":
            has_internal_first = bool(_media_file(studio.get("first_frame")))
            if kwargs.get("first_frame", _UNWIRED) is _UNWIRED and not has_internal_first:
                raise ValueError("Promax FL2V: load a First Frame in Promax Studio or connect first_frame.")
            if not has_internal_first:
                needed.append("first_frame")
            if kwargs.get("last_frame", _UNWIRED) is not _UNWIRED and not _media_file(studio.get("last_frame")):
                needed.append("last_frame")

        elif mode == "Ref2V":
            available = []
            for i in range(1, 10):
                internal = bool(_media_file(_studio_ref_entry(studio, i)))
                wired = kwargs.get(f"reference_{i}", _UNWIRED) is not _UNWIRED
                if internal or wired:
                    available.append(i)
            selected = _select_ref_indices(
                available, reference_policy, manual_refs, auto_ref_limit,
                global_prompt, summary, shots_json,
            )
            internal_video = bool(_media_file(studio.get("reference_video")))
            internal_audio = bool(_media_file(studio.get("reference_audio")))
            has_video = internal_video or kwargs.get("reference_video", _UNWIRED) is not _UNWIRED
            has_audio = internal_audio or kwargs.get("reference_audio", _UNWIRED) is not _UNWIRED
            if not selected and not has_video and not has_audio:
                raise ValueError("Promax Ref2V: load at least one reference in Promax Studio or connect one.")
            for i in selected:
                if not _media_file(_studio_ref_entry(studio, i)):
                    needed.append(f"reference_{i}")
            if has_video and not internal_video:
                needed.append("reference_video")
            if has_audio:
                if kwargs.get("audio_vae", _UNWIRED) is _UNWIRED:
                    raise ValueError("Promax: audio reference requires audio_vae.")
                needed.append("audio_vae")
                if not internal_audio:
                    needed.append("reference_audio")

        return [key for key in dict.fromkeys(needed) if kwargs.get(key, _UNWIRED) is None]

    @classmethod
    def execute(cls, clip, vae, mode, preset, global_prompt, summary, soundscape, music,
                shots_json, shift_video, shift_audio, model_fl2va=None, model_ref2va=None,
                first_frame=None, last_frame=None,
                reference_1=None, ref1_description="", ref1_retained="appearance and identity",
                reference_2=None, ref2_description="", ref2_retained="appearance and identity",
                reference_3=None, ref3_description="", ref3_retained="appearance and identity",
                reference_4=None, ref4_description="", ref4_retained="appearance and identity",
                reference_5=None, ref5_description="", ref5_retained="appearance and identity",
                reference_6=None, ref6_description="", ref6_retained="appearance and identity",
                reference_7=None, ref7_description="", ref7_retained="appearance and identity",
                reference_8=None, ref8_description="", ref8_retained="appearance and identity",
                reference_9=None, ref9_description="", ref9_retained="appearance and identity",
                reference_policy="Auto (12GB)", auto_ref_limit=4, manual_refs="1,2,3",
                reference_video=None, video_description="Scene and motion reference.",
                video_retained="environment and visual style", reference_audio=None, audio_vae=None,
                audio_description="Sound reference.", audio_retained="timbre and acoustic character",
                studio_media_json=EMPTY_STUDIO):
        studio = _parse_studio_media(studio_media_json)
        model = model_ref2va if mode == "Ref2V" else model_fl2va
        if model is None:
            raise ValueError(f"Promax: missing model for {mode}.")

        references, images, videos, audios = [], {}, {}, {}
        selected_bank = []

        socket_refs = {
            1: (reference_1, ref1_description, ref1_retained),
            2: (reference_2, ref2_description, ref2_retained),
            3: (reference_3, ref3_description, ref3_retained),
            4: (reference_4, ref4_description, ref4_retained),
            5: (reference_5, ref5_description, ref5_retained),
            6: (reference_6, ref6_description, ref6_retained),
            7: (reference_7, ref7_description, ref7_retained),
            8: (reference_8, ref8_description, ref8_retained),
            9: (reference_9, ref9_description, ref9_retained),
        }

        if mode == "Ref2V":
            available = []
            for idx, (tensor, _, _) in socket_refs.items():
                if tensor is not None or _media_file(_studio_ref_entry(studio, idx)):
                    available.append(idx)
            selected_bank = _select_ref_indices(
                available, reference_policy, manual_refs, auto_ref_limit,
                global_prompt, summary, shots_json,
            )
            remap = {bank_idx: picture_idx for picture_idx, bank_idx in enumerate(selected_bank, 1)}
            global_prompt = _remap_ref_tokens(global_prompt, remap)
            summary = _remap_ref_tokens(summary, remap)
            soundscape = _remap_ref_tokens(soundscape, remap)
            music = _remap_ref_tokens(music, remap)
            shots_json = _remap_ref_tokens(shots_json, remap)

        plan = compile_plan(mode, preset, shots_json, global_prompt, summary, soundscape, music)
        width, height, length = plan["width"], plan["height"], plan["frames"]

        if mode == "Ref2V":
            for picture_idx, bank_idx in enumerate(selected_bank, 1):
                tensor, description, retained = socket_refs[bank_idx]
                if tensor is None:
                    tensor = _internal_ref_image(studio, bank_idx)
                if tensor is None:
                    continue
                if not str(description).strip():
                    entry = _studio_ref_entry(studio, bank_idx) or {}
                    description = f"Reference {bank_idx}: {entry.get('name') or 'visual identity reference'}"
                if int(tensor.shape[0]) != 1:
                    raise ValueError("Promax: each reference image expects one image, not a video/batch.")
                images[f"ref_image_{picture_idx-1}"] = tensor
                references.append((f"<Picture {picture_idx}>", description, retained))

            if reference_video is None:
                reference_video = _internal_video(studio, width, height)
            if reference_video is not None:
                count = int(reference_video.shape[0])
                if count < 5 or count > 120:
                    raise ValueError("Promax: reference video must contain 5–120 frames at 24 fps.")
                h, w = reference_video.shape[1:3]
                scale = min(1.0, math.sqrt(width * height / (w * h)))
                tw = max(32, int(w * scale) // 32 * 32)
                th = max(32, int(h * scale) // 32 * 32)
                videos["ref_video_0"] = fit_image(reference_video, tw, th, crop=False)
                references.append(("<Video 1>", video_description, video_retained))

            if reference_audio is None:
                reference_audio = _internal_audio(studio)
            if reference_audio is not None:
                if audio_vae is None:
                    raise ValueError("Promax: audio reference requires audio_vae.")
                sr = int(reference_audio["sample_rate"])
                if sr <= 0 or reference_audio["waveform"].shape[-1] / sr > 15:
                    raise ValueError("Promax: audio reference must be at most 15 seconds with a positive sample rate.")
                audios["ref_audio_0"] = reference_audio
                references.append(("<Audio 1>", audio_description, audio_retained))

        prompt = render_prompt(plan, references)
        native = core()

        if mode == "Ref2V":
            out = native.MiniMaxH3ReferenceToVideo.execute(
                clip=clip, vae=vae, audio_vae=audio_vae, prompt=prompt,
                width=width, height=height, length=length, ref_image_size="match",
                ref_images=images or None, ref_videos=videos or None, ref_audios=audios or None,
            )
        else:
            if mode == "FL2V":
                first_frame = first_frame if first_frame is not None else _internal_image(studio, "first_frame")
                last_frame = last_frame if last_frame is not None else _internal_image(studio, "last_frame")
                if first_frame is None:
                    raise ValueError("Promax FL2V: load a First Frame in Promax Studio or connect first_frame.")
            first = fit_image(first_frame[:1], width, height) if mode == "FL2V" else None
            last = fit_image(last_frame[-1:], width, height) if mode == "FL2V" and last_frame is not None else None
            out = native.MiniMaxH3ImageToVideo.execute(
                clip=clip, vae=vae, prompt=prompt, width=width, height=height,
                length=length, first_frame=first, last_frame=last,
            )

        positive, latent = unpack(out)[:2]
        patched = unpack(native.MiniMaxH3SigmaShift.execute(
            model=model, shift_video=float(shift_video), shift_audio=float(shift_audio)
        ))[0]
        bank_text = f" | ref bank active {selected_bank}" if mode == "Ref2V" else ""
        studio_count = sum(1 for i in range(1, 10) if _media_file(_studio_ref_entry(studio, i)))
        report = (
            f"Promax 0.4 Studio | {mode} | {width}x{height} | {length} frames @24 fps | "
            f"requested {plan['requested_seconds']:.3f}s / actual {plan['actual_seconds']:.3f}s | "
            f"{len(references)} encoded refs{bank_text} | {studio_count} Studio image refs loaded | "
            "12 GB target, GPU memory not guaranteed"
        )
        log.info(report)
        return io.NodeOutput(
            patched, positive, latent, 24.0, prompt, report,
            ui={"promax_report": [report], "promax_prompt": [prompt]},
        )


class MiniMaxH3PromaxLastFrame(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromaxLastFrame",
            display_name="Promax · Last Frame",
            category="MiniMax H3/Promax",
            inputs=[io.Image.Input("images")],
            outputs=[io.Image.Output("image")],
        )

    @classmethod
    def execute(cls, images):
        if images.shape[0] < 1:
            raise ValueError("Promax: decoded video has no frames.")
        return io.NodeOutput(images[-1:].clone())
