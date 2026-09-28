import inspect
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
from harness import load, Output, ROOT

node, plan = load()
DEFAULT = dict(mode='T2V', preset='Balanced portrait 480x864', shots_json=plan.DEFAULT_SHOTS,
               global_prompt='Harbour at dawn.', summary='', soundscape='Waves.', music='')

class PlanTests(unittest.TestCase):
    def test_grid(self):
        p = plan.compile_plan(**DEFAULT)
        self.assertEqual(p['frames'], 124)
        self.assertAlmostEqual(p['actual_seconds'], 124/24)
        self.assertEqual((p['width'],p['height']), (480,864))
    def test_exact_portrait(self):
        p = plan.compile_plan(**(DEFAULT | {'preset': 'Portrait 9:16 576x1024'}))
        self.assertEqual(p['width'] * 16, p['height'] * 9)
    def test_reject_duration(self):
        for seconds in [0, -1, True, float('nan'), float('inf'), 16, 3]:
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                plan.compile_plan(**(DEFAULT | {'shots_json': json.dumps([{'seconds': seconds, 'camera': 'Wide.', 'action': 'Boat.'}])}))
    def test_reject_invalid_and_empty_shots(self):
        for data in ['{', '{}', '[]', '[null]', '[{"seconds":5,"camera":"","action":"a"}]']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                plan.compile_plan(**(DEFAULT | {'shots_json':data}))
    def test_camera_first_and_sound(self):
        prompt = plan.render_prompt(plan.compile_plan(**DEFAULT))
        self.assertIn('[Shot 1] Wide static shot',prompt)
        self.assertIn('[Shot 2] At 00:02.000, Low angle',prompt)
        self.assertIn('overall_soundscape: Waves.',prompt)
        self.assertIn('non_diegetic_music: N/A',prompt)
    def test_ref_fields(self):
        p = plan.compile_plan(**(DEFAULT | {'mode':'Ref2V','summary':'A ripple.'}))
        prompt = plan.render_prompt(p, [('<Picture 1>','Fisherman.','identity')])
        self.assertLess(prompt.index('summary:'),prompt.index('retention_analysis:'))
        self.assertIn('detailed_description:',prompt)
        with self.assertRaises(ValueError): plan.render_prompt(p)
    def test_no_mutation(self):
        p = plan.compile_plan(**DEFAULT); before = json.dumps(p)
        plan.render_prompt(p)
        self.assertEqual(json.dumps(p),before)

