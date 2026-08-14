"""Dynamic sizing and title wrapping/fitting.

Ported verbatim from video_editor.py. None of this depended on MoviePy - it
is Pillow font metrics and plain search/DP over line arrangements - so the
math that decides *how big* the title gets and *where* it breaks is unchanged;
only what finally draws it (this project uses Pillow directly, see
`assets.render_text_image`, instead of MoviePy's TextClip) is different.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PIL import ImageFont

from .constants import (
    CARD_HEIGHT_SCALE,
    MAX_TITLE_LINES,
    PAD_X_RATIO,
    PAD_Y_RATIO,
    PROBE_SIZE,
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
    line_gap: int
    stroke_width: int
    text_shadow_blur: int


def layout_for(width: int, height: int, font_size: int, line_gap_ratio: float) -> Layout:
    """Every measurement that follows from a chosen font size."""
    font_size = max(14, font_size)
    side_margin = max(8, round(width * 0.05))
    edge_margin = max(8, round(height * 0.06))
    pad_x = max(10, round(font_size * PAD_X_RATIO))
    pad_y = max(8, round(font_size * PAD_Y_RATIO))
    line_gap = max(0, round(font_size * line_gap_ratio))
    stroke_width = max(1, round(font_size * 0.055))
    text_shadow_blur = max(2, round(font_size * 0.30))
    return Layout(
        font_size=font_size,
        side_margin=side_margin,
        top_margin=edge_margin,
        bottom_margin=edge_margin,
        pad_x=pad_x,
        pad_y=pad_y,
        line_gap=line_gap,
        stroke_width=stroke_width,
        text_shadow_blur=text_shadow_blur,
    )


def compute_layout(width: int, height: int, font_ratio: float, line_gap_ratio: float) -> Layout:
    """Scale the whole title block to the frame (reference = min(w, h))."""
    reference = min(width, height)
    return layout_for(width, height, round(reference * font_ratio), line_gap_ratio)


def max_text_width(width: int, layout: Layout) -> int:
    """Widest the text may be before it has to wrap."""
    return max(1, width - 2 * layout.side_margin - 2 * layout.pad_x)


def card_size(width: int, height: int, box_ratio: float, side_margin: int) -> tuple[int, int]:
    """The card's size, fixed by the frame alone."""
    scale = (3.0 * box_ratio) ** 0.5
    box_w = max(1, min(round(width * scale), max(1, width - 2 * side_margin)))
    box_h = max(1, min(round(height / 3 * scale * CARD_HEIGHT_SCALE), height))
    return box_w, box_h


def card_padding(box_w: int, box_h: int, fill: float) -> tuple[int, int]:
    """The inset between the text and the edge of the glass, in pixels."""
    margin = max(0.0, (1.0 - fill) / 2.0)
    return max(1, round(box_w * margin)), max(1, round(box_h * margin))


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


def text_block_height(
    font: str, font_size: int, lines: int, line_gap: int, stroke: int, caps: bool = False
) -> int:
    """Height a `lines`-line title occupies, independent of which glyphs it uses."""
    from PIL import Image, ImageDraw

    sample = "\n".join(["HG" if caps else "Hg"] * max(1, lines))
    try:
        metrics = ImageFont.truetype(font, font_size)
        draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        box = draw.multiline_textbbox(
            (0, 0), sample, font=metrics, spacing=line_gap, stroke_width=stroke
        )
        return box[3] - box[1]
    except Exception:  # noqa: BLE001 - unusual font, fall back to a typical ratio
        return round(font_size * 1.0) * lines + line_gap * (lines - 1)


def _partition_lines(
    words: list[str], widths: list[float], space: float, count: int
) -> list[list[str]] | None:
    """Split `words` into exactly `count` lines, minimising the widest line."""
    n = len(words)
    if count > n:
        return None

    def line_width(start: int, stop: int) -> float:
        return sum(widths[start:stop]) + space * (stop - start - 1)

    inf = float("inf")
    best = [[inf] * (n + 1) for _ in range(count + 1)]
    split = [[-1] * (n + 1) for _ in range(count + 1)]
    best[0][n] = 0.0
    for k in range(1, count + 1):
        for i in range(n - 1, -1, -1):
            for j in range(i + 1, n + 1):
                if best[k - 1][j] == inf:
                    continue
                widest = max(line_width(i, j), best[k - 1][j])
                if widest < best[k][i]:
                    best[k][i] = widest
                    split[k][i] = j
    if best[count][0] == inf:
        return None

    lines, index = [], 0
    for k in range(count, 0, -1):
        stop = split[k][index]
        lines.append(words[index:stop])
        index = stop
    return lines


