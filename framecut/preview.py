from __future__ import annotations

from pathlib import Path

import cv2
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPaintEvent, QPainter
from PySide6.QtWidgets import QWidget


class PreviewWidget(QWidget):
    filesDropped = Signal(list)
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.image = QImage()
        self.safe_area = False
        self.setMinimumSize(360, 220)
        self.setAcceptDrops(True)
        self.setStyleSheet("background: #090A0D; border: 1px solid #252730; border-radius: 7px;")

    def set_frame(self, frame) -> None:
        if frame is None:
            return
        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.image = QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        self.update()

    def clear(self) -> None:
        self.image = QImage()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.image.isNull():
            painter.setPen(Qt.GlobalColor.darkGray)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "导入媒体，或将视频拖到这里")
            return
        target = self.image.size()
        target.scale(self.size() - QSize(4, 4), Qt.AspectRatioMode.KeepAspectRatio)
        rect = QRect(0, 0, target.width(), target.height())
        rect.moveCenter(self.rect().center())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(rect, self.image)
        if self.safe_area:
            painter.setPen(Qt.GlobalColor.darkGray)
            painter.drawRect(rect.adjusted(rect.width() * 0.05, rect.height() * 0.05, -rect.width() * 0.05, -rect.height() * 0.05))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)
