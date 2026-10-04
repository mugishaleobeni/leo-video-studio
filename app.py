import json
import os
import gradio as gr
from planner import make_plan
from backend import PROFILES
import jobs

CSS = '''.gradio-container {max-width: 1180px !important;} .hero {padding: 30px; border-radius: 18px; background: linear-gradient(110deg,#101b38,#24204b); color: white;}'''

def plan_story(prompt, style, episodes, shots, characters, writer):
    try:
        plan = make_plan(prompt, style, episodes, shots, characters, writer)
        count = sum(len(e['shots']) for e in plan['episodes'])
        return json.dumps(plan, indent=2, ensure_ascii=False), f'{count} shots · about {count*81/16:.1f}s total. Review the storyboard before rendering.'
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def create_project(text, profile, steps, seed):
    try:
        job_id = jobs.create(json.loads(text), profile, steps, seed)
        _, _, estimate = jobs.inspect(job_id)
        return job_id, f'Project saved: {job_id}. {estimate}. Rendering has not started.'
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def run_project(job_id, shot):
    try:
        if shot and not __import__('re').fullmatch(r'\d{2}-\d{2,3}', shot):
            raise ValueError('Shot ID must look like 01-02; leave blank to resume normally')
        if shot:
            _, plan, _ = jobs.inspect(job_id)
            valid = {f'{e:02}-{s:02}' for e, ep in enumerate(plan['episodes'], 1) for s in range(1,len(ep['shots'])+1)}
            if shot not in valid:
                raise ValueError('Shot ID does not exist in this plan')
        for message, files in jobs.render(job_id, regenerate=shot or None):
            videos = [p for p in files if p.endswith('.mp4')]
            yield message, files, videos[-1] if videos else None
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def stop_project(job_id):
    try:
        return jobs.request_cancel(job_id)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def status_project(job_id):
    try:
        state, _, estimate = jobs.inspect(job_id)
        return f'{state["status"]}: {state["message"]}\n{estimate}'
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def audio_project(job_id, episode, audio):
    try:
        if not audio:
            raise ValueError('Upload an audio file')
        return jobs.soundtrack(job_id, episode, audio)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def queue_youtube(job_id, prefix, description, kids, synthetic, prefer_audio, upload_only):
    try:
        import youtube_upload
        if not (youtube_upload.secret_root() / 'token.json').exists():
            raise ValueError('Connect YouTube before starting an upload automation')
        import automation
        settings = {'title_prefix':prefix,'description':description,'made_for_kids':kids,
                    'synthetic':synthetic,'prefer_audio':prefer_audio}
        task_id = automation.enqueue(job_id,settings,upload_only)
        return task_id, 'Task queued. The automation server runs it independently of this browser.'
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def youtube_status(job_id, task_id):
    try:
        import automation
        import youtube_upload
        message = youtube_upload.connected()
        if task_id:
            task=automation.task_status(task_id)
            message += '\n' + task['status'] + ': ' + task['message']
            if task.get('upload_errors'):
                message += '\nUpload issues: ' + json.dumps(task['upload_errors'])
        rows=[]
        if job_id:
            path=jobs.folder(job_id) / 'youtube.json'
            ledger=json.loads(path.read_text()) if path.exists() else {}
            rows=[[number,record.get('status',''),record.get('review_url','')] for number,record in sorted(ledger.items(),key=lambda p:int(p[0]))]
        return message, rows
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def stop_youtube(task_id):
    try:
        import automation
        return automation.stop_task(task_id)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

def reset_youtube(job_id, episode, confirmed):
    try:
        from youtube_upload import reset_incomplete_upload
        return reset_incomplete_upload(job_id, episode, confirmed)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

