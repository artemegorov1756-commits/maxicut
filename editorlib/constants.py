"""Every tunable default, ported verbatim from video_editor.py.

None of these numbers changed meaning when the renderer moved from MoviePy to
ffmpeg - a ratio of the frame is the same ratio regardless of what draws it -
so they are carried over unchanged, including the rationale in each comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Title card layout
# --------------------------------------------------------------------------- #

#: Font size as a fraction of the frame's *shorter-ish* reference dimension.
DEFAULT_FONT_RATIO = 0.045

#: Vertical centre of the title block, as a fraction of frame height.
DEFAULT_POSITION_RATIO = 0.70

#: How much of the *screen* the card covers, by area.
DEFAULT_BOX_RATIO = 0.16

#: Extra vertical trim applied to the card after DEFAULT_BOX_RATIO sizing.
CARD_HEIGHT_SCALE = 0.7

#: How much of the card the title fills, per axis.
DEFAULT_TITLE_FILL = 0.80

#: Padding between text and glass edge, as a fraction of font size. Only used
#: off the fixed-card path (--no-box).
PAD_X_RATIO = 0.70
PAD_Y_RATIO = 0.40

#: Gap between lines of a wrapped title, as a fraction of the font size.
DEFAULT_LINE_GAP = 0.30

#: Hard ceiling on how many lines a title may wrap onto.
MAX_TITLE_LINES = 2

#: How much bigger the emphasised line is rendered, as a multiplier.
DEFAULT_EMPHASIS_RATIO = 1.4

#: Which of a two-line title's lines gets the bigger size by default.
DEFAULT_EMPHASIS_LINE = "first"

#: Font size used to take per-unit measurements for the layout search.
PROBE_SIZE = 400

# --------------------------------------------------------------------------- #
# Glass panel
# --------------------------------------------------------------------------- #

#: Frosted-blur radius (ffmpeg gblur sigma), as a fraction of min(w, h).
DEFAULT_GLASS_BLUR = 0.03

#: How much white is milked into the panel: 0 clear glass, 1 opaque paint.
DEFAULT_GLASS_TINT = 0.18

#: How far the colour wash goes at the bottom edge, 0-1.
DEFAULT_GLASS_WASH = 1.0

#: How far up the card the wash reaches, as a fraction of its height.
GLASS_WASH_REACH = 1.0

#: How deep into the panel the bevel/rim reaches, fraction of the short side.
EDGE_RATIO = 0.16

# --------------------------------------------------------------------------- #
# Fonts and brands
# --------------------------------------------------------------------------- #

BUNDLED_FONT = Path(__file__).resolve().parent.parent / "fonts" / "HeliosExtC-Bold.ttf"
PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


@dataclass(frozen=True)
class Brand:
    """A logo and the colour the card's gradient takes underneath it."""

    logo: Path
    wash_color: tuple[int, int, int]


BRANDS = {
    "jvg": Brand(PUBLIC_DIR / "jvg_logo.png", (187, 32, 38)),
    "ups": Brand(PUBLIC_DIR / "ups_logo.png", (241, 162, 35)),
    "post_id": Brand(PUBLIC_DIR / "post_id_logo.png", (41, 69, 213)),
}

DEFAULT_BRAND = "jvg"

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

DEFAULT_LOGO_RATIO = 0.20
LOGO_MAX_HEIGHT_RATIO = 0.14
DEFAULT_LOGO_OPACITY = 0.7
LOGO_PAD_X_RATIO = 0.06
LOGO_PAD_Y_RATIO = 0.09
LOGO_SHADOW_BLUR = 0.18
LOGO_SHADOW_OPACITY = 0.45

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

DEFAULT_SPEED = 1.1
DEFAULT_ASPECT = (9, 16)
DEFAULT_TITLE_DURATION = 4.0
PAD_BG_COLOR = (0, 0, 0)
