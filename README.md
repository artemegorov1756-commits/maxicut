# ffmpeg_editor.py

An ffmpeg-native reimplementation of `video_editor.py`'s liquid-glass
lower-third title pipeline. Same CLI surface, same brands, same text-fitting
math, same subliminal-flash and speed-ramp behaviour - but nothing here
imports MoviePy, and every actual video operation (crop, blur, blend, overlay,
speed change, encode) runs as a single ffmpeg `-filter_complex` graph.

```
python ffmpeg_editor.py clip.mp4 "Dr. Ada Lovelace" -o titled.mp4
python ffmpeg_editor.py clip.mp4 "Same Day Delivery" --brand ups
python ffmpeg_editor.py https://example.com/clip.mp4 "Live from Berlin"
```

Requires `ffmpeg` and `ffprobe` on `PATH` (a build with `libx264`; add
`h264_nvenc` if you want `--gpu`), plus `pillow` and `numpy` for Python.

## How it's organised

```
ffmpeg_editor.py       CLI entry point: argparse + orchestration (run())
editorlib/
  download.py           URL/local-file resolution (ported verbatim - pure urllib)
  probe.py               ffprobe wrapper (width/height/duration/fps/sample rate)
  textfit.py              Layout + text wrap/fit math (ported verbatim - pure PIL/numpy)
  constants.py             Every tunable default, ported verbatim
  assets.py                 Everything precomputed once with PIL/numpy: text and
                             logo bitmaps, and the glass panel's static maps
  subliminal.py              Frame-exact timing for the hidden logo flashes
  graph.py                    Builds the whole ffmpeg -filter_complex graph
  render.py                    Runs ffmpeg, then ffplay for the preview
```

`textfit.py`, `download.py`, and most of `assets.py` never touched MoviePy in
the original either - they're Pillow font metrics, urllib, and plain
arithmetic - so they're ported unchanged. The only real rewrite is what used
to be `GlassPanel` (a per-frame NumPy render) and `CompositeVideoClip`
(MoviePy's layer stack): those become static PNG assets plus native ffmpeg
filters (`gblur`, `maskedmerge`, `alphamerge`, `overlay`, `fade`, `setpts`)
assembled once in `graph.py` and executed by ffmpeg itself.

## Two deliberate differences from video_editor.py

**1. The glass panel doesn't bend light at its edges.**
The original's `GlassPanel` did two things every frame: frosted the footage
under it (a blur), and *refracted* it - rim pixels sampled the scene from just
outside the panel's own boundary, bent inward, the way a real lens would. This
project keeps the first (ffmpeg's `gblur` still blurs the *actual, live*
footage under the panel every frame - see the extraction in this repo's test
notes, where a moving test pattern is visibly sharp outside the panel and
blurred inside it) but drops the second: no outward sampling, no lens bend.
That was a scoped decision, not a limitation of ffmpeg - `displace` can do
true refraction from a precomputed static map - but it adds a materially more
complex filter graph for an effect that reads as a fairly subtle rim
highlight at normal panel sizes. `assets.py`'s module docstring has the full
rationale if this needs revisiting.

**2. `--gpu` only changes the encoder.**
In `video_editor.py`, `--gpu` swapped both the encoder (`h264_nvenc`) *and*
the panel's own compute (CuPy instead of NumPy) - and the script's own help
text notes that on the hardware it was built against, GPU panel compute
measured *slower* than CPU. Here, the panel is always ordinary ffmpeg CPU
filters regardless of `--gpu`; the flag only swaps `-c:v libx264` for
`-c:v h264_nvenc` at the final encode step.

## One easy-to-miss faithfulness detail: audio pitch on `--speed`

MoviePy's `MultiplySpeed` effect time-remaps both video *and* audio with a
plain linear transform - it's a naive resample, not a pitch-preserving
time-stretch, so audio pitch rises with speed exactly like physically playing
a tape faster (subtle at the default 1.1x, more obvious at higher values).
This project matches that on purpose: audio speed uses ffmpeg's
`asetrate=<original_rate*speed>,aresample=<original_rate>` trick, **not**
`atempo` or `rubberband` (both of which this ffmpeg build has, and both of
which *would* preserve pitch - the wrong behaviour for matching the original).

## Testing notes

Validated end-to-end against a synthetic `testsrc2` clip across: the default
path (two-line emphasis fit, glass panel, real brand logo + subliminal
flashes, 9:16 pad, 1.1x speed), `--no-box` with a single-word title, a
`--font-ratio`-pinned title under `--no-pad --no-speed`, and `--no-emphasis`
(forcing the uniform two-line DP wrapper). Frame extraction confirmed the
panel genuinely frosts live footage (not a static blur), fade-in alpha ramps
correctly, the corner logo and its drop shadow render, and subliminal flashes
land on the exact expected frames post-speed-change.

Two real bugs surfaced and were fixed during that testing:
- ffmpeg's `crop` filter silently rounds odd width/height down to even
  unless `exact=1` is set, which desynced the panel's blur crop from its
  (exactly-sized) PNG masks. All `crop` calls in `graph.py` now pass
  `exact=1`.
- Pillow's `multiline_textbbox` can return fractional coordinates; the text
  canvas size in `assets.render_text_image` is now rounded outward before
  use.

`--gpu` (h264_nvenc) is implemented but untested here for lack of NVENC
hardware in the test environment.
