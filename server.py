"""Optional OAuth/automation server. Existing `python app.py` remains supported."""
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import gradio as gr
import uvicorn
from app import demo, CSS
from youtube_upload import secret_root, private_json, SCOPE
import automation

security=HTTPBasic()

def owner(credentials: HTTPBasicCredentials=Depends(security)):
    username,password=os.getenv('APP_USER',''),os.getenv('APP_PASSWORD','')
    if not username or not password:
        raise HTTPException(503,'Set APP_USER and APP_PASSWORD before enabling YouTube connection')
    if not (secrets.compare_digest(credentials.username.encode(),username.encode()) and
            secrets.compare_digest(credentials.password.encode(),password.encode())):
        raise HTTPException(401,'Owner login required',headers={'WWW-Authenticate':'Basic'})

def flow(state=None, verifier=None):
    from google_auth_oauthlib.flow import Flow
    file=os.getenv('YOUTUBE_CLIENT_SECRET_FILE','')
    redirect=os.getenv('YOUTUBE_REDIRECT_URI','')
    parsed=urlparse(redirect)
    if not file or not os.path.isfile(file):
        raise ValueError('Configure YOUTUBE_CLIENT_SECRET_FILE on the server')
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1')):
        raise ValueError('YOUTUBE_REDIRECT_URI must use HTTPS, or HTTP on localhost for development')
    if parsed.path != '/youtube/callback':
        raise ValueError('Redirect URI must end with /youtube/callback')
    # Client secrets must never be inside the directory served by Gradio.
    from jobs import ROOT
    p=__import__('pathlib').Path(file).resolve()
    if p==ROOT or ROOT in p.parents:
        raise ValueError('OAuth client secret file must be outside STUDIO_DATA')
    return Flow.from_client_secrets_file(file,scopes=[SCOPE],state=state,redirect_uri=redirect,
                                         code_verifier=verifier,autogenerate_code_verifier=verifier is None)

@asynccontextmanager
async def lifespan(app):
    automation.start_worker()
    yield
    automation.stop_worker()

web=FastAPI(lifespan=lifespan)

@web.get('/youtube/connect',dependencies=[Depends(owner)])
def connect():
    try:
        f=flow()
        url,state=f.authorization_url(access_type='offline',prompt='consent',include_granted_scopes='true')
        root=secret_root()
        # Expire old unused callbacks.
        for p in root.glob('oauth-*.json'):
            if time.time()-p.stat().st_mtime>1800: p.unlink(missing_ok=True)
        private_json(root / ('oauth-'+hashlib.sha256(state.encode()).hexdigest()+'.json'),
                     {'state':state,'verifier':f.code_verifier,'expires':time.time()+1800})
        response=RedirectResponse(url,status_code=302)
        response.set_cookie('youtube_oauth_state',state,max_age=1800,httponly=True,
                            secure=os.getenv('YOUTUBE_REDIRECT_URI','').startswith('https://'),samesite='lax',path='/youtube')
        return response
    except Exception as exc:
        raise HTTPException(400,str(exc)) from exc

@web.get('/youtube/callback')
def callback(request: Request, state: str='',code: str='',error: str=''):
    cookie=request.cookies.get('youtube_oauth_state','')
    if not state or not cookie or not secrets.compare_digest(state,cookie):
        raise HTTPException(400,'OAuth state check failed. Start connection again.')
    path=secret_root() / ('oauth-'+hashlib.sha256(state.encode()).hexdigest()+'.json')
    if not path.exists(): raise HTTPException(400,'Connection state expired or already used')
    record=json.loads(path.read_text())
    path.unlink()  # One-time callback; all failures require restarting consent.
    if record['expires']<time.time() or not secrets.compare_digest(record['state'],state):
        raise HTTPException(400,'Connection state expired')
    if error or not code:
        raise HTTPException(400,'Google authorization was cancelled or did not return a code')
    try:
        f=flow(state,record['verifier'])
        f.fetch_token(code=code)
        if not f.credentials.refresh_token:
            raise ValueError('Offline permission was not returned. Reconnect and consent again.')
        private_json(secret_root() / 'token.json',json.loads(f.credentials.to_json()))
        response=HTMLResponse('<h2>YouTube connected</h2><p>Return to Leo Video Studio. Episodes will upload privately for your review.</p><a href="/">Return to studio</a>')
        response.delete_cookie('youtube_oauth_state',path='/youtube')
        return response
    except Exception:
        raise HTTPException(400,'Could not complete Google authorization. Check server OAuth configuration and reconnect.')

username,password=os.getenv('APP_USER'),os.getenv('APP_PASSWORD')
if not username or not password:
    raise RuntimeError('The YouTube automation server requires APP_USER and APP_PASSWORD')
demo.queue(max_size=4)
# Gradio 6 applies CSS at mount time through Blocks configuration.
demo.css=CSS
web=gr.mount_gradio_app(web,demo,path='/',auth=(username,password),allowed_paths=[str(__import__('jobs').ROOT)])

if __name__=='__main__':
    uvicorn.run(web,host=os.getenv('SERVER_NAME','0.0.0.0'),port=int(os.getenv('PORT','7860')))
