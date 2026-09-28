"""Minimal schema/native-call doubles. Not an inference or ComfyUI integration test."""
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]

class Field:
    def __init__(self, id=None, **kw):
        self.id = id
        self.kw = kw

class Kind:
    Input = Output = Field

class Output:
    def __init__(self, *args, **kw):
        self.args = args
        self.ui = kw.get('ui')

io = types.SimpleNamespace(ComfyNode=object, NodeOutput=Output,
    Schema=lambda **kw: types.SimpleNamespace(**kw),
    **{key: Kind for key in ['Model', 'Clip', 'Vae', 'Combo', 'String', 'Float', 'Image', 'Audio', 'Conditioning', 'Latent']})

def load():
    name = '_promax_test_package'
    package = types.ModuleType(name); package.__path__ = [str(ROOT)]
    sys.modules[name] = package
    latest = types.ModuleType('comfy_api.latest'); latest.io = io
    sys.modules['comfy_api'] = types.ModuleType('comfy_api')
    sys.modules['comfy_api.latest'] = latest
    for file in ['promax_plan', 'minimax_core', 'minimax_promax']:
        spec = importlib.util.spec_from_file_location(name + '.' + file, ROOT / (file + '.py'))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name + '.minimax_promax'], sys.modules[name + '.promax_plan']
