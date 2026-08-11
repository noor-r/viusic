import json
from typing import Any


def safe_json_object(text: Any, fallback: dict | None = None) -> dict:
    """Parse a JSON object from model output, tolerating code fences or surrounding prose."""
    if not isinstance(text, str):
        return dict(fallback or {})

    cleaned = text.strip()
    if not cleaned:
        return dict(fallback or {})

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        cleaned = "\n".join(lines).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = cleaned.find("{")
        while start != -1:
            candidate = cleaned[start:]
            try:
                parsed, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                start = cleaned.find("{", start + 1)
                continue

            if isinstance(parsed, dict):
                return parsed
            break

        return dict(fallback or {"_raw_unparsed": text})

    if isinstance(parsed, dict):
        return parsed
    return dict(fallback or {"_raw_unparsed": text})
