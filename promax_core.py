"""Canonical MiniMax H3 ProMax Director core.

This intentionally reuses the mature Director backend and public node id so the existing
Director timeline UI attaches unchanged. ProMax features are layered around this core
instead of maintaining a second H3 encoder/UI implementation.
"""

from comfy_api.latest import io

from .minimax_perf import MiniMaxH3DirectorOptimized


class MiniMaxH3PromaxDirectorCore(MiniMaxH3DirectorOptimized):
    """Director-compatible ProMax core with the mature timeline UI and exact H3 encoding."""

    @classmethod
    def define_schema(cls):
        # Build a fresh Schema instead of mutating the Director Schema in place. ComfyUI's
        # node registry/search can retain metadata captured when the original Schema is
        # constructed; mutating display_name/category afterwards left the node indexed as
        # the old Director even though Python saw the new values.
        base = super().define_schema()
        return io.Schema(
            # Keep the public id the mature Director JS recognises. This is what makes the
            # existing full timeline/media/reference UI attach to ProMax without copying it.
            node_id="MiniMaxH3DirectorCS",
            display_name="MiniMax H3 ProMax · Director Core",
            category="MiniMax H3/Promax",
            description=(
                "ProMax main Director. Uses the exact mature MiniMax H3 Director timeline, "
                "media/reference handling, planner and H3 conditioning path, plus the 12 GB "
                "compile-only/auto-prune optimizations used by ProMax continuation workflows."
            ),
            inputs=base.inputs,
            outputs=base.outputs,
        )
