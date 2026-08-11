"""
main.py
FastAPI backend tying together: video processing -> vision scene
analysis -> overall synthesis -> music recommendation (YouTube queries +
AI generation prompt).

Run with:
    uvicorn app.main:app --reload --port 8000

Requires one of KIMI_API_KEY, MISTRAL_API_KEY, or OPENAI_API_KEY in the environment.
"""

import os
import subprocess
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import APIStatusError, AuthenticationError, OpenAI

from . import video_processor as vp
from . import content_analyzer as ca
from . import music_recommender as mr

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
FRAMES_DIR = BASE_DIR / "frames"
OUTPUTS_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"

for d in (UPLOADS_DIR, FRAMES_DIR, OUTPUTS_DIR):
    d.mkdir(exist_ok=True)


def load_environment(env_path: Path | None = None) -> None:
    load_dotenv(env_path or BASE_DIR / ".env", override=True)


load_environment()

app = FastAPI(title="Video Content -> Music Recommender")

mr.set_youtube_search_function(mr.search_with_downloader)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store. Fine for a single-user/dev deployment; swap for
# Redis/DB if you need multi-worker or persistence across restarts.
JOBS: dict = {}

MAX_SCENES_FOR_VISION = int(os.environ.get("MAX_SCENES_FOR_VISION", "8"))
DEFAULT_KIMI_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpeg", ".mpg", ".ogv"}


def normalize_api_base_url(raw_url: str | None) -> str:
    base_url = (raw_url or DEFAULT_KIMI_BASE_URL).strip().rstrip("/")
    if not base_url:
        base_url = DEFAULT_KIMI_BASE_URL

    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("API base URL must be a valid absolute URL.")

    if "api.kimi.com" in parsed.netloc:
        return DEFAULT_KIMI_BASE_URL

    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")] or "/v1"

    if not path.endswith("/v1"):
        raise ValueError(
            "API base URL must point to an OpenAI-compatible API root ending in /v1, not a specific route."
        )

    return f"{parsed.scheme}://{parsed.netloc}{path}"


def is_supported_video_file(filename: str | None, mime_type: str | None = None) -> bool:
    if not filename:
        return False

    lowered_name = filename.lower()
    suffix = Path(lowered_name).suffix
    if suffix in SUPPORTED_VIDEO_EXTENSIONS:
        return True

    if not mime_type:
        return False

    return mime_type.lower().startswith("video/")


def resolve_api_base_url() -> str:
    load_environment()
    if os.environ.get("MISTRAL_API_KEY"):
        mistral_base_url = (
            os.environ.get("MISTRAL_BASE_URL")
            or os.environ.get("MISTRAL_API_BASE_URL")
            or DEFAULT_MISTRAL_BASE_URL
        )
        return normalize_api_base_url(mistral_base_url)

    return normalize_api_base_url(
        os.environ.get("KIMI_BASE_URL")
        or os.environ.get("KIMI_API_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE_URL")
    )


def get_client() -> OpenAI:
    load_environment()
    api_key = (
        os.environ.get("KIMI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="No API key is set on the server. Configure MISTRAL_API_KEY (or KIMI_API_KEY / OPENAI_API_KEY).",
        )

    try:
        base_url = resolve_api_base_url()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    timeout_seconds = float(os.environ.get("KIMI_TIMEOUT_SECONDS", "60"))
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0)


