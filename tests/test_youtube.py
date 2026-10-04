import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import jobs
import youtube_upload as yt
import automation
from planner import make_plan

class Response:
    def __init__(self,status,headers=None,data=None):
        self.status_code,self.headers,self.data=status,headers or {},data or {}
    def json(self): return self.data

class Session:
    def __init__(self,interrupt=False,already_done=False):
        self.posts=0
        self.puts=[]
        self.interrupt=interrupt
        self.already_done=already_done
        self.body=None
    def post(self,url,**kwargs):
        self.posts+=1
        self.body=kwargs['json']
        return Response(200,{'Location':'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test'})
    def put(self,url,**kwargs):
        self.puts.append(kwargs)
        if not kwargs['data']:
            return Response(201,data={'id':'abcDEF123_-'}) if self.already_done else Response(308)
        if self.interrupt: raise ConnectionError('interrupted for test')
        return Response(201,data={'id':'abcDEF123_-'})

class YouTubeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.previous=jobs.ROOT
        jobs.ROOT=Path(self.temp.name)/'projects'
        jobs.ROOT.mkdir()
        self.env=patch.dict(os.environ,{'YOUTUBE_SECRET_DIR':str(Path(self.temp.name)/'secrets')})
        self.env.start()
        self.job=jobs.create(make_plan('A family meets in a garden.',shots=1),'Economy 1.3B / 480p',10)
        (jobs.folder(self.job)/'episode-01.mp4').write_bytes(b'test-video-bytes')
    def tearDown(self):
        self.env.stop()
        jobs.ROOT=self.previous
        self.temp.cleanup()

    def test_private_upload_and_deduplication(self):
        session=Session()
        url=yt.upload_episode(self.job,1,{},session=session)
        self.assertIn('studio.youtube.com',url)
        self.assertEqual(session.body['status']['privacyStatus'],'private')
        self.assertNotIn('publishAt',session.body['status'])
        self.assertTrue(session.body['status']['containsSyntheticMedia'])
        self.assertEqual(session.posts,1)
        yt.upload_episode(self.job,1,{},session=session)
        self.assertEqual(session.posts,1)
        public=json.loads((jobs.folder(self.job)/'youtube.json').read_text())
        self.assertNotIn('session_uri',json.dumps(public))
        private=yt.secret_root()/self.job/'upload-01.json'
        self.assertEqual(private.stat().st_mode & 0o777,0o600)

    def test_resume_recovers_completed_response_without_reupload(self):
        with self.assertRaisesRegex(RuntimeError,'Network interrupted'):
            yt.upload_episode(self.job,1,{},session=Session(interrupt=True))
        recovered=Session(already_done=True)
        yt.upload_episode(self.job,1,{},session=recovered)
        self.assertEqual(recovered.posts,0)
        self.assertEqual(len(recovered.puts),1)

    def test_changed_video_is_not_silently_duplicated(self):
        yt.upload_episode(self.job,1,{},session=Session())
        (jobs.folder(self.job)/'episode-01.mp4').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError,'changed'):
            yt.upload_episode(self.job,1,{},session=Session())

    def test_reset_requires_review_and_preserves_completed_video(self):
        with self.assertRaises(RuntimeError):
            yt.upload_episode(self.job,1,{},session=Session(interrupt=True))
        with self.assertRaises(ValueError):
            yt.reset_incomplete_upload(self.job,1,False)
        yt.reset_incomplete_upload(self.job,1,True)
        session=Session()
        yt.upload_episode(self.job,1,{},session=session)
        self.assertEqual(session.posts,1)
        with self.assertRaises(ValueError):
            yt.reset_incomplete_upload(self.job,1,True)

    def test_secret_directory_is_not_served(self):
        with patch.dict(os.environ,{'YOUTUBE_SECRET_DIR':str(jobs.ROOT/'secrets')}):
            with self.assertRaises(ValueError): yt.secret_root()
        with self.assertRaises(ValueError): yt.safe_uri('https://attacker.example/upload')

    def test_upload_failure_does_not_interrupt_render(self):
        task_id=automation.enqueue(self.job,{},False)
        task=automation.task_status(task_id)
        def render(job):
            yield 'Rendering',[]
            state=json.loads((jobs.folder(job)/'state.json').read_text())
            state['status']='completed'
            jobs.atomic_json(jobs.folder(job)/'state.json',state)
            yield 'Done',[]
        def upload(*args): raise RuntimeError('quotaExceeded')
        automation.execute_task(task,uploader=upload,render_fn=render)
        self.assertEqual(jobs.inspect(self.job)[0]['status'],'completed')
        self.assertEqual(automation.task_status(task_id)['status'],'needs_attention')

if __name__=='__main__': unittest.main()
