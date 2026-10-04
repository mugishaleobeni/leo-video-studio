# YouTube private review automation

The studio can render a planned season, upload each finished episode privately, and save its YouTube Studio review link. You review the generated footage in your channel and decide what to publish. The app never sets public/unlisted visibility or publishAt, and it does not publish videos on your behalf.

## Google configuration

1. Create a Google Cloud project in https://console.cloud.google.com/ and enable YouTube Data API v3.
2. Configure Google Auth Platform / OAuth consent. For initial testing, add the Google account that owns your channel as a test user if your app's audience is external/testing.
3. Create an OAuth client of type **Web application**. Add this exact authorized redirect URI, replacing the host with your deployed studio address:

   `https://YOUR_STUDIO_HOST/youtube/callback`

4. Download the OAuth client JSON and store it outside the directory used by STUDIO_DATA. Do not commit it to GitHub.
5. Configure your GPU host's secrets/environment:

```text
APP_USER=engineer
APP_PASSWORD=your-own-strong-password
STUDIO_DATA=/data/projects
YOUTUBE_SECRET_DIR=/data/youtube-secrets
YOUTUBE_CLIENT_SECRET_FILE=/data/youtube-secrets/client_secret.json
YOUTUBE_REDIRECT_URI=https://YOUR_STUDIO_HOST/youtube/callback
```

Use a writable persistent volume for both projects and secrets. The secret directory is rejected if it is inside STUDIO_DATA because that directory is exposed for studio downloads. Tokens and upload session URLs are stored separately with restrictive file permissions, never in downloadable project files.

For localhost development, `http://localhost:7860/youtube/callback` is accepted and must also be registered with Google. Remote deployment requires HTTPS. Configure your proxy to preserve the app host and route /youtube/connect and /youtube/callback to this same server.

## Start the optional automation server

```bash
python -m pip install -r requirements.txt -r requirements-youtube.txt
python server.py
```

The original `python app.py` and original Dockerfile still run the ordinary rendering studio. YouTube automation requires server.py; this separate startup avoids making the original renderer dependent on OAuth setup.

For Docker:

```bash
docker build -f Dockerfile.youtube -t leo-video-studio-youtube .
docker run --rm --gpus all -p 7860:7860 \
  -v /YOUR/PERSISTENT/DIRECTORY:/data \
  -e APP_USER -e APP_PASSWORD \
  -e YOUTUBE_SECRET_DIR -e YOUTUBE_CLIENT_SECRET_FILE -e YOUTUBE_REDIRECT_URI \
  leo-video-studio-youtube
```

Set those environment variables in your shell or secret manager first. This image uses server.py. Do not run several server workers: this is one owner and one background production worker per GPU. Keep the server alive; closing the browser does not stop a task, but stopping the server stops computation. Queued/running tasks recover on the next server start using persistent project files and upload sessions.

## Connect and run a season

1. Open the **YouTube review uploads** tab.
2. Click **Connect your YouTube channel**. The connection page asks for your studio owner username/password using the browser's login prompt. Sign in to Google and select the correct YouTube channel/account. Approve the upload permission.
3. Build/review a storyboard and save the project as before. Keep its project ID.
4. In the YouTube tab, set the season title prefix, description, intended audience and synthetic-content disclosure. Auto-generated episode titles and description text are derived from the reviewed plan; edit them later in YouTube Studio if needed.
5. Click **Render season + upload privately**. Each complete episode is uploaded while the production continues. The task is persisted and runs independently of the page.
6. Use **Refresh upload status & review links**. Review links open the matching video in YouTube Studio. YouTube processing may continue after upload completion; the app confirms upload acceptance, not processing success or final HD readiness.
7. Review the story, visual quality, audio and metadata. Publish manually if the video and API project are eligible.

Use **Upload / retry finished episodes only** for a season rendered earlier or after repairing connection/quota issues. Confirmed upload IDs are reused; an unchanged episode is not automatically uploaded again. Network/HTTP 500/502/503/504 failures get up to three attempts with backoff. Other failures are reported, and rendering continues. Quota failures need attention or a later retry; the app does not promise unlimited upload capacity.

If soundtracks exist when uploading and “Upload soundtrack version when available” is checked, those are selected. Audio added after uploading will not replace an existing YouTube video. Generate/upload a new project for a new version. If an episode changes after its upload begins, the uploader stops and preserves the old video/session rather than silently creating a duplicate.

**Stop automation** stops between shots/episodes; the current GPU shot or HTTP chunk can finish first. Existing private uploads remain in your channel. Future work can be resumed by queueing a new task for the same project.

## Interrupted or expired uploads

The app saves a resumable session address privately and asks YouTube for the received byte range on retry. If YouTube already completed the upload, it recovers the confirmed video ID instead of uploading again.

An expired/ambiguous session is not reset automatically. Check YouTube Studio for the episode first. If it exists, keep/review it. If no video exists, open **Recover an expired or incomplete upload session**, select the episode, confirm your check, clear the incomplete session and retry. Confirmed completed records cannot be cleared from this recovery control. This protects against common duplicate-upload cases; it is not a universal exactly-once guarantee across Google outages or manual channel actions.

## Important platform limits

YouTube states that videos uploaded through videos.insert by unverified API projects created after 28 July 2020 are restricted to private viewing. Lifting that restriction requires an API project audit. Do not assume manually changing visibility in Studio will bypass the restriction. OAuth consent verification and the YouTube API audit are separate processes.

Google can expire/revoke access depending on consent-app status and account actions. If authorization fails, reconnect using the same channel and follow Google's consent/verification requirements for long-running deployments. API quota, channel upload limits and content policies still apply. The app does not guarantee publication, monetization or revenue.

Single-channel scope: all studio users share the same token and project directory. This is an owner tool, not a multi-tenant channel manager. Do not switch connected channels while a production is active. To revoke access, use your Google account's third-party permissions settings and remove the server's token.json. Existing videos remain on YouTube.

## What was tested

The existing rendering tests pass. Added mocked protocol tests cover private visibility, completed-upload deduplication, interrupted-response recovery, changed-file protection, credential isolation and upload failure separation from rendering. HTTP checks cover the studio login page, owner protection of the OAuth entry route and callback state rejection.

No real Google account was connected and no actual video was uploaded during development. Live OAuth and upload need validation on your deployed server with your own Google Cloud configuration. GPU inference remains untested in this CPU workspace.

Official references:

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/docs/videos/