class NodeTests(unittest.TestCase):
    def test_schema_matches_signature(self):
        sig = inspect.signature(node.MiniMaxH3Promax.execute)
        for f in node.MiniMaxH3Promax.define_schema().inputs:
            self.assertIn(f.id,sig.parameters)
    def test_lazy_one_model_only(self):
        got = node.MiniMaxH3Promax.check_lazy_status(**DEFAULT, model_fl2va=None, model_ref2va=None, clip=None, vae=None)
        self.assertEqual(got,['model_fl2va','clip','vae'])
    def test_missing_selected_model_never_falls_back(self):
        with self.assertRaisesRegex(ValueError,'model_fl2va'):
            node.MiniMaxH3Promax.check_lazy_status(**DEFAULT, model_ref2va=object())
    def test_lazy_ref_mode_skips_keyframes(self):
        got = node.MiniMaxH3Promax.check_lazy_status(**(DEFAULT | {'mode':'Ref2V'}), model_fl2va=None, model_ref2va=None, clip=1,vae=1, reference_1=None,first_frame=None)
        self.assertEqual(got,['model_ref2va','reference_1'])
    def test_lazy_audio_needs_vae(self):
        with self.assertRaisesRegex(ValueError,'audio_vae'):
            node.MiniMaxH3Promax.check_lazy_status(**(DEFAULT | {'mode':'Ref2V'}),model_ref2va=None,reference_audio=None)
    def test_lazy_fl2v_needs_first_frame(self):
        with self.assertRaisesRegex(ValueError,'first_frame'):
            node.MiniMaxH3Promax.check_lazy_status(**(DEFAULT | {'mode':'FL2V'}),model_fl2va=None)
    def test_native_t2v_dispatch(self):
        calls=[]
        def condition(**kw): calls.append(kw); return Output('positive',{'samples':'AV'})
        native=types.SimpleNamespace(MiniMaxH3ImageToVideo=types.SimpleNamespace(execute=condition),
                 MiniMaxH3SigmaShift=types.SimpleNamespace(execute=lambda **kw: Output(kw['model'])))
        with patch.object(node,'core',return_value=native):
            result=node.MiniMaxH3Promax.execute(**DEFAULT,clip='clip',vae='vae',model_fl2va='fl',model_ref2va='ref',shift_video=12,shift_audio=3)
        self.assertEqual(result.args[:4],('fl','positive',{'samples':'AV'},24.0))
        self.assertEqual(calls[0]['length'],124)
        self.assertIsNone(calls[0]['first_frame'])
    def test_reference_holes_rejected(self):
        tensor=types.SimpleNamespace(shape=(1,100,100,3))
        with self.assertRaisesRegex(ValueError,'consecutively'):
            node.MiniMaxH3Promax.execute(**(DEFAULT | {'mode':'Ref2V'}),clip=1,vae=1,model_ref2va=1,shift_video=12,shift_audio=3,reference_2=tensor,ref2_description='Person')
    def test_native_ref_dispatch(self):
        tensor=types.SimpleNamespace(shape=(1,100,100,3)); calls=[]
        def condition(**kw): calls.append(kw); return Output('positive','latent')
        native=types.SimpleNamespace(MiniMaxH3ReferenceToVideo=types.SimpleNamespace(execute=condition),MiniMaxH3SigmaShift=types.SimpleNamespace(execute=lambda **kw: Output(kw['model'])))
        with patch.object(node,'core',return_value=native):
            result=node.MiniMaxH3Promax.execute(**(DEFAULT | {'mode':'Ref2V'}),clip=1,vae=1,model_ref2va='ref',shift_video=12,shift_audio=3,reference_1=tensor,ref1_description='Person')
        self.assertEqual(result.args[0],'ref')
        self.assertEqual(calls[0]['ref_image_size'],'match')
        self.assertEqual(calls[0]['ref_images'],{'ref_image_0':tensor})
    def test_reject_long_reference_video(self):
        with self.assertRaisesRegex(ValueError,'5–120'):
            node.MiniMaxH3Promax.execute(**(DEFAULT | {'mode':'Ref2V'}),clip=1,vae=1,model_ref2va=1,shift_video=12,shift_audio=3,reference_video=types.SimpleNamespace(shape=(121,100,100,3)))

class WorkflowTests(unittest.TestCase):
    def test_links_and_values(self):
        paths=[p for p in (ROOT/'example_workflows').glob('Promax*.json') if not p.name.endswith('.api.json')]
        self.assertGreaterEqual(len(paths),2)
        for path in paths:
            flow=json.loads(path.read_text()); nodes={n['id']:n for n in flow['nodes']}
            for lid,source,slot,target,target_slot,kind in flow['links']:
                self.assertIn(lid,nodes[source]['outputs'][slot]['links'])
                self.assertEqual(nodes[target]['inputs'][target_slot]['link'],lid)
                self.assertEqual(nodes[source]['outputs'][slot]['type'],kind)
                self.assertEqual(nodes[target]['inputs'][target_slot]['type'],kind)
            director=next(n for n in flow['nodes'] if n['type']=='MiniMaxH3Promax')
            fields=[f for f in node.MiniMaxH3Promax.define_schema().inputs if 'default' in f.kw]
            self.assertEqual(len(director['widgets_values']),len(fields))
            values=dict(zip([f.id for f in fields],director['widgets_values']))
            plan.compile_plan(**{k:values[k] for k in DEFAULT})

if __name__=='__main__': unittest.main()
