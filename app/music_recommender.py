"""
music_recommender.py
Takes the video's overall + scene-by-scene analysis and produces:
  1. YouTube search queries (passed to YOUR existing YouTube search feature
     via the `youtube_search_fn` slot below).
  2. An AI music-generation prompt (for Suno/Udio-style tools), including a
     structured section-by-section cue sheet that mirrors the video's
     transitions so a generated track can follow the same arc.

Wire your existing YouTube search feature into `set_youtube_search_function`.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import Callable, List, Optional

from openai import OpenAI

from .json_utils import safe_json_object

def resolve_model() -> str:
    if os.environ.get("KIMI_TEXT_MODEL"):
        return os.environ["KIMI_TEXT_MODEL"]
    if os.environ.get("MISTRAL_MODEL"):
        return os.environ["MISTRAL_MODEL"]
    if os.environ.get("KIMI_MODEL"):
        return os.environ["KIMI_MODEL"]
    if os.environ.get("OPENAI_API_KEY"):
        return "gpt-4o-mini"
    if os.environ.get("MISTRAL_API_KEY"):
        return "mistral-small-latest"
    return "gpt-4o-mini"


MODEL = None


def get_model() -> str:
    global MODEL
    if MODEL is None:
        MODEL = resolve_model()
    return MODEL

RECOMMENDER_SYSTEM_PROMPT = """You are a music supervisor. You are given:
- The VIDEO DURATION in seconds — this is critical. The recommended music MUST suit this length.
  If the video is under 3 minutes, focus on short tracks or a specific clip range. Never suggest
  a 10-minute ambient piece for a 30-second video.
- A structured analysis of the video (overall theme/mood/pacing, narrative type, content category)
- Per-scene emotional beats and transition points
- Existing audio features if available

Produce music-matching recommendations respecting:
- VIDEO LENGTH: ideal_track_duration must match the video's actual duration closely
- The emotional arc, pacing/energy curve, and transition moments
- What the video is trying to convey
- Current music trends in the relevant genre (what sounds fresh and contemporary right now)

