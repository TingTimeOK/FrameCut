from __future__ import annotations

import copy
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .catalog import TEXT_TEMPLATES
from .export import ENCODERS
from .media import imwrite, write_wav
from .model import Clip, Project, text_style
from .render import FrameRenderer, pil_color


class ExportDialog(QDialog):
    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("导出视频")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.name = QLineEdit(project.name)
        form.addRow("作品名称", self.name)
        path_row = QHBoxLayout()
        self.path = QLineEdit(str(Path.home() / "Videos" / f"{project.name}.mp4"))
        browse = QPushButton("浏览…")
        browse.clicked.connect(self.choose_path)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        form.addRow("保存到", path_row)
        self.resolution = QComboBox()
        self.resolutions = {
            "草稿原始尺寸": (project.width, project.height),
            "4K UHD · 3840×2160": (3840, 2160),
            "1080P · 1920×1080": (1920, 1080),
            "720P · 1280×720": (1280, 720),
            "竖屏 1080P · 1080×1920": (1080, 1920),
            "竖屏 720P · 720×1280": (720, 1280),
        }
        for label in self.resolutions:
            self.resolution.addItem(label)
        form.addRow("分辨率", self.resolution)
        self.fps = QComboBox()
        for value in [24, 25, 30, 50, 60]:
            self.fps.addItem(str(value), value)
        self.fps.setCurrentText(str(round(project.fps)))
        form.addRow("帧率", self.fps)
        self.encoder = QComboBox()
        for label, value in ENCODERS.items():
            self.encoder.addItem(label, value)
        form.addRow("视频编码", self.encoder)
        self.crf = QSpinBox()
        self.crf.setRange(0, 51)
        self.crf.setValue(20)
        self.crf.setToolTip("数值越小画质越高、文件越大。H.264 推荐 18–23。")
        form.addRow("画质 CRF", self.crf)
        self.preset = QComboBox()
        self.preset.addItems(["veryfast", "fast", "medium", "slow"])
        self.preset.setCurrentText("medium")
        form.addRow("编码速度", self.preset)
        self.audio = QComboBox()
        self.audio.addItems(["128k", "192k", "256k", "320k"])
        self.audio.setCurrentText("192k")
        form.addRow("音频码率", self.audio)
        self.summary = QLabel(f"时长 {project.duration:.2f} 秒 · {project.fps:g} FPS 草稿 · H.264 / AAC")
        self.summary.setProperty("muted", True)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始导出")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出视频", self.path.text(), "MP4 视频 (*.mp4);;Matroska (*.mkv)")
        if path:
            self.path.setText(path)

    def accept(self) -> None:
        if not self.path.text().strip() or self.project.duration <= 0:
            QMessageBox.warning(self, "导出", "请先添加片段并选择有效的输出路径。")
            return
        if Path(self.path.text()).exists():
            answer = QMessageBox.question(self, "覆盖文件", "输出文件已存在，是否覆盖？")
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().accept()

    def settings(self) -> dict:
        width, height = self.resolutions[self.resolution.currentText()]
        return {"width": width, "height": height, "fps": self.fps.currentData(), "encoder": self.encoder.currentData(), "crf": self.crf.value(), "preset": self.preset.currentText(), "audio_bitrate": self.audio.currentText()}


