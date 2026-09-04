from pathlib import Path

import numpy as np


def write_tiny_iq(path: Path) -> Path:
    n = np.arange(4096, dtype=np.float32)
    iq = np.exp(2j * np.pi * 0.08 * n) + 0.4 * np.exp(2j * np.pi * 0.22 * n)
    iq.astype('<c8').tofile(path)
    return path
