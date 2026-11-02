from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from .audio import mix_to_file
from .media import decode_audio, detect_audio_events, detect_beats, executable, run_command
from .model import Clip, Project


class ASRWorker(QThread):
    progressed = Signal(str)
    completed = Signal(list)
    failed = Signal(str)

    def __init__(self, path: str, model: str = "small", language: str = "auto", parent=None, start: float = 0.0, duration: float = 0.0, speed: float = 1.0) -> None:
        super().__init__(parent)
        self.path = path
        self.model = model
        self.language = language
        self.start = start
        self.duration = duration
        self.speed = speed

    def run(self) -> None:
        try:
            self.progressed.emit(f"正在加载 {self.model} 识别模型（首次使用需下载）…")
            from faster_whisper import WhisperModel
            model = WhisperModel(self.model, device="cpu", compute_type="int8")
            self.progressed.emit("正在识别语音并生成时间码…")
            source = self.path
            if self.duration > 0:
                audio = decode_audio(self.path, 16000, self.start, self.duration)
                source = audio.mean(axis=1)
            segments, info = model.transcribe(source, language=None if self.language == "auto" else self.language, word_timestamps=True, vad_filter=True)
            result = []
            for segment in segments:
                text = segment.text.strip()
                if text:
                    result.append({"start": float(segment.start) / self.speed, "end": float(segment.end) / self.speed, "text": text, "words": [{"start": float(w.start) / self.speed, "end": float(w.end) / self.speed, "word": w.word} for w in (segment.words or [])]})
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(str(error))


class TrackingWorker(QThread):
    progressed = Signal(int)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, clip: Clip, fps: float, region: list[float], parent=None) -> None:
        super().__init__(parent)
        self.clip = clip
        self.fps = fps
        self.region = region

    def run(self) -> None:
        capture = cv2.VideoCapture(self.clip.path)
        try:
            if not capture.isOpened():
                raise RuntimeError("无法打开跟踪视频")
            start_source = self.clip.source_time(self.clip.start)
            capture.set(cv2.CAP_PROP_POS_MSEC, start_source * 1000)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("无法读取跟踪起始帧")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            height, width = gray.shape
            x, y, w, h = self.region
            left, top = int(x * width), int(y * height)
            box_w, box_h = max(8, int(w * width)), max(8, int(h * height))
            template = gray[top:top + box_h, left:left + box_w].copy()
            if template.size == 0:
                raise RuntimeError("跟踪区域无效")
            initial_center = np.array([left + box_w / 2, top + box_h / 2], dtype=np.float64)
            previous = initial_center.copy()
            x_frames = []
            y_frames = []
            sample_count = max(2, int(self.clip.duration * min(self.fps, 15)))
            for index in range(sample_count):
                local = index / min(self.fps, 15)
                source_time = self.clip.source_time(self.clip.start + local)
                capture.set(cv2.CAP_PROP_POS_MSEC, source_time * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                margin_x, margin_y = box_w * 2, box_h * 2
                sx = max(0, int(previous[0] - box_w / 2 - margin_x))
                sy = max(0, int(previous[1] - box_h / 2 - margin_y))
                ex = min(width, int(previous[0] + box_w / 2 + margin_x))
                ey = min(height, int(previous[1] + box_h / 2 + margin_y))
                search = gray[sy:ey, sx:ex]
                if search.shape[0] <= box_h or search.shape[1] <= box_w:
                    continue
                result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
                _, confidence, _, location = cv2.minMaxLoc(result)
                if confidence >= 0.32:
                    center = np.array([sx + location[0] + box_w / 2, sy + location[1] + box_h / 2])
                    previous = 0.65 * center + 0.35 * previous
                delta = (previous - initial_center) / np.array([width, height])
                x_frames.append({"time": local, "value": float(delta[0]), "easing": "linear"})
                y_frames.append({"time": local, "value": float(delta[1]), "easing": "linear"})
                self.progressed.emit(int((index + 1) / sample_count * 100))
            if len(x_frames) < 2:
                raise RuntimeError("有效跟踪帧不足，请选择对比度更明显的区域")
            self.completed.emit({"track_x": x_frames, "track_y": y_frames})
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            capture.release()


class AudioAnalysisWorker(QThread):
    progressed = Signal(str)
    completed = Signal(str, object)
    failed = Signal(str)

    def __init__(self, clip: Clip, mode: str, sample_rate: int = 22050, other_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self.clip = clip
        self.mode = mode
        self.sample_rate = sample_rate
        self.other_path = other_path

    def run(self) -> None:
        try:
            self.progressed.emit("正在解码与分析音频…")
            samples = decode_audio(self.clip.path, self.sample_rate, self.clip.source_offset, self.clip.source_duration or self.clip.duration)
            if self.mode == "beats":
                self.completed.emit(self.mode, detect_beats(samples, self.sample_rate))
            elif self.mode == "events":
                events = detect_audio_events(samples, self.sample_rate)
                mono = samples.mean(axis=1)
                rms = float(np.sqrt(np.mean(mono * mono) + 1e-12))
                peak = float(np.max(np.abs(samples))) if samples.size else 0.0
                self.completed.emit(self.mode, {"events": events, "rms_db": 20 * np.log10(max(rms, 1e-8)), "peak_db": 20 * np.log10(max(peak, 1e-8))})
            elif self.mode == "align":
                from .media import alignment_offset
                reference = decode_audio(self.other_path, self.sample_rate)
                self.completed.emit(self.mode, alignment_offset(reference, samples, self.sample_rate))
        except Exception as error:
            self.failed.emit(str(error))


class PreviewAudioWorker(QThread):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, path: str, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.path = path

    def run(self) -> None:
        try:
            mix_to_file(self.project, self.path)
            self.completed.emit(self.path)
        except Exception as error:
            self.failed.emit(str(error))
