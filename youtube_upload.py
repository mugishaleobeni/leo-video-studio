"""Optional private-only YouTube integration; tokens and sessions live outside served files."""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from filelock import FileLock
import jobs

SCOPE = 'https://www.googleapis.com/auth/youtube.upload'

def secret_root():
    root = Path(os.getenv('YOUTUBE_SECRET_DIR', str(jobs.ROOT.parent / 'youtube_secrets'))).resolve()
    if root == jobs.ROOT or jobs.ROOT in root.parents:
        raise ValueError('YOUTUBE_SECRET_DIR must be outside STUDIO_DATA to prevent serving credentials')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root

def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    root = secret_root()
    path = root / 'token.json'
    if not path.exists():
        raise RuntimeError('Connect your YouTube channel in the YouTube tab first')
    with FileLock(str(root / '.credentials.lock')):
        creds = Credentials.from_authorized_user_file(str(path), scopes=[SCOPE])
        if not creds.valid:
            if not creds.refresh_token:
                raise RuntimeError('Reconnect YouTube to grant offline upload access')
            creds.refresh(Request())
            private_json(path, json.loads(creds.to_json()))
        return creds

def connected():
    try:
        return 'YouTube connected (upload permission saved)' if (secret_root() / 'token.json').exists() else 'YouTube not connected'
    except Exception as exc:
        return str(exc)

def signature(path):
    digest = hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def safe_uri(uri):
    parsed = urlparse(uri)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.googleapis.com','youtube.googleapis.com'):
        raise ValueError('Untrusted YouTube upload session address')
    return uri

def metadata(plan, episode_index, settings):
    episode = plan['episodes'][episode_index-1]
    prefix = settings.get('title_prefix','').strip() or plan['title']
    title = f'{prefix} | Episode {episode_index}: {episode["title"]}'
    title = title.replace('<','').replace('>','')[:100]
    description = settings.get('description','').strip() + '\n\n' + episode['title'] + '\n'
    if settings.get('synthetic', True):
        description += 'Created with AI-generated video.\n'
    narration = ' '.join(s.get('narration','') for s in episode['shots']).strip()
    description += narration or ' '.join(s['prompt'] for s in episode['shots'][:3])
    description = description.replace('<','').replace('>','').encode('utf-8')[:4900].decode('utf-8','ignore')
    return {'snippet':{'title':title,'description':description,'categoryId':'1'},
            'status':{'privacyStatus':'private','selfDeclaredMadeForKids':bool(settings.get('made_for_kids',False)),
                      'containsSyntheticMedia':bool(settings.get('synthetic',True))}}

def http_call(session, method, url, **kwargs):
    try:
        return getattr(session, method)(url, **kwargs)
    except (ConnectionError, TimeoutError):
        raise RuntimeError('Network interrupted. Retry the retained upload session.') from None
    except Exception as exc:
        try:
            import requests
            if isinstance(exc, requests.RequestException):
                raise RuntimeError('Network interrupted. Retry the retained upload session.') from None
        except ImportError:
            pass
        raise

def check_response(response):
    if response.status_code not in (200,201,308):
        # Do not include session URLs, tokens or raw HTTP requests in public status.
        reason = 'YouTube request failed'
        try:
            error = response.json().get('error',{})
            reason = error.get('errors',[{}])[0].get('reason',reason)
        except Exception:
            pass
        raise RuntimeError(f'{reason} (HTTP {response.status_code}). Retry later or reconnect YouTube; upload session is retained.')

def offset(response):
    value = response.headers.get('Range','')
    if not value:
        return 0
    match = re.fullmatch(r'bytes=0-(\d+)', value)
    if not match:
        raise ValueError('Invalid acknowledged upload range')
    return int(match[1]) + 1

