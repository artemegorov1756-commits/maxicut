"""Timing for the hidden full-frame logo flashes.

The original (`frame_window` in video_editor.py) worked in *seconds*, centred
on a frame and inset by half a frame on each side, because MoviePy's writer
samples a composite by asking "what's showing at t = i/fps" and a naively
placed window could land its boundary exactly on that sampling instant, at
the mercy of float rounding.

ffmpeg's `overlay` filter can be gated directly on the *integer output frame
number* (`enable='between(n,first,last)'` - verified empirically, see the
project notes) rather than on time, which sidesteps that problem entirely: a
frame index is either inside `[first, last]` or it isn't, no float involved.
So this module keeps the original's centring logic (still needed so the
correct handful of frames get chosen) but reports it directly in frame-index
space instead of round-tripping through seconds.
"""

from __future__ import annotations

from dataclasses import dataclass


def subliminal_frame_range(moment: float, fps: float, duration: float, count: int) -> tuple[int, int]:
    """First and last (inclusive) output frame index for a flash at `moment`.

    `moment` is a fraction of `duration` (0-1). The window is centred on the
    frame nearest `moment * duration`, then clamped inside [0, total-1] and
    widened to exactly `count` frames without running past either end.
    """
    total = max(1, int(duration * fps))
    count = max(1, min(count, total))
    index = min(max(round(moment * duration * fps), 0), total - 1)
    first = min(max(index - (count - 1) // 2, 0), total - count)
    return first, first + count - 1


@dataclass(frozen=True)
class SubliminalPlan:
    frame_count: int
    actual_ms: float
    widened: bool  # True if frame_count was raised above the raw ms request to survive speed-up
    flashes: list[tuple[int, int]]  # (first_frame, last_frame_inclusive) per moment


def compute_subliminal_plan(
    fps: float,
    duration: float,
    moments: list[float],
    milliseconds: float,
    speed: float,
) -> SubliminalPlan:
    """How many frames each flash gets, and where they land.

    `speed` is the multiplier the graph will apply to the whole composite
    afterwards (see `graph.py`). Exactly as in the original: ffmpeg conforms
    the time-compressed stream back to a constant frame rate by dropping
    frames, so a flash narrower than the compression factor can fall entirely
    between two surviving output frames. Widening the window to at least
    `ceil(speed)` frames closes that gap - the same guarantee the original's
    `build_subliminal_layers` derives, for the same reason.
    """
    import math

    raw_count = max(1, min(round(milliseconds / 1000 * fps), int(duration * fps)))
    count = raw_count
    survives_speed = max(1, math.ceil(speed))
    if survives_speed > count:
        count = min(survives_speed, int(duration * fps))
    actual_ms = count / fps * 1000

    flashes = [subliminal_frame_range(moment, fps, duration, count) for moment in moments]
    return SubliminalPlan(
        frame_count=count, actual_ms=actual_ms, widened=count > raw_count, flashes=flashes
    )
