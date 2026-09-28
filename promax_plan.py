"""Independent Promax storyboard compiler. Standard library only; no model loading."""
from __future__ import annotations

import json
import math

MODES = ['T2V', 'FL2V', 'Ref2V']
PRESETS = {
    'Preview portrait 288x512': (288, 512),
    'Balanced portrait 480x864': (480, 864),
    'Portrait 9:16 576x1024': (576, 1024),
    'Preview landscape 512x288': (512, 288),
    'Balanced landscape 864x480': (864, 480),
    'Landscape 16:9 1024x576': (1024, 576),
}
DEFAULT_SHOTS = json.dumps([
    {'seconds': 2, 'camera': 'Wide static shot from the quayside.',
     'action': 'A small fishing boat rocks gently in a quiet harbour. A large shadow moves beneath it.', 'audio': 'Water laps against the hull.'},
    {'seconds': 3, 'camera': 'Low angle close shot of the water beside the boat.',
     'action': 'A circular ripple spreads outward. The fisherman leans over the rail to look.', 'audio': 'A deep underwater rumble grows louder.'},
], ensure_ascii=False)


def text(value, name):
    if not isinstance(value, str):
        raise ValueError(f'Promax: {name} must be text.')
    if len(value) > 20000:
        raise ValueError(f'Promax: {name} is too long (maximum 20000 characters).')
    return value.strip()


def compile_plan(mode, preset, shots_json, global_prompt, summary='', soundscape='', music=''):
    if mode not in MODES or preset not in PRESETS:
        raise ValueError('Promax: unknown generation mode or resolution preset.')
    if not isinstance(shots_json, str) or len(shots_json) > 100000:
        raise ValueError('Promax: invalid or oversized storyboard JSON.')
    try:
        shots = json.loads(shots_json)
    except (ValueError, TypeError) as exc:
        raise ValueError('Promax: invalid storyboard JSON. Restore a valid shot list.') from exc
    if not isinstance(shots, list) or not 1 <= len(shots) <= 12:
        raise ValueError('Promax: use between 1 and 12 shots per clip.')
    clean, cursor = [], 0.0
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ValueError(f'Promax: shot {index} must be an object.')
        duration = shot.get('seconds')
        if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration < 0.25:
            raise ValueError(f'Promax: shot {index} duration must be a finite number >= 0.25 seconds.')
        camera = text(shot.get('camera', ''), 'camera')
        action = text(shot.get('action', ''), 'action')
        if not camera or not action:
            raise ValueError(f'Promax: shot {index} needs both camera and action.')
        clean.append({'start': cursor, 'seconds': float(duration), 'camera': camera,
                      'action': action, 'audio': text(shot.get('audio', ''), 'shot audio')})
        cursor += duration
    if not 4 <= cursor <= 15:
        raise ValueError(f'Promax: clip duration is {cursor:g}s. Use 4–15 seconds; render longer films as separate clips.')
    frames = math.ceil(cursor * 24 - 1e-8)
    frames += (5 - frames) % 17
    global_prompt = text(global_prompt, 'global prompt')
    if not global_prompt:
        raise ValueError('Promax: global prompt is empty.')
    return {'mode': mode, 'width': PRESETS[preset][0], 'height': PRESETS[preset][1],
            'shots': clean, 'requested_seconds': cursor, 'frames': frames,
            'actual_seconds': frames / 24, 'global_prompt': global_prompt,
            'summary': text(summary, 'summary'), 'soundscape': text(soundscape, 'soundscape'),
            'music': text(music, 'music')}


def stamp(seconds):
    ms = round(seconds * 1000)
    return f'{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}'


def render_prompt(plan, references=()):
    """Native H3 field names; timing stays advisory, not a hard attention mask."""
    parts = []
    if plan['mode'] == 'Ref2V':
        if not references:
            raise ValueError('Promax: Ref2V requires at least one image, video or audio reference.')
        parts.append('subject_definitions:\n' + '\n'.join(f'{label}: {description}' for label, description, _ in references))
        if plan['summary']:
            parts.append('summary: ' + plan['summary'])
        parts.append('retention_analysis:\n' + '\n'.join(f'{label}: retained: {retained}' for label, _, retained in references))
    body = [plan['global_prompt']]
    for index, shot in enumerate(plan['shots'], 1):
        time = '' if index == 1 else f"At {stamp(shot['start'])}, "
        pieces = [shot['camera'], shot['action'], shot['audio']]
        body.append(f'[Shot {index}] {time}' + ' '.join(p for p in pieces if p))
    field = 'detailed_description' if plan['mode'] == 'Ref2V' else 'integrated_multimodal_description'
    parts.append(field + ': ' + ' '.join(body))
    parts.append('overall_soundscape: ' + (plan['soundscape'] or 'Natural ambient sound matching the visible action.'))
    parts.append('non_diegetic_music: ' + (plan['music'] or 'N/A'))
    return '\n\n'.join(parts)
