"""Contract tests for the optional SatoDive integration, without ComfyUI/GPU."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class _Port:
    @staticmethod
    def Input(*_args, **_kwargs):
        return object()

    @staticmethod
    def Output(*_args, **_kwargs):
        return object()


class _IO:
    ComfyNode = object
    Model = Clip = Latent = Video = String = Int = Float = _Port

    @staticmethod
    def Custom(_name):
        return _Port

    @staticmethod
    def Schema(**kwargs):
        return kwargs

    @staticmethod
    def NodeOutput(*values):
        return values


class ContinuationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        latest = types.ModuleType("comfy_api.latest")
        latest.io = _IO
        comfy_api = types.ModuleType("comfy_api")
        comfy_api.latest = latest
        with patch.dict(sys.modules, {"comfy_api": comfy_api, "comfy_api.latest": latest}):
            spec = importlib.util.spec_from_file_location(
                "minimax_continuation", pathlib.Path(__file__).with_name("minimax_continuation.py")
            )
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    @staticmethod
    def _context(received):
        class Context:
            @staticmethod
            def INPUT_TYPES():
                return {"required": {"prompt": ("STRING", {"default": ""})}}

            @staticmethod
            def generate(**kwargs):
                received.update(kwargs)
                return "model", "context"
        return Context

    def test_passes_director_prompt_and_native_latent_to_sato(self):
        received = {}
        nodes = types.SimpleNamespace(NODE_CLASS_MAPPINGS={
            "MiniMaxH3EasyContextSegments_SatoDive": self._context(received),
        })
        with patch.dict(sys.modules, {"nodes": nodes}):
            result = self.module.MiniMaxH3DirectorContinuation.execute(
                "bundle", "[0s-8s] He picks up the necklace", "AV latent", 864, 480,
                seconds=8, fps=24,
            )
        self.assertEqual(result, ("model", "context"))
        self.assertEqual(received["prompt"], "[0s-8s] He picks up the necklace")
        self.assertEqual(received["seed_latent"], "AV latent")
        self.assertEqual((received["width"], received["height"]), (864, 480))
        self.assertEqual(received["resolution"], "custom")
        self.assertEqual(received["continuity_mode"], "Native Guide")
        self.assertEqual(received["seconds"], 8)

    def test_zero_seconds_inherits_director_scene_duration(self):
        received = {}
        nodes = types.SimpleNamespace(NODE_CLASS_MAPPINGS={
            "MiniMaxH3EasyContextSegments_SatoDive": self._context(received),
        })
        prompt = "[0s-3s] She stops.\n[3s-10s] She throws the helmet."
        with patch.dict(sys.modules, {"nodes": nodes}):
            self.module.MiniMaxH3DirectorContinuation.execute(
                "bundle", prompt, "AV latent", 864, 480, seconds=0, fps=24,
            )
        self.assertEqual(received["seconds"], 10.0)

    def test_manual_seconds_overrides_director_duration(self):
        received = {}
        nodes = types.SimpleNamespace(NODE_CLASS_MAPPINGS={
            "MiniMaxH3EasyContextSegments_SatoDive": self._context(received),
        })
        with patch.dict(sys.modules, {"nodes": nodes}):
            self.module.MiniMaxH3DirectorContinuation.execute(
                "bundle", "[0s-10s] scene", "AV latent", 864, 480,
                seconds=6.5, fps=24,
            )
        self.assertEqual(received["seconds"], 6.5)

    def test_zero_seconds_requires_timed_director_prompt(self):
        with self.assertRaisesRegex(ValueError, "no timed"):
            self.module._director_scene_seconds("untimed prompt")

    def test_reports_missing_dependency(self):
        with patch.dict(sys.modules, {"nodes": types.SimpleNamespace(NODE_CLASS_MAPPINGS={})}):
            with self.assertRaisesRegex(RuntimeError, "Install and enable SatoDive"):
                self.module.MiniMaxH3DirectorContinuation.execute(
                    "bundle", "[0s-8s] next shot", "AV latent", 864, 480,
                )


if __name__ == "__main__":
    unittest.main()
