"""Layout measurements and title wrapping.

The sizing model here is deliberately the *inverse* of what this module used
to do. It previously grew the type until it filled a fixed-size card (and had
a whole DP search over line arrangements plus a two-line "emphasis" split to
do it), which meant a short title rendered huge and a long one rendered small.
The design reference pins the type instead - one font size derived from the
frame, a fixed card width, and a card that grows *downward* to fit however
many lines the title wraps onto - so the fitting search is gone and what is
left is plain Pillow font metrics.

Everything is pure PIL/numpy-free arithmetic; nothing here draws pixels (see
`assets.py`) or touches ffmpeg (see `graph.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PIL import Image, ImageDraw, ImageFont

from .constants import (
    CARD_PAD_X_RATIO,
    CARD_PAD_Y_RATIO,
    MAX_TITLE_LINES,
    MIN_FONT_SIZE,
    PAD_X_RATIO,
    PAD_Y_RATIO,
    SIDE_MARGIN_RATIO,
)


@dataclass(frozen=True)
class Layout:
    """Every pixel measurement for the title block, derived from frame size."""

    font_size: int
    side_margin: int
    top_margin: int
    bottom_margin: int
    pad_x: int
    pad_y: int
    card_pad_x: int
    card_pad_y: int
    line_gap: int
    stroke_width: int


def layout_for(width: int, height: int, font_size: int, line_gap_ratio: float) -> Layout:
    """Every measurement that follows from a chosen font size."""
    font_size = max(MIN_FONT_SIZE, font_size)
    side_margin = max(8, round(width * SIDE_MARGIN_RATIO))
    edge_margin = max(8, round(height * 0.06))
    return Layout(
        font_size=font_size,
        side_margin=side_margin,
        top_margin=edge_margin,
        bottom_margin=edge_margin,
        pad_x=max(10, round(font_size * PAD_X_RATIO)),
        pad_y=max(8, round(font_size * PAD_Y_RATIO)),
        card_pad_x=max(1, round(font_size * CARD_PAD_X_RATIO)),
        card_pad_y=max(1, round(font_size * CARD_PAD_Y_RATIO)),
        line_gap=max(0, round(font_size * line_gap_ratio)),
        stroke_width=max(1, round(font_size * 0.055)),
    )


def compute_layout(width: int, height: int, font_ratio: float, line_gap_ratio: float) -> Layout:
    """Scale the whole title block to the frame (reference = min(w, h))."""
    return layout_for(width, height, round(min(width, height) * font_ratio), line_gap_ratio)


def max_text_width(width: int, layout: Layout) -> int:
    """Widest the text may be before it has to wrap, off the card path."""
    return max(1, width - 2 * layout.side_margin - 2 * layout.pad_x)


def card_width(width: int, ratio: float, side_margin: int) -> int:
    """The card's width, fixed by the frame alone - the text wraps inside it."""
    return max(1, min(round(width * ratio), max(1, width - 2 * side_margin)))


def line_height(font: str, font_size: int) -> int:
    """One line's own height: the font's ascent plus descent, no leading."""
    try:
        ascent, descent = ImageFont.truetype(font, font_size).getmetrics()
        return ascent + descent
    except Exception:  # noqa: BLE001 - unusual font, fall back to a typical ratio
        return round(font_size * 1.3)


def line_pitch(font: str, font_size: int, line_gap: int, stroke: int = 0) -> int:
    """Baseline-to-baseline advance Pillow actually uses for multiline text.

    This mirrors `ImageDraw._multiline_spacing`: the bottom of an "A" bounding
    box, plus the stroke, plus the caller's spacing. It has to be measured the
    way Pillow measures it rather than guessed, because Pillow's advance is
    *not* the font's ascent+descent - on Stem Medium the two differ by 10 px at
    a 33 px size, which over three lines walks the text 20 px out of the card
    that was sized to hold it.
    """
    try:
        metrics = ImageFont.truetype(font, font_size)
        probe = ImageDraw.Draw(Image.new("L", (1, 1)))
        return probe.textbbox((0, 0), "A", font=metrics, stroke_width=stroke)[3] + stroke + line_gap
    except Exception:  # noqa: BLE001 - unusual font, fall back to a typical ratio
        return font_size + line_gap


def text_block_height(
    font: str, font_size: int, lines: int, line_gap: int, stroke: int = 0
) -> int:
    """Height a `lines`-line block occupies, independent of which glyphs it uses.

    Glyph-independent on purpose - it is what sets the card's height, and a card
    that resized itself depending on whether the title happened to contain a
    descender or an accent would jitter between otherwise identical clips.
    """
    lines = max(1, lines)
    pitch = line_pitch(font, font_size, line_gap, stroke)
    return (lines - 1) * pitch + line_height(font, font_size)


def wrap_title(
    title: str,
    font: str,
    font_size: int,
    stroke: int,
    limit: int,
    max_lines: int = MAX_TITLE_LINES,
) -> str:
    """Break `title` into lines no wider than `limit` pixels, at most `max_lines`."""
    try:
        metrics = ImageFont.truetype(font, font_size)
    except Exception:  # noqa: BLE001 - unusual font: leave the title on one line
        return title

    def width(text: str) -> int:
        return round(metrics.getlength(text)) + 2 * stroke

    def chop(word: str) -> list[str]:
        pieces, current = [], ""
        for char in word:
            if current and width(current + char) > limit:
                pieces.append(current)
                current = char
            else:
                current += char
        return [*pieces, current] if current else pieces

    words: list[str] = []
    for word in title.split():
        words.extend([word] if width(word) <= limit else chop(word))

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if current and width(candidate) > limit and len(lines) < max_lines - 1:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines) or title


__all__ = [
    "Layout",
    "layout_for",
    "compute_layout",
    "max_text_width",
    "card_width",
    "line_height",
    "line_pitch",
    "text_block_height",
    "wrap_title",
    "replace",
]
