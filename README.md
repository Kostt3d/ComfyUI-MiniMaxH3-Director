# ComfyUI MiniMax H3 Director

A MiniMax H3 Director fork focused on practical production workflows in ComfyUI, including a validated 12 GB path for RTX 50-series GPUs.

## New: H3 Director 12GB 2-in-1 workflow

The recommended workflow is now a **single 2-in-1 production graph**:

1. **Latent continuation chain** keeps H3 continuity lossless from one generated clip to the next.
2. **Cumulative master chain** automatically builds the review/export episode master: GEN01, then GEN01+GEN02, then GEN01+GEN02+GEN03, and so on.

The two chains are deliberately separate:

- the **saved H3 latent** is the only continuation state used for the next H3 generation;
- the **cumulative MP4 master** is for review/export only and is never fed back into H3.

Official example workflow:

[`example_workflows/H3 Director 12GB - 2-in-1 Latent + Master Chain.json`](example_workflows/H3%20Director%2012GB%20-%202-in-1%20Latent%20%2B%20Master%20Chain.json)

Detailed production guide:

[`docs/H3_DIRECTOR_12GB_PRODUCTION_GUIDE.md`](docs/H3_DIRECTOR_12GB_PRODUCTION_GUIDE.md)

### What one run produces

Each generation can produce all three outputs at once:

- the **current clip** only, typically about 15 seconds;
- the **joint H3 AV latent** for the next generation;
- the **cumulative master** for the episode.

### GEN01

For the first generation:

- keep **Load Previous Latent** bypassed/disconnected;
- set **Master Chain `reset_chain = ON`**;
- write the GEN01 prompt in Director;
- run the workflow.

Outputs:

- current clip = GEN01;
- saved latent = continuation state for GEN02;
- cumulative master = GEN01.

### GEN02 and later

For every following generation:

- enable **Load Previous Latent**;
- select the latent saved by the immediately previous generation;
- set **Master Chain `reset_chain = OFF`**;
- keep `master_back = 0` during normal forward production;
- replace the Director prompt with GEN02, GEN03, etc.;
- run.

The Master Chain automatically finds the newest matching saved master, aligns the continuation overlap, removes the duplicated overlap region, preserves the source audio through the join, and appends the new clip.

Example:

```text
GEN01 -> clip 01 + latent 01 + master 01
GEN02 -> clip 02 + latent 02 + master 01+02
GEN03 -> clip 03 + latent 03 + master 01+02+03
GEN04 -> clip 04 + latent 04 + master 01+02+03+04
```

### Redoing an already assembled generation

If the newest cumulative master already contains a generation you want to redo, use the latent from the generation before it and set:

```text
master_back = 1
```

Example: if `MASTER 01+02+03` already exists and you redo GEN03, use latent GEN02 plus `master_back=1` so the chain rebuilds from master `01+02` instead of appending to the already assembled GEN03.

## Validated 12 GB baseline

The production path was validated around this configuration:

```text
480 x 864 portrait
24 fps
about 15 s per generation
Euler
Simple scheduler
8 steps
video sigma = 6
audio sigma = 3
context_length = 5
ref_image_size = match
Director compile_only = ON
Director auto_prune_refs = ON
low_vram_cleanup = ON
```

Recommended model stack used during validation:

```text
Qwen3-VL 4B FP8
+ MiniMax H3 ClipProj 4B -> 5120
+ MiniMax H3 Video VAE
+ MiniMax H3 Audio VAE
+ Ref2VA pruned FP8-scaled transformer
+ LightX2V Ref2VA Turbo 8-step LoRA
```

On the validated CUDA 13.0 / PyTorch cu130 setup, **native PyTorch attention is the recommended default**. The optional KJ/SageAttention node is kept in the example workflow but is bypassed by default. Enable it only if your local SageAttention build imports and runs correctly.

