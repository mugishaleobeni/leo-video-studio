# Leo Video Studio

A self-hosted, single-owner video production app: prompt → editable scenes → GPU-generated clips → episode MP4s.

This replaces the moving-shapes demo. It uses existing Wan model weights through Hugging Face Diffusers to generate live-action-looking people and environments or stylized 3D animation. It is **not a new model trained from scratch**, and rendered quality must be tested on your chosen GPU. No generated Wan footage or model weights are bundled.

## Features and precise limits

| Feature | Implementation |
| --- | --- |
| Realistic / 3D animation | Style-conditioned Wan text-to-video inference; 3D-style video, not editable Blender meshes |
| Prompt splitting | Word-boundary partitioning for long briefs; editable JSON shot drafts |
| AI screenplay planning | Optional separately configured OpenAI-compatible writing service generates episode arcs and individual shot prompts |
| Episodes / seasons | 1–12 episodes, 1–240 shots each; separate MP4 per episode |
| Duration | 81 frames / 16 FPS per shot ≈ 5.06 seconds; maximum episode here ≈ 20.25 minutes |
| 1080p export | 1920×1080 H.264 using Lanczos upscaling, aspect-preserving padding, 24 FPS; extra frames are duplicated, not AI-interpolated |
| Model source quality | Economy: 832×480; Quality: 1280×720 |
| Continuity | Shared written character bible and deterministic per-shot seeds; no guaranteed identity consistency |
| Resume | Completed scene hashes and files persist; a restart rerenders only incomplete/changed shots |
| Regenerate | Choose a shot ID; rebuild affected episode exports |
| Audio | Upload music or recorded narration and mux into an episode; no automatic voices or lip-sync |
| Subtitles | Optional narration text exported to SRT; not automatically burned into MP4 |
| Hosting | GPU Python server, Dockerfile, login and bounded queue |

This is a functional production-pipeline implementation, not a guarantee of a professionally finished film. Review the storyboard and every shot. Long seasons multiply GPU time and cost. AI writer planning runs in batches of up to 24 shots with recent-scene context. A long episode still needs editorial review to maintain a coherent story. Subject identity control, image-to-video references, AI voice casting, lip-sync, advanced upscaling, scene transitions and multi-tenant billing are not implemented.

## YouTube season automation

The optional YouTube tab can render a season and upload each completed episode **privately** for your review. Titles/descriptions, saved upload progress, retry controls and YouTube Studio links are included. Background tasks continue when you close the browser, provided the GPU server remains running. Publication is manual. See YOUTUBE_SETUP.md for Google Cloud OAuth configuration and start with `python server.py`. The original `python app.py` rendering workflow remains available.

## Upload to GitHub

Extract the ZIP. Upload the files from this directory into a new GitHub repository, or:

```bash
git init
git add .
git commit -m "Add Leo Video Studio"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/leo-video-studio.git
git push -u origin main
```

GitHub stores the code; a push does not start a GPU or publish the app. GitHub Pages cannot run this backend.

## Run on an online GPU machine

You need a Linux NVIDIA GPU host with CUDA, sufficient CPU memory and persistent disk for model caches and rendered footage. The quality 14B model is substantially heavier than the economy model. Actual VRAM depends on model, precision and offloading; do not assume a free CPU web host can generate these videos. Test one economy shot before committing to a season. First startup downloads large model weights; keep HF_HOME on persistent storage.

```bash
git clone https://github.com/YOUR_USERNAME/leo-video-studio.git
cd leo-video-studio
python -m pip install -r requirements.txt
# Install FFmpeg using your host's package manager if it is absent.
sudo apt-get update
sudo apt-get install -y ffmpeg
python -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

Install the correct CUDA-compatible PyTorch package for your host if CUDA prints False. Use https://pytorch.org/get-started/locally/. Configure secrets and paths in your provider's environment panel:

```text
APP_USER=engineer
APP_PASSWORD=your-own-strong-password
STUDIO_DATA=/data/projects
HF_HOME=/data/model-cache
CPU_OFFLOAD=1
PORT=7860
```

Do not commit actual secrets. .env.example is a template; the app does not automatically load .env files. /data must exist, be writable and use a persistent mounted volume.

```bash
python app.py
```

Expose port 7860 through your provider's HTTPS proxy. Keep login enabled; all authenticated users share the same project directory. This is an owner studio, not a secure multi-user SaaS. ALLOW_PUBLIC=1 explicitly disables the requirement for configured login; avoid doing that on an unrestricted billable GPU.

CPU_OFFLOAD=1 uses CPU RAM to reduce GPU pressure. Offload is not a guarantee that any GPU can handle the selected profile. CPU_OFFLOAD=0 loads the entire pipeline onto CUDA. Generation uses an 81-frame clip with 10–80 denoising steps. Rendering actual videos can take substantial time; no time or cost estimate is promised.

## Docker on a GPU server

Requires compatible NVIDIA drivers and NVIDIA Container Toolkit on the host. Verify the base image and package versions on your cloud image before deployment; Docker has not been built in this workspace.

```bash
docker build -t leo-video-studio .
docker run --rm --gpus all -p 7860:7860 \
  -v /YOUR/PERSISTENT/DIRECTORY:/data \
  -e APP_USER -e APP_PASSWORD \
  leo-video-studio
