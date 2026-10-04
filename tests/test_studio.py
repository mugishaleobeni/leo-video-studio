import json
import tempfile
import unittest
from unittest.mock import patch
import re
import os
import io
from pathlib import Path
import subprocess
import jobs
from planner import partition, make_plan, validate
from media import ffmpeg

class TestRenderer:
    """Test-only video source; never selectable in the actual app."""
    def __init__(self, fail_on=None):
        self.calls = 0
        self.fail_on = fail_on
    def render(self, shot, plan, path, profile, seed, steps):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError('GPU interrupted (test)')
        ffmpeg(['-f','lavfi','-i','color=c=blue:s=64x64:r=16:d=0.125','-c:v','libx264','-pix_fmt','yuv420p',str(path)])

class StudioTests(unittest.TestCase):
    def test_partition_preserves_long_brief(self):
        text = ' '.join(f'word{i}' for i in range(500))
        chunks = partition(text, 1000)
        self.assertEqual(' '.join(chunks), text)
        self.assertTrue(all(len(c)<=1000 for c in chunks))
        with self.assertRaises(ValueError):
            make_plan(text, shots=1)

    def test_planning_and_validation(self):
        plan = make_plan('A father meets his daughter. They embrace in the rain.', style='Realistic', episodes=2, shots=3)
        self.assertEqual(len(plan['episodes']),2)
        self.assertTrue(all(len(e['shots'])==3 for e in plan['episodes']))
        plan['episodes'][0]['shots'][0]['prompt']=''
        with self.assertRaises(ValueError): validate(plan)
        with self.assertRaises(ValueError): jobs.folder('../../secret')

    def test_batched_writer_plans_long_episode(self):
        calls = []
        def response(request, timeout):
            payload = json.loads(request.data)
            instruction = payload['messages'][0]['content']
            count = int(re.search(r'Create exactly (\d+) shots', instruction).group(1))
            calls.append(count)
            plan = {'title': 'Season', 'style': 'Realistic', 'characters': 'Rex has a red jacket',
                    'episodes': [{'title': 'Episode 1', 'shots': [{'prompt': 'Rex walks through the city.', 'narration': ''} for _ in range(count)]}]}
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        with patch.dict(os.environ, {'WRITER_BASE_URL':'https://writer.example/v1','WRITER_MODEL':'test'}):
            with patch('planner.urllib.request.urlopen', side_effect=response):
                plan = make_plan('Rex explores a city.', episodes=1, shots=25, use_writer=True)
        self.assertEqual(calls,[24,1])
        self.assertEqual(len(plan['episodes'][0]['shots']),25)
        self.assertEqual(plan['characters'],'Rex has a red jacket')

    def test_failure_resume_regenerate_and_1080_export(self):
        previous = jobs.ROOT
        with tempfile.TemporaryDirectory() as temp:
            jobs.ROOT=Path(temp)
            try:
                plan=make_plan('A red car drives through a neon city.', style='3D animation', shots=2)
                job=jobs.create(plan,'Economy 1.3B / 480p',steps=10)
                renderer=TestRenderer(fail_on=2)
                with self.assertRaises(RuntimeError): list(jobs.render(job,renderer=renderer))
                state,_,_=jobs.inspect(job)
                self.assertEqual(state['status'],'failed')
                self.assertEqual(len(state['completed']),1)
                renderer=TestRenderer()
                list(jobs.render(job,renderer=renderer))
                self.assertEqual(renderer.calls,1)
                state,_,_=jobs.inspect(job)
                self.assertEqual(state['status'],'completed')
                output=jobs.folder(job)/'episode-01.mp4'
                probe=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height,r_frame_rate','-of','json',str(output)],capture_output=True,text=True,check=True)
                info=json.loads(probe.stdout)['streams'][0]
                self.assertEqual((info['width'],info['height']),(1920,1080))
                self.assertEqual(info['r_frame_rate'],'24/1')
                list(jobs.render(job,renderer=renderer))
                self.assertEqual(renderer.calls,1)
                list(jobs.render(job,regenerate='01-02',renderer=renderer))
                self.assertEqual(renderer.calls,2)
            finally:
                jobs.ROOT=previous

if __name__=='__main__': unittest.main()
