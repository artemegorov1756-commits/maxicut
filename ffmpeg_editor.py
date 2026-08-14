#!/usr/bin/env python3
"""Burn a liquid-glass lower-third title onto a video and render it to MP4 -
the ffmpeg-native counterpart to video_editor.py.

Same CLI surface, same layout/text-fitting math, same brands, same
subliminal-flash and speed-ramp behaviour. The difference is what actually
draws the pixels: this version never imports MoviePy. Python precomputes the
static parts (title bitmap, logo, glass-panel maps) with PIL/numpy and hands
everything else to a single ffmpeg -filter_complex graph - see
editorlib/graph.py for how that graph is built, and its module docstring (and
assets.py's) for the one deliberate visual difference: the glass panel still
frosts the *live* footage under it every frame, but no longer bends rim
pixels outward to sample past its own edge (no refraction), by design - see
README.md.

Example:
    python ffmpeg_editor.py clip.mp4 "Dr. Ada Lovelace" -o titled.mp4
    python ffmpeg_editor.py clip.mp4 "Same Day Delivery" --brand ups
    python ffmpeg_editor.py https://example.com/clip.mp4 "Live from Berlin"

Requires ffmpeg/ffprobe on PATH (with libx264; h264_nvenc only if --gpu).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from editorlib import assets, download, graph, probe, render
from editorlib.constants import (
    BRANDS,
    DEFAULT_ASPECT,
    DEFAULT_BOX_RATIO,
    DEFAULT_BRAND,
    DEFAULT_EMPHASIS_LINE,
    DEFAULT_EMPHASIS_RATIO,
    DEFAULT_GLASS_BLUR,
    DEFAULT_GLASS_TINT,
    DEFAULT_GLASS_WASH,
    DEFAULT_LINE_GAP,
    DEFAULT_LOGO_OPACITY,
    DEFAULT_LOGO_RATIO,
    DEFAULT_POSITION_RATIO,
    DEFAULT_SPEED,
    DEFAULT_SUBLIMINAL_AT,
    DEFAULT_SUBLIMINAL_MS,
    DEFAULT_SUBLIMINAL_OPACITY,
    DEFAULT_SUBLIMINAL_RATIO,
    DEFAULT_TITLE_DURATION,
    DEFAULT_TITLE_FILL,
)
from editorlib.download import TitleMakerError


def parse_aspect(text: str) -> tuple[int, int]:
    parts = text.split(":")
    if len(parts) == 2:
        try:
            w, h = int(parts[0]), int(parts[1])
        except ValueError:
            w = h = 0
        if w > 0 and h > 0:
            return w, h
    raise argparse.ArgumentTypeError(f"invalid aspect ratio {text!r}, expected W:H")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Overlay a liquid-glass lower-third title on a video and "
        "render it to MP4, using ffmpeg for every video operation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  %(prog)s clip.mp4 "Dr. Ada Lovelace"\n'
            '  %(prog)s clip.mp4 "Same Day Delivery" --brand ups\n'
            '  %(prog)s https://example.com/clip.mp4 "Live from Berlin" -o out.mp4\n'
            '  %(prog)s clip.mp4 "Chapter One" --start 2 --duration 5 --no-box\n'
            '  %(prog)s /home/user/videos/clip.mp4 "From WSL" (output written back to the same WSL dir)\n'
        ),
    )
    parser.add_argument(
        "source", help="local video path, http(s) URL, or a POSIX WSL path "
        "(e.g. /home/user/clip.mp4) - resolved via the Windows \\\\wsl.localhost\\... share"
    )
    parser.add_argument("title", help="title text to overlay")
    parser.add_argument("-o", "--output", type=Path, help="output path (default: <input>_titled.mp4); forced to .mp4")
    parser.add_argument("--font", help="path to a .ttf/.otf font file")
    parser.add_argument(
        "--font-ratio", type=float, default=None, metavar="R",
        help="pin the font to this fraction of min(width, height); by default the "
        "title is grown to fill the panel instead",
    )
    parser.add_argument(
        "--position", type=float, default=DEFAULT_POSITION_RATIO, metavar="R",
        help="vertical centre of the title as a fraction of height "
        f"(default: {DEFAULT_POSITION_RATIO}, the top of the lower third)",
    )
    parser.add_argument(
        "--box-ratio", type=float, default=DEFAULT_BOX_RATIO, metavar="R",
        help=f"how much of the frame the card covers, by area (default: {DEFAULT_BOX_RATIO})",
    )
    parser.add_argument(
        "--text-fill", type=float, default=DEFAULT_TITLE_FILL, metavar="R",
        help=f"how much of the card the title spans, per axis (default: {DEFAULT_TITLE_FILL})",
    )
    parser.add_argument(
        "--line-gap", type=float, default=DEFAULT_LINE_GAP, metavar="R",
        help=f"gap between lines, as a fraction of font size (default: {DEFAULT_LINE_GAP})",
    )
    parser.add_argument("--no-caps", action="store_true", help="keep the title's own capitalisation")
    parser.add_argument(
        "--emphasis-line", choices=["first", "second"], default=DEFAULT_EMPHASIS_LINE,
        help=f"which line of a two-line title is bigger (default: {DEFAULT_EMPHASIS_LINE})",
    )
    parser.add_argument(
        "--emphasis-ratio", type=float, default=DEFAULT_EMPHASIS_RATIO, metavar="X",
        help=f"how much bigger the emphasised line is (default: {DEFAULT_EMPHASIS_RATIO:g})",
    )
    parser.add_argument("--no-emphasis", action="store_true", help="keep both lines of a wrapped title the same size")
    parser.add_argument(
        "--glass-blur", type=float, default=DEFAULT_GLASS_BLUR, metavar="R",
        help=f"frost of the glass, as a fraction of min(width, height) (default: {DEFAULT_GLASS_BLUR})",
    )
    parser.add_argument(
        "--glass-tint", type=float, default=DEFAULT_GLASS_TINT, metavar="A",
        help=f"how much white is milked into the panel, 0-1 (default: {DEFAULT_GLASS_TINT})",
    )
    parser.add_argument(
        "--glass-wash", type=float, default=DEFAULT_GLASS_WASH, metavar="A",
        help=f"how much colour the glass picks up along its bottom edge, 0-1 (default: {DEFAULT_GLASS_WASH})",
    )
    parser.add_argument(
        "--glass-color", type=assets.parse_color, default=None, metavar="RGB",
        help="colour of that gradient, as R,G,B or #rrggbb - overrides --brand's colour",
    )
    parser.add_argument(
        "--brand", choices=sorted(BRANDS), default=DEFAULT_BRAND,
        help="which logo to burn in, and the card gradient that goes with it: "
        + ", ".join(f"{name} ({b.wash_color[0]},{b.wash_color[1]},{b.wash_color[2]})" for name, b in sorted(BRANDS.items()))
        + f" (default: {DEFAULT_BRAND})",
    )
    parser.add_argument("--logo", metavar="PATH", help="image for the top-left corner (default: the --brand logo)")
    parser.add_argument(
        "--logo-ratio", type=float, default=DEFAULT_LOGO_RATIO, metavar="R",
        help=f"logo width as a fraction of frame width (default: {DEFAULT_LOGO_RATIO})",
    )
    parser.add_argument(
        "--logo-opacity", type=float, default=DEFAULT_LOGO_OPACITY, metavar="A",
        help=f"how solid the logo sits over the footage, 0-1 (default: {DEFAULT_LOGO_OPACITY})",
    )
    parser.add_argument("--no-logo", action="store_true", help="render without the corner logo")
    parser.add_argument("--no-subliminal", action="store_true", help="render without the hidden full-frame logo flashes")
    parser.add_argument("--subliminal-image", metavar="PATH", help="image for the hidden flashes (default: the --brand logo)")
    parser.add_argument(
        "--subliminal-at", type=float, nargs="+", default=list(DEFAULT_SUBLIMINAL_AT), metavar="R",
        help="where the flashes go, as fractions of the duration (default: "
        + " ".join(str(r) for r in DEFAULT_SUBLIMINAL_AT) + ")",
    )
    parser.add_argument(
        "--subliminal-ms", type=float, default=DEFAULT_SUBLIMINAL_MS, metavar="MS",
        help=f"how long each flash lasts, in milliseconds (default: {DEFAULT_SUBLIMINAL_MS:g})",
    )
    parser.add_argument(
        "--subliminal-ratio", type=float, default=DEFAULT_SUBLIMINAL_RATIO, metavar="R",
        help=f"flash logo size as a fraction of the frame (default: {DEFAULT_SUBLIMINAL_RATIO})",
    )
    parser.add_argument(
        "--subliminal-opacity", type=float, default=DEFAULT_SUBLIMINAL_OPACITY, metavar="A",
        help=f"how solid the flash frame sits over the footage it replaces, 0-1 (default: {DEFAULT_SUBLIMINAL_OPACITY:g})",
    )
    parser.add_argument("--start", type=float, default=0.0, metavar="SEC", help="when the title appears (default: 0)")
    parser.add_argument(
        "--duration", type=float, default=None, metavar="SEC",
        help=f"how long it stays up (default: {DEFAULT_TITLE_DURATION:g}s, or the whole clip if shorter)",
    )
    parser.add_argument("--fade", type=float, default=0.5, metavar="SEC", help="fade in/out length, 0 to disable (default: 0.5)")
    parser.add_argument("--no-box", action="store_true", help="drop the glass panel, keep the outlined text only")
    parser.add_argument(
        "--speed", type=float, default=DEFAULT_SPEED, metavar="X",
        help=f"playback speed multiplier for the whole rendered video (default: {DEFAULT_SPEED:g})",
    )
    parser.add_argument(
        "--no-speed", action="store_true",
        help=f"disable the speed change (equivalent to --speed 1.0, overrides --speed {DEFAULT_SPEED:g})",
    )
    parser.add_argument(
        "--aspect", type=parse_aspect, default=DEFAULT_ASPECT, metavar="W:H",
        help=f"standardize the output to this aspect ratio (default: {DEFAULT_ASPECT[0]}:{DEFAULT_ASPECT[1]})",
    )
    parser.add_argument("--no-pad", action="store_true", help="keep the source's own shape instead of padding to --aspect")
    parser.add_argument(
        "--gpu", action="store_true",
        help="use h264_nvenc instead of libx264 for the final encode. Unlike video_editor.py's "
        "--gpu, this has no effect on the glass panel: that always runs as ordinary ffmpeg CPU "
        "filters (gblur/overlay/alphamerge) regardless of this flag - see README.md.",
    )
    parser.add_argument(
        "--preset", default=None,
        help="encoder speed/quality trade-off. libx264 (CPU): ultrafast..veryslow (default: medium). "
        "h264_nvenc (GPU): p1..p7 (default: p4).",
    )
    parser.add_argument("--threads", type=int, default=None, help="encoder threads")
    parser.add_argument(
        "--wsl-distro", metavar="NAME",
        help="WSL distro to resolve a POSIX source path against (e.g. /home/user/clip.mp4); "
        "default: whichever distro 'wsl -l -v' marks as default",
    )
    parser.add_argument("--max-download-mb", type=int, default=download.DEFAULT_MAX_DOWNLOAD_MB, metavar="MB", help="download size ceiling")
    parser.add_argument("--keep-download", action="store_true", help="keep the temporary file downloaded from a URL")
    parser.add_argument("--no-preview", action="store_true", help="do not launch ffplay when done")
    return parser.parse_args(argv)


def default_output(source_path: Path, source: str, is_remote: bool) -> Path:
    r"""Pick an output path next to the input: same directory, `_titled` suffix.

    For a WSL source, `source_path` is already the resolved `\\wsl.localhost\...`
    (or `\\wsl$\...`) UNC path, so the default output lands back in that same
    WSL directory rather than the script's own CWD. Remote sources have no
    directory of their own, so those default to the CWD as before.
    """
    if is_remote:
        stem = Path(urllib.parse.urlparse(source).path).stem or "video"
        stem = "".join(c for c in stem if c.isalnum() or c in "-_") or "video"
        return Path(f"{stem}_titled.mp4")
    stem = "".join(c for c in source_path.stem if c.isalnum() or c in "-_") or "video"
    return source_path.with_name(f"{stem}_titled.mp4")


def run(args: argparse.Namespace) -> int:
    if not args.title.strip():
        raise TitleMakerError("Title text is empty.")
    if args.font_ratio is not None and not 0 < args.font_ratio < 1:
        raise TitleMakerError("--font-ratio must be between 0 and 1.")
    if not 0 < args.position < 1:
        raise TitleMakerError("--position must be between 0 and 1.")
    if not 0 < args.box_ratio <= 1:
        raise TitleMakerError("--box-ratio must be between 0 and 1.")
    if not 0 < args.text_fill <= 1:
        raise TitleMakerError("--text-fill must be between 0 and 1.")
    if not 0 <= args.line_gap < 3:
        raise TitleMakerError("--line-gap must be between 0 and 3.")
    if not 1.0 <= args.emphasis_ratio <= 5.0:
        raise TitleMakerError("--emphasis-ratio must be between 1.0 and 5.0.")
    if not 0 <= args.glass_blur < 1:
        raise TitleMakerError("--glass-blur must be between 0 and 1.")
    if not 0 <= args.glass_tint <= 1:
        raise TitleMakerError("--glass-tint must be between 0 and 1.")
    if not 0 <= args.glass_wash <= 1:
        raise TitleMakerError("--glass-wash must be between 0 and 1.")
    if not 0 < args.logo_ratio <= 1:
        raise TitleMakerError("--logo-ratio must be between 0 and 1.")
    if not 0 <= args.logo_opacity <= 1:
        raise TitleMakerError("--logo-opacity must be between 0 and 1.")
    if any(not 0 <= at < 1 for at in args.subliminal_at):
        raise TitleMakerError("--subliminal-at takes fractions of the duration, 0 <= R < 1.")
    if not 0 < args.subliminal_ms <= 1000:
        raise TitleMakerError("--subliminal-ms must be between 0 and 1000.")
    if not 0 < args.subliminal_ratio <= 1:
        raise TitleMakerError("--subliminal-ratio must be between 0 and 1.")
    if not 0 <= args.subliminal_opacity <= 1:
        raise TitleMakerError("--subliminal-opacity must be between 0 and 1.")
    if args.speed <= 0:
        raise TitleMakerError("--speed must be greater than 0.")
    speed = 1.0 if args.no_speed else args.speed

    gpu = args.gpu
    if not args.gpu:
        print("[1/5] CPU encode (pass --gpu to try h264_nvenc instead): libx264")
    else:
        print("[1/5] GPU encode (--gpu): h264_nvenc; glass panel still runs as CPU ffmpeg filters")
    codec = "h264_nvenc" if gpu else "libx264"
    preset = args.preset or ("p4" if gpu else "medium")

    brand = BRANDS[args.brand]
    wash_color = args.glass_color if args.glass_color is not None else brand.wash_color

    logo_path = assets.resolve_logo(args.logo, args.no_logo, brand.logo)
    subliminal_path = assets.resolve_logo(args.subliminal_image, args.no_subliminal, brand.logo)

    source_path, is_temp = download.resolve_source(
        args.source, args.max_download_mb * 1024 * 1024, args.wsl_distro
    )

    output = args.output or default_output(source_path, args.source, is_temp)
    if output.suffix.lower() != ".mp4":
        output = output.with_suffix(".mp4")
    if output.parent != Path("."):
        output.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_editor_assets_"))
    build_result = None
    start_time = time.perf_counter()
    try:
        print("[2/5] Probing video")
        info = probe.probe(source_path)
        print(f"      {info.width}x{info.height}, {info.duration:.2f}s, {info.fps:g} fps")

        if args.start >= info.duration:
            raise TitleMakerError(f"--start {args.start}s is beyond the {info.duration:.2f}s video.")
        remaining = info.duration - args.start
        duration_title = args.duration if args.duration is not None else min(DEFAULT_TITLE_DURATION, remaining)
        duration_title = min(duration_title, remaining)
        fade = min(args.fade, duration_title / 2)

        print("[3/5] Building liquid-glass lower third")
        print(
            f"      brand {args.brand} - card gradient rgb{tuple(wash_color)}"
            + ("" if args.glass_color is None else " (--glass-color override)")
        )

        font = assets.resolve_font(args.font)
        build_result = graph.build(
            args=args,
            source_path=source_path,
            info=info,
            font=font,
            wash_color=wash_color,
            logo_path=logo_path,
            subliminal_path=subliminal_path,
            speed=speed,
            duration_title=duration_title,
            fade=fade,
            tmp_dir=tmp_dir,
        )
        for line in build_result.log:
            print(line)

        render.render(build_result, output, codec, preset, args.threads)
    finally:
        if build_result is not None:
            render.cleanup(build_result.temp_files)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if is_temp and not args.keep_download:
            source_path.unlink(missing_ok=True)
        elif is_temp:
            print(f"      downloaded file kept at {source_path}")

    size_mb = output.stat().st_size / 1e6
    elapsed = time.perf_counter() - start_time
    print(f"      done: {output.resolve()} ({size_mb:.1f} MB, {elapsed:.1f}s)")

    if not args.no_preview:
        render.preview(output)
    return 0


def main() -> int:
    try:
        return run(parse_args())
    except TitleMakerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
