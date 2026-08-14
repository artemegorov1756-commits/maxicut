"""Everything that gets precomputed once with PIL/numpy and handed to ffmpeg
as an input image, instead of being redrawn or resampled every frame.

Three families of asset live here:

* **Text and logo bitmaps** - straight replacements for MoviePy's TextClip /
  ImageClip. These never touched MoviePy internals in the first place (the
  original already rendered logos with Pillow); only the text path changes,
  from `TextClip` to `ImageDraw` directly.

* **Glass panel maps** (alpha, rim, tint, wash, shadow) - these *did* change
  meaning. The original's `GlassPanel` recomputed geometry once but then
  *sampled the live video frame* through it every frame (frost + outward
  refraction). Per the simplified-glass decision for this project, the
  outward refraction (bending rim pixels to sample footage from just outside
  the panel) is dropped; what stays "live" is the frost itself - ffmpeg's
  `gblur` still blurs the *actual* footage under the panel every frame, via
  the filter graph in `graph.py`. Only the shape, rim mix, tint and wash -
  which never depended on the footage's pixels even in the original - are
  materialised here as static PNGs.
"""

from __future__ import annotations

import math
import platform
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .constants import (
    BUNDLED_FONT,
    EDGE_RATIO,
    FFMPEG_INSTALL_HINTS,
    FONT_CANDIDATES,
    LOGO_MAX_HEIGHT_RATIO,
    SUBLIMINAL_BG,
)
from .download import TitleMakerError

# --------------------------------------------------------------------------- #
# Font / colour / logo resolution
# --------------------------------------------------------------------------- #


