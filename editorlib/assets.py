"""Everything that gets precomputed once with PIL/numpy and handed to ffmpeg
as an input image, instead of being redrawn or resampled every frame.

Three families of asset live here: the title bitmap, the logo lockup
(wordmark + accent rule, recoloured to the brand's colour), and the card
itself.

The card used to be the interesting one. It was a liquid-glass panel whose
static maps - rounded-rect alpha, rim bevel, white tint, brand-coloured wash -
were materialised here so that ffmpeg's `gblur`/`maskedmerge` could frost the
*live* footage under it every frame. The design reference has none of that:
the card is a flat translucent rectangle, so it is now a single solid RGBA
image and the whole glass subgraph is gone from `graph.py`.
"""

from __future__ import annotations

import math
import platform
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .constants import (
    BUNDLED_FONT,
    FFMPEG_INSTALL_HINTS,
    FONT_CANDIDATES,
    LOGO_MAX_HEIGHT_RATIO,
    LOGO_RULE_GAP_RATIO,
    LOGO_RULE_HEIGHT_RATIO,
    SUBLIMINAL_BG,
)
from .download import TitleMakerError

# --------------------------------------------------------------------------- #
# Font / colour / logo resolution
# --------------------------------------------------------------------------- #


def resolve_font(explicit: str | None) -> str:
    """Return a usable TrueType/OpenType path."""
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
    """Parse an R,G,B triple or a #rrggbb hex colour."""
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
# The card
# --------------------------------------------------------------------------- #


def build_card_image(
    width: int, height: int, color: tuple[int, int, int], opacity: float
) -> Image.Image:
    """The title card: a flat rectangle of `color`, uniformly translucent.

    Square corners and a single alpha value across the whole surface - the
    reference has no rounding, no bevel and no gradient, so there is nothing
    per-pixel to compute here any more.
    """
    alpha = int(round(min(max(opacity, 0.0), 1.0) * 255))
    return Image.new("RGBA", (max(1, width), max(1, height)), (*color, alpha))


def build_gradient_card_image(
    width: int, height: int, color: tuple[int, int, int], opacity: float, power: float = 1.0
) -> Image.Image:
    """The "bar" style's scrim: `color` fading left-to-right from `opacity` to 0.

    Every row is identical - the gradient only varies across `width` - so this
    is the "bar" style's stand-in for `build_card_image`'s flat fill: a scrim
    that holds solid where the caption starts, next to the accent bar, and
    thins out to nothing by the box's right edge instead of drawing a hard
    edge over footage. `power` > 1 keeps it darker longer before the fade
    kicks in, since alpha follows `(1 - x/width) ** power` rather than a plain
    linear ramp.
    """
    width, height = max(1, width), max(1, height)
    peak = min(max(opacity, 0.0), 1.0) * 255
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)
    alpha_row = np.clip((1.0 - x) ** power * peak, 0, 255).astype(np.uint8)
    alpha = np.tile(alpha_row, (height, 1))
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = color[0]
    rgba[..., 1] = color[1]
    rgba[..., 2] = color[2]
    rgba[..., 3] = alpha
    return Image.fromarray(rgba, mode="RGBA")


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


def tint_ink(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Repaint every pixel `color`, keeping the artwork's own alpha.

    Only meaningful for a monochrome wordmark; a logo that already carries its
    own colours is left alone by the caller (see `Brand.wordmark`).
    """
    flat = Image.new("RGBA", image.size, (*color, 255))
    flat.putalpha(image.getchannel("A"))
    return flat


def build_logo_image(
    path: Path,
    width: int,
    height: int,
    ratio: float,
    color: tuple[int, int, int] | None = None,
    rule: bool = False,
) -> Image.Image:
    """The logo lockup, scaled for a `width` x `height` frame.

    `color` recolours the artwork; `rule` adds the accent bar under it, drawn
    the full width of the wordmark in the same colour. Both are what
    `Brand.wordmark` decides between at the call site.
    """
    logo = load_logo_artwork(path)

    logo_w = max(1, round(width * ratio))
    logo_h = max(1, round(logo_w * logo.height / logo.width))

    ceiling = max(1, round(height * LOGO_MAX_HEIGHT_RATIO))
    if logo_h > ceiling:
        logo_w = max(1, round(logo_w * ceiling / logo_h))
        logo_h = ceiling

    logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
    if color is not None:
        logo = tint_ink(logo, color)
    if not rule or color is None:
        return logo

    gap = max(1, round(logo_w * LOGO_RULE_GAP_RATIO))
    rule_h = max(1, round(logo_w * LOGO_RULE_HEIGHT_RATIO))
    lockup = Image.new("RGBA", (logo_w, logo_h + gap + rule_h), (0, 0, 0, 0))
    lockup.alpha_composite(logo, (0, 0))
    ImageDraw.Draw(lockup).rectangle(
        [0, logo_h + gap, logo_w - 1, logo_h + gap + rule_h - 1], fill=(*color, 255)
    )
    return lockup


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
# Soft drop shadow - the --no-box path only, where text sits on raw footage.
# On the card path the card itself supplies the contrast, and the reference
# shows no halo around the glyphs.
# --------------------------------------------------------------------------- #


def soft_shadow_from_alpha(alpha: np.ndarray, blur: int, opacity: float) -> tuple[Image.Image, int]:
    """A soft dark halo from an alpha mask, e.g. text ink.

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
# Text rendering
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
    align: str = "left",
) -> Image.Image:
    """Render (possibly multi-line) `text` to an RGBA image cropped to its ink.

    Drawn onto a canvas padded by `safety_margin` so Pillow's own stroke bleed
    and descenders never fall off the edge, then cropped back down.

    Note that cropping to ink means the returned image's height depends on
    which glyphs the title happens to use; the card's height is measured from
    font metrics instead (`textfit.text_block_height`) so it cannot jitter, and
    the ink is centred inside it.
    """
    metrics = ImageFont.truetype(font, font_size)
    margin_x, margin_y = safety_margin(font, font_size, stroke_width)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    # Pillow's textbbox can return fractional (subpixel) coordinates; round
    # outward so the canvas is never a hair too small for its own ink.
    x0, y0, x1, y1 = probe.multiline_textbbox(
        (0, 0), text, font=metrics, spacing=line_gap, stroke_width=stroke_width, align=align
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
        align=align,
    )
    return _crop_to_ink(image)
