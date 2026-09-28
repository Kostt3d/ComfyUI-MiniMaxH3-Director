"""Pure-Python tests for the low-VRAM Director performance wrapper."""

import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).parent
PACKAGE = "_minimax_perf_testpkg"


class _Input:
    def __init__(self, id, **kwargs):
        self.id = id
        self.lazy = kwargs.get("lazy")
        self.optional = kwargs.get("optional", False)


class _Port:
    @staticmethod
    def Input(id, **kwargs):
        return _Input(id, **kwargs)

    @staticmethod
    def Output(*_args, **_kwargs):
        return object()


class _IO:
    ComfyNode = object
    Boolean = _Port

    @staticmethod
    def NodeOutput(*values):
        return values


class _BaseDirector:
    @classmethod
    def define_schema(cls):
        return types.SimpleNamespace(inputs=[
            _Input("model", lazy=True, optional=True),
            _Input("model_ref2va", lazy=True, optional=True),
            _Input("clip"),
            _Input("vae"),
            _Input("audio_vae", optional=True),
            _Input("timeline_data"),
        ])

    @classmethod
    def check_lazy_status(cls, **_kwargs):
        return []

    @classmethod
    def execute(cls, **kwargs):
        return ("base", kwargs)


class PerfHelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]

        latest = types.ModuleType("comfy_api.latest")
        latest.io = _IO
        comfy_api = types.ModuleType("comfy_api")
        comfy_api.latest = latest

        fake_media = types.ModuleType(f"{PACKAGE}.minimax_media")
        fake_plan = types.ModuleType(f"{PACKAGE}.minimax_plan")
        fake_plan.MODEL_FPS = 24.0
        fake_plan.ROLE_LAST = "last"
        fake_plan.ROLE_FIRST = "first"
        fake_plan.REF_VIDEO_MAX_SEC = 15.0
        fake_plan.REF_VIDEO_SHORT_EDGE = 768
        fake_plan.REF_VIDEO_ASPECT_BUDGET = 1.75
        fake_plan.parse_timeline = lambda value: json.loads(value) if value else {}

        fake_core = types.ModuleType(f"{PACKAGE}.minimax_core")
        fake_core.core = lambda: object()

        fake_director = types.ModuleType(f"{PACKAGE}.minimax_director")
        fake_director.MIN_CANVAS_EDGE = 32
        fake_director.MODEL_FPS = 24.0
        fake_director.MiniMaxH3Director = _BaseDirector
        fake_director._UNCONNECTED = object()
        fake_director._grab_base_frame = lambda *_a, **_k: None
        fake_director._load_event_tensor = lambda *_a, **_k: None
        fake_director.resolve_canvas = lambda *_a, **_k: (480, 864)
        fake_director.resolve_size = lambda w, h, *_a, **_k: (w, h)
        fake_director.resolve_window = lambda *_a, **_k: (0, 360)

        modules = {
            PACKAGE: package,
            "comfy_api": comfy_api,
            "comfy_api.latest": latest,
            f"{PACKAGE}.minimax_media": fake_media,
            f"{PACKAGE}.minimax_plan": fake_plan,
            f"{PACKAGE}.minimax_core": fake_core,
            f"{PACKAGE}.minimax_director": fake_director,
        }

        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                f"{PACKAGE}.minimax_perf", ROOT / "minimax_perf.py"
            )
            cls.module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.module
            spec.loader.exec_module(cls.module)

    def test_explicit_ref_slots_ignores_subject_card_descriptions(self):
        tdata = {
            "subjects": [{"description": "self description mentions @ref9"}],
            "segments": [{"prompt": "@ref1 looks at @char3"}],
            "summary": "@character7 remains aboard",
        }
        used = self.module.explicit_ref_slots(tdata, "keep @ref2 coherent")
        self.assertEqual(used, {1, 2, 3, 7})

    def test_prune_blanks_only_unused_images_and_preserves_original(self):
        tdata = {
            "subjects": [
                {"images": ["mika"]},
                {"images": ["joel"]},
                {"images": ["rony"]},
            ]
        }
        pruned, slots = self.module.prune_unused_subject_images(tdata, {1, 3})
        self.assertEqual(slots, [2])
        self.assertEqual(pruned["subjects"][0]["images"], ["mika"])
        self.assertEqual(pruned["subjects"][1]["images"], [])
        self.assertEqual(pruned["subjects"][2]["images"], ["rony"])
        self.assertEqual(tdata["subjects"][1]["images"], ["joel"])

    def test_schema_makes_heavy_inputs_lazy_and_adds_controls(self):
        schema = self.module.MiniMaxH3DirectorOptimized.define_schema()
        by_id = {item.id: item for item in schema.inputs}
        for name in ("model", "model_ref2va", "clip", "vae", "audio_vae"):
            self.assertTrue(by_id[name].lazy)
            self.assertTrue(by_id[name].optional)
        self.assertIn("compile_only", by_id)
        self.assertIn("auto_prune_refs", by_id)

    def test_compile_only_requests_no_heavy_lazy_inputs(self):
        self.assertEqual(
            self.module.MiniMaxH3DirectorOptimized.check_lazy_status(compile_only=True),
            [],
        )


if __name__ == "__main__":
    unittest.main()
