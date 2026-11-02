from __future__ import annotations

from typing import Any


FILTERS: dict[str, dict[str, Any]] = {
    "none": {"name": "原始", "category": "基础"},
    "clear": {"name": "清透", "category": "基础", "adjust": {"brightness": 5, "contrast": 8, "saturation": 8}},
    "portrait": {"name": "自然肤色", "category": "人像", "adjust": {"brightness": 4, "temperature": 6, "saturation": -4}},
    "soft": {"name": "柔光", "category": "人像", "adjust": {"brightness": 8, "contrast": -12, "saturation": -5}},
    "cinema": {"name": "电影", "category": "风格", "adjust": {"contrast": 18, "saturation": -12, "temperature": -8, "vignette": 25}},
    "teal_orange": {"name": "青橙", "category": "风格", "wheels": {"shadows": [-0.08, 0.04, 0.09], "highlights": [0.09, 0.025, -0.07]}},
    "vintage": {"name": "旧胶片", "category": "风格", "adjust": {"contrast": -12, "saturation": -25, "temperature": 14, "grain": 18}},
    "bw": {"name": "黑白", "category": "单色", "adjust": {"saturation": -100, "contrast": 12}},
    "cool": {"name": "冷调", "category": "基础", "adjust": {"temperature": -24, "saturation": 5}},
    "warm": {"name": "暖阳", "category": "基础", "adjust": {"temperature": 24, "brightness": 4}},
    "vivid": {"name": "高饱和", "category": "基础", "adjust": {"saturation": 32, "contrast": 8}},
}

EFFECTS = {
    "none": "无特效",
    "blur": "柔焦模糊",
    "pixelate": "像素化",
    "glitch": "数字故障",
    "rgb": "RGB 分离",
    "grain": "胶片颗粒",
    "vignette": "暗角",
    "mirror": "镜像",
    "edge": "霓虹描边",
    "dream": "梦境柔光",
}

TRANSITIONS = {
    "none": "无转场",
    "dissolve": "叠化",
    "fade_black": "淡入淡出",
    "slide_left": "向左滑动",
    "slide_right": "向右滑动",
    "wipe": "线性擦除",
    "zoom": "缩放",
}

ANIMATIONS = {
    "none": "无",
    "fade": "渐显 / 渐隐",
    "slide_left": "左侧滑动",
    "slide_right": "右侧滑动",
    "slide_up": "向上滑动",
    "zoom_in": "放大进入",
    "zoom_out": "缩小进入",
    "typewriter": "逐字出现",
    "pulse": "呼吸循环",
    "spin": "旋转循环",
}

TEXT_TEMPLATES = {
    "plain": {"name": "简洁白字", "color": "#FFFFFF", "stroke_width": 2, "template": "plain"},
    "title": {"name": "醒目的标题", "color": "#FFFFFF", "size": 78, "bold": True, "stroke_width": 0, "template": "gradient"},
    "caption": {"name": "底部字幕", "color": "#FFFFFF", "size": 42, "background": "#00000088", "template": "plain"},
    "neon": {"name": "霓虹花字", "color": "#74F7E1", "stroke_color": "#A855F7", "stroke_width": 5, "template": "neon"},
    "news": {"name": "新闻标题", "color": "#FFFFFF", "background": "#DC2626DD", "size": 46, "bold": True, "template": "plain"},
    "handwritten": {"name": "手写便签", "color": "#2D3748", "background": "#FFF4B8EE", "size": 48, "template": "paper"},
}

VOICE_EFFECTS = {
    "none": "原声",
    "chipmunk": "花栗鼠",
    "deep": "低沉",
    "child": "童声",
    "robot": "机器人",
    "echo": "空间回声",
    "radio": "电台",
}

MATERIAL_CATEGORIES = ["滤镜", "特效", "转场", "花字", "贴纸", "音乐", "音效"]