Return strictly valid JSON with these keys:
{
  "ideal_track_duration": "duration matching the video length, e.g. '0:55-1:10'. If a longer track works, specify a clip range to use, e.g. 'use 1:00-2:05 of a longer track'",
  "youtube_search_queries": [
    "3-5 YouTube search query strings. For videos under 2 minutes include length hints like 'short intro', '60 second', '1 minute edit'. Always include 'no copyright' or 'royalty free'. Vary genre and mood terms."
  ],
  "song_style_descriptors": ["5-8 short tags describing ideal song style, e.g. 'cinematic', 'slow-burn build', 'warm analog synths'"],
  "reference_artists_or_tracks_style": "1-2 sentences describing the STYLE of well-known reference points, without naming copyrighted lyrics — genre/artist-style only, e.g. 'in the vein of Bon Iver's ambient folk or Hans Zimmer's minimalist builds'",
  "trending_directions": "1-2 sentences on currently trending sounds in the recommended genre that would feel fresh and contemporary (e.g. 'phonk-influenced trap beats are dominating sports/action content in 2024-2025', 'hyper-compressed pop production with glassy synths is trending for upbeat lifestyle content')",
  "suno_udio_prompt": "a single dense prompt string (under 200 words) ready to paste into Suno/Udio. Include genre, mood, instrumentation, tempo/BPM, structure, how it should evolve to match the video's transitions, AND a total duration target matching the video length.",
  "section_cue_sheet": [
    {"timestamp_range": "0:00-0:08", "musical_intent": "what the music should be doing here and why, tied to the visual/emotional beat"}
  ],
  "tempo_bpm_recommendation": "a specific BPM or tight range, reasoned from the video's pacing/energy curve",
  "reasoning": "2-3 sentences explaining why this music choice fits the emotional arc, transitions, AND matches the video's specific duration"
}
Return ONLY the JSON object, no other text."""


# ---------------------------------------------------------------------------
# YOUTUBE SEARCH INTEGRATION SLOT
# ---------------------------------------------------------------------------
# Plug your existing YouTube search feature in here. It should accept a
# query string and return a list of result dicts, e.g.:
#   [{"title": "...", "url": "...", "channel": "...", "duration": "3:45"}, ...]
#
# Wire it up at app startup, e.g. in main.py:
#     from app.music_recommender import set_youtube_search_function
#     set_youtube_search_function(my_youtube_search)
#
# If left unset, `run_youtube_searches` will just return the raw queries so
# the frontend can still show them (for manual searching / copy-paste).

_youtube_search_fn: Optional[Callable[[str], List[dict]]] = None


def _downloader_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "downloader"


def _discover_downloader_artifact(base_dir: Path) -> Optional[str]:
    project_dir = base_dir / "SoundMind.Downloader"
    candidates = [
        project_dir / "bin" / "Debug" / "net10.0" / "SoundMind.Downloader.exe",
        project_dir / "bin" / "Debug" / "net10.0" / "SoundMind.Downloader.dll",
        project_dir / "bin" / "Release" / "net10.0" / "SoundMind.Downloader.exe",
        project_dir / "bin" / "Release" / "net10.0" / "SoundMind.Downloader.dll",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Support alternate target frameworks/configurations by scanning bin outputs.
    discovered: list[Path] = []
    for pattern in ("bin/**/SoundMind.Downloader.exe", "bin/**/SoundMind.Downloader.dll"):
        discovered.extend(project_dir.glob(pattern))

    if discovered:
        best = max(discovered, key=lambda path: path.stat().st_mtime)
        return str(best)

    return None


def _resolve_downloader_path() -> Optional[str]:
    """Locate the bundled SoundMind downloader executable or DLL."""
    bundled = _discover_downloader_artifact(_downloader_base_dir())
    if bundled:
        return bundled

    for name in ("SoundMind.Downloader.exe", "SoundMind.Downloader"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def _resolve_dotnet_path() -> Optional[str]:
    """Resolve dotnet CLI path even when PATH is not inherited by the process."""
    resolved = shutil.which("dotnet")
    if resolved:
        return resolved

    # Common Windows install location fallback.
    windows_default = Path("C:/Program Files/dotnet/dotnet.exe")
    if windows_default.exists():
        return str(windows_default)

    dotnet_root = os.environ.get("DOTNET_ROOT")
    if dotnet_root:
        candidate = Path(dotnet_root) / "dotnet.exe"
        if candidate.exists():
            return str(candidate)

    return None


def _build_downloader_if_needed() -> Optional[str]:
    """Attempt a local downloader build and return the resolved binary path."""
    base_dir = _downloader_base_dir()
    solution = base_dir / "YoutubeDownloader.slnx"
    project = base_dir / "SoundMind.Downloader" / "SoundMind.Downloader.csproj"
    dotnet_path = _resolve_dotnet_path()

    if not dotnet_path:
        raise RuntimeError("dotnet CLI was not found. Install .NET SDK to build the downloader project.")

    for build_target in (solution, project):
        if not build_target.exists():
            continue
        command = [dotnet_path, "build", str(build_target), "-c", "Debug", "-v", "minimal"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=900)
        except FileNotFoundError as exc:
            raise RuntimeError("dotnet CLI was not found. Install .NET SDK to build the downloader project.") from exc

        if completed.returncode == 0:
            resolved = _discover_downloader_artifact(base_dir)
            if resolved:
                return resolved

    return None


def _ensure_downloader_path() -> str:
    downloader_path = _resolve_downloader_path()
    if downloader_path:
        return downloader_path

    rebuilt_path = _build_downloader_if_needed()
    if rebuilt_path:
        return rebuilt_path

    raise RuntimeError(
        "The downloader executable was not found and an automatic build did not produce it. "
        "Build the downloader project at downloader/YoutubeDownloader.slnx first."
    )


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    if isinstance(seconds, str):
        return seconds
    total_seconds = int(float(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def normalize_downloader_results(payload: object) -> List[dict]:
    """Normalize the downloader's JSON output into the shape the web UI expects."""
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            payload = payload["results"]
        elif isinstance(payload.get("videos"), list):
            payload = payload["videos"]
        else:
            return []

    if not isinstance(payload, list):
        return []

    normalized: List[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "title": item.get("title") or item.get("name") or "Untitled",
            "channel": item.get("author") or item.get("channel") or item.get("uploader") or "YouTube",
            "url": item.get("url") or item.get("link") or "",
            "duration": _format_duration(item.get("durationSeconds") or item.get("duration")),
            "thumbnail": item.get("thumbnail") or item.get("thumb") or "",
        })
    return normalized[:5]


