from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from .media import format_time
from .model import Clip, Project


CLIP_COLORS = {
    "video": "#287E86",
    "image": "#4773A6",
    "audio": "#8362A9",
    "text": "#A86F36",
    "sticker": "#3E8C68",
    "color": "#666D7C",
}


class TimelineWidget(QWidget):
    clipSelected = Signal(str)
    clipDoubleClicked = Signal(str)
    playheadChanged = Signal(float)
    operationStarted = Signal()
    changed = Signal()
    operationFinished = Signal()
    contextRequested = Signal(str, QPoint)
    zoomChanged = Signal(float)

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.selected_id = ""
        self.playhead = 0.0
        self.pixels_per_second = 80.0
        self.header_width = 116
        self.ruler_height = 32
        self.row_height = 66
        self.drag_mode = ""
        self.drag_clip: Clip | None = None
        self.drag_origin: dict[str, Any] = {}
        self.drag_start = QPoint()
        self.last_mouse = QPoint()
        self.snapping = True
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(self.ruler_height + len(project.tracks) * self.row_height + 30)
        self.update_size()

    def set_project(self, project: Project) -> None:
        self.project = project
        self.selected_id = ""
        self.playhead = 0.0
        self.update_size()
        self.update()

    def update_size(self) -> None:
        width = self.header_width + max(60.0, self.project.duration + 10.0) * self.pixels_per_second
        height = self.ruler_height + len(self.project.tracks) * self.row_height + 30
        self.setMinimumSize(int(width), int(height))

    def set_zoom(self, pixels_per_second: float) -> None:
        self.pixels_per_second = min(500.0, max(12.0, pixels_per_second))
        self.update_size()
        self.zoomChanged.emit(self.pixels_per_second)
        self.update()

    def time_at(self, x: float) -> float:
        return max(0.0, (x - self.header_width) / self.pixels_per_second)

    def x_at(self, seconds: float) -> float:
        return self.header_width + seconds * self.pixels_per_second

    def row_at(self, y: float) -> int:
        return int((y - self.ruler_height) // self.row_height)

    def clip_rect(self, clip: Clip) -> QRectF:
        return QRectF(self.x_at(clip.start), self.ruler_height + clip.track * self.row_height + 6, max(5.0, clip.duration * self.pixels_per_second), self.row_height - 12)

    def hit_clip(self, point: QPoint) -> Clip | None:
        for clip in reversed(sorted(self.project.clips, key=lambda c: c.track)):
            if self.clip_rect(clip).contains(point):
                return clip
        return None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#121318"))
        width = self.width()
        painter.fillRect(0, 0, width, self.ruler_height, QColor("#1B1D24"))
        painter.fillRect(0, self.ruler_height, self.header_width, self.height(), QColor("#191B22"))
        step = self.ruler_step()
        first = int(self.time_at(self.header_width) / step) * step
        for index in range(int((width - self.header_width) / (step * self.pixels_per_second)) + 3):
            seconds = first + index * step
            if seconds < 0:
                continue
            x = self.x_at(seconds)
            painter.setPen(QPen(QColor("#353842"), 1))
            painter.drawLine(int(x), self.ruler_height - 9, int(x), self.ruler_height)
            painter.setPen(QColor("#9298A8"))
            painter.drawText(QRectF(x + 4, 6, 90, 20), format_time(seconds))
            painter.setPen(QPen(QColor("#24262E"), 1))
            painter.drawLine(int(x), self.ruler_height, int(x), self.height())
        for index, track in enumerate(self.project.tracks):
            y = self.ruler_height + index * self.row_height
            painter.fillRect(0, y, width, self.row_height, QColor("#171920") if index % 2 == 0 else QColor("#15171D"))
            painter.setPen(QPen(QColor("#292C35"), 1))
            painter.drawLine(0, y + self.row_height, width, y + self.row_height)
            painter.setPen(QColor("#D4D8E1") if not track.muted else QColor("#737887"))
            painter.drawText(QRectF(10, y + 8, 96, 21), track.name)
            painter.setPen(QColor("#777E8D"))
            painter.drawText(QRectF(10, y + 34, 45, 18), "静音" if track.kind == "audio" else "隐藏")
            painter.drawText(QRectF(65, y + 34, 40, 18), "锁定")
            self.draw_toggle(painter, QRectF(43, y + 35, 12, 12), track.muted if track.kind == "audio" else not track.visible)
            self.draw_toggle(painter, QRectF(96, y + 35, 12, 12), track.locked)
        for clip in self.project.clips:
            self.draw_clip(painter, clip)
        for marker in self.project.markers:
            x = self.x_at(float(marker["time"]))
            painter.setPen(QPen(QColor(marker.get("color", "#F4C95D")), 1))
            painter.drawLine(int(x), 1, int(x), self.height())
            painter.setBrush(QColor(marker.get("color", "#F4C95D")))
            painter.drawPolygon([QPoint(int(x) - 4, 1), QPoint(int(x) + 4, 1), QPoint(int(x), 7)])
        x = self.x_at(self.playhead)
        painter.setPen(QPen(QColor("#61E7DC"), 1.4))
        painter.drawLine(int(x), 0, int(x), self.height())
        painter.setBrush(QColor("#61E7DC"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon([QPoint(int(x) - 6, 0), QPoint(int(x) + 6, 0), QPoint(int(x), 11)])

    def draw_toggle(self, painter: QPainter, rect: QRectF, checked: bool) -> None:
        painter.setPen(QPen(QColor("#656B7A"), 1))
        painter.setBrush(QColor("#2EAAA4") if checked else QColor("#252831"))
        painter.drawRoundedRect(rect, 2, 2)
        if checked:
            painter.setPen(QPen(QColor("#E5FFFC"), 2))
            painter.drawLine(int(rect.left() + 3), int(rect.center().y()), int(rect.center().x()), int(rect.bottom() - 3))
            painter.drawLine(int(rect.center().x()), int(rect.bottom() - 3), int(rect.right() - 2), int(rect.top() + 3))

    def draw_clip(self, painter: QPainter, clip: Clip) -> None:
        rect = self.clip_rect(clip)
        color = QColor(CLIP_COLORS.get(clip.kind, "#47737E"))
        if clip.id == self.selected_id:
            color = color.lighter(120)
        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        painter.fillPath(path, color)
        painter.setPen(QPen(QColor("#D9FFFA") if clip.id == self.selected_id else color.darker(125), 1.5 if clip.id == self.selected_id else 1))
        painter.drawPath(path)
        painter.save()
        painter.setClipPath(path)
        thumbnails = clip.effects.get("thumbnails", [])
        if thumbnails and rect.width() > 70:
            thumb_width = self.row_height - 12
            for i, thumbnail in enumerate(thumbnails):
                x = rect.left() + i * thumb_width
                if x > rect.right():
                    break
                image = QImage(thumbnail)
                if not image.isNull():
                    painter.drawImage(QRectF(x, rect.top(), thumb_width, rect.height()), image)
                    painter.fillRect(QRectF(x, rect.top(), thumb_width, rect.height()), QColor(20, 50, 55, 100))
        if clip.kind == "audio" or (clip.kind == "video" and clip.effects.get("waveform")):
            waveform = clip.effects.get("waveform", [])
            painter.setPen(QPen(QColor(220, 200, 255, 150), 1))
            middle = rect.center().y()
            for i, value in enumerate(waveform[:max(0, int(rect.width() / 3))]):
                x = rect.left() + i * 3
                amplitude = min(rect.height() * 0.35, float(value) * rect.height() * 1.8)
                painter.drawLine(int(x), int(middle - amplitude), int(x), int(middle + amplitude))
        painter.setPen(QColor("#F3F5F8"))
        name_rect = QRectF(rect.left() + 7, rect.top() + 5, max(0, rect.width() - 14), 20)
        painter.drawText(name_rect, clip.name)
        painter.setPen(QColor(235, 240, 247, 180))
        painter.drawText(QRectF(rect.left() + 7, rect.top() + 28, max(0, rect.width() - 14), 18), f"{clip.duration:.2f}s" + (f"  {clip.speed:g}×" if clip.speed != 1 else "") + ("  倒放" if clip.reverse else ""))
        for prop, frames in clip.keyframes.items():
            for frame in frames:
                x = rect.left() + frame["time"] / max(clip.duration, 0.001) * rect.width()
                y = rect.bottom() - 6
                painter.setBrush(QColor("#FFF2A8"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPolygon([QPoint(int(x), int(y - 3)), QPoint(int(x) + 3, int(y)), QPoint(int(x), int(y + 3)), QPoint(int(x) - 3, int(y))])
        for beat in clip.effects.get("beats", []):
            x = rect.left() + float(beat) / max(clip.duration, 0.001) * rect.width()
            painter.setPen(QPen(QColor("#FFE278"), 1))
            painter.drawLine(int(x), int(rect.bottom() - 11), int(x), int(rect.bottom() - 3))
        painter.restore()

    def ruler_step(self) -> float:
        target = 100.0 / self.pixels_per_second
        for step in [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]:
            if step >= target:
                return step
        return 900

    def mousePressEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint()
        self.last_mouse = point
        if event.button() == Qt.MouseButton.RightButton:
            clip = self.hit_clip(point)
            if clip:
                self.selected_id = clip.id
                self.clipSelected.emit(clip.id)
                self.contextRequested.emit(clip.id, event.globalPosition().toPoint())
            self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        if point.x() < self.header_width and point.y() >= self.ruler_height:
            row = self.row_at(point.y())
            if 0 <= row < len(self.project.tracks):
                track = self.project.tracks[row]
                local_y = point.y() - (self.ruler_height + row * self.row_height)
                if 34 <= local_y <= 51:
                    self.operationStarted.emit()
                    if 40 <= point.x() <= 59:
                        if track.kind == "audio":
                            track.muted = not track.muted
                        else:
                            track.visible = not track.visible
                    elif 91 <= point.x() <= 111:
                        track.locked = not track.locked
                    self.project.touch()
                    self.changed.emit()
                    self.operationFinished.emit()
                    self.update()
                    return
        clip = self.hit_clip(point)
        if clip:
            self.selected_id = clip.id
            self.clipSelected.emit(clip.id)
            if clip.track < len(self.project.tracks) and self.project.tracks[clip.track].locked:
                self.update()
                return
            rect = self.clip_rect(clip)
            self.drag_clip = clip
            self.drag_start = point
            self.drag_origin = {"start": clip.start, "track": clip.track, "source_offset": clip.source_offset, "source_duration": clip.source_duration, "duration": clip.duration}
            self.drag_mode = "trim_left" if abs(point.x() - rect.left()) <= 7 else "trim_right" if abs(point.x() - rect.right()) <= 7 else "move"
            self.operationStarted.emit()
        elif point.y() <= self.ruler_height or point.x() >= self.header_width:
            self.drag_mode = "seek"
            self.set_playhead(self.time_at(point.x()))
            self.selected_id = ""
            self.clipSelected.emit("")
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint()
        self.last_mouse = point
        if self.drag_mode == "seek":
            self.set_playhead(self.time_at(point.x()))
            return
        if not self.drag_clip or not event.buttons() & Qt.MouseButton.LeftButton:
            clip = self.hit_clip(point)
            cursor = Qt.CursorShape.ArrowCursor
            if clip:
                rect = self.clip_rect(clip)
                cursor = Qt.CursorShape.SizeHorCursor if abs(point.x() - rect.left()) <= 7 or abs(point.x() - rect.right()) <= 7 else Qt.CursorShape.OpenHandCursor
            elif point.y() <= self.ruler_height:
                cursor = Qt.CursorShape.PointingHandCursor
            self.setCursor(cursor)
            return
        clip = self.drag_clip
        delta = (point.x() - self.drag_start.x()) / self.pixels_per_second
        origin = self.drag_origin
        if self.drag_mode == "move":
            new_start = max(0.0, origin["start"] + delta)
            if self.snapping:
                new_start = self.snap_time(new_start, clip)
            clip.start = new_start
            new_track = max(0, min(len(self.project.tracks) - 1, self.row_at(point.y())))
            target_kind = self.project.tracks[new_track].kind
            if (clip.kind == "audio") == (target_kind == "audio"):
                clip.track = new_track
        elif self.drag_mode == "trim_left":
            minimum = origin["start"] - origin["source_offset"] / max(clip.speed, 0.05) if not clip.reverse else max(0.0, origin["start"] - (origin["source_duration"] - origin["source_offset"]) / max(clip.speed, 0.05))
            new_start = min(max(minimum, origin["start"] + delta), origin["start"] + origin["duration"] - 0.04)
            if self.snapping:
                new_start = self.snap_time(new_start, clip)
            actual = new_start - origin["start"]
            clip.start = new_start
            clip.duration = origin["duration"] - actual
            if not clip.reverse:
                clip.source_offset = max(0.0, origin["source_offset"] + actual * clip.speed)
            else:
                clip.source_duration = max(0.04, origin["source_duration"] - actual * clip.speed)
        elif self.drag_mode == "trim_right":
            new_end = max(origin["start"] + 0.04, origin["start"] + origin["duration"] + delta)
            if self.snapping:
                new_end = self.snap_time(new_end, clip)
            maximum = origin["start"] + max(0.04, (clip.source_duration - clip.source_offset) / max(clip.speed, 0.05)) if clip.source_duration and not clip.reverse else new_end
            clip.duration = min(new_end, maximum) - origin["start"]
        self.project.touch()
        self.changed.emit()
        self.update_size()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.drag_mode and self.drag_mode != "seek":
            self.operationFinished.emit()
        self.drag_mode = ""
        self.drag_clip = None
        self.drag_origin = {}

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        clip = self.hit_clip(event.position().toPoint())
        if clip:
            self.clipDoubleClicked.emit(clip.id)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.pixels_per_second * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15))
            event.accept()
        else:
            event.ignore()

    def snap_time(self, value: float, moving: Clip) -> float:
        tolerance = 8.0 / self.pixels_per_second
        candidates = [0.0, self.playhead] + [float(m["time"]) for m in self.project.markers]
        for clip in self.project.clips:
            if clip.id != moving.id:
                candidates += [clip.start, clip.end]
        closest = min(candidates, key=lambda c: abs(c - value), default=value)
        return closest if abs(closest - value) <= tolerance else value

    def set_playhead(self, seconds: float) -> None:
        self.playhead = min(max(0.0, seconds), max(self.project.duration, 0.0))
        self.playheadChanged.emit(self.playhead)
        self.update()
