"""Pure-numpy / pure-python detection primitives (no GDAL).

Moved verbatim from ``windthrow.py`` in the v1.3.0 refactor so the WI,
LDI and DiD detectors share one tested implementation and the module
stays unit-testable without a GDAL runtime.  The optional scipy import
keeps its graceful degradation (``filter_small_objects`` / the caller of
``median_filter_nan`` handle ``scipy is None``).

Conventions (frozen, see constants.py):

* dB-domain chunks use NaN for invalid pixels;
* ``_DB_DETECT_FRACTION`` / ``_DB_NODATA_FLOOR`` implement the
  already-dB auto-detection shared with the preprocessor;
* ``MEDIAN_SENTINEL`` neutralises NaN inside median-filter windows;
* object cleanup uses 8-connected labelling, matching the polygonise
  step (gdal.Polygonize, 8-connected).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Sequence

import warnings

import numpy as np

try:
    import scipy.ndimage
except Exception:  # pragma: no cover
    scipy = None  # type: ignore

from .constants import (
    _DB_DETECT_FRACTION,
    _DB_NODATA_FLOOR,
)

__all__ = [
    "extract_polarization",
    "pair_by_polarization",
    "common_polarizations",
    "to_db_domain_chunk",
    "compute_wi",
    "adaptive_threshold",
    "mask_from_threshold",
    "filter_small_objects",
    "background_offset_db",
    "median_filter_nan",
    "box_mean_nan",
]


# ======================================================================
# Polarisation naming helpers
# ======================================================================
def extract_polarization(path: str) -> str:
    """Return the polarisation token ("VV"/"VH"/"HH"/"HV") of a file, else "".

    Matches both Planetary Computer flat naming (``..._vv.tif``) and
    classical .SAFE naming (``s1a-iw-grd-vv-...001.tif``); the token
    must be delimited by separators so ``vvpanel.tif`` does not match.
    """
    name = os.path.basename(path).lower()
    stem = os.path.splitext(name)[0]
    m = re.search(r"(?:^|[-_])(vv|vh|hh|hv)(?:[-_.]|$)", stem)
    return m.group(1).upper() if m else ""


def pair_by_polarization(paths: Sequence[str]) -> Dict[str, List[str]]:
    """Group file paths by the polarisation token found in their names.

    Files without a recognisable token are collected under the key "".
    """
    grouped: Dict[str, List[str]] = {}
    for p in paths:
        grouped.setdefault(extract_polarization(p), []).append(p)
    return grouped


def common_polarizations(pre: Sequence[str], post: Sequence[str]) -> List[str]:
    """Return the polarisations present in BOTH stacks, VV first, VH second."""
    pre_pols = set(pair_by_polarization(pre).keys())
    post_pols = set(pair_by_polarization(post).keys())
    both = pre_pols & post_pols
    both.discard("")
    ordered = [p for p in ("VV", "VH") if p in both]
    ordered += sorted(both - set(ordered))
    return ordered


# ======================================================================
# Index / threshold primitives
# ======================================================================
def to_db_domain_chunk(arr: np.ndarray) -> np.ndarray:
    """Convert one chunk of SAR data to the dB domain (NaN = invalid).

    Auto-detects the scale: already-dB data (physical negatives above
    the no-data floor) is passed through with no-data sentinels mapped
    to NaN; linear amplitude / raw DN is converted with 10*log10, where
    zero / negative pixels become NaN.
    """
    a = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(a)
    n_finite = int(np.count_nonzero(finite))
    if n_finite == 0:
        return np.full(a.shape, np.nan, dtype=np.float32)
    physical_negative = (a < 0.0) & (a > _DB_NODATA_FLOOR)
    neg_frac = float(np.count_nonzero(physical_negative)) / n_finite
    if neg_frac > _DB_DETECT_FRACTION:
        out = a.copy()
        out[~(finite & (a > _DB_NODATA_FLOOR))] = np.nan
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(np.where(finite & (a > 0.0), a, np.nan))
    return db.astype(np.float32, copy=False)


def compute_wi(d_vv: np.ndarray, d_vh: np.ndarray) -> np.ndarray:
    """Windthrow Index: WI = dVV + dVH (dB, positive = backscatter increase).

    If only one polarisation difference is available (the other is
    ``None``), WI degrades to that single difference.
    """
    if d_vv is None and d_vh is None:
        raise ValueError("compute_wi requires at least one polarisation")
    if d_vv is None:
        return np.asarray(d_vh, dtype=np.float32)
    if d_vh is None:
        return np.asarray(d_vv, dtype=np.float32)
    wi = np.asarray(d_vv, dtype=np.float32) + np.asarray(d_vh, dtype=np.float32)
    # NaN in either channel must stay NaN, not propagate silently as
    # inf-inf; the float addition already yields NaN for NaN inputs.
    return wi.astype(np.float32, copy=False)


def adaptive_threshold(mean_wi: float, offset_db: float) -> float:
    """Paper-style threshold: mean(WI) within the mask plus a dB offset."""
    return float(mean_wi) + float(offset_db)


def mask_from_threshold(wi: np.ndarray, threshold_db: float) -> np.ndarray:
    """Flag pixels with WI strictly above ``threshold_db`` (NaN never flagged)."""
    arr = np.asarray(wi, dtype=np.float32)
    return (np.isfinite(arr) & (arr > float(threshold_db)))


def filter_small_objects(mask: np.ndarray, min_pixels: int) -> np.ndarray:
    """Remove connected flagged objects smaller than ``min_pixels``.

    Uses 8-connected labelling (matching the polygonise step).  Requires
    scipy; if scipy is unavailable the mask is returned unchanged.
    """
    binary = np.asarray(mask).astype(bool)
    if min_pixels <= 1 or not binary.any():
        return binary
    if scipy is None:  # pragma: no cover - QGIS always ships scipy
        return binary
    structure = np.ones((3, 3), dtype=bool)  # 8-connectivity
    labels, n = scipy.ndimage.label(binary, structure=structure)
    if n == 0:
        return np.zeros_like(binary)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0  # background
    keep = sizes >= int(min_pixels)
    return keep[labels]


def background_offset_db(
    pre_db: np.ndarray,
    post_db: np.ndarray,
    max_values: int = 8_000_000,
) -> float:
    """Median background shift between post- and pre-scene tiles (dB).

    The offset is ``median(post - pre)`` over pixels that are finite in
    both arrays; it captures weather-driven radiometric changes (wet
    soil, snowmelt, vegetation state) common to the whole scene.  Very
    large samples are deterministically stride-subsampled to
    ``max_values`` elements to bound memory.  Returns 0.0 for an empty
    sample.
    """
    pre = np.asarray(pre_db, dtype=np.float32)
    post = np.asarray(post_db, dtype=np.float32)
    if pre.shape != post.shape:
        raise ValueError("pre/post tiles must share the same shape")
    with np.errstate(invalid="ignore"):
        delta = post - pre
    vals = delta[np.isfinite(delta)]
    if vals.size == 0:
        return 0.0
    if vals.size > int(max_values):
        stride = int(np.ceil(vals.size / float(max_values)))
        vals = vals[::stride]
    return float(np.median(vals))


def median_filter_nan(wi: np.ndarray, size: int) -> np.ndarray:
    """Apply a NaN-aware spatial median without false positive border halos.

    The pre-1.4.1 implementation filled NaNs with +1e6 before filtering;
    majority-NaN windows therefore became +1e6 and PASSED the positive
    detection threshold, so real missing areas could appear as spurious
    disturbance polygons at NoData/mask borders. For sparse invalid values
    a bounded-memory sliding-window nanmedian is used; all-finite tiles
    stay on the fast scipy median_filter path.

    The result equals what a per-window ``np.nanmedian`` produces, and
    the caller still re-applies the original NaN positions.
    """
    if scipy is None:  # pragma: no cover
        raise ImportError("scipy is required for median filtering")
    arr = np.asarray(wi, dtype=np.float32)
    if size < 3:
        return arr.copy()
    if size % 2 == 0:
        raise ValueError("Median filter size must be odd")
    invalid = ~np.isfinite(arr)
    if not invalid.any():
        return scipy.ndimage.median_filter(arr, size=size, mode="nearest")
    from numpy.lib.stride_tricks import sliding_window_view
    radius = size // 2
    padded = np.pad(np.where(invalid, np.nan, arr), radius, mode="edge")
    result = np.empty_like(arr)
    # The padded sliding view is zero-copy; np.nanmedian allocates
    # temporaries proportional to the number of window elements.
    row_budget = max(16, min(128, 1_000_000 // max(1, arr.shape[1] * size * size)))
    for y0 in range(0, arr.shape[0], row_budget):
        y1 = min(arr.shape[0], y0 + row_budget)
        view = sliding_window_view(
            padded[y0:y1 + 2 * radius], (size, size))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            result[y0:y1] = np.nanmedian(view, axis=(-2, -1))
    result[invalid] = np.nan
    return result


def box_mean_nan(
    arr: np.ndarray,
    size: int,
    min_valid_frac: float = 0.30,
) -> np.ndarray:
    """NaN-aware box mean (spatial averaging) of an index tile.

    v1.3.1 — «вариант A» of the multilook experiment.  The coherence of
    a single 12-day pair carries a per-pixel estimation noise of
    sigma ~ 0.20 coherence units at 80 m, i.e. the same order as the
    windthrow excess, so a per-pixel threshold flags ~8-14 % of the
    frame.  Averaging the index over a ``size x size`` window divides
    that noise by ``sqrt(N)`` and, because a windthrow patch is much
    wider than one pixel, RAISES the class separation instead of
    trading it away (23-event Shikhov re-run, ring 2-10 km AUC:
    0.720 -> 0.755 at 3x3 and 0.772 at 5x5; frame-wide flagged area
    11.0 % -> 7.1 % / 5.1 %).

    Only finite pixels contribute; the window mean is the mean of its
    valid pixels, and windows whose valid fraction is below
    ``min_valid_frac`` become NaN (they can never be flagged).  The
    output keeps the input shape and is float32.

    ``size < 3`` returns an unmodified copy (the filter is off).  The
    caller is responsible for the halo when processing in chunks.

    Caveat (measured): features NARROWER than the window are diluted —
    the Shikhov reference contours are thin strips (median width 1-3 px
    at 80 m) and lose recall under 5x5, while wide patches gain a lot.
    See the report in testing/ОТЧЁТ_вариантА.md.
    """
    a = np.asarray(arr, dtype=np.float32)
    if size < 3:
        return a.copy()
    if scipy is None:  # pragma: no cover - QGIS always ships scipy
        raise ImportError("scipy is required for spatial averaging")
    valid = np.isfinite(a)
    filled = np.where(valid, a, 0.0).astype(np.float64)
    frac = scipy.ndimage.uniform_filter(
        valid.astype(np.float64), size=size, mode="nearest")
    total = scipy.ndimage.uniform_filter(
        filled, size=size, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = total / np.where(frac > 0.0, frac, 1.0)
    out[frac < float(min_valid_frac)] = np.nan
    return out.astype(np.float32, copy=False)
