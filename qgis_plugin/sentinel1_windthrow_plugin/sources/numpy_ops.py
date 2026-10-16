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

import numpy as np

try:
    import scipy.ndimage
except Exception:  # pragma: no cover
    scipy = None  # type: ignore

from .constants import (
    _DB_DETECT_FRACTION,
    _DB_NODATA_FLOOR,
    _MEDIAN_SENTINEL,
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
    """Median-filter a WI tile tolerating NaN (invalid) pixels.

    NaNs are replaced by a large sentinel so they never drag the local
    median down; windows that are majority-sentinel resolve to the
    sentinel value, which cannot pass the positive windthrow threshold.
    Callers must re-apply the original NaN positions afterwards.
    """
    if scipy is None:  # pragma: no cover
        raise ImportError("scipy is required for median filtering")
    if size < 3:
        return np.asarray(wi, dtype=np.float32).copy()
    arr = np.asarray(wi, dtype=np.float32)
    invalid = ~np.isfinite(arr)
    filled = np.where(invalid, _MEDIAN_SENTINEL, arr)
    out = scipy.ndimage.median_filter(filled, size=size, mode="nearest")
    out = out.astype(np.float32, copy=False)
    out[invalid] = np.nan
    return out