def convert_mp4_to_mp3(source_mp4_path: Path, output_mp3_path: Path) -> Path:
    cmd = [
        "ffmpeg", "-y", "-i", str(source_mp4_path),
        "-vn", "-codec:a", "libmp3lame", "-q:a", "2", str(output_mp3_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return output_mp3_path


def trim_audio_clip(source_mp3_path: Path, output_mp3_path: Path, start_seconds: float, end_seconds: float) -> Path:
    clip_duration = end_seconds - start_seconds
    if clip_duration <= 0:
        raise ValueError("clip_end_seconds must be greater than clip_start_seconds")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_seconds:.3f}",
        "-i", str(source_mp3_path),
        "-t", f"{clip_duration:.3f}",
        "-codec:a", "libmp3lame",
        "-q:a", "2",
        str(output_mp3_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return output_mp3_path


def build_attach_ffmpeg_command(
    source_video_path: Path,
    music_mp3_path: Path,
    output_video_path: Path,
    has_source_audio: bool,
    keep_original_audio: bool = False,
    original_audio_role: str = "background",
) -> list[str]:
    if keep_original_audio and has_source_audio:
        # Preserve the original soundtrack by mixing it with the new music.
        # Use the full video duration and keep both tracks audible rather than
        # trimming the result to the shorter input as soon as one stream ends.
        orig_vol = "0.2" if original_audio_role == "background" else "1.0"
        music_vol = "0.2" if original_audio_role == "foreground" else "1.0"
        filter_complex = (
            f"[0:a:0]volume={orig_vol},aresample=async=1[orig];"
            f"[1:a:0]volume={music_vol},aresample=async=1[music];"
            "[orig][music]amix=inputs=2:duration=longest:dropout_transition=0[mixed];"
            "[mixed]loudnorm=I=-16:TP=-1.5:LRA=11[out]"
        )
        return [
            "ffmpeg", "-y",
            "-i", str(source_video_path),
            "-stream_loop", "-1",
            "-i", str(music_mp3_path),
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", "[out]",
            "-c:v", "copy", "-c:a", "aac",
            "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            "-disposition:a:0", "default",
            str(output_video_path),
        ]

    # Default: replace original audio entirely with new music.
    return [
        "ffmpeg", "-y",
        "-i", str(source_video_path),
        "-stream_loop", "-1",
        "-i", str(music_mp3_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-movflags", "+faststart",
        "-disposition:a:0", "default",
        "-shortest",
        str(output_video_path),
    ]


def attach_mp3_to_video(
    source_video_path: Path,
    music_mp3_path: Path,
    output_video_path: Path,
    keep_original_audio: bool = False,
    original_audio_role: str = "background",
) -> Path:
    has_source_audio = False
    try:
        has_source_audio = vp.probe_video(str(source_video_path)).has_audio
    except Exception:
        has_source_audio = False

    cmd = build_attach_ffmpeg_command(
        source_video_path,
        music_mp3_path,
        output_video_path,
        has_source_audio=has_source_audio,
        keep_original_audio=keep_original_audio,
        original_audio_role=original_audio_role,
    )
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return output_video_path


# ---------------------------------------------------------------------------
# WIRE UP YOUR EXISTING YOUTUBE SEARCH FEATURE HERE
# ---------------------------------------------------------------------------
# Example:
#   from my_youtube_module import search_youtube
#   mr.set_youtube_search_function(search_youtube)
#
# Left unregistered by default -> the API will just return search query
# strings without live results, so the app still works end-to-end.


@app.post("/api/analyze")
async def analyze_video(file: UploadFile = File(...)):
    if not is_supported_video_file(file.filename, file.content_type):
        raise HTTPException(
            status_code=400,
            detail="Please upload a supported video file (MP4, MOV, WebM, AVI, etc.).",
        )

    job_id = str(uuid.uuid4())[:8]
    video_path = UPLOADS_DIR / f"{job_id}_{file.filename}"
    scene_frames_dir = FRAMES_DIR / job_id

    with open(video_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        info = vp.probe_video(str(video_path))

        scenes = vp.detect_scenes(str(video_path))
        if len(scenes) > MAX_SCENES_FOR_VISION:
            # Always include the first and last scene; distribute the rest evenly
            n = MAX_SCENES_FOR_VISION
            indices = set()
            for i in range(n):
                indices.add(int(round(i * (len(scenes) - 1) / (n - 1))) if n > 1 else 0)
            scenes = [scenes[i] for i in sorted(indices)]
            for i, s in enumerate(scenes):
                s.index = i

        scenes = vp.extract_frames_for_scenes(str(video_path), scenes, str(scene_frames_dir))

        audio_features = None
        if info.has_audio:
            wav_path = scene_frames_dir / "audio.wav"
            scene_frames_dir.mkdir(exist_ok=True)
            extracted = vp.extract_audio(str(video_path), str(wav_path))
            if extracted:
                audio_features = vp.estimate_audio_features(extracted)

        client = get_client()

        # analyze all scenes in parallel to avoid sequential AI round-trips
        with ThreadPoolExecutor(max_workers=min(len(scenes), 5)) as pool:
            futures = {pool.submit(ca.analyze_scene_frame, client, s): s.index for s in scenes}
            scene_analyses_map = {}
            for fut in as_completed(futures):
                scene_analyses_map[futures[fut]] = fut.result()
        scene_analyses = [scene_analyses_map[s.index] for s in scenes]
        overall_analysis = ca.analyze_full_video(
            client, scene_analyses, audio_features, video_duration_seconds=info.duration
        )
        music_recommendation = mr.generate_music_recommendation(
            client, overall_analysis, scene_analyses, audio_features,
            video_duration_seconds=info.duration,
        )

        recommended_queries = music_recommendation.get("youtube_search_queries") or []
        if not isinstance(recommended_queries, list) or not recommended_queries:
            recommended_queries = mr.fallback_search_queries(
                overall_analysis, music_recommendation, video_duration_seconds=info.duration
            )
            music_recommendation["youtube_search_queries"] = recommended_queries

        youtube_results = mr.run_youtube_searches(recommended_queries)
        ranked_music_candidates = mr.build_recommended_music_candidates(
            overall_analysis,
            music_recommendation,
            youtube_results,
            video_duration_seconds=info.duration,
        )

        result = {
            "job_id": job_id,
            "video_info": {
                "duration": info.duration,
                "width": info.width,
                "height": info.height,
                "fps": round(info.fps, 2),
                "has_audio": info.has_audio,
            },
            "audio_features": audio_features,
            "scene_analyses": scene_analyses,
            "overall_analysis": overall_analysis,
            "music_recommendation": music_recommendation,
            "youtube_results": youtube_results,
            "recommended_music_candidates": ranked_music_candidates,
            "_video_path": str(video_path),
        }

        JOBS[job_id] = result
        return result

    except AuthenticationError as e:
        raise HTTPException(
            status_code=500,
            detail="Analysis failed: API key was rejected by the provider. Verify the configured key for your provider and restart the server.",
        ) from e
    except APIStatusError as e:
        detail = str(e)
        if e.status_code == 404:
            detail = (
                "Provider returned 404. Check your API base URL and use an OpenAI-compatible "
                "endpoint like https://api.mistral.ai/v1 or https://api.moonshot.cn/v1."
            )
        raise HTTPException(status_code=500, detail=f"Analysis failed: {detail}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e
    finally:
        # Keep the uploaded file for optional music-attach export and debugging.
        if "video_path" in locals() and video_path.exists():
            video_path.touch(exist_ok=True)


@app.post("/api/search")
async def search_videos(payload: dict):
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="A search query is required")

    try:
        results = mr.search_with_downloader(query)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"query": query, "results": results}


@app.post("/api/music/attach")
async def attach_music_to_video(payload: dict):
    job_id = str(payload.get("job_id", "")).strip()
    source_url = str(payload.get("source_url", "")).strip()
    source_title = str(payload.get("source_title", "track")).strip()
    clip_start_seconds_raw = payload.get("clip_start_seconds")
    clip_end_seconds_raw = payload.get("clip_end_seconds")
    keep_original_audio = bool(payload.get("keep_original_audio", False))
    original_audio_role = str(payload.get("original_audio_role", "background")).strip()
    if original_audio_role not in ("background", "foreground"):
        original_audio_role = "background"

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")
    if not source_url:
        raise HTTPException(status_code=400, detail="source_url is required")

    clip_start_seconds: float | None = None
    clip_end_seconds: float | None = None
    if clip_start_seconds_raw is not None or clip_end_seconds_raw is not None:
        if clip_start_seconds_raw is None or clip_end_seconds_raw is None:
            raise HTTPException(
                status_code=400,
                detail="Both clip_start_seconds and clip_end_seconds are required when selecting a music section.",
            )
        try:
            clip_start_seconds = float(clip_start_seconds_raw)
            clip_end_seconds = float(clip_end_seconds_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="clip_start_seconds and clip_end_seconds must be numbers.") from exc

        if clip_start_seconds < 0:
            raise HTTPException(status_code=400, detail="clip_start_seconds must be >= 0.")
        if clip_end_seconds <= clip_start_seconds:
            raise HTTPException(status_code=400, detail="clip_end_seconds must be greater than clip_start_seconds.")

    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    original_video_path = Path(job.get("_video_path", ""))
    if not original_video_path.exists():
        raise HTTPException(status_code=404, detail="Original uploaded video no longer exists")

    output_dir = OUTPUTS_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    attach_id = str(uuid.uuid4())[:8]
    safe_stem = mr.sanitize_filename(source_title)
    downloaded_mp4 = output_dir / f"{attach_id}_{safe_stem}_source.mp4"
    downloaded_mp3 = output_dir / f"{attach_id}_{safe_stem}_source.mp3"
    track_mp3 = output_dir / f"{attach_id}_{safe_stem}.mp3"
    selected_track_mp3 = output_dir / f"{attach_id}_{safe_stem}_selected.mp3"
    merged_video = output_dir / f"{original_video_path.stem}_{attach_id}_{safe_stem}_with_music.mp4"
    source_mp4_url: str | None = None

    # Default to audio-only download for better end-to-end attach speed.
    use_audio_only = os.environ.get("ATTACH_DOWNLOAD_AUDIO_ONLY", "1").strip().lower() not in {"0", "false", "no"}

    try:
        if use_audio_only:
            mr.download_media_with_downloader(source_url, str(downloaded_mp3))
            track_mp3 = downloaded_mp3
        else:
            mr.download_video_with_downloader(source_url, str(downloaded_mp4))
            convert_mp4_to_mp3(downloaded_mp4, track_mp3)
            source_mp4_url = f"/api/outputs/{job_id}/{downloaded_mp4.name}"

        music_input_path = track_mp3
        if clip_start_seconds is not None and clip_end_seconds is not None:
            music_input_path = trim_audio_clip(track_mp3, selected_track_mp3, clip_start_seconds, clip_end_seconds)

        attach_mp3_to_video(
            original_video_path, music_input_path, merged_video,
            keep_original_audio=keep_original_audio,
            original_audio_role=original_audio_role,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise HTTPException(status_code=500, detail=f"FFmpeg processing failed: {detail}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    output = {
        "job_id": job_id,
        "track_mp3_url": f"/api/outputs/{job_id}/{(selected_track_mp3 if selected_track_mp3.exists() else track_mp3).name}",
        "source_mp4_url": source_mp4_url,
        "merged_video_url": f"/api/outputs/{job_id}/{merged_video.name}",
    }
    job["attached_music"] = output
    return output


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS[job_id]


@app.get("/api/frames/{job_id}/{filename}")
async def get_frame(job_id: str, filename: str):
    path = FRAMES_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/api/outputs/{job_id}/{filename}")
async def get_output_file(job_id: str, filename: str, download: bool = False):
    path = OUTPUTS_DIR / job_id / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")
    if download:
        return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store"})
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


# Serve the frontend
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
