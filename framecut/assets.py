from __future__ import annotations

import math
import os
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def asset_directory() -> Path:
    configured = os.environ.get("FRAMECUT_HOME")
    if configured:
        root = Path(configured)
    else:
        root = Path.home() / "Documents" / "FrameCut"
    path = root / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_builtin_assets() -> dict[str, list[Path]]:
    root = asset_directory()
    stickers = root / "stickers"
    audio = root / "audio"
    stickers.mkdir(exist_ok=True)
    audio.mkdir(exist_ok=True)
    sticker_specs = [
        ("星光", "star", "#FFE27A"),
        ("爱心", "heart", "#FF6D91"),
        ("箭头", "arrow", "#6DE5DC"),
        ("灵感", "bulb", "#FFD36A"),
        ("对话", "bubble", "#A78BFA"),
        ("播放", "play", "#70A5FD"),
    ]
    sticker_paths = []
    for name, shape, color in sticker_specs:
        path = stickers / f"{shape}.png"
        if not path.exists():
            image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            if shape == "star":
                points = []
                for i in range(10):
                    angle = -math.pi / 2 + i * math.pi / 5
                    radius = 105 if i % 2 == 0 else 45
                    points.append((128 + math.cos(angle) * radius, 128 + math.sin(angle) * radius))
                draw.polygon(points, fill=color, outline="#FFFFFF", width=6)
            elif shape == "heart":
                points = []
                for i in range(101):
                    t = i / 100 * math.tau
                    x = 16 * math.sin(t) ** 3
                    y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
                    points.append((128 + x * 6, 128 - y * 6))
                draw.polygon(points, fill=color)
            elif shape == "arrow":
                draw.line((42, 190, 184, 56), fill=color, width=30)
                draw.polygon([(138, 42), (207, 38), (202, 110)], fill=color)
            elif shape == "bulb":
                draw.ellipse((58, 30, 198, 170), fill=color, outline="#FFF6CF", width=5)
                draw.rounded_rectangle((96, 151, 160, 214), radius=12, fill="#E9EDF5")
                for angle in range(0, 360, 45):
                    x1 = 128 + math.cos(math.radians(angle)) * 91
                    y1 = 99 + math.sin(math.radians(angle)) * 91
                    x2 = 128 + math.cos(math.radians(angle)) * 112
                    y2 = 99 + math.sin(math.radians(angle)) * 112
                    draw.line((x1, y1, x2, y2), fill=color, width=7)
            elif shape == "bubble":
                draw.rounded_rectangle((30, 46, 226, 182), radius=38, fill=color)
                draw.polygon([(78, 174), (67, 225), (120, 179)], fill=color)
                for x in (85, 128, 171):
                    draw.ellipse((x - 8, 106, x + 8, 122), fill="#FFFFFF")
            else:
                draw.ellipse((28, 28, 228, 228), fill=color)
                draw.polygon([(103, 77), (103, 179), (185, 128)], fill="#FFFFFF")
            image.save(path)
        sticker_paths.append(path)
    music_paths = []
    specs = [("清晨脉冲", 104, 220.0, "music"), ("城市漫步", 92, 196.0, "music"), ("轻盈科技", 120, 246.94, "music"), ("提示音", 120, 660.0, "sfx"), ("轻击", 120, 120.0, "sfx"), ("上升音效", 120, 330.0, "sfx")]
    for name, tempo, frequency, kind in specs:
        path = audio / f"{name}.wav"
        if not path.exists():
            sr = 44100
            duration = 8.0 if kind == "music" else 1.2
            t = np.arange(int(sr * duration), dtype=np.float64) / sr
            beat = 60.0 / tempo
            if kind == "music":
                progression = [1.0, 1.12246, 1.33484, 1.49831]
                chord = np.array([progression[int(x / (beat * 4)) % 4] for x in t])
                melody = np.sin(2 * np.pi * frequency * chord * t) * 0.16
                harmony = np.sin(2 * np.pi * frequency * 1.5 * chord * t) * 0.06
                bass = np.sin(2 * np.pi * frequency / 2 * chord * t) * 0.11
                kick = np.sin(2 * np.pi * (62 - 30 * np.mod(t, beat) / beat) * t) * np.exp(-np.mod(t, beat) * 18) * 0.16
                envelope = np.minimum(t * 4, 1) * np.minimum((duration - t) * 3, 1)
                samples = (melody + harmony + bass + kick) * envelope
            elif name == "上升音效":
                phase = 2 * np.pi * (frequency * t + 420 * t * t)
                samples = np.sin(phase) * np.linspace(0, 0.25, len(t)) * np.minimum((duration - t) * 8, 1)
            else:
                samples = np.sin(2 * np.pi * frequency * t) * np.exp(-t * (5 if name == "提示音" else 14)) * 0.35
                if name == "提示音":
                    samples += np.sin(2 * np.pi * frequency * 1.5 * t) * np.exp(-t * 7) * 0.15
            stereo = np.column_stack([samples, samples])
            with wave.open(str(path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(sr)
                output.writeframes((np.clip(stereo, -1, 1) * 32767).astype("<i2").tobytes())
        music_paths.append(path)
    return {"贴纸": sticker_paths, "音乐": music_paths[:3], "音效": music_paths[3:]}
