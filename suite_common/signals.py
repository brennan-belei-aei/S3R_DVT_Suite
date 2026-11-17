"""Small signal-processing helpers shared by the analysis plugins."""
from __future__ import annotations

import numpy as np


def centered_moving_average(values, window):
    """Odd-length centered moving average with reflected edges; window <= 1 returns a float copy."""
    values = np.asarray(values, dtype=float)
    if window <= 1:
        return values.copy()
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(values, pad, mode="reflect")
    return np.convolve(padded, np.ones(window) / window, mode="valid")
