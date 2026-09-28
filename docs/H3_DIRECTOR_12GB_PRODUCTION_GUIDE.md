# MiniMax H3 Director 12GB Production Guide

This guide documents the production path validated on an RTX 5070 12 GB system with ComfyUI Desktop, MiniMax H3 Ref2VA, SatoDive latent continuation, and the Director 12GB nodes in this repository.

## What this path is for

Use this workflow when you want to build a sequence of short H3 clips while preserving continuity through the saved joint audio/video latent, and simultaneously maintain a cumulative review/export master.

The important distinction is:

- **Current clip**: the new generated segment only.
- **H3 latent**: the lossless continuation state used to generate the next clip.
- **Cumulative master**: stitched review/export video. Never feed this MP4 back into H3 as the continuation state.

## Validated 12 GB baseline

The tested production baseline is:

- 480 x 864 portrait
- 24 fps
- about 15 seconds per generation
- Euler sampler
- Simple scheduler
- 8 steps
- video/audio sigma shift: 6 / 3
- `context_length = 5`
- `ref_image_size = match`
- Director `compile_only` enabled
- Director `auto_prune_refs` enabled
- Ref2VA pruned FP8-scaled model
- Qwen3-VL 4B FP8 + ClipProj
- LightX2V Ref2VA Turbo 8-step LoRA
- optional Realism People LoRA tested at a conservative `0.2` strength
- low-VRAM cleanup enabled
- profiling enabled while tuning

**Model-role update:** the bundled 2-in-1 graph now selects
`minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors` in its one diffusion
loader, and sends that same MODEL output to both `fl2va_model` and
`ref2va_model` on SatoDive Model Adapter. This is an experimental alternative
to the tested Ref2VA FP8 baseline above. It enables an FL2VA model role for
simple text/first-last-frame scenes while retaining Ref2VA reference support
without loading two separate transformers. SatoDive selects the model role
from scene media; the Run Manager selects whether to use a previous latent and
append a master. These are distinct choices.

The existing Ref2VA Turbo 8-step LoRA is still connected to the hybrid.
**That particular pairing is unverified**: first test a short simple clip and
a short reference-conditioned clip, checking quality, reference adherence,
audio, time and memory before using it for an episode. Choose the actual
checkpoint and LoRA filenames present on your system. If the hybrid/LoRA
pairing fails or loses quality, restore the validated Ref2VA FP8 setup for
continuation and test a separate FL2VA checkpoint plus matching FL2VA Turbo
LoRA in a separate workflow, avoiding simultaneous loading on 12 GB.

The example workflow also contains an **optional, bypassed H3 latent upscale branch**.
It uses `MiniMaxH3EasySegmentRefine_SatoDive` from the compatible
`Kostt3d/Minimax-H3-Latent-Continuation-v2` fork. The baseline render does not
require an upscaler checkpoint.

## Three-switch Run Manager

The bundled 12 GB workflow includes **MiniMax H3 12GB Run Manager**:

| Switch | What the workflow does |
| --- | --- |
| `generation_simple` | Bypasses Load Previous Latent and resets the cumulative master. Use for GEN01. |
| `generation_continue` | Enables Load Previous Latent and appends to the cumulative master. Select the preceding saved AV latent in the loader before GEN02+. |
| `upscale_latent` | Enables the three-node 3D upscale, decode and separate export branch. It can accompany either generation mode. |

Simple and Continue are mutually exclusive in the manager UI. Its switches
control only the five marked nodes in this example workflow. The frontend
updates their actual graph modes when a switch changes or when this workflow
opens. If the manager does not respond after updating the custom node, restart
ComfyUI and hard-refresh its frontend.

The saved AV latent and cumulative master always come from the original render;
the upscale switch only changes the optional export. Keep your episode canvas
and frame rate consistent across generations.

## Optional 3D latent upscale export

The production workflow has three bypassed nodes: `OPTIONAL • H3 3D LATENT
UPSCALE + REFINE`, `OPTIONAL • DECODE UPSCALED CLIP`, and `OPTIONAL • SAVE
UPSCALED CLIP`. To use them:

1. Install the compatible SatoDive continuation fork linked above and place an
   H3 3D latent upscaler checkpoint in `ComfyUI/models/latent_upscale_models`.
2. Turn on `upscale_latent` in Run Manager. Select the checkpoint in Segment Refine.
3. Start with `latent_upscale_scale=1.3`, `refine_execution=tiled_low_vram`,
   `latent_upscale_device=cpu`, and `latent_upscale_precision=fp16` on 12 GB.
   This is a conservative starting configuration, not a measured speed claim.
4. Inspect the separate `UPSCALED` output and compare it against the baseline
   clip. Raise scale only after measuring VRAM and render time on your machine.

This is a **second H3 sampling pass**, so it normally increases generation time.
The original Segment Render feeds Save Latent and the cumulative master. The
optional branch feeds only its separate export: never pass its changed-size
latent into the next generation while the Director canvas remains at 480×864.
This keeps the AV continuation and master stitching on a consistent canvas.

FaceDetailer from Impact Pack expects an image diffusion model and detector;
the H3 transformer is not a drop-in face model for that node. Processing video
frames independently can introduce face flicker. For a specific face problem,
run a separate, temporally checked post-production pass with a compatible face
model after exporting the clip; do not insert FaceDetailer into the H3 AV latent
chain.

On the validated CUDA 13.0 / PyTorch cu130 setup, **native PyTorch attention is the recommended default**. A local SageAttention/KJ installation may be used only if it imports and runs correctly in that environment. Do not treat SageAttention as required for the 12 GB path.

## Fixed reference library and auto-prune

Director keeps the subject library stable as `@ref1` through `@ref9`.

`auto_prune_refs` only looks for explicit reference tags in the compiled scene text. It does not infer that a character name means a particular slot.

