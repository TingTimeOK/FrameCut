from __future__ import annotations

import zipfile
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .assets import ensure_builtin_assets
from .catalog import EFFECTS, FILTERS, TEXT_TEMPLATES, TRANSITIONS
from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


class ResourcePanel(QWidget):
    importRequested = Signal()
    addMediaRequested = Signal(list)
    mediaSelected = Signal(str)
    applyRequested = Signal(str, str)
    textRequested = Signal(str)
    stickerRequested = Signal(str)
    audioRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.assets = ensure_builtin_assets()
        self.settings = QSettings("FrameCut", "FrameCut")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(["媒体", "文本", "贴纸", "音频", "滤镜", "特效", "转场", "素材商城"])
        self.category.currentTextChanged.connect(self.rebuild)
        top.addWidget(self.category, 1)
        self.import_button = QPushButton("导入")
        self.import_button.clicked.connect(self.import_requested)
        top.addWidget(self.import_button)
        layout.addLayout(top)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索素材与功能")
        self.search.textChanged.connect(self.rebuild)
        layout.addWidget(self.search)
        self.description = QLabel("本地媒体")
        self.description.setProperty("muted", True)
        layout.addWidget(self.description)
        self.list = QListWidget()
        self.list.setIconSize(QSize(72, 48))
        self.list.setSpacing(4)
        self.list.itemDoubleClicked.connect(self.activate)
        self.list.currentRowChanged.connect(self.update_description)
        layout.addWidget(self.list, 1)
        self.hint = QLabel("双击素材添加到时间线")
        self.hint.setProperty("muted", True)
        layout.addWidget(self.hint)
        self.rebuild()

    def import_requested(self) -> None:
        if self.category.currentText() == "素材商城":
            path, _ = QFileDialog.getOpenFileName(self, "导入 FrameCut 素材包", "", "素材包 (*.zip)")
            if path:
                self.import_pack(path)
        else:
            self.importRequested.emit()

    def rebuild(self) -> None:
        self.list.clear()
        category = self.category.currentText()
        query = self.search.text().strip().lower()
        if category == "媒体":
            self.import_button.setText("导入")
            return
        if category in {"文本", "贴纸", "音频"}:
            self.import_button.setText("导入")
        else:
            self.import_button.setText("素材包" if category == "素材商城" else "导入")
        rows = []
        if category in {"文本", "素材商城"}:
            rows += [("text", key, value["name"], "花字模板") for key, value in TEXT_TEMPLATES.items()]
        if category in {"贴纸", "素材商城"}:
            rows += [("sticker", str(path), path.stem, "原创透明贴纸") for path in self.assets["贴纸"]]
        if category in {"音频", "素材商城"}:
            rows += [("audio", str(path), path.stem, "原创合成音乐 / 可自由使用") for path in self.assets["音乐"] + self.assets["音效"]]
        if category in {"滤镜", "素材商城"}:
            rows += [("filter", key, value["name"], value["category"]) for key, value in FILTERS.items() if key != "none"]
        if category in {"特效", "素材商城"}:
            rows += [("effect", key, name, "实时画面特效") for key, name in EFFECTS.items() if key != "none"]
        if category in {"转场", "素材商城"}:
            rows += [("transition", key, name, "与前一片段重叠使用") for key, name in TRANSITIONS.items() if key != "none"]
        favorites = set(self.settings.value("favorites", [], type=list))
        for kind, key, name, description in rows:
            if query and query not in (name + description).lower():
                continue
            item = QListWidgetItem(("★ " if key in favorites else "") + name)
            item.setData(Qt.ItemDataRole.UserRole, (kind, key))
            item.setData(Qt.ItemDataRole.UserRole + 1, description)
            item.setToolTip(description)
            if kind in {"sticker", "audio"}:
                item.setText(name)
            self.list.addItem(item)
        self.description.setText("内置原创素材 · 无账号与 VIP 限制" if category == "素材商城" else f"{self.list.count()} 项")

    def update_description(self) -> None:
        item = self.list.currentItem()
        if item:
            self.description.setText(item.data(Qt.ItemDataRole.UserRole + 1))

    def activate(self, item: QListWidgetItem) -> None:
        kind, key = item.data(Qt.ItemDataRole.UserRole)
        if kind == "media":
            self.mediaSelected.emit(key)
        elif kind == "text":
            self.textRequested.emit(key)
        elif kind in {"filter", "effect", "transition"}:
            self.applyRequested.emit(kind, key)
        elif kind == "sticker":
            self.stickerRequested.emit(key)
        elif kind == "audio":
            self.audioRequested.emit(key)

    def show_media(self, items: list[dict]) -> None:
        if self.category.currentText() != "媒体":
            return
        self.list.clear()
        query = self.search.text().strip().lower()
        for media in items:
            if query and query not in media["name"].lower():
                continue
            item = QListWidgetItem(media["name"])
            item.setData(Qt.ItemDataRole.UserRole, ("media", media["path"]))
            item.setData(Qt.ItemDataRole.UserRole + 1, f"{media['kind']} · {media['duration']:.2f}s · {media.get('width', 0)}×{media.get('height', 0)}")
            item.setToolTip(media["path"])
            self.list.addItem(item)
        self.description.setText(f"{len(items)} 个媒体文件")

    def import_pack(self, path: str) -> None:
        destination = Path(self.assets["贴纸"][0]).parent.parent / "imported"
        destination.mkdir(exist_ok=True)
        allowed = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | {".cube"}
        count = 0
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    member_path = Path(member.filename)
                    if member.is_dir() or member_path.is_absolute() or ".." in member_path.parts or member_path.suffix.lower() not in allowed or member.file_size > 200 * 1024 * 1024:
                        continue
                    target = destination / member_path.name
                    if not target.exists():
                        target.write_bytes(archive.read(member))
                    if target.suffix.lower() in IMAGE_EXTENSIONS:
                        self.assets["贴纸"].append(target)
                    elif target.suffix.lower() in AUDIO_EXTENSIONS:
                        self.assets["音乐"].append(target)
                    count += 1
            self.rebuild()
            QMessageBox.information(self, "素材包", f"已安全导入 {count} 个素材。")
        except (OSError, zipfile.BadZipFile) as error:
            QMessageBox.warning(self, "素材包", str(error))