def fit_title_to_card(
    text: str,
    font: str,
    avail_w: int,
    avail_h: int,
    line_gap_ratio: float,
    caps: bool = False,
) -> tuple[int, str]:
    """Grow the type until it fills the card's padded interior."""
    words = text.split()
    if not words:
        words = [text]
    max_lines = min(len(words), MAX_TITLE_LINES)
    try:
        metrics = ImageFont.truetype(font, PROBE_SIZE)
        widths = [metrics.getlength(w) / PROBE_SIZE for w in words]
        space = metrics.getlength(" ") / PROBE_SIZE
    except Exception:  # noqa: BLE001 - unusual font, fall back to a crude guess
        widths = [0.6 * len(w) for w in words]
        space = 0.3

    best = None
    best_key = None
    for count in range(1, max_lines + 1):
        arrangement = _partition_lines(words, widths, space, count)
        if arrangement is None:
            continue
        unit_w = 0.0
        index = 0
        for line in arrangement:
            line_w = sum(widths[index : index + len(line)]) + space * (len(line) - 1)
            unit_w = max(unit_w, line_w)
            index += len(line)
        unit_h = (
            text_block_height(font, PROBE_SIZE, count, round(PROBE_SIZE * line_gap_ratio), 0, caps)
            / PROBE_SIZE
        )
        font_size = max(1, int(min(avail_w / max(unit_w, 1e-6), avail_h / max(unit_h, 1e-6))))

        coverage = (font_size * unit_w * font_size * unit_h) / (avail_w * avail_h)
        key = (font_size, coverage)
        if best_key is None or key > best_key:
            best_key = key
            best = (font_size, "\n".join(" ".join(line) for line in arrangement))
    return best


def fit_two_line_title_to_card(
    text: str,
    font: str,
    avail_w: int,
    avail_h: int,
    line_gap_ratio: float,
    emphasis_ratio: float,
    emphasis_line: str,
    caps: bool = False,
) -> tuple[int, int, str, str] | None:
    """Split `text` into exactly two lines, one `emphasis_ratio` bigger, and
    grow both until the block fills the card. None for a single-word title."""
    words = text.split()
    if len(words) < 2:
        return None

    try:
        metrics = ImageFont.truetype(font, PROBE_SIZE)
        widths = [metrics.getlength(w) / PROBE_SIZE for w in words]
        space = metrics.getlength(" ") / PROBE_SIZE
    except Exception:  # noqa: BLE001 - unusual font, fall back to a crude guess
        widths = [0.6 * len(w) for w in words]
        space = 0.3

    mult_a, mult_b = (
        (emphasis_ratio, 1.0) if emphasis_line == "first" else (1.0, emphasis_ratio)
    )

    best_k, best_cost = 1, float("inf")
    for k in range(1, len(words)):
        width_a = sum(widths[:k]) + space * (k - 1)
        width_b = sum(widths[k:]) + space * (len(words) - k - 1)
        cost = max(mult_a * width_a, mult_b * width_b)
        if cost < best_cost:
            best_cost = cost
            best_k = k

    unit_h = text_block_height(font, PROBE_SIZE, 1, 0, 0, caps) / PROBE_SIZE
    bound_w = avail_w / max(best_cost, 1e-6)
    bound_h = avail_h / (mult_a * unit_h + mult_b * unit_h + line_gap_ratio)
    size = max(1, int(min(bound_w, bound_h)))

    line_a = " ".join(words[:best_k])
    line_b = " ".join(words[best_k:])
    return max(1, int(size * mult_a)), max(1, int(size * mult_b)), line_a, line_b


__all__ = [
    "Layout",
    "layout_for",
    "compute_layout",
    "max_text_width",
    "card_size",
    "card_padding",
    "wrap_title",
    "text_block_height",
    "fit_title_to_card",
    "fit_two_line_title_to_card",
    "replace",
]
