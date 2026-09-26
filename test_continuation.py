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

    def test_passes_director_prompt_and_native_latent_to_sato(self):
        received = {}

        class Context:
            @staticmethod
            def INPUT_TYPES():
                return {"required": {"prompt": ("STRING", {"default": ""})}}

            @staticmethod
            def generate(**kwargs):
                received.update(kwargs)
                return "model", "context"

        nodes = types.SimpleNamespace(NODE_CLASS_MAPPINGS={
            "MiniMaxH3EasyContextSegments_SatoDive": Context,
        })
        with patch.dict(sys.modules, {"nodes": nodes}):
            result = self.module.MiniMaxH3DirectorContinuation.execute(
                "bundle", "He picks up the necklace", "AV latent", 864, 480,
                seconds=8, fps=24,
            )
        self.assertEqual(result, ("model", "context"))
        self.assertEqual(received["prompt"], "He picks up the necklace")
        self.assertEqual(received["seed_latent"], "AV latent")
        self.assertEqual((received["width"], received["height"]), (864, 480))
        self.assertEqual(received["resolution"], "custom")
        self.assertEqual(received["continuity_mode"], "Native Guide")

    def test_reports_missing_dependency(self):
        with patch.dict(sys.modules, {"nodes": types.SimpleNamespace(NODE_CLASS_MAPPINGS={})}):
            with self.assertRaisesRegex(RuntimeError, "Install and enable SatoDive"):
                self.module.MiniMaxH3DirectorContinuation.execute(
                    "bundle", "next shot", "AV latent", 864, 480,
                )


if __name__ == "__main__":
    unittest.main()
