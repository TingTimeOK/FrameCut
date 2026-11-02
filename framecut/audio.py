from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np

from .media import decode_audio, write_wav
from .model import Clip, Project


def atempo_chain(speed: float) -> str:
    filters = []
    remaining = max(0.05, speed)
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}")
    return ",".join(filters)


def audio_filter(clip: Clip, sample_rate: int) -> str:
    filters = []
    if clip.vocal_mode == "keep_vocal":
        filters.append("pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1")
    elif clip.vocal_mode == "remove_vocal":
        filters.append("pan=stereo|c0=c0-c1|c1=c1-c0,volume=0.8")
    if clip.denoise > 0:
        filters.append(f"afftdn=nr={min(60, max(1, clip.denoise)):.1f}:nf=-35")
    if clip.loudness:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    pitch = clip.pitch
    if clip.voice_effect == "chipmunk":
        pitch += 7
    elif clip.voice_effect == "deep":
        pitch -= 6
    elif clip.voice_effect == "child":
        pitch += 4
    if pitch:
        filters.append(f"rubberband=pitch={2.0 ** (pitch / 12.0):.6f}")
    if clip.voice_effect == "robot":
        filters.append("acrusher=bits=4:mix=0.65,vibrato=f=18:d=0.35")
    elif clip.voice_effect == "echo":
        filters.append("aecho=0.8:0.65:70|140:0.35|0.2")
    elif clip.voice_effect == "radio":
        filters.append("highpass=f=350,lowpass=f=3200,acompressor=threshold=-18dB:ratio=4")
    if clip.effects.get("speed_points"):
        points = clip.effects["speed_points"]
        average = sum(float(p["speed"]) for p in points) / len(points)
        filters.append(atempo_chain(average))
    elif abs(clip.speed - 1.0) > 0.001:
        filters.append(atempo_chain(clip.speed))
    if clip.reverse:
        filters.append("areverse")
    filters.append(f"aresample={sample_rate}")
    return ",".join(filters)


def mix_project(project: Project, duration: float | None = None, progress: Callable[[float], None] | None = None) -> np.ndarray:
    duration = max(duration if duration is not None else project.duration, 1.0 / project.fps)
    total = max(2, int(math.ceil(duration * project.sample_rate)))
    mix = np.zeros((total, 2), dtype=np.float32)
    clips = [c for c in project.clips if c.audible and not c.muted and c.volume > 0 and c.path]
    for index, clip in enumerate(clips):
        if clip.track < len(project.tracks) and project.tracks[clip.track].muted:
            continue
        input_duration = min(clip.source_duration or clip.duration * clip.speed, clip.duration * max(clip.speed, 1.0) + 1.0)
        try:
            samples = decode_audio(clip.path, project.sample_rate, clip.source_offset, input_duration, audio_filter(clip, project.sample_rate))
        except (RuntimeError, OSError):
            continue
        required = max(2, int(round(clip.duration * project.sample_rate)))
        if len(samples) < required:
            samples = np.pad(samples, ((0, required - len(samples)), (0, 0)))
        else:
            samples = samples[:required]
        gain = np.full(required, clip.volume, dtype=np.float32)
        if clip.keyframes.get("volume"):
            times = np.arange(required, dtype=np.float32) / project.sample_rate
            ordered = sorted(clip.keyframes["volume"], key=lambda f: f["time"])
            gain *= np.interp(times, [f["time"] for f in ordered], [f["value"] for f in ordered]).astype(np.float32)
        samples *= gain[:, None]
        fade_in = min(required, int(clip.fade_in * project.sample_rate))
        fade_out = min(required, int(clip.fade_out * project.sample_rate))
        if fade_in:
            samples[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)[:, None]
        if fade_out:
            samples[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)[:, None]
        if clip.channel_mode == "left":
            samples[:, 1] = samples[:, 0]
        elif clip.channel_mode == "right":
            samples[:, 0] = samples[:, 1]
        elif clip.channel_mode == "mono":
            mono = samples.mean(axis=1)
            samples[:, 0] = samples[:, 1] = mono
        elif clip.channel_mode == "swap":
            samples = samples[:, ::-1]
        pan = np.clip(clip.pan, -1.0, 1.0)
        left_gain = math.cos((pan + 1.0) * math.pi / 4.0)
        right_gain = math.sin((pan + 1.0) * math.pi / 4.0)
        samples *= np.array([left_gain, right_gain], dtype=np.float32)
        start = max(0, round(clip.start * project.sample_rate))
        end = min(total, start + required)
        if end > start:
            mix[start:end] += samples[:end - start]
        if progress:
            progress((index + 1) / max(1, len(clips)))
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0.98:
        mix *= 0.98 / peak
    return mix


def mix_to_file(project: Project, path: str, duration: float | None = None, progress: Callable[[float], None] | None = None) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    samples = mix_project(project, duration, progress)
    write_wav(path, samples, project.sample_rate)
    return path
