# ffmpeg_editor.py

Burns a lower-third title lockup onto a video and renders it to MP4. Nothing
here imports MoviePy: every actual video operation (pad, overlay, fade, speed
change, encode) runs as a single ffmpeg `-filter_complex` graph.

```
python ffmpeg_editor.py clip.mp4 "Dr. Ada Lovelace" -o titled.mp4
python ffmpeg_editor.py clip.mp4 "Same Day Delivery" --brand ups
python ffmpeg_editor.py https://example.com/clip.mp4 "Live from Berlin"
python ffmpeg_editor.py clip.mp4 "9 Months on the Front Line" --video-source DmitriyGrinich1/X
```

Requires `ffmpeg` and `ffprobe` on `PATH` (a build with `libx264`; add
`h264_nvenc` if you want `--gpu`), plus `pillow` and `numpy` for Python.

## The design

Each brand picks one of two lockup styles (`Brand.style` in `constants.py`).

**`card`** (`ups`, `post_id`) - three flat layers, measured off
`new_video_design.jpg`:

```
   +--------------+
   |  LOGO        |          logo, recoloured to the brand's colour
   |==============|          accent rule, full logo width
   +---------------------------------------+
   | TITLE SET LEFT-ALIGNED IN STEM MEDIUM  |   flat translucent card
   | ACROSS UP TO THREE LINES               |   (black @ 35%, square corners)
   +---------------------------------------+
```

Both parts share a left margin of `0.136 x width`, and the logo sits flush on
top of the card, so the two read as one lockup and fade in and out together.

**`bar`** (`whatsup`) - no flat card. The logo is pinned near the top of the
frame on its own, independent of the caption below it; the caption sits on a
scrim instead of a card - `BAR_CARD_COLOR` at `BAR_CARD_OPACITY`, fading
horizontally to fully transparent by the right edge of the caption's box
(`BAR_CARD_FADE_POWER` bows the fade so it holds darker near the accent bar,
where the text actually starts) - plus the same stroke + drop shadow as
`--no-box` for legibility past where the scrim has faded out, and a solid
accent bar in the brand's colour to its left. Measured off the reference
screenshots in the whatsup brand brief:

```
   +--------------+
   |  ЩО ТАМ?     |          logo, pinned near the top of the frame
   +--------------+          (own colours, not recoloured)

              ...            (independent of the caption's position)

   | TITLE SET LEFT-ALIGNED IN STEM MEDIUM
   | ACROSS UP TO THREE LINES               <- accent bar, full caption height
```

The logo and the caption's left edge still share the same `0.136 x width`
margin, but nothing else ties their positions together - the logo's top edge
is a fixed fraction of frame height (`LOGO_TOP_RATIO`) instead of following
the caption up and down as the line count changes.

Like `card` (see below), the `bar` scrim + accent bar don't shrink-wrap to
the actual line count: the box is always sized as if the title used all
`MAX_TITLE_LINES` lines, landing at a fixed ~18% of frame height
(`BAR_FONT_RATIO`). A one- or two-line title's text is simply centred inside
that fixed box instead of the box shrinking down with it. `bar` also anchors
`--position` a little higher than `card` does (`BAR_POSITION_RATIO` vs.
`DEFAULT_POSITION_RATIO`), since the box no longer shrinks to sit closer to
the bottom on short titles.

Either style is removed by `--no-box` (drops the card, or the bar - the
caption's own stroke + shadow is what's left).

**The type size is fixed**, which is the inverse of what this used to do.
`--font-ratio` sets the size as a fraction of `min(width, height)` and
`--card-width` sets the card's width as a fraction of frame width; the title
wraps inside that width onto at most `MAX_TITLE_LINES` (3) lines. A short
title and a long one therefore render at the same size, where the old
grow-to-fill behaviour would have set one huge and the other small.

Neither style's box grows or shrinks with the line count any more: both
`card` and `bar` are pinned at `MAX_TITLE_LINES` lines' worth of height
regardless of how many lines the title actually uses (see the note above).
`card`'s width is likewise fixed at `--card-width` (default
`DEFAULT_CARD_WIDTH_RATIO`, ~80% of frame width) rather than being sized to
the text, stretched out from the shared left margin toward the frame's right
edge the same way `bar`'s box is (see `textfit.card_width`).

Card/box height is computed from font metrics (ascent + descent + leading per
line), not from the rendered bitmap, so two titles that wrap to the same number
of lines always get identically sized cards - a bitmap measurement would make
the card shrink whenever a title happened to contain no descender or accent.
The text ink is then centred inside that card/box.

`--position` is the card's **top** edge, not its centre, because the logo
hangs off the top of the card: anchoring by the centre would shove the logo up
and down the frame every time the line count changed.

### Video-source credit

`--video-source CHANNEL/PLATFORM` (e.g. `--video-source DmitriyGrinich1/X`)
burns in a small `Video: CHANNEL/PLATFORM` line under the title block - plain
text, no background box, set in a regular (non-bold) system font rather than
the title's bold display type (`assets.resolve_credit_font`; falls back to
the title's own font if the OS has none of the regular-weight candidates in
`CREDIT_FONT_CANDIDATES`). It shares the left margin with the card/logo
(`SIDE_MARGIN_RATIO`), but not the card/title's fade envelope: like the logo,
it fades in on the title's cue and then stays on screen for the rest of the
video instead of disappearing when the title fades out. Omitted entirely
when the flag isn't passed. Tunables live in `constants.py`
(`CREDIT_FONT_RATIO`, `CREDIT_GAP_RATIO`, `CREDIT_FONT_CANDIDATES`).

