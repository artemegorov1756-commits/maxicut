"""ffmpeg-native title/logo pipeline.

Python's job is to parse args, resolve inputs, precompute the *static* pieces
of the lockup (card, text bitmap, logo lockup) with PIL/numpy, and assemble one
ffmpeg -filter_complex graph that ffmpeg itself executes frame-by-frame. Every
operation that touches actual video pixels - pad, overlay, fade, speed, encode
- is ffmpeg's.
"""
