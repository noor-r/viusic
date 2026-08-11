"""
content_analyzer.py
Sends extracted scene frames to a vision-capable chat model to build a scene-by-scene
breakdown (setting, action, emotion, visual pacing) and then a second pass
synthesizes an overall narrative/mood summary across the whole video.
"""

import base64
import json
import os
from typing import List, Optional

from openai import OpenAI

from .json_utils import safe_json_object
from .video_processor import Scene

def resolve_model() -> str:
    if os.environ.get("KIMI_VISION_MODEL"):
        return os.environ["KIMI_VISION_MODEL"]
    if os.environ.get("MISTRAL_VISION_MODEL"):
        return os.environ["MISTRAL_VISION_MODEL"]
    if os.environ.get("KIMI_MODEL"):
        return os.environ["KIMI_MODEL"]
    if os.environ.get("MISTRAL_MODEL"):
        return os.environ["MISTRAL_MODEL"]
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

SCENE_SYSTEM_PROMPT = """You are a film/video analyst helping build a music-recommendation \
pipeline. For the image provided (a representative frame from one scene of a video), \
describe ONLY what you can see, in compact JSON. Be concrete, not flowery. \
Return strictly valid JSON with these keys:
{
  "setting": "short phrase, e.g. 'indoor kitchen, daytime'",
  "subjects": "who/what is in frame",
  "action": "what appears to be happening",
  "mood": "1-3 words, e.g. 'tense', 'joyful', 'melancholic', 'energetic'",
  "visual_energy": "low | medium | high",
  "color_tone": "e.g. 'warm/golden', 'cool/desaturated', 'high-contrast/neon'"
}
Return ONLY the JSON object, no other text."""

OVERALL_SYSTEM_PROMPT = """You are a music supervisor analyzing a video's full scene-by-scene \
breakdown to decide what music would fit best. You will receive:
- The total video duration in seconds
- A JSON list of per-scene descriptions (in chronological order)
- Basic audio-track stats (tempo/loudness/energy) if the video already has audio

Synthesize an OVERALL analysis. Consider:
- What TYPE of content this is (music video, sports highlight, vlog, ad, etc.)
- The narrative arc / what the video is trying to convey
- The emotional arc across scenes (does mood shift? build? stay constant?)
- Pacing and transitions: are cuts fast/energetic or slow/contemplative?
- Overall visual energy and tone

Return strictly valid JSON with these keys:
{
  "narrative_type": "one of: music_video | highlight_reel | vlog | short_film | documentary | advertisement | tutorial | product_showcase | other",
  "content_category": "brief category tag, e.g. 'sports', 'travel', 'music', 'fashion', 'gaming', 'food', 'nature', 'dance'",
  "overall_theme": "1-2 sentences on what the video is about / trying to convey",
  "emotional_arc": "describe how emotion evolves scene to scene, referencing approximate timestamps, e.g. 'starts calm at 0:00, builds tension by 0:20, peaks euphorically at 0:45'",
  "dominant_mood": "1-3 words capturing the overarching mood",
  "pacing": "slow | moderate | fast | mixed",
  "energy_curve": "brief description e.g. 'steady build', 'high-energy throughout', 'calm with one intense peak'",
  "genre_affinities": ["2-4 music genres/styles that would fit"],
  "key_transition_moments": ["1-4 short phrases describing notable transition points and what emotion/energy shift happens there"]
}
Return ONLY the JSON object, no other text."""


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_scene_frame(client: OpenAI, scene: Scene) -> dict:
    """Send one scene's representative frame to the model and get structured JSON back."""
    image_b64 = _encode_image(scene.frame_path)

    response = client.chat.completions.create(
        model=get_model(),
        max_tokens=500,
        temperature=0.2,
        timeout=60,
        messages=[
            {"role": "system", "content": SCENE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                    {
                        "type": "text",
                        "text": f"This frame is from scene {scene.index}, "
                                f"timestamp {scene.start:.1f}s-{scene.end:.1f}s "
                                f"(duration {scene.duration:.1f}s). Analyze it.",
                    },
                ],
            }
        ],
    )

    text = _extract_text(response)
    parsed = _safe_json_parse(text)
    parsed["scene_index"] = scene.index
    parsed["start"] = scene.start
    parsed["end"] = scene.end
    parsed["duration"] = scene.duration
    return parsed


def analyze_full_video(
    client: OpenAI,
    scene_analyses: List[dict],
    audio_features: Optional[dict] = None,
    video_duration_seconds: Optional[float] = None,
) -> dict:
    """Second pass: synthesize scene-level analyses into an overall summary."""
    payload = {
        "video_duration_seconds": video_duration_seconds,
        "scenes": scene_analyses,
        "audio_features": audio_features or "no audio track detected",
    }

    response = client.chat.completions.create(
        model=get_model(),
        max_tokens=1000,
        temperature=0.2,
        timeout=60,
        messages=[
            {"role": "system", "content": OVERALL_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
    )

    return _safe_json_parse(_extract_text(response))


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
    """Parse model output defensively even when it includes code fences or extra prose."""
    return safe_json_object(text, fallback={"_raw_unparsed": text})
