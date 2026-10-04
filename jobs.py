"""Durable, resumable single-owner production jobs."""
import hashlib
import json
import os
import uuid
from pathlib import Path
from filelock import FileLock, Timeout
from backend import WanRenderer, PROFILES
from planner import validate
from media import normalize, concatenate, subtitles, add_audio, SECONDS, duration

ROOT = Path(os.getenv('STUDIO_DATA', 'studio_data')).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
RENDERER = WanRenderer()

def atomic_json(path, data):
    temporary = Path(str(path) + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(temporary, path)

def folder(job_id):
    try:
        parsed = uuid.UUID(str(job_id))
    except ValueError as exc:
        raise ValueError('Invalid project ID') from exc
    path = ROOT / parsed.hex
    if not path.is_dir():
        raise ValueError('Project not found')
    return path

def create(plan, profile, steps=40, seed=42):
    plan = validate(plan)
    if profile not in PROFILES or not 10 <= int(steps) <= 80 or not 0 <= int(seed) <= 2147483647:
        raise ValueError('Invalid model settings')
    job_id = uuid.uuid4().hex
    directory = ROOT / job_id
    directory.mkdir()
    atomic_json(directory / 'plan.json', plan)
    atomic_json(directory / 'state.json', {'status': 'ready', 'profile': profile, 'steps': int(steps), 'seed': int(seed),
                 'completed': {}, 'message': 'Ready to render', 'resolution': '1920×1080 export, upscaled from model output'})
    return job_id

def inspect(job_id):
    directory = folder(job_id)
    state = json.loads((directory / 'state.json').read_text())
    plan = json.loads((directory / 'plan.json').read_text())
    count = sum(len(e['shots']) for e in plan['episodes'])
    return state, plan, f'{count} shots · about {count * SECONDS:.1f} seconds total · {len(plan["episodes"])} episodes'

def request_cancel(job_id):
    (folder(job_id) / 'cancel').touch()
    return 'Stop requested. The current shot finishes before stopping; completed shots are kept.'

def outputs(directory):
    return [str(p) for p in sorted(directory.glob('episode-*.mp4')) if '.partial.' not in p.name] + [str(p) for p in sorted(directory.glob('episode-*.srt'))] + [str(directory / 'plan.json'), str(directory / 'state.json')]

def render(job_id, regenerate=None, renderer=None):
    directory = folder(job_id)
    renderer = renderer or RENDERER
    lock = FileLock(str(ROOT / '.gpu.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError('Another render is using the GPU. Try again after it finishes.') from exc
    try:
        state, plan, estimate = inspect(job_id)
        validate(plan)
        (directory / 'cancel').unlink(missing_ok=True)
        state.update(status='rendering', message='Loading video model. First run downloads model weights.')
        atomic_json(directory / 'state.json', state)
        yield state['message'], outputs(directory)
        index = 0
        for ep_index, episode in enumerate(plan['episodes'], 1):
            ep_folder = directory / f'episode-{ep_index:02}'
            ep_folder.mkdir(exist_ok=True)
            clips = []
            for shot_index, shot in enumerate(episode['shots'], 1):
                index += 1
                key = f'{ep_index:02}-{shot_index:02}'
                native, hd = ep_folder / f'{key}-native.mp4', ep_folder / f'{key}-1080p.mp4'
                scene_seed = state.setdefault('shot_seeds', {}).get(key, state['seed'] + index)
                if regenerate == key:
                    scene_seed += 10000
                    state['shot_seeds'][key] = scene_seed
                identity = {'shot': shot, 'style': plan['style'], 'characters': plan['characters'], 'profile': state['profile'],
                            'steps': state['steps'], 'seed': scene_seed}
                digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                if (directory / 'cancel').exists():
                    state.update(status='stopped', message='Stopped between shots. Resume to continue.')
                    atomic_json(directory / 'state.json', state)
                    yield state['message'], outputs(directory)
                    return
                if state['completed'].get(key) != digest or not hd.is_file() or regenerate == key:
                    # Delete stale episode outputs before replacing a shot.
                    (directory / f'episode-{ep_index:02}.mp4').unlink(missing_ok=True)
                    (directory / f'episode-{ep_index:02}-soundtrack.mp4').unlink(missing_ok=True)
                    (directory / f'episode-{ep_index:02}.srt').unlink(missing_ok=True)
                    state['completed'].pop(key, None)
                    state['message'] = f'Rendering episode {ep_index}, shot {shot_index} ({index}). {estimate}'
                    atomic_json(directory / 'state.json', state)
                    yield state['message'], outputs(directory)
                    renderer.render(shot, plan, native, state['profile'], scene_seed, state['steps'])
                    tmp_hd = hd.with_name(hd.stem + '.partial.mp4')
                    normalize(native, tmp_hd)
                    os.replace(tmp_hd, hd)
                    state['completed'][key] = digest
                    atomic_json(directory / 'state.json', state)
                clips.append(hd)
            episode_file = directory / f'episode-{ep_index:02}.mp4'
            temporary = episode_file.with_name(episode_file.stem + '.partial.mp4')
            concatenate(clips, temporary)
            os.replace(temporary, episode_file)
            subtitles(episode['shots'], directory / f'episode-{ep_index:02}.srt', [duration(p) for p in clips])
            yield f'Episode {ep_index} exported at 1920×1080 (upscaled).', outputs(directory)
        state.update(status='completed', message='All episodes exported. Review shots for visual quality and character continuity.')
        atomic_json(directory / 'state.json', state)
        yield state['message'], outputs(directory)
    except Exception as exc:
        if 'state' in locals():
            state.update(status='failed', message=str(exc))
            atomic_json(directory / 'state.json', state)
        raise
    finally:
        lock.release()

def soundtrack(job_id, episode_number, audio):
    directory = folder(job_id)
    _, plan, _ = inspect(job_id)
    number = int(episode_number)
    if not 1 <= number <= len(plan['episodes']):
        raise ValueError('Episode number is out of range')
    video = directory / f'episode-{number:02}.mp4'
    if not video.is_file():
        raise ValueError('Render this episode first')
    result = directory / f'episode-{number:02}-soundtrack.mp4'
    add_audio(video, audio, result)
    return str(result)
