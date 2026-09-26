"""Bridge the Director storyboard to SatoDive's native AV latent continuation.

This node deliberately delegates context planning to the installed SatoDive
extension. It does not decode and re-encode the previous clip.
"""

from comfy_api.latest import io


H3Bundle = io.Custom("MINIMAX_H3_BUNDLE")
H3Context = io.Custom("MINIMAX_H3_CONTEXT")


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
                "Connect the outputs to SatoDive Segment Sample and Decode. "
                "Timeline media references must also be supplied to SatoDive."
            ),
            inputs=[
                H3Bundle.Input("h3_bundle"),
                io.String.Input("director_prompt", force_input=True),
                io.Latent.Input("seed_latent"),
                io.Int.Input("width", force_input=True, min=32, max=8192),
                io.Int.Input("height", force_input=True, min=32, max=8192),
                io.Float.Input("seconds", default=8.0, min=1.0, max=120.0),
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
        seconds=8.0, fps=24.0, context_length=22, seed_video=None,
    ):
        if not str(director_prompt).strip():
            raise ValueError("Director prompt is empty; add a shot to the timeline.")
        if int(width) < 32 or int(height) < 32:
            raise ValueError("Connect Director width and height outputs.")

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
            seconds=float(seconds),
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
