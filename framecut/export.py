from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .audio import mix_to_file
from .media import executable
from .model import Project
from .render import FrameRenderer


ENCODERS = {
    "H.264（兼容性最佳）": "libx264",
    "H.265（更小体积）": "libx265",
    "AV1（新一代编码）": "libsvtav1",
    "H.264 NVIDIA": "h264_nvenc",
    "H.264 Intel QSV": "h264_qsv",
    "H.264 AMD": "h264_amf",
}


class ExportWorker(QThread):
    progressed = Signal(int, str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, output_path: str, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.output_path = output_path
        self.settings = settings
        self.process: subprocess.Popen | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def run(self) -> None:
        temporary = Path(tempfile.mkdtemp(prefix="framecut_export_", dir=str(Path(self.output_path).parent)))
        try:
            self.progressed.emit(1, "正在混合音频…")
            audio_path = str(temporary / "mix.wav")
            mix_to_file(self.project, audio_path, progress=lambda value: self.progressed.emit(1 + int(value * 7), "正在混合音频…"))
            if self.cancelled:
                return
            width, height = int(self.settings["width"]), int(self.settings["height"])
            fps = float(self.settings["fps"])
            encoder = self.settings.get("encoder", "libx264")
            hdr = any(c.effects.get("hdr", {}).get("enabled") for c in self.project.clips)
            if hdr and encoder not in {"libx265", "libsvtav1"}:
                encoder = "libx265"
                self.progressed.emit(9, "HDR10 导出已切换为 10-bit H.265")
            args = [
                executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-s:v", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
                "-i", audio_path,
            ]
            post_filters = []
            if any(c.effects.get("interpolate") for c in self.project.clips):
                post_filters.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
            if any(c.effects.get("stabilize") for c in self.project.clips):
                post_filters.append("deshake=rx=32:ry=32")
            if any(c.effects.get("quality", {}).get("deflicker") for c in self.project.clips):
                post_filters.append("deflicker=mode=pm:size=5")
            if post_filters:
                args += ["-vf", ",".join(post_filters)]
            args += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", encoder]
            if encoder in {"libx264", "libx265"}:
                args += ["-preset", self.settings.get("preset", "medium"), "-crf", str(self.settings.get("crf", 20))]
            elif encoder == "libsvtav1":
                args += ["-preset", "7", "-crf", str(self.settings.get("crf", 28))]
            else:
                args += ["-b:v", self.settings.get("video_bitrate", "12M")]
            args += ["-pix_fmt", "yuv420p10le" if hdr else "yuv420p", "-c:a", "aac", "-b:a", self.settings.get("audio_bitrate", "192k"), "-ar", str(self.project.sample_rate)]
            if hdr:
                args += ["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"]
                if encoder == "libx265":
                    args += ["-tag:v", "hvc1"]
            args += ["-shortest", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", self.output_path]
            startup = None
            if os.name == "nt":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startup)
            errors: list[bytes] = []
            progress_lines: list[str] = []

            def read_stdout() -> None:
                if self.process and self.process.stdout:
                    progress_lines.extend(self.process.stdout.read().decode("utf-8", errors="replace").splitlines())

            def read_stderr() -> None:
                if self.process and self.process.stderr:
                    errors.append(self.process.stderr.read())

            stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()
            renderer = FrameRenderer(self.project)
            total_frames = max(1, int(round(self.project.duration * fps)))
            last_percent = -1
            try:
                for index in range(total_frames):
                    if self.cancelled:
                        break
                    frame = renderer.render(index / fps, width, height)
                    self.process.stdin.write(frame.tobytes())
                    percent = 10 + int((index + 1) / total_frames * 89)
                    if percent != last_percent:
                        self.progressed.emit(percent, f"正在渲染第 {index + 1} / {total_frames} 帧")
                        last_percent = percent
                self.process.stdin.close()
                code = self.process.wait(timeout=120)
            except (BrokenPipeError, OSError):
                code = self.process.wait(timeout=30)
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            if self.cancelled:
                Path(self.output_path).unlink(missing_ok=True)
                return
            if code != 0:
                message = errors[-1].decode("utf-8", errors="replace") if errors else "FFmpeg 编码失败"
                raise RuntimeError(message.strip())
            self.progressed.emit(100, "导出完成")
            self.completed.emit(self.output_path)
        except Exception as error:
            if not self.cancelled:
                self.failed.emit(str(error))
        finally:
            renderer = locals().get("renderer")
            if renderer:
                renderer.cache.close()
            for path in temporary.iterdir():
                path.unlink(missing_ok=True)
            temporary.rmdir()