### Brands

Each brand is a logo plus the colour that goes with it:

| brand | colour | logo |
| --- | --- | --- |
| `whatsup` (default) | `#c7f62e` | `public/whatsup_logo.png` |
| `ups` | `#ffcc00` | `public/ups_logo.png` |
| `post_id` | `#fdcf09` | `public/post_id_logo.png` |

`Brand.wordmark` decides how the colour is used. A wordmark logo is
monochrome artwork, so it gets repainted in the brand colour and picks up the
accent rule beneath it. `whatsup` sets `wordmark=False`: its artwork is a
filled lockup that already carries the brand's lime, and repainting it would
flatten the knockout text while a rule would just double up on the fill.
`--logo-color` overrides either way, `--no-logo-color` opts out entirely.

`Brand.style` picks the lockup layout - `card` for `post_id`, `bar` for
`whatsup`/`ups`. See "The design" above.

## How it's organised

```
ffmpeg_editor.py       CLI entry point: argparse + orchestration (run())
editorlib/
  download.py           URL/local-file resolution (pure urllib)
  probe.py               ffprobe wrapper (width/height/duration/fps/sample rate)
  textfit.py              Layout measurements and title wrapping (pure PIL metrics)
  constants.py             Every tunable default, measured off the design reference
  assets.py                 Everything precomputed once with PIL/numpy: the card,
                             the text bitmap, and the logo lockup
  subliminal.py              Frame-exact timing for the hidden logo flashes
  graph.py                    Builds the whole ffmpeg -filter_complex graph
  render.py                    Runs ffmpeg, then ffplay for the preview
```

## What used to be here: the liquid-glass panel

The card was a frosted-glass panel. Drawing it meant splitting the source
stream, cropping the region under the card, running two `gblur` passes at
different radii, blending them through a rim-bevel mask with `maskedmerge`,
overlaying a white tint map and a brand-coloured gradient wash, and
re-attaching an alpha plane with `alphamerge` - all so the panel could frost
the *live* footage beneath it every frame, plus a two-layer elevation shadow
underneath and a soft halo behind the text.

The design reference has none of it. Sampling the reference confirmed the
card is flat: high-frequency detail inside the card matches the footage just
outside it (no blur), the edge transition is ~6 px (no drop shadow), and the
fill is uniform top to bottom (no gradient). So the card is now one solid RGBA
image, nothing in the graph reads the footage under it, and `GlassMaps` and
its five static maps are gone. The text halo survives only on the `--no-box`
path, where the text does sit directly on footage and needs its own contrast.

Two behaviours that came with the old panel went with it:

- **Per-line emphasis.** `--emphasis-line` / `--emphasis-ratio` set one line of
  a two-line title larger than the other. The reference sets all lines at one
  size, and it only ever made sense alongside grow-to-fill sizing.
- **The corner logo.** The logo used to sit in the top-left corner with its own
  drop shadow, on screen for the whole clip. It is now part of the title
  lockup, so it appears and fades with the card.

## `--gpu` only changes the encoder

The flag swaps `-c:v libx264` for `-c:v h264_nvenc` at the final encode step
and nothing else; every filter in the graph is ordinary ffmpeg CPU work.

## One easy-to-miss detail: audio pitch on `--speed`

Audio speed uses ffmpeg's `asetrate=<original_rate*speed>,aresample=<original_rate>`
trick, **not** `atempo` or `rubberband`. That is deliberate: it is a naive
resample, not a pitch-preserving time-stretch, so pitch rises with speed
exactly like physically playing a tape faster. `--speed` is off by default
(1.0x, unchanged); pass e.g. `--speed 1.1` to opt in - subtle there, more
obvious higher. It matches the behaviour of MoviePy's `MultiplySpeed`, which
this pipeline was ported from.

## Testing notes

Validated end-to-end against a synthetic `testsrc2` clip: the default card
path at the reference's own resolution and title (geometry checked against
`new_video_design.jpg` pixel-for-pixel), a one-line title, `--no-box`, and
`--no-pad --no-speed`.

Bugs found and fixed in earlier rounds of testing, still worth knowing about:

- ffmpeg's `crop` filter silently rounds odd width/height down to even unless
  `exact=1` is set. All `crop` calls in `graph.py` pass `exact=1`.
- Pillow's `multiline_textbbox` can return fractional coordinates; the text
  canvas size in `assets.render_text_image` is rounded outward before use.
- `-f lavfi` inputs negotiate their own pixel format before handing frames to
  `filter_complex`, and with no alpha-aware filter in that internal graph they
  silently pick opaque `yuv420p`. The transparent canvas the lockup composites
  onto therefore bakes `format=rgba` into the lavfi source string itself, not
  as a later filter step.

`--gpu` (h264_nvenc) is implemented but untested here for lack of NVENC
hardware in the test environment.