def _search_with_ytdlp(query: str) -> List[dict]:
    """Fallback YouTube search using yt-dlp when the bundled downloader is unavailable."""
    command = [
        "yt-dlp",
        *_build_ytdlp_common_args(),
        f"ytsearch5:{query}",
        "--dump-single-json",
        "--flat-playlist",
        "--no-warnings",
        "--skip-download",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(stderr or f"yt-dlp search failed with exit code {completed.returncode}")

    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"yt-dlp returned invalid JSON: {completed.stdout[:200]}") from exc

    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []

    normalized: List[dict] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": item.get("title") or "Untitled",
                "channel": item.get("uploader") or item.get("channel") or "YouTube",
                "url": item.get("url") or item.get("webpage_url") or "",
                "duration": _format_duration(item.get("duration")),
                "thumbnail": item.get("thumbnail") or "",
            }
        )

    return normalized[:5]


def _downloader_fallback_enabled() -> bool:
    return os.environ.get("DOWNLOADER_FALLBACK_TO_YTDLP", "1").strip().lower() in {"1", "true", "yes"}


def _resolve_ytdlp_cookies_file() -> str:
    """Resolve cookies source for yt-dlp from file path or env content."""
    cookies_file = (os.environ.get("YTDLP_COOKIES_FILE") or "").strip()
    if cookies_file:
        return cookies_file

    # Support base64-encoded cookie payload for PaaS environments.
    cookies_b64 = (os.environ.get("YTDLP_COOKIES_B64") or "").strip()
    if cookies_b64:
        try:
            import base64

            cookie_text = base64.b64decode(cookies_b64).decode("utf-8")
        except Exception:
            return ""

        digest = sha256(cookies_b64.encode("utf-8")).hexdigest()[:12]
        cookie_path = Path(tempfile.gettempdir()) / f"yt-dlp-cookies-{digest}.txt"
        cookie_path.write_text(cookie_text, encoding="utf-8")
        return str(cookie_path)

    return ""


def _browser_cookie_db_available(cookies_from_browser: str) -> bool:
    """Best-effort check for known browser cookie DB paths before enabling browser-cookie mode."""
    browser = (cookies_from_browser or "").split(":", 1)[0].strip().lower()
    if not browser:
        return False

    system_name = platform.system().lower()
    home = Path.home()
    candidates: list[Path] = []

    if browser == "chrome":
        if system_name == "windows":
            local_app_data = Path(os.environ.get("LOCALAPPDATA") or "")
            if local_app_data:
                base = local_app_data / "Google" / "Chrome" / "User Data" / "Default"
                candidates = [base / "Cookies", base / "Network" / "Cookies"]
        elif system_name == "darwin":
            base = home / "Library" / "Application Support" / "Google" / "Chrome" / "Default"
            candidates = [base / "Cookies", base / "Network" / "Cookies"]
        else:
            base = home / ".config" / "google-chrome" / "Default"
            candidates = [base / "Cookies", base / "Network" / "Cookies"]
    elif browser == "chromium":
        if system_name == "windows":
            local_app_data = Path(os.environ.get("LOCALAPPDATA") or "")
            if local_app_data:
                base = local_app_data / "Chromium" / "User Data" / "Default"
                candidates = [base / "Cookies", base / "Network" / "Cookies"]
        elif system_name == "darwin":
            base = home / "Library" / "Application Support" / "Chromium" / "Default"
            candidates = [base / "Cookies", base / "Network" / "Cookies"]
        else:
            base = home / ".config" / "chromium" / "Default"
            candidates = [base / "Cookies", base / "Network" / "Cookies"]
    elif browser == "edge":
        if system_name == "windows":
            local_app_data = Path(os.environ.get("LOCALAPPDATA") or "")
            if local_app_data:
                base = local_app_data / "Microsoft" / "Edge" / "User Data" / "Default"
                candidates = [base / "Cookies", base / "Network" / "Cookies"]
        elif system_name == "darwin":
            base = home / "Library" / "Application Support" / "Microsoft Edge" / "Default"
            candidates = [base / "Cookies", base / "Network" / "Cookies"]
        else:
            base = home / ".config" / "microsoft-edge" / "Default"
            candidates = [base / "Cookies", base / "Network" / "Cookies"]
    elif browser == "firefox":
        if system_name == "windows":
            app_data = Path(os.environ.get("APPDATA") or "")
            if app_data:
                profiles = app_data / "Mozilla" / "Firefox" / "Profiles"
                if profiles.exists():
                    candidates.extend(profiles.glob("*/cookies.sqlite"))
        elif system_name == "darwin":
            profiles = home / "Library" / "Application Support" / "Firefox" / "Profiles"
            if profiles.exists():
                candidates.extend(profiles.glob("*/cookies.sqlite"))
        else:
            profiles = home / ".mozilla" / "firefox"
            if profiles.exists():
                candidates.extend(profiles.glob("*/cookies.sqlite"))
    else:
        # Unknown browser token: let yt-dlp handle custom identifiers.
        return True

    return any(candidate.exists() for candidate in candidates)


