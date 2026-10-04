"""Persistent one-shot season tasks; worker continues independently of the browser."""
import json
import threading
import time
import uuid
from pathlib import Path
from filelock import FileLock, Timeout
import jobs
from youtube_upload import upload_episode

STOP = threading.Event()
THREAD = None

def queue_root():
    root = jobs.ROOT / 'automation'
    root.mkdir(exist_ok=True)
    return root

def enqueue(job_id, settings, upload_only=False):
    jobs.inspect(job_id)
    root = queue_root()
    with FileLock(str(root / '.queue.lock')):
        for path in root.glob('*.json'):
            task = json.loads(path.read_text())
            if task['job_id'] == job_id and task['status'] in ('queued','running'):
                raise ValueError('This project already has an active automation task')
        task_id = uuid.uuid4().hex
        task = {'id':task_id,'job_id':job_id,'settings':settings,'upload_only':bool(upload_only),
                'status':'queued','message':'Queued for private YouTube upload','created_at':time.time(),'upload_errors':{}}
        jobs.atomic_json(root / f'{task_id}.json', task)
        return task_id

def task_status(task_id):
    path = queue_root() / f'{uuid.UUID(task_id).hex}.json'
    if not path.exists(): raise ValueError('Automation task not found')
    return json.loads(path.read_text())

def stop_task(task_id):
    task = task_status(task_id)
    (queue_root() / f'{task["id"]}.stop').touch()
    if task['status']=='running': jobs.request_cancel(task['job_id'])
    return 'Stop requested. Completed videos and private uploads are kept.'

def execute_task(task, uploader=upload_episode, render_fn=jobs.render):
    path = queue_root() / f'{task["id"]}.json'
    marker = path.with_suffix('.stop')
    def save(): jobs.atomic_json(path,task)
    if marker.exists():
        task.update(status='stopped',message='Stopped before starting')
        save()
        return
    task.update(status='running',message='Rendering and uploading completed episodes privately')
    save()
    _,plan,_ = jobs.inspect(task['job_id'])
    attempted = set()
    def upload_ready():
        for index in range(1,len(plan['episodes'])+1):
            if marker.exists(): return
            source = jobs.folder(task['job_id']) / f'episode-{index:02}.mp4'
            if source.exists() and index not in attempted:
                attempted.add(index)
                try:
                    for attempt in range(3):
                        try:
                            uploader(task['job_id'],index,task['settings'])
                            break
                        except Exception as error:
                            transient = 'Network interrupted' in str(error) or any(f'HTTP {code}' in str(error) for code in (500,502,503,504))
                            if not transient or attempt == 2 or marker.exists():
                                raise
                            STOP.wait(2 ** attempt)
                    
                    task['upload_errors'].pop(str(index),None)
                except Exception as exc:
                    # Upload failure does not throw into the render generator.
                    task['upload_errors'][str(index)] = str(exc)
                save()
    try:
        if task.get('upload_only'):
            upload_ready()
        else:
            for message, files in render_fn(task['job_id']):
                task['message'] = message
                save()
                if marker.exists():
                    jobs.request_cancel(task['job_id'])
                else:
                    upload_ready()
            state,_,_ = jobs.inspect(task['job_id'])
            if state['status']=='stopped' or marker.exists():
                task.update(status='stopped',message='Stopped. Resume rendering or queue a new task to continue.')
                save()
                return
        if marker.exists():
            task.update(status='stopped',message='Stopped; already uploaded videos remain private')
        elif task['upload_errors']:
            task.update(status='needs_attention',message='Rendering finished; some uploads failed. Retry private uploads after fixing the connection/quota.')
        else:
            # A task only succeeds if every planned episode has a confirmed private video ID.
            ledger_path = jobs.folder(task['job_id']) / 'youtube.json'
            ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
            missing = [i for i in range(1,len(plan['episodes'])+1) if not ledger.get(str(i),{}).get('video_id')]
            task.update(status='needs_attention' if missing else 'completed',
                        message=f'Episodes {missing} need rendering/upload confirmation' if missing else 'All episodes uploaded privately. Review and publish in YouTube Studio.')
    except Exception as exc:
        task.update(status='failed',message=str(exc))
    save()

def worker():
    root = queue_root()
    lock = FileLock(str(root / '.worker.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return
    try:
        # A prior process can die while rendering/uploading. Existing sessions and hashes support recovery.
        for path in root.glob('*.json'):
            task=json.loads(path.read_text())
            if task['status']=='running':
                task['status']='queued'
                jobs.atomic_json(path,task)
        while not STOP.is_set():
            tasks=[]
            for path in root.glob('*.json'):
                task=json.loads(path.read_text())
                if task['status']=='queued': tasks.append(task)
            if tasks:
                execute_task(min(tasks,key=lambda t:t['created_at']))
            else:
                STOP.wait(1)
    finally:
        lock.release()

def start_worker():
    global THREAD
    if THREAD and THREAD.is_alive(): return
    STOP.clear()
    THREAD=threading.Thread(target=worker,name='private-youtube-worker',daemon=True)
    THREAD.start()

def stop_worker():
    STOP.set()
