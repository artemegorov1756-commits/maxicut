"""Assemble one ffmpeg -filter_complex graph that composites the title lockup.

Every layer is an ffmpeg `overlay` of a *static* PNG that Python drew once
with PIL - card, text, logo - each stacked onto its own transparent full-frame
canvas, then overlaid on the footage. Card + text share one fade envelope and
disappear together after `--duration`; the logo gets its own envelope that
fades in on the same cue but never fades out, so it stays on screen for the
rest of the video instead of vanishing with the title.

This used to be considerably more involved. The card was a liquid-glass panel,
which meant splitting the source, cropping the region under the card, running
two `gblur` passes at different radii, blending them through a rim mask with
`maskedmerge`, overlaying tint and wash maps, and re-attaching an alpha plane
with `alphamerge` - all so the panel could frost the *live* footage beneath it
every frame. The design reference has a flat translucent card, so all of that
is gone; nothing in the graph reads the footage under the card any more.

The whole graph is built in the source video's own pre-speed timeline
(seconds and frame numbers both refer to the untouched source); `--speed` is
applied once, at the very end, to the fully-composited stream, so overlay
timing never has to account for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import assets
from .constants import (
    ACCENT_BAR_GAP_RATIO,
    ACCENT_BAR_WIDTH_RATIO,
    BAR_CARD_COLOR,
    BAR_CARD_FADE_POWER,
    BAR_CARD_OPACITY,
    BAR_WIDTH_RATIO,
    LOGO_CARD_GAP_RATIO,
    LOGO_TOP_RATIO,
    MAX_TITLE_LINES,
    MIN_FONT_SIZE,
)
from .probe import VideoInfo
from .subliminal import compute_subliminal_plan
from .textfit import (
    card_width,
    compute_layout,
    layout_for,
    max_text_width,
    text_block_height,
    wrap_title,
)


class FilterGraph:
    """Minimal builder for an ffmpeg -filter_complex string plus its inputs.

    Every extra input (image assets, lavfi sources) is appended to `inputs` in
    the order added; its ffmpeg input index is implied by that order, with
    input 0 reserved for the source video by convention (added by the caller
    first).
    """

    def __init__(self) -> None:
        self.inputs: list[list[str]] = []
        self.chains: list[str] = []
        self._n = 0

    def add_video_input(self, path: Path) -> str:
        idx = len(self.inputs)
        self.inputs.append(["-i", str(path)])
        return f"{idx}:v"

    def add_image_input(self, path: Path) -> str:
        idx = len(self.inputs)
        # Looped so it keeps supplying frames for as long as any downstream
        # overlay demands one, regardless of how long the main video runs.
        self.inputs.append(["-loop", "1", "-i", str(path)])
        return f"{idx}:v"

    def add_lavfi_input(self, spec: str) -> str:
        idx = len(self.inputs)
        self.inputs.append(["-f", "lavfi", "-i", spec])
        return f"{idx}:v"

    def label(self, hint: str = "s") -> str:
        self._n += 1
        return f"{hint}{self._n}"

    def chain(self, inputs: list[str], filt: str, outputs: list[str]) -> None:
        in_s = "".join(f"[{i}]" for i in inputs)
        out_s = "".join(f"[{o}]" for o in outputs)
        self.chains.append(f"{in_s}{filt}{out_s}")

    def filter_complex(self) -> str:
        return ";".join(self.chains)


@dataclass
class BuildResult:
    graph: FilterGraph
    video_map: str  # e.g. "[vout]"
    audio_map: str | None  # e.g. "0:a" or "[aout]"
    temp_files: list[Path]
    log: list[str]
    width: int
    height: int


def _even(n: int) -> int:
    return n - n % 2


def _overlay(x: int = 0, y: int = 0, enable: str | None = None) -> str:
    """An `overlay` filter that stops when its *shortest* input runs out.

    `shortest=1` is load-bearing, not a tidy-up. Every image asset here is fed
    in with `-loop 1`, which never reaches EOF, and overlay's framesync
    otherwise runs until *all* of its inputs are exhausted - so a single
    looped PNG keeps the graph producing frames long after the footage has
    ended, repeating the last frame forever. Left off, a 6-second clip encodes
    for as long as you let it run (measured: 26+ minutes of output and still
    going). With it, each overlay ends with whichever of its inputs is
    genuinely finite: the transparent canvas for the lockup's own layers, and
    the footage itself for the composite onto the main timeline.
    """
    filt = f"overlay={x}:{y}:format=auto:shortest=1"
    return filt if enable is None else f"{filt}:enable='{enable}'"


def compute_pad_geometry(
    source_w: int, source_h: int, aspect_w: int, aspect_h: int
) -> tuple[float, int, int, int, int, int, int]:
    """Port of `pad_to_aspect`'s canvas/scale/position math (pure arithmetic).

    Returns (scale, scaled_w, scaled_h, canvas_w, canvas_h, x, y). The canvas
    is sized from the source's own long edge, so footage is only ever scaled
    *down* to fit, never blown up past its native resolution.
    """
    long_edge = max(source_w, source_h)
    if aspect_w <= aspect_h:
        canvas_w, canvas_h = round(long_edge * aspect_w / aspect_h), long_edge
    else:
        canvas_w, canvas_h = long_edge, round(long_edge * aspect_h / aspect_w)

    scale = min(canvas_w / source_w, canvas_h / source_h, 1.0)
    scaled_w = round(source_w * scale) if scale < 1.0 else source_w
    scaled_h = round(source_h * scale) if scale < 1.0 else source_h
    x, y = (canvas_w - scaled_w) // 2, (canvas_h - scaled_h) // 2
    return scale, scaled_w, scaled_h, canvas_w, canvas_h, x, y


def build(
    *,
    args,
    source_path: Path,
    info: VideoInfo,
    font: str,
    font_ratio: float,
    position: float,
    brand,
    logo_color: tuple[int, int, int] | None,
    logo_path: Path | None,
    subliminal_path: Path | None,
    speed: float,
    duration_title: float,
    fade: float,
    tmp_dir: Path,
) -> BuildResult:
    g = FilterGraph()
    temp_files: list[Path] = []
    log: list[str] = []

    def save_png(image: Image.Image, name: str) -> Path:
        path = tmp_dir / name
        image.save(path)
        temp_files.append(path)
        return path

    # ----------------------------------------------------------------- #
    # Even dimensions + aspect padding (mirrors make_dimensions_even and
    # pad_to_aspect, run in the same order: even the raw input, THEN pad,
    # THEN even the result - a scale/pad pass can itself land on an odd size).
    # ----------------------------------------------------------------- #
    cur = g.add_video_input(source_path)
    width, height = info.width, info.height

    even_w, even_h = _even(width), _even(height)
    if (even_w, even_h) != (width, height):
        nxt = g.label("evensrc")
        g.chain([cur], f"crop={even_w}:{even_h}:0:0:exact=1", [nxt])
        cur = nxt
        log.append(f"      cropping {width}x{height} -> {even_w}x{even_h} for yuv420p")
    width, height = even_w, even_h

    if not args.no_pad:
        scale, scaled_w, scaled_h, canvas_w, canvas_h, x, y = compute_pad_geometry(
            width, height, *args.aspect
        )
        if scale < 1.0:
            nxt = g.label("scaled")
            g.chain([cur], f"scale={scaled_w}:{scaled_h}:flags=lanczos", [nxt])
            cur = nxt
        if (canvas_w, canvas_h) != (scaled_w, scaled_h):
            nxt = g.label("padded")
            g.chain([cur], f"pad={canvas_w}:{canvas_h}:{x}:{y}:color=black", [nxt])
            cur = nxt
            log.append(
                f"      padding {width}x{height} -> {canvas_w}x{canvas_h} "
                f"({args.aspect[0]}:{args.aspect[1]}, black background)"
            )
        width, height = canvas_w, canvas_h

        even_w, even_h = _even(width), _even(height)
        if (even_w, even_h) != (width, height):
            nxt = g.label("evenpad")
            g.chain([cur], f"crop={even_w}:{even_h}:0:0:exact=1", [nxt])
            cur = nxt
            log.append(f"      cropping {width}x{height} -> {even_w}x{even_h} for yuv420p")
        width, height = even_w, even_h

    video_main = cur
    box_requested = not args.no_box
    # "card": the flat translucent rectangle behind the title. "bar": no card -
    # the title sits on the footage with a coloured accent bar to its left
    # instead. Both are what --no-box removes, per brand.
    use_card = box_requested and brand.style == "card"
    use_bar = box_requested and brand.style == "bar"
    caps = not args.no_caps

    log.append(f"      {width}x{height}, {info.duration:.2f}s, {info.fps:g} fps")

    # ----------------------------------------------------------------- #
    # Layout + title bitmap.
    #
    # The type size is fixed by --font-ratio and the card's width by
    # --card-width; on "card" what varies is the card's *height*, which
    # follows from how many lines the title wraps onto (measured from font
    # metrics rather than from the rendered bitmap, so that two titles
    # wrapping to the same number of lines always get identically sized
    # cards, whether or not their glyphs happen to carry descenders or
    # accents). "bar" pins its box height too - see the box_lines comment
    # below - so a short title's text is centred inside a fixed-size box
    # instead of the box shrinking to it.
    # ----------------------------------------------------------------- #
    text_source = args.title.upper() if caps else args.title
    align = "left" if (use_card or use_bar) else "center"
    design_size = compute_layout(width, height, font_ratio, args.line_gap).font_size

    # The "bar" style's caption box: starts at SIDE_MARGIN_RATIO on the left
    # (shared with the logo above it) and stretches out to BAR_WIDTH_RATIO of
    # the frame width - not --card-width, which is the "card" style's own,
    # much narrower, width ratio.
    def bar_card_width(side_margin: int) -> int:
        return max(1, min(round(width * BAR_WIDTH_RATIO), width - side_margin))

    def lay_out(size: int):
        """Everything about the title block that depends on the type size.

        Re-derived per size rather than scaled, because the card's padding is
        itself a fraction of the font size: shrink the type and the space it
        has to fit into grows a little, which a single scale factor misses.
        """
        lay = layout_for(width, height, size, args.line_gap)
        # A card supplies its own contrast; the bar style sits the title
        # directly on footage (like the no-box path) so it needs the same
        # stroke to hold up against busy backgrounds.
        pen = 0 if use_card else lay.stroke_width
        if use_card:
            room = max(1, card_width(width, args.card_width, lay.side_margin) - 2 * lay.card_pad_x)
        elif use_bar:
            bar_w = max(1, round(lay.font_size * ACCENT_BAR_WIDTH_RATIO))
            bar_gap = max(1, round(lay.font_size * ACCENT_BAR_GAP_RATIO))
            room = max(1, bar_card_width(lay.side_margin) - bar_w - bar_gap)
        else:
            room = max_text_width(width, lay)
        margin_x, _ = assets.safety_margin(font, lay.font_size, pen)
        text = wrap_title(
            text_source, font, lay.font_size, pen, max(1, room - 2 * margin_x), MAX_TITLE_LINES
        )
        return lay, pen, room, text, assets.render_text_image(
            text, font, lay.font_size, pen, lay.line_gap, align=align
        )

    layout, stroke, avail_w, wrapped, text_img = lay_out(design_size)

    shrunk_from = None
    if text_img.width > avail_w:
        # A title long enough to still overflow after wrapping onto
        # MAX_TITLE_LINES lines cannot be fixed by wrapping - there are no more
        # lines to give it - so find the largest size that does fit rather than
        # let it run off the card. This is a fallback, not the sizing model:
        # every title that fits at --font-ratio renders at exactly that size,
        # which is the whole point of pinning it.
        #
        # Searched rather than scaled. One proportional step off the overflow
        # ratio overshoots hard (measured: 33 px -> 15 px where 26 px fits),
        # because a smaller size also lets the wrap redistribute words across
        # the lines - so the widest line does not shrink linearly with the type.
        shrunk_from = layout.font_size
        best, lo, hi = None, MIN_FONT_SIZE, design_size
        while lo <= hi:
            mid = (lo + hi) // 2
            trial = lay_out(mid)
            if trial[4].width <= trial[2]:
                best, lo = trial, mid + 1
            else:
                hi = mid - 1
        layout, stroke, avail_w, wrapped, text_img = best or lay_out(MIN_FONT_SIZE)

    lines = wrapped.count("\n") + 1
    pad_y = layout.card_pad_y if use_card else layout.pad_y
    # Neither "card" nor "bar" shrink-wraps its box to the actual line count:
    # the card/scrim/accent-bar is always sized as if the title used all
    # MAX_TITLE_LINES, so the box holds at a fixed height and a short title's
    # text just sits centred inside it instead of shrinking the box down with
    # it. Only the boxless --no-box path (neither use_card nor use_bar) still
    # sizes its (invisible) block to the actual line count.
    box_lines = MAX_TITLE_LINES if (use_card or use_bar) else lines
    card_h = text_block_height(font, layout.font_size, box_lines, layout.line_gap, stroke) + 2 * pad_y

    if use_card or use_bar:
        card_w = (
            card_width(width, args.card_width, layout.side_margin)
            if use_card
            else bar_card_width(layout.side_margin)
        )
        card_x = layout.side_margin
    else:
        card_w = min(width - 2 * layout.side_margin, text_img.width + 2 * layout.pad_x)
        card_x = max(0, (width - card_w) // 2)

    if shrunk_from is not None:
        log.append(
            f"      note: the title does not fit on {MAX_TITLE_LINES} lines at {shrunk_from} px, "
            f"so the type was stepped down to {layout.font_size} px - raise --card-width to keep "
            "it at full size"
        )
    if text_img.width > avail_w:
        log.append(
            f"      warning: the title still needs {text_img.width} px of width at the smallest "
            f"size allowed ({MIN_FONT_SIZE} px) but the card's interior is only {avail_w} px"
        )

    # --position is the card's TOP edge, not its centre. On the "card" style
    # the logo lockup sits directly above the card, so anchoring by the centre
    # would shove the logo up and down the frame every time the line count
    # changed; on "bar" the logo is pinned independently (see LOGO_TOP_RATIO)
    # but the card's top edge is still the more stable anchor of the two.
    card_y = round(height * position)
    card_y = max(0, min(card_y, max(0, height - layout.bottom_margin - card_h)))

    bar_w = bar_gap = 0
    if use_bar:
        bar_w = max(1, round(layout.font_size * ACCENT_BAR_WIDTH_RATIO))
        bar_gap = max(1, round(layout.font_size * ACCENT_BAR_GAP_RATIO))

    if use_card:
        text_x = card_x + layout.card_pad_x
    elif use_bar:
        text_x = card_x + bar_w + bar_gap
    else:
        text_x = card_x + (card_w - text_img.width) // 2
    text_y = card_y + (card_h - text_img.height) // 2

    # ----------------------------------------------------------------- #
    # The lockup: card + text, composited on a transparent full-frame canvas
    # so one fade envelope covers both (verified pattern: color@0.0 ->
    # format=rgba -> chained overlays -> fade -> single overlay onto the main
    # timeline). The logo gets its own canvas and fade envelope below, so it
    # can outlast this group instead of fading out with it.
    # ----------------------------------------------------------------- #
    # format=rgba must be baked into the lavfi source string itself, not
    # applied as a later filter_complex step: -f lavfi inputs negotiate their
    # own pixel format internally before handing frames to filter_complex, and
    # with no alpha-aware filter in that internal graph it silently picks
    # opaque yuv420p, discarding the "@0.0" alpha and leaving a solid black
    # frame-sized rectangle that overlay would then draw on top of the video.
    canvas = g.add_lavfi_input(
        f"color=c=black@0.0:s={width}x{height}:r={info.fps}:d={info.duration + 1.0:.3f},format=rgba"
    )
    t = canvas

    if use_card:
        card_img = assets.build_card_image(card_w, card_h, args.card_color, args.card_opacity)
        card_in = g.add_image_input(save_png(card_img, "card.png"))
        nxt = g.label("t")
        g.chain([t, card_in], _overlay(card_x, card_y), [nxt])
        t = nxt
    else:
        # Off the card path the text sits directly on the footage and needs
        # its own contrast - a drop shadow behind the glyphs, plus (on "bar")
        # a solid accent bar to their left.
        if use_bar:
            scrim_img = assets.build_gradient_card_image(
                card_w, card_h, BAR_CARD_COLOR, BAR_CARD_OPACITY, BAR_CARD_FADE_POWER
            )
            scrim_in = g.add_image_input(save_png(scrim_img, "scrim.png"))
            nxt = g.label("t")
            g.chain([t, scrim_in], _overlay(card_x, card_y), [nxt])
            t = nxt

            bar_img = assets.build_card_image(bar_w, card_h, brand.color, 1.0)
            bar_in = g.add_image_input(save_png(bar_img, "bar.png"))
            nxt = g.label("t")
            g.chain([t, bar_in], _overlay(card_x, card_y), [nxt])
            t = nxt

        text_alpha = np.asarray(text_img.getchannel("A"), dtype=np.float32) / 255.0
        shadow_img, shadow_pad = assets.soft_shadow_from_alpha(
            text_alpha, max(2, round(layout.font_size * 0.30)), 0.55
        )
        shadow_in = g.add_image_input(save_png(shadow_img, "text_shadow.png"))
        nxt = g.label("t")
        g.chain(
            [t, shadow_in],
            _overlay(text_x - shadow_pad, text_y - shadow_pad),
            [nxt],
        )
        t = nxt

    text_in = g.add_image_input(save_png(text_img, "text.png"))
    nxt = g.label("t")
    g.chain([t, text_in], _overlay(text_x, text_y), [nxt])
    t = nxt

    kind = "card" if use_card else "scrim + bar + text" if use_bar else "text block"
    log.append(
        f"      {kind} {card_w}x{card_h} px "
        f"at ({card_x}, {card_y}) - "
        f"{card_w * card_h / (width * height):.0%} of frame, "
        f"{lines} line{'s' if lines != 1 else ''} at {layout.font_size} px, "
        f"title {text_img.width}x{text_img.height} px"
        + (
            f" - fill rgb{tuple(args.card_color)} @ {args.card_opacity:.0%}"
            if use_card
            else (
                f" - scrim rgb{BAR_CARD_COLOR} @ {BAR_CARD_OPACITY:.0%} fading right, "
                f"bar rgb{tuple(brand.color)} {bar_w}px wide"
            )
            if use_bar
            else ""
        )
    )

    end = args.start + duration_title
    if fade > 0:
        faded = g.label("titlefaded")
        g.chain(
            [t],
            f"fade=t=in:st={args.start:.6f}:d={fade:.6f}:alpha=1,"
            f"fade=t=out:st={end - fade:.6f}:d={fade:.6f}:alpha=1",
            [faded],
        )
        t = faded
        enable = None
    else:
        enable = f"between(t,{args.start:.6f},{end:.6f})"

    after_title = g.label("aftertitle")
    g.chain([video_main, t], _overlay(enable=enable), [after_title])
    cur = after_title

    # ----------------------------------------------------------------- #
    # Logo lockup - fades in with the title (same start/fade window) but,
    # unlike the card and text, does NOT fade out with it: it is composited
    # on its own transparent canvas and overlaid on top of the already-
    # title-faded stream, so it stays on screen for the rest of the video
    # instead of disappearing after --duration seconds. On "card" it sits
    # flush on top of the card, left-aligned with it; on "bar" it is pinned
    # near the top of the frame, independent of wherever the caption lands.
    # ----------------------------------------------------------------- #
    if logo_path is not None:
        logo_img = assets.build_logo_image(
            logo_path, width, height, args.logo_ratio, logo_color, brand.wordmark
        )
        if args.logo_opacity < 1.0:
            faded_alpha = (
                np.asarray(logo_img.getchannel("A"), dtype=np.float32) * args.logo_opacity
            )
            logo_img = Image.fromarray(
                np.dstack(
                    [
                        np.asarray(logo_img.convert("RGB")),
                        np.clip(faded_alpha, 0, 255).astype(np.uint8),
                    ]
                ),
                mode="RGBA",
            )

        if brand.style == "bar":
            lx = layout.side_margin
            ly = round(height * LOGO_TOP_RATIO)
        else:
            lx = card_x
            ly = card_y - round(height * LOGO_CARD_GAP_RATIO) - logo_img.height
            if ly < 0:
                log.append(
                    f"      note: the logo lockup needs {logo_img.height} px above the card but "
                    f"only {card_y} px are free - pinning it to the top of the frame"
                )
                ly = 0

        logo_canvas = g.add_lavfi_input(
            f"color=c=black@0.0:s={width}x{height}:r={info.fps}:d={info.duration + 1.0:.3f},format=rgba"
        )
        logo_in = g.add_image_input(save_png(logo_img, "logo.png"))
        logo_t = g.label("logot")
        g.chain([logo_canvas, logo_in], _overlay(lx, ly), [logo_t])

        if fade > 0:
            logo_faded = g.label("logofaded")
            g.chain(
                [logo_t],
                f"fade=t=in:st={args.start:.6f}:d={fade:.6f}:alpha=1",
                [logo_faded],
            )
            logo_t = logo_faded
            logo_enable = None
        else:
            logo_enable = f"gte(t,{args.start:.6f})"

        after_logo = g.label("afterlogo")
        g.chain([cur, logo_t], _overlay(enable=logo_enable), [after_logo])
        cur = after_logo

        log.append(
            f"      logo {logo_img.width}x{logo_img.height} px at ({lx}, {ly}) - "
            f"{logo_path.name}, visible from {args.start:.2f}s to the end of the video"
            + (
                f", recoloured rgb{tuple(logo_color)} with accent rule"
                if logo_color is not None and brand.wordmark
                else ", artwork's own colours"
            )
        )

    # ----------------------------------------------------------------- #
    # Subliminal flashes - drawn last, full-frame, single-frame-exact via
    # ffmpeg's own output frame counter (`n`), gated per moment.
    # ----------------------------------------------------------------- #
    if subliminal_path is not None:
        frame_img = assets.build_subliminal_frame(subliminal_path, width, height, args.subliminal_ratio)
        alpha_val = round(255 * args.subliminal_opacity)
        rgba = np.dstack(
            [np.asarray(frame_img), np.full((height, width), alpha_val, dtype=np.uint8)]
        )
        sub_in = g.add_image_input(save_png(Image.fromarray(rgba, mode="RGBA"), "subliminal.png"))

        plan = compute_subliminal_plan(info.fps, info.duration, list(args.subliminal_at), args.subliminal_ms, speed)
        log.append(
            f"      subliminal {frame_img.width}x{frame_img.height} px frames from "
            f"{subliminal_path.name} - {plan.frame_count} frame"
            f"{'s' if plan.frame_count > 1 else ''} ({plan.actual_ms:.0f} ms) at {args.subliminal_opacity:.0%}"
        )
        if args.subliminal_ms < 1000 / info.fps:
            log.append(
                f"      note: {args.subliminal_ms:g} ms is shorter than one frame at "
                f"{info.fps:g} fps ({1000 / info.fps:.0f} ms), so each flash is a single frame "
                "- the shortest a video can hold"
            )
        if plan.widened:
            log.append(
                f"      note: widened to {plan.frame_count} frame"
                f"{'s' if plan.frame_count > 1 else ''} so the flash survives {speed:g}x speed-up"
            )

        for moment, (first, last) in zip(args.subliminal_at, plan.flashes):
            nxt = g.label("aftersub")
            g.chain([cur, sub_in], _overlay(enable=f"between(n,{first},{last})"), [nxt])
            cur = nxt
            log.append(f"      flash at {moment:.0%} of the clip - frames {first}-{last}")

    # ----------------------------------------------------------------- #
    # Speed, applied last to the whole composite (video + audio together).
    # ----------------------------------------------------------------- #
    if speed != 1.0:
        sped = g.label("sped")
        g.chain([cur], f"setpts=PTS/{speed:.10f},fps={info.fps:g}", [sped])
        cur = sped
        log.append(f"      speed {speed:g}x - {info.duration:.2f}s -> {info.duration / speed:.2f}s")

    final_v = g.label("vout")
    g.chain([cur], "format=yuv420p", [final_v])

    audio_map = None
    if info.has_audio:
        if speed != 1.0 and info.sample_rate:
            # Deliberately NOT pitch-preserving (no rubberband/atempo): the
            # original's MultiplySpeed effect time-remaps both streams with a
            # plain linear transform, which is a naive resample, not a
            # time-stretch - audio pitch rises with speed exactly like
            # physically playing a tape faster. asetrate+aresample is ffmpeg's
            # equivalent of that "wrong" (but game-faithful) resample.
            new_rate = round(info.sample_rate * speed)
            a_out = g.label("aout")
            g.chain(["0:a"], f"asetrate={new_rate},aresample={info.sample_rate}", [a_out])
            audio_map = f"[{a_out}]"
        else:
            audio_map = "0:a"

    return BuildResult(
        graph=g,
        video_map=f"[{final_v}]",
        audio_map=audio_map,
        temp_files=temp_files,
        log=log,
        width=width,
        height=height,
    )