Therefore, every subject, prop, vehicle, creature, or environment that must be conditioned in the current generation should be called explicitly with its `@refN` tag.

Example:

```text
ACTIVE REFERENCES:
@ref2 Joel
@ref5 Serge
@ref7 fishing boat
```

Unused slots remain numbered but their images are cleared before conditioning. If no explicit `@refN` tag is present, Director keeps all references for backward compatibility.

## GEN01: first generation

For the first clip of an episode or sequence:

1. Write the GEN01 prompt in Director.
2. Leave **Load Previous Latent** bypassed/disconnected.
3. Keep `context_length = 5` and `ref_image_size = match`.
4. Set **Master Chain `reset_chain = ON`**.
5. Run the workflow.
6. Save all three outputs:
   - current clip,
   - H3 latent,
   - cumulative master.

For episode-specific naming, a useful convention is:

```text
Save Latent:           HORS_CARTE_EP01
Save Current Clip:     video/HORS_CARTE_EP01_CLIP
Master Chain prefix:   HORS_CARTE_EP01_MASTER
Save Cumulative Master video/HORS_CARTE_EP01_MASTER
```

GEN01 becomes the first cumulative master automatically when `reset_chain` is ON.

## GEN02 and every following generation

For GEN02+:

1. Load the latent saved by the immediately previous generation.
2. Connect/enable **Load Previous Latent**.
3. Write only the new generation prompt in Director.
4. Set **Master Chain `reset_chain = OFF`**.
5. Keep `master_back = 0` for normal forward production.
6. Keep the exact same canvas dimensions and frame rate.
7. Run.

The workflow produces:

```text
GEN02 current clip
+
GEN02 latent
+
MASTER GEN01 + GEN02
```

Then:

```text
GEN03 uses GEN02 latent
→ MASTER GEN01 + GEN02 + GEN03

GEN04 uses GEN03 latent
→ MASTER GEN01 + GEN02 + GEN03 + GEN04
```

Continue the same pattern for the rest of the episode.

## Re-generating an earlier clip

If a cumulative master already contains a clip you want to redo, use `master_back` to select an earlier saved master.

Example: the latest master already contains GEN01+GEN02+GEN03 and you want to regenerate GEN03.

- Load the GEN02 latent as the continuation seed.
- Set `master_back = 1` so the Master Chain uses the previous cumulative master (GEN01+GEN02) instead of the newest one.
- Generate the replacement GEN03.

This prevents the old GEN03 from being stitched twice.

## Prompting rules that proved important in continuation production

H3 continuation is more reliable when the prompt describes a filmable scene rather than a dense list of abstract prohibitions.

For each timed block, use this order:

```text
[0:00-0:03]
CAMERA:
Describe the framing, axis, and camera motion first.

ACTION:
Describe the physically observable action using explicit @ref tags.

DIALOGUE:
Assign one speaker and the line.
```

Practical rules:

- Use clean hard cuts when changing the principal referenced person.
- Define the first frame after a hard cut explicitly, for example: `The very first frame after the cut already shows @ref2 Joel at the helm.`
- Avoid panning across several referenced identities to "find" another character. This can encourage identity morphs or extra people.
- Characters can remain present off camera. Do not force the entire cast to remain visible in every shot.
- For a moving boat, preserve motion cues even in close-ups: moving horizon, water travelling backward relative to the hull, wind on clothing, engine vibration, pitch/roll, and natural body balance.
- Keep sight lines and camera geography coherent. If a character watches danger ahead of the boat, place the camera so the character can physically look over the bow toward it.
- Carry persistent state forward: wet clothing remains wet, stored objects remain stored, secured equipment remains secured, and speed/heading only change when the story explicitly changes them.
- End every generation on a simple, stable composition that can serve as the latent continuation state for the next generation.

## Realism People LoRA

When the Realism People LoRA is loaded, the trigger used in the tested prompts is:

```text
r34l1sm
```

Place it near the beginning of the Director summary/compiled prompt so it is included in the current generation. A conservative `0.2` strength was tested successfully with the Turbo 8-step Ref2VA path. Treat that value as a tested starting point, not a universal optimum.

## Master Chain behavior

The **MiniMax H3 Director 12GB Master Chain** is intentionally independent from the latent continuation chain.

- `reset_chain = ON`: current clip becomes the first master.
- `reset_chain = OFF`: the node finds the newest saved master matching `master_prefix`, then stitches the current clip using SatoDive overlap alignment.
- `master_back = 0`: newest matching master.
- `master_back = 1`: previous matching master, useful when replacing the most recent generation.

The Master Chain uses the H3 context when available to determine the overlap, then applies overlap-aware stitching, source-audio handling, and simple mean/contrast color-drift correction.

## Memory expectations on a 12 GB GPU

The validated RTX 5070 setup can run with VRAM close to full and significant system-RAM/shared-GPU-memory use. This is expected for the 12 GB path.

During long renders:

- close unnecessary memory-heavy applications,
- leave the Windows page file enabled,
- expect model offloading and high RAM use,
- keep an eye on disk paging if system RAM is nearly exhausted.

High SSD active time does not automatically mean the workflow is broken; it can reflect memory-mapped model access, cache activity, or paging while weights are offloaded.

## Production checklist

Before each generation:

- previous latent correct?
- `reset_chain` correct for GEN01 vs GEN02+?
- `master_back` correct if redoing a clip?
- active `@refN` tags only for references actually needed?
- exact same width, height, fps?
- boat/vehicle speed and heading explicitly continuous?
- persistent props and wetness consistent?
- hard cuts define the first new frame?
- final frame simple enough to continue cleanly?

If all of those are correct, render the next generation and use the saved H3 latent, not the stitched MP4, as the continuation source.
