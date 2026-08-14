"""ffmpeg-native reimplementation of video_editor.py's title/logo pipeline.

Where the original used MoviePy to composite and render, every module here
that touches actual video pixels shells out to ffmpeg instead. Python's job is
reduced to: parse args, resolve inputs, precompute the *static* pieces (text
bitmap, logo bitmap, glass-panel maps) with PIL/numpy, and assemble one
ffmpeg -filter_complex graph that ffmpeg itself executes frame-by-frame.
"""