def _build_ytdlp_common_args() -> List[str]:
    """Build shared yt-dlp args with optional anti-bot/auth settings from env vars."""
    args = [
        "--no-playlist",
        "--retries",
        os.environ.get("YTDLP_RETRIES", "5"),
        "--fragment-retries",
        os.environ.get("YTDLP_FRAGMENT_RETRIES", "5"),
        "--extractor-retries",
        os.environ.get("YTDLP_EXTRACTOR_RETRIES", "5"),
        "--sleep-requests",
        os.environ.get("YTDLP_SLEEP_REQUESTS", "1"),
    ]

    cookies_file = _resolve_ytdlp_cookies_file()
    cookies_from_browser = (os.environ.get("YTDLP_COOKIES_FROM_BROWSER") or "").strip()
    if cookies_file:
        args.extend(["--cookies", cookies_file])
    elif cookies_from_browser and _browser_cookie_db_available(cookies_from_browser):
        args.extend(["--cookies-from-browser", cookies_from_browser])

    js_runtime = _resolve_ytdlp_js_runtime()
    if js_runtime:
        args.extend(["--js-runtimes", js_runtime])

    user_agent = (os.environ.get("YTDLP_USER_AGENT") or "").strip()
    if user_agent:
        args.extend(["--user-agent", user_agent])

    return args


def _resolve_ytdlp_js_runtime() -> str:
    """Return a single supported JS runtime token for broad yt-dlp compatibility."""
    raw_value = (os.environ.get("YTDLP_JS_RUNTIMES") or "node").strip()
    if not raw_value:
        return "node"

    # Accept comma and whitespace-separated values, but pass one runtime to yt-dlp.
    requested = [token.strip().lower() for token in re.split(r"[,\s]+", raw_value) if token.strip()]
    supported = {"node", "deno", "bun", "quickjs"}
    for runtime in requested:
        if runtime in supported:
            return runtime

    return "node"


