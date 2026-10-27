"""Surface helpers: spike removal keeps buildings and corners, drops isolated returns."""

import numpy as np

from coolpath.surface import despike


def test_despike():
    z = np.zeros((40, 40), np.float32)
    z[10:20, 10:20] = 50.0            # building: corners must survive
    z[30, 30] = 2000.0                # isolated noise return
    z[5, 30] = 25.0                   # single-pixel mast
    z[25:27, 5:7] = 12.0              # 2 x 2 kiosk roof: each pixel has 3 supporting neighbours
    out, n = despike(z, window_px=5, tol_m=10, min_support=2)
    assert np.isnan(out[30, 30]) and np.isnan(out[5, 30])
    assert np.isfinite(out[10:20, 10:20]).all() and np.isfinite(out[25:27, 5:7]).all()
    assert n == 2


def test_gap_fill_median():
    from coolpath.surface import gap_fill_median
    v = np.array([[10, 12, 0, 0, 0],
                  [11, 0.5, 0, 0, 0],
                  [9, 13, 0, 0, 0],
                  [0, 0, 0, 0, 0]], np.float32)
    mask = np.zeros_like(v, bool)
    mask[:3, :4] = True
    out = gap_fill_median(v, mask, 2.0, 3)
    assert out[1, 1] == np.median([10, 12, 11, 9, 13])      # gap -> median of valid neighbours
    assert out[0, 0] == 10 and out[2, 1] == 13              # valid pixels unchanged
    assert out[1, 2] == np.median([12, 13])                 # edge of the crown
    assert out[1, 3] == 0                                   # no valid neighbour -> unchanged
    assert out[3, 0] == 0                                   # outside mask -> unchanged
