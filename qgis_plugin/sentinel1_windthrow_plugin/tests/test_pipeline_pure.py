"""Read-only tests for pipeline.threshold_pass (v1.3.0, spec §4).

Deliberately free of any raster-IO dependency (FakeBand instead of a
real band) so this module also runs in the lightweight CI job — the
threshold pass only reads via ``ReadAsArray``.
"""

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources.pipeline import threshold_pass
from sentinel1_windthrow_plugin.sources.numpy_ops import (
    box_mean_nan,
    mask_from_threshold,
    median_filter_nan,
)

NODATA = -9999.0


class FakeBand:
    """Minimal read-only stand-in for a raster band over a numpy array."""

    def __init__(self, arr):
        self._arr = np.asarray(arr)

    def ReadAsArray(self, xoff=0, yoff=0, xsize=None, ysize=None):
        if xoff == 0 and yoff == 0 and xsize is None and ysize is None:
            return self._arr.copy()
        if xsize is None:
            xsize = self._arr.shape[1] - xoff
        if ysize is None:
            ysize = self._arr.shape[0] - yoff
        return self._arr[yoff:yoff + ysize, xoff:xoff + xsize].copy()


def _sample_index(height=6, width=8):
    """Background 0.0, one 2x3 blob at 10.0, nodata corner."""
    arr = np.zeros((height, width), dtype=np.float32)
    arr[2:4, 3:6] = 10.0
    arr[0, 0] = NODATA
    return arr


def test_threshold_pass_flags_above_threshold():
    arr = _sample_index()
    mask = threshold_pass(
        FakeBand(arr), 8, 6, 2.9, nodata=NODATA, median_size=1)
    expected = arr > 2.9
    expected[0, 0] = False  # nodata never flagged
    assert mask.dtype == np.dtype(bool)
    assert mask.shape == (6, 8)
    assert np.array_equal(mask, expected)


def test_threshold_pass_nodata_and_nan_excluded():
    arr = _sample_index()
    arr[4, 1] = 50.0  # valid bright pixel -> flagged
    arr[4, 2] = NODATA  # invalid -> NaN -> never flagged
    mask = threshold_pass(
        FakeBand(arr), 8, 6, 2.9, nodata=NODATA, median_size=1)
    assert mask[4, 1]
    assert not mask[4, 2]
    assert not mask[0, 0]  # nodata corner excluded too


def test_threshold_pass_median_removes_isolated_pixel():
    arr = np.zeros((9, 9), dtype=np.float32)
    arr[4, 4] = 10.0  # isolated single-pixel bloom
    # median_size >= 3: the 3x3 median around the lone pixel is 0.0
    mask = threshold_pass(
        FakeBand(arr), 9, 9, 2.9, nodata=NODATA, median_size=3)
    assert not mask.any()
    # median_size < 3 disables the filter: the pixel survives
    mask1 = threshold_pass(
        FakeBand(arr), 9, 9, 2.9, nodata=NODATA, median_size=1)
    assert mask1.sum() == 1 and mask1[4, 4]


def test_threshold_pass_inside_band_restriction():
    arr = _sample_index()  # blob rows 2..3, cols 3..5 (6 px)
    inside = np.zeros((6, 8), dtype=np.uint8)
    inside[2:3, 3:6] = 255  # only the top row of the blob is inside
    mask = threshold_pass(
        FakeBand(arr), 8, 6, 2.9, nodata=NODATA, median_size=1,
        inside_band=FakeBand(inside))
    assert mask.sum() == 3
    assert mask[2, 3] and mask[2, 4] and not mask[3, 3]


def test_threshold_pass_chunked_matches_full():
    """Halo reads must make chunked filtering identical to full-tile."""
    rng = np.random.default_rng(42)
    height, width = 700, 12  # spans two 512-row chunks
    arr = rng.normal(0.0, 1.0, size=(height, width)).astype(np.float32)
    hits = rng.random(size=(height, width)) < 0.05
    arr[hits] = 10.0
    invalid = rng.random(size=(height, width)) < 0.10
    arr[invalid] = NODATA

    mask = threshold_pass(
        FakeBand(arr), width, height, 2.9, nodata=NODATA, median_size=3)

    tile = arr.astype(np.float32, copy=True)
    tile[tile == NODATA] = np.nan
    med = median_filter_nan(tile, 3)
    expected = mask_from_threshold(med, 2.9)
    expected &= ~np.isnan(med)
    expected[invalid] = False
    assert np.array_equal(mask, expected)


