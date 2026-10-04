import json
import shutil
import subprocess
from pathlib import Path

FPS, SECONDS = 16, 81 / 16

def ffmpeg(args):
    executable = shutil.which('ffmpeg')
    if not executable:
        raise RuntimeError('Install FFmpeg on the server before exporting')
    result = subprocess.run([executable, '-hide_banner', '-loglevel', 'error', '-y', *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('FFmpeg failed: ' + result.stderr[-2000:])

def normalize(source, target):
    # Lanczos resize is an upscale, not native 1080p generation or learned detail enhancement.
    ffmpeg(['-i', str(source), '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24',
            '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(target)])

def concatenate(clips, target):
    target = Path(target)
    listing = target.with_suffix('.concat.txt')
    # Paths are generated internally, never from story text.
    lines = []
    for clip in clips:
        name = Path(clip).resolve().as_posix()
        if "'" in name or '\n' in name:
            raise ValueError('Storage path must not contain quotes or newlines')
        lines.append("file '" + name + "'")
    listing.write_text('\n'.join(lines))
    try:
        ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(target)])
    finally:
        listing.unlink(missing_ok=True)

def add_audio(video, audio, target):
    ffmpeg(['-i', str(video), '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
            '-af', 'apad', '-shortest', '-movflags', '+faststart', str(target)])

def timestamp(seconds):
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{ms:03}'

def duration(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(result.stdout)['format']['duration'])

def subtitles(shots, target, durations=None):
    blocks = []
    offset = 0.0
    for i, shot in enumerate(shots):
        length = durations[i] if durations is not None else SECONDS
        if shot.get('narration', '').strip():
            blocks.append(f'{len(blocks)+1}\n{timestamp(offset)} --> {timestamp(offset+length)}\n{shot["narration"].strip()}\n')
        offset += length
    Path(target).write_text('\n'.join(blocks), encoding='utf-8')
