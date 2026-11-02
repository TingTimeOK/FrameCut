from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

import cv2
from PySide6.QtCore import QByteArray, QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QIcon, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __app_name__, __version__
from .assets import asset_directory
from .catalog import TEXT_TEMPLATES
from .dialogs import (
    AudioEventsDialog,
    AudioRecordDialog,
    CornerPinDialog,
    CoverDialog,
    ExportDialog,
    HSLDialog,
    HandwriteDialog,
    ProjectDialog,
    RegionDialog,
    SpeedCurveDialog,
)
from .export import ExportWorker
from .inspector import Inspector
from .library import ResourcePanel
from .media import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MediaCache,
    audio_envelope,
    decode_audio,
    format_time,
    imwrite,
    parse_lrc,
    parse_srt,
    probe_media,
)
from .model import Clip, Project, Track, new_id, text_style
from .preview import PreviewWidget
from .render import FrameRenderer
from .tasks import ASRWorker, AudioAnalysisWorker, PreviewAudioWorker, TrackingWorker
from .theme import APP_STYLE
from .timeline import TimelineWidget


class MainWindow(QMainWindow):
    def __init__(self, project: Project | None = None) -> None:
        super().__init__()
        self.project = project or Project()
        self.settings = QSettings("FrameCut", "FrameCut")
        self.media_items: list[dict] = []
        self.selected_id = ""
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.last_snapshot = self.project.to_dict()
        self.renderer = FrameRenderer(self.project, MediaCache(frame_capacity=16))
        self.playing = False
        self.play_requested = False
        self.last_tick = 0.0
        self.export_worker: ExportWorker | None = None
        self.task_worker = None
        self.task_progress: QProgressDialog | None = None
        self.preview_audio_worker: PreviewAudioWorker | None = None
        self.clipboard: Clip | None = None
        self.setWindowTitle(f"{self.project.name} — {__app_name__}")
        self.setMinimumSize(1180, 720)
        self.resize(1560, 940)
        self.setAcceptDrops(True)
        self.build_actions()
        self.build_ui()
        self.build_menus()
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)
        self.player.errorOccurred.connect(lambda error, text: self.statusBar().showMessage(f"音频预览：{text}", 5000))
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.playback_tick)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self.update_title()
        self.statusBar().showMessage("就绪 · 草稿、预览和导出使用同一渲染管线")

    def build_actions(self) -> None:
        self.actions = {}
        specs = [
            ("new", "新建草稿", "Ctrl+N", self.new_project),
            ("open", "打开草稿…", "Ctrl+O", self.open_project),
            ("save", "保存草稿", "Ctrl+S", self.save_project),
            ("save_as", "草稿另存为…", "Ctrl+Shift+S", self.save_project_as),
            ("import", "导入媒体…", "Ctrl+I", self.import_dialog),
            ("export", "导出视频…", "Ctrl+E", self.export_video),
            ("undo", "撤销", "Ctrl+Z", self.undo),
            ("redo", "重做", "Ctrl+Y", self.redo),
            ("split", "在播放头分割", "Ctrl+B", self.split_clip),
            ("delete", "删除片段", "Delete", self.delete_clip),
            ("duplicate", "复制片段", "Ctrl+D", self.duplicate_clip),
            ("copy", "复制到剪贴板", "Ctrl+C", self.copy_clip),
            ("paste", "粘贴片段", "Ctrl+V", self.paste_clip),
            ("text", "添加文本", "Ctrl+T", lambda: self.add_text("plain")),
            ("freeze", "定格", "", self.freeze_clip),
            ("reverse", "倒放", "", self.reverse_clip),
            ("marker", "添加标记", "M", self.add_marker),
            ("record", "录音 / 配音…", "", self.record_audio),
            ("subtitle", "智能字幕识别…", "", self.recognize_subtitles),
            ("srt", "导入 SRT 字幕…", "", self.import_srt),
            ("lrc", "导入 LRC 歌词…", "", self.import_lrc),
            ("handwrite", "手写 / 涂鸦…", "", self.add_handwrite),
            ("cover", "设置作品封面…", "", self.set_cover),
            ("project", "草稿设置…", "", self.project_settings),
            ("add_video_track", "添加视频轨", "", lambda: self.add_track("video")),
            ("add_audio_track", "添加音频轨", "", lambda: self.add_track("audio")),
            ("play", "播放 / 暂停", "Space", self.toggle_playback),
            ("previous", "上一帧", "Left", lambda: self.step_frame(-1)),
            ("next", "下一帧", "Right", lambda: self.step_frame(1)),
            ("start", "跳到开头", "Home", lambda: self.seek(0)),
            ("end", "跳到结尾", "End", lambda: self.seek(self.project.duration)),
            ("features", "功能与实现说明", "F1", self.show_features),
        ]
        for key, text, shortcut, callback in specs:
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.actions[key] = action
        self.actions["save"].setEnabled(True)
        self.snap_action = QCheckBox("时间吸附", self)
        self.snap_action.setChecked(True)
        self.snap_action.toggled.connect(lambda value: setattr(self.timeline, "snapping", value) if hasattr(self, "timeline") else None)
        self.safe_action = QCheckBox("安全框", self)
        self.safe_action.toggled.connect(self.toggle_safe_area)

    def build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for key in ["import", "undo", "redo", "split", "delete", "text", "record", "cover", "export"]:
            toolbar.addAction(self.actions[key])
            if key in {"import", "delete", "cover"}:
                toolbar.addSeparator()
        self.actions["export"].setText("导出")
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(main_splitter, 1)
        upper = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(upper)
        self.library = ResourcePanel()
        self.library.importRequested.connect(self.import_dialog)
        self.library.addMediaRequested.connect(self.import_files)
        self.library.applyRequested.connect(self.apply_resource)
        self.library.textRequested.connect(self.add_text)
        self.library.stickerRequested.connect(self.add_sticker)
        self.library.audioRequested.connect(self.add_audio_file)
        self.library.mediaSelected.connect(self.add_media)
        self.library.category.currentTextChanged.connect(lambda _: self.library.show_media(self.media_items))
        upper.addWidget(self.library)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(9, 9, 9, 7)
        self.preview = PreviewWidget()
        self.preview.filesDropped.connect(self.import_and_add)
        center_layout.addWidget(self.preview, 1)
        controls = QHBoxLayout()
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        self.time_label.setObjectName("timecode")
        controls.addWidget(self.time_label)
        controls.addStretch()
        for key in ["start", "previous", "play", "next", "end"]:
            button = QToolButton()
            button.setDefaultAction(self.actions[key])
            button.setObjectName("ghost")
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.safe_action)
        volume_icon = QLabel("预览音量")
        controls.addWidget(volume_icon)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(100)
        self.volume.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100) if hasattr(self, "audio_output") else None)
        controls.addWidget(self.volume)
        center_layout.addLayout(controls)
        upper.addWidget(center)
        self.inspector = Inspector(self.project)
        self.inspector.changed.connect(self.parameter_changed)
        self.inspector.actionRequested.connect(self.handle_action)
        self.inspector.playheadRequested.connect(lambda: self.statusBar().showMessage("请先把播放头移到片段范围内", 4000))
        upper.addWidget(self.inspector)
        upper.setSizes([260, 940, 340])
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        timeline_tools = QHBoxLayout()
        timeline_tools.setContentsMargins(8, 4, 8, 4)
        timeline_tools.addWidget(self.snap_action)
        add_track = QToolButton()
        add_track.setText("添加轨道")
        menu = QMenu(add_track)
        menu.addAction(self.actions["add_video_track"])
        menu.addAction(self.actions["add_audio_track"])
        add_track.setMenu(menu)
        add_track.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        timeline_tools.addWidget(add_track)
        timeline_tools.addStretch()
        timeline_tools.addWidget(QLabel("时间线缩放（Ctrl + 滚轮）"))
        zoom = QSlider(Qt.Orientation.Horizontal)
        zoom.setRange(12, 300)
        zoom.setValue(80)
        zoom.setMaximumWidth(180)
        zoom.valueChanged.connect(lambda value: self.timeline.set_zoom(value) if hasattr(self, "timeline") else None)
        timeline_tools.addWidget(zoom)
        bottom_layout.addLayout(timeline_tools)
        self.timeline = TimelineWidget(self.project)
        self.timeline.clipSelected.connect(self.select_clip)
        self.timeline.clipDoubleClicked.connect(self.clip_double_clicked)
        self.timeline.playheadChanged.connect(self.on_playhead)
        self.timeline.operationFinished.connect(self.checkpoint)
        self.timeline.changed.connect(self.timeline_changed)
        self.timeline.contextRequested.connect(self.clip_context_menu)
        scroll = QScrollArea()
        scroll.setWidget(self.timeline)
        scroll.setWidgetResizable(False)
        bottom_layout.addWidget(scroll, 1)
        main_splitter.addWidget(bottom)
        main_splitter.setSizes([630, 280])
        self.timeline_scroll = scroll

    def build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        for key in ["new", "open", "save", "save_as", "import", "export"]:
            file_menu.addAction(self.actions[key])
        edit = self.menuBar().addMenu("编辑")
        for key in ["undo", "redo", "split", "copy", "paste", "duplicate", "delete"]:
            edit.addAction(self.actions[key])
        clip_menu = self.menuBar().addMenu("片段")
        for key in ["text", "freeze", "reverse", "marker"]:
            clip_menu.addAction(self.actions[key])
        tools = self.menuBar().addMenu("工具")
        for key in ["subtitle", "srt", "lrc", "record", "handwrite", "cover"]:
            tools.addAction(self.actions[key])
        draft = self.menuBar().addMenu("草稿")
        draft.addAction(self.actions["project"])
        draft.addAction(self.actions["add_video_track"])
        draft.addAction(self.actions["add_audio_track"])
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self.actions["features"])

    def update_title(self) -> None:
        self.setWindowTitle(f"{'*' if self.project.dirty else ''}{self.project.name} — {__app_name__}")

    def timeline_changed(self) -> None:
        self.renderer.project = self.project
        self.update_preview()
        self.update_title()

    def parameter_changed(self) -> None:
        self.checkpoint()
        self.timeline.update_size()
        self.timeline.update()
        self.update_preview()
        self.update_title()

    def checkpoint(self) -> None:
        current = self.project.to_dict()
        if current != self.last_snapshot:
            self.undo_stack.append(self.last_snapshot)
            if len(self.undo_stack) > 80:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self.last_snapshot = current
        self.update_title()

    def restore_snapshot(self, data: dict) -> None:
        self.pause()
        self.project = Project.from_dict(data)
        self.renderer.project = self.project
        self.renderer.cache.frames.clear()
        self.timeline.set_project(self.project)
        self.inspector.set_project(self.project)
        self.last_snapshot = self.project.to_dict()
        self.update_preview()
        self.update_title()

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(self.project.to_dict())
            self.restore_snapshot(self.undo_stack.pop())

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(self.project.to_dict())
            self.restore_snapshot(self.redo_stack.pop())

    def select_clip(self, clip_id: str) -> None:
        self.selected_id = clip_id
        self.inspector.set_playhead(self.timeline.playhead)
        self.inspector.show_clip(self.project.clip(clip_id))

    def clip_double_clicked(self, clip_id: str) -> None:
        clip = self.project.clip(clip_id)
        if clip and clip.kind == "text":
            self.inspector.show_clip(clip)
            self.inspector.tabs.setCurrentIndex(0)
        elif clip:
            self.seek(clip.start)

    def on_playhead(self, seconds: float) -> None:
        self.inspector.set_playhead(seconds)
        self.time_label.setText(f"{format_time(seconds, True)} / {format_time(self.project.duration, True)}")
        self.update_preview()

    def update_preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        width = min(960, self.project.width)
        height = round(width * self.project.height / self.project.width)
        try:
            frame = self.renderer.render(self.timeline.playhead, width, height)
            self.preview.set_frame(frame)
        except Exception as error:
            self.statusBar().showMessage(f"预览错误：{error}", 5000)

    def seek(self, seconds: float) -> None:
        if self.playing:
            self.player.setPosition(round(seconds * 1000))
            self.last_tick = time.monotonic()
        self.timeline.set_playhead(seconds)

    def step_frame(self, direction: int) -> None:
        self.pause()
        self.seek(self.timeline.playhead + direction / self.project.fps)

    def toggle_playback(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        if self.project.duration <= 0 or self.playing or (self.preview_audio_worker and self.preview_audio_worker.isRunning()):
            return
        self.play_requested = True
        if self.timeline.playhead >= self.project.duration - 0.001:
            self.seek(0)
        self.statusBar().showMessage("正在准备时间线混音…")
        cache = asset_directory().parent / "cache"
        cache.mkdir(exist_ok=True)
        project_copy = copy.deepcopy(self.project)
        self.preview_audio_worker = PreviewAudioWorker(project_copy, str(cache / "preview_mix.wav"), self)
        self.preview_audio_worker.completed.connect(self.start_preview_audio)
        self.preview_audio_worker.failed.connect(lambda error: self.statusBar().showMessage(f"音频预览失败：{error}", 5000))
        self.preview_audio_worker.start()

    def start_preview_audio(self, path: str) -> None:
        if not self.play_requested:
            return
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setPosition(round(self.timeline.playhead * 1000))
        self.player.play()
        self.playing = True
        self.last_tick = time.monotonic()
        self.timer.start()
        self.statusBar().showMessage("正在播放", 1500)

    def playback_tick(self) -> None:
        now = time.monotonic()
        delta = min(0.1, now - self.last_tick)
        self.last_tick = now
        seconds = self.timeline.playhead + delta
        if seconds >= self.project.duration:
            self.timeline.set_playhead(self.project.duration)
            self.pause()
        else:
            self.timeline.set_playhead(seconds)

    def pause(self) -> None:
        if hasattr(self, "timer"):
            self.timer.stop()
        if hasattr(self, "player"):
            self.player.pause()
        self.playing = False
        self.play_requested = False

    def import_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "导入媒体", "", "媒体文件 (*.mp4 *.mov *.mkv *.avi *.webm *.mp3 *.wav *.m4a *.flac *.png *.jpg *.jpeg *.webp *.bmp);;全部文件 (*)")
        if paths:
            self.import_files(paths)

    def import_and_add(self, paths: list[str]) -> None:
        self.import_files(paths)
        for path in paths:
            try:
                self.add_media(path)
            except (ValueError, RuntimeError):
                pass

    def import_files(self, paths: list[str]) -> None:
        existing = {item["path"] for item in self.media_items}
        added = []
        for path in paths:
            resolved = str(Path(path).resolve())
            if resolved in existing:
                continue
            try:
                media = probe_media(path)
                self.prepare_media_metadata(media)
                self.media_items.append(media)
                added.append(media)
            except (ValueError, RuntimeError, OSError) as error:
                QMessageBox.warning(self, "无法导入", f"{Path(path).name}\n{error}")
        self.library.show_media(self.media_items)
        if added and not self.project.clips:
            self.add_media(added[0]["path"])
        self.statusBar().showMessage(f"已导入 {len(added)} 个文件；双击素材添加到时间线", 4000)

    def prepare_media_metadata(self, media: dict) -> None:
        cache = asset_directory().parent / "cache" / "thumbnails"
        cache.mkdir(parents=True, exist_ok=True)
        if media["kind"] == "video":
            frame = self.renderer.cache.frame(media["path"], min(media["duration"] * 0.15, 2.0))
            if frame is not None:
                path = str(cache / f"{new_id()}.jpg")
                imwrite(path, cv2.resize(frame, (160, 90)))
                media["thumbnail"] = path
        elif media["kind"] == "image":
            media["thumbnail"] = media["path"]

    def add_media(self, path: str, start: float | None = None) -> Clip:
        media = next((item for item in self.media_items if item["path"] == str(Path(path).resolve())), None) or probe_media(path)
        if not any(item["path"] == media["path"] for item in self.media_items):
            self.media_items.append(media)
            self.library.show_media(self.media_items)
        start = self.timeline.playhead if start is None else start
        kind = media["kind"]
        track = 3 if kind == "audio" else 1 if kind == "image" else 0
        if kind == "video" and any(c.track == 0 and c.start <= start < c.end for c in self.project.clips):
            track = 1
        if kind == "audio" and any(c.track == 3 and c.start <= start < c.end for c in self.project.clips):
            track = 4
        clip = Clip(path=media["path"], kind=kind, name=media["name"], track=track, start=start, duration=media["duration"], source_duration=media["duration"])
        if media.get("thumbnail"):
            clip.effects["thumbnails"] = [media["thumbnail"]]
        if kind in {"audio", "video"} and media.get("has_audio", kind == "audio"):
            try:
                samples = decode_audio(media["path"], 11025, 0, min(12.0, media["duration"]))
                envelope, _ = audio_envelope(samples, 11025)
                if len(envelope):
                    clip.effects["waveform"] = [float(envelope[min(len(envelope) - 1, int(i / 100 * len(envelope)))]) for i in range(100)]
            except RuntimeError:
                pass
        self.project.add_clip(clip)
        self.timeline.selected_id = clip.id
        self.timeline.update_size()
        self.timeline.update()
        self.select_clip(clip.id)
        self.checkpoint()
        self.update_preview()
        return clip

    def add_audio_file(self, path: str) -> None:
        self.add_media(path)

    def add_text(self, template: str = "plain") -> Clip:
        style = {**text_style(), **TEXT_TEMPLATES.get(template, {})}
        style.pop("name", None)
        clip = Clip(path="", kind="text", name="文本", track=2, start=self.timeline.playhead, duration=4.0, source_duration=4.0, text="双击编辑文本", style=style)
        self.project.add_clip(clip)
        self.timeline.selected_id = clip.id
        self.timeline.update_size()
        self.select_clip(clip.id)
        self.checkpoint()
        self.update_preview()
        return clip

    def add_sticker(self, path: str) -> None:
        clip = self.add_media(path, self.timeline.playhead)
        clip.kind = "sticker"
        clip.track = 2
        clip.duration = 4.0
        clip.source_duration = 4.0
        clip.effects["scale"] = 0.35
        clip.name = f"贴纸 · {Path(path).stem}"
        self.checkpoint()
        self.timeline.update()
        self.select_clip(clip.id)
        self.update_preview()

    def apply_resource(self, kind: str, key: str) -> None:
        clip = self.project.clip(self.selected_id)
        if not clip:
            clip = next((c for c in self.project.clips_at(self.timeline.playhead, visual_only=True) if c.kind != "text"), None)
        if not clip:
            self.statusBar().showMessage("请先选择要应用的视频片段", 4000)
            return
        if kind == "filter":
            clip.filter_name = key
        elif kind == "effect":
            clip.effects["effect"] = key
        elif kind == "transition":
            clip.effects["transition"] = {"kind": key, "duration": 0.6}
        self.select_clip(clip.id)
        self.checkpoint()
        self.update_preview()

    def split_clip(self) -> None:
        clip = self.project.clip(self.selected_id)
        if not clip or not clip.start < self.timeline.playhead < clip.end:
            clip = next((c for c in self.project.clips if c.start < self.timeline.playhead < c.end and not self.project.tracks[c.track].locked), None)
        if not clip:
            self.statusBar().showMessage("播放头必须位于可编辑片段内部", 3000)
            return
        _, right = clip.split(self.timeline.playhead)
        self.project.add_clip(right)
        self.selected_id = right.id
        self.timeline.selected_id = right.id
        self.timeline.update()
        self.select_clip(right.id)
        self.checkpoint()
        self.update_preview()

    def delete_clip(self) -> None:
        if self.selected_id:
            self.project.remove_clip(self.selected_id)
            self.selected_id = ""
            self.timeline.selected_id = ""
            self.inspector.show_clip(None)
            self.timeline.update_size()
            self.timeline.update()
            self.checkpoint()
            self.update_preview()

    def duplicate_clip(self) -> None:
        clip = self.project.clip(self.selected_id)
        if clip:
            duplicate = clip.duplicate()
            duplicate.start = clip.end
            self.project.add_clip(duplicate)
            self.selected_id = duplicate.id
            self.timeline.selected_id = duplicate.id
            self.timeline.update_size()
            self.select_clip(duplicate.id)
            self.checkpoint()

    def copy_clip(self) -> None:
        clip = self.project.clip(self.selected_id)
        if clip:
            self.clipboard = clip.duplicate()

    def paste_clip(self) -> None:
        if self.clipboard:
            clip = self.clipboard.duplicate()
            clip.start = self.timeline.playhead
            self.project.add_clip(clip)
            self.selected_id = clip.id
            self.timeline.selected_id = clip.id
            self.timeline.update_size()
            self.select_clip(clip.id)
            self.checkpoint()

    def reverse_clip(self) -> None:
        clip = self.project.clip(self.selected_id)
        if clip and clip.kind == "video":
            clip.reverse = not clip.reverse
            self.project.touch()
            self.checkpoint()
            self.inspector.refresh()
            self.update_preview()

    def freeze_clip(self) -> None:
        clip = self.project.clip(self.selected_id)
        if not clip or clip.kind != "video" or not clip.start < self.timeline.playhead < clip.end:
            self.statusBar().showMessage("请选择视频，并把播放头放在定格位置", 4000)
            return
        source_time = clip.source_time(self.timeline.playhead)
        frame = self.renderer.cache.frame(clip.path, source_time)
        if frame is None:
            return
        path = asset_directory().parent / "cache" / f"freeze_{new_id()}.png"
        imwrite(str(path), frame)
        _, right = clip.split(self.timeline.playhead)
        right.start += 3.0
        freeze = Clip(path=str(path), kind="image", name="定格", track=clip.track, start=self.timeline.playhead, duration=3.0, source_duration=3.0)
        self.project.add_clip(right)
        self.project.add_clip(freeze)
        self.timeline.update_size()
        self.checkpoint()
        self.update_preview()

    def add_marker(self) -> None:
        self.project.markers.append({"time": self.timeline.playhead, "label": "标记", "color": "#F4C95D"})
        self.project.touch()
        self.timeline.update()
        self.checkpoint()

    def add_track(self, kind: str) -> None:
        number = 1 + sum(t.kind == kind for t in self.project.tracks)
        self.project.tracks.append(Track(kind, f"{'视频' if kind == 'video' else '音频'} {number}"))
        self.project.touch()
        self.timeline.update_size()
        self.checkpoint()

    def clip_context_menu(self, clip_id: str, point) -> None:
        menu = QMenu(self)
        for key in ["split", "copy", "paste", "duplicate", "reverse", "freeze", "delete"]:
            menu.addAction(self.actions[key])
        menu.exec(point)

    def handle_action(self, action: str, payload: dict) -> None:
        clip = self.project.clip(self.selected_id)
        if not clip:
            return
        if action == "freeze":
            self.freeze_clip()
        elif action == "curve_speed":
            dialog = SpeedCurveDialog(clip, self)
            if dialog.exec():
                source_span = clip.duration * clip.speed
                points = dialog.points()
                average = sum(p["speed"] for p in points) / len(points)
                clip.effects["speed_points"] = points
                clip.speed = 1.0
                clip.duration = max(0.04, source_span / average)
                clip.source_duration = max(clip.source_duration, source_span)
                self.after_advanced_edit(clip)
        elif action == "corner_pin":
            dialog = CornerPinDialog(clip, self)
            if dialog.exec():
                self.after_advanced_edit(clip)
        elif action == "matting_region":
            frame = self.renderer.render(self.timeline.playhead, self.project.width, self.project.height)
            dialog = RegionDialog(frame, "选择智能抠像主体", self)
            if dialog.exec():
                clip.effects["matting"] = {"enabled": True, "rect": dialog.region()}
                self.after_advanced_edit(clip)
        elif action == "tracking":
            self.start_tracking(clip)
        elif action == "import_lut":
            path, _ = QFileDialog.getOpenFileName(self, "导入 3D LUT", "", "Cube LUT (*.cube)")
            if path:
                clip.effects["lut_path"] = path
                self.after_advanced_edit(clip)
        elif action == "color_match":
            path, _ = QFileDialog.getOpenFileName(self, "选择色彩参考", "", "图片或视频 (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.mkv)")
            if path:
                clip.effects["color_match"] = {"path": path, "strength": 0.85, "time": self.timeline.playhead}
                self.after_advanced_edit(clip)
        elif action == "hsl_colors":
            dialog = HSLDialog(clip, self)
            if dialog.exec():
                self.after_advanced_edit(clip)
        elif action in {"beats", "audio_events", "align_audio"}:
            other = ""
            if action == "align_audio":
                other, _ = QFileDialog.getOpenFileName(self, "选择对齐参考音频", "", "音频或视频 (*.wav *.mp3 *.m4a *.flac *.mp4 *.mov)")
                if not other:
                    return
            self.start_audio_analysis(clip, "events" if action == "audio_events" else "beats" if action == "beats" else "align", other)

    def after_advanced_edit(self, clip: Clip) -> None:
        self.project.touch()
        self.renderer.matting_masks.clear()
        self.timeline.update_size()
        self.timeline.update()
        self.inspector.refresh()
        self.checkpoint()
        self.update_preview()

    def start_tracking(self, clip: Clip) -> None:
        if clip.kind != "video":
            QMessageBox.information(self, "运动跟踪", "请选择视频片段。")
            return
        frame = self.renderer.cache.frame(clip.path, clip.source_time(self.timeline.playhead))
        if frame is None:
            return
        dialog = RegionDialog(frame, "框选要跟踪的物体", self)
        if not dialog.exec():
            return
        worker = TrackingWorker(copy.deepcopy(clip), self.project.fps, dialog.region(), self)
        worker.clip_id = clip.id
        self.task_worker = worker
        self.task_progress = QProgressDialog("正在跟踪物体…", "取消", 0, 100, self)
        self.task_progress.setWindowTitle("运动跟踪")
        self.task_progress.canceled.connect(worker.terminate)
        worker.progressed.connect(self.task_progress.setValue)
        worker.completed.connect(self.tracking_completed)
        worker.failed.connect(self.task_failed)
        worker.start()

    def tracking_completed(self, keyframes: dict) -> None:
        clip = self.project.clip(self.task_worker.clip_id)
        if clip:
            clip.keyframes.update(keyframes)
            clip.effects["tracking_enabled"] = True
            self.after_advanced_edit(clip)
        self.close_task()
        self.statusBar().showMessage("跟踪完成：结果已转换为可编辑位置关键帧，可让文本或贴纸跟随", 6000)

    def start_audio_analysis(self, clip: Clip, mode: str, other: str = "") -> None:
        worker = AudioAnalysisWorker(copy.deepcopy(clip), mode, self.project.sample_rate, other, self)
        worker.clip_id = clip.id
        self.task_worker = worker
        self.task_progress = QProgressDialog("正在分析音频…", "", 0, 0, self)
        self.task_progress.setCancelButton(None)
        self.task_progress.setWindowTitle("音频分析")
        worker.completed.connect(self.audio_analysis_completed)
        worker.failed.connect(self.task_failed)
        worker.start()

    def audio_analysis_completed(self, mode: str, result: object) -> None:
        worker = self.task_worker
        clip = self.project.clip(worker.clip_id)
        if clip and mode == "beats":
            clip.effects["beats"] = result
            for beat in result:
                self.project.markers.append({"time": clip.start + beat, "label": "踩点", "color": "#FFE278", "clip_id": clip.id})
            self.after_advanced_edit(clip)
            self.statusBar().showMessage(f"已添加 {len(result)} 个踩点", 4000)
        elif clip and mode == "align":
            old = clip.start
            clip.start = max(0.0, clip.start + float(result))
            self.after_advanced_edit(clip)
            self.statusBar().showMessage(f"音频对齐完成：偏移 {float(result):+.3f}s，片段已移动", 5000)
        elif mode == "events":
            dialog = AudioEventsDialog(result, self)
            dialog.exec()
            if clip:
                for event in result["events"]:
                    if event["type"] == "loud":
                        self.project.markers.append({"time": clip.start + event["start"], "label": "音频事件", "color": "#FF9B7B"})
                self.after_advanced_edit(clip)
        self.close_task()

    def task_failed(self, message: str) -> None:
        self.close_task()
        QMessageBox.warning(self, "后台任务", message)

    def close_task(self) -> None:
        if self.task_progress:
            self.task_progress.close()
            self.task_progress.deleteLater()
            self.task_progress = None
        self.task_worker = None

    def recognize_subtitles(self) -> None:
        clip = self.project.clip(self.selected_id)
        path = clip.path if clip and clip.audible else ""
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "选择要识别的音频或视频", "", "媒体文件 (*.mp4 *.mov *.mkv *.mp3 *.wav *.m4a *.flac)")
        if not path:
            return
        model, ok = QInputDialog.getItem(self, "智能字幕", "识别模型（越大越准确、越慢）", ["tiny", "base", "small", "medium"], 2, False)
        if not ok:
            return
        language, ok = QInputDialog.getItem(self, "智能字幕", "语言", ["auto", "zh", "en", "ja", "ko"], 0, False)
        if not ok:
            return
        selected = clip if clip and clip.audible else None
        speed = selected.speed if selected else 1.0
        if selected and selected.effects.get("speed_points"):
            points = selected.effects["speed_points"]
            speed = sum(point["speed"] for point in points) / len(points)
        source_length = min(selected.duration * speed, max(0.0, selected.source_duration - selected.source_offset)) if selected else 0.0
        worker = ASRWorker(path, model, language, self, start=selected.source_offset if selected else 0.0, duration=source_length, speed=speed)
        worker.source_clip_id = selected.id if selected else ""
        worker.base_time = selected.start if selected else self.timeline.playhead
        self.task_worker = worker
        self.task_progress = QProgressDialog("正在加载识别模型…", "", 0, 0, self)
        self.task_progress.setCancelButton(None)
        self.task_progress.setWindowTitle("智能字幕")
        worker.progressed.connect(self.task_progress.setLabelText)
        worker.completed.connect(self.subtitles_completed)
        worker.failed.connect(self.task_failed)
        worker.start()

    def subtitles_completed(self, segments: list[dict]) -> None:
        worker = self.task_worker
        base = worker.base_time
        for segment in segments:
            start = max(0.0, base + segment["start"])
            style = {**text_style(), **TEXT_TEMPLATES["caption"]}
            style.pop("name", None)
            clip = Clip(path="", kind="text", name="字幕", track=2, start=start, duration=max(0.3, segment["end"] - segment["start"]), source_duration=segment["end"] - segment["start"], text=segment["text"], style=style)
            clip.effects["words"] = segment.get("words", [])
            clip.effects["recognize_task"] = True
            self.project.add_clip(clip)
        self.project.touch()
        self.timeline.update_size()
        self.checkpoint()
        self.close_task()
        self.statusBar().showMessage(f"识别完成：已生成 {len(segments)} 条带时间码字幕", 5000)

    def import_srt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入字幕", "", "SRT 字幕 (*.srt)")
        if path:
            try:
                segments = parse_srt(Path(path).read_text(encoding="utf-8-sig"))
                self.add_timed_text(segments, "字幕")
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "字幕", str(error))

    def import_lrc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入歌词", "", "LRC 歌词 (*.lrc);;文本 (*.txt)")
        if path:
            segments = parse_lrc(Path(path).read_text(encoding="utf-8-sig"))
            self.add_timed_text(segments, "歌词")
            self.statusBar().showMessage(f"已导入 {len(segments)} 句歌词，可在文本面板设置歌词动画", 5000)

    def add_timed_text(self, segments: list[dict], kind: str) -> None:
        base = self.timeline.playhead
        for segment in segments:
            style = {**text_style(), **TEXT_TEMPLATES["caption"]}
            style.pop("name", None)
            if kind == "歌词":
                style.update(color="#74F7E1", keyword_color="#FFE45E", animation_loop="pulse")
            clip = Clip(path="", kind="text", name=kind, track=2, start=base + segment["start"], duration=max(0.3, segment["end"] - segment["start"]), source_duration=segment["end"] - segment["start"], text=segment["text"], style=style)
            self.project.add_clip(clip)
        self.timeline.update_size()
        self.checkpoint()

    def record_audio(self) -> None:
        dialog = AudioRecordDialog(self)
        dialog.recorded.connect(self.add_recorded_audio)
        dialog.exec()

    def add_recorded_audio(self, path: str) -> None:
        self.import_files([path])
        self.add_media(path, self.timeline.playhead)

    def add_handwrite(self) -> None:
        dialog = HandwriteDialog(self)
        if dialog.exec() and dialog.path:
            clip = self.add_media(dialog.path)
            clip.kind = "sticker"
            clip.track = 2
            clip.name = "手写笔迹"
            clip.duration = 5.0
            clip.effects["handwrite_reveal"] = 2.0
            self.after_advanced_edit(clip)

    def set_cover(self) -> None:
        if self.project.duration <= 0:
            QMessageBox.information(self, "封面", "请先添加画面。")
            return
        frame = self.renderer.render(self.timeline.playhead, self.project.width, self.project.height)
        dialog = CoverDialog(self.project, frame, self)
        if dialog.exec() and dialog.cover_path:
            self.project.cover = {"path": dialog.cover_path, "time": self.timeline.playhead, "title": dialog.title.text(), "template": dialog.template.currentText()}
            self.project.touch()
            self.checkpoint()
            self.statusBar().showMessage(f"封面已保存并关联草稿：{dialog.cover_path}", 5000)

    def export_video(self) -> None:
        if self.export_worker:
            QMessageBox.information(self, "导出", "已有导出任务正在进行。")
            return
        missing = [c.name for c in self.project.clips if c.kind not in {"text", "color"} and (not c.path or not Path(c.path).exists())]
        if missing:
            QMessageBox.warning(self, "无法导出", "以下素材缺失，请重新链接：\n" + "\n".join(missing))
            return
        dialog = ExportDialog(self.project, self)
        if not dialog.exec():
            return
        self.pause()
        if self.project.dirty:
            self.autosave()
        output = dialog.path.text()
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        worker = ExportWorker(copy.deepcopy(self.project), output, dialog.settings(), self)
        self.export_worker = worker
        progress = QProgressDialog("正在准备导出…", "取消", 0, 100, self)
        progress.setWindowTitle("导出视频")
        progress.setMinimumDuration(0)
        progress.setValue(0)
        worker.progressed.connect(lambda value, text: (progress.setValue(value), progress.setLabelText(text)))
        worker.completed.connect(lambda path: self.export_finished(progress, path))
        worker.failed.connect(lambda error: self.export_failed(progress, error))
        progress.canceled.connect(worker.cancel)
        worker.start()

    def export_finished(self, progress: QProgressDialog, path: str) -> None:
        progress.close()
        self.export_worker = None
        answer = QMessageBox.question(self, "导出完成", f"作品已导出到：\n{path}\n\n是否打开所在文件夹？")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def export_failed(self, progress: QProgressDialog, error: str) -> None:
        progress.close()
        self.export_worker = None
        QMessageBox.warning(self, "导出失败", error)

    def project_settings(self) -> None:
        dialog = ProjectDialog(self.project, self)
        if dialog.exec():
            dialog.apply(self.project)
            self.renderer.matting_masks.clear()
            self.checkpoint()
            self.update_preview()

    def new_project(self) -> None:
        if not self.maybe_save():
            return
        self.pause()
        self.project = Project()
        self.media_items = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.last_snapshot = self.project.to_dict()
        self.renderer.project = self.project
        self.renderer.cache.frames.clear()
        self.timeline.set_project(self.project)
        self.inspector.set_project(self.project)
        self.library.show_media([])
        self.update_title()
        self.update_preview()

    def open_project(self) -> None:
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开草稿", "", "FrameCut 草稿 (*.fcut.json *.fcut);;全部文件 (*)")
        if path:
            self.load_project(path)

    def load_project(self, path: str) -> None:
        try:
            project = Project.load(path)
            self.pause()
            self.project = project
            self.media_items = []
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.last_snapshot = project.to_dict()
            self.renderer.project = project
            self.renderer.cache.frames.clear()
            self.timeline.set_project(project)
            self.inspector.set_project(project)
            known = {c.path for c in project.clips}
            for media_path in known:
                if media_path and Path(media_path).exists():
                    try:
                        self.media_items.append(probe_media(media_path))
                    except (ValueError, RuntimeError):
                        pass
            self.library.show_media(self.media_items)
            self.update_title()
            self.update_preview()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "打开草稿", str(error))

    def save_project(self) -> bool:
        if not self.project.path:
            return self.save_project_as()
        try:
            self.project.save()
            self.last_snapshot = self.project.to_dict()
            self.update_title()
            self.statusBar().showMessage(f"草稿已保存：{self.project.path}", 3000)
            return True
        except OSError as error:
            QMessageBox.warning(self, "保存草稿", str(error))
            return False

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "保存草稿", str(Path.home() / "Documents" / f"{self.project.name}.fcut.json"), "FrameCut 草稿 (*.fcut.json)")
        if not path:
            return False
        self.project.path = path
        return self.save_project()

    def autosave(self) -> None:
        if not self.project.dirty:
            return
        try:
            if self.project.path:
                target = Path(self.project.path)
                target = target.with_name(target.stem + ".autosave" + target.suffix)
                data = self.project.to_dict()
                target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                target = asset_directory().parent / "autosave" / "未命名工程.fcut.json"
                target.parent.mkdir(exist_ok=True)
                target.write_text(json.dumps(self.project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.statusBar().showMessage(f"已自动备份：{target}", 2500)
        except OSError as error:
            self.statusBar().showMessage(f"自动备份失败：{error}", 5000)

    def maybe_save(self) -> bool:
        if not self.project.dirty:
            return True
        answer = QMessageBox.question(self, "未保存的草稿", "当前草稿有未保存的修改，是否保存？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return True

    def toggle_safe_area(self, checked: bool) -> None:
        self.preview.safe_area = checked
        self.preview.update()

    def show_features(self) -> None:
        text = (
            "FrameCut 帧界 1.0\n\n独立实现的 Windows 视频剪辑应用。\n\n"
            "已纳入所选 43 项功能：草稿与多轨剪辑、分割/裁剪、常规与曲线变速、倒放/定格、变换和关键帧；"
            "基础调节、HSL/曲线/色轮、滤镜/LUT、色彩匹配、混合、画质增强、补帧、防抖、打光与 HDR；"
            "智能抠像、色度抠图、蒙版和运动跟踪；文本、花字、ASR/导入字幕、字幕动画、LRC 歌词、贴纸和手写；"
            "特效、转场、入出场/循环动画、运动模糊和本地素材商城；"
            "音量/淡入淡出/声像/声道、变声、降噪/响度、中置人声分离、录音、原创音乐、踩点、VAD/事件检测与音频对齐；"
            "H.264/H.265/AV1 导出和封面。\n\n"
            "算法说明：智能抠像采用本地 GrabCut，跟踪采用模板匹配，人声分离采用立体声中置通道，"
            "并非剪映的云端或专有 AI 模型。ASR 由 faster-whisper 提供，模型首次使用需要联网下载。\n\n"
            "本应用不加载或修改 JianyingPro 的程序、素材和草稿。"
        )
        QMessageBox.information(self, f"关于 {__app_name__}", text)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.maybe_save():
            event.ignore()
            return
        self.pause()
        self.autosave()
        self.settings.setValue("geometry", self.saveGeometry())
        if self.export_worker:
            self.export_worker.cancel()
            self.export_worker.wait(3000)
        if self.task_worker:
            self.task_worker.wait(2000)
        if self.preview_audio_worker:
            self.preview_audio_worker.wait(2000)
        self.renderer.cache.close()
        event.accept()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        media = [p for p in paths if Path(p).suffix.lower() in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS]
        drafts = [p for p in paths if p.endswith(".fcut.json") or p.endswith(".fcut")]
        if drafts:
            self.load_project(drafts[0])
        elif media:
            self.import_and_add(media)


def main() -> int:
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication(sys.argv)
    app.setApplicationName("FrameCut")
    app.setOrganizationName("FrameCut")
    app.setStyle("Fusion")
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc") if Path("C:/Windows/Fonts/msyh.ttc").exists() else -1
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    app.setFont(QFont("Microsoft YaHei UI" if "Microsoft YaHei UI" in families else "Microsoft YaHei", 9))
    app.setStyleSheet(APP_STYLE)
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "resources" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        window.load_project(sys.argv[1])
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
