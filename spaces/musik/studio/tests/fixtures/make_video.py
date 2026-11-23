"""Kurze Testvideos per ffmpeg, damit Videotests ohne echte Dateien laufen."""

import subprocess
from pathlib import Path

from djset.render.stretch import ffmpeg_path


def kurzes_video(
    tmp_path: Path, sekunden: float = 2.5, fps: int = 25, mit_ton: bool = True
) -> Path:
    """Ein `testsrc`-Video mit optionaler Sinusspur (`mit_ton=False`: ohne Tonspur)."""
    ziel = tmp_path / ("video.mp4" if mit_ton else "video_stumm.mp4")
    cmd = [
        ffmpeg_path(), "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={sekunden}:size=160x120:rate={fps}",
    ]
    if mit_ton:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={sekunden}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if mit_ton:
        cmd += ["-c:a", "aac"]
    cmd.append(str(ziel))
    subprocess.run(cmd, check=True, capture_output=True)
    return ziel