def upload_episode(job_id, episode_index, settings, session=None):
    directory = jobs.folder(job_id)
    _, plan, _ = jobs.inspect(job_id)
    if not 1 <= episode_index <= len(plan['episodes']):
        raise ValueError('Episode index out of range')
    filename = f'episode-{episode_index:02}.mp4'
    source = directory / filename
    if settings.get('prefer_audio') and (directory / f'episode-{episode_index:02}-soundtrack.mp4').exists():
        source = directory / f'episode-{episode_index:02}-soundtrack.mp4'
    if not source.is_file():
        return None
    root = secret_root() / job_id
    root.mkdir(exist_ok=True, mode=0o700)
    ledger_file = root / f'upload-{episode_index:02}.json'
    public_file = directory / 'youtube.json'
    def publish(record):
        public = json.loads(public_file.read_text()) if public_file.exists() else {}
        public[str(episode_index)] = {k:record[k] for k in ('status','video_id','review_url','source_sha256') if k in record}
        jobs.atomic_json(public_file,public)
    with FileLock(str(root / '.upload.lock')):
        digest = signature(source)
        record = json.loads(ledger_file.read_text()) if ledger_file.exists() else {}
        if record and record.get('source_sha256') != digest:
            raise RuntimeError('Episode changed after an upload started. Use a new project for a new uploaded version; existing private videos are preserved.')
        if record.get('video_id'):
            publish(record)
            return record['review_url']
        if session is None:
            from google.auth.transport.requests import AuthorizedSession
            session = AuthorizedSession(credentials())
        size = source.stat().st_size
        if not size:
            raise ValueError('Video file is empty')
        if not record.get('session_uri'):
            response = http_call(session, 'post', 'https://www.googleapis.com/upload/youtube/v3/videos',
                params={'uploadType':'resumable','part':'snippet,status','notifySubscribers':'false'},json=metadata(plan,episode_index,settings),
                headers={'X-Upload-Content-Length':str(size),'X-Upload-Content-Type':'video/mp4'},timeout=60)
            check_response(response)
            record = {'status':'uploading','source_sha256':digest,'session_uri':safe_uri(response.headers['Location'])}
            private_json(ledger_file,record)
            publish(record)
        uri = safe_uri(record['session_uri'])
        # Always ask the server how much it has received, including after a process restart.
        response = http_call(session, 'put', uri,data=b'',headers={'Content-Length':'0','Content-Range':f'bytes */{size}'},timeout=60,allow_redirects=False)
        if response.status_code in (404,410):
            raise RuntimeError('Upload session expired. Check YouTube Studio before resetting this episode upload to avoid a duplicate.')
        check_response(response)
        start = offset(response) if response.status_code == 308 else size
        with source.open('rb') as video:
            while start < size:
                video.seek(start)
                chunk = video.read(8*1024*1024)
                response = http_call(session, 'put', uri,data=chunk,headers={'Content-Type':'video/mp4','Content-Length':str(len(chunk)),
                    'Content-Range':f'bytes {start}-{start+len(chunk)-1}/{size}'},timeout=180,allow_redirects=False)
                check_response(response)
                next_start = offset(response) if response.status_code == 308 else size
                if next_start <= start or next_start > size:
                    raise RuntimeError('YouTube returned an unexpected upload position; retry to check session status')
                start = next_start
                record['uploaded_bytes'] = start
                private_json(ledger_file,record)
        if response.status_code == 308:
            raise RuntimeError('All bytes received but upload completion not confirmed. Retry to query the existing session.')
        result = response.json()
        video_id = result.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise RuntimeError('YouTube did not confirm a video ID; retry the retained session before starting another upload')
        record.update(status='private_review',video_id=video_id,review_url=f'https://studio.youtube.com/video/{video_id}/edit')
        private_json(ledger_file,record)
        publish(record)
        return record['review_url']


def reset_incomplete_upload(job_id, episode_index, confirmed_no_video):
    if not confirmed_no_video:
        raise ValueError('Check YouTube Studio first and confirm no video exists for this episode')
    jobs.folder(job_id)
    episode_index=int(episode_index)
    _,plan,_=jobs.inspect(job_id)
    if not 1<=episode_index<=len(plan['episodes']): raise ValueError('Episode index out of range')
    root=secret_root()/job_id
    path=root/f'upload-{episode_index:02}.json'
    root.mkdir(exist_ok=True,mode=0o700)
    with FileLock(str(root/'.upload.lock')):
        if not path.exists(): return 'No upload session exists; queue private uploads normally.'
        record=json.loads(path.read_text())
        if record.get('video_id'):
            raise ValueError('This episode already has a confirmed video. Review it in Studio; its upload record is preserved.')
        path.unlink()
        public=jobs.folder(job_id)/'youtube.json'
        if public.exists():
            data=json.loads(public.read_text())
            data.pop(str(episode_index),None)
            jobs.atomic_json(public,data)
    return 'Incomplete session cleared. Queue private uploads to start a new session.'
