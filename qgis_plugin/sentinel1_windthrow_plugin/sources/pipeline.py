"""Shared detection postprocess pipeline (v1.3.0 refactor, spec §4).

Template-method stages used by every detector (WI / L-band decline /
coherence DiD) once the mode-specific float32 index raster exists:

1. :func:`threshold_pass` — optional NaN-aware spatial averaging
   (box mean, v1.3.1) and/or median filter with chunked halo reads,
   then ``mask_from_threshold`` and an optional ``inside`` mask
   intersection → boolean mask;
2. :func:`write_mask_raster` — cleaned mask as byte GeoTIFF (0/255,
   nodata 0);
3. :func:`polygonize_mask` — OGR polygonize (8-connected), per-feature
   ``area_ha`` attribute and a minimum-size (``min_pixels``) filter;
4. :func:`run_postprocess` — convenience wrapper chaining 1–3 for
   callers that hold either a file path or an open band.

The stage bodies are moved verbatim from ``windthrow.py`` (steps 4–7
of :meth:`WindthrowDetector.detect_file`) — ``coh_delta.py`` carried a
line-for-line copy.  Only three things are parameterised:

* ``nodata`` of the float index (``WI_NODATA`` / ``DCOH_NODATA`` share
  the value ``INDEX_NODATA``);
* the median-filter warning label (``"WI"`` / ``"dcoh"``) and the
  progress-callback span of the threshold pass (70–80 % vs 55–70 %).

One intentional hardening for the shared code: a failed chunk re-read
raises ``RuntimeError(f"Failed to re-read rows ...")`` (windthrow
behaviour).  ``coh_delta`` previously crashed later on ``None``; the
green path is unchanged.

This module must NOT import any detector — the dependency graph stays
``constants -> numpy_ops/raster_utils -> pipeline -> detectors``.

GDAL import conventions follow :mod:`.raster_utils`: ``osgeo`` is
optional at module level (pure-numpy unit tests run without a GDAL
runtime) and every GDAL entry point calls :func:`_ensure_gdal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

import numpy as np

try:
    from osgeo import gdal  # always present in the QGIS Python env
    gdal.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None  # type: ignore

try:
    import scipy.ndimage
except Exception:  # pragma: no cover
    scipy = None  # type: ignore

from .base import OperationCancelled
from .constants import (
    _CHUNK_ROWS,
    DEFAULT_MEDIAN_SIZE,
    DEFAULT_SMOOTH_SIZE,
    INDEX_NODATA,
)
from .numpy_ops import (
    box_mean_nan,
    filter_small_objects,
    mask_from_threshold,
    median_filter_nan,
)
from .raster_utils import (
    _create_output_vector,
    _ensure_gdal,
    _pixel_area_m2,
)
from ..logger import log_info, log_warning

__all__ = [
    "PipelineConfig",
    "PostprocessResult",
    "threshold_pass",
    "remove_small_objects",
    "write_mask_raster",
    "polygonize_mask",
    "run_postprocess",
]

#: Optional progress reporter ``(fraction, message)``.
ProgressFn = Optional[Callable[[float, str], None]]
#: Optional cancellation probe returning ``True`` when the user aborted.
CancelFn = Optional[Callable[[], bool]]


@dataclass
class PipelineConfig:
    """Postprocess parameters shared by all detection modes.

    ``threshold`` is the already-computed cut value (adaptive or fixed
    — the *selection* logic stays detector-specific on purpose).
    """

    threshold: float
    min_pixels: int
    median_size: int = DEFAULT_MEDIAN_SIZE
    smooth_size: int = DEFAULT_SMOOTH_SIZE
    nodata: float = INDEX_NODATA
    output_base: str = ""
    median_label: str = "index"


@dataclass
class PostprocessResult:
    """Artifacts produced by :func:`run_postprocess`."""

    mask_path: str
    vector_path: str
    n_objects: int


def threshold_pass(
    index_band,
    width: int,
    height: int,
    threshold: float,
    nodata: float = INDEX_NODATA,
    median_size: int = DEFAULT_MEDIAN_SIZE,
    smooth_size: int = DEFAULT_SMOOTH_SIZE,
    inside_band=None,
    report: ProgressFn = None,
    cancelled: CancelFn = None,
    progress_start: float = 0.0,
    progress_span: float = 0.0,
    median_label: str = "index",
) -> np.ndarray:
    """Chunked median filter + thresholding of a float index band.

    Verbatim merge of the threshold loops of
    ``WindthrowDetector.detect_file`` (step 4) and
    ``CoherenceDeltaDetector.detect_file`` (step 5).  ``index_band`` is
    only READ here — the caller owns its lifetime (windthrow passes an
    in-memory band, coh_delta a band of the on-disk index raster).

    :param smooth_size: optional v1.3.1 NaN-aware box-mean window
        applied to the index BEFORE the median filter (1 = off).  The
        halo grows to ``max(median_size, smooth_size) // 2`` so chunked
        and full-tile results stay identical.
    :param inside_band: optional byte mask band; flagged pixels must
        also be inside (value > 0) — the analysis/forest restriction.
    :param progress_start/progress_span: legacy progress mapping —
        windthrow reported 70→80 %, coh_delta 55→70 %.
    :param median_label: index name for the scipy-unavailable warning
        (``"WI"`` / ``"dcoh"`` — messages preserved verbatim).
    :returns: boolean array of shape ``(height, width)``.
    """
    _report = report if report is not None else (lambda f, m: None)
    _cancelled = cancelled if cancelled is not None else (lambda: False)

    full_mask = np.zeros((height, width), dtype=bool)
    r_median = median_size // 2 if median_size >= 3 else 0
    r_smooth = smooth_size // 2 if smooth_size >= 3 else 0
    r = max(r_median, r_smooth)
    filter_active = median_size >= 3 and scipy is not None
    if median_size >= 3 and scipy is None:
        log_warning(
            f"scipy unavailable — {median_label} median filter skipped")
    smooth_active = smooth_size >= 3 and scipy is not None
    if smooth_size >= 3 and scipy is None:
        log_warning(
            f"scipy unavailable — {median_label} spatial averaging "
            "skipped")
    for y0 in range(0, height, _CHUNK_ROWS):
        if _cancelled():
            raise OperationCancelled()
        rows = min(_CHUNK_ROWS, height - y0)
        ry0, ry1 = max(0, y0 - r), min(height, y0 + rows + r)
        tile = index_band.ReadAsArray(0, ry0, width, ry1 - ry0)
        if tile is None:
            raise RuntimeError(f"Failed to re-read rows {ry0}..{ry1}")
        invalid = tile == nodata
        tile = tile.astype(np.float32, copy=False)
        tile[invalid] = np.nan
        if smooth_active:
            tile = box_mean_nan(tile, smooth_size)
        if filter_active:
            tile = median_filter_nan(tile, median_size)
        centre = tile[y0 - ry0: y0 - ry0 + rows]
        flagged = mask_from_threshold(centre, threshold)
        flagged &= ~np.isnan(centre)
        if inside_band is not None:
            inside = inside_band.ReadAsArray(0, y0, width, rows) > 0
            flagged &= inside
        full_mask[y0:y0 + rows] = flagged
        _report(progress_start
                + progress_span * (y0 + rows) / float(height),
                "Thresholding")
    return full_mask


def remove_small_objects(
    full_mask: np.ndarray,
    min_pixels: int,
) -> np.ndarray:
    """8-connected speckle cleanup (verbatim detectors' step 5/6).

    Objects smaller than ``min_pixels`` are dropped (scipy labelling);
    ``min_pixels <= 1`` keeps the mask untouched, exactly like the
    original ``if self.min_pixels > 1`` guard.
    """
    if min_pixels > 1:
        return filter_small_objects(full_mask, min_pixels)
    return full_mask


def write_mask_raster(
    full_mask: np.ndarray,
    ref_info: dict,
    output_base: str,
) -> str:
    """Write the cleaned boolean mask as ``<output_base>_mask.tif``.

    Byte 0/255 raster (255 = detection), nodata 0, georeferencing
    copied from ``ref_info``.  Verbatim from the detectors' step 6/7.
    """
    _ensure_gdal()
    height, width = full_mask.shape
    if height != ref_info["height"] or width != ref_info["width"]:
        raise RuntimeError(
            "Mask shape does not match the reference grid: "
            f"{width}x{height} vs {ref_info['width']}x{ref_info['height']}")
    mask_path = f"{output_base}_mask.tif"
    driver = gdal.GetDriverByName("GTiff")
    mask_out = driver.Create(
        mask_path, width, height, 1, gdal.GDT_Byte,
        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
    )
    if mask_out is None:
        raise RuntimeError(f"Cannot create output file: {mask_path}")
    try:
        mask_out.SetGeoTransform(ref_info["geotransform"])
        if ref_info["projection"]:
            mask_out.SetProjection(ref_info["projection"])
        mband = mask_out.GetRasterBand(1)
        mband.WriteArray(full_mask.astype(np.uint8) * 255)
        mband.SetNoDataValue(0)
        mband.FlushCache()
        try:
            mband.ComputeStatistics(False)
        except Exception as exc:
            # Spec 7.3: optional stats stay best-effort, but the failure
            # is logged instead of silently swallowed.
            log_info(f"Could not compute mask statistics: {exc}")
    finally:
        mask_out = None
    return mask_path


def polygonize_mask(
    mask_path: str,
    ref_info: dict,
    output_base: str,
    min_pixels: int,
) -> Tuple[str, int]:
    """Polygonise ``mask_path`` into a vector layer with ``area_ha``.

    8-connected polygonize, per-feature ``area_ha`` field and removal
    of polygons smaller than ``min_pixels`` (area-based).  Verbatim
    from the detectors' final stage — the single place to fix
    polygonize/area bugs (spec §4.3).

    :returns: ``(vector_path, n_objects)``.
    """
    _ensure_gdal()
    vector_path = (output_base
                   if output_base.lower().endswith((".gpkg", ".shp"))
                   else output_base + ".gpkg")
    vds, layer = _create_output_vector(vector_path, ref_info["projection"])
    px_area_m2 = _pixel_area_m2(ref_info["geotransform"],
                                ref_info["projection"])
    try:
        mask_src = gdal.Open(mask_path, gdal.GA_ReadOnly)
        band = mask_src.GetRasterBand(1)
        gdal.Polygonize(band, band, layer, -1, ["8CONNECTED=8"], None)
        min_area_m2 = (min_pixels * px_area_m2 if min_pixels > 0 else 0.0)
        # Collect first, modify after: OGR does not guarantee safe
        # iteration while features are being deleted.
        fid_area = []
        layer.ResetReading()
        for feat in layer:
            geom = feat.GetGeometryRef()
            area_m2 = geom.GetArea() if geom is not None else 0.0
            fid_area.append((feat.GetFID(), area_m2))
        n_kept = 0
        for fid, area_m2 in fid_area:
            if area_m2 < min_area_m2:
                layer.DeleteFeature(fid)
            else:
                f = layer.GetFeature(fid)
                if f is not None:
                    f.SetField("area_ha", area_m2 / 10000.0)
                    layer.SetFeature(f)
                    f = None
                n_kept += 1
        layer.ResetReading()
        mask_src = None
    finally:
        vds = None
    return vector_path, n_kept


def run_postprocess(
    index: Union[str, object],
    ref_info: dict,
    config: PipelineConfig,
    inside_band=None,
    inside_mask_path: Optional[str] = None,
    report: ProgressFn = None,
    cancelled: CancelFn = None,
    progress_start: float = 0.0,
    progress_span: float = 0.0,
) -> PostprocessResult:
    """Chain threshold → mask raster → polygonize (spec §4.1 sketch).

    :param index: path of a GDAL-readable float index raster, or an
        already-open band.  A file handle opened here is released right
        after the threshold pass (matches the coh_delta lifecycle) —
        the mask/vector stages never keep the index open.
    :param inside_band: open byte mask band, or
    :param inside_mask_path: path of the mask raster to open (exactly
        one of the two).  The handle, when opened here, is closed with
        the index after the threshold pass.
    """
    _ensure_gdal()
    width, height = ref_info["width"], ref_info["height"]
    if inside_band is not None and inside_mask_path is not None:
        raise ValueError(
            "Pass either inside_band or inside_mask_path, not both")
    opened_ds = None
    opened_inside = None
    if isinstance(index, str):
        opened_ds = gdal.Open(index, gdal.GA_ReadOnly)
        if opened_ds is None:
            raise RuntimeError(f"Cannot open index raster: {index}")
        index_band = opened_ds.GetRasterBand(1)
    else:
        index_band = index
    if inside_band is None and inside_mask_path is not None:
        opened_inside = gdal.Open(inside_mask_path, gdal.GA_ReadOnly)
        if opened_inside is None:
            raise RuntimeError(
                f"Cannot open mask raster: {inside_mask_path}")
        inside_band = opened_inside.GetRasterBand(1)
    try:
        full_mask = threshold_pass(
            index_band, width, height, config.threshold,
            nodata=config.nodata, median_size=config.median_size,
            smooth_size=config.smooth_size,
            inside_band=inside_band, report=report, cancelled=cancelled,
            progress_start=progress_start, progress_span=progress_span,
            median_label=config.median_label,
        )
    finally:
        # Release the index/mask handles BEFORE the vector stages —
        # large rasters must not stay resident during polygonize.
        opened_ds = None
        opened_inside = None
    full_mask = remove_small_objects(full_mask, config.min_pixels)
    mask_path = write_mask_raster(full_mask, ref_info, config.output_base)
    vector_path, n_objects = polygonize_mask(
        mask_path, ref_info, config.output_base, config.min_pixels)
    return PostprocessResult(
        mask_path=mask_path, vector_path=vector_path, n_objects=n_objects)
