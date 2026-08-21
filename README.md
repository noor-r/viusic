# Scoring Desk — Video Content Analyzer → Music Recommender

Upload a video. It gets broken into scenes, each scene is read by Mistral's
vision model, and the results are synthesized into an overall emotional arc
and pacing profile. That profile then drives two music outputs:

1. **YouTube search queries** — ready to plug into your existing YouTube
   search feature (see the integration slot below), so you can pull real
   tracks.
2. **A Suno/Udio-style generation prompt** — a single dense prompt plus a
   section-by-section cue sheet, so an AI-generated track can follow the
   video's actual transitions instead of just matching a generic mood.

## How it works (pipeline)

```
video upload
   │
   ▼
ffmpeg: probe metadata, detect scene cuts, extract 1 frame per scene,
        extract audio track → estimate tempo/loudness/energy
   │
   ▼
Mistral vision: analyze each scene frame → {setting, action, mood,
               visual_energy, color_tone}
   │
   ▼
Mistral (2nd pass): synthesize all scenes + audio features → overall theme,
                    emotional arc, pacing, energy curve, transition moments
   │
   ▼
Mistral (3rd pass): overall + scene analysis → music recommendation:
                    - YouTube search queries
                    - style tags / reference styles
                    - Suno/Udio prompt + section cue sheet
                    - recommended BPM
   │
   ▼
YouTube search integration (your existing feature, plugged in) → live results
```

Three model calls total per video for the "brains" of it, plus one vision
call per detected scene (capped at 8 scenes by default for very fast-cut videos — see
`MAX_SCENES_FOR_VISION` in `app/main.py`).

## Setup

```bash
cd video-music-analyzer
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

You also need `ffmpeg` and `ffprobe` on PATH (already present in most
environments; on Ubuntu: `apt install ffmpeg`).

Set your Mistral API key:

```bash
export MISTRAL_API_KEY=9W...
export MISTRAL_API_BASE_URL=https://api.mistral.ai/v1
export MISTRAL_TIMEOUT_SECONDS=60
```

If YouTube blocks anonymous downloads/searches (HTTP 429, "Sign in to confirm you're not a bot", or JS runtime warnings), configure the yt-dlp fallback with one of:

```bash
# Preferred: reuse your logged-in browser session
export YTDLP_COOKIES_FROM_BROWSER=chrome

# Or use a Netscape-format cookies file
export YTDLP_COOKIES_FILE=/absolute/path/to/youtube-cookies.txt

# Optional: enable additional JavaScript runtimes for YouTube extraction
export YTDLP_JS_RUNTIMES=node
```

Optional retry/throttle tuning:

```bash
export YTDLP_RETRIES=5
export YTDLP_FRAGMENT_RETRIES=5
export YTDLP_EXTRACTOR_RETRIES=5
export YTDLP_SLEEP_REQUESTS=1
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` — that's the whole app, frontend included
(served as static files from the same FastAPI process).

## Wiring in your existing YouTube search feature

This is the one piece intentionally left as a slot, since you said you
already have it built. Open `app/music_recommender.py` and note the
`set_youtube_search_function` function:

```python
def set_youtube_search_function(fn: Callable[[str], List[dict]]) -> None:
    """Register your existing YouTube search feature here."""
```

Your function needs this shape:

```python
def my_youtube_search(query: str) -> list[dict]:
    # ... your existing implementation ...
    return [
        {"title": "...", "url": "https://youtube.com/watch?v=...", "channel": "...", "duration": "3:45"},
        ...
    ]
```

Then in `app/main.py`, near the top (there's a marked section for this),
wire it up at import time:

```python
from my_youtube_module import my_youtube_search
from . import music_recommender as mr
mr.set_youtube_search_function(my_youtube_search)
```

If you don't wire anything in, the app still works end-to-end — the UI
just falls back to a "Search on YouTube" link built from the query string
instead of showing live results inline.

## API

- `POST /api/analyze` — multipart form upload with a `file` field
  (the video). Returns the full analysis + recommendation JSON.
- `GET /api/jobs/{job_id}` — refetch a previous result from the in-memory
  job store (cleared on server restart).
- `GET /api/frames/{job_id}/{filename}` — serves the extracted scene
  thumbnail images shown in the timeline.

## Notes on the audio-feature estimation

`estimate_audio_features` in `app/video_processor.py` computes a rough BPM,
loudness, and energy/dynamics score using only `numpy` (autocorrelation on
an onset envelope) — deliberately dependency-light instead of pulling in
`librosa`. It's accurate enough to bucket a video's existing soundtrack
into slow/medium/fast for matching purposes, but isn't a precise beat
tracker. If you want tighter tempo detection later, swap this function for
a `librosa.beat.beat_track` call.

## Scaling notes

- Jobs are stored in an in-memory dict (`JOBS` in `app/main.py`). Fine for
  single-worker dev use; swap for Redis or a DB if you deploy with multiple
  workers or need results to survive a restart.
- Very fast-cut videos are capped at 8 vision calls by default (`MAX_SCENES_FOR_VISION`)
  to bound cost/latency — scenes are evenly subsampled rather than dropped
  from one part of the video.
- Uploaded video files are deleted right after processing; extracted scene
  thumbnails are kept on disk under `frames/{job_id}/` so the UI can display
  them.
