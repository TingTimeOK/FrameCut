from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any

import cv2
import numpy as np


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".mts"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def executable(name: str) -> str:
    bundled = Path(getattr(__import__("sys"), "_MEIPASS", Path(__file__).resolve().parent.parent)) / "runtime" / f"{name}.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"未找到 {name}。请安装 FFmpeg 并加入 PATH，或使用完整 Windows 发行包。")
    return found


def run_command(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.run(args, capture_output=True, startupinfo=startup, timeout=timeout, check=False)


def probe_media(path: str) -> dict[str, Any]:
    suffix = Path(path).suffix.lower()
    result: dict[str, Any] = {
        "path": str(Path(path).resolve()),
        "name": Path(path).name,
        "kind": "video" if suffix in VIDEO_EXTENSIONS else "audio" if suffix in AUDIO_EXTENSIONS else "image",
        "duration": 5.0 if suffix in IMAGE_EXTENSIONS else 0.0,
        "width": 0,
        "height": 0,
        "fps": 30.0,
        "has_audio": False,
        "codec": "",
    }
    if suffix in IMAGE_EXTENSIONS:
        image = imread(path)
        if image is None:
            raise ValueError(f"无法读取图片：{path}")
        result["height"], result["width"] = image.shape[:2]
        return result
    process = run_command([
        executable("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", path,
    ], timeout=30)
    if process.returncode:
        raise ValueError(process.stderr.decode("utf-8", errors="replace").strip() or "无法读取媒体信息")
    data = json.loads(process.stdout.decode("utf-8"))
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video:
        result.update(kind="video", width=int(video.get("width", 0)), height=int(video.get("height", 0)), codec=video.get("codec_name", ""))
        rate = video.get("avg_frame_rate", "30/1")
        try:
            numerator, denominator = rate.split("/")
            result["fps"] = float(numerator) / max(float(denominator), 1.0) or 30.0
        except (ValueError, ZeroDivisionError):
            pass
    elif audio:
        result.update(kind="audio", codec=audio.get("codec_name", ""))
    result["has_audio"] = audio is not None
    duration = data.get("format", {}).get("duration")
    if duration is None and video:
        duration = video.get("duration")
    if duration is None and audio:
        duration = audio.get("duration")
    result["duration"] = max(0.04, float(duration or 0.0))
    return result


def imread(path: str) -> np.ndarray | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    except (OSError, cv2.error):
        return None


def imwrite(path: str, image: np.ndarray) -> None:
    suffix = Path(path).suffix or ".png"
    ok, data = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError("图片编码失败")
    data.tofile(path)


class MediaCache:
    def __init__(self, frame_capacity: int = 24) -> None:
        self.captures: dict[str, cv2.VideoCapture] = {}
        self.images: OrderedDict[str, np.ndarray] = OrderedDict()
        self.frames: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()
        self.frame_capacity = frame_capacity
        self.lock = threading.RLock()

    def image(self, path: str) -> np.ndarray | None:
        with self.lock:
            if path in self.images:
                self.images.move_to_end(path)
                return self.images[path].copy()
            image = imread(path)
            if image is not None:
                self.images[path] = image
                if len(self.images) > 16:
                    self.images.popitem(last=False)
            return image

    def frame(self, path: str, seconds: float, fps: float = 30.0) -> np.ndarray | None:
        key = (str(Path(path).resolve()), max(0, round(seconds * fps)))
        with self.lock:
            if key in self.frames:
                self.frames.move_to_end(key)
                return self.frames[key].copy()
            capture = self.captures.get(path)
            if capture is None or not capture.isOpened():
                capture = cv2.VideoCapture(path)
                if not capture.isOpened():
                    return None
                self.captures[path] = capture
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000.0)
            ok, frame = capture.read()
            if not ok:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = capture.read()
            if ok:
                self.frames[key] = frame
                if len(self.frames) > self.frame_capacity:
                    self.frames.popitem(last=False)
            return frame.copy() if ok else None

    def close(self) -> None:
        with self.lock:
            for capture in self.captures.values():
                capture.release()
            self.captures.clear()
            self.frames.clear()
            self.images.clear()


def decode_audio(path: str, sample_rate: int = 22050, start: float = 0.0, duration: float = 0.0, filters: str = "") -> np.ndarray:
    args = [executable("ffmpeg"), "-v", "error"]
    if start > 0:
        args += ["-ss", f"{start:.6f}"]
    if duration > 0:
        args += ["-t", f"{duration:.6f}"]
    args += ["-i", path]
    if filters:
        args += ["-af", filters]
    args += ["-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(sample_rate), "-"]
    process = run_command(args, timeout=180)
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace") or "音频解码失败")
    return np.frombuffer(process.stdout, dtype="<f4").reshape(-1, 2).copy()


def write_wav(path: str, samples: np.ndarray, sample_rate: int) -> None:
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2")
    with wave.open(path, "wb") as output:
        output.setnchannels(samples.shape[1] if samples.ndim == 2 else 1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def audio_envelope(samples: np.ndarray, sample_rate: int, window: float = 0.05) -> tuple[np.ndarray, float]:
    mono = np.mean(samples, axis=1) if samples.ndim == 2 else samples
    size = max(1, int(sample_rate * window))
    count = len(mono) // size
    trimmed = mono[:count * size].reshape(count, size)
    rms = np.sqrt(np.mean(trimmed * trimmed, axis=1) + 1e-12)
    return rms, window


def detect_beats(samples: np.ndarray, sample_rate: int, sensitivity: float = 1.0) -> list[float]:
    if not len(samples):
        return []
    envelope, hop = audio_envelope(samples, sample_rate, 0.025)
    flux = np.maximum(0.0, np.diff(envelope, prepend=envelope[0]))
    if len(flux) < 4:
        return []
    kernel = max(3, int(0.35 / hop))
    local = np.convolve(flux, np.ones(kernel) / kernel, mode="same")
    threshold = local + np.std(flux) * (1.4 / max(0.2, sensitivity))
    candidates = np.where((flux > threshold) & (flux > np.percentile(flux, 60)))[0]
    beats: list[float] = []
    minimum = 0.20
    for index in candidates:
        at = index * hop
        if not beats or at - beats[-1] >= minimum:
            beats.append(round(float(at), 3))
        elif flux[index] > flux[int(beats[-1] / hop)]:
            beats[-1] = round(float(at), 3)
    return beats


def detect_voice_activity(samples: np.ndarray, sample_rate: int, threshold_scale: float = 1.0) -> list[dict[str, float | str]]:
    if not len(samples):
        return []
    envelope, hop = audio_envelope(samples, sample_rate, 0.04)
    noise = np.percentile(envelope, 25)
    active_level = np.percentile(envelope, 85)
    threshold = max(0.006, noise * 2.8 * threshold_scale, active_level * 0.12)
    active = envelope > threshold
    segments: list[dict[str, float | str]] = []
    start: float | None = None
    for i, value in enumerate(np.pad(active.astype(int), (1, 1))):
        if value and start is None:
            start = (i - 1) * hop
        elif not value and start is not None:
            end = (i - 1) * hop
            if end - start >= 0.12:
                segments.append({"type": "voice", "start": round(start, 3), "end": round(end, 3)})
            start = None
    return segments


def detect_audio_events(samples: np.ndarray, sample_rate: int) -> list[dict[str, float | str]]:
    voice = detect_voice_activity(samples, sample_rate)
    events: list[dict[str, float | str]] = []
    cursor = 0.0
    duration = len(samples) / sample_rate
    for segment in voice:
        if float(segment["start"]) - cursor >= 0.35:
            events.append({"type": "silence", "start": cursor, "end": segment["start"]})
        events.append(segment)
        cursor = float(segment["end"])
    if duration - cursor >= 0.35:
        events.append({"type": "silence", "start": cursor, "end": duration})
    envelope, hop = audio_envelope(samples, sample_rate)
    loud = np.percentile(envelope, 95)
    for i, value in enumerate(envelope):
        if value > max(0.15, loud * 0.98):
            events.append({"type": "loud", "start": round(i * hop, 3), "end": round((i + 1) * hop, 3)})
    return sorted(events, key=lambda e: float(e["start"]))


def alignment_offset(reference: np.ndarray, target: np.ndarray, sample_rate: int) -> float:
    left = np.mean(reference, axis=1)
    right = np.mean(target, axis=1)
    count = min(len(left), len(right), sample_rate * 120)
    left, right = left[:count], right[:count]
    size = 1 << (2 * count - 1).bit_length()
    spectrum = np.fft.rfft(left, size) * np.conj(np.fft.rfft(right, size))
    correlation = np.fft.irfft(spectrum, size)
    lag = int(np.argmax(correlation))
    if lag > size // 2:
        lag -= size
    return -lag / sample_rate


def parse_timestamp(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def parse_srt(text: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    result = []
    for block in blocks:
        lines = block.strip().splitlines()
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        start, end = [part.strip() for part in lines[timing].split("-->")[:2]]
        result.append({"start": parse_timestamp(start), "end": parse_timestamp(end), "text": "\n".join(lines[timing + 1:]).strip()})
    return result


def parse_lrc(text: str) -> list[dict[str, Any]]:
    entries = []
    for line in text.splitlines():
        tags = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        content = re.sub(r"\[[^\]]+\]", "", line).strip()
        for minutes, seconds in tags:
            if content:
                entries.append({"start": int(minutes) * 60 + float(seconds), "text": content})
    entries.sort(key=lambda e: e["start"])
    for index, entry in enumerate(entries):
        entry["end"] = entries[index + 1]["start"] if index + 1 < len(entries) else entry["start"] + 4.0
    return entries


def format_time(seconds: float, milliseconds: bool = False) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    secs = int(seconds % 60)
    if milliseconds:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{int(seconds % 1 * 1000):03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
