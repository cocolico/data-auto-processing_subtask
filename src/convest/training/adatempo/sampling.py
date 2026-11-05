"""One discrete observation-frame convention for videos, labels and timestamps."""
import numpy as np


def nearest_frames(positions):
    return np.rint(np.asarray(positions, dtype=np.float64)).astype(np.int64)
