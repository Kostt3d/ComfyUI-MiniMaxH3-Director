"""Visible controls for the three paths in the bundled 12 GB workflow.

The frontend applies these switches to marked nodes in the same graph. The
backend deliberately does not modify global ComfyUI node modes: execution on
the server must reflect the graph the user actually queued.
"""

from comfy_api.latest import io


class MiniMaxH3RunManager(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3RunManagerCS",
            display_name="MiniMax H3 12GB Run Manager",
            category="MiniMax H3 Director",
            description=(
                "Choose simple OR continuation, then optionally enable latent upscale. "
                "The switches control the marked Load Latent, Master Chain and upscale "
                "nodes in the bundled 12 GB workflow. Keep the same canvas across clips."
            ),
            inputs=[
                io.Boolean.Input("generation_simple", default=True,
                                 label_on="SIMPLE ON", label_off="SIMPLE OFF"),
                io.Boolean.Input("generation_continue", default=False,
                                 label_on="CONTINUE ON", label_off="CONTINUE OFF"),
                io.Boolean.Input("upscale_latent", default=False,
                                 label_on="UPSCALE ON", label_off="UPSCALE OFF"),
            ],
            outputs=[io.String.Output(display_name="mode")],
        )

    @classmethod
    def execute(cls, generation_simple=True, generation_continue=False,
                upscale_latent=False):
        if bool(generation_simple) == bool(generation_continue):
            raise ValueError("Choose exactly one: generation_simple or generation_continue.")
        mode = "CONTINUE" if generation_continue else "SIMPLE"
        return io.NodeOutput(mode + (" + LATENT UPSCALE" if upscale_latent else ""))
