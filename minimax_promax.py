"""MiniMax H3 Promax: new Director implementation using ComfyUI's native H3 engine.

No inheritance from the existing Director, no custom CUDA kernels, no global patches.
"""
from __future__ import annotations

import logging
from comfy_api.latest import io
from .minimax_core import core
from .promax_plan import MODES, PRESETS, DEFAULT_SHOTS, compile_plan, render_prompt

log = logging.getLogger(__name__)
_UNWIRED = object()


def unpack(output):
    return output.args if hasattr(output, 'args') else output


def fit_image(image, width, height, crop=True):
    import comfy.utils
    return comfy.utils.common_upscale(image[..., :3].movedim(-1, 1), width, height,
                                      'bilinear', 'center' if crop else 'disabled').movedim(1, -1)


class MiniMaxH3Promax(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        inputs = [
            io.Model.Input('model_fl2va', optional=True, lazy=True),
            io.Model.Input('model_ref2va', optional=True, lazy=True),
            io.Clip.Input('clip', lazy=True), io.Vae.Input('vae', lazy=True),
            io.Combo.Input('mode', options=MODES, default='T2V'),
            io.Combo.Input('preset', options=list(PRESETS), default='Balanced portrait 480x864'),
            io.String.Input('global_prompt', multiline=True, default='Photorealistic harbour at dawn. A fisherman aboard a small wooden boat. Natural light and plausible physical motion.'),
            io.String.Input('summary', multiline=True, default=''),
            io.String.Input('soundscape', multiline=True, default='Water, creaking wood, distant seabirds. No dialogue.'),
            io.String.Input('music', multiline=True, default=''),
            io.String.Input('shots_json', multiline=True, default=DEFAULT_SHOTS),
            io.Float.Input('shift_video', default=12.0, min=0.1, max=30.0, step=0.1),
            io.Float.Input('shift_audio', default=3.0, min=0.1, max=30.0, step=0.1),
            io.Image.Input('first_frame', optional=True, lazy=True),
            io.Image.Input('last_frame', optional=True, lazy=True),
        ]
        for i in range(1, 4):
            inputs.extend([
                io.Image.Input(f'reference_{i}', optional=True, lazy=True),
                io.String.Input(f'ref{i}_description', multiline=True, default=''),
                io.String.Input(f'ref{i}_retained', default='appearance and identity'),
            ])
        inputs.extend([
            io.Image.Input('reference_video', optional=True, lazy=True,
                           tooltip='Frames at 24 fps. At most 5 seconds; resize before loading large videos.'),
            io.String.Input('video_description', default='Scene and motion reference.'),
            io.String.Input('video_retained', default='environment and visual style'),
            io.Audio.Input('reference_audio', optional=True, lazy=True),
            io.Vae.Input('audio_vae', optional=True, lazy=True),
            io.String.Input('audio_description', default='Sound reference.'),
            io.String.Input('audio_retained', default='timbre and acoustic character'),
        ])
        return io.Schema(node_id='MiniMaxH3Promax', display_name='MiniMax H3 Promax · Director',
                         category='MiniMax H3/Promax', inputs=inputs,
                         outputs=[io.Model.Output('model'), io.Conditioning.Output('positive'),
                                  io.Latent.Output('latent'), io.Float.Output('fps'),
                                  io.String.Output('prompt'), io.String.Output('report')])

    @classmethod
    def check_lazy_status(cls, mode, preset, shots_json, global_prompt, summary='',
                          soundscape='', music='', **kwargs):
        # Reject broken timelines before asking ComfyUI to evaluate the heavy loaders.
        compile_plan(mode, preset, shots_json, global_prompt, summary, soundscape, music)
        model_key = 'model_ref2va' if mode == 'Ref2V' else 'model_fl2va'
        if kwargs.get(model_key, _UNWIRED) is _UNWIRED:
            raise ValueError(f'Promax {mode}: connect {model_key}; no fallback to the wrong model family.')
        needed = [model_key, 'clip', 'vae']
        if mode == 'FL2V':
            if kwargs.get('first_frame', _UNWIRED) is _UNWIRED:
                raise ValueError('Promax FL2V: connect first_frame.')
            needed.extend(['first_frame', 'last_frame'])
        elif mode == 'Ref2V':
            refs = ['reference_1', 'reference_2', 'reference_3', 'reference_video', 'reference_audio']
            if not any(kwargs.get(key, _UNWIRED) is not _UNWIRED for key in refs):
                raise ValueError('Promax Ref2V: connect at least one reference.')
            needed.extend(refs)
            if kwargs.get('reference_audio', _UNWIRED) is not _UNWIRED:
                if kwargs.get('audio_vae', _UNWIRED) is _UNWIRED:
                    raise ValueError('Promax: reference_audio requires audio_vae.')
                needed.append('audio_vae')
        return [key for key in needed if kwargs.get(key, _UNWIRED) is None]

    @classmethod
    def execute(cls, clip, vae, mode, preset, global_prompt, summary, soundscape, music,
                shots_json, shift_video, shift_audio, model_fl2va=None, model_ref2va=None,
                first_frame=None, last_frame=None, reference_1=None, reference_2=None,
                reference_3=None, ref1_description='', ref2_description='', ref3_description='',
                ref1_retained='appearance and identity', ref2_retained='appearance and identity',
                ref3_retained='appearance and identity', reference_video=None,
                video_description='Scene and motion reference.', video_retained='environment and visual style',
                reference_audio=None, audio_vae=None, audio_description='Sound reference.',
                audio_retained='timbre and acoustic character'):
        plan = compile_plan(mode, preset, shots_json, global_prompt, summary, soundscape, music)
        model = model_ref2va if mode == 'Ref2V' else model_fl2va
        if model is None:
            raise ValueError(f'Promax: missing model for {mode}.')
        width, height, length = plan['width'], plan['height'], plan['frames']
        references, images, videos, audios = [], {}, {}, {}
        if mode == 'Ref2V':
            slots = [(reference_1, ref1_description, ref1_retained),
                     (reference_2, ref2_description, ref2_retained),
                     (reference_3, ref3_description, ref3_retained)]
            gap = False
            for index, (tensor, description, retained) in enumerate(slots, 1):
                if tensor is None:
                    gap = True
                    continue
                if gap:
                    raise ValueError('Promax: connect image references consecutively from reference_1; gaps would change <Picture N> numbering.')
                if not description.strip():
                    raise ValueError(f'Promax: fill ref{index}_description to identify <Picture {index}>.')
                if int(tensor.shape[0]) != 1:
                    raise ValueError('Promax: each reference image socket expects one image, not a video/batch.')
                images[f'ref_image_{index-1}'] = tensor
                references.append((f'<Picture {index}>', description, retained))
            if reference_video is not None:
                count = int(reference_video.shape[0])
                if count < 5 or count > 120:
                    raise ValueError('Promax: reference_video must contain 5–120 frames at 24 fps. Trim upstream; no silent truncation.')
                # Area cap BEFORE native H3 encoding; preserve source aspect ratio.
                import math
                h, w = reference_video.shape[1:3]
                scale = min(1.0, math.sqrt(width * height / (w * h)))
                tw, th = max(32, int(w * scale) // 32 * 32), max(32, int(h * scale) // 32 * 32)
                videos['ref_video_0'] = fit_image(reference_video, tw, th, crop=False)
                references.append(('<Video 1>', video_description, video_retained))
            if reference_audio is not None:
                if audio_vae is None:
                    raise ValueError('Promax: audio reference requires audio_vae.')
                sr = int(reference_audio['sample_rate'])
                if sr <= 0 or reference_audio['waveform'].shape[-1] / sr > 15:
                    raise ValueError('Promax: audio reference must be at most 15 seconds with a positive sample rate.')
                audios['ref_audio_0'] = reference_audio
                references.append(('<Audio 1>', audio_description, audio_retained))
        prompt = render_prompt(plan, references)
        native = core()
        if mode == 'Ref2V':
            out = native.MiniMaxH3ReferenceToVideo.execute(
                clip=clip, vae=vae, audio_vae=audio_vae, prompt=prompt, width=width,
                height=height, length=length, ref_image_size='match',
                ref_images=images or None, ref_videos=videos or None, ref_audios=audios or None)
        else:
            if mode == 'FL2V' and first_frame is None:
                raise ValueError('Promax FL2V: first_frame is required.')
            first = fit_image(first_frame[:1], width, height) if mode == 'FL2V' else None
            last = fit_image(last_frame[-1:], width, height) if mode == 'FL2V' and last_frame is not None else None
            out = native.MiniMaxH3ImageToVideo.execute(clip=clip, vae=vae, prompt=prompt,
                width=width, height=height, length=length, first_frame=first, last_frame=last)
        positive, latent = unpack(out)[:2]
        patched = unpack(native.MiniMaxH3SigmaShift.execute(model=model,
                         shift_video=float(shift_video), shift_audio=float(shift_audio)))[0]
        report = (f'Promax 0.1 | {mode} | {width}x{height} | {length} frames @24 fps | '
                  f"requested {plan['requested_seconds']:.3f}s / actual {plan['actual_seconds']:.3f}s | "
                  f'{len(references)} refs | 12 GB target, GPU memory not guaranteed')
        log.info(report)
        return io.NodeOutput(patched, positive, latent, 24.0, prompt, report,
                             ui={'promax_report': [report], 'promax_prompt': [prompt]})


class MiniMaxH3PromaxLastFrame(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='MiniMaxH3PromaxLastFrame', display_name='Promax · Last Frame',
                         category='MiniMax H3/Promax', inputs=[io.Image.Input('images')],
                         outputs=[io.Image.Output('image')])

    @classmethod
    def execute(cls, images):
        if images.shape[0] < 1:
            raise ValueError('Promax: decoded video has no frames.')
        return io.NodeOutput(images[-1:].clone())
