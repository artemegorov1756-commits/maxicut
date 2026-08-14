"""Run ffmpeg on the assembled graph, then optionally preview with ffplay.

The Python-side work is done by the time anything here runs: `graph.build()`
has already produced a `BuildResult` describing every input and the whole
-filter_complex string. This module's only job is turning that into a single
ffmpeg subprocess invocation and cleaning up the temp PNGs afterwards.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from .constants import FFMPEG_INSTALL_HINTS
from .download import TitleMakerError
from .graph import BuildResult


def _tool_path(name: str) -> str | None:
    return shutil.which(name)


def ffmpeg_install_hint() -> str:
    return FFMPEG_INSTALL_HINTS.get(platform.system(), FFMPEG_INSTALL_HINTS["Linux"])


def render(
    result: BuildResult,
    output: Path,
    codec: str,
    preset: str | None,
    threads: int | None,
) -> None:
    ffmpeg = _tool_path("ffmpeg")
    if ffmpeg is None:
        raise TitleMakerError(
            f"ffmpeg was not found on PATH.\n      Install it: {ffmpeg_install_hint()}"
        )

    argv = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-stats", "-nostdin"]
    for input_args in result.graph.inputs:
        argv.extend(input_args)

    argv.extend(["-filter_complex", result.graph.filter_complex()])
    argv.extend(["-map", result.video_map])
    if result.audio_map:
        argv.extend(["-map", result.audio_map])

    argv.extend(["-c:v", codec])
    if preset:
        argv.extend(["-preset", preset])
    if result.audio_map:
        argv.extend(["-c:a", "aac"])
    if threads:
        argv.extend(["-threads", str(threads)])
    # yuv420p is what QuickTime, browsers and phones expect (already baked
    # into the graph's final `format` filter); +faststart moves the index to
    # the front so the file streams without a full read first.
    argv.extend(["-movflags", "+faststart"])
    if result.audio_map:
        argv.append("-shortest")
    argv.append(str(output))

    print(f"[4/5] Rendering {output} ({codec}, {result.width}x{result.height})")
    try:
        subprocess.run(argv, check=True)
    except subprocess.CalledProcessError as exc:
        raise TitleMakerError(f"ffmpeg exited with status {exc.returncode}.") from exc
    except OSError as exc:
        raise TitleMakerError(f"Could not run ffmpeg: {exc}") from exc


def cleanup(temp_files: list[Path]) -> None:
    for path in temp_files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def preview(output: Path) -> None:
    """Open the finished file in ffplay, if ffplay is installed."""
    ffplay = _tool_path("ffplay")
    if ffplay is None:
        print(
            "[5/5] Skipping preview: 'ffplay' is not on PATH.\n"
            f"      Install FFmpeg (ffplay ships with it): {ffmpeg_install_hint()}"
        )
        return

    if platform.system() == "Linux" and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        print(
            "[5/5] Skipping preview: no display detected (headless session).\n"
            f"      Play it later with: ffplay {output}"
        )
        return

    print("[5/5] Previewing with ffplay - press Q or ESC to close")
    try:
        subprocess.run(
            [ffplay, "-autoexit", "-window_title", output.name, str(output)],
            check=False,
        )
    except OSError as exc:
        print(f"      Could not launch ffplay: {exc}")
