from __future__ import annotations

import copy
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex


def default_adjust() -> dict[str, float]:
    return {
        "brightness": 0.0,
        "contrast": 0.0,
        "saturation": 0.0,
        "temperature": 0.0,
        "tint": 0.0,
        "exposure": 0.0,
        "highlights": 0.0,
        "shadows": 0.0,
        "sharpen": 0.0,
        "vignette": 0.0,
        "grain": 0.0,
    }


@dataclass
class Track:
    kind: str
    name: str
    muted: bool = False
    locked: bool = False
    visible: bool = True
    id: str = field(default_factory=new_id)


@dataclass
class Clip:
    path: str
    kind: str
    name: str
    track: int
    start: float
    duration: float
    source_duration: float = 0.0
    source_offset: float = 0.0
    speed: float = 1.0
    reverse: bool = False
    freeze_at: float | None = None
    opacity: float = 1.0
    volume: float = 1.0
    muted: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.0
    pan: float = 0.0
    channel_mode: str = "stereo"
    pitch: float = 0.0
    voice_effect: str = "none"
    denoise: float = 0.0
    loudness: bool = False
    vocal_mode: str = "none"
    blend_mode: str = "normal"
    filter_name: str = "none"
    filter_strength: float = 1.0
    text: str = ""
    style: dict[str, Any] = field(default_factory=dict)
    adjust: dict[str, float] = field(default_factory=default_adjust)
    hsl: dict[str, float] = field(default_factory=lambda: {"hue": 0.0, "saturation": 0.0, "lightness": 0.0})
    curves: list[list[float]] = field(default_factory=lambda: [[0.0, 0.0], [0.25, 0.25], [0.5, 0.5], [0.75, 0.75], [1.0, 1.0]])
    wheels: dict[str, list[float]] = field(default_factory=lambda: {"shadows": [0.0, 0.0, 0.0], "midtones": [0.0, 0.0, 0.0], "highlights": [0.0, 0.0, 0.0]})
    effects: dict[str, Any] = field(default_factory=dict)
    keyframes: dict[str, list[dict[str, float]]] = field(default_factory=dict)
    id: str = field(default_factory=new_id)

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def visual(self) -> bool:
        return self.kind in {"video", "image", "text", "sticker", "color"}

    @property
    def audible(self) -> bool:
        return self.kind in {"video", "audio"}

    def source_time(self, timeline_seconds: float) -> float:
        local = min(max(timeline_seconds - self.start, 0.0), self.duration)
        if self.freeze_at is not None:
            return self.freeze_at
        points = self.effects.get("speed_points") or []
        if points:
            elapsed = self._curve_source_time(local, points)
        else:
            elapsed = local * self.speed
        if self.reverse:
            base = self.source_duration or self.duration * self.speed
            return max(0.0, self.source_offset + base - elapsed - 1.0 / 60.0)
        return self.source_offset + elapsed

    def _curve_source_time(self, local: float, points: list[dict[str, float]]) -> float:
        ordered = sorted(points, key=lambda p: p["time"])
        if len(ordered) < 2:
            return local * self.speed

        def rate(at: float) -> float:
            x = min(max(at / max(self.duration, 0.001), 0.0), 1.0)
            for left, right in zip(ordered, ordered[1:]):
                if left["time"] <= x <= right["time"]:
                    span = max(right["time"] - left["time"], 1e-6)
                    mix = (x - left["time"]) / span
                    return max(0.05, left["speed"] * (1.0 - mix) + right["speed"] * mix)
            return max(0.05, ordered[-1]["speed"])

        steps = max(1, min(240, int(local * 20) + 1))
        step = local / steps
        return sum((rate(i * step) + rate((i + 1) * step)) * 0.5 * step for i in range(steps))

    def value_at(self, prop: str, local_time: float, default: float) -> float:
        frames = self.keyframes.get(prop) or []
        if not frames:
            return float(self.effects.get(prop, default))
        ordered = sorted(frames, key=lambda f: f["time"])
        if local_time <= ordered[0]["time"]:
            return ordered[0]["value"]
        for left, right in zip(ordered, ordered[1:]):
            if left["time"] <= local_time <= right["time"]:
                span = max(right["time"] - left["time"], 1e-6)
                t = (local_time - left["time"]) / span
                easing = right.get("easing", "linear")
                if easing == "ease_in_out":
                    t = t * t * (3.0 - 2.0 * t)
                elif easing == "ease_out":
                    t = 1.0 - (1.0 - t) ** 2
                return left["value"] * (1.0 - t) + right["value"] * t
        return ordered[-1]["value"]

    def split(self, timeline_seconds: float) -> tuple["Clip", "Clip"]:
        offset = timeline_seconds - self.start
        if not 0.001 < offset < self.duration - 0.001:
            raise ValueError("分割位置必须位于片段内部")
        right = copy.deepcopy(self)
        right.id = new_id()
        source_delta = self.source_time(timeline_seconds) - self.source_time(self.start)
        if self.reverse:
            right.source_offset = self.source_offset
            right.source_duration = max(0.04, self.source_duration - abs(source_delta))
        else:
            right.source_offset += abs(source_delta)
        right.start = timeline_seconds
        right.duration = self.duration - offset
        self.duration = offset
        for prop in list(self.keyframes):
            original = copy.deepcopy(self.keyframes[prop])
            self.keyframes[prop] = [f for f in original if f["time"] <= offset]
            for frame in original:
                if frame["time"] > offset:
                    frame["time"] -= offset
            right.keyframes[prop] = [f for f in original if f["time"] > offset]
        return self, right

    def duplicate(self) -> "Clip":
        result = copy.deepcopy(self)
        result.id = new_id()
        return result


