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

#: Font size for the "bar" style, as a fraction of min(width, height): sized
#: so a MAX_TITLE_LINES-line caption's box lands at roughly 15% of the
#: frame's height (measured: 0.05 -> 152 px on a 1024 px-tall frame for a
#: 3-line caption, i.e. ~14.8%). Still the pinned-type model - this is *the*
#: size for every "bar" clip. The box itself is pinned too: it is always
#: sized for MAX_TITLE_LINES lines regardless of how many the title actually
#: uses (see graph.py), so a one- or two-line title gets the full-height box,
#: its text centred inside it, rather than a shorter one.
BAR_FONT_RATIO = 0.05

#: The "card" style's max type size. Kept numerically equal to BAR_FONT_RATIO
#: by design - both styles now read at the same size - and expressed as a
#: direct reference so the two can't quietly drift apart; split it into its
#: own literal again if "card" ever needs a different size.
DEFAULT_FONT_RATIO = BAR_FONT_RATIO

#: Vertical position of the "bar" style's box TOP edge, as a fraction of
#: frame height - anchored by its top (not its centre) because the box is
#: pinned at a fixed height (see graph.py): a centred anchor would only
#: matter if the box still grew or shrank with the line count, which it no
#: longer does.
BAR_POSITION_RATIO = 0.62

#: The "card" style's position. Kept numerically equal to BAR_POSITION_RATIO
#: by design - both styles now sit at the same height - see DEFAULT_FONT_RATIO
#: above for why it's a direct reference rather than its own literal. Also
#: anchored by its TOP edge: the logo lockup sits directly above the card, so
#: a centred anchor would shove the logo around whenever the line count
#: changed (it no longer does, but the top anchor is still the simpler one to
#: reason about).
DEFAULT_POSITION_RATIO = BAR_POSITION_RATIO

#: Left margin shared by the card and the logo above it. Reference: 122 / 900.
SIDE_MARGIN_RATIO = 0.136

#: Card width as a fraction of frame width, for both "card"-style brands.
#: Stretched out toward the frame's right edge rather than boxed in with a
#: mirrored right margin (see textfit.card_width) - and, like the "bar"
#: style's box, no longer shrink-wrapped to the actual line count: the card
#: is always sized for MAX_TITLE_LINES lines (see graph.py), so a short
#: title's text sits centred inside the full-height card instead of the card
#: shrinking down to it.
DEFAULT_CARD_WIDTH_RATIO = 0.80

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
    #: Overrides DEFAULT_LOGO_OPACITY for this brand's logo when set; still
    #: overridden in turn by an explicit --logo-opacity. None defers to the
    #: global default.
    opacity: float | None = None


BRANDS = {
    "whatsup": Brand(PUBLIC_DIR / "whatsup_logo.png", (199, 246, 46), wordmark=False, style="bar"),
    "ups": Brand(PUBLIC_DIR / "ups_logo.png", (255, 204, 0)),
    "post_id": Brand(PUBLIC_DIR / "post_id_logo.png", (253, 207, 9)),
    "frt": Brand(PUBLIC_DIR / "FRT_info.png", (251, 127, 1), wordmark=False, style="bar", opacity=0.5),
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

#: Width of the "bar" style's caption box (scrim + accent bar), as a fraction
#: of frame width - fixed, not derived from a right-edge margin. Unlike
#: "card" - a discrete rectangle that reads as intentional only with an even
#: margin on both sides, hence sharing SIDE_MARGIN_RATIO left and right -
#: "bar" has no rectangle to balance, so it stretches out from the shared left
#: margin to a wide fixed fraction of the frame instead of stopping at
#: DEFAULT_CARD_WIDTH_RATIO (measured off the *other* reference, the "card"
#: style's, and much narrower).
BAR_WIDTH_RATIO = 0.80

#: A scrim behind the caption, standing in for the "card": solid `BAR_CARD_COLOR`
#: at `BAR_CARD_OPACITY` next to the accent bar, fading horizontally to fully
#: transparent by the right edge of the caption's box. `BAR_CARD_FADE_POWER`
#: bows that fade so it holds darker near the bar - where the text actually
#: sits - instead of thinning out evenly (alpha follows (1 - x/width)^power).
BAR_CARD_COLOR = (0, 0, 0)
BAR_CARD_OPACITY = 0.55
BAR_CARD_FADE_POWER = 1.6

# --------------------------------------------------------------------------- #
# Video-source credit line
# --------------------------------------------------------------------------- #

#: Type size for the small "Video: Channel/Platform" credit line, as a
#: fraction of min(width, height) - a footnote under the title block, not
#: another caption, so it sits well below BAR_FONT_RATIO/DEFAULT_FONT_RATIO.
CREDIT_FONT_RATIO = 0.035

#: Gap between the bottom of the title block (card, scrim, or the --no-box
#: text) and the top of the credit line, as a fraction of frame height.
CREDIT_GAP_RATIO = 0.045

#: Regular (non-bold) system fonts for the credit line - it should read as a
#: quiet footnote, not compete with the title's bold display type. Nothing
#: bundled in fonts/ ships a regular weight (Stem-Medium and HeliosExtC-Bold
#: are both heavy), so this always falls back to whatever the OS provides;
#: if none of these exist, the credit line falls back to the title's own font
#: (see assets.resolve_credit_font).
CREDIT_FONT_CANDIDATES = {
    "Windows": [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ],
    "Darwin": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
    ],
    "Linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ],
}

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