def test_threshold_pass_cancelled_raises():
    from sentinel1_windthrow_plugin.sources.base import OperationCancelled
    arr = _sample_index(height=1200, width=8)  # more than one chunk

    def cancel():
        return True

    with pytest.raises(OperationCancelled):
        threshold_pass(
            FakeBand(arr), 8, 1200, 2.9, nodata=NODATA, median_size=1,
            cancelled=cancel)


def test_threshold_pass_progress_reported():
    arr = _sample_index(height=600, width=8)
    calls = []
    threshold_pass(
        FakeBand(arr), 8, 600, 2.9, nodata=NODATA, median_size=1,
        report=lambda f, m: calls.append((f, m)),
        progress_start=55.0, progress_span=15.0)
    assert calls and all(m == "Thresholding" for _, m in calls)
    fracs = [f for f, _ in calls]
    assert fracs[0] > 55.0 and fracs[-1] == 70.0
    assert fracs == sorted(fracs)


def test_threshold_pass_scipy_missing_keeps_raw(monkeypatch):
    import sentinel1_windthrow_plugin.sources.pipeline as pipeline_mod
    arr = np.zeros((9, 9), dtype=np.float32)
    arr[4, 4] = 10.0
    warnings = []
    monkeypatch.setattr(pipeline_mod, "scipy", None)
    monkeypatch.setattr(pipeline_mod, "log_warning",
                        lambda msg: warnings.append(msg))
    mask = threshold_pass(
        FakeBand(arr), 9, 9, 2.9, nodata=NODATA, median_size=3,
        median_label="WI")
    # filter skipped -> isolated pixel survives, warning preserved
    assert mask.sum() == 1
    assert warnings == ["scipy unavailable — WI median filter skipped"]



# ----------------------------------------------------------------------
# v1.3.1 - spatial averaging (smooth_size / box_mean_nan)
# ----------------------------------------------------------------------
def test_box_mean_nan_off_returns_copy():
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    out = box_mean_nan(arr, 1)
    assert np.array_equal(out, arr)
    assert out is not arr


def test_box_mean_nan_is_nan_aware():
    arr = np.full((5, 5), 2.0, dtype=np.float32)
    arr[2, 2] = np.nan
    out = box_mean_nan(arr, 3)
    # the invalid pixel is ignored, not propagated to its neighbours
    assert out[2, 2] == pytest.approx(2.0)
    assert np.isfinite(out).all()


def test_box_mean_nan_window_with_few_valid_pixels_is_nan():
    arr = np.full((9, 9), 1.0, dtype=np.float32)
    arr[0:8, 0:8] = np.nan
    out = box_mean_nan(arr, 3, min_valid_frac=0.5)
    assert np.isnan(out[0, 0])
    assert np.isfinite(out[8, 8])


def test_box_mean_nan_suppresses_isolated_speckle():
    rng = np.random.default_rng(7)
    arr = (rng.random((80, 80)) < 0.2).astype(np.float32) * 0.6
    raw = int((arr > 0.25).sum())
    smooth = box_mean_nan(arr, 5)
    assert raw > 800
    assert int((smooth > 0.25).sum()) < raw / 4


def test_threshold_pass_smooth_keeps_blob_drops_speckle():
    rng = np.random.default_rng(11)
    arr = np.zeros((80, 80), dtype=np.float32)
    arr[30:50, 30:50] = 0.6                      # windthrow-like patch
    speck = rng.random((80, 80)) < 0.15
    speck[30:50, 30:50] = False
    arr[speck] = 0.6                             # isolated scene speckle
    raw = threshold_pass(FakeBand(arr), 80, 80, 0.25, nodata=NODATA,
                         median_size=1)
    smooth = threshold_pass(FakeBand(arr), 80, 80, 0.25, nodata=NODATA,
                            median_size=1, smooth_size=5)
    assert smooth.sum() < 0.6 * raw.sum()
    assert smooth[35:45, 35:45].all()            # the patch core survives
    # far speckle is largely gone (a couple of % may survive)
    assert smooth[0:20, 0:20].sum() < 0.2 * raw[0:20, 0:20].sum()


def test_threshold_pass_smooth_chunked_matches_full_tile():
    rng = np.random.default_rng(3)
    height, width = 700, 12                      # spans two _CHUNK_ROWS blocks
    arr = rng.normal(0.0, 1.0, size=(height, width)).astype(np.float32)
    arr[rng.random((height, width)) < 0.05] = 10.0
    arr[rng.random((height, width)) < 0.10] = NODATA
    mask = threshold_pass(FakeBand(arr), width, height, 2.9, nodata=NODATA,
                          median_size=1, smooth_size=5)
    tile = arr.astype(np.float32, copy=True)
    tile[tile == NODATA] = np.nan
    expected = mask_from_threshold(box_mean_nan(tile, 5), 2.9)
    assert np.array_equal(mask, expected)