with gr.Blocks(title='Leo Video Studio') as demo:
    gr.HTML('<div class="hero"><h1>Leo Video Studio</h1><p>Turn a story into scenes, episodes and a season.</p></div>')
    gr.Markdown('Realistic live-action or 3D-style animated video using Wan on your GPU server. '
                '**1080p exports are upscaled from 480p or 720p model output.** '
                'Review storyboards and generated shots; character identity and polished results are not guaranteed.')
    with gr.Tab('1 · Storyboard'):
        story = gr.Textbox(label='Story or production brief', lines=8, placeholder='Describe your characters, world, actions and story arc…')
        with gr.Row():
            style = gr.Dropdown(['Realistic','3D animation'], value='Realistic', label='Visual style')
            episodes = gr.Slider(1,12,value=1,step=1,label='Episodes')
            shots = gr.Slider(1,240,value=6,step=1,label='Shots per episode · about 5 seconds each')
        characters = gr.Textbox(label='Character bible', lines=3, placeholder='Stable appearance, clothes, colors and identifying details for each character')
        writer = gr.Checkbox(label='Use configured AI writer for scene progression and season arcs', value=False)
        gr.Markdown('Without a writer endpoint, long prompts are split into editable shot drafts; short briefs may repeat. '
                    'The AI writer requires a separate server or API configuration.')
        plan_button = gr.Button('Build storyboard', variant='primary')
        plan_json = gr.Textbox(label='Editable storyboard JSON', lines=18)
        plan_note = gr.Textbox(label='Production estimate', interactive=False)
        plan_button.click(plan_story,[story,style,episodes,shots,characters,writer],[plan_json,plan_note])
        with gr.Row():
            profile = gr.Dropdown(list(PROFILES),value='Quality 14B / 720p',label='Video model')
            steps = gr.Slider(10,80,value=40,step=1,label='Denoising steps')
            seed = gr.Number(value=42,precision=0,label='Seed',minimum=0,maximum=2147483647)
        save = gr.Button('Save reviewed production plan')
    with gr.Tab('2 · Render & resume'):
        job_id = gr.Textbox(label='Project ID · save this to resume later')
        status = gr.Textbox(label='Production status',interactive=False,lines=3)
        shot_id = gr.Textbox(label='Optional shot to regenerate',placeholder='01-02 means episode 1, shot 2. Leave blank to resume.')
        with gr.Row():
            run = gr.Button('Render / resume project',variant='primary')
            refresh = gr.Button('Refresh status')
            stop = gr.Button('Stop after current shot')
        preview = gr.Video(label='Latest episode preview')
        downloads = gr.Files(label='Download episode MP4s, narration SRTs and project files')
        gr.Markdown('A season is exported as separate episode files. Already completed shots are reused when resuming. '
                    'Every shot requires a GPU generation pass; costs grow with shot count. '
                    'SRT narration is subtitle text, not generated speech. Videos are silent unless you add audio.')
        run.click(run_project,[job_id,shot_id],[status,downloads,preview],concurrency_limit=1)
        refresh.click(status_project,[job_id],status,queue=False)
        stop.click(stop_project,[job_id],status,queue=False)
    with gr.Tab('3 · Soundtrack'):
        gr.Markdown('Upload your narration or music track and mux it into an exported episode. Short audio is padded with silence; long audio is trimmed to the video length.')
        ep = gr.Number(value=1,precision=0,label='Episode number',minimum=1,maximum=12)
        audio = gr.File(label='Audio track',file_types=['.wav','.mp3','.m4a','.aac','.ogg'],type='filepath')
        mux = gr.Button('Export episode with audio')
        audio_result = gr.File(label='Download video with soundtrack')
        mux.click(audio_project,[job_id,ep,audio],audio_result,concurrency_limit=1)
    with gr.Tab('4 · YouTube review uploads'):
        gr.Markdown('**Private uploads only.** Review every episode in YouTube Studio and publish yourself. '
                    'Start with `python server.py` to enable connection and background automation. '
                    '[Connect your YouTube channel](/youtube/connect). The connection page uses your studio owner login. '
                    'Google may lock uploads from unverified API projects private until an API audit is approved.')
        prefix=gr.Textbox(label='Season title / title prefix',placeholder='Leave blank to use storyboard title')
        description=gr.Textbox(label='Description introduction',lines=3)
        kids=gr.Checkbox(label='These videos are made for kids',value=False)
        synthetic=gr.Checkbox(label='Declare altered or synthetic content to YouTube',value=True)
        prefer_audio=gr.Checkbox(label='Upload soundtrack version when available',value=True)
        gr.Markdown('Uses the project ID from the Render tab. If soundtracks are added later, they will not replace an already uploaded version.')
        with gr.Row():
            auto=gr.Button('Render season + upload privately',variant='primary')
            upload=gr.Button('Upload / retry finished episodes only')
        task_id=gr.Textbox(label='Automation task ID · keep this for status checks')
        youtube_note=gr.Textbox(label='Automation / connection status',lines=4,interactive=False)
        review_rows=gr.Dataframe(headers=['Episode','Status','YouTube Studio review URL'],datatype=['str','str','str'],interactive=False)
        with gr.Row():
            check=gr.Button('Refresh upload status & review links')
            halt=gr.Button('Stop automation')
        common=[job_id,prefix,description,kids,synthetic,prefer_audio]
        auto.click(lambda *a:queue_youtube(*a,False),common,[task_id,youtube_note],queue=False)
        upload.click(lambda *a:queue_youtube(*a,True),common,[task_id,youtube_note],queue=False)
        check.click(youtube_status,[job_id,task_id],[youtube_note,review_rows],queue=False)
        halt.click(stop_youtube,[task_id],youtube_note,queue=False)
        with gr.Accordion('Recover an expired or incomplete upload session',open=False):
            reset_episode=gr.Number(label='Episode number',value=1,precision=0,minimum=1,maximum=12)
            reset_confirm=gr.Checkbox(label='I checked YouTube Studio and no uploaded video exists for this episode',value=False)
            reset_button=gr.Button('Clear incomplete session for retry')
            reset_button.click(reset_youtube,[job_id,reset_episode,reset_confirm],youtube_note,queue=False)
    save.click(create_project,[plan_json,profile,steps,seed],[job_id,status])

if __name__ == '__main__':
    username, password = os.getenv('APP_USER'), os.getenv('APP_PASSWORD')
    if not username or not password:
        if os.getenv('ALLOW_PUBLIC') != '1':
            raise RuntimeError('Set APP_USER and APP_PASSWORD for this single-owner studio, or ALLOW_PUBLIC=1 for deliberate public access.')
        auth = None
    else:
        auth = (username,password)
    demo.queue(max_size=4).launch(server_name=os.getenv('SERVER_NAME','0.0.0.0'),server_port=int(os.getenv('PORT','7860')),
                                 auth=auth,share=False,css=CSS,allowed_paths=[str(jobs.ROOT)])