def _download_media_with_ytdlp(video_url: str, output_path: str) -> str:
    """Fallback media download using yt-dlp for deployment environments."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    output_ext = output.suffix.lower()
    if output_ext == ".mp3":
        command = [
            "yt-dlp",
            *_build_ytdlp_common_args(),
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--output",
            str(output),
            video_url,
        ]
    elif output_ext == ".mp4":
        command = [
            "yt-dlp",
            *_build_ytdlp_common_args(),
            "--format",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "--output",
            str(output),
            video_url,
        ]
    else:
        raise RuntimeError("yt-dlp fallback supports only .mp3 and .mp4 outputs.")

    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=900)
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(stderr or f"yt-dlp download failed with exit code {completed.returncode}")

    if not output.exists():
        raise RuntimeError("yt-dlp reported success but output file was not created.")

    return str(output)


def search_with_downloader(query: str) -> List[dict]:
    """Search YouTube via the bundled C# downloader executable."""
    downloader_path = _resolve_downloader_path()
    if not downloader_path:
        return _search_with_ytdlp(query)

    command = [downloader_path, "search", query]
    if downloader_path.endswith(".dll"):
        dotnet_path = _resolve_dotnet_path()
        if not dotnet_path:
            raise RuntimeError("dotnet CLI was not found. Install .NET SDK to run the downloader project.")
        command = [dotnet_path, downloader_path, "search", query]

    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
    if completed.returncode != 0:
        if _downloader_fallback_enabled():
            return _search_with_ytdlp(query)
        stderr = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(stderr or f"Downloader failed with exit code {completed.returncode}")

    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Downloader returned invalid JSON: {completed.stdout[:200]}") from exc

    normalized = normalize_downloader_results(payload)
    if normalized:
        return normalized
    if _downloader_fallback_enabled():
        return _search_with_ytdlp(query)
    return normalized


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "track"


def download_media_with_downloader(video_url: str, output_path: str) -> str:
    """Download a YouTube URL to a media file using the bundled C# downloader."""
    try:
        downloader_path = _ensure_downloader_path()
    except Exception:
        if _downloader_fallback_enabled():
            return _download_media_with_ytdlp(video_url, output_path)
        raise

    command = [downloader_path, video_url, output_path]
    if downloader_path.endswith(".dll"):
        dotnet_path = _resolve_dotnet_path()
        if not dotnet_path:
            raise RuntimeError("dotnet CLI was not found. Install .NET SDK to run the downloader project.")
        command = [dotnet_path, downloader_path, video_url, output_path]

    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
    if completed.returncode != 0:
        if _downloader_fallback_enabled():
            return _download_media_with_ytdlp(video_url, output_path)

        stderr = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(stderr or f"Downloader failed with exit code {completed.returncode}")

    return output_path


def download_video_with_downloader(video_url: str, output_mp4_path: str) -> str:
    """Backward-compatible wrapper for MP4 downloads."""
    return download_media_with_downloader(video_url, output_mp4_path)


def set_youtube_search_function(fn: Callable[[str], List[dict]]) -> None:
    """Register your existing YouTube search feature here."""
    global _youtube_search_fn
    _youtube_search_fn = fn


def run_youtube_searches(queries: List[str], max_results_per_query: int = 5) -> dict:
    """
    Runs each recommended query through the registered YouTube search
    function, if one has been wired up. Falls back to returning just the
    query strings (no results) so the UI degrades gracefully.
    """
    if not queries:
        return {}

    if _youtube_search_fn is None:
        return {q: {"status": "no_search_function_registered", "results": []} for q in queries}

    _search_fn = _youtube_search_fn  # local reference so Pylance knows it's non-None

    def _search(q: str) -> tuple[str, dict]:
        try:
            results = _search_fn(q)
            return q, {"status": "ok", "results": results[:max_results_per_query]}
        except Exception as e:  # noqa: BLE001
            return q, {"status": "error", "error": str(e), "results": []}

    output = {}
    with ThreadPoolExecutor(max_workers=min(len(queries), 4)) as pool:
        for q, result in pool.map(_search, queries):
            output[q] = result
    return output


def _build_duration_hint(video_duration_seconds: Optional[float]) -> str:
    if video_duration_seconds is None:
        return "short edit"

    duration = int(float(video_duration_seconds))
    if duration < 90:
        return "1 minute short edit"
    if duration < 180:
        return "short edit"
    if duration < 300:
        return "2-3 minute clip"
    return "3-4 minute clip"


