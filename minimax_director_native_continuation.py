"""Native continuation adapter for MiniMax H3 Director conditioning.

Director remains the source of conditioning, so Ref2VA Picture/Video/Audio
references already encoded by Director are preserved. This adapter only adds a
synchronised tail guide from the previous H3 AV latent.
"""

from collections.abc import Mapping

from comfy_api.latest import io


class MiniMaxH3DirectorNativeContinuation(io.ComfyNode):
    """Add previous-clip AV latent context to Director's existing conditioning."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3DirectorNativeContinuationCS",
            display_name="Director V2 - native continuation",
            category="MiniMax H3 Director",
            description=(
                "Continuation that preserves Director conditioning verbatim, including "
                "Ref2VA Picture/Video/Audio references. Connect Director positive + latent "
                "and the previous sampled H3 AV latent."
            ),
            inputs=[
                io.Conditioning.Input("director_positive"),
                io.Latent.Input("director_latent"),
                io.Latent.Input("previous_latent"),
                io.Int.Input(
                    "context_length", default=22, min=5, max=345, step=17,
                    tooltip="Previous-video context in frames. H3 native guide grid: 5, 22, 39, 56...",
                ),
            ],
            outputs=[
                io.Conditioning.Output(display_name="positive"),
                io.Latent.Output(display_name="latent"),
            ],
            is_experimental=True,
        )

    @staticmethod
    def _streams(latent, name):
        if not isinstance(latent, Mapping) or "samples" not in latent:
            raise ValueError(f"{name} must be a MiniMax H3 AV latent")
        samples = latent["samples"]
        streams = getattr(samples, "tensors", None)
        if not isinstance(streams, (tuple, list)) or len(streams) != 2:
            raise ValueError(f"{name} must contain MiniMax H3 video + audio streams")
        video, audio = streams
        if getattr(video, "ndim", 0) != 5 or getattr(audio, "ndim", 0) != 4:
            raise ValueError(f"{name} has incompatible MiniMax H3 AV stream shapes")
        return video, audio

    @staticmethod
    def _video_tokens_for_frames(frames):
        frames = int(frames)
        if frames < 5 or (frames - 5) % 17 != 0:
            raise ValueError("context_length must follow H3's native 17k+5 frame grid")
        return ((frames - 5) // 17) * 5 + 2

    @staticmethod
    def _audio_tokens_for_frames(frames):
        return round(int(frames) * 40 / 24)

    @classmethod
    def execute(cls, director_positive, director_latent, previous_latent, context_length=22):
        previous_video, previous_audio = cls._streams(previous_latent, "previous_latent")
        target_video, _target_audio = cls._streams(director_latent, "director_latent")

        if tuple(previous_video.shape[-2:]) != tuple(target_video.shape[-2:]):
            raise ValueError(
                "Continuation requires the previous clip and this Director scene to use the "
                "same resolution/aspect ratio; latent spatial sizes do not match."
            )

        video_tokens = cls._video_tokens_for_frames(context_length)
        audio_tokens = cls._audio_tokens_for_frames(context_length)
        if video_tokens > int(previous_video.shape[2]):
            raise ValueError("previous_latent is shorter than the requested context_length")
        if audio_tokens > int(previous_audio.shape[-1]):
            raise ValueError("previous_latent audio is shorter than the requested context_length")
        if not isinstance(director_positive, list) or not director_positive:
            raise ValueError("Connect Director's positive conditioning output")

        guide = {
            "resolved_frame_index": 0,
            "latent": previous_video[:, :, -video_tokens:].clone(),
            "audio_latent": previous_audio[..., -audio_tokens:].clone(),
        }

        guided = []
        for embedding, metadata in director_positive:
            values = dict(metadata)
            # Ref2VA references are stored in the conditioning metadata (minimax_refs).
            # Copying the metadata keeps those tensors intact; only keyframes are extended.
            keyframes = [dict(item) for item in (values.get("minimax_keyframes") or [])]
            conflicts = [
                item for item in keyframes
                if 0 <= float(item.get("resolved_frame_index", -1)) < int(context_length)
            ]
            if conflicts:
                raise ValueError(
                    "Director already contains an opening keyframe inside the continuation "
                    "context window. Remove the first-frame keyframe for this continuation "
                    "scene; Picture/Video references can stay enabled."
                )
            keyframes.append(dict(guide))
            keyframes.sort(key=lambda item: float(item.get("resolved_frame_index", 0)))
            values["minimax_keyframes"] = keyframes
            guided.append([embedding, values])

        return io.NodeOutput(guided, director_latent)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3DirectorNativeContinuationCS": MiniMaxH3DirectorNativeContinuation,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3DirectorNativeContinuationCS": "Director V2 - native continuation",
}
