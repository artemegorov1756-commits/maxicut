#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pillow>=12.0",
#     "numpy>=2.0",
# ]
# ///
"""Burn a lower-third title lockup onto a video and render it to MP4.

Each brand picks one of two lockup styles (`Brand.style`, see
editorlib/constants.py): a flat translucent card with the title set
left-aligned inside it and the logo sitting directly above, or - `whatsup` -
no card, with the title on the raw footage marked by a coloured accent bar and
the logo pinned near the top of the frame on its own. Either way the title's
width and type size are fixed fractions of the frame; what varies is the
title block's height, which grows to fit however many lines (up to three) it
wraps onto. See editorlib/constants.py, whose numbers were measured off the
design references.

Python precomputes every layer as a static PNG with PIL/numpy and hands the
rest to a single ffmpeg -filter_complex graph - see editorlib/graph.py.

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
    BAR_FONT_RATIO,
    BAR_POSITION_RATIO,
    BRANDS,
    DEFAULT_ASPECT,
    DEFAULT_BRAND,
    DEFAULT_CARD_COLOR,
    DEFAULT_CARD_OPACITY,
    DEFAULT_CARD_WIDTH_RATIO,
    DEFAULT_FONT_RATIO,
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
    MAX_TITLE_LINES,
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
        description="Overlay a lower-third title lockup on a video and render "
        "it to MP4, using ffmpeg for every video operation.",
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
    parser.add_argument("title", help=f"title text to overlay (wraps onto up to {MAX_TITLE_LINES} lines)")
    parser.add_argument("-o", "--output", type=Path, help="output path (default: <input>_titled.mp4); forced to .mp4")
    parser.add_argument("--font", help="path to a .ttf/.otf font file (default: the bundled Stem Medium)")
    parser.add_argument(
        "--font-ratio", type=float, default=None, metavar="R",
        help="type size as a fraction of min(width, height) "
        f"(default: {DEFAULT_FONT_RATIO} for 'card'-style brands, {BAR_FONT_RATIO} for "
        "'bar'-style brands). The card grows to fit the text, so this sets the "
        "size on every clip rather than varying with title length",
    )
    parser.add_argument(
        "--position", type=float, default=None, metavar="R",
        help="vertical position of the title block's TOP edge as a fraction of "
        f"height (default: {DEFAULT_POSITION_RATIO} for 'card'-style brands, "
        f"{BAR_POSITION_RATIO} for 'bar'-style brands); on the 'card' style the logo "
        "lockup sits above it, on 'bar' the logo is positioned independently",
    )
    parser.add_argument(
        "--card-width", type=float, default=DEFAULT_CARD_WIDTH_RATIO, metavar="R",
        help=f"card width as a fraction of frame width (default: {DEFAULT_CARD_WIDTH_RATIO}); "
        "the title wraps inside it",
    )
    parser.add_argument(
        "--card-color", type=assets.parse_color, default=DEFAULT_CARD_COLOR, metavar="RGB",
        help="card fill colour, as R,G,B or #rrggbb (default: "
        f"{DEFAULT_CARD_COLOR[0]},{DEFAULT_CARD_COLOR[1]},{DEFAULT_CARD_COLOR[2]})",
    )
    parser.add_argument(
        "--card-opacity", type=float, default=DEFAULT_CARD_OPACITY, metavar="A",
        help=f"how solid the card sits over the footage, 0-1 (default: {DEFAULT_CARD_OPACITY})",
    )
    parser.add_argument(
        "--line-gap", type=float, default=DEFAULT_LINE_GAP, metavar="R",
        help=f"leading between lines, as a fraction of font size (default: {DEFAULT_LINE_GAP})",
    )
    parser.add_argument("--no-caps", action="store_true", help="keep the title's own capitalisation")
    parser.add_argument(
        "--brand", choices=sorted(BRANDS), default=DEFAULT_BRAND,
        help="which logo to burn in, and the colour that goes with it: "
        + ", ".join(
            f"{name} (#{b.color[0]:02x}{b.color[1]:02x}{b.color[2]:02x})"
            for name, b in sorted(BRANDS.items())
        )
        + f" (default: {DEFAULT_BRAND})",
    )
    parser.add_argument("--logo", metavar="PATH", help="image for the logo (default: the --brand logo)")
    parser.add_argument(
        "--logo-ratio", type=float, default=DEFAULT_LOGO_RATIO, metavar="R",
        help=f"logo width as a fraction of frame width (default: {DEFAULT_LOGO_RATIO})",
    )
    parser.add_argument(
        "--logo-opacity", type=float, default=DEFAULT_LOGO_OPACITY, metavar="A",
        help=f"how solid the logo sits over the footage, 0-1 (default: {DEFAULT_LOGO_OPACITY:g})",
    )
    parser.add_argument(
        "--logo-color", type=assets.parse_color, default=None, metavar="RGB",
        help="recolour the logo to this, as R,G,B or #rrggbb - overrides --brand's colour",
    )
    parser.add_argument(
        "--no-logo-color", action="store_true",
        help="leave the logo artwork's own colours alone instead of recolouring it",
    )
    parser.add_argument("--no-logo", action="store_true", help="render without the logo")
    parser.add_argument(
        "--subliminal", action="store_true",
        help="add hidden full-frame logo flashes (off by default; implied by --subliminal-image)",
    )
    parser.add_argument("--subliminal-image", metavar="PATH", help="image for the hidden flashes (default: the --brand logo); implies --subliminal")
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
    parser.add_argument("--no-box", action="store_true", help="drop the card (or the accent bar, on 'bar'-style brands), keep the outlined text only")
    parser.add_argument(
        "--speed", type=float, default=DEFAULT_SPEED, metavar="X",
        help=f"playback speed multiplier for the whole rendered video (default: {DEFAULT_SPEED:g}, i.e. unchanged)",
    )
    parser.add_argument(
        "--no-speed", action="store_true",
        help="force the speed change off, overriding any --speed value",
    )
    parser.add_argument(
        "--aspect", type=parse_aspect, default=DEFAULT_ASPECT, metavar="W:H",
        help=f"standardize the output to this aspect ratio (default: {DEFAULT_ASPECT[0]}:{DEFAULT_ASPECT[1]})",
    )
    parser.add_argument("--no-pad", action="store_true", help="keep the source's own shape instead of padding to --aspect")
    parser.add_argument(
        "--gpu", action="store_true",
        help="use h264_nvenc instead of libx264 for the final encode.",
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


def resolve_logo_color(args, brand) -> tuple[int, int, int] | None:
    """Which colour to repaint the logo, or None to leave the artwork alone.

    An explicit --logo-color always wins; otherwise a brand recolours its logo
    only when that logo is a monochrome wordmark, since artwork that already
    carries its own colours would just be flattened by a repaint.
    """
    if args.no_logo_color:
        return None
    if args.logo_color is not None:
        return args.logo_color
    return brand.color if brand.wordmark else None


def run(args: argparse.Namespace) -> int:
    if not args.title.strip():
        raise TitleMakerError("Title text is empty.")
    brand = BRANDS[args.brand]
    font_ratio = args.font_ratio
    if font_ratio is None:
        font_ratio = BAR_FONT_RATIO if brand.style == "bar" else DEFAULT_FONT_RATIO
    if not 0 < font_ratio < 1:
        raise TitleMakerError("--font-ratio must be between 0 and 1.")
    position = args.position
    if position is None:
        position = BAR_POSITION_RATIO if brand.style == "bar" else DEFAULT_POSITION_RATIO
    if not 0 < position < 1:
        raise TitleMakerError("--position must be between 0 and 1.")
    if not 0 < args.card_width <= 1:
        raise TitleMakerError("--card-width must be between 0 and 1.")
    if not 0 <= args.card_opacity <= 1:
        raise TitleMakerError("--card-opacity must be between 0 and 1.")
    if not 0 <= args.line_gap < 3:
        raise TitleMakerError("--line-gap must be between 0 and 3.")
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
        print("[1/5] GPU encode (--gpu): h264_nvenc")
    codec = "h264_nvenc" if gpu else "libx264"
    preset = args.preset or ("p4" if gpu else "medium")

    logo_color = resolve_logo_color(args, brand)

    logo_path = assets.resolve_logo(args.logo, args.no_logo, brand.logo)
    subliminal_enabled = args.subliminal or args.subliminal_image is not None
    subliminal_path = assets.resolve_logo(args.subliminal_image, not subliminal_enabled, brand.logo)

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

        print("[3/5] Building the title lockup")
        print(
            f"      brand {args.brand} - colour "
            + (f"rgb{tuple(logo_color)}" if logo_color is not None else "left to the artwork")
            + ("" if args.logo_color is None else " (--logo-color override)")
        )

        font = assets.resolve_font(args.font)
        build_result = graph.build(
            args=args,
            source_path=source_path,
            info=info,
            font=font,
            font_ratio=font_ratio,
            position=position,
            brand=brand,
            logo_color=logo_color,
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
