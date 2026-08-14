"""ffprobe wrapper: the handful of facts the graph builder needs about the
source before it can lay out a single filter.

MoviePy's VideoFileClip used to answer these (``.w``, ``.h``, ``.duration``,
``.fps``) by opening the container itself. ffmpeg needs the same numbers
handed to it up front, since the filter graph (panel position, fade timings,
subliminal frame indices) is built once in Python before ffmpeg ever runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .download import TitleMakerError


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool
    #: Needed for the naive asetrate/aresample speed trick (see graph.py) -
    #: None when there is no audio stream to read a rate from.
    sample_rate: int | None


def _ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if path is None:
        raise TitleMakerError(
            "ffprobe was not found on PATH. It ships with ffmpeg - see the "
            "install hint printed for --help."
        )
    return path


def probe(path: Path) -> VideoInfo:
    args = [
        _ffprobe_path(),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise TitleMakerError(f"ffprobe failed on {path}: {exc.stderr.strip()}") from exc
    except OSError as exc:
        raise TitleMakerError(f"Could not run ffprobe: {exc}") from exc

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise TitleMakerError(f"{path} has no video stream.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    sample_rate = int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None

    width = int(video["width"])
    height = int(video["height"])

    # r_frame_rate is a "num/den" string; avg_frame_rate is preferred when the
    # container tags a variable rate, since r_frame_rate can then read as the
    # stream's time-base rather than its real cadence.
    rate_str = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        fps = float(Fraction(rate_str))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    if not fps:
        rate_str = video.get("r_frame_rate", "24/1")
        fps = float(Fraction(rate_str)) if rate_str != "0/0" else 24.0

    duration_str = data.get("format", {}).get("duration") or video.get("duration")
    if duration_str is None:
        raise TitleMakerError(f"Could not determine the duration of {path}.")
    duration = float(duration_str)

    return VideoInfo(
        width=width, height=height, duration=duration, fps=fps,
        has_audio=audio is not None, sample_rate=sample_rate,
    )
