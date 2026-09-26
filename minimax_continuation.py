"""Bridge the Director storyboard to SatoDive's native AV latent continuation.

This node deliberately delegates context planning to the installed SatoDive
extension. It does not decode and re-encode the previous clip.
"""

import re

from comfy_api.latest import io


H3Bundle = io.Custom("MINIMAX_H3_BUNDLE")
H3Context = io.Custom("MINIMAX_H3_CONTEXT")


# Director compiles timeline shots as markers such as [0s-1.5s].  Keep the
# expression deliberately tolerant of whitespace and markers without an `s`, so
# older saved workflows and hand-edited prompts keep working.
_SHOT_RANGE_RE = re.compile(
    r"\[\s*\d+(?:\.\d+)?\s*s?\s*-\s*(\d+(?:\.\d+)?)\s*s?\s*\]",
    re.IGNORECASE,
)


def _director_scene_seconds(director_prompt):
    """Return the end time of the last timed shot in a compiled Director prompt."""
    ends = [float(value) for value in _SHOT_RANGE_RE.findall(str(director_prompt))]
    if not ends:
        raise ValueError(
            "Continuation seconds is 0 (Director duration mode), but the Director prompt "
            "contains no timed [start-end] shot markers. Set seconds manually, or connect "
            "the compiled prompt output from MiniMax H3 Director."
        )
    seconds = max(ends)
    if seconds <= 0:
        raise ValueError("Director scene duration must be greater than zero.")
    return seconds


def _context_node():
    import nodes

    node_class = nodes.NODE_CLASS_MAPPINGS.get(
        "MiniMaxH3EasyContextSegments_SatoDive"
    )
    if node_class is None:
        raise RuntimeError(
            "Install and enable SatoDive/Minimax-H3-Latent-Continuation, "
            "then restart ComfyUI."
        )
    return node_class


class MiniMaxH3DirectorContinuation(io.ComfyNode):
    """Use the Director's compiled timeline as the next SatoDive segment prompt."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3DirectorContinuationCS",
            display_name="MiniMax H3 Director Latent Continuation",
            category="MiniMax H3 Director",
            description=(
                "Connect Director.prompt and the prior SatoDive AV latent. "
                "With seconds=0, the continuation duration is read automatically from "
                "the Director storyboard timeline. Set seconds above 0 only to override it. "
                "Connect the outputs to SatoDive Segment Sample and Decode. Timeline media "
                "references must also be supplied to SatoDive."
            ),
            inputs=[
                H3Bundle.Input("h3_bundle"),
                io.String.Input("director_prompt", force_input=True),
                io.Latent.Input("seed_latent"),
                io.Int.Input("width", force_input=True, min=32, max=8192),
                io.Int.Input("height", force_input=True, min=32, max=8192),
                io.Float.Input(
                    "seconds", default=0.0, min=0.0, max=120.0,
                    tooltip=(
                        "0 = automatically use this Director scene's timeline duration. "
                        "Set a value above 0 to override the Director duration."
                    ),
                ),
                io.Float.Input("fps", default=24.0, min=1.0, max=120.0),
                io.Int.Input("context_length", default=22, min=1, max=128),
                io.Video.Input("seed_video", optional=True),
            ],
            outputs=[
                io.Model.Output(display_name="model"),
                H3Context.Output(display_name="h3_context"),
            ],
        )

    @classmethod
    def execute(
        cls, h3_bundle, director_prompt, seed_latent, width, height,
        seconds=0.0, fps=24.0, context_length=22, seed_video=None,
    ):
        if not str(director_prompt).strip():
            raise ValueError("Director prompt is empty; add a shot to the timeline.")
        if int(width) < 32 or int(height) < 32:
            raise ValueError("Connect Director width and height outputs.")

        # A zero value means Director owns the scene length.  The compiled prompt is the
        # most reliable source here: it already reflects the Director render window and
        # the exact scene timing, while keeping this bridge independent of Director's UI.
        resolved_seconds = float(seconds)
        if resolved_seconds <= 0:
            resolved_seconds = _director_scene_seconds(director_prompt)

        context_class = _context_node()
        required = context_class.INPUT_TYPES()["required"]
        defaults = {
            name: spec[1].get("default")
            for name, spec in required.items()
            if len(spec) > 1 and isinstance(spec[1], dict)
            and "default" in spec[1]
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
            ref_image_size="1K area (~1MP)",
            reference_mention_mode="By index",
            context_prompt_optimizer_mode="1",
            context_prompt_optimizer_concurrency=1,
            seed_latent=seed_latent,
            seed_video=seed_video,
        )
        model, context = context_class.generate(**defaults)
        return io.NodeOutput(model, context)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3DirectorContinuationCS": MiniMaxH3DirectorContinuation,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3DirectorContinuationCS": "MiniMax H3 Director Latent Continuation",
}