The 2-in-1 production workflow has a separate, bypassed H3 3D latent upscale
and segment-refine export path. It requires the compatible
[`Kostt3d/Minimax-H3-Latent-Continuation-v2`](https://github.com/Kostt3d/Minimax-H3-Latent-Continuation-v2)
fork and an H3 latent upscaler checkpoint. It leaves the original saved AV
latent and cumulative master at their baseline resolution. See the
[production guide](docs/H3_DIRECTOR_12GB_PRODUCTION_GUIDE.md#optional-3d-latent-upscale-export)
before enabling the three optional nodes.

The same workflow now has a **12GB Run Manager** with three switches: Simple,
Continue and Latent Upscale. Simple and Continue are exclusive; Latent Upscale
is independent. The manager synchronizes the marked latent loader, master
reset, refine, upscale decode and upscale export nodes in this workflow.

The 2-in-1 graph now wires one experimental **FL2VA/Ref2VA hybrid checkpoint**
to both model roles of SatoDive Model Adapter. It avoids loading two H3
transformers together on the 12 GB path. The hybrid with the existing Ref2VA
Turbo LoRA has not been validated on the target RTX 5070; compare a simple
scene and a reference-conditioned scene before using it for production.

An optional **Realism People** LoRA has also been tested conservatively at strength `0.2`; when used, include the trigger `r34l1sm` in the prompt.

## Fixed reference library and auto-prune

Director keeps the reference library stable as `@ref1` through `@ref9`.

With `auto_prune_refs = ON`, unused subject images are removed from the current conditioning pass while the slot numbering stays stable.

Pruning is **explicit-tag based**. If a person, vehicle, prop, creature, or environment must be conditioned in the current generation, call the appropriate `@refN` somewhere in the Director text.

Example:

```text
ACTIVE REFERENCES:
@ref2 Joel
@ref5 Serge
@ref7 fishing boat
```

If no explicit `@refN` tag exists, all references are retained for backward compatibility.

## Prompting lessons for latent continuation

Multi-character continuation is much more stable when prompts preserve simple film grammar and physical continuity.

Recommended rules:

- define camera geography before actions;
- use hard cuts when changing principal referenced characters;
- explicitly define the first image after an important cut;
- avoid camera moves that travel across several referenced identities;
- let characters remain off camera instead of forcing the whole cast into every shot;
- preserve object state, wetness, clothing, direction, weather and completed actions across generations;
- when a vehicle or boat is moving, keep visible motion cues in every close-up: moving horizon, passing water, wind, vibration, pitch/roll and body-balance corrections;
- keep the last composition of a generation simple and stable because it becomes the continuation state for the next one.

The full production notes are in [`docs/H3_DIRECTOR_12GB_PRODUCTION_GUIDE.md`](docs/H3_DIRECTOR_12GB_PRODUCTION_GUIDE.md).

## Nodes added by the 12 GB path

### MiniMax H3 Director 12GB Engine

A unified continuation node used for both first generation and later continuation:

- GEN01: leave `seed_latent` disconnected;
- GEN02+: connect the previous saved H3 latent;
- receives Director `scene`, prompt, dimensions and media;
- defaults to `context_length=5` and `ref_image_size=match`;
- supports low-VRAM cleanup and profiling.

### MiniMax H3 Director 12GB Master Chain

Builds a cumulative episode master independently from the latent chain:

- `reset_chain=ON` for GEN01;
- `reset_chain=OFF` for GEN02+;
- finds the newest matching saved master automatically;
- uses the real H3 continuation context when available to align overlap;
- supports `master_back` when replacing a previous generation;
- performs overlap-aware stitch and optional color drift correction.

## Installation

Clone this repository into `ComfyUI/custom_nodes` and install SatoDive's MiniMax H3 latent continuation extension separately:

```powershell
cd ComfyUI/custom_nodes
git clone https://github.com/Kostt3d/ComfyUI-MiniMaxH3-Director.git
git clone https://github.com/Kostt3d/Minimax-H3-Latent-Continuation-v2.git
```

Restart ComfyUI and hard-refresh the frontend if the Director UI looks stale.

Do not keep two copies of MiniMax H3 Director installed at the same time.

## Updating

```powershell
cd C:\Users\kosto\Documents\ComfyUI\custom_nodes\ComfyUI-MiniMaxH3-Director
git switch main
git pull origin main
```

Restart ComfyUI after updating.

## Main Director features

MiniMax H3 Director remains a timeline-oriented prompt editor for H3. It supports:

- shot timeline editing;
- `@ref1` to `@ref9` subject/reference slots;
- images, video and audio references;
- prompt compilation;
- summary, soundscape and non-diegetic music fields;
- Ref2VA reference workflows;
- retake tools;
- prompt enhancement;
- first/last frame and storyboard-oriented workflows;
- 12 GB compile-only and auto-prune path in this fork.

## Legacy example workflows

The original examples remain useful for learning individual pieces of the stack:

- `MiniMax H3 Director.json`
- `MiniMax H3 Director + Save Latent.json`
- `MiniMax H3 Director + Latent Continuation.json`
- `MiniMax H3 Director + Enhance Prompt.json`

For new 12 GB episodic production, prefer the **2-in-1 Latent + Master Chain** workflow above.

## Requirements

- ComfyUI with MiniMax H3 support
- Python 3.10+
- MiniMax H3 model files required by your selected workflow
- SatoDive MiniMax H3 Latent Continuation for the 12 GB continuation/master path

Model filenames can vary by quantization and local installation. Adjust loader dropdowns to the files present on your system.

## Credits

This project is based on the MiniMax H3 Director work derived from the LTX Director timeline editor by **WhatDreamsCost**, adapted for MiniMax H3.

The 12 GB latent continuation and master-chain path integrates with the separate SatoDive MiniMax H3 Latent Continuation project. SatoDive's code is not copied into this repository.

## License

GPL-3.0. See [`LICENSE`](LICENSE).
