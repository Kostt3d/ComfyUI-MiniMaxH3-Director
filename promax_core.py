"""Canonical MiniMax H3 ProMax Director core.

This intentionally reuses the mature Director backend and public node id so the existing
Director timeline UI attaches unchanged. ProMax features are layered around this core
instead of maintaining a second H3 encoder/UI implementation.
"""

from .minimax_perf import MiniMaxH3DirectorOptimized


class MiniMaxH3PromaxDirectorCore(MiniMaxH3DirectorOptimized):
    """Director-compatible ProMax core with the mature timeline UI and exact H3 encoding."""

    @classmethod
    def define_schema(cls):
        schema = super().define_schema()
        # Keep the public id the Director JS already recognises. Changing this id would
        # detach the 600k-line mature timeline editor and recreate the problem this class
        # exists to solve.
        schema.node_id = "MiniMaxH3DirectorCS"
        schema.display_name = "MiniMax H3 ProMax · Director Core"
        schema.category = "MiniMax H3/Promax"
        schema.description = (
            "ProMax main Director. Uses the exact mature MiniMax H3 Director timeline, "
            "media/reference handling, planner and H3 conditioning path, plus the 12 GB "
            "compile-only/auto-prune optimizations used by ProMax continuation workflows."
        )
        return schema
