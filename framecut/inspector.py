from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .catalog import ANIMATIONS, EFFECTS, TRANSITIONS, VOICE_EFFECTS
from .model import Clip, Project


class CurveEditor(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.points: list[list[float]] = []
        self.selected = -1
        self.setMinimumHeight(150)
        self.setMaximumHeight(190)

    def set_points(self, points: list[list[float]]) -> None:
        self.points = points
        self.selected = -1
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#11141A"))
        for i in range(1, 4):
            x, y = self.width() * i / 4, self.height() * i / 4
            painter.setPen(QPen(QColor("#292E38"), 1))
            painter.drawLine(int(x), 0, int(x), self.height())
            painter.drawLine(0, int(y), self.width(), int(y))
        painter.setPen(QPen(QColor("#424754"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(0, self.height(), self.width(), 0)
        if len(self.points) >= 2:
            painter.setPen(QPen(QColor("#56D9CF"), 2))
            for left, right in zip(self.points, self.points[1:]):
                painter.drawLine(self.point_pos(left), self.point_pos(right))
        for i, point in enumerate(self.points):
            painter.setBrush(QColor("#F5E68A") if i == self.selected else QColor("#D6FFFB"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self.point_pos(point), 4, 4)

    def point_pos(self, point: list[float]) -> QPointF:
        return QPointF(point[0] * self.width(), (1.0 - point[1]) * self.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        distances = [(self.point_pos(p) - position).manhattanLength() for p in self.points]
        self.selected = min(range(len(distances)), key=distances.__getitem__, default=-1)
        if self.selected >= 0 and distances[self.selected] > 18:
            self.points.append([position.x() / self.width(), 1.0 - position.y() / self.height()])
            self.points.sort()
            self.selected = self.points.index(min(self.points, key=lambda p: abs(p[0] - position.x() / self.width())))
            self.changed.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.selected < 0 or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        point = self.points[self.selected]
        if self.selected not in {0, len(self.points) - 1}:
            point[0] = min(max(event.position().x() / self.width(), self.points[self.selected - 1][0] + 0.01), self.points[self.selected + 1][0] - 0.01)
        point[1] = min(max(1.0 - event.position().y() / self.height(), 0.0), 1.0)
        self.changed.emit()
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if 0 < self.selected < len(self.points) - 1:
            self.points.pop(self.selected)
            self.selected = -1
            self.changed.emit()
            self.update()


class Inspector(QWidget):
    changed = Signal()
    actionRequested = Signal(str, dict)
    playheadRequested = Signal()

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.clip: Clip | None = None
        self.playhead = 0.0
        self.loading = False
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel("未选择片段")
        self.header.setObjectName("section")
        self.header.setContentsMargins(12, 10, 12, 8)
        self.root.addWidget(self.header)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs)
        self.show_clip(None)

    def set_project(self, project: Project) -> None:
        self.project = project
        self.show_clip(None)

    def set_playhead(self, seconds: float) -> None:
        self.playhead = seconds

    def show_clip(self, clip: Clip | None) -> None:
        self.loading = True
        self.clip = clip
        self.header.setText(f"{clip.name}  ·  {clip.kind}" if clip else "未选择片段")
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()
        if clip is None:
            empty = QLabel("在时间线选择片段后，可在这里调整参数。\n\n所有修改会实时预览并写入草稿。")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setProperty("muted", True)
            self.tabs.addTab(empty, "检查器")
        else:
            self.tabs.addTab(self.scroll(self.build_video()), "画面" if clip.kind != "text" else "文本")
            if clip.kind not in {"text", "sticker", "color"}:
                self.tabs.addTab(self.scroll(self.build_adjust()), "调节")
            if clip.audible:
                self.tabs.addTab(self.scroll(self.build_audio()), "音频")
            self.tabs.addTab(self.scroll(self.build_animation()), "动画")
            self.tabs.addTab(self.scroll(self.build_keyframes()), "关键帧")
        self.loading = False

    def refresh(self) -> None:
        index = self.tabs.currentIndex()
        self.show_clip(self.clip)
        self.tabs.setCurrentIndex(min(index, self.tabs.count() - 1))

    def scroll(self, widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(widget)
        return area

    def page(self) -> tuple[QWidget, QVBoxLayout]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 20)
        layout.setSpacing(10)
        return widget, layout

    def group(self, layout: QVBoxLayout, title: str) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(9, 9, 9, 9)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(7)
        layout.addWidget(box)
        return form

    def mutate(self, callback: Callable[[], None]) -> None:
        if self.loading or not self.clip:
            return
        callback()
        self.project.touch()
        self.changed.emit()

    def slider(self, form: QFormLayout, label: str, minimum: float, maximum: float, getter: Callable[[], float], setter: Callable[[float], None], step: float = 1.0, suffix: str = "") -> QSlider:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(round(minimum / step), round(maximum / step))
        slider.setSingleStep(1)
        value = QLabel()
        value.setMinimumWidth(52)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(slider, 1)
        row.addWidget(value)
        form.addRow(label, container)
        slider.setValue(round(getter() / step))
        value.setText(f"{getter():g}{suffix}")
        slider.valueChanged.connect(lambda v: (value.setText(f"{v * step:g}{suffix}"), self.mutate(lambda: setter(v * step))))
        return slider

    def double(self, form: QFormLayout, label: str, minimum: float, maximum: float, getter: Callable[[], float], setter: Callable[[float], None], suffix: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setValue(getter())
        spin.setSuffix(suffix)
        spin.valueChanged.connect(lambda value: self.mutate(lambda: setter(value)))
        form.addRow(label, spin)
        return spin

    def combo(self, form: QFormLayout, label: str, values: dict[str, str], getter: Callable[[], str], setter: Callable[[str], None]) -> QComboBox:
        combo = QComboBox()
        for key, name in values.items():
            combo.addItem(name, key)
        index = combo.findData(getter())
        combo.setCurrentIndex(max(0, index))
        combo.currentIndexChanged.connect(lambda: self.mutate(lambda: setter(combo.currentData())))
        form.addRow(label, combo)
        return combo

    def check(self, form: QFormLayout, label: str, getter: Callable[[], bool], setter: Callable[[bool], None]) -> QCheckBox:
        box = QCheckBox()
        box.setChecked(getter())
        box.toggled.connect(lambda value: self.mutate(lambda: setter(value)))
        form.addRow(label, box)
        return box

    def action_button(self, form: QFormLayout, label: str, action: str, payload: dict | None = None) -> None:
        button = QPushButton(label)
        button.clicked.connect(lambda: self.actionRequested.emit(action, payload or {}))
        form.addRow(button)

    def build_video(self) -> QWidget:
        widget, layout = self.page()
        clip = self.clip
        assert clip
        if clip.kind == "text":
            form = self.group(layout, "文本内容")
            editor = QPlainTextEdit(clip.text)
            editor.setMaximumHeight(90)
            editor.textChanged.connect(lambda: self.mutate(lambda: setattr(clip, "text", editor.toPlainText())))
            form.addRow(editor)
            font = QFontComboBox()
            font.setCurrentFont(clip.style.get("font", "Microsoft YaHei"))
            font.currentFontChanged.connect(lambda value: self.mutate(lambda: clip.style.update(font=value.family())))
            form.addRow("字体", font)
            self.double(form, "字号", 8, 300, lambda: clip.style.get("size", 54), lambda v: clip.style.update(size=v))
            self.check(form, "粗体", lambda: clip.style.get("bold", False), lambda v: clip.style.update(bold=v))
            self.check(form, "阴影", lambda: clip.style.get("shadow", True), lambda v: clip.style.update(shadow=v))
            self.slider(form, "描边宽度", 0, 16, lambda: clip.style.get("stroke_width", 2), lambda v: clip.style.update(stroke_width=int(v)))
            self.slider(form, "横向位置", -0.5, 1.5, lambda: clip.style.get("x", 0.5), lambda v: clip.style.update(x=v), 0.01)
            self.slider(form, "纵向位置", -0.5, 1.5, lambda: clip.style.get("y", 0.82), lambda v: clip.style.update(y=v), 0.01)
            self.slider(form, "缩放", 0.1, 5, lambda: clip.style.get("scale", 1), lambda v: clip.style.update(scale=v), 0.01)
            self.slider(form, "旋转", -180, 180, lambda: clip.style.get("rotation", 0), lambda v: clip.style.update(rotation=v), 1, "°")
            self.color_row(form, "文字颜色", "color")
            self.color_row(form, "描边颜色", "stroke_color")
            self.color_row(form, "背景颜色", "background")
            keywords = QLineEdit("，".join(clip.style.get("keywords", [])))
            keywords.setPlaceholderText("关键词，用逗号分隔")
            keywords.editingFinished.connect(lambda: self.mutate(lambda: clip.style.update(keywords=[k.strip() for k in keywords.text().replace("，", ",").split(",") if k.strip()])))
            form.addRow("强调关键词", keywords)
            self.color_row(form, "关键词颜色", "keyword_color")
            targets = {c.id: c.name for c in self.project.clips if c.kind == "video"}
            self.combo(form, "跟随视频跟踪", {"": "不跟随", **targets}, lambda: clip.effects.get("tracking_target", ""), lambda v: clip.effects.update(tracking_target=v))
        else:
            form = self.group(layout, "变换 / 裁剪")
            self.slider(form, "缩放", 0.05, 5, lambda: clip.effects.get("scale", 1), lambda v: clip.effects.update(scale=v), 0.01, "×")
            self.slider(form, "横向缩放", 0.05, 5, lambda: clip.effects.get("scale_x", 1), lambda v: clip.effects.update(scale_x=v), 0.01)
            self.slider(form, "纵向缩放", 0.05, 5, lambda: clip.effects.get("scale_y", 1), lambda v: clip.effects.update(scale_y=v), 0.01)
            self.slider(form, "旋转", -180, 180, lambda: clip.effects.get("rotation", 0), lambda v: clip.effects.update(rotation=v), 1, "°")
            self.slider(form, "水平位置", -1, 1, lambda: clip.effects.get("x", 0), lambda v: clip.effects.update(x=v), 0.01)
            self.slider(form, "垂直位置", -1, 1, lambda: clip.effects.get("y", 0), lambda v: clip.effects.update(y=v), 0.01)
            self.check(form, "水平镜像", lambda: clip.effects.get("mirror_horizontal", False), lambda v: clip.effects.update(mirror_horizontal=v))
            self.check(form, "垂直镜像", lambda: clip.effects.get("mirror_vertical", False), lambda v: clip.effects.update(mirror_vertical=v))
            self.slider(form, "圆角半径", 0, 0.5, lambda: clip.effects.get("round_radius", 0), lambda v: clip.effects.update(round_radius=v), 0.01)
            crop = clip.effects.setdefault("crop", {})
            for key, label in [("left", "裁剪左侧"), ("right", "裁剪右侧"), ("top", "裁剪顶部"), ("bottom", "裁剪底部")]:
                self.slider(form, label, 0, 0.45, lambda k=key: crop.get(k, 0), lambda v, k=key: (crop.update({k: v, "enabled": True})), 0.01)
            self.action_button(form, "四点透视（Corner Pin）…", "corner_pin")
            form = self.group(layout, "速度 / 时间")
            def set_speed(value: float) -> None:
                source_span = clip.duration * clip.speed
                clip.speed = value
                clip.duration = max(0.04, source_span / value)
                clip.effects.pop("speed_points", None)
            self.double(form, "播放速度", 0.1, 16, lambda: clip.speed, set_speed, "×")
            self.action_button(form, "曲线变速…", "curve_speed")
            self.check(form, "倒放", lambda: clip.reverse, lambda v: setattr(clip, "reverse", v))
            self.action_button(form, "从播放头创建定格", "freeze")
            form = self.group(layout, "合成")
            self.slider(form, "不透明度", 0, 1, lambda: clip.opacity, lambda v: setattr(clip, "opacity", v), 0.01)
            self.combo(form, "混合模式", {k: n for k, n in [("normal", "正常"), ("multiply", "正片叠底"), ("screen", "滤色"), ("overlay", "叠加"), ("add", "线性减淡"), ("darken", "变暗"), ("lighten", "变亮"), ("difference", "差值")]}, lambda: clip.blend_mode, lambda v: setattr(clip, "blend_mode", v))
        if clip.kind in {"video", "image"}:
            form = self.group(layout, "色度抠图 / 智能抠像")
            chroma = clip.effects.setdefault("chroma", {})
            self.check(form, "启用色度抠图", lambda: chroma.get("enabled", False), lambda v: chroma.update(enabled=v))
            self.slider(form, "目标色相", 0, 360, lambda: chroma.get("hue", 120), lambda v: chroma.update(hue=v), 1, "°")
            self.slider(form, "强度 / 容差", 1, 100, lambda: chroma.get("tolerance", 35), lambda v: chroma.update(tolerance=v))
            self.slider(form, "边缘平滑", 0, 20, lambda: chroma.get("smooth", 4), lambda v: chroma.update(smooth=int(v)))
            matting = clip.effects.setdefault("matting", {})
            self.check(form, "智能主体抠像（GrabCut）", lambda: matting.get("enabled", False), lambda v: matting.update(enabled=v))
            self.action_button(form, "设置主体区域…", "matting_region")
            form = self.group(layout, "蒙版 / 运动跟踪")
            mask = clip.effects.setdefault("mask", {})
            self.check(form, "启用蒙版", lambda: mask.get("enabled", False), lambda v: mask.update(enabled=v))
            self.combo(form, "形状", {"rectangle": "矩形", "ellipse": "椭圆", "linear": "线性", "mirror": "镜面"}, lambda: mask.get("shape", "rectangle"), lambda v: mask.update(shape=v))
            self.slider(form, "羽化", 0, 100, lambda: mask.get("feather", 0), lambda v: mask.update(feather=v))
            self.slider(form, "蒙版宽度", 0.05, 1.5, lambda: mask.get("width", 0.8), lambda v: mask.update(width=v), 0.01)
            self.slider(form, "蒙版高度", 0.05, 1.5, lambda: mask.get("height", 0.8), lambda v: mask.update(height=v), 0.01)
            self.slider(form, "蒙版旋转", -180, 180, lambda: mask.get("rotation", 0), lambda v: mask.update(rotation=v), 1, "°")
            self.check(form, "反转蒙版", lambda: mask.get("invert", False), lambda v: mask.update(invert=v))
            self.action_button(form, "跟踪画面中的物体…", "tracking")
            targets = {c.id: c.name for c in self.project.clips if c.visual and c.id != clip.id}
            self.combo(form, "跟随跟踪结果", {"": "不跟随", **targets}, lambda: clip.effects.get("tracking_target", ""), lambda v: clip.effects.update(tracking_target=v))
            form = self.group(layout, "画质 / 光效")
            quality = clip.effects.setdefault("quality", {})
            self.slider(form, "视频降噪", 0, 100, lambda: quality.get("denoise", 0), lambda v: quality.update(denoise=v))
            self.slider(form, "细节增强", 0, 100, lambda: quality.get("sharpen", 0), lambda v: quality.update(sharpen=v))
            self.check(form, "去闪烁（导出）", lambda: quality.get("deflicker", False), lambda v: quality.update(deflicker=v))
            self.check(form, "智能补帧（导出）", lambda: clip.effects.get("interpolate", False), lambda v: clip.effects.update(interpolate=v))
            self.check(form, "视频防抖（导出）", lambda: clip.effects.get("stabilize", False), lambda v: clip.effects.update(stabilize=v))
            relight = clip.effects.setdefault("relight", {})
            self.check(form, "智能打光", lambda: relight.get("enabled", False), lambda v: relight.update(enabled=v))
            self.slider(form, "打光强度", 0, 100, lambda: relight.get("intensity", 35), lambda v: relight.update(intensity=v))
            self.slider(form, "光源半径", 0.05, 2, lambda: relight.get("radius", 0.8), lambda v: relight.update(radius=v), 0.01)
            self.slider(form, "光源 X", 0, 1, lambda: relight.get("x", 0.25), lambda v: relight.update(x=v), 0.01)
            self.slider(form, "光源 Y", 0, 1, lambda: relight.get("y", 0.2), lambda v: relight.update(y=v), 0.01)
            hdr = clip.effects.setdefault("hdr", {})
            self.check(form, "HDR 色调映射 / 导出", lambda: hdr.get("enabled", False), lambda v: hdr.update(enabled=v))
            self.slider(form, "HDR 强度", 0, 100, lambda: hdr.get("intensity", 30), lambda v: hdr.update(intensity=v))
        layout.addStretch()
        return widget

    def color_row(self, form: QFormLayout, label: str, key: str) -> None:
        clip = self.clip
        button = QPushButton(clip.style.get(key, "#FFFFFF"))
        def choose() -> None:
            color = QColorDialog.getColor(QColor(button.text()), self, label, QColorDialog.ColorDialogOption.ShowAlphaChannel)
            if color.isValid():
                value = color.name(QColor.NameFormat.HexArgb) if color.alpha() < 255 else color.name()
                button.setText(value)
                self.mutate(lambda: clip.style.update({key: value}))
        button.clicked.connect(choose)
        form.addRow(label, button)

    def build_adjust(self) -> QWidget:
        widget, layout = self.page()
        clip = self.clip
        assert clip
        form = self.group(layout, "基础调节")
        for key, label, low, high in [
            ("brightness", "亮度", -100, 100), ("contrast", "对比度", -100, 100), ("saturation", "饱和度", -100, 100),
            ("temperature", "色温", -100, 100), ("tint", "色调", -100, 100), ("exposure", "曝光", -100, 100),
            ("highlights", "高光", -100, 100), ("shadows", "阴影", -100, 100), ("sharpen", "锐化", 0, 100),
            ("vignette", "暗角", 0, 100), ("grain", "颗粒", 0, 100),
        ]:
            self.slider(form, label, low, high, lambda k=key: clip.adjust.get(k, 0), lambda v, k=key: clip.adjust.update({k: v}))
        reset = QPushButton("重置基础调节")
        from .model import default_adjust
        reset.clicked.connect(lambda: self.mutate(lambda: clip.adjust.update(default_adjust())))
        form.addRow(reset)
        form = self.group(layout, "HSL / 曲线 / 色轮")
        self.slider(form, "全局色相", -180, 180, lambda: clip.hsl.get("hue", 0), lambda v: clip.hsl.update(hue=v), 1, "°")
        self.slider(form, "HSL 饱和度", -100, 100, lambda: clip.hsl.get("saturation", 0), lambda v: clip.hsl.update(saturation=v))
        self.slider(form, "HSL 明度", -100, 100, lambda: clip.hsl.get("lightness", 0), lambda v: clip.hsl.update(lightness=v))
        self.action_button(form, "八色系独立 HSL…", "hsl_colors")
        curve_channel = QComboBox()
        for key, name in [("master", "RGB 联动"), ("curves_r", "红"), ("curves_g", "绿"), ("curves_b", "蓝")]:
            curve_channel.addItem(name, key)
        curves = CurveEditor()
        def selected_curve() -> list[list[float]]:
            if curve_channel.currentData() == "master":
                return clip.curves
            return clip.effects.setdefault(curve_channel.currentData(), [[0, 0], [0.25, 0.25], [0.5, 0.5], [0.75, 0.75], [1, 1]])
        curve_channel.currentIndexChanged.connect(lambda: curves.set_points(selected_curve()))
        curves.set_points(clip.curves)
        curves.changed.connect(lambda: self.mutate(lambda: None))
        form.addRow("曲线通道", curve_channel)
        form.addRow("曲线", curves)
        reset_curve = QPushButton("重置当前曲线（双击中间点可删除）")
        def reset_curves() -> None:
            selected_curve()[:] = [[0, 0], [0.25, 0.25], [0.5, 0.5], [0.75, 0.75], [1, 1]]
            curves.set_points(selected_curve())
            self.mutate(lambda: None)
        reset_curve.clicked.connect(reset_curves)
        form.addRow(reset_curve)
        for key, label in [("shadows", "阴影色轮"), ("midtones", "中间调色轮"), ("highlights", "高光色轮")]:
            for channel, channel_name in enumerate(("红", "绿", "蓝")):
                self.slider(form, f"{label} · {channel_name}", -1, 1, lambda k=key, i=channel: clip.wheels[k][i], lambda v, k=key, i=channel: clip.wheels[k].__setitem__(i, v), 0.01)
        form = self.group(layout, "滤镜 / 色彩匹配")
        self.slider(form, "滤镜强度", 0, 1, lambda: clip.filter_strength, lambda v: setattr(clip, "filter_strength", v), 0.01)
        self.action_button(form, "导入 .cube LUT…", "import_lut")
        self.action_button(form, "从参考画面匹配色彩…", "color_match")
        layout.addStretch()
        return widget

    def build_audio(self) -> QWidget:
        widget, layout = self.page()
        clip = self.clip
        assert clip
        form = self.group(layout, "片段音频")
        self.slider(form, "音量", 0, 2, lambda: clip.volume, lambda v: setattr(clip, "volume", v), 0.01)
        self.check(form, "静音", lambda: clip.muted, lambda v: setattr(clip, "muted", v))
        self.double(form, "淡入", 0, min(30, clip.duration), lambda: clip.fade_in, lambda v: setattr(clip, "fade_in", v), " 秒")
        self.double(form, "淡出", 0, min(30, clip.duration), lambda: clip.fade_out, lambda v: setattr(clip, "fade_out", v), " 秒")
        self.slider(form, "声像", -1, 1, lambda: clip.pan, lambda v: setattr(clip, "pan", v), 0.01)
        self.combo(form, "声道映射", {"stereo": "立体声", "left": "左声道复制", "right": "右声道复制", "mono": "单声道", "swap": "左右交换"}, lambda: clip.channel_mode, lambda v: setattr(clip, "channel_mode", v))
        form = self.group(layout, "音效 / 变声")
        self.slider(form, "变调", -12, 12, lambda: clip.pitch, lambda v: setattr(clip, "pitch", v), 1, " 半音")
        self.combo(form, "声音效果", VOICE_EFFECTS, lambda: clip.voice_effect, lambda v: setattr(clip, "voice_effect", v))
        form = self.group(layout, "恢复 / 分离")
        self.slider(form, "降噪强度", 0, 60, lambda: clip.denoise, lambda v: setattr(clip, "denoise", v))
        self.check(form, "响度均衡（-16 LUFS）", lambda: clip.loudness, lambda v: setattr(clip, "loudness", v))
        self.combo(form, "立体声中置人声分离", {"none": "不分离", "keep_vocal": "保留中置人声", "remove_vocal": "消除中置人声"}, lambda: clip.vocal_mode, lambda v: setattr(clip, "vocal_mode", v))
        self.action_button(form, "分析音频事件 / 响度", "audio_events")
        self.action_button(form, "自动踩点", "beats")
        self.action_button(form, "与另一段音频对齐…", "align_audio")
        layout.addStretch()
        return widget

    def build_animation(self) -> QWidget:
        widget, layout = self.page()
        clip = self.clip
        assert clip
        style = clip.style if clip.kind == "text" else clip.effects
        form = self.group(layout, "入场 / 出场 / 循环")
        self.combo(form, "入场动画", ANIMATIONS, lambda: style.get("animation_in", "none"), lambda v: style.update(animation_in=v))
        self.combo(form, "出场动画", ANIMATIONS, lambda: style.get("animation_out", "none"), lambda v: style.update(animation_out=v))
        self.combo(form, "循环动画", {"none": "无", "pulse": "呼吸", "spin": "旋转"}, lambda: style.get("animation_loop", "none"), lambda v: style.update(animation_loop=v))
        self.double(form, "动画时长", 0.1, 5, lambda: style.get("animation_duration", 0.6), lambda v: style.update(animation_duration=v), " 秒")
        if clip.kind != "text":
            form = self.group(layout, "视频特效 / 转场")
            self.combo(form, "特效", EFFECTS, lambda: clip.effects.get("effect", "none"), lambda v: clip.effects.update(effect=v))
            self.slider(form, "特效强度", 0, 100, lambda: clip.effects.get("effect_intensity", 60), lambda v: clip.effects.update(effect_intensity=v))
            transition = clip.effects.setdefault("transition", {})
            self.combo(form, "进入转场（与前段重叠）", TRANSITIONS, lambda: transition.get("kind", "none"), lambda v: transition.update(kind=v))
            self.double(form, "转场时长", 0.1, 3, lambda: transition.get("duration", 0.6), lambda v: transition.update(duration=v), " 秒")
            self.slider(form, "运动模糊", 0, 12, lambda: clip.effects.get("motion_blur", 0), lambda v: clip.effects.update(motion_blur=int(v)), 1, " 帧")
        layout.addStretch()
        return widget

    def build_keyframes(self) -> QWidget:
        widget, layout = self.page()
        clip = self.clip
        assert clip
        form = self.group(layout, "添加 / 编辑关键帧")
        properties = {"x": "水平位置", "y": "垂直位置", "scale": "缩放", "rotation": "旋转", "opacity": "不透明度"}
        if clip.audible:
            properties["volume"] = "音量"
        prop = QComboBox()
        for key, label in properties.items():
            prop.addItem(label, key)
        form.addRow("属性", prop)
        easing = QComboBox()
        easing.addItem("线性", "linear")
        easing.addItem("缓入缓出", "ease_in_out")
        easing.addItem("缓出", "ease_out")
        form.addRow("插值", easing)
        add = QPushButton("在当前播放头添加 / 更新")
        form.addRow(add)
        frames_list = QListWidget()
        form.addRow(frames_list)
        def rebuild() -> None:
            frames_list.clear()
            for key in clip.keyframes:
                for index, frame in enumerate(clip.keyframes[key]):
                    frames_list.addItem(f"{properties.get(key, key)}  {frame['time']:.3f}s  =  {frame['value']:.3f}")
                    frames_list.item(frames_list.count() - 1).setData(Qt.ItemDataRole.UserRole, (key, index))
        def current_value(key: str) -> float:
            if key == "opacity":
                return clip.opacity
            if key == "volume":
                return clip.volume
            return float(clip.effects.get(key, 1.0 if key == "scale" else 0.0))
        def add_frame() -> None:
            if not clip.start <= self.playhead <= clip.end:
                self.playheadRequested.emit()
                return
            key = prop.currentData()
            local = max(0.0, self.playhead - clip.start)
            frames = clip.keyframes.setdefault(key, [])
            frame = next((f for f in frames if abs(f["time"] - local) < 1.0 / self.project.fps), None)
            if frame:
                frame.update(value=current_value(key), easing=easing.currentData())
            else:
                frames.append({"time": local, "value": current_value(key), "easing": easing.currentData()})
            self.mutate(rebuild)
        add.clicked.connect(add_frame)
        remove = QPushButton("删除选中关键帧")
        def remove_frame() -> None:
            data = frames_list.currentData()
            if data:
                key, index = data
                clip.keyframes[key].pop(index)
                self.mutate(rebuild)
        remove.clicked.connect(remove_frame)
        form.addRow(remove)
        rebuild()
        note = QLabel("先移动播放头，再改变参数并点击添加。关键帧以片段起点为时间基准，分割片段时会自动拆分。")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        layout.addWidget(note)
        layout.addStretch()
        return widget
