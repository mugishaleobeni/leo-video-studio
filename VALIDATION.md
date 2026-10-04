# Validation and limitations

Built and checked on 2026-10-04 with Python 3.12, Gradio 6.29.1 and PyTorch 2.14.1+cpu.

Passed:

- Four automated tests: lossless long-brief partitioning, schema and path validation, batched writer planning for a 25-shot episode, and render-job failure/recovery/regeneration with 1080p episode assembly.
- FFmpeg/ffprobe verified 1920×1080 at 24 FPS. The integration test uses tiny test-only color clips, never exposed as a production rendering mode.
- The writer batching test uses a mocked /chat/completions response. No external writer endpoint was called.
- Authenticated app configuration started; the login page returned HTTP 200.
- Source compilation.

Not executed or verified here:

- Actual Wan inference: no NVIDIA CUDA GPU was available and Wan weights were not downloaded. Realistic human footage and 3D-style footage have not been generated in this workspace.
- Cloud deployment, NVIDIA Docker build/run, hosted writer responses, notebook execution on Colab.
- Visual quality, reliable anatomy/motion, cross-shot character identity, long-season story coherence or rendering cost/speed.

This is an implementation of a real-model inference/production pipeline, not a newly trained foundation model or a verified finished professional film system. The GPU rendering integration follows the official Diffusers Wan API and model IDs. It must be exercised on the target GPU before committing to a long production. Dependency ranges are bounded but not fully locked.

1080p export is conventional Lanczos scaling with padding from 480p or 720p model frames, not native 1080p generation or an AI super-resolution model. 24 FPS output uses duplicated/dropped frames. Generated footage is silent; uploaded audio can be added. No automatic dialogue, lip-sync, reference-image character controls or editable 3D scene output is included.

## YouTube update

- Original four rendering/planning tests still pass.
- Added private upload, deduplication, response-loss recovery, changed-file and secret-isolation tests, plus upload-failure separation from rendering. Upload protocol is mocked; no YouTube account was connected or video sent.
- FastAPI/Gradio studio login returned HTTP 200. Anonymous OAuth entry returned 401; missing server configuration returned 400 for an authenticated owner, and a callback without valid state/cookie returned 400.
- Original app.py and Dockerfile remain the ordinary rendering entry points. Optional server.py and Dockerfile.youtube add OAuth/background automation.
- Live OAuth, YouTube upload, proxy deployment, new Docker image and GPU inference still require target-server validation.

Final YouTube update checks: all 10 automated tests passed, including incomplete-session recovery protection. Login/OAuth entry/state rejection checks passed again after UI integration.
