"""Pure-Python contract tests for the cumulative 12 GB master chain."""

import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class _Input:
    def __init__(self, *args, **kwargs):
        pass


class _Port:
    Input = _Input

    @staticmethod
    def Output(*_args, **_kwargs):
        return object()


class _IO:
    ComfyNode = object
    Video = String = Boolean = Int = Combo = _Port

    @staticmethod
    def Custom(_name):
        return _Port

    @staticmethod
    def Schema(**kwargs):
        return kwargs

    @staticmethod
    def NodeOutput(*values):
        return values


class _VideoFromFile:
    def __init__(self, path):
        self.path = path


class _VideoFromComponents:
    def __init__(self, components, bit_depth=8, color_space="sRGB"):
        self.components = components
        self.bit_depth = bit_depth
        self.color_space = color_space


class MasterChainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

        latest = types.ModuleType("comfy_api.latest")
        latest.io = _IO
        latest.InputImpl = types.SimpleNamespace(
            VideoFromFile=_VideoFromFile,
            VideoFromComponents=_VideoFromComponents,
        )
        latest.Types = types.SimpleNamespace(
            VideoComponents=lambda **kwargs: kwargs,
        )
        comfy_api = types.ModuleType("comfy_api")
        comfy_api.latest = latest

        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_output_directory = lambda: cls.tmp.name

        modules = {
            "comfy_api": comfy_api,
            "comfy_api.latest": latest,
            "folder_paths": folder_paths,
        }
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                "minimax_master", pathlib.Path(__file__).with_name("minimax_master.py")
            )
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        for root, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                os.remove(os.path.join(root, name))

    def test_reset_chain_passes_current_clip_through(self):
        current = object()
        result = self.module.MiniMaxH3DirectorMasterChain.execute(
            current, reset_chain=True
        )
        self.assertIs(result[0], current)
        self.assertIn("RESET", result[1])

    def test_master_files_are_newest_first(self):
        older = os.path.join(self.tmp.name, "H3_Director_12GB_MASTER_00001.mp4")
        newer = os.path.join(self.tmp.name, "H3_Director_12GB_MASTER_00002.mp4")
        open(older, "wb").close()
        open(newer, "wb").close()
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))
        found = self.module._master_files("H3_Director_12GB_MASTER")
        self.assertEqual(found, [newer, older])

    def test_no_previous_master_falls_back_to_current(self):
        current = object()
        result = self.module.MiniMaxH3DirectorMasterChain.execute(
            current, reset_chain=False
        )
        self.assertIs(result[0], current)
        self.assertIn("No previous master", result[1])

    def test_append_calls_sato_stitch(self):
        path = os.path.join(self.tmp.name, "H3_Director_12GB_MASTER_00001.mp4")
        open(path, "wb").close()
        received = {}

        class Stitch:
            def stitch(self, source, continuation, alignment, overlap_frames,
                       search_frames, match_window, color_match="mean + contrast",
                       window_audio="from source", h3_context=None):
                received.update(
                    source=source,
                    continuation=continuation,
                    alignment=alignment,
                    overlap_frames=overlap_frames,
                    search_frames=search_frames,
                    match_window=match_window,
                    color_match=color_match,
                    window_audio=window_audio,
                    h3_context=h3_context,
                )
                return "frames", "audio", 24.0, 5, "stitched"

        nodes = types.SimpleNamespace(NODE_CLASS_MAPPINGS={
            "MiniMaxH3EasyStitchContinuation_SatoDive": Stitch,
        })
        with patch.dict(sys.modules, {"nodes": nodes}):
            result = self.module.MiniMaxH3DirectorMasterChain.execute(
                "current",
                reset_chain=False,
                h3_context="ctx",
                overlap_frames=5,
                search_frames=8,
                match_window=5,
            )
        self.assertEqual(received["alignment"], "auto")
        self.assertEqual(received["h3_context"], "ctx")
        self.assertEqual(received["overlap_frames"], 5)
        self.assertIsInstance(result[0], _VideoFromComponents)
        self.assertIn("stitched", result[1])


if __name__ == "__main__":
    unittest.main()
