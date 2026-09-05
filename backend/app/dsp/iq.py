"""Backward-compatible IQ reader entry point.

Routes through the unified :mod:`app.recordings.reader` so every caller uses
the same format dispatch (complex64_le, float16_interleaved_le).
"""
from app.recordings.reader import read_segment, read_segment_from_path


def read_iq(recording, data_root, start_sample: int = 0, count: int | None = None):
    return read_segment(recording, data_root, start_sample, count)