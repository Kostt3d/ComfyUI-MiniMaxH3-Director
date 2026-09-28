from comfy_api.latest import ComfyExtension, io
from typing_extensions import override

# Canonical ProMax core: exact mature Director backend + UI, with the optimized
# compile-only/auto-prune continuation path layered on top.
from .promax_core import MiniMaxH3PromaxDirectorCore as MiniMaxH3Director
from .minimax_enhance import MiniMaxH3EnhancePrompt
from .minimax_lastframe import MiniMaxH3SaveLastFrame
from .minimax_preview import MiniMaxH3PreviewOverride
from .minimax_retake import MiniMaxH3RetakeStitch
from .minimax_continuation import MiniMaxH3DirectorContinuation
from .minimax_master import MiniMaxH3DirectorMasterChain
from .minimax_run_manager import MiniMaxH3RunManager

# Only the Last Frame helper is retained from the experimental parallel ProMax node.
# The old MiniMaxH3Promax main node is deliberately not registered anymore, so there is
# only one official ProMax Director and it always gets the mature Director timeline UI.
from .minimax_promax import MiniMaxH3PromaxLastFrame
from .promax_continuation import (
    MiniMaxH3PromaxContinue,
    MiniMaxH3PromaxAppend,
    MiniMaxH3PromaxSaveLatent,
    MiniMaxH3PromaxLoadLatent,
)
from .promax_master import (
    MiniMaxH3PromaxCommitMaster,
    MiniMaxH3PromaxLoadMaster,
    MiniMaxH3PromaxPreviewTake,
)
from .promax_trim import MiniMaxH3PromaxTrimMedia


class MiniMaxH3DirectorExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            MiniMaxH3Director,
            MiniMaxH3PreviewOverride,
            MiniMaxH3RetakeStitch,
            MiniMaxH3EnhancePrompt,
            MiniMaxH3SaveLastFrame,
            MiniMaxH3DirectorContinuation,
            MiniMaxH3DirectorMasterChain,
            MiniMaxH3RunManager,
            MiniMaxH3PromaxLastFrame,
            MiniMaxH3PromaxContinue,
            MiniMaxH3PromaxAppend,
            MiniMaxH3PromaxSaveLatent,
            MiniMaxH3PromaxLoadLatent,
            MiniMaxH3PromaxCommitMaster,
            MiniMaxH3PromaxLoadMaster,
            MiniMaxH3PromaxPreviewTake,
            MiniMaxH3PromaxTrimMedia,
        ]


async def comfy_entrypoint() -> MiniMaxH3DirectorExtension:
    return MiniMaxH3DirectorExtension()


NODE_CLASS_MAPPINGS = {
    # Official ProMax main node. Public id intentionally stays MiniMaxH3DirectorCS so the
    # mature Director JavaScript timeline attaches without any duplicated UI implementation.
    "MiniMaxH3DirectorCS": MiniMaxH3Director,

    # ProMax continuation / Take-Commit extensions.
    "MiniMaxH3PromaxLastFrame": MiniMaxH3PromaxLastFrame,
    "MiniMaxH3PromaxContinue": MiniMaxH3PromaxContinue,
    "MiniMaxH3PromaxAppend": MiniMaxH3PromaxAppend,
    "MiniMaxH3PromaxSaveLatent": MiniMaxH3PromaxSaveLatent,
    "MiniMaxH3PromaxLoadLatent": MiniMaxH3PromaxLoadLatent,
    "MiniMaxH3PromaxCommitMaster": MiniMaxH3PromaxCommitMaster,
    "MiniMaxH3PromaxLoadMaster": MiniMaxH3PromaxLoadMaster,
    "MiniMaxH3PromaxPreviewTake": MiniMaxH3PromaxPreviewTake,
    "MiniMaxH3PromaxTrimMedia": MiniMaxH3PromaxTrimMedia,

    # Existing Director helpers remain available.
    "MiniMaxH3PreviewOverrideCS": MiniMaxH3PreviewOverride,
    "MiniMaxH3RetakeStitchCS": MiniMaxH3RetakeStitch,
    "MiniMaxH3EnhancePromptCS": MiniMaxH3EnhancePrompt,
    "MiniMaxH3SaveLastFrameCS": MiniMaxH3SaveLastFrame,
    "MiniMaxH3DirectorContinuationCS": MiniMaxH3DirectorContinuation,
    "MiniMaxH3DirectorMasterChainCS": MiniMaxH3DirectorMasterChain,
    "MiniMaxH3RunManagerCS": MiniMaxH3RunManager,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3DirectorCS": "MiniMax H3 ProMax · Director Core",
    "MiniMaxH3PromaxLastFrame": "Promax · Last Frame",
    "MiniMaxH3PromaxContinue": "Promax · Latent Continuation",
    "MiniMaxH3PromaxAppend": "Promax · Append Continuation",
    "MiniMaxH3PromaxSaveLatent": "Promax · Save AV Latent",
    "MiniMaxH3PromaxLoadLatent": "Promax · Load AV Latent",
    "MiniMaxH3PromaxCommitMaster": "Promax · COMMIT Take → Master",
    "MiniMaxH3PromaxLoadMaster": "Promax · Load Master",
    "MiniMaxH3PromaxPreviewTake": "Promax · Preview CURRENT TAKE",
    "MiniMaxH3PromaxTrimMedia": "Promax · Trim Continuation Media",
    "MiniMaxH3PreviewOverrideCS": "MiniMax H3 Preview Override",
    "MiniMaxH3RetakeStitchCS": "MiniMax H3 Retake Stitch",
    "MiniMaxH3EnhancePromptCS": "MiniMax H3 Enhance Prompt",
    "MiniMaxH3SaveLastFrameCS": "MiniMax H3 Save Last Frame",
    "MiniMaxH3DirectorContinuationCS": "MiniMax H3 Director 12GB Engine",
    "MiniMaxH3DirectorMasterChainCS": "MiniMax H3 Director 12GB Master Chain",
    "MiniMaxH3RunManagerCS": "MiniMax H3 12GB Run Manager",
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