@dataclass
class Project:
    name: str = "未命名工程"
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    sample_rate: int = 48000
    background: str = "#101014"
    path: str = ""
    dirty: bool = False
    tracks: list[Track] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    markers: list[dict[str, Any]] = field(default_factory=list)
    cover: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    modified_at: float = field(default_factory=time.time)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.tracks:
            self.tracks = default_tracks()

    @property
    def duration(self) -> float:
        return max((clip.end for clip in self.clips), default=0.0)

    def clip(self, clip_id: str | None) -> Clip | None:
        return next((c for c in self.clips if c.id == clip_id), None)

    def clips_at(self, seconds: float, visual_only: bool = False) -> list[Clip]:
        return [c for c in self.clips if c.start <= seconds < c.end and (not visual_only or c.visual)]

    def ensure_track(self, index: int, kind: str) -> None:
        while len(self.tracks) <= index:
            number = 1 + sum(t.kind == kind for t in self.tracks)
            label = {"video": "视频", "audio": "音频", "text": "文本"}.get(kind, "轨道")
            self.tracks.append(Track(kind=kind, name=f"{label} {number}"))

    def add_clip(self, clip: Clip) -> Clip:
        self.ensure_track(clip.track, "audio" if clip.kind == "audio" else "video")
        self.clips.append(clip)
        self.touch()
        return clip

    def remove_clip(self, clip_id: str) -> None:
        self.clips = [c for c in self.clips if c.id != clip_id]
        self.touch()

    def touch(self) -> None:
        self.dirty = True
        self.modified_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("dirty", None)
        data["app"] = "FrameCut"
        return data

    def save(self, path: str | Path | None = None) -> str:
        target = Path(path or self.path)
        if not target:
            raise ValueError("尚未指定草稿路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        self.path = str(target.resolve())
        self.dirty = False
        return self.path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        tracks = [Track(**{k: v for k, v in t.items() if k in Track.__dataclass_fields__}) for t in data.get("tracks", [])]
        clips = []
        for raw in data.get("clips", []):
            values = {k: v for k, v in raw.items() if k in Clip.__dataclass_fields__}
            clip = Clip(**values)
            clip.adjust = {**default_adjust(), **clip.adjust}
            clips.append(clip)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k not in {"tracks", "clips", "dirty"}}
        project = cls(**known, tracks=tracks, clips=clips)
        project.dirty = False
        return project

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("app") != "FrameCut":
            raise ValueError("不是 FrameCut 草稿文件")
        if int(data.get("schema_version", 0)) > SCHEMA_VERSION:
            raise ValueError("草稿由更新版本创建，请升级应用")
        project = cls.from_dict(data)
        project.path = str(Path(path).resolve())
        return project


def default_tracks() -> list[Track]:
    return [
        Track("video", "主视频"),
        Track("video", "画中画"),
        Track("text", "文本 / 贴纸"),
        Track("audio", "音频 1"),
        Track("audio", "音频 2"),
    ]


def text_style() -> dict[str, Any]:
    return {
        "font": "Microsoft YaHei",
        "size": 54,
        "color": "#FFFFFF",
        "bold": False,
        "italic": False,
        "align": "center",
        "x": 0.5,
        "y": 0.82,
        "scale": 1.0,
        "rotation": 0.0,
        "opacity": 1.0,
        "stroke_color": "#000000",
        "stroke_width": 2,
        "shadow": True,
        "shadow_color": "#00000099",
        "background": "#00000000",
        "line_spacing": 1.2,
        "template": "plain",
        "keywords": [],
        "keyword_color": "#FFE45E",
        "animation_in": "none",
        "animation_out": "none",
        "animation_loop": "none",
        "animation_duration": 0.6,
    }
