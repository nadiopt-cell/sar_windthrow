"""Bi-temporal windthrow detection for Sentinel-1 SAR imagery.

Implements the rapid change-detection method of Rüetschi, Small & Waser
(2019, "Rapid Detection of Windthrows Using Sentinel-1 C-Band SAR Data",
Remote Sensing 11(2):115), adapted to a lightweight QGIS plugin:

1.  Two stacks of pre-processed SAR images in dB (or raw amplitude /
    DN, which are auto-converted) cover the pre-storm and post-storm
    windows.  Per polarisation, the stack is combined into a single
    composite by a pixel-wise median (a robust approximation of the
    local-resolution-weighted compositing used in the paper).
2.  Image differencing per polarisation:
        dVV = VV_post - VV_pre     dVH = VH_post - VH_pre      [dB]
    Windthrown areas show a BACKSCATTER INCREASE (chaotically oriented
    trunks and branches raise surface roughness and reduce canopy
    attenuation), so the Windthrow Index
        WI = dVV + dVH                                             [dB]
    is positive over windthrow.
3.  A pixel is flagged when WI exceeds a threshold.  Two modes:
        * adaptive (paper default): threshold = mean(WI) + a, with the
          mean taken over the analysis mask (forest) or the whole
          scene; ``a`` is a dB offset, optimum ~2.9 dB in the paper;
        * fixed: a user-defined absolute WI threshold in dB.
4.  Connected-component cleanup removes flagged objects smaller than
    ``min_pixels`` (paper optimum n = 27 pixels at 10 m ~ 0.27 ha).
5.  The cleaned mask is vectorised (gdal.Polygonize, 8-connected) into
    a GeoPackage / Shapefile with an ``area_ha`` attribute per object.

v0.8 addition — background normalization (``normalize_background=True``):
weather-driven changes between acquisitions (wet soil after rain,
snowmelt, vegetation growth) shift the whole post-storm image by a
common dB offset and flood the detection with false alarms.  Before
differencing, per polarisation the median difference
``median(post - pre)`` inside the background mask (forest sample; when
absent — the analysis mask or the whole scene) is subtracted from the
post-storm image, centring the background WI on ~0 dB.  The WI raster
is then written as ``<base>_wi_norm.tif``.

All heavy work is chunked row-band processing through GDAL so that
full-size Sentinel-1 IW scenes never need to be fully resident in RAM
(except one uint8 mask + one int32 label array during cleanup; see the
README "Memory" note).  Pure-numpy helpers are kept free of GDAL
imports so they can be unit-tested without a GDAL runtime.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from osgeo import gdal, ogr, osr  # always present in the QGIS Python env
    gdal.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None  # type: ignore
    ogr = None  # type: ignore
    osr = None  # type: ignore

try:
    import scipy.ndimage
except Exception:  # pragma: no cover
    scipy = None  # type: ignore

from .base import OperationCancelled

#: Fraction of negative pixels marking a chunk as "already in dB"
#: (kept in sync with the preprocessor's heuristic).
_DB_DETECT_FRACTION = 0.05
#: Values below this are no-data sentinels, not physical dB.
_DB_NODATA_FLOOR = -100.0
#: No-data value written to the float32 WI raster.
WI_NODATA = -9999.0
#: Sentinel used to neutralise NaN inside the median filter window.
_MEDIAN_SENTINEL = 1.0e6
#: Default row-band height for chunked processing (pixels).
_CHUNK_ROWS = 512

ProgressCallback = Optional[Callable[[float, str], None]]
CancelCallback = Optional[Callable[[], bool]]


# ======================================================================
# Pure-numpy helpers (unit-testable without GDAL)
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


# ======================================================================
# GDAL-level helpers
# ======================================================================
def _ensure_gdal() -> None:
    if gdal is None:  # pragma: no cover
        raise ImportError("osgeo.gdal is required for windthrow detection")


def _read_raster_info(path: str) -> Dict:
    """Return {width, height, geotransform, projection, nodata} of a raster."""
    _ensure_gdal()
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Cannot open raster: {path}")
    try:
        if ds.RasterCount < 1:
            raise RuntimeError(f"Raster has no bands: {path}")
        band = ds.GetRasterBand(1)
        return {
            "width": ds.RasterXSize,
            "height": ds.RasterYSize,
            "geotransform": ds.GetGeoTransform(),
            "projection": ds.GetProjection(),
            "nodata": band.GetNoDataValue(),
        }
    finally:
        ds = None


def _same_grid(info_a: Dict, info_b: Dict, tol: float = 1e-9) -> bool:
    """True when two raster infos describe the same grid."""
    if (info_a["width"] != info_b["width"]
            or info_a["height"] != info_b["height"]):
        return False
    gt_a = tuple(info_a["geotransform"])
    gt_b = tuple(info_b["geotransform"])
    if len(gt_a) != 6 or len(gt_b) != 6:
        return False
    return all(abs(x - y) <= tol for x, y in zip(gt_a, gt_b))


def ensure_aligned(path: str, ref_info: Dict, tmp_dir: str) -> str:
    """Return a path guaranteed to share the reference grid.

    When the input already matches the reference grid (the usual case
    for same-track Sentinel-1 products) the original path is returned
    untouched.  Otherwise the raster is warped (bilinear, appropriate
    for dB data) onto the reference grid into ``tmp_dir``.
    """
    _ensure_gdal()
    info = _read_raster_info(path)
    if _same_grid(info, ref_info):
        return path
    os.makedirs(tmp_dir, exist_ok=True)
    out_path = os.path.join(
        tmp_dir, f"_warped_{os.path.splitext(os.path.basename(path))[0]}.tif"
    )
    if os.path.exists(out_path):
        try:
            gdal.GetDriverByName("GTiff").Delete(out_path)
        except Exception:
            pass
    gdal.Warp(
        out_path,
        path,
        format="GTiff",
        width=ref_info["width"],
        height=ref_info["height"],
        outputBounds=(
            ref_info["geotransform"][0],
            ref_info["geotransform"][3] + ref_info["geotransform"][5] * ref_info["height"],
            ref_info["geotransform"][0] + ref_info["geotransform"][1] * ref_info["width"],
            ref_info["geotransform"][3],
        ),
        dstNodata=WI_NODATA,
        resampleAlg="bilinear",
        multithread=True,
    )
    return out_path


def build_median_composite(
    files: Sequence[str],
    ref_info: Dict,
    tmp_dir: str,
    output_path: str,
    progress_cb: ProgressCallback = None,
    cancel_cb: CancelCallback = None,
) -> str:
    """Pixel-wise median composite of aligned dB rasters (chunked).

    ``files`` must contain at least two rasters (for a single raster the
    caller uses it directly).  Every input is aligned to ``ref_info``
    first, converted to dB per chunk, and combined with ``nanmedian``.
    Returns the absolute path of the written float32 GeoTIFF.
    """
    _ensure_gdal()
    if len(files) < 2:
        raise ValueError("build_median_composite needs >= 2 files")
    aligned = [ensure_aligned(f, ref_info, tmp_dir) for f in files]

    datasets = []
    try:
        for p in aligned:
            ds = gdal.Open(p, gdal.GA_ReadOnly)
            if ds is None:
                raise RuntimeError(f"Cannot open raster: {p}")
            datasets.append(ds)

        width, height = ref_info["width"], ref_info["height"]
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(output_path):
            try:
                gdal.GetDriverByName("GTiff").Delete(output_path)
            except Exception:
                pass
        driver = gdal.GetDriverByName("GTiff")
        dst = driver.Create(
            output_path, width, height, 1, gdal.GDT_Float32,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER",
                     "PREDICTOR=3"],
        )
        if dst is None:
            raise RuntimeError(f"Cannot create output file: {output_path}")
        try:
            dst.SetGeoTransform(ref_info["geotransform"])
            if ref_info["projection"]:
                dst.SetProjection(ref_info["projection"])
            band = dst.GetRasterBand(1)
            band.SetNoDataValue(WI_NODATA)

            n_files = len(datasets)
            for y0 in range(0, height, _CHUNK_ROWS):
                if cancel_cb is not None and cancel_cb():
                    raise OperationCancelled()
                rows = min(_CHUNK_ROWS, height - y0)
                stack = np.empty((n_files, rows, width), dtype=np.float32)
                for i, ds in enumerate(datasets):
                    chunk = ds.GetRasterBand(1).ReadAsArray(
                        0, y0, width, rows
                    )
                    if chunk is None:
                        raise RuntimeError(
                            f"Failed to read rows {y0}..{y0 + rows} of {aligned[i]}"
                        )
                    stack[i] = to_db_domain_chunk(chunk)
                with np.errstate(invalid="ignore"):
                    med = np.nanmedian(stack, axis=0)
                med = med.astype(np.float32, copy=False)
                med[~np.isfinite(med)] = WI_NODATA
                band.WriteArray(med, 0, y0)
                if progress_cb is not None:
                    frac = (y0 + rows) / float(height) * 100.0
                    progress_cb(frac, "Compositing")
                del stack
            band.FlushCache()
        finally:
            dst = None
    finally:
        datasets.clear()

    return os.path.abspath(output_path)


def rasterize_vector_mask(
    vector_path: str, ref_info: Dict, tmp_dir: str
) -> str:
    """Burn a vector layer onto the reference grid (inside = 255)."""
    _ensure_gdal()
    os.makedirs(tmp_dir, exist_ok=True)
    out_path = os.path.join(
        tmp_dir,
        f"_mask_{os.path.splitext(os.path.basename(vector_path))[0]}.tif",
    )
    if os.path.exists(out_path):
        try:
            gdal.GetDriverByName("GTiff").Delete(out_path)
        except Exception:
            pass
    gt = ref_info["geotransform"]
    gdal.Rasterize(
        out_path,
        vector_path,
        format="GTiff",
        outputBounds=(
            gt[0],
            gt[3] + gt[5] * ref_info["height"],
            gt[0] + gt[1] * ref_info["width"],
            gt[3],
        ),
        width=ref_info["width"],
        height=ref_info["height"],
        burnValues=255,
        initValues=0,
        outputType=gdal.GDT_Byte,
    )
    return out_path


def _pixel_area_m2(geo_transform: Sequence[float], projection: str) -> float:
    """Approximate the ground area of one pixel in square metres."""
    gt = tuple(geo_transform)
    px_area = abs(gt[1] * gt[5] - gt[2] * gt[4])
    if px_area <= 0:
        return 0.0
    # Geographic CRS: convert deg^2 to m^2 using the scene-centre latitude.
    if projection and "GEOGCS" in projection and "PROJCS" not in projection:
        centre_y = gt[3] + gt[5] * 0.5
        lat = np.deg2rad(np.clip(centre_y, -89.5, 89.5))
        m_per_deg_lat = 111132.954 - 559.822 * np.cos(2 * lat) \
            + 1.175 * np.cos(4 * lat)
        m_per_deg_lon = (np.pi / 180.0) * 6378137.0 * np.cos(lat)
        px_area = abs(gt[1]) * m_per_deg_lon * abs(gt[5]) * m_per_deg_lat
    return float(px_area)


def _resolve_mask_raster(path: str, ref_info: Dict, tmp_dir: str) -> str:
    """Turn a raster-or-vector mask path into a reference-grid raster.

    GeoPackage / zipped vectors are rasterised; rasters are warped onto
    the reference grid only when they do not already match it.
    """
    _ensure_gdal()
    head = open(path, "rb").read(16)
    if head.startswith(b"PK"):  # GeoPackage / zipped vector
        return rasterize_vector_mask(path, ref_info, tmp_dir)
    try:
        ds = gdal.Open(path, gdal.GA_ReadOnly)
        is_raster = ds is not None
        ds = None
    except Exception:
        is_raster = False
    if is_raster:
        return ensure_aligned(path, ref_info, tmp_dir)
    return rasterize_vector_mask(path, ref_info, tmp_dir)


def _intersect_masks(
    mask_a: str, mask_b: str, ref_info: Dict, tmp_dir: str
) -> str:
    """Pixel-wise AND of two reference-grid mask rasters (0/255 byte)."""
    _ensure_gdal()
    os.makedirs(tmp_dir, exist_ok=True)
    name_a = os.path.splitext(os.path.basename(mask_a))[0]
    name_b = os.path.splitext(os.path.basename(mask_b))[0]
    out_path = os.path.join(tmp_dir, f"_mask_{name_a}_x_{name_b}.tif")
    if os.path.exists(out_path):
        try:
            gdal.GetDriverByName("GTiff").Delete(out_path)
        except Exception:
            pass
    ds_a = gdal.Open(mask_a, gdal.GA_ReadOnly)
    ds_b = gdal.Open(mask_b, gdal.GA_ReadOnly)
    if ds_a is None or ds_b is None:
        raise RuntimeError(
            f"Cannot open masks for intersection: {mask_a}, {mask_b}")
    width, height = ref_info["width"], ref_info["height"]
    for ds, label in ((ds_a, mask_a), (ds_b, mask_b)):
        if (ds.GetRasterBand(1).XSize != width
                or ds.GetRasterBand(1).YSize != height):
            raise RuntimeError(
                f"Mask grid mismatch while intersecting: {label} is "
                f"{ds.GetRasterBand(1).XSize}x{ds.GetRasterBand(1).YSize}, "
                f"expected {width}x{height}")
    driver = gdal.GetDriverByName("GTiff")
    out = driver.Create(
        out_path, width, height, 1, gdal.GDT_Byte,
        options=["TILED=YES", "COMPRESS=LZW"],
    )
    if out is None:
        raise RuntimeError(f"Cannot create intersected mask: {out_path}")
    try:
        out.SetGeoTransform(ref_info["geotransform"])
        if ref_info["projection"]:
            out.SetProjection(ref_info["projection"])
        band_a = ds_a.GetRasterBand(1)
        band_b = ds_b.GetRasterBand(1)
        out_band = out.GetRasterBand(1)
        out_band.SetNoDataValue(0)
        for y0 in range(0, height, _CHUNK_ROWS):
            rows = min(_CHUNK_ROWS, height - y0)
            a = band_a.ReadAsArray(0, y0, width, rows) > 0
            b = band_b.ReadAsArray(0, y0, width, rows) > 0
            out_band.WriteArray(((a & b).astype(np.uint8)) * 255, 0, y0)
        out_band.FlushCache()
    finally:
        ds_a = None
        ds_b = None
        out = None
    return out_path


def _create_output_vector(
    vector_path: str, projection_wkt: str
):
    """Create an empty polygon layer; returns (driver, datasource, layer)."""
    _ensure_gdal()
    ext = os.path.splitext(vector_path)[1].lower()
    if ext == ".shp":
        driver_name = "ESRI Shapefile"
        layer_name = os.path.splitext(os.path.basename(vector_path))[0]
    else:
        driver_name = "GPKG"
        layer_name = "windthrow"
    driver = ogr.GetDriverByName(driver_name)
    if driver is None:
        raise RuntimeError(f"OGR driver not available: {driver_name}")
    if os.path.exists(vector_path):
        try:
            driver.DeleteDataSource(vector_path)
        except Exception:
            pass
    parent = os.path.dirname(os.path.abspath(vector_path))
    os.makedirs(parent, exist_ok=True)
    ds = driver.CreateDataSource(vector_path)
    if ds is None:
        raise RuntimeError(f"Cannot create output vector: {vector_path}")
    srs = None
    if projection_wkt:
        srs = osr.SpatialReference()
        if srs.ImportFromWkt(projection_wkt) != 0:
            srs = None
    layer = ds.CreateLayer(layer_name, srs, ogr.wkbPolygon)
    if layer is None:
        raise RuntimeError(f"Cannot create layer in {vector_path}")
    field = ogr.FieldDefn("area_ha", ogr.OFTReal)
    field.SetWidth(16)
    field.SetPrecision(4)
    if layer.CreateField(field) != 0:
        raise RuntimeError("Cannot create area_ha field")
    return ds, layer


# ======================================================================
# Main detector
# ======================================================================
class WindthrowDetector:
    """Bi-temporal WI windthrow detector (Rüetschi et al. 2019).

    Parameters mirror the paper's decision tree: ``a_db`` (dB offset
    above the mean WI, adaptive mode) or ``fixed_threshold_db`` (fixed
    mode), ``min_pixels`` (minimum object size in pixels), and an
    optional median filter applied to WI before thresholding.
    """

    def __init__(
        self,
        threshold_mode: str = "adaptive",
        a_db: float = 2.9,
        fixed_threshold_db: float = 3.0,
        min_pixels: int = 27,
        median_filter_size: int = 3,
        normalize_background: bool = True,
    ) -> None:
        if threshold_mode not in ("adaptive", "fixed"):
            raise ValueError("threshold_mode must be 'adaptive' or 'fixed'")
        self.threshold_mode = threshold_mode
        self.a_db = float(a_db)
        self.fixed_threshold_db = float(fixed_threshold_db)
        self.min_pixels = int(min_pixels)
        self.median_filter_size = int(median_filter_size)
        self.normalize_background = bool(normalize_background)
        # Diagnostics filled in by detect_file():
        self.mean_wi: Optional[float] = None
        self.threshold_used: Optional[float] = None
        self.n_objects: int = 0
        self.composites: List[str] = []
        # Per-polarisation background offsets (dB) applied to the post
        # image when normalize_background is enabled (v0.8).
        self.offset_db: Dict[str, float] = {}

    # ------------------------------------------------------------------
    def detect_file(
        self,
        pre_paths: Sequence[str],
        post_paths: Sequence[str],
        output_base: str,
        analysis_mask_path: Optional[str] = None,
        progress_cb: ProgressCallback = None,
        cancel_cb: CancelCallback = None,
        background_mask_path: Optional[str] = None,
        forest_mask_path: Optional[str] = None,
    ) -> Dict[str, object]:
        """Run the full detection chain and write raster + vector outputs.

        :param pre_paths: SAR files covering the pre-storm window.
        :param post_paths: SAR files covering the post-storm window.
        :param output_base: output path base (no extension); produces
            ``<base>_wi.tif`` (or ``<base>_wi_norm.tif`` when background
            normalization is on), ``<base>_mask.tif``, ``<base>.gpkg``
            and, when a stack is composited, ``<base>_pre_<pol>.tif`` /
            ``<base>_post_<pol>.tif``.
        :param analysis_mask_path: optional raster or vector; detection
            flags are restricted to pixels inside (value > 0 / polygon
            areas).
        :param background_mask_path: optional raster or vector defining
            the background/forest sample used for the adaptive-threshold
            mean WI and, when ``normalize_background`` is on, for the
            per-polarisation median post−pre offsets.  Falls back to
            ``analysis_mask_path`` and then to the whole scene.  For the
            Rüetschi-style forest mean pass a forest sample that EXCLUDES
            known windthrow polygons (e.g. a buffer around them minus
            the polygons themselves).
        :param forest_mask_path: optional raster or vector defining the
            FOREST area (v0.9); detections are restricted to the
            intersection with ``analysis_mask_path``, and — when no
            ``background_mask_path`` is given — the adaptive-threshold
            mean is computed over the forest only (paper behaviour).
            ESA WorldCover rasters built by
            ``sources.forest_mask`` are directly usable here.
        :return: dict with keys ``wi``, ``mask``, ``vector``,
            ``composites``, ``threshold_db``, ``mean_wi``, ``offset_db``,
            ``normalize_background``.
        """
        _ensure_gdal()
        report = (lambda f, m: progress_cb(f, m) if progress_cb else None)
        cancelled = (lambda: bool(cancel_cb()) if cancel_cb else False)

        # ---- 1. Validate inputs and pick polarisations ----------------
        if not pre_paths or not post_paths:
            raise ValueError("Both pre-storm and post-storm file lists are required")
        for p in list(pre_paths) + list(post_paths):
            if not os.path.isfile(p):
                raise ValueError(f"File not found: {p}")
        pols = common_polarizations(pre_paths, post_paths)
        if not pols:
            raise ValueError(
                "No common polarisation found between the pre- and "
                "post-storm file names (looked for vv/vh/hh/hv tokens)."
            )

        pre_by_pol = pair_by_polarization(pre_paths)
        post_by_pol = pair_by_polarization(post_paths)

        base_dir = os.path.dirname(os.path.abspath(output_base)) or "."
        os.makedirs(base_dir, exist_ok=True)
        tmp_dir = os.path.join(base_dir, "_windthrow_tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        base_name = os.path.basename(output_base)
        if base_name.lower().endswith((".tif", ".tiff", ".gpkg", ".shp")):
            base_name = os.path.splitext(base_name)[0]
            output_base = os.path.join(base_dir, base_name)

        # Reference grid: the first post-storm file of the first pol.
        ref_info = _read_raster_info(post_by_pol[pols[0]][0])
        width, height = ref_info["width"], ref_info["height"]

        # Optional analysis mask (detection restriction, flags only).
        mask_raster: Optional[str] = None
        if analysis_mask_path:
            if not os.path.isfile(analysis_mask_path):
                raise ValueError(f"Mask file not found: {analysis_mask_path}")
            mask_raster = _resolve_mask_raster(
                analysis_mask_path, ref_info, tmp_dir)

        # Optional background/forest sample mask (statistics only).
        bg_mask_raster: Optional[str] = None
        if background_mask_path:
            if not os.path.isfile(background_mask_path):
                raise ValueError(
                    f"Background mask file not found: {background_mask_path}")
            bg_mask_raster = _resolve_mask_raster(
                background_mask_path, ref_info, tmp_dir)

        # Optional forest mask (v0.9) — restricts the analysis area to
        # forest: detections and (by default) the adaptive-threshold
        # statistics live inside the forest ∩ analysis-mask intersection.
        forest_raster: Optional[str] = None
        if forest_mask_path:
            if not os.path.isfile(forest_mask_path):
                raise ValueError(
                    f"Forest mask file not found: {forest_mask_path}")
            forest_raster = _resolve_mask_raster(
                forest_mask_path, ref_info, tmp_dir)
            if mask_raster:
                mask_raster = _intersect_masks(
                    mask_raster, forest_raster, ref_info, tmp_dir)
            else:
                mask_raster = forest_raster

        # ---- 2. Composites per period / polarisation ------------------
        comp_paths: Dict[Tuple[str, str], str] = {}
        self.composites = []
        comp_span = 40.0 / (2.0 * len(pols))
        offset_span = (10.0 / len(pols)) if self.normalize_background else 0.0
        done_steps = 0
        for period, by_pol in (("pre", pre_by_pol), ("post", post_by_pol)):
            for pol in pols:
                files = by_pol[pol]
                if len(files) == 1:
                    comp_paths[(period, pol)] = ensure_aligned(
                        files[0], ref_info, tmp_dir)
                else:
                    out = f"{output_base}_{period}_{pol}.tif"
                    build_median_composite(
                        files, ref_info, tmp_dir, out,
                        progress_cb=lambda f, m, d0=done_steps * comp_span:
                            report(d0 + (f / 100.0) * comp_span, m),
                        cancel_cb=cancel_cb,
                    )
                    comp_paths[(period, pol)] = out
                    self.composites.append(out)
                done_steps += 1
                report(done_steps * comp_span, "Compositing")

        # ---- 2b. Background normalization offsets (v0.8) --------------
        # Per polarisation: offset = median(post - pre) inside the
        # background sample (bg mask, else analysis mask, else whole
        # scene).  The offset is subtracted from the post image before
        # differencing, centring the background WI on ~0 dB.
        offsets: Dict[str, float] = {}
        if self.normalize_background:
            stats_ds_h = None
            stats_band_h = None
            if bg_mask_raster:
                stats_ds_h = gdal.Open(bg_mask_raster, gdal.GA_ReadOnly)
                stats_band_h = stats_ds_h.GetRasterBand(1)
            elif mask_raster:
                stats_ds_h = gdal.Open(mask_raster, gdal.GA_ReadOnly)
                stats_band_h = stats_ds_h.GetRasterBand(1)
            pol_done = 0
            try:
                for pol in pols:
                    if cancelled():
                        raise OperationCancelled()
                    pre_p = gdal.Open(comp_paths[("pre", pol)], gdal.GA_ReadOnly)
                    post_p = gdal.Open(comp_paths[("post", pol)], gdal.GA_ReadOnly)
                    if pre_p is None or post_p is None:
                        raise RuntimeError(
                            f"Cannot open composites for offset ({pol})")
                    samples: List[np.ndarray] = []
                    for y0 in range(0, height, _CHUNK_ROWS):
                        if cancelled():
                            raise OperationCancelled()
                        rows = min(_CHUNK_ROWS, height - y0)
                        pre_c = to_db_domain_chunk(
                            pre_p.GetRasterBand(1).ReadAsArray(0, y0, width, rows))
                        post_c = to_db_domain_chunk(
                            post_p.GetRasterBand(1).ReadAsArray(0, y0, width, rows))
                        with np.errstate(invalid="ignore"):
                            delta = post_c - pre_c
                        if stats_band_h is not None:
                            inside = stats_band_h.ReadAsArray(
                                0, y0, width, rows) > 0
                            delta = delta[inside]
                        vals = delta[np.isfinite(delta)]
                        if vals.size:
                            samples.append(vals)
                    pre_p = None
                    post_p = None
                    if samples:
                        all_vals = np.concatenate(samples)
                        if all_vals.size > 8_000_000:
                            stride = int(np.ceil(
                                all_vals.size / 8_000_000.0))
                            all_vals = all_vals[::stride]
                        offsets[pol] = float(np.median(all_vals))
                    else:
                        offsets[pol] = 0.0
                    self.offset_db = dict(offsets)
                    pol_done += 1
                    report(40.0 + pol_done * offset_span,
                           "Background normalization")
            finally:
                stats_ds_h = None
                stats_band_h = None

        # ---- 3. WI raster + running statistics (chunked) --------------
        suffix = "_wi_norm" if self.normalize_background else "_wi"
        wi_path = f"{output_base}{suffix}.tif"
        if os.path.exists(wi_path):
            try:
                gdal.GetDriverByName("GTiff").Delete(wi_path)
            except Exception:
                pass
        driver = gdal.GetDriverByName("GTiff")
        wi_ds = driver.Create(
            wi_path, width, height, 1, gdal.GDT_Float32,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER",
                     "PREDICTOR=3"],
        )
        if wi_ds is None:
            raise RuntimeError(f"Cannot create output file: {wi_path}")
        wi_ds.SetGeoTransform(ref_info["geotransform"])
        if ref_info["projection"]:
            wi_ds.SetProjection(ref_info["projection"])
        wi_band = wi_ds.GetRasterBand(1)
        wi_band.SetNoDataValue(WI_NODATA)

        datasets: Dict[Tuple[str, str], object] = {}
        mask_ds = None
        mask_band = None
        bg_mask_ds = None
        bg_mask_band = None
        try:
            for key, path in comp_paths.items():
                ds = gdal.Open(path, gdal.GA_ReadOnly)
                if ds is None:
                    raise RuntimeError(f"Cannot open composite: {path}")
                datasets[key] = ds

            if mask_raster:
                mask_ds = gdal.Open(mask_raster, gdal.GA_ReadOnly)
                mask_band = mask_ds.GetRasterBand(1)
            # Adaptive-threshold statistics come from the background
            # sample (v0.8) when provided, else from the analysis mask
            # (v0.7.0 behaviour), else from the whole scene.
            if bg_mask_raster:
                bg_mask_ds = gdal.Open(bg_mask_raster, gdal.GA_ReadOnly)
                bg_mask_band = bg_mask_ds.GetRasterBand(1)
            stats_band = bg_mask_band if bg_mask_band is not None else mask_band

            running_sum = 0.0
            running_sumsq = 0.0
            running_count = 0

            for y0 in range(0, height, _CHUNK_ROWS):
                if cancelled():
                    raise OperationCancelled()
                rows = min(_CHUNK_ROWS, height - y0)
                wi_chunk = np.zeros((rows, width), dtype=np.float32)
                valid_chunk = np.ones((rows, width), dtype=bool)
                for pol in pols:
                    pre_chunk = datasets[("pre", pol)].GetRasterBand(1).ReadAsArray(
                        0, y0, width, rows)
                    post_chunk = datasets[("post", pol)].GetRasterBand(1).ReadAsArray(
                        0, y0, width, rows)
                    if pre_chunk is None or post_chunk is None:
                        raise RuntimeError(
                            f"Failed to read chunk rows {y0}..{y0 + rows}")
                    pre_db = to_db_domain_chunk(pre_chunk)
                    post_db = to_db_domain_chunk(post_chunk)
                    if self.normalize_background and pol in offsets:
                        post_db = post_db - offsets[pol]
                    with np.errstate(invalid="ignore"):
                        delta = post_db - pre_db
                    delta = delta.astype(np.float32, copy=False)
                    good = np.isfinite(delta)
                    wi_chunk[good] += delta[good]
                    # A pixel is valid only when EVERY polarisation yields
                    # a finite difference (paper sums VV + VH).
                    valid_chunk &= good
                wi_chunk[~valid_chunk] = WI_NODATA
                wi_band.WriteArray(wi_chunk, 0, y0)

                # Adaptive-threshold statistics (sample-limited).
                finite = wi_chunk != WI_NODATA
                if stats_band is not None:
                    inside = stats_band.ReadAsArray(0, y0, width, rows) > 0
                    finite &= inside
                vals = wi_chunk[finite].astype(np.float64, copy=False)
                if vals.size:
                    running_sum += float(vals.sum())
                    running_sumsq += float(np.square(vals).sum())
                    running_count += int(vals.size)
                report(50.0 + 20.0 * (y0 + rows) / float(height),
                       "Windthrow Index")
                del wi_chunk

            wi_band.FlushCache()
            if running_count == 0:
                raise RuntimeError(
                    "No valid overlapping pixels found — check that the "
                    "pre- and post-storm images cover the same area."
                )
            mean_wi = running_sum / running_count
            self.mean_wi = mean_wi
            if self.threshold_mode == "adaptive":
                threshold = adaptive_threshold(mean_wi, self.a_db)
            else:
                threshold = self.fixed_threshold_db
            self.threshold_used = threshold

            # ---- 4. Threshold pass (optional median filter, halo) ----
            full_mask = np.zeros((height, width), dtype=bool)
            r = self.median_filter_size // 2 if self.median_filter_size >= 3 else 0
            filter_active = self.median_filter_size >= 3 and scipy is not None
            if self.median_filter_size >= 3 and scipy is None:
                from ..logger import log_warning
                log_warning("scipy unavailable — WI median filter skipped")
            for y0 in range(0, height, _CHUNK_ROWS):
                if cancelled():
                    raise OperationCancelled()
                rows = min(_CHUNK_ROWS, height - y0)
                ry0, ry1 = max(0, y0 - r), min(height, y0 + rows + r)
                tile = wi_band.ReadAsArray(0, ry0, width, ry1 - ry0)
                if tile is None:
                    raise RuntimeError(f"Failed to re-read rows {ry0}..{ry1}")
                invalid = tile == WI_NODATA
                tile = tile.astype(np.float32, copy=False)
                tile[invalid] = np.nan
                if filter_active:
                    tile = median_filter_nan(tile, self.median_filter_size)
                centre = tile[y0 - ry0: y0 - ry0 + rows]
                flagged = mask_from_threshold(centre, threshold)
                flagged &= ~np.isnan(centre)
                if mask_band is not None:
                    inside = mask_band.ReadAsArray(0, y0, width, rows) > 0
                    flagged &= inside
                full_mask[y0:y0 + rows] = flagged
                report(70.0 + 10.0 * (y0 + rows) / float(height),
                       "Thresholding")
        finally:
            wi_ds = None
            datasets.clear()
            mask_ds = None
            mask_band = None
            bg_mask_ds = None
            bg_mask_band = None

        # ---- 5. Object cleanup (connected components) ------------------
        if self.min_pixels > 1:
            full_mask = filter_small_objects(full_mask, self.min_pixels)

        # ---- 6. Write the cleaned mask raster --------------------------
        mask_path = f"{output_base}_mask.tif"
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
            except Exception:
                pass
        finally:
            mask_out = None

        # ---- 7. Polygonise + attribute + size filter -------------------
        vector_path = (output_base if output_base.lower().endswith((".gpkg", ".shp"))
                       else output_base + ".gpkg")
        vds, layer = _create_output_vector(vector_path, ref_info["projection"])
        px_area_m2 = _pixel_area_m2(ref_info["geotransform"], ref_info["projection"])
        try:
            mask_rd = gdal.Open(mask_path, gdal.GA_ReadOnly)
            band = mask_rd.GetRasterBand(1)
            gdal.Polygonize(band, band, layer, -1, ["8CONNECTED=8"], None)
            min_area_m2 = self.min_pixels * px_area_m2 if self.min_pixels > 0 else 0.0
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
            mask_rd = None
            self.n_objects = n_kept
        finally:
            vds = None

        # ---- 8. Best-effort cleanup of temporaries ---------------------
        try:
            for f in os.listdir(tmp_dir):
                try:
                    os.remove(os.path.join(tmp_dir, f))
                except OSError:
                    pass
            os.rmdir(tmp_dir)
        except OSError:
            pass

        report(100.0, "Done")
        return {
            "wi": os.path.abspath(wi_path),
            "mask": os.path.abspath(mask_path),
            "vector": os.path.abspath(vector_path),
            "composites": list(self.composites),
            "threshold_db": self.threshold_used,
            "mean_wi": self.mean_wi,
            "n_objects": self.n_objects,
            "offset_db": dict(self.offset_db),
            "normalize_background": self.normalize_background,
            "forest_mask": (os.path.abspath(forest_raster)
                            if forest_raster else None),
        }
