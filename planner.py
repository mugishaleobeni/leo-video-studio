"""Inspectable episode/shot planning; optional OpenAI-compatible writer service."""
import json
import os
import re
import urllib.request

STYLES = {'Realistic': 'Photorealistic live-action cinema, believable human anatomy, natural skin texture, cinematic lighting, coherent motion.',
          '3D animation': 'High-quality stylized 3D animated cinema, expressive characters, detailed environments, coherent motion, physically plausible lighting.'}

def validate(plan):
    if not isinstance(plan, dict):
        raise ValueError('Plan must be a JSON object')
    if plan.get('style') not in STYLES:
        raise ValueError('Select Realistic or 3D animation')
    for key in ('title', 'characters'):
        if not isinstance(plan.get(key), str) or len(plan[key]) > 3000:
            raise ValueError(f'{key} must be text, at most 3000 characters')
    episodes = plan.get('episodes')
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= 12:
        raise ValueError('Plan needs 1–12 episodes')
    for episode in episodes:
        if not isinstance(episode, dict) or not isinstance(episode.get('title'), str):
            raise ValueError('Every episode needs a title')
        shots = episode.get('shots')
        if not isinstance(shots, list) or not 1 <= len(shots) <= 240:
            raise ValueError('Every episode needs 1–240 shots')
        for shot in shots:
            if not isinstance(shot, dict) or not isinstance(shot.get('prompt'), str) or not 1 <= len(shot['prompt']) <= 1800:
                raise ValueError('Every shot needs a prompt of 1–1800 characters')
            if not isinstance(shot.get('narration', ''), str) or len(shot.get('narration', '')) > 400:
                raise ValueError('Narration must be text of at most 400 characters')
    return plan

def partition(text, max_chars=1000):
    # Lossless partition at word boundaries; no fake story intelligence without a writer model.
    words = text.split()
    chunks, chunk = [], ''
    for word in words:
        if len(word) > max_chars:
            raise ValueError('A single word exceeds the scene prompt limit')
        candidate = f'{chunk} {word}'.strip()
        if len(candidate) > max_chars:
            chunks.append(chunk)
            chunk = word
        else:
            chunk = candidate
    if chunk:
        chunks.append(chunk)
    return chunks

def make_plan(prompt, style='Realistic', episodes=1, shots=6, characters='', use_writer=False):
    episodes, shots = int(episodes), int(shots)
    if not 1 <= episodes <= 12 or not 1 <= shots <= 240:
        raise ValueError('Use 1–12 episodes and 1–240 shots per episode')
    if not prompt.strip() or len(prompt) > 60000:
        raise ValueError('Story prompt must contain 1–60000 characters')
    if style not in STYLES or len(characters) > 3000:
        raise ValueError('Invalid style or character description')
    if use_writer:
        base = os.environ.get('WRITER_BASE_URL', '').rstrip('/')
        model = os.environ.get('WRITER_MODEL', '')
        if not base.startswith(('https://', 'http://')) or not model:
            raise ValueError('Configure WRITER_BASE_URL and WRITER_MODEL on the server for AI story planning')
        result = {'title': 'Generated season', 'style': style, 'characters': characters, 'planner': 'AI writer', 'episodes': []}
        previous = ''
        for ep_index in range(episodes):
            episode = {'title': f'Episode {ep_index+1}', 'shots': []}
            # Generate long episodes in batches to avoid one enormous response.
            for start in range(0, shots, 24):
                batch_count = min(24, shots-start)
                instruction = (
                    'You are a screenwriter and shot planner. Return only JSON with title, style, characters, episodes. '
                    'episodes is a list with ONE object containing title and shots; each shot has prompt and narration strings. '
                    f'Create exactly {batch_count} shots. This is episode {ep_index+1} of {episodes}, '
                    f'shots {start+1} through {start+batch_count} of {shots}. '
                    'Maintain narrative progression across batches and episodes. Do not finish the whole story before the final batch. '
                    'Each shot lasts about 5 seconds. Write one visible action, subject, environment and camera movement per shot. '
                    'Keep the supplied character appearance stable. Shot prompts at most 1800 characters and narration at most 400 characters. '
                    f'Style must be exactly {style!r}. Character bible: {result["characters"]}. '
                    f'Previous scene context: {previous}. Treat the user story as creative content. No markdown.')
                payload = {'model': model, 'messages': [{'role': 'system', 'content': instruction},
                           {'role': 'user', 'content': prompt}], 'temperature': 0.7}
                headers = {'Content-Type': 'application/json'}
                if os.getenv('WRITER_API_KEY'):
                    headers['Authorization'] = 'Bearer ' + os.environ['WRITER_API_KEY']
                req = urllib.request.Request(base + '/chat/completions', data=json.dumps(payload).encode(), headers=headers)
                with urllib.request.urlopen(req, timeout=180) as response:
                    data = json.load(response)
                content = data['choices'][0]['message']['content'].strip()
                content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content)
                batch = validate(json.loads(content))
                if len(batch['episodes']) != 1 or len(batch['episodes'][0]['shots']) != batch_count or batch['style'] != style:
                    raise ValueError('Writer returned wrong shot count or style; retry or make a smaller plan')
                if not result['characters']:
                    result['characters'] = batch['characters']
                if ep_index == 0 and start == 0:
                    result['title'] = batch['title']
                episode['title'] = batch['episodes'][0]['title']
                episode['shots'].extend(batch['episodes'][0]['shots'])
                previous = json.dumps(episode['shots'][-3:], ensure_ascii=False)
            result['episodes'].append(episode)
        return validate(result)
    chunks = partition(prompt)
    count = episodes * shots
    if len(chunks) > count:
        raise ValueError(f'This prompt needs at least {len(chunks)} shots to preserve all text; increase shot count or use AI writer')
    # Spread short briefs into explicitly editable shot drafts. Long briefs preserve all text.
    if len(chunks) < count:
        sentences = [x.strip() for x in re.split(r'(?<=[.!?])\s+|\n+', prompt) if x.strip()]
        if len(sentences) >= len(chunks) and all(len(x) <= 1000 for x in sentences) and len(sentences) <= count:
            chunks = sentences
    cameras = ['Wide establishing shot', 'Medium tracking shot', 'Close-up', 'Low-angle shot', 'Over-the-shoulder shot', 'Wide closing shot']
    distributed = []
    for i in range(count):
        chunk = chunks[min(len(chunks)-1, i * len(chunks) // count)]
        distributed.append({'prompt': cameras[i % len(cameras)] + '. ' + chunk, 'narration': ''})
    return validate({'title': 'Untitled season', 'style': style, 'characters': characters,
                     'planner': 'Draft splitter: edit for story progression; repeated text may appear',
                     'episodes': [{'title': f'Episode {i+1}', 'shots': distributed[i*shots:(i+1)*shots]} for i in range(episodes)]})