class CoverDialog(QDialog):
    def __init__(self, project: Project, frame: np.ndarray, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.frame = frame
        self.cover_path = ""
        self.setWindowTitle("作品封面")
        self.setMinimumSize(760, 540)
        layout = QVBoxLayout(self)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(340)
        self.preview.setStyleSheet("background: #090A0D; border-radius: 6px;")
        layout.addWidget(self.preview, 1)
        form = QFormLayout()
        self.title = QLineEdit(project.name)
        self.title.textChanged.connect(self.update_preview)
        form.addRow("封面标题", self.title)
        self.template = QComboBox()
        self.template.addItems(["无", "底部渐变标题", "左侧大字", "极简居中"])
        self.template.currentTextChanged.connect(self.update_preview)
        form.addRow("封面模板", self.template)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存封面并关联草稿")
        buttons.accepted.connect(self.save_cover)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_preview()

    def compose(self) -> np.ndarray:
        frame = self.frame.copy()
        if self.template.currentText() == "无" or not self.title.text().strip():
            return frame
        height, width = frame.shape[:2]
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        size = max(24, int(width * 0.055))
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", size)
        except OSError:
            font = ImageFont.load_default(size=size)
        kind = self.template.currentText()
        if kind == "底部渐变标题":
            for y in range(height // 2, height):
                alpha = int((y - height / 2) / (height / 2) * 190)
                draw.line((0, y, width, y), fill=(0, 0, 0, alpha))
            position = (int(width * 0.06), int(height * 0.80))
        elif kind == "左侧大字":
            draw.rounded_rectangle((width * 0.04, height * 0.12, width * 0.65, height * 0.55), radius=18, fill=(8, 12, 18, 170))
            position = (int(width * 0.075), int(height * 0.23))
        else:
            box = draw.textbbox((0, 0), self.title.text(), font=font, stroke_width=2)
            position = ((width - box[2]) // 2, (height - box[3]) // 2)
            draw.rounded_rectangle((position[0] - 25, position[1] - 20, position[0] + box[2] + 25, position[1] + box[3] + 25), radius=12, fill=(0, 0, 0, 140))
        draw.text(position, self.title.text(), font=font, fill=(255, 255, 255, 255), stroke_width=max(1, size // 25), stroke_fill=(0, 0, 0, 190))
        return cv2.cvtColor(np.asarray(Image.alpha_composite(image, overlay).convert("RGB")), cv2.COLOR_RGB2BGR)

    def update_preview(self) -> None:
        frame = self.compose()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3, QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def save_cover(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存封面", str(Path.home() / "Pictures" / f"{self.project.name}_封面.png"), "PNG 图片 (*.png);;JPEG 图片 (*.jpg)")
        if not path:
            return
        imwrite(path, self.compose())
        self.cover_path = path
        self.accept()


class DrawingCanvas(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.image = QImage(1280, 720, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)
        self.color = QColor("#63E6FF")
        self.brush_size = 12
        self.last = QPoint()
        self.drawing = False
        self.undo_stack: list[QImage] = []
        self.redo_stack: list[QImage] = []
        self.setMinimumSize(640, 360)

    def canvas_point(self, point) -> QPoint:
        return QPoint(int(point.x() / self.width() * self.image.width()), int(point.y() / self.height() * self.image.height()))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#24262D"))
        for x in range(0, self.width(), 24):
            for y in range(0, self.height(), 24):
                if (x // 24 + y // 24) % 2 == 0:
                    painter.fillRect(x, y, 24, 24, QColor("#2B2E36"))
        painter.drawImage(self.rect(), self.image)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.undo_stack.append(self.image.copy())
            self.redo_stack.clear()
            self.last = self.canvas_point(event.position())
            self.drawing = True
            self.draw_to(self.last)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drawing:
            self.draw_to(self.canvas_point(event.position()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.drawing = False
        self.changed.emit()

    def draw_to(self, point: QPoint) -> None:
        painter = QPainter(self.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.color, self.brush_size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(self.last, point)
        painter.end()
        self.last = point
        self.update()

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(self.image.copy())
            self.image = self.undo_stack.pop()
            self.update()

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(self.image.copy())
            self.image = self.redo_stack.pop()
            self.update()

    def clear(self) -> None:
        self.undo_stack.append(self.image.copy())
        self.image.fill(Qt.GlobalColor.transparent)
        self.update()


class HandwriteDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.path = ""
        self.setWindowTitle("手写 / 涂鸦")
        layout = QVBoxLayout(self)
        self.canvas = DrawingCanvas()
        layout.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        color = QPushButton("笔刷颜色")
        color.clicked.connect(self.choose_color)
        row.addWidget(color)
        row.addWidget(QLabel("粗细"))
        size = QSpinBox()
        size.setRange(1, 80)
        size.setValue(12)
        size.valueChanged.connect(lambda v: setattr(self.canvas, "brush_size", v))
        row.addWidget(size)
        undo, redo, clear = QPushButton("撤销"), QPushButton("重做"), QPushButton("清空")
        undo.clicked.connect(self.canvas.undo)
        redo.clicked.connect(self.canvas.redo)
        clear.clicked.connect(self.canvas.clear)
        row.addWidget(undo)
        row.addWidget(redo)
        row.addWidget(clear)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("添加到手写轨")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.canvas.color, self, "笔刷颜色")
        if color.isValid():
            self.canvas.color = color

    def save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存笔迹", str(Path.home() / "Pictures" / "FrameCut_笔迹.png"), "PNG 图片 (*.png)")
        if path and self.canvas.image.save(path, "PNG"):
            self.path = path
            self.accept()


class RegionCanvas(QLabel):
    def __init__(self, frame: np.ndarray) -> None:
        super().__init__()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3, QImage.Format.Format_RGB888).copy()
        self.origin = QPoint()
        self.current = QRect()
        self.setMinimumSize(720, 405)
        self.setMouseTracking(True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#08090B"))
        target = self.image.size()
        target.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        rect = QRect(0, 0, target.width(), target.height())
        rect.moveCenter(self.rect().center())
        self.display_rect = rect
        painter.drawImage(rect, self.image)
        painter.setPen(QPen(QColor("#62E5DA"), 2))
        painter.setBrush(QColor(40, 210, 195, 35))
        painter.drawRect(self.current)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.origin = event.position().toPoint()
        self.current = QRect(self.origin, self.origin)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.current = QRect(self.origin, event.position().toPoint()).normalized()
            self.update()

    def normalized_region(self) -> list[float]:
        if self.current.width() < 10 or self.current.height() < 10:
            return [0.1, 0.1, 0.8, 0.8]
        selected = self.current.intersected(self.display_rect)
        return [max(0.0, (selected.left() - self.display_rect.left()) / self.display_rect.width()), max(0.0, (selected.top() - self.display_rect.top()) / self.display_rect.height()), min(1.0, selected.width() / self.display_rect.width()), min(1.0, selected.height() / self.display_rect.height())]


class RegionDialog(QDialog):
    def __init__(self, frame: np.ndarray, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        note = QLabel("在画面中拖出目标区域；未选择时使用中央区域。")
        layout.addWidget(note)
        self.canvas = RegionCanvas(frame)
        layout.addWidget(self.canvas)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def region(self) -> list[float]:
        return self.canvas.normalized_region()


class SpeedCurveDialog(QDialog):
    def __init__(self, clip: Clip, parent=None) -> None:
        super().__init__(parent)
        self.clip = clip
        self.setWindowTitle("曲线变速")
        layout = QVBoxLayout(self)
        self.preset = QComboBox()
        self.preset.addItems(["自定义", "蒙太奇", "英雄时刻", "子弹时间", "渐快", "渐慢"])
        self.preset.currentTextChanged.connect(self.apply_preset)
        layout.addWidget(self.preset)
        self.spins: list[QDoubleSpinBox] = []
        form = QFormLayout()
        existing = clip.effects.get("speed_points", [])
        for index, label in enumerate(["起点", "前段", "中段", "后段", "终点"]):
            spin = QDoubleSpinBox()
            spin.setRange(0.1, 8)
            spin.setSingleStep(0.1)
            spin.setSuffix("×")
            spin.setValue(existing[index]["speed"] if len(existing) == 5 else 1.0)
            form.addRow(f"{label}（{index * 25}%）", spin)
            self.spins.append(spin)
        layout.addLayout(form)
        self.info = QLabel()
        layout.addWidget(self.info)
        for spin in self.spins:
            spin.valueChanged.connect(self.update_info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_info()

    def apply_preset(self, name: str) -> None:
        presets = {"蒙太奇": [0.5, 2.5, 0.5, 2.5, 0.5], "英雄时刻": [1, 1, 0.25, 1, 1], "子弹时间": [2, 0.2, 0.2, 0.2, 2], "渐快": [0.25, 0.5, 1, 2, 4], "渐慢": [4, 2, 1, 0.5, 0.25]}
        if name in presets:
            for spin, value in zip(self.spins, presets[name]):
                spin.setValue(value)

    def points(self) -> list[dict[str, float]]:
        return [{"time": i / 4, "speed": spin.value()} for i, spin in enumerate(self.spins)]

    def update_info(self) -> None:
        average = sum(spin.value() for spin in self.spins) / 5
        source_span = self.clip.duration * self.clip.speed
        self.info.setText(f"原素材可用时长 {source_span:.2f}s · 平均速度约 {average:.2f}× · 新时长约 {source_span / average:.2f}s")


class AudioRecordDialog(QDialog):
    recorded = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("录音 / 配音")
        self.setMinimumWidth(460)
        self.stream = None
        self.blocks = []
        self.sample_rate = 48000
        self.output_path = ""
        layout = QVBoxLayout(self)
        self.status = QLabel("选择输入设备后开始录音。录音将保存为 48 kHz 立体声 WAV。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.device = QComboBox()
        try:
            import sounddevice as sd
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] > 0:
                    self.device.addItem(device["name"], index)
        except Exception as error:
            self.status.setText(f"音频设备初始化失败：{error}")
        layout.addWidget(self.device)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        layout.addWidget(self.level)
        self.time = QLabel("00:00.0")
        self.time.setObjectName("timecode")
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.time)
        self.button = QPushButton("开始录音")
        self.button.setObjectName("primary")
        self.button.clicked.connect(self.toggle)
        layout.addWidget(self.button)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_level)
        self.started_at = 0.0

    def toggle(self) -> None:
        if self.stream:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        try:
            import sounddevice as sd
            self.blocks = []
            def callback(data, frames, time_info, status) -> None:
                self.blocks.append(data.copy())
            self.stream = sd.InputStream(samplerate=self.sample_rate, channels=2, dtype="float32", device=self.device.currentData(), callback=callback)
            self.stream.start()
            self.started_at = __import__("time").monotonic()
            self.button.setText("停止并保存")
            self.status.setText("正在录音…")
            self.timer.start()
        except Exception as error:
            QMessageBox.warning(self, "录音", str(error))

    def update_level(self) -> None:
        elapsed = __import__("time").monotonic() - self.started_at
        self.time.setText(f"{int(elapsed // 60):02d}:{elapsed % 60:04.1f}")
        if self.blocks:
            recent = self.blocks[-1]
            self.level.setValue(min(100, int(np.sqrt(np.mean(recent * recent)) * 300)))

    def stop(self) -> None:
        if not self.stream:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None
        self.timer.stop()
        self.button.setText("开始录音")
        if not self.blocks:
            self.status.setText("没有录到音频")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存录音", str(Path.home() / "Music" / "FrameCut_配音.wav"), "WAV 音频 (*.wav)")
        if path:
            write_wav(path, np.concatenate(self.blocks), self.sample_rate)
            self.output_path = path
            self.recorded.emit(path)
            self.accept()
        else:
            self.status.setText("录音已停止，未保存")

    def closeEvent(self, event) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        super().closeEvent(event)


class AudioEventsDialog(QDialog):
    def __init__(self, result: dict, parent=None) -> None:
        super().__init__(parent)
        self.events = result["events"]
        self.setWindowTitle("音频事件检测 / VAD")
        self.setMinimumSize(560, 460)
        layout = QVBoxLayout(self)
        summary = QLabel(f"RMS {result['rms_db']:.1f} dBFS · 峰值 {result['peak_db']:.1f} dBFS · 检测到 {len(self.events)} 个区间")
        layout.addWidget(summary)
        listing = QListWidget()
        names = {"voice": "语音活动", "silence": "静音", "loud": "高能量事件"}
        for event in self.events:
            listing.addItem(f"{names.get(event['type'], event['type'])}  {event['start']:.3f}s — {event['end']:.3f}s")
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class HSLDialog(QDialog):
    def __init__(self, clip: Clip, parent=None) -> None:
        super().__init__(parent)
        self.clip = clip
        self.setWindowTitle("八色系 HSL")
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        layout.addLayout(grid)
        settings = clip.effects.setdefault("hsl_colors", {})
        grid.addWidget(QLabel("色系"), 0, 0)
        for col, text in enumerate(["色相", "饱和度", "明度"], 1):
            grid.addWidget(QLabel(text), 0, col)
        colors = [("red", "红"), ("orange", "橙"), ("yellow", "黄"), ("green", "绿"), ("cyan", "青"), ("blue", "蓝"), ("purple", "紫"), ("magenta", "品红")]
        for row, (key, name) in enumerate(colors, 1):
            grid.addWidget(QLabel(name), row, 0)
            values = settings.setdefault(key, [0.0, 0.0, 0.0])
            for col in range(3):
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(-100, 100)
                slider.setValue(int(values[col]))
                slider.valueChanged.connect(lambda value, v=values, i=col: v.__setitem__(i, value))
                grid.addWidget(slider, row, col + 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CornerPinDialog(QDialog):
    def __init__(self, clip: Clip, parent=None) -> None:
        super().__init__(parent)
        self.clip = clip
        self.setWindowTitle("四点透视")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.spins = []
        corners = clip.effects.get("corner_pin", [[0, 0], [1, 0], [1, 1], [0, 1]])
        for index, name in enumerate(["左上", "右上", "右下", "左下"]):
            row = QHBoxLayout()
            values = []
            for axis in range(2):
                spin = QDoubleSpinBox()
                spin.setRange(-1, 2)
                spin.setSingleStep(0.01)
                spin.setDecimals(3)
                spin.setValue(corners[index][axis])
                row.addWidget(spin)
                values.append(spin)
            self.spins.append(values)
            form.addRow(name, row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        self.clip.effects["corner_pin"] = [[spin.value() for spin in row] for row in self.spins]
        super().accept()


class ProjectDialog(QDialog):
    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("草稿设置")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.name = QLineEdit(project.name)
        form.addRow("名称", self.name)
        self.ratio = QComboBox()
        self.ratio.addItems(["16:9 横屏 · 1920×1080", "16:9 · 1280×720", "9:16 竖屏 · 1080×1920", "1:1 · 1080×1080", "4:3 · 1440×1080", "保持当前尺寸"])
        form.addRow("画布", self.ratio)
        self.fps = QComboBox()
        for fps in [24, 25, 30, 50, 60]:
            self.fps.addItem(str(fps), fps)
        self.fps.setCurrentText(str(round(project.fps)))
        form.addRow("帧率", self.fps)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self, project: Project) -> None:
        project.name = self.name.text().strip() or "未命名工程"
        sizes = {0: (1920, 1080), 1: (1280, 720), 2: (1080, 1920), 3: (1080, 1080), 4: (1440, 1080)}
        if self.ratio.currentIndex() in sizes:
            project.width, project.height = sizes[self.ratio.currentIndex()]
        project.fps = self.fps.currentData()
        project.touch()
