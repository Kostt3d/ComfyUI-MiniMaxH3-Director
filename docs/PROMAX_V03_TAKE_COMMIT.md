# MiniMax H3 Promax v0.3 — Take / Commit Master

## Goal

Promax treats every new generation as a **Take** until you explicitly validate it.

A rejected Take never changes the series Master and does not create a numbered latent file.
Only `Promax · COMMIT Take → Master` can replace the deterministic Master file.

Default Master path:

```text
ComfyUI/output/Promax/masters/MASTER.promaxlatent
```

The write is atomic: Promax writes a temporary file first, then replaces the Master only after the new file has been written successfully.

## First clip

1. Generate clip 1 normally with `MiniMax H3 Promax · Director`.
2. Connect the final `SamplerCustomAdvanced.output` to `Promax · COMMIT Take → Master.candidate_latent`.
3. Leave `commit_take = TAKE ONLY` while judging the render.
4. If the Take is rejected, change the seed/prompt/camera and render again. The Master does not exist or does not change.
5. When the Take is accepted, keep the same seed/input values, set `commit_take = COMMIT TAKE`, and queue once. ComfyUI can reuse the unchanged sampled result from cache. Promax writes `MASTER.promaxlatent`.

Use a fixed seed while reviewing a Take. Do not use `randomize after generate` if you intend to commit that exact cached Take on the next queue.

## Continuation clip

Recommended chain:

```text
Promax · Load Master
        │
        ├──────────────► Promax · Latent Continuation.previous_latent
        │
        └──────────────► Promax · Append Continuation.previous_latent

MiniMax H3 Promax · Director (new shot, T2V)
        │ positive
        ▼
Promax · Latent Continuation
        │ positive                 │ latent
        ▼                          ▼
BasicGuider                 SamplerCustomAdvanced
                                   │ output
                                   ▼
                         Promax · Append Continuation
                                   │
                         cumulative_latent
                                   ▼
                    Promax · COMMIT Take → Master
```

Keep `COMMIT TAKE` OFF while trying alternatives. Each rejected Take starts again from the same last committed Master.

When one Take is accepted, turn `COMMIT TAKE` ON. The candidate cumulative latent replaces the one deterministic Master file. The following clip loads this new Master.

### Example

```text
MASTER = clips 1 + 2 validated

Take 3A  -> rejected -> MASTER unchanged
Take 3B  -> rejected -> MASTER unchanged
Take 3C  -> accepted -> COMMIT -> MASTER = clips 1 + 2 + 3C
```

## Exporting the visible Take

`Promax · Append Continuation.sampled_window` still contains the hidden overlap used for H3 continuity.
Decode `sampled_window`, then pass decoded video/audio through `Promax · Trim Continuation Media` using `trim_overlap_frames` from `Promax · Append Continuation`.

This produces only the new visible section, without repeating the overlap at the start.

## Reference Bank: 9 image references

Promax v0.3 exposes `reference_1` through `reference_9`.

The reference UI uses stable bank tags:

```text
@ref1 ... @ref9
```

Promax remaps selected bank slots to compact native H3 tokens (`<Picture 1>`, `<Picture 2>`, ...), so sparse bank slots are safe.

Three policies are available:

- `Auto (12GB)`: if the prompt contains explicit `@refN` tags, only those loaded references are encoded. If no tags are present, Promax uses the first `auto_ref_limit` loaded image references. Default limit: 4.
- `Manual`: encode the bank slots listed in `manual_refs`, for example `1,4,7`.
- `Force all loaded`: encode every loaded image reference. This exists for testing and larger VRAM systems; it is not the recommended default for a 12 GB card.

Reference video and reference audio remain separate from this image-bank pruning.

## First real GPU validation

Start with:

- 480 × 864
- fixed seed 42
- clip 1 around 5 seconds
- continuation overlap: 22 frames
- visible extension: 119 frames
- `COMMIT TAKE` OFF during all trial renders

Validate:

- no Master file modification on rejected Takes;
- exact Take is committed when the toggle is enabled without changing upstream inputs;
- next continuation loads the committed Master;
- no visible repeated overlap after `Trim Continuation Media`;
- character/face stability, camera continuity, audio seam and morphing during the first second.
