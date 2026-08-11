"""
video_processor.py
Handles all ffmpeg-based work: probing video metadata, detecting scene
(shot) boundaries, extracting representative frames per scene, and pulling
the audio track out for separate inspection (tempo/energy/loudness).
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Scene:
    index: int
    start: float          # seconds
    end: float             # seconds
    duration: float
    frame_path: Optional[str] = None  # representative frame for this scene


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe_video(video_path: str) -> VideoInfo:
    """Use ffprobe to pull basic metadata about the video."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    video_stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio_stream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)

    if video_stream is None:
        raise ValueError("No video stream found in file.")

    duration = float(data["format"].get("duration", 0))

    # fps can be a fraction like "30000/1001"
    fps_raw = video_stream.get("r_frame_rate", "0/1")
    num, den = fps_raw.split("/")
    fps = float(num) / float(den) if float(den) != 0 else 0.0

    return VideoInfo(
        duration=duration,
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        fps=fps,
        has_audio=audio_stream is not None,
    )


def detect_scenes(video_path: str, threshold: float = 0.35, min_scene_len: float = 0.6) -> List[Scene]:
    """
    Detect scene/shot boundaries using ffmpeg's built-in scene-detection filter.
    threshold: 0.0-1.0, higher = less sensitive (fewer cuts detected).
    min_scene_len: merge scenes shorter than this many seconds into neighbors.

    Falls back to fixed-interval segmentation if fewer than 2 cuts are found
    (e.g. for a single continuous clip with no hard cuts).
    """
    cmd = [
        "ffmpeg", "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    # showinfo prints to stderr; parse out pts_time values
    timestamps = []
    for line in result.stderr.splitlines():
        if "pts_time:" in line:
            try:
                part = line.split("pts_time:")[1].split(" ")[0]
                timestamps.append(float(part))
            except (IndexError, ValueError):
                continue

    info = probe_video(video_path)
    total_duration = info.duration

    boundaries = [0.0] + sorted(set(timestamps)) + [total_duration]
    # de-dup / merge boundaries that are too close together
    cleaned = [boundaries[0]]
    for b in boundaries[1:]:
        if b - cleaned[-1] >= min_scene_len:
            cleaned.append(b)
    if cleaned[-1] != total_duration:
        cleaned[-1] = total_duration

    # Fallback: if we detected essentially no cuts, chunk into N even segments
    if len(cleaned) < 3:
        target_chunks = max(3, min(8, int(total_duration // 4) or 1))
        step = total_duration / target_chunks
        cleaned = [round(step * i, 2) for i in range(target_chunks)] + [total_duration]

    scenes = []
    for i in range(len(cleaned) - 1):
        start, end = cleaned[i], cleaned[i + 1]
        scenes.append(Scene(index=i, start=start, end=end, duration=end - start))

    return scenes


def extract_frame(video_path: str, timestamp: float, out_path: str) -> str:
    """Grab a single representative frame at `timestamp` seconds."""
    cmd_fast_seek = [
        "ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path,
        "-frames:v", "1", "-q:v", "2", out_path,
    ]
    cmd_precise_seek = [
        "ffmpeg", "-y", "-i", video_path, "-ss", str(timestamp),
        "-frames:v", "1", "-q:v", "2", out_path,
    ]

    fast_error = None
    try:
        subprocess.run(cmd_fast_seek, capture_output=True, text=True, check=True)
        return out_path
    except subprocess.CalledProcessError as exc:
        fast_error = (exc.stderr or exc.stdout or str(exc)).strip()

    try:
        subprocess.run(cmd_precise_seek, capture_output=True, text=True, check=True)
        return out_path
    except subprocess.CalledProcessError as exc:
        precise_error = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(
            f"Failed to extract frame at {timestamp:.3f}s. "
            f"Fast seek error: {fast_error or 'n/a'} | Precise seek error: {precise_error or 'n/a'}"
        ) from exc


def extract_frames_for_scenes(video_path: str, scenes: List[Scene], frames_dir: str) -> List[Scene]:
    """Extract one representative frame per scene (taken at the scene midpoint)."""
    os.makedirs(frames_dir, exist_ok=True)
    for scene in scenes:
        midpoint = scene.start + (scene.duration / 2)
        out_path = os.path.join(frames_dir, f"scene_{scene.index:03d}.jpg")
        extract_frame(video_path, midpoint, out_path)
        scene.frame_path = out_path
    return scenes


def extract_audio(video_path: str, out_path: str) -> Optional[str]:
    """Extract the audio track as a wav file. Returns None if no audio stream."""
    info = probe_video(video_path)
    if not info.has_audio:
        return None
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", out_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out_path


def estimate_audio_features(wav_path: str) -> dict:
    """
    Lightweight audio feature estimation using only numpy and the stdlib wave
    module (no librosa). Returns approximate tempo (BPM), loudness (RMS dB),
    dynamic range, and a spectral brightness estimate.

    BPM is estimated via spectral-flux onset detection + autocorrelation,
    which is substantially more robust than raw-envelope autocorrelation.
    """
    import numpy as np
    import wave

    with wave.open(wav_path, "rb") as wf:
        n_frames = wf.getnframes()
        rate = wf.getframerate()
        raw = wf.readframes(n_frames)

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return {"tempo_bpm": None, "rms_db": None, "energy": None, "spectral_brightness": None, "dynamic_range_db": None}

    # --- Loudness ---
    rms = float(np.sqrt(np.mean(samples ** 2)) + 1e-9)
    rms_db = round(20.0 * float(np.log10(rms + 1e-9)), 1)

    # --- Dynamic range: ratio of RMS in loudest vs quietest 10% windows ---
    win = rate // 4  # 250 ms windows
    hop = win // 2
    rms_windows = np.array([
        float(np.sqrt(np.mean(samples[i:i + win] ** 2)) + 1e-9)
        for i in range(0, len(samples) - win, hop)
    ])
    if rms_windows.size > 4:
        top10 = np.percentile(rms_windows, 90)
        bot10 = np.percentile(rms_windows, 10)
        dynamic_range_db = round(20.0 * float(np.log10((top10 + 1e-9) / (bot10 + 1e-9))), 1)
    else:
        dynamic_range_db = None

    # --- Spectral brightness (centroid proxy) via FFT of short windows ---
    fft_size = 2048
    hop_s = fft_size // 4
    brightness_vals = []
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / rate)
    for i in range(0, len(samples) - fft_size, hop_s):
        frame = samples[i:i + fft_size] * np.hanning(fft_size)
        mag = np.abs(np.fft.rfft(frame))
        total = mag.sum() + 1e-9
        centroid = float(np.dot(freqs, mag) / total)
        brightness_vals.append(centroid)
    spectral_brightness = round(float(np.mean(brightness_vals)), 1) if brightness_vals else None

    # --- Spectral-flux onset envelope for BPM detection ---
    # Better than raw RMS envelope: captures transient attacks more cleanly.
    flux_size = 1024
    flux_hop = 256
    prev_mag = None
    flux = []
    for i in range(0, len(samples) - flux_size, flux_hop):
        frame = samples[i:i + flux_size] * np.hanning(flux_size)
        mag = np.abs(np.fft.rfft(frame))
        if prev_mag is not None:
            diff = mag - prev_mag
            flux.append(float(np.maximum(diff, 0).sum()))  # half-wave rectify
        prev_mag = mag
    flux = np.array(flux)

    tempo_bpm = None
    if flux.size > 20:
        flux_rate = rate / flux_hop  # frames per second
        # Normalize flux
        flux -= flux.mean()
        # Autocorrelation peak search in 60-200 BPM range
        autocorr = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
        min_lag = max(1, int(flux_rate * 60 / 200))
        max_lag = int(flux_rate * 60 / 60)
        if max_lag > min_lag and max_lag < len(autocorr):
            segment = autocorr[min_lag:max_lag]
            if segment.size > 0:
                peak_lag = min_lag + int(np.argmax(segment))
                bpm_raw = 60.0 * flux_rate / peak_lag
                # Also check double/half-time variants and pick the most musical
                candidates = [bpm_raw, bpm_raw * 2, bpm_raw / 2]
                # Prefer candidates in 80-160 BPM range (most common for music)
                in_range = [b for b in candidates if 80 <= b <= 160]
                tempo_bpm = round((in_range[0] if in_range else bpm_raw), 1)

    # --- Dynamics/energy score (coefficient of variation of flux) ---
    energy = round(float(np.std(flux) / (np.mean(np.abs(flux)) + 1e-9)), 3) if flux.size else 0.0

    return {
        "tempo_bpm": tempo_bpm,
        "rms_db": rms_db,
        "energy": energy,
        "spectral_brightness": spectral_brightness,
        "dynamic_range_db": dynamic_range_db,
    }
