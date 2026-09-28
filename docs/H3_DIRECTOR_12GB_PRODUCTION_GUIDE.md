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
