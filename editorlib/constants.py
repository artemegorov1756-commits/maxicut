"""Every tunable default for the title lockup.

The numbers under "Title card layout" and "Logo" were measured off the design
reference (`new_video_design.jpg`, a 900x1600 frame) and are expressed as
ratios of the frame or of the font size, so they hold at any resolution. Where
a comment cites a pixel value it is the reference's own measurement at 900x1600.

The card is a *flat* translucent rectangle: no frost, no bevel, no gradient.
It used to be a liquid-glass panel that blurred the live footage under it and
washed a brand colour along its bottom edge; the reference shows neither, so
both are gone - see assets.py and graph.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Title card layout
# --------------------------------------------------------------------------- #

#: Font size as a fraction of min(width, height). Reference: 33 px / 900.
#: Unlike the old grow-to-fill behaviour this is *the* size - the card grows to
#: fit the text, not the other way round - so type reads the same on every clip.
DEFAULT_FONT_RATIO = 0.037

#: Font size for the "bar" style, same fraction but bigger: sized so a typical
#: 3-line caption's box lands at roughly 18% of the frame's height (measured:
#: 0.06 -> 182 px on a 1024 px-tall frame for a 3-line caption, i.e. ~17.8%).
#: Still the pinned-type model - this is *the* size for every "bar" clip, not
#: a per-title fit - so a one- or two-line title renders a shorter box rather
#: than being blown up to fill 18% on its own.
BAR_FONT_RATIO = 0.06

#: Vertical position of the card's TOP edge, as a fraction of frame height.
#: The card is anchored by its top (not its centre) because the logo lockup
#: sits directly above it: a centred card would shove the logo around whenever
#: the line count changed. Reference: 1122 / 1600.
DEFAULT_POSITION_RATIO = 0.70

#: Left margin shared by the card and the logo above it. Reference: 122 / 900.
SIDE_MARGIN_RATIO = 0.136

#: Card width as a fraction of frame width. Fixed - the text wraps inside it.
#: Reference: 548 / 900.
DEFAULT_CARD_WIDTH_RATIO = 0.609

#: Inset between the text and the card's edges, as a fraction of the font size.
#: Reference: 13 px and 28 px at a 33 px font.
CARD_PAD_X_RATIO = 0.40
CARD_PAD_Y_RATIO = 0.85

#: Padding between text and card edge for the --no-box path, which has no card
#: to measure against.
PAD_X_RATIO = 0.70
PAD_Y_RATIO = 0.40

#: Leading between lines, as a fraction of the font size. It is handed to
#: Pillow as its `spacing`, which sits on top of Pillow's own per-line advance
#: (roughly the cap height, not the full ascent+descent - see
#: `textfit.line_pitch`), so the resulting baseline-to-baseline pitch is about
#: 1.45x the font size. Reference: a 47.5 px pitch at a 33 px font.
DEFAULT_LINE_GAP = 0.44

#: Hard ceiling on how many lines a title may wrap onto.
MAX_TITLE_LINES = 3

#: Floor for the fallback that shrinks an over-long title to fit the card.
#: Below this the type stops being legible on a phone, so it overflows
#: (with a warning) rather than shrinking further.
MIN_FONT_SIZE = 14

# --------------------------------------------------------------------------- #
# Card fill
# --------------------------------------------------------------------------- #

#: A flat rectangle of this colour at this opacity, and nothing else. Measured
#: off the reference by comparing footage either side of the card's right edge.
DEFAULT_CARD_COLOR = (0, 0, 0)
DEFAULT_CARD_OPACITY = 0.35

# --------------------------------------------------------------------------- #
# Fonts and brands
# --------------------------------------------------------------------------- #

BUNDLED_FONT = Path(__file__).resolve().parent.parent / "fonts" / "Stem-Medium.otf"
PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


@dataclass(frozen=True)
class Brand:
    """A logo and the colour that goes with it."""

    logo: Path
    #: The brand's corresponding colour: what a wordmark logo is recoloured to,
    #: and what the accent rule under it (or the caption's accent bar, on the
    #: "bar" style) is drawn in.
    color: tuple[int, int, int]
    #: True when `logo` is a plain monochrome wordmark - it gets recoloured to
    #: `color` and picks up the accent rule beneath it. False when the artwork
    #: already carries the brand's colour itself (a filled lockup), in which
    #: case recolouring would flatten it and a rule would double up on it.
    wordmark: bool = True
    #: "card": the original lockup - logo flush above a flat translucent card,
    #: title set inside it. "bar": no card; the caption sits directly on the
    #: footage with a coloured accent bar to its left, and the logo is pinned
    #: near the top of the frame independent of the caption's position.
    #: Reference: `whatsup_new_video_design.jpg`.
    style: str = "card"


BRANDS = {
    "whatsup": Brand(PUBLIC_DIR / "whatsup_logo.png", (199, 246, 46), wordmark=False, style="bar"),
    "ups": Brand(PUBLIC_DIR / "ups_logo.png", (255, 204, 0)),
    "post_id": Brand(PUBLIC_DIR / "post_id_logo.png", (253, 207, 9)),
}

DEFAULT_BRAND = "whatsup"

FONT_CANDIDATES = {
    "Windows": [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ],
    "Darwin": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/Library/Fonts/Arial.ttf",
    ],
    "Linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}

FFMPEG_INSTALL_HINTS = {
    "Windows": "winget install --id Gyan.FFmpeg   (or: choco install ffmpeg-full)",
    "Darwin": "brew install ffmpeg",
    "Linux": "sudo apt install ffmpeg   (Fedora: sudo dnf install ffmpeg)",
}

# --------------------------------------------------------------------------- #
# Logo
# --------------------------------------------------------------------------- #

#: Logo width as a fraction of frame width. Reference: 174 / 900.
DEFAULT_LOGO_RATIO = 0.193
LOGO_MAX_HEIGHT_RATIO = 0.14
DEFAULT_LOGO_OPACITY = 1.0

#: The accent rule under a wordmark logo. It spans the logo's full width, and
#: both measurements are fractions of that *width* rather than of the logo's
#: height: artwork differs in how much tagline it carries under the wordmark,
#: and tying the rule to height would make it thicken on the taller lockups.
#: Reference: an 11 px gap and a 16 px rule under a 174 px-wide logo.
LOGO_RULE_GAP_RATIO = 0.063
LOGO_RULE_HEIGHT_RATIO = 0.092

#: Gap between the bottom of the logo lockup and the top of the card, as a
#: fraction of frame height. The reference sits them flush. Only used on the
#: "card" style - see `LOGO_TOP_RATIO` for "bar".
LOGO_CARD_GAP_RATIO = 0.0

#: Top edge of the logo lockup on the "bar" style, as a fraction of frame
#: height. Unlike "card", this brand's logo is pinned near the top of the
#: frame independent of wherever the caption ends up. Reference: ~270 / 1600.
LOGO_TOP_RATIO = 0.17

# --------------------------------------------------------------------------- #
# "bar" style: caption directly on footage, accent bar to its left
# --------------------------------------------------------------------------- #

#: Accent bar width, as a fraction of the font size. Reference: 8 px at a
#: 33 px font.
ACCENT_BAR_WIDTH_RATIO = 0.24

#: Gap between the accent bar and the caption text, as a fraction of the font
#: size. Reference: 15 px at a 33 px font.
ACCENT_BAR_GAP_RATIO = 0.45

#: Right-edge margin for the "bar" style's caption box, as a fraction of frame
#: width. Unlike "card" - a discrete rectangle that reads as intentional only
#: with an even margin on both sides, hence sharing SIDE_MARGIN_RATIO left and
#: right - "bar" has no rectangle to balance: the reference lets the caption
#: run almost to the frame edge, well past where DEFAULT_CARD_WIDTH_RATIO
#: (measured off the *other* reference, the "card" style's) would cap it.
#: Reference: ~46 / 900.
BAR_RIGHT_MARGIN_RATIO = 0.051

#: A scrim behind the caption, standing in for the "card": solid `BAR_CARD_COLOR`
#: at `BAR_CARD_OPACITY` next to the accent bar, fading horizontally to fully
#: transparent by the right edge of the caption's box. `BAR_CARD_FADE_POWER`
#: bows that fade so it holds darker near the bar - where the text actually
#: sits - instead of thinning out evenly (alpha follows (1 - x/width)^power).
BAR_CARD_COLOR = (0, 0, 0)
BAR_CARD_OPACITY = 0.55
BAR_CARD_FADE_POWER = 1.6

# --------------------------------------------------------------------------- #
# Subliminal flashes
# --------------------------------------------------------------------------- #

DEFAULT_SUBLIMINAL_AT = (0.33, 0.66)
DEFAULT_SUBLIMINAL_MS = 10.0
DEFAULT_SUBLIMINAL_RATIO = 0.35
SUBLIMINAL_BG = (0, 0, 0)
DEFAULT_SUBLIMINAL_OPACITY = 0.1

# --------------------------------------------------------------------------- #
# Speed / aspect / output
# --------------------------------------------------------------------------- #

DEFAULT_SPEED = 1.0
DEFAULT_ASPECT = (9, 16)
DEFAULT_TITLE_DURATION = 4.0
PAD_BG_COLOR = (0, 0, 0)