def resolve_font(explicit: str | None) -> str:
    """Return a usable TrueType path."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise TitleMakerError(f"Font file not found: {path}")
        return str(path)

    if BUNDLED_FONT.is_file():
        return str(BUNDLED_FONT)

    import sys

    for candidate in FONT_CANDIDATES.get(platform.system(), FONT_CANDIDATES["Linux"]):
        if Path(candidate).is_file():
            print(
                f"      note: {BUNDLED_FONT.name} is missing, falling back to "
                f"{Path(candidate).name}",
                file=sys.stderr,
            )
            return candidate

    raise TitleMakerError(
        "No default font found on this system. Pass one explicitly, e.g.\n"
        "  --font /path/to/YourFont.ttf"
    )


def resolve_logo(explicit: str | None, disabled: bool, default: Path) -> Path | None:
    """Which image to burn in, or None to leave it out."""
    if disabled:
        return None
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise TitleMakerError(f"Logo file not found: {path}")
        return path
    return default if default.is_file() else None


def parse_color(text: str) -> tuple[int, int, int]:
    """Parse an "R,G,B" triple or a "#rrggbb" hex colour."""
    import argparse

    value = text.strip().lstrip("#")
    if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))

    parts = [part.strip() for part in text.replace(";", ",").split(",")]
    if len(parts) == 3:
        try:
            channels = tuple(int(part) for part in parts)
        except ValueError:
            channels = None
        if channels is not None and all(0 <= c <= 255 for c in channels):
            return channels

    raise argparse.ArgumentTypeError(
        f"invalid colour {text!r}, expected R,G,B (0-255 each) or #rrggbb"
    )


def ffmpeg_install_hint() -> str:
    return FFMPEG_INSTALL_HINTS.get(platform.system(), FFMPEG_INSTALL_HINTS["Linux"])


# --------------------------------------------------------------------------- #
# Logo artwork
# --------------------------------------------------------------------------- #


def load_logo_artwork(path: Path) -> Image.Image:
    """Open `path` as RGBA, cropped to its own *alpha* bounding box."""
    try:
        with Image.open(path) as opened:
            logo = opened.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - any unreadable image, one message
        raise TitleMakerError(f"Could not read the logo {path}: {exc}") from exc

    ink = logo.getchannel("A").getbbox()
    if ink is None:
        raise TitleMakerError(f"The logo {path} is fully transparent.")
    return logo.crop(ink)


def build_logo_image(path: Path, width: int, height: int, ratio: float) -> Image.Image:
    """Load the logo and scale it for a `width` x `height` frame."""
    logo = load_logo_artwork(path)

    logo_w = max(1, round(width * ratio))
    logo_h = max(1, round(logo_w * logo.height / logo.width))

    ceiling = max(1, round(height * LOGO_MAX_HEIGHT_RATIO))
    if logo_h > ceiling:
        logo_w = max(1, round(logo_w * ceiling / logo_h))
        logo_h = ceiling

    return logo.resize((logo_w, logo_h), Image.LANCZOS)


def build_subliminal_frame(path: Path, width: int, height: int, ratio: float) -> Image.Image:
    """One whole video frame: the logo, centred, and nothing else."""
    logo = load_logo_artwork(path)

    scale = min(width * ratio / logo.width, height * ratio / logo.height)
    logo_w = max(1, round(logo.width * scale))
    logo_h = max(1, round(logo.height * scale))
    logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

    frame = Image.new("RGB", (width, height), SUBLIMINAL_BG)
    frame.paste(logo, ((width - logo_w) // 2, (height - logo_h) // 2), logo)
    return frame


# --------------------------------------------------------------------------- #
# Soft drop shadows (shared by text and logo)
# --------------------------------------------------------------------------- #


def soft_shadow_from_alpha(alpha: np.ndarray, blur: int, opacity: float) -> tuple[Image.Image, int]:
    """A soft dark halo from an alpha mask, e.g. text or logo ink.

    Returns (RGBA black image, padding added on every side - subtract it from
    the ink's own position to keep the two in register).
    """
    pad = max(1, blur * 2)
    canvas = np.zeros((alpha.shape[0] + 2 * pad, alpha.shape[1] + 2 * pad), dtype=np.float32)
    canvas[pad : pad + alpha.shape[0], pad : pad + alpha.shape[1]] = alpha
    blurred = Image.fromarray((canvas * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
    shadow_alpha = np.asarray(blurred, dtype=np.float32) / 255.0 * opacity

    rgba = np.zeros((*shadow_alpha.shape, 4), dtype=np.uint8)
    rgba[..., 3] = np.clip(shadow_alpha * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA"), pad


# --------------------------------------------------------------------------- #
# Text rendering (replaces MoviePy's TextClip)
# --------------------------------------------------------------------------- #


def safety_margin(font: str, font_size: int, stroke_width: int) -> tuple[int, int]:
    """Slack around the text canvas so drawing cannot clip descenders/stroke."""
    try:
        descent = ImageFont.truetype(font, font_size).getmetrics()[1]
    except Exception:  # noqa: BLE001 - unusual font, fall back to a typical ratio
        descent = round(font_size * 0.25)
    return stroke_width * 2 + 2, stroke_width * 2 + descent + 2


def _crop_to_ink(image: Image.Image) -> Image.Image:
    """Crop an RGBA image down to the pixels that actually carry ink."""
    alpha = np.asarray(image.getchannel("A"))
    columns = alpha.any(axis=0).nonzero()[0]
    rows = alpha.any(axis=1).nonzero()[0]
    if columns.size == 0 or rows.size == 0:
        return image
    x1, x2 = int(columns[0]), int(columns[-1]) + 1
    y1, y2 = int(rows[0]), int(rows[-1]) + 1
    if (x1, y1) == (0, 0) and (x2, y2) == image.size:
        return image
    return image.crop((x1, y1, x2, y2))


def render_text_image(
    text: str,
    font: str,
    font_size: int,
    stroke_width: int,
    line_gap: int,
    color: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Render (possibly multi-line) `text` to an RGBA image cropped to its ink.

    Replaces MoviePy's ``TextClip(method="label") + _crop_to_ink``. Drawn onto
    a canvas padded by `_safety_margin` so PIL's own stroke bleed and
    descenders never fall off the edge, then cropped back down - the same two
    steps the original used, just with Pillow driving the draw directly
    instead of through MoviePy's TextClip wrapper.
    """
    metrics = ImageFont.truetype(font, font_size)
    margin_x, margin_y = safety_margin(font, font_size, stroke_width)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    # Pillow's textbbox can return fractional (subpixel) coordinates; round
    # outward so the canvas is never a hair too small for its own ink.
    x0, y0, x1, y1 = probe.multiline_textbbox(
        (0, 0), text, font=metrics, spacing=line_gap, stroke_width=stroke_width, align="center"
    )
    x0, y0 = math.floor(x0), math.floor(y0)
    x1, y1 = math.ceil(x1), math.ceil(y1)
    canvas_w = (x1 - x0) + 2 * margin_x
    canvas_h = (y1 - y0) + 2 * margin_y

    image = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.multiline_text(
        (margin_x - x0, margin_y - y0),
        text,
        font=metrics,
        fill=(*color, 255),
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 255) if stroke_width else None,
        spacing=line_gap,
        align="center",
    )
    return _crop_to_ink(image)


def render_split_title_image(
    lines: list[tuple[str, int]], font: str, line_gap_ratio: float
) -> Image.Image:
    """Render each `(text, font_size)` line at its own size and stack them.

    Replaces `build_split_title_clip`. The only way to mix sizes within one
    title - each size needs its own draw call - so the pieces are rendered
    separately and composited by hand, same as the original.
    """
    pieces = [render_text_image(text, font, size, 0, 0) for text, size in lines]

    gap = max(0, round(min(size for _, size in lines) * line_gap_ratio))
    width = max(piece.width for piece in pieces)
    height = sum(piece.height for piece in pieces) + gap * (len(pieces) - 1)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y = 0
    for piece in pieces:
        x = (width - piece.width) // 2
        canvas.alpha_composite(piece, (x, y))
        y += piece.height + gap
    return canvas


