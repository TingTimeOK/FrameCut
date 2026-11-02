from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .catalog import FILTERS
from .media import MediaCache
from .model import Clip, Project, text_style


def color_tuple(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = (value or "#FFFFFF").lstrip("#")
    if len(value) == 8:
        return int(value[2:4], 16), int(value[4:6], 16), int(value[0:2], 16), int(value[6:8], 16)
    if len(value) == 6:
        return int(value[4:6], 16), int(value[2:4], 16), int(value[0:2], 16), alpha
    return 255, 255, 255, alpha


def pil_color(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    b, g, r, a = color_tuple(value, alpha)
    return r, g, b, a


def clamp(image: np.ndarray) -> np.ndarray:
    return np.clip(image, 0, 255).astype(np.uint8)


def smoothstep(value: float) -> float:
    value = min(max(value, 0.0), 1.0)
    return value * value * (3.0 - 2.0 * value)


class FrameRenderer:
    def __init__(self, project: Project, cache: MediaCache | None = None) -> None:
        self.project = project
        self.cache = cache or MediaCache()
        self.matting_masks: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()
        self.luts: dict[str, np.ndarray] = {}
        self.reference_stats: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.fonts: dict[tuple[str, int, bool], ImageFont.FreeTypeFont] = {}

    def render(self, seconds: float, width: int | None = None, height: int | None = None) -> np.ndarray:
        width = width or self.project.width
        height = height or self.project.height
        canvas = np.zeros((height, width, 4), dtype=np.uint8)
        canvas[:, :] = color_tuple(self.project.background)
        clips = sorted(self.project.clips_at(seconds, visual_only=True), key=lambda c: (c.track, c.start))
        for clip in clips:
            if clip.track >= len(self.project.tracks):
                continue
            track = self.project.tracks[clip.track]
            if not track.visible:
                continue
            layer = self.render_clip(clip, seconds, width, height)
            if layer is None:
                continue
            opacity = clip.opacity * clip.value_at("opacity", seconds - clip.start, 1.0)
            transition_opacity, wipe = self._transition_state(clip, seconds - clip.start, width, height)
            opacity *= transition_opacity * self._animation_state(clip, seconds - clip.start)[0]
            if wipe is not None:
                layer[:, :, 3] = clamp(layer[:, :, 3].astype(np.float32) * wipe)
            canvas = self.blend(canvas, layer, clip.blend_mode, opacity)
        return canvas[:, :, :3]

    def render_clip(self, clip: Clip, seconds: float, width: int, height: int) -> np.ndarray | None:
        local = seconds - clip.start
        if clip.kind == "text":
            return self.render_text(clip, local, width, height)
        if clip.kind == "color":
            source = np.zeros((max(2, height // 2), max(2, width // 2), 3), dtype=np.uint8)
            source[:, :] = color_tuple(clip.style.get("color", "#202020"))[:3]
        elif clip.kind in {"image", "sticker"}:
            source = self.cache.image(clip.path)
        else:
            source = self.cache.frame(clip.path, clip.source_time(seconds), self.project.fps)
            blur = int(clip.effects.get("motion_blur", 0))
            if blur > 0:
                earlier = self.cache.frame(clip.path, max(0.0, clip.source_time(seconds) - blur / self.project.fps), self.project.fps)
                if earlier is not None and earlier.shape == source.shape:
                    source = cv2.addWeighted(source, 0.65, earlier, 0.35, 0)
        if source is None:
            return None
        if len(source.shape) == 2:
            source = cv2.cvtColor(source, cv2.COLOR_GRAY2BGR)
        source = self.crop(source, clip.effects.get("crop"))
        if source.size == 0:
            return None
        original_alpha = source[:, :, 3].copy() if source.ndim == 3 and source.shape[2] == 4 else None
        source = source[:, :, :3]
        source = self.apply_adjustments(source, clip)
        source = self.apply_video_effect(source, clip, local)
        source = self.apply_relight_hdr(source, clip)
        bgra = cv2.cvtColor(source, cv2.COLOR_BGR2BGRA)
        alpha = self.apply_alpha(bgra[:, :, :3], clip, seconds)
        if original_alpha is not None:
            alpha = clamp(alpha.astype(np.float32) * original_alpha.astype(np.float32) / 255.0)
        bgra[:, :, 3] = alpha
        bgra = self.transform(bgra, clip, local, width, height)
        if clip.effects.get("handwrite_reveal"):
            reveal = min(1.0, local / max(0.1, float(clip.effects.get("handwrite_reveal"))))
            bgra[:, int(width * reveal):, 3] = 0
        return bgra

    def crop(self, source: np.ndarray, crop: dict[str, Any] | None) -> np.ndarray:
        if not crop or not crop.get("enabled", True):
            return source
        height, width = source.shape[:2]
        left = int(np.clip(float(crop.get("left", 0.0)), 0.0, 0.95) * width)
        right = int(np.clip(float(crop.get("right", 0.0)), 0.0, 0.95) * width)
        top = int(np.clip(float(crop.get("top", 0.0)), 0.0, 0.95) * height)
        bottom = int(np.clip(float(crop.get("bottom", 0.0)), 0.0, 0.95) * height)
        return source[top:height - bottom, left:width - right].copy()

    def apply_adjustments(self, source: np.ndarray, clip: Clip) -> np.ndarray:
        values = dict(clip.adjust)
        preset = FILTERS.get(clip.filter_name, {})
        strength = min(max(clip.filter_strength, 0.0), 1.0)
        for key, value in preset.get("adjust", {}).items():
            values[key] = values.get(key, 0.0) + value * strength
        wheel_preset = {"wheels": {name: [value * strength for value in values_] for name, values_ in preset.get("wheels", {}).items()}}
        image = source.astype(np.float32)
        exposure = 2.0 ** (values.get("exposure", 0.0) / 100.0)
        contrast = 1.0 + values.get("contrast", 0.0) / 100.0
        image = (image - 127.5) * contrast + 127.5 + values.get("brightness", 0.0) * 1.6
        image *= exposure
        image[:, :, 2] += values.get("temperature", 0.0) * 0.55 + values.get("tint", 0.0) * 0.18
        image[:, :, 0] -= values.get("temperature", 0.0) * 0.55
        image[:, :, 1] -= values.get("tint", 0.0) * 0.35
        luminance = cv2.cvtColor(clamp(image), cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        shadows = np.clip((0.5 - luminance) * 2.0, 0.0, 1.0)
        highlights = np.clip((luminance - 0.5) * 2.0, 0.0, 1.0)
        image += shadows[:, :, None] * values.get("shadows", 0.0) + highlights[:, :, None] * values.get("highlights", 0.0)
        image = clamp(image)
        saturation = values.get("saturation", 0.0)
        hue = clip.hsl.get("hue", 0.0)
        lightness = clip.hsl.get("lightness", 0.0)
        if saturation or hue or lightness:
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 0] = np.mod(hsv[:, :, 0] + hue / 2.0, 180.0)
            hsv[:, :, 1] *= max(0.0, 1.0 + (saturation + clip.hsl.get("saturation", 0.0)) / 100.0)
            hsv[:, :, 2] *= max(0.0, 1.0 + lightness / 100.0)
            image = cv2.cvtColor(clamp(hsv), cv2.COLOR_HSV2BGR)
        image = self.apply_hsl_colors(image, clip.effects.get("hsl_colors", {}))
        if clip.curves and len(clip.curves) >= 2:
            points = sorted(clip.curves)
            lut = np.interp(np.arange(256), [p[0] * 255 for p in points], [p[1] * 255 for p in points]).astype(np.uint8)
            image = cv2.LUT(image, lut)
        for channel, key in enumerate(("curves_b", "curves_g", "curves_r")):
            points = sorted(clip.effects.get(key, []))
            if len(points) >= 2:
                lut = np.interp(np.arange(256), [p[0] * 255 for p in points], [p[1] * 255 for p in points]).astype(np.uint8)
                image[:, :, channel] = cv2.LUT(image[:, :, channel], lut)
        image = self.apply_wheels(image, clip, wheel_preset)
        quality = clip.effects.get("quality", {})
        if quality.get("denoise", 0):
            image = cv2.bilateralFilter(image, 5, 15 + quality["denoise"] * 0.5, 15 + quality["denoise"] * 0.5)
        sharpen = values.get("sharpen", 0.0) + quality.get("sharpen", 0.0)
        if sharpen:
            blurred = cv2.GaussianBlur(image, (0, 0), 1.2)
            image = cv2.addWeighted(image, 1.0 + sharpen / 100.0, blurred, -sharpen / 100.0, 0)
        if clip.effects.get("color_match"):
            image = self.apply_color_match(image, clip.effects["color_match"])
        if clip.effects.get("lut_path"):
            image = self.apply_lut(image, clip.effects["lut_path"])
        vignette = values.get("vignette", 0.0)
        if vignette:
            yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
            distance = ((xx / image.shape[1] - 0.5) ** 2 + (yy / image.shape[0] - 0.5) ** 2) * 2.0
            mask = 1.0 - np.clip(distance, 0, 1) * vignette / 100.0
            image = clamp(image.astype(np.float32) * mask[:, :, None])
        if values.get("grain", 0.0):
            rng = np.random.default_rng(23)
            noise = rng.normal(0, values["grain"] * 0.16, image.shape[:2])[:, :, None]
            image = clamp(image.astype(np.float32) + noise)
        return image

    def apply_hsl_colors(self, image: np.ndarray, settings: dict[str, list[float]]) -> np.ndarray:
        if not settings:
            return image
        ranges = {"red": (0, 8), "orange": (8, 20), "yellow": (20, 38), "green": (38, 85), "cyan": (85, 105), "blue": (105, 135), "purple": (135, 165), "magenta": (165, 180)}
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
        for name, values in settings.items():
            if name not in ranges:
                continue
            low, high = ranges[name]
            mask = (hsv[:, :, 0] >= low) & (hsv[:, :, 0] < high)
            hsv[:, :, 0][mask] = np.mod(hsv[:, :, 0][mask] + values[0] / 2.0, 180)
            hsv[:, :, 1][mask] *= 1.0 + values[1] / 100.0
            hsv[:, :, 2][mask] *= 1.0 + values[2] / 100.0
        return cv2.cvtColor(clamp(hsv), cv2.COLOR_HSV2BGR)

    def apply_wheels(self, image: np.ndarray, clip: Clip, preset: dict[str, Any]) -> np.ndarray:
        wheels = {name: list(values) for name, values in clip.wheels.items()}
        for name, values in preset.get("wheels", {}).items():
            wheels[name] = [a + b for a, b in zip(wheels.get(name, [0, 0, 0]), values)]
        if not any(any(abs(v) > 0.001 for v in values) for values in wheels.values()):
            return image
        luminance = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        masks = {
            "shadows": np.clip(1.0 - luminance * 2.0, 0.0, 1.0),
            "midtones": np.clip(1.0 - np.abs(luminance - 0.5) * 2.0, 0.0, 1.0),
            "highlights": np.clip(luminance * 2.0 - 1.0, 0.0, 1.0),
        }
        result = image.astype(np.float32)
        for name, values in wheels.items():
            rgb = np.array([values[2], values[1], values[0]], dtype=np.float32) * 100.0
            result += masks.get(name, 0)[:, :, None] * rgb
        return clamp(result)

    def apply_color_match(self, image: np.ndarray, settings: dict[str, Any]) -> np.ndarray:
        path = settings.get("path", "")
        if not path:
            return image
        if path not in self.reference_stats:
            reference = self.cache.image(path)
            if reference is None:
                reference = self.cache.frame(path, float(settings.get("time", 0.0)))
            if reference is None:
                return image
            lab = cv2.cvtColor(reference[:, :, :3], cv2.COLOR_BGR2LAB).astype(np.float32)
            self.reference_stats[path] = lab.mean(axis=(0, 1)), lab.std(axis=(0, 1)) + 1e-5
        mean, std = self.reference_stats[path]
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
        current_mean, current_std = lab.mean(axis=(0, 1)), lab.std(axis=(0, 1)) + 1e-5
        matched = (lab - current_mean) * (std / current_std) + mean
        strength = float(settings.get("strength", 1.0))
        return cv2.cvtColor(clamp(lab * (1.0 - strength) + matched * strength), cv2.COLOR_LAB2BGR)

    def apply_lut(self, image: np.ndarray, path: str) -> np.ndarray:
        if path not in self.luts:
            try:
                size, table = self.read_cube(path)
                self.luts[path] = table.reshape(size, size, size, 3)
            except (OSError, ValueError):
                return image
        table = self.luts[path]
        size = table.shape[0]
        indices = np.clip((image.astype(np.float32) / 255.0 * (size - 1)).round().astype(np.int32), 0, size - 1)
        rgb = table[indices[:, :, 2], indices[:, :, 1], indices[:, :, 0]]
        return clamp(rgb[:, :, ::-1] * 255.0)

    @staticmethod
    def read_cube(path: str) -> tuple[int, np.ndarray]:
        size = 0
        rows = []
        for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("LUT_3D_SIZE"):
                size = int(line.split()[-1])
            elif line and line[0].isdigit():
                parts = line.split()
                if len(parts) >= 3:
                    rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
        if not size or len(rows) != size ** 3:
            raise ValueError("无效 .cube LUT")
        return size, np.asarray(rows, dtype=np.float32)

    def apply_video_effect(self, image: np.ndarray, clip: Clip, local: float) -> np.ndarray:
        effect = clip.effects.get("effect", "none")
        intensity = float(clip.effects.get("effect_intensity", 60.0)) / 100.0
        if effect == "none" or intensity <= 0:
            return image
        height, width = image.shape[:2]
        if effect in {"blur", "dream"}:
            blurred = cv2.GaussianBlur(image, (0, 0), 2.0 + intensity * 8.0)
            return cv2.addWeighted(image, 0.35 if effect == "dream" else 1.0 - intensity, blurred, 0.65 if effect == "dream" else intensity, 8 if effect == "dream" else 0)
        if effect == "pixelate":
            small = cv2.resize(image, (max(2, int(width * (1.0 - intensity * 0.95))), max(2, int(height * (1.0 - intensity * 0.95)))), interpolation=cv2.INTER_AREA)
            return cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)
        if effect in {"rgb", "glitch"}:
            shift = max(1, int(width * intensity * (0.035 if effect == "rgb" else 0.07)))
            result = image.copy()
            result[:, :, 0] = np.roll(image[:, :, 0], -shift, axis=1)
            result[:, :, 2] = np.roll(image[:, :, 2], shift, axis=1)
            if effect == "glitch":
                for row in range(0, height, max(8, height // 12)):
                    offset = int(math.sin(local * 19.0 + row) * shift * 2)
                    result[row:row + 4] = np.roll(result[row:row + 4], offset, axis=1)
            return result
        if effect == "grain":
            rng = np.random.default_rng(int(local * self.project.fps) + 17)
            noise = rng.normal(0, intensity * 20.0, image.shape[:2])[:, :, None]
            return clamp(image.astype(np.float32) + noise)
        if effect == "vignette":
            temp = Clip(path=clip.path, kind=clip.kind, name=clip.name, track=clip.track, start=0, duration=1)
            temp.adjust["vignette"] = intensity * 100
            return self.apply_adjustments(image, temp)
        if effect == "mirror":
            return cv2.addWeighted(image, 1.0 - intensity, cv2.flip(image, 1), intensity, 0)
        if effect == "edge":
            edges = cv2.Canny(image, 70, 150)
            colored = np.zeros_like(image)
            colored[:, :, 1] = edges
            colored[:, :, 2] = edges
            return cv2.addWeighted(image, 1.0 - intensity * 0.5, colored, intensity, 0)
        return image

    def apply_relight_hdr(self, image: np.ndarray, clip: Clip) -> np.ndarray:
        relight = clip.effects.get("relight", {})
        if relight.get("enabled"):
            height, width = image.shape[:2]
            x, y = relight.get("x", 0.25), relight.get("y", 0.2)
            yy, xx = np.mgrid[0:height, 0:width]
            distance = np.sqrt((xx / width - x) ** 2 + (yy / height - y) ** 2)
            light = np.clip(1.0 - distance / max(0.05, relight.get("radius", 0.8)), 0.0, 1.0)
            color = np.array(color_tuple(relight.get("color", "#FFE1B0"))[:3], dtype=np.float32) / 255.0
            amount = relight.get("intensity", 35.0) / 100.0
            image = clamp(image.astype(np.float32) + light[:, :, None] * color * amount * 180.0)
        hdr = clip.effects.get("hdr", {})
        if hdr.get("enabled"):
            strength = hdr.get("intensity", 30.0) / 100.0
            normalized = image.astype(np.float32) / 255.0
            mapped = 1.0 - np.exp(-normalized * (1.0 + strength * 2.0))
            mapped = np.power(np.clip(mapped, 0, 1), 1.0 / (1.0 + strength))
            image = clamp((normalized * (1.0 - strength) + mapped * strength) * 255.0)
        return image

    def apply_alpha(self, source: np.ndarray, clip: Clip, seconds: float) -> np.ndarray:
        height, width = source.shape[:2]
        alpha = np.full((height, width), 255, dtype=np.uint8)
        chroma = clip.effects.get("chroma", {})
        if chroma.get("enabled"):
            hsv = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)
            target = float(chroma.get("hue", 60.0)) / 2.0
            tolerance = max(1.0, chroma.get("tolerance", 35.0) / 2.0)
            difference = np.minimum(np.abs(hsv[:, :, 0].astype(np.float32) - target), 180.0 - np.abs(hsv[:, :, 0].astype(np.float32) - target))
            saturation_gate = np.clip((hsv[:, :, 1].astype(np.float32) - 25.0) / 50.0, 0.0, 1.0)
            alpha = clamp(np.clip((difference - tolerance) / max(2.0, tolerance * 0.5), 0.0, 1.0) * 255.0 * saturation_gate + 255.0 * (1.0 - saturation_gate))
            smooth = int(chroma.get("smooth", 4))
            if smooth:
                alpha = cv2.GaussianBlur(alpha, (0, 0), smooth / 2.0)
        matting = clip.effects.get("matting", {})
        if matting.get("enabled"):
            key = (clip.id, round(seconds * self.project.fps))
            if key not in self.matting_masks:
                small_scale = min(1.0, 480.0 / max(height, width))
                small = cv2.resize(source, (max(16, int(width * small_scale)), max(16, int(height * small_scale))))
                rect = matting.get("rect", [0.08, 0.04, 0.84, 0.92])
                sh, sw = small.shape[:2]
                box = (max(0, int(rect[0] * sw)), max(0, int(rect[1] * sh)), min(sw - 1, int(rect[2] * sw)), min(sh - 1, int(rect[3] * sh)))
                mask = np.zeros((sh, sw), dtype=np.uint8)
                bg_model, fg_model = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
                try:
                    cv2.grabCut(small, mask, box, bg_model, fg_model, 2, cv2.GC_INIT_WITH_RECT)
                    binary = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
                    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
                    binary = cv2.GaussianBlur(binary, (0, 0), 1.2)
                except cv2.error:
                    binary = np.full((sh, sw), 255, dtype=np.uint8)
                self.matting_masks[key] = binary
                if len(self.matting_masks) > 12:
                    self.matting_masks.popitem(last=False)
            matte = cv2.resize(self.matting_masks[key], (width, height), interpolation=cv2.INTER_LINEAR)
            alpha = np.minimum(alpha, matte)
        mask = clip.effects.get("mask", {})
        if mask.get("enabled"):
            shape_mask = self.mask_array(mask, width, height)
            alpha = clamp(alpha.astype(np.float32) * shape_mask)
        radius = int(clip.effects.get("round_radius", 0.0) * min(width, height))
        if radius > 0:
            rounded = np.zeros((height, width), dtype=np.uint8)
            cv2.rectangle(rounded, (radius, 0), (width - radius, height), 255, -1)
            cv2.rectangle(rounded, (0, radius), (width, height - radius), 255, -1)
            for center in ((radius, radius), (width - radius, radius), (radius, height - radius), (width - radius, height - radius)):
                cv2.circle(rounded, center, radius, 255, -1)
            alpha = clamp(alpha.astype(np.float32) * rounded.astype(np.float32) / 255.0)
        return alpha

    def mask_array(self, mask: dict[str, Any], width: int, height: int) -> np.ndarray:
        result = np.zeros((height, width), dtype=np.uint8)
        cx, cy = int(mask.get("x", 0.5) * width), int(mask.get("y", 0.5) * height)
        w, h = max(2, int(mask.get("width", 0.8) * width)), max(2, int(mask.get("height", 0.8) * height))
        shape = mask.get("shape", "rectangle")
        if shape == "ellipse":
            cv2.ellipse(result, (cx, cy), (w // 2, h // 2), mask.get("rotation", 0.0), 0, 360, 255, -1)
        elif shape in {"linear", "mirror"}:
            yy, xx = np.mgrid[0:height, 0:width]
            angle = math.radians(mask.get("rotation", 0.0))
            coordinate = (xx - cx) * math.cos(angle) + (yy - cy) * math.sin(angle)
            if shape == "linear":
                result = np.where(coordinate > 0, 255, 0).astype(np.uint8)
            else:
                result = np.where(np.abs(coordinate) < w / 2, 255, 0).astype(np.uint8)
        else:
            cv2.rectangle(result, (cx - w // 2, cy - h // 2), (cx + w // 2, cy + h // 2), 255, -1)
        if mask.get("invert"):
            result = 255 - result
        feather = float(mask.get("feather", 0.0))
        if feather:
            result = cv2.GaussianBlur(result, (0, 0), max(0.5, feather / 100.0 * min(width, height) / 4.0))
        return result.astype(np.float32) / 255.0

    def transform(self, source: np.ndarray, clip: Clip, local: float, width: int, height: int) -> np.ndarray:
        sh, sw = source.shape[:2]
        base = min(width / sw, height / sh)
        _, animation_scale, animation_x, animation_y, animation_rotation = self._animation_state(clip, local)
        scale = clip.value_at("scale", local, clip.effects.get("scale", 1.0)) * animation_scale
        sx = clip.value_at("scale_x", local, clip.effects.get("scale_x", 1.0))
        sy = clip.value_at("scale_y", local, clip.effects.get("scale_y", 1.0))
        if clip.effects.get("mirror_horizontal"):
            sx *= -1
        if clip.effects.get("mirror_vertical"):
            sy *= -1
        rotation = clip.value_at("rotation", local, clip.effects.get("rotation", 0.0)) + animation_rotation
        x = clip.value_at("x", local, clip.effects.get("x", 0.0)) + animation_x
        y = clip.value_at("y", local, clip.effects.get("y", 0.0)) + animation_y
        transition = clip.effects.get("transition", {})
        transition_duration = float(transition.get("duration", 0.6))
        if transition.get("kind", "none") != "none" and 0 <= local < transition_duration:
            progress = smoothstep(local / max(0.05, transition_duration))
            if transition["kind"] == "slide_left":
                x += 1.0 - progress
            elif transition["kind"] == "slide_right":
                x -= 1.0 - progress
            elif transition["kind"] == "zoom":
                scale *= 0.55 + progress * 0.45
        target_id = clip.effects.get("tracking_target")
        target = self.project.clip(target_id)
        if target:
            x += target.value_at("track_x", local, 0.0)
            y += target.value_at("track_y", local, 0.0)
        matrix = cv2.getRotationMatrix2D((sw / 2.0, sh / 2.0), rotation, base * scale)
        matrix[:, 0] *= sx
        matrix[:, 1] *= sy
        matrix[0, 2] += width * (0.5 + x) - sw / 2.0
        matrix[1, 2] += height * (0.5 + y) - sh / 2.0
        corners = clip.effects.get("corner_pin")
        if corners and len(corners) == 4:
            original = np.float32([[0, 0], [sw - 1, 0], [sw - 1, sh - 1], [0, sh - 1]])
            destination = np.float32([[p[0] * width, p[1] * height] for p in corners])
            matrix = cv2.getPerspectiveTransform(original, destination)
            return cv2.warpPerspective(source, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        return cv2.warpAffine(source, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

    def _transition_state(self, clip: Clip, local: float, width: int, height: int) -> tuple[float, np.ndarray | None]:
        transition = clip.effects.get("transition", {})
        kind = transition.get("kind", "none")
        duration = float(transition.get("duration", 0.6))
        if kind == "none" or local >= duration or local < 0:
            return 1.0, None
        progress = smoothstep(local / max(0.05, duration))
        if kind in {"dissolve", "fade_black", "zoom"}:
            return progress, None
        if kind == "wipe":
            xx = np.linspace(0, 1, width, dtype=np.float32)[None, :]
            wipe = np.repeat(np.clip((progress - xx) * 20.0 + 1.0, 0.0, 1.0), height, axis=0)
            return 1.0, wipe
        if kind in {"slide_left", "slide_right"}:
            direction = -1 if kind == "slide_left" else 1
            offset = int(width * direction * (1.0 - progress))
            wipe = np.ones((height, width), dtype=np.float32)
            if offset:
                wipe = np.roll(wipe, offset, axis=1)
            return progress, None
        return progress, None

    def _animation_state(self, clip: Clip, local: float) -> tuple[float, float, float, float, float]:
        effects = clip.effects if clip.kind != "text" else clip.style
        opacity, scale, x, y, rotation = 1.0, 1.0, 0.0, 0.0, 0.0
        duration = float(effects.get("animation_duration", 0.6))
        states = [(effects.get("animation_in", "none"), local / max(0.05, duration), False)]
        if clip.duration - local < duration:
            states.append((effects.get("animation_out", "none"), (clip.duration - local) / max(0.05, duration), True))
        for kind, raw, outgoing in states:
            progress = smoothstep(raw)
            if kind == "fade":
                opacity *= progress
            elif kind == "slide_left":
                x -= (1.0 - progress) * (0.35 if not outgoing else -0.35)
            elif kind == "slide_right":
                x += (1.0 - progress) * (0.35 if not outgoing else -0.35)
            elif kind == "slide_up":
                y += (1.0 - progress) * 0.3
            elif kind in {"zoom_in", "zoom_out"}:
                start = 0.5 if kind == "zoom_in" else 1.5
                scale *= start + (1.0 - start) * progress
        loop = effects.get("animation_loop", "none")
        if loop == "pulse":
            scale *= 1.0 + math.sin(local * 5.0) * 0.04
        elif loop == "spin":
            rotation += local * 35.0
        return opacity, scale, x, y, rotation

    def blend(self, base: np.ndarray, layer: np.ndarray, mode: str, opacity: float) -> np.ndarray:
        alpha = layer[:, :, 3:4].astype(np.float32) / 255.0 * np.clip(opacity, 0.0, 1.0)
        b = base[:, :, :3].astype(np.float32) / 255.0
        f = layer[:, :, :3].astype(np.float32) / 255.0
        if mode == "multiply":
            mixed = b * f
        elif mode == "screen":
            mixed = 1.0 - (1.0 - b) * (1.0 - f)
        elif mode == "overlay":
            mixed = np.where(b < 0.5, 2.0 * b * f, 1.0 - 2.0 * (1.0 - b) * (1.0 - f))
        elif mode == "add":
            mixed = np.minimum(1.0, b + f)
        elif mode == "darken":
            mixed = np.minimum(b, f)
        elif mode == "lighten":
            mixed = np.maximum(b, f)
        elif mode == "difference":
            mixed = np.abs(b - f)
        else:
            mixed = f
        output = b * (1.0 - alpha) + mixed * alpha
        base[:, :, :3] = clamp(output * 255.0)
        return base

    def font(self, family: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (family, size, bold)
        if key not in self.fonts:
            candidates = []
            if "yahei" in family.lower():
                candidates = ["msyhbd.ttc" if bold else "msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"]
            candidates += [family, "C:/Windows/Fonts/arial.ttf", "arial.ttf"]
            for candidate in candidates:
                try:
                    self.fonts[key] = ImageFont.truetype(candidate, size)
                    break
                except OSError:
                    continue
            else:
                self.fonts[key] = ImageFont.load_default(size=size)
        return self.fonts[key]

    def render_text(self, clip: Clip, local: float, width: int, height: int) -> np.ndarray:
        style = {**text_style(), **clip.style}
        content = clip.text
        if style.get("animation_in") == "typewriter":
            count = int(len(content) * min(1.0, local / max(0.1, style.get("animation_duration", 0.6))))
            content = content[:count]
        scale_factor = max(0.5, width / 1280.0)
        size = max(8, int(style.get("size", 54) * scale_factor))
        font = self.font(style.get("font", "Microsoft YaHei"), size, style.get("bold", False))
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        spacing = max(0, int(size * (style.get("line_spacing", 1.2) - 1.0)))
        box = draw.multiline_textbbox((0, 0), content, font=font, spacing=spacing, align=style.get("align", "center"), stroke_width=int(style.get("stroke_width", 0)))
        tw, th = box[2] - box[0], box[3] - box[1]
        cx, cy = style.get("x", 0.5) * width, style.get("y", 0.82) * height
        x, y = cx - tw / 2.0 - box[0], cy - th / 2.0 - box[1]
        background = style.get("background", "#00000000")
        if background not in {"#00000000", "transparent"}:
            draw.rounded_rectangle((cx - tw / 2 - size * 0.3, cy - th / 2 - size * 0.2, cx + tw / 2 + size * 0.3, cy + th / 2 + size * 0.2), radius=size * 0.15, fill=pil_color(background))
        if style.get("shadow"):
            shadow_position = (x + max(2, size // 18), y + max(2, size // 18))
            draw.multiline_text(shadow_position, content, font=font, fill=pil_color(style.get("shadow_color", "#00000099")), spacing=spacing, align=style.get("align", "center"), stroke_width=int(style.get("stroke_width", 0)), stroke_fill=pil_color(style.get("shadow_color", "#00000099")))
        fill = pil_color(style.get("keyword_color" if any(k in content for k in style.get("keywords", [])) else "color", style.get("color", "#FFFFFF")))
        if style.get("template") == "gradient":
            text_mask = Image.new("L", (width, height), 0)
            mask_draw = ImageDraw.Draw(text_mask)
            mask_draw.multiline_text((x, y), content, font=font, fill=255, spacing=spacing, align=style.get("align", "center"), stroke_width=int(style.get("stroke_width", 0)), stroke_fill=255)
            gradient = np.zeros((height, width, 4), dtype=np.uint8)
            top = np.array(pil_color("#63E6FF"), dtype=np.float32)
            bottom = np.array(pil_color("#A78BFA"), dtype=np.float32)
            for row in range(max(0, int(cy - th / 2)), min(height, int(cy + th / 2) + 1)):
                t = (row - (cy - th / 2)) / max(th, 1)
                gradient[row, :] = (top * (1 - t) + bottom * t).astype(np.uint8)
            image = Image.alpha_composite(image, Image.fromarray(gradient))
            image.putalpha(Image.fromarray(np.minimum(np.asarray(image.getchannel("A")), np.asarray(text_mask))))
            draw = ImageDraw.Draw(image)
        else:
            draw.multiline_text((x, y), content, font=font, fill=fill, spacing=spacing, align=style.get("align", "center"), stroke_width=int(style.get("stroke_width", 0)), stroke_fill=pil_color(style.get("stroke_color", "#000000")))
        if style.get("template") == "neon":
            glow = image.filter(ImageFilter.GaussianBlur(max(2, size // 9)))
            image = Image.alpha_composite(glow, image)
        opacity, scale, ax, ay, rotation = self._animation_state(clip, local)
        array = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGBA2BGRA)
        if rotation or scale != 1.0 or ax or ay:
            center = (cx, cy)
            matrix = cv2.getRotationMatrix2D(center, rotation + style.get("rotation", 0.0), scale * style.get("scale", 1.0))
            matrix[0, 2] += ax * width
            matrix[1, 2] += ay * height
            array = cv2.warpAffine(array, matrix, (width, height), borderMode=cv2.BORDER_CONSTANT)
        target = self.project.clip(clip.effects.get("tracking_target"))
        if target:
            dx = target.value_at("track_x", local, 0.0) * width
            dy = target.value_at("track_y", local, 0.0) * height
            matrix = np.float32([[1, 0, dx], [0, 1, dy]])
            array = cv2.warpAffine(array, matrix, (width, height), borderMode=cv2.BORDER_CONSTANT)
        array[:, :, 3] = clamp(array[:, :, 3].astype(np.float32) * opacity * style.get("opacity", 1.0))
        return array