def _normalize_query(query: str, mood: str, tag_phrase: str, bpm: str, video_duration_seconds: Optional[float]) -> str:
    compact = " ".join(str(query).strip().split())
    if not compact:
        compact = f"{mood} {tag_phrase} instrumental"

    lower = compact.lower()
    if re.search(r"\b(1|2)\s*(hour|hr|hrs|hours)\b|\bfull album\b|\b10 hour\b", lower):
        compact = re.sub(r"\b(1|2)\s*(hour|hr|hrs|hours)\b", "", lower, flags=re.IGNORECASE)
        compact = re.sub(r"\bfull album\b", "", compact, flags=re.IGNORECASE)
        compact = re.sub(r"\s+", " ", compact).strip()

    if "royalty free" not in lower and "no copyright" not in lower:
        compact = f"{compact} royalty free"
    if "instrumental" not in lower:
        compact = f"{compact} instrumental"
    if "short" not in lower and "clip" not in lower and "edit" not in lower and "loop" not in lower:
        compact = f"{compact} {_build_duration_hint(video_duration_seconds)}"
    if bpm and f"{bpm.lower()}" not in lower:
        compact = f"{compact} {bpm}"

    return " ".join(compact.split())


def build_music_search_queries(
    overall_analysis: dict,
    recommendation: dict,
    video_duration_seconds: Optional[float] = None,
) -> List[str]:
    """Build LLM-guided, duration-aware search queries for music matching."""
    tags = recommendation.get("song_style_descriptors") or []
    mood = (overall_analysis or {}).get("dominant_mood") or "cinematic"
    bpm = (recommendation or {}).get("tempo_bpm_recommendation") or ""
    trending = recommendation.get("trending_directions") or ""

    tag_tokens = [str(t).strip() for t in tags if str(t).strip()][:3]
    tag_phrase = " ".join(tag_tokens) if tag_tokens else "instrumental"

    raw_queries = recommendation.get("youtube_search_queries") or []
    if isinstance(raw_queries, list):
        query_candidates = [str(q).strip() for q in raw_queries if str(q).strip()]
    else:
        query_candidates = []

    if not query_candidates:
        query_candidates = [
            f"{mood} {tag_phrase} instrumental",
            f"{mood} cinematic background music",
            f"{tag_phrase} soundtrack",
        ]

    normalized: List[str] = []
    for query in query_candidates:
        normalized_query = _normalize_query(query, mood, tag_phrase, bpm, video_duration_seconds)
        if normalized_query not in normalized:
            normalized.append(normalized_query)

    if len(normalized) < 3:
        extra_queries = [
            f"{mood} {tag_phrase} instrumental {_build_duration_hint(video_duration_seconds)} royalty free",
            f"{mood} cinematic background music {_build_duration_hint(video_duration_seconds)} no copyright",
            f"{tag_phrase} soundtrack edit {_build_duration_hint(video_duration_seconds)} royalty free",
        ]
        for query in extra_queries:
            compact = " ".join(query.split())
            if compact not in normalized:
                normalized.append(compact)

    if trending and len(normalized) < 5:
        normalized.append(f"{tag_phrase} {trending[:80]} royalty free")

    return normalized[:5]


def _normalize_recommendation_duration(recommendation: dict, video_duration_seconds: Optional[float]) -> dict:
    """Keep LLM recommendations practical for short-form video by avoiding absurdly long durations."""
    normalized = dict(recommendation or {})
    ideal_duration = normalized.get("ideal_track_duration")
    if not isinstance(ideal_duration, str) or not ideal_duration.strip():
        return normalized

    text = ideal_duration.strip().lower()
    if re.search(r"\b(1|2)\s*(hour|hr|hrs|hours)\b|\bfull album\b|\b10 hour\b", text):
        if video_duration_seconds is not None:
            duration = int(float(video_duration_seconds))
            if duration < 90:
                normalized["ideal_track_duration"] = "use 0:30-1:00 of a longer track"
            elif duration < 180:
                normalized["ideal_track_duration"] = "use 0:45-1:30 of a longer track"
            else:
                normalized["ideal_track_duration"] = "use 1:00-2:30 of a longer track"
        else:
            normalized["ideal_track_duration"] = "use a short clip from a longer track"

    return normalized