```

Set APP_USER and APP_PASSWORD in your shell/secret manager first; -e passes their existing values. Model weights are downloaded into the mounted cache, not committed to GitHub. Run one app process/worker per GPU. The disk lock serializes rendering but cannot distribute pipelines across separate hosts.

## Interactive online notebook

Upload render_online.ipynb to Google Colab, set your repository URL, select a GPU and run its cells. It generates one realistic shot and exports a 1080p MP4. It does not start a browser server. The model download and GPU inference still need to succeed on that runtime; this notebook is provided as a starting point and has not been executed on Colab here.

## Configure AI story planning

The studio can connect to your hosted language model or an API implementing /chat/completions. It sends the story and character bible to that service. Configure:

```text
WRITER_BASE_URL=https://YOUR_WRITER_SERVER/v1
WRITER_MODEL=YOUR_TEXT_MODEL_ID
WRITER_API_KEY=YOUR_KEY_IF_REQUIRED
```

Enable “Use configured AI writer” in the app. Without a writer, splitting is structural, not intelligent screenplay creation: repeated brief text may appear with different camera framing. In either mode, review/edit the JSON before saving. A writer must return the requested counts and schema; malformed output is rejected. API costs and writer context limits belong to your selected provider. Long plans make multiple sequential writer requests; planning can take a long time and incur provider costs. If a batch fails, planning must be retried; unfinished writer plans are not persisted. Make smaller plans to limit that risk.

## Production workflow

1. Choose Realistic or 3D animation. Write the setting, characters, action, tone and story arc.
2. Add consistent character appearance, clothes and colors to the character bible.
3. Select episodes and shots. Six shots make approximately 30 seconds.
4. Build and review/edit the storyboard. Each shot should contain one action that can occur in five seconds.
5. Save the reviewed plan and keep its project ID.
6. Render. Download each episode and the project JSON. A failure is recorded in state.json.
7. After restarting the host, paste the same project ID and resume. Completed shots are reused. Stop requests are checked between shots, not within a GPU pass.
8. To replace a shot, enter an ID such as 01-02 and resume. Outputs for that episode and its prior soundtrack are invalidated and rebuilt.
9. Upload an audio track and export a chosen episode with audio. SRT narration can be imported into a video editor.

Plans are immutable after saving. To change prompts, edit the storyboard and save a new project. A resume of the existing project uses its saved plan, not the current storyboard editor. Generated scenes and caches remain on disk until you delete the project/cache on your server; provision storage and back up valuable work. Temporary files may remain after interruption and are overwritten on resume.

## Tests and validation

```bash
python -m unittest discover -s tests -v
```

The tests use a clearly test-only FFmpeg source to exercise planning, failure recovery, resume, shot regeneration and real 1080p assembly. That source is not part of the product rendering path. Actual Wan inference needs a CUDA GPU and model downloads and has not been run in this workspace. See VALIDATION.md.

## Official references

- Wan pipeline and models: https://huggingface.co/docs/diffusers/api/pipelines/wan
- Quality model: https://huggingface.co/Wan-AI/Wan2.1-T2V-14B-Diffusers
- Economy model: https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers
- Gradio hosting: https://www.gradio.app/guides/sharing-your-app

Dependencies are bounded ranges, not a complete reproducibility lock. Follow model licenses and applicable requirements when choosing deployment and data. The AI video model is already pretrained; there is no from-scratch training command in this studio.
