"""Forest mask providers for windthrow detection (v0.9).

False alarms of the WI detector are dominated by non-forest land cover
(agricultural fields, clearcuts, bare ground) — restricting detection to
forest removes most of them.  Two mask sources are supported:

* **"worldcover"** — ESA WorldCover 10 m land cover (2020 / 2021),
  distributed on Microsoft Planetary Computer as the ``esa-worldcover``
  STAC collection.  The ``map`` asset is a byte land-cover COG whose
  class 10 is "Tree cover".  The tile(s) covering the AOI are located
  via the STAC API, signed, warped onto the Sentinel-1 reference grid
  with nearest-neighbour resampling and binarised.
* **"file"** — a user-provided raster (values > 0 = forest) or vector
  (polygons = forest).  It is passed through unchanged: the detector
  rasterises / warps it onto the reference grid itself
  (``_resolve_mask_raster``).

The result is always a byte raster on the exact reference grid
(255 = forest, 0 = everything else, nodata 0) ready to be used as
``forest_mask_path`` of :meth:`WindthrowDetector.detect_file`.

Caveat worth documenting: ESA WorldCover epochs (2020 / 2021) postdate
many storm events.  Young regrowth inside old windthrows may be mapped
as shrub/grass, so the mask can also exclude *true* positives; the
optional majority-filter cleaning pass (``majority_size``) bridges small
such gaps.  For operational use pick the epoch closest to (but not
after) the event.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from osgeo import gdal
except ImportError:  # pragma: no cover - handled by _ensure_gdal()
    gdal = None

try:
    from scipy.ndimage import median_filter as _scipy_median_filter
except ImportError:  # pragma: no cover - fallback path below
    _scipy_median_filter = None

from .base import OperationCancelled
from .pc_client import PlanetaryComputerClient
from .windthrow import _ensure_gdal, _read_raster_info

#: Raised when the user cancels a forest-mask build (same type the
#: detector and the SAR source raise, so the QGIS task machinery can
#: treat all cancellations uniformly).
OperationCancelledError = OperationCancelled

#: STAC collection id on Microsoft Planetary Computer.
WC_COLLECTION = "esa-worldcover"
#: COG asset with the 10 m classification ("Discrete Classification").
WC_ASSET = "map"
#: ESA WorldCover legend — 10 = Tree cover.
DEFAULT_FOREST_CLASSES: Tuple[int, ...] = (10,)

ProgressCb = Optional[Callable[[float, str], None]]
CancelCb = Optional[Callable[[], bool]]


# ======================================================================
# Reference-grid helpers
# ======================================================================
def read_ref_info(raster_path: str) -> Dict:
    """Public wrapper: read geotransform / projection / size of a raster."""
    return _read_raster_info(raster_path)


def bbox_4326(ref_info: Dict) -> Tuple[float, float, float, float]:
    """Bounding box of the reference grid in EPSG:4326 (lon/lat).

    Samples a 3x3 grid of corners and edge midpoints (sufficient for the
    km-scale AOIs of storm detection) and transforms them to WGS84.
    Axis order is forced to traditional GIS order — blindly trusting the
    default axis mapping silently swaps lon/lat and moves the AOI to a
    wrong hemisphere (a real bug caught during step 7).
    """
    _ensure_gdal()
    from osgeo import osr

    gt = ref_info["geotransform"]
    width, height = ref_info["width"], ref_info["height"]
    projection = ref_info.get("projection") or ""
    if not projection:
        raise ValueError(
            "Reference grid has no projection — cannot derive a WGS84 bbox")

    xs, ys = [], []
    for fy in (0.0, 0.5, 1.0):
        for fx in (0.0, 0.5, 1.0):
            xs.append(gt[0] + gt[1] * width * fx + gt[2] * height * fy)
            ys.append(gt[3] + gt[4] * width * fx + gt[5] * height * fy)

    src = osr.SpatialReference()
    src.ImportFromWkt(projection)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)
    points = transform.TransformPoints(list(zip(xs, ys)))
    if not points:
        raise RuntimeError("Coordinate transformation returned no points")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return (min(lons), min(lats), max(lons), max(lats))


# ======================================================================
# Array-level primitives (unit-testable without GDAL)
# ======================================================================
def classify_forest(
    arr: np.ndarray,
    forest_classes: Sequence[int] = DEFAULT_FOREST_CLASSES,
) -> np.ndarray:
    """Binarise a land-cover class raster: 255 = forest, 0 = other."""
    classes = np.asarray(list(forest_classes), dtype=arr.dtype)
    return np.isin(arr, classes).astype(np.uint8) * 255


def majority_filter_mask(
    mask: np.ndarray, size: int = 3
) -> np.ndarray:
    """Despeckle a binary 0/255 mask with a majority (median) filter.

    Keeps solid blocks, removes isolated single pixels and smooths
    jagged edges.  ``size`` must be odd and >= 3; sizes < 3 or even
    sizes return the input unchanged.
    """
    mask = np.asarray(mask)
    if size is None or size < 3 or size % 2 == 0:
        return mask
    if _scipy_median_filter is not None:
        return _scipy_median_filter(
            mask.astype(np.uint8), size=int(size), mode="nearest"
        ).astype(mask.dtype)
    # NumPy fallback: neighbour voting (roll-based, edges wrapped — the
    # scipy path above is preferred; this one only needs to be decent).
    binary = mask > 0
    votes = np.zeros(mask.shape, dtype=np.int32)
    r = int(size) // 2
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            votes += np.roll(np.roll(binary, dy, axis=0), dx, axis=1)
    return ((votes * 2) > int(size) * int(size)).astype(mask.dtype) * 255


# ======================================================================
# Raster-level builder
# ======================================================================
def _grid_bounds(ref_info: Dict) -> Tuple[float, float, float, float]:
    gt = ref_info["geotransform"]
    width, height = ref_info["width"], ref_info["height"]
    return (
        gt[0],
        gt[3] + gt[5] * height,
        gt[0] + gt[1] * width,
        gt[3],
    )


def build_forest_mask_from_rasters(
    raster_paths: Sequence[str],
    ref_info: Dict,
    out_path: str,
    forest_classes: Sequence[int] = DEFAULT_FOREST_CLASSES,
    majority_size: int = 3,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> str:
    """Warp land-cover raster(s) onto the reference grid and binarise.

    :param raster_paths: local paths or GDAL-supported URLs
        (``/vsicurl/https://...``) of the land-cover COG tiles.
    :param ref_info: reference grid (``read_ref_info`` of a Sentinel-1
        composite): geotransform, projection, width, height.
    :param out_path: destination GeoTIFF (byte, 255 = forest, 0 = other).
    :returns: ``out_path``.
    """
    _ensure_gdal()
    report = (lambda f, m: progress_cb(f, m) if progress_cb else None)
    cancelled = (lambda: bool(cancel_cb()) if cancel_cb else False)

    if not raster_paths:
        raise RuntimeError("No land-cover raster paths provided")
    projection = ref_info.get("projection") or ""
    if not projection:
        raise ValueError("Reference grid has no projection")

    report(5.0, "Forest mask: building VRT of source tiles")
    vrt = gdal.BuildVRT("", list(raster_paths))
    if vrt is None:
        raise RuntimeError(
            "Cannot build a VRT of the land-cover tiles — check that the "
            "source URLs are reachable")
    warp_path = out_path + ".warp_tmp.tif"
    try:
        if cancelled():
            raise OperationCancelledError()
        report(15.0, "Forest mask: resampling to the radar grid")
        bounds = _grid_bounds(ref_info)
        gdal.Warp(
            warp_path,
            vrt,
            format="GTiff",
            outputBounds=bounds,
            width=ref_info["width"],
            height=ref_info["height"],
            dstSRS=projection,
            resampleAlg="near",
            outputType=gdal.GDT_Byte,
            dstNodata=0,
            multithread=True,
        )
        vrt = None
        if cancelled():
            raise OperationCancelledError()
        src = gdal.Open(warp_path, gdal.GA_ReadOnly)
        if src is None:
            raise RuntimeError(
                f"Resampled land-cover raster is unreadable: {warp_path}")
        try:
            classes = src.GetRasterBand(1).ReadAsArray()
        finally:
            src = None
        if classes is None:
            raise RuntimeError(
                f"Failed to read resampled land-cover data: {warp_path}")

        report(55.0, "Forest mask: classifying")
        forest = classify_forest(classes, forest_classes)
        del classes
        if cancelled():
            raise OperationCancelledError()

        if majority_size and majority_size >= 3:
            report(75.0, "Forest mask: majority filter")
            forest = majority_filter_mask(forest, majority_size)
    finally:
        try:
            gdal.GetDriverByName("GTiff").Delete(warp_path)
        except Exception:
            pass

    report(90.0, "Forest mask: writing output")
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        out_path, ref_info["width"], ref_info["height"], 1, gdal.GDT_Byte,
        options=["TILED=YES", "COMPRESS=LZW"],
    )
    if ds is None:
        raise RuntimeError(f"Cannot create forest mask: {out_path}")
    try:
        ds.SetGeoTransform(ref_info["geotransform"])
        ds.SetProjection(projection)
        band = ds.GetRasterBand(1)
        band.WriteArray(forest)
        band.SetNoDataValue(0)
        band.FlushCache()
    finally:
        ds = None
    report(100.0, "Forest mask: done")
    return out_path


# ======================================================================
# ESA WorldCover via Planetary Computer
# ======================================================================
def fetch_worldcover_hrefs(
    bbox: Tuple[float, float, float, float],
    year: int = 2020,
    client: Optional[PlanetaryComputerClient] = None,
) -> List[str]:
    """Locate and sign the ESA WorldCover ``map`` asset(s) for a bbox."""
    client = client or PlanetaryComputerClient()
    datetime_range = (
        f"{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z")
    items = client.search_items(
        collection=WC_COLLECTION,
        bbox=bbox,
        datetime_range=datetime_range,
    )
    if not items:
        raise RuntimeError(
            f"No ESA WorldCover items found for bbox {list(bbox)} in "
            f"{year} on Planetary Computer ({WC_COLLECTION})")
    # Belt and braces: keep only items whose id carries the requested
    # year; fall back to the unfiltered set if the filter empties it.
    year_tag = str(year)
    tagged = [it for it in items if year_tag in str(it.get("id", ""))]
    if tagged:
        items = tagged

    hrefs: List[str] = []
    seen = set()
    for item in items:
        asset = (item.get("assets") or {}).get(WC_ASSET) or {}
        href = asset.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        hrefs.append(client.sign_href(href))
    if not hrefs:
        raise RuntimeError(
            f"ESA WorldCover items for {year} carry no '{WC_ASSET}' asset")
    return hrefs


def build_worldcover_forest_mask(
    bbox: Tuple[float, float, float, float],
    ref_info: Dict,
    out_path: str,
    year: int = 2020,
    forest_classes: Sequence[int] = DEFAULT_FOREST_CLASSES,
    majority_size: int = 3,
    client: Optional[PlanetaryComputerClient] = None,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> str:
    """Download-free forest mask from ESA WorldCover for ``bbox``.

    The WorldCover COG tiles are read through ``/vsicurl/`` (only the
    AOI window is transferred) and resampled onto the radar grid.
    """
    report = (lambda f, m: progress_cb(f, m) if progress_cb else None)
    cancelled = (lambda: bool(cancel_cb()) if cancel_cb else False)
    if cancelled():
        raise OperationCancelledError()

    report(2.0, f"Forest mask: searching ESA WorldCover {year}")
    hrefs = fetch_worldcover_hrefs(bbox, year=year, client=client)
    # GDAL needs the /vsicurl/ prefix for authenticated-range COG reads.
    vsicurl = [h if h.startswith("/vsicurl/") else "/vsicurl/" + h
               for h in hrefs]
    return build_forest_mask_from_rasters(
        vsicurl,
        ref_info,
        out_path,
        forest_classes=forest_classes,
        majority_size=majority_size,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )


# ======================================================================
# Dispatcher
# ======================================================================
def build_forest_mask(
    source: str,
    ref_info: Dict,
    out_path: str,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    mask_file: Optional[str] = None,
    year: int = 2020,
    forest_classes: Sequence[int] = DEFAULT_FOREST_CLASSES,
    majority_size: int = 3,
    client: Optional[PlanetaryComputerClient] = None,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> str:
    """Build (or pass through) a forest mask on the reference grid.

    :param source: ``"worldcover"`` (auto-download from Planetary
        Computer; requires ``bbox``) or ``"file"`` (use ``mask_file``
        as-is — the detector rasterises/warps it itself).
    :returns: path of the forest mask to pass to the detector.  For
        ``source="file"`` this is simply ``mask_file``.
    """
    if source == "worldcover":
        if bbox is None:
            raise ValueError("bbox is required for the WorldCover source")
        return build_worldcover_forest_mask(
            bbox, ref_info, out_path, year=year,
            forest_classes=forest_classes, majority_size=majority_size,
            client=client, progress_cb=progress_cb, cancel_cb=cancel_cb)
    if source == "file":
        if not mask_file or not os.path.isfile(mask_file):
            raise ValueError(f"Forest mask file not found: {mask_file}")
        return mask_file
    raise ValueError(
        f"Unknown forest mask source: {source!r} (use 'worldcover' or 'file')")