# --------------------------------------------------------------------------- #
# Glass panel maps
# --------------------------------------------------------------------------- #


def _rounded_rect_sdf(width: int, height: int, radius: float) -> np.ndarray:
    """Signed distance to the edge of a rounded rectangle, in pixels."""
    xs = np.arange(width, dtype=np.float32) - (width - 1) / 2
    ys = np.arange(height, dtype=np.float32) - (height - 1) / 2
    px = np.abs(xs)[None, :] - (width / 2 - radius)
    py = np.abs(ys)[:, None] - (height / 2 - radius)
    outside = np.hypot(np.maximum(px, 0.0), np.maximum(py, 0.0))
    inside = np.minimum(np.maximum(px, py), 0.0)
    return outside + inside - radius


class GlassMaps:
    """Static per-pixel maps for one glass panel instance.

    `alpha` is the panel's own rounded-rect coverage (0-1); `rim_mix` is 0 in
    the flat middle and 1 at the bevel, used to blend a sharper (less blurred)
    pass in near the edge; `tint_rgba` and `wash_rgba` are premultiplied-alpha
    overlay images ffmpeg lays on top of the blurred footage with a plain
    `overlay` filter, reproducing ``glass*(1-a) + colour*a`` without any
    per-frame Python involved.
    """

    def __init__(
        self,
        box_w: int,
        box_h: int,
        radius: float,
        tint: float,
        wash: float,
        wash_color: tuple[int, int, int],
        wash_reach: float,
    ) -> None:
        self.width, self.height = box_w, box_h
        sdf = _rounded_rect_sdf(box_w, box_h, radius)
        self.alpha = np.clip(0.5 - sdf, 0.0, 1.0).astype(np.float32)

        edge = max(2.0, min(box_w, box_h) * EDGE_RATIO)
        rim = np.clip((sdf + edge) / edge, 0.0, 1.0).astype(np.float32)
        self.rim_mix = rim**2

        depth = np.arange(box_h, dtype=np.float32) / max(1, box_h - 1)
        tint_map = np.clip(tint * (1.15 - 0.30 * depth), 0.0, 1.0)

        ramp = np.clip(1.0 - (1.0 - depth) / wash_reach, 0.0, 1.0)
        ramp = ramp * ramp * (3.0 - 2.0 * ramp)  # smoothstep
        wash_map = np.clip(wash * ramp, 0.0, 1.0)

        self.tint_rgba = self._flat_color_image((255, 255, 255), tint_map)
        self.wash_rgba = self._flat_color_image(wash_color, wash_map)

    def _flat_color_image(self, rgb: tuple[int, int, int], row_alpha: np.ndarray) -> Image.Image:
        """An RGBA image of a solid colour whose alpha varies only by row."""
        rgba = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        rgba[..., 0] = rgb[0]
        rgba[..., 1] = rgb[1]
        rgba[..., 2] = rgb[2]
        rgba[..., 3] = np.clip(row_alpha * 255, 0, 255).astype(np.uint8)[:, None]
        return Image.fromarray(rgba, mode="RGBA")

    def alpha_image(self) -> Image.Image:
        return Image.fromarray(np.clip(self.alpha * 255, 0, 255).astype(np.uint8), mode="L")

    def rim_image(self) -> Image.Image:
        return Image.fromarray(np.clip(self.rim_mix * 255, 0, 255).astype(np.uint8), mode="L")

    def drop_shadow(self, layers: list[tuple[float, float, float]]) -> tuple[Image.Image, int]:
        """Two-layer elevation shadow cast by this panel's own shape.

        `layers` are ``(blur, offset, opacity)`` pairs stacked the way real
        overlapping shadows darken (``canvas += layer * (1 - canvas)``).
        """
        pad = max(1, int(np.ceil(max(2 * blur + offset for blur, offset, _ in layers))))
        canvas = np.zeros((self.height + 2 * pad, self.width + 2 * pad), dtype=np.float32)
        for blur, offset, opacity in layers:
            top = pad + int(round(offset))
            layer = np.zeros_like(canvas)
            layer[top : top + self.height, pad : pad + self.width] = self.alpha
            blurred = Image.fromarray((layer * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
            layer = np.asarray(blurred, dtype=np.float32) / 255.0 * opacity
            canvas += layer * (1.0 - canvas)

        rgba = np.zeros((*canvas.shape, 4), dtype=np.uint8)
        rgba[..., 3] = np.clip(canvas * 255, 0, 255).astype(np.uint8)
        return Image.fromarray(rgba, mode="RGBA"), pad
