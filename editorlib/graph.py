"""Assemble one ffmpeg -filter_complex graph that reproduces compose_lower_third.

Everything that used to be a MoviePy CompositeVideoClip layer becomes an
ffmpeg `overlay` here; everything that used to be a per-frame numpy
computation (the glass panel) becomes a handful of *static* PNGs plus native
ffmpeg filters (`gblur`, `maskedmerge`, `alphamerge`) that ffmpeg itself runs
once per frame - see `assets.py`'s module docstring for what that trades away.

The whole graph is built in the source video's own pre-speed timeline
(seconds and frame numbers both refer to the untouched source); `--speed` is
applied once, at the very end, to the fully-composited stream - exactly the
order the original used (`MultiplySpeed` on the finished `CompositeVideoClip`)
so overlay timing never has to account for it. See `_apply_speed`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import assets
from .constants import DEFAULT_FONT_RATIO, GLASS_WASH_REACH, LOGO_PAD_X_RATIO, LOGO_PAD_Y_RATIO, LOGO_SHADOW_BLUR, LOGO_SHADOW_OPACITY
from .probe import VideoInfo
from .subliminal import compute_subliminal_plan
from .textfit import (
    card_padding,
    card_size,
    compute_layout,
    fit_title_to_card,
    fit_two_line_title_to_card,
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
    wash_color: tuple[int, int, int],
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

    draw_box = not args.no_box
    caps = not args.no_caps

    # A copy of the untouched (post-pad) footage, split off before any
    # overlay is drawn, so the glass panel's frost reads the same pixels the
    # original's GlassPanel.render() did: the base video, not any overlay
    # already stacked on top of it.
    if draw_box:
        video_main, video_for_panel = g.label("vmain"), g.label("vpanel")
        g.chain([cur], "split=2", [video_main, video_for_panel])
        cur = video_main
    else:
        video_main = cur

    log.append(f"      {width}x{height}, {info.duration:.2f}s, {info.fps:g} fps")

    # ----------------------------------------------------------------- #
    # Layout + title bitmap
    # ----------------------------------------------------------------- #
    layout = compute_layout(width, height, args.font_ratio or DEFAULT_FONT_RATIO, args.line_gap)
    stroke = 0 if draw_box else layout.stroke_width
    text_source = args.title.upper() if caps else args.title

    if draw_box:
        box_w, box_h = card_size(width, height, args.box_ratio, layout.side_margin)
        pad_x, pad_y = card_padding(box_w, box_h, args.text_fill)
        avail_w, avail_h = max(1, box_w - 2 * pad_x), max(1, box_h - 2 * pad_y)
        fit_text = args.font_ratio is None

        if fit_text:
            split = None
            if not args.no_emphasis:
                split = fit_two_line_title_to_card(
                    text_source, font, avail_w, avail_h, args.line_gap,
                    args.emphasis_ratio, args.emphasis_line, caps,
                )
            if split is not None:
                size_a, size_b, line_a, line_b = split

                def draw_split(sizes: tuple[int, int]) -> Image.Image:
                    return assets.render_split_title_image(
                        [(line_a, sizes[0]), (line_b, sizes[1])], font, args.line_gap
                    )

                text_img = draw_split((size_a, size_b))
                for _ in range(2):
                    if text_img.width <= avail_w and text_img.height <= avail_h:
                        break
                    scale = min(avail_w / text_img.width, avail_h / text_img.height)
                    size_a, size_b = max(1, int(size_a * scale)), max(1, int(size_b * scale))
                    text_img = draw_split((size_a, size_b))
                font_desc = f"{size_a}/{size_b} px"
                final_font_size = max(size_a, size_b)
            else:
                font_size, wrapped = fit_title_to_card(
                    text_source, font, avail_w, avail_h, args.line_gap, caps
                )

                def draw_uniform(size: int) -> Image.Image:
                    return assets.render_text_image(
                        wrapped, font, size, 0, round(size * args.line_gap)
                    )

                text_img = draw_uniform(font_size)
                for _ in range(2):
                    if text_img.width <= avail_w and text_img.height <= avail_h:
                        break
                    font_size = max(
                        1, int(font_size * min(avail_w / text_img.width, avail_h / text_img.height))
                    )
                    text_img = draw_uniform(font_size)
                font_desc = f"{font_size} px"
                final_font_size = font_size
        else:
            margin_x, _ = assets.safety_margin(font, layout.font_size, 0)
            wrapped = wrap_title(text_source, font, layout.font_size, 0, max(1, avail_w - 2 * margin_x))
            text_img = assets.render_text_image(wrapped, font, layout.font_size, 0, layout.line_gap)
            if text_img.width > avail_w or text_img.height > avail_h:
                log.append(
                    f"      warning: the title needs {text_img.width}x{text_img.height} px but "
                    f"the card's interior is {avail_w}x{avail_h} px - drop --font-ratio, or raise "
                    "--box-ratio / --text-fill"
                )
            font_desc = f"{layout.font_size} px"
            final_font_size = layout.font_size
    else:
        max_w = max_text_width(width, layout)
        wrapped = wrap_title(text_source, font, layout.font_size, layout.stroke_width, max_w)
        text_img = assets.render_text_image(
            wrapped, font, layout.font_size, layout.stroke_width, layout.line_gap
        )
        font_desc = f"{layout.font_size} px"
        final_font_size = layout.font_size
        block_h = text_block_height(
            font, layout.font_size, wrapped.count("\n") + 1, layout.line_gap, layout.stroke_width, caps
        )
        box_w = min(width - 2 * layout.side_margin, text_img.width + 2 * layout.pad_x)
        box_h = max(block_h, text_img.height) + 2 * layout.pad_y

    box_x = max(0, (width - box_w) // 2)
    box_y = round(height * args.position - box_h / 2)
    lowest = max(0, height - layout.bottom_margin - box_h)
    box_y = max(0, min(box_y, lowest))

    text_x = box_x + (box_w - text_img.width) // 2
    text_y = box_y + (box_h - text_img.height) // 2

    text_alpha = np.asarray(text_img.getchannel("A"), dtype=np.float32) / 255.0
    text_shadow_blur = max(2, round(final_font_size * 0.30))
    text_shadow_img, text_shadow_pad = assets.soft_shadow_from_alpha(text_alpha, text_shadow_blur, 0.55)

    # ----------------------------------------------------------------- #
    # Glass panel (maps + the live-blur ffmpeg subgraph)
    # ----------------------------------------------------------------- #
    panel_rgba_label = None
    panel_shadow_img = panel_shadow_pad = None
    if draw_box:
        reference = min(width, height)
        radius = min(round(box_h * 0.32), round(reference * 0.06), box_w // 2, box_h // 2)
        maps = assets.GlassMaps(box_w, box_h, radius, args.glass_tint, args.glass_wash, wash_color, GLASS_WASH_REACH)
        blur_radius = min(reference * args.glass_blur, min(box_w, box_h) * 0.35)
        detail_radius = blur_radius * 0.35
        blur_pad = max(1, math.ceil(2 * blur_radius + 2))

        panel_shadow_img, panel_shadow_pad = maps.drop_shadow(
            [
                (max(4.0, reference * 0.045), reference * 0.012, 0.38),
                (max(2.0, reference * 0.018), reference * 0.030, 0.32),
            ]
        )

        left_pad = max(0, min(blur_pad, box_x))
        right_pad = max(0, min(blur_pad, width - (box_x + box_w)))
        top_pad = max(0, min(blur_pad, box_y))
        bottom_pad = max(0, min(blur_pad, height - (box_y + box_h)))
        crop_x, crop_y = box_x - left_pad, box_y - top_pad
        crop_w, crop_h = box_w + left_pad + right_pad, box_h + top_pad + bottom_pad

        panel_src = g.label("panelsrc")
        g.chain([video_for_panel], f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}:exact=1", [panel_src])

        frosted_pad, detail_pad = g.label("frostedpad"), g.label("detailpad")
        g.chain([panel_src], "split=2", [frosted_pad, detail_pad])

        frosted_blur, detail_blur = g.label("frostedblur"), g.label("detailblur")
        g.chain([frosted_pad], f"gblur=sigma={blur_radius:.4f}", [frosted_blur])
        g.chain([detail_pad], f"gblur=sigma={max(0.01, detail_radius):.4f}", [detail_blur])

        frosted, detail = g.label("frosted"), g.label("detail")
        g.chain([frosted_blur], f"crop={box_w}:{box_h}:{left_pad}:{top_pad}:exact=1", [frosted])
        g.chain([detail_blur], f"crop={box_w}:{box_h}:{left_pad}:{top_pad}:exact=1", [detail])

        rim_in = g.add_image_input(save_png(maps.rim_image(), "rim.png"))
        rim_gray = g.label("rimgray")
        g.chain([rim_in], "format=gray", [rim_gray])

        glass_blend = g.label("glassblend")
        g.chain([frosted, detail, rim_gray], "maskedmerge", [glass_blend])

        tint_in = g.add_image_input(save_png(maps.tint_rgba, "tint.png"))
        tinted = g.label("tinted")
        g.chain([glass_blend, tint_in], "overlay=0:0:format=auto", [tinted])

        wash_in = g.add_image_input(save_png(maps.wash_rgba, "wash.png"))
        washed = g.label("washed")
        g.chain([tinted, wash_in], "overlay=0:0:format=auto", [washed])

        alpha_in = g.add_image_input(save_png(maps.alpha_image(), "panelalpha.png"))
        alpha_gray = g.label("panelalphagray")
        g.chain([alpha_in], "format=gray", [alpha_gray])

        panel_rgba_label = g.label("panelrgba")
        g.chain([washed, alpha_gray], "alphamerge", [panel_rgba_label])

    # ----------------------------------------------------------------- #
    # Title group: shadow + panel + text-shadow + text, composited on a
    # transparent full-frame canvas so one fade envelope covers all of them
    # (verified pattern: color@0.0 -> format=rgba -> chained overlays -> fade
    # -> single overlay onto the main timeline).
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

    if draw_box:
        shadow_in = g.add_image_input(save_png(panel_shadow_img, "panel_shadow.png"))
        nxt = g.label("t")
        g.chain([t, shadow_in], f"overlay={box_x - panel_shadow_pad}:{box_y - panel_shadow_pad}:format=auto", [nxt])
        t = nxt

        nxt = g.label("t")
        g.chain([t, panel_rgba_label], f"overlay={box_x}:{box_y}:format=auto", [nxt])
        t = nxt

    text_shadow_in = g.add_image_input(save_png(text_shadow_img, "text_shadow.png"))
    nxt = g.label("t")
    g.chain([t, text_shadow_in], f"overlay={text_x - text_shadow_pad}:{text_y - text_shadow_pad}:format=auto", [nxt])
    t = nxt

    text_in = g.add_image_input(save_png(text_img, "text.png"))
    nxt = g.label("t")
    g.chain([t, text_in], f"overlay={text_x}:{text_y}:format=auto", [nxt])
    t = nxt

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
    overlay_filt = "overlay=0:0:format=auto"
    if enable:
        overlay_filt += f":enable='{enable}'"
    g.chain([video_main, t], overlay_filt, [after_title])
    cur = after_title

    log.append(
        f"      glass panel {box_w}x{box_h} px at ({box_x}, {box_y}) - "
        f"{box_w * box_h / (width * height):.0%} of frame, "
        f"title {text_img.width}x{text_img.height} px "
        f"({text_img.width / box_w:.0%}x{text_img.height / box_h:.0%} of the card), "
        f"font {font_desc}"
    )

    # ----------------------------------------------------------------- #
    # Corner logo - drawn on top of the title group, for the whole clip.
    # ----------------------------------------------------------------- #
    if logo_path is not None:
        logo_img = assets.build_logo_image(logo_path, width, height, args.logo_ratio)
        logo_rgb = np.asarray(logo_img.convert("RGB"))
        logo_alpha = np.asarray(logo_img.getchannel("A"), dtype=np.float32) / 255.0 * args.logo_opacity
        logo_final = Image.fromarray(
            np.dstack([logo_rgb, np.clip(logo_alpha * 255, 0, 255).astype(np.uint8)]), mode="RGBA"
        )
        lx = layout.side_margin + round(width * LOGO_PAD_X_RATIO)
        ly = max(0, min(layout.top_margin + round(height * LOGO_PAD_Y_RATIO), height - logo_final.height))
        logo_shadow_img, logo_shadow_pad = assets.soft_shadow_from_alpha(
            logo_alpha, max(2, round(logo_final.height * LOGO_SHADOW_BLUR)), LOGO_SHADOW_OPACITY
        )

        logo_shadow_in = g.add_image_input(save_png(logo_shadow_img, "logo_shadow.png"))
        nxt = g.label("afterlogoshadow")
        g.chain([cur, logo_shadow_in], f"overlay={lx - logo_shadow_pad}:{ly - logo_shadow_pad}:format=auto", [nxt])
        cur = nxt

        logo_in = g.add_image_input(save_png(logo_final, "logo.png"))
        nxt = g.label("afterlogo")
        g.chain([cur, logo_in], f"overlay={lx}:{ly}:format=auto", [nxt])
        cur = nxt

        log.append(f"      logo {logo_final.width}x{logo_final.height} px at ({lx}, {ly}) - {logo_path.name}")

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
            g.chain([cur, sub_in], f"overlay=0:0:format=auto:enable='between(n,{first},{last})'", [nxt])
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