def fallback_search_queries(overall_analysis: dict, recommendation: dict, video_duration_seconds: Optional[float] = None) -> List[str]:
    """Build pragmatic search queries when model output omits them."""
    return build_music_search_queries(overall_analysis, recommendation, video_duration_seconds=video_duration_seconds)


def build_recommended_music_candidates(
    overall_analysis: dict,
    recommendation: dict,
    grouped_results: dict,
    video_duration_seconds: Optional[float] = None,
) -> List[dict]:
    """Rank and deduplicate results from multiple queries into a compact shortlist."""
    mood = (overall_analysis or {}).get("dominant_mood") or ""
    tags = recommendation.get("song_style_descriptors") or []
    tag_tokens = [str(t).strip().lower() for t in tags if str(t).strip()]
    desired_duration = int(float(video_duration_seconds)) if video_duration_seconds is not None else None

    scored_candidates: List[tuple[float, dict]] = []
    seen_urls = set()

    for query, result_payload in grouped_results.items():
        if not isinstance(result_payload, dict):
            continue
        results = result_payload.get("results") or []
        if not isinstance(results, list):
            continue
        for item in results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title and not url:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            duration_seconds = None
            raw_duration = item.get("duration_seconds") if isinstance(item.get("duration_seconds"), (int, float)) else None
            if raw_duration is None:
                duration_text = str(item.get("duration") or "").strip()
                if re.search(r"\d+", duration_text):
                    try:
                        parts = [int(p) for p in re.findall(r"\d+", duration_text)]
                        if len(parts) == 1:
                            duration_seconds = parts[0]
                        elif len(parts) >= 2:
                            duration_seconds = parts[-1] + (parts[-2] * 60 if len(parts) >= 2 else 0)
                    except ValueError:
                        duration_seconds = None

            if duration_seconds is not None and duration_seconds > 7200:
                continue

            title_lower = title.lower()
            query_lower = str(query).lower()
            text_blob = f"{title_lower} {query_lower}"
            score = 0.0
            if mood:
                score += 1.5 if mood.lower() in text_blob else 0.0
            if tag_tokens:
                score += 0.5 * sum(1 for tag in tag_tokens if tag in text_blob)
            if desired_duration is not None and duration_seconds is not None:
                if 30 <= duration_seconds <= max(90, desired_duration + 90):
                    score += 1.0
                elif duration_seconds > max(90, desired_duration + 90):
                    score -= 0.5
            if "royalty free" in query_lower or "no copyright" in query_lower:
                score += 0.5
            if "short" in query_lower or "clip" in query_lower or "edit" in query_lower:
                score += 0.5

            normalized_item = dict(item)
            normalized_item["duration_seconds"] = duration_seconds
            normalized_item["match_score"] = round(score, 2)
            scored_candidates.append((score, normalized_item))

    scored_candidates.sort(key=lambda entry: (-entry[0], entry[1].get("title", "")))
    ranked = [item for _, item in scored_candidates[:8]]
    if not ranked:
        return []

    return ranked


# ---------------------------------------------------------------------------
# AI MUSIC RECOMMENDATION (genre matching + generative prompt)
# ---------------------------------------------------------------------------

def generate_music_recommendation(
    client: OpenAI,
    overall_analysis: dict,
    scene_analyses: List[dict],
    audio_features: Optional[dict] = None,
    video_duration_seconds: Optional[float] = None,
) -> dict:
    payload = {
        "video_duration_seconds": video_duration_seconds,
        "overall_analysis": overall_analysis,
        "scene_by_scene": scene_analyses,
        "existing_audio_features": audio_features or "no audio track detected",
    }

    response = client.chat.completions.create(
        model=get_model(),
        max_tokens=1200,
        temperature=0.2,
        timeout=60,
        messages=[
            {"role": "system", "content": RECOMMENDER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
    )

    recommendation = _safe_json_parse(_extract_text(response))
    return _normalize_recommendation_duration(recommendation, video_duration_seconds)


def _extract_text(response) -> str:
    choice = response.choices[0] if getattr(response, "choices", None) else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", "") if message else ""
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _safe_json_parse(text: str) -> dict:
    return safe_json_object(text, fallback={"_raw_unparsed": text})
