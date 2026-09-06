"""Forest mask providers for windthrow detection (v0.9 / v1.1).

False alarms of the WI detector are dominated by non-forest land cover
(agricultural fields, clearcuts, bare ground) — restricting detection to
forest removes most of them.  Three mask sources are supported:

* **"gfc"** (v1.1) — Hansen/UMD Global Forest Change (GFW),
  *reconstructed for the year preceding the event* — the retrospective
  mask of record (report ed.8 §9).  Given the storm year ``Y``, the
  mask keeps pixels with ``treecover2000 >= tau`` that were NOT lost in
  2001..Y-1::

      forest_candidate(Y) = treecover2000 >= tau
                            AND NOT (1 <= lossyear <= Y - 2001)

  Losses in the EVENT year are deliberately kept in the candidate mask
  (they ARE the windthrow); the separate ``background`` variant
  additionally removes them, giving a clean-forest background sample
  for the coherence-DiD statistics (validated on 12 events:
  background shrinks 28-53 %, the "mask from the future" failure of
  single-epoch maps is impossible by construction).  Layers are read
  through ``/vsicurl/`` window by window — only the AOI is
  transferred; the boolean candidate raster is averaged onto the
  reference grid and a target pixel is forest when the forest
  fraction >= ``frac_threshold`` (0.5; mirrors the 30 m -> 80 m
  rescore).
* **"worldcover"** — ESA WorldCover 10 m land cover (2020 / 2021),
  distributed on Microsoft Planetary Computer as the ``esa-worldcover``
  STAC collection.  The ``map`` asset is a byte land-cover COG whose
  class 10 is "Tree cover".  The tile(s) covering the AOI are located
  via the STAC API, signed, warped onto the Sentinel-1 reference grid
  with nearest-neighbour resampling and binarised.  Single-epoch:
  acceptable only for near-real-time use — for retrospective events it
  is a "mask from the future" (report ed.8 §9: WorldCover-2021 left
  ZERO background pixels around the 30.07.2017 squall ID655).
* **"file"** — a user-provided raster (values > 0 = forest) or vector
  (polygons = forest).  It is passed through unchanged: the detector
  rasterises / warps it onto the reference grid itself
  (``_resolve_mask_raster``).

The result is always a byte raster on the exact reference grid
(255 = forest, 0 = everything else, nodata 0) ready to be used as
``forest_mask_path`` of :meth:`WindthrowDetector.detect_file`.

Caveats worth documenting: ESA WorldCover epochs (2020 / 2021) postdate
many storm events.  Young regrowth inside old windthrows may be mapped
as shrub/grass, so the mask can also exclude *true* positives; the
optional majority-filter cleaning pass (``majority_size``) bridges small
such gaps.  GFC is annual: intra-year pre-storm logging is not
resolvable (the ID654 lesson), and losses after the GFC product year
are unknown.
"""

from __future__ import annotations

import math
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

# ======================================================================
# Hansen / UMD Global Forest Change (GFW) — annual reconstruction (v1.1)
# ======================================================================
#: Published GFC version used by the project (report ed.8 §9).
GFC_VERSION = "GFC-2024-v1.12"
#: Public Google Cloud Storage bucket of the GFC tiles (no auth needed).
GFC_BASE_URL = (
    "https://storage.googleapis.com/earthenginepartners-hansen/"
    + GFC_VERSION)
#: Layers required by the reconstruction recipe.
GFC_LAYERS = ("treecover2000", "lossyear")
#: Valid event years for GFC-2024-v1.12 (loss years 2001-2024).
GFC_LOSS_YEARS = (2001, 2024)
#: Default treecover2000 threshold in % (tau grid 20/30/50 was tested —
#: conclusions robust; tau=30 is the primary value of report ed.8 §9).
GFC_DEFAULT_TAU = 30
#: Default forest-fraction threshold of an aggregated target pixel
#: (mirrors the validated 30 m -> 80 m "average" rescore).
GFC_DEFAULT_FRAC = 0.5
#: GFC tiles are 10 x 10 degrees named by their UPPER-LEFT corner
#: ("60N_040E" covers 50-60 N, 40-50 E — a real pitfall caught during
#: the rescore: events at 61-62 N live in *70N_* tiles).
_GFC_TILE_DEG = 10
_GFC_MIN_LAT, _GFC_MAX_LAT = -60.0, 80.0  # product lat coverage

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


def gfc_tile_name(ul_lat: float, ul_lon: float) -> str:
    """Hansen tile name from its upper-left corner (``50N_030E`` form)."""
    ns = "N" if ul_lat >= 0 else "S"
    ew = "E" if ul_lon >= 0 else "W"
    return (f"{abs(int(ul_lat)):02d}{ns}_{abs(int(ul_lon)):03d}{ew}")


def gfc_tiles_for_bbox(bbox: Tuple[float, float, float, float]) -> List[str]:
    """GFC 10-deg tile names covering a WGS84 bbox (lon/lat order).

    Pure geometry — no network.  Rows go north -> south, columns west
    -> east, matching the layout of the GFC tile index.
    """
    xmin, ymin, xmax, ymax = bbox
    if not (xmin < xmax and ymin < ymax):
        raise ValueError(f"Invalid bbox {tuple(bbox)} (min/max order)")
    if ymax > _GFC_MAX_LAT or ymin < _GFC_MIN_LAT:
        raise ValueError(
            f"bbox latitude {ymin}..{ymax} outside the GFC coverage "
            f"({_GFC_MIN_LAT}..{_GFC_MAX_LAT})")
    tiles: List[str] = []
    ul_lat = int(math.ceil(ymax / _GFC_TILE_DEG) * _GFC_TILE_DEG)
    while ul_lat - _GFC_TILE_DEG < ymax and ul_lat > ymin:
        ul_lon = int(math.floor(xmin / _GFC_TILE_DEG) * _GFC_TILE_DEG)
        row: List[str] = []
        while ul_lon < xmax:
            row.append(gfc_tile_name(ul_lat, ul_lon))
            ul_lon += _GFC_TILE_DEG
        tiles.extend(row)
        ul_lat -= _GFC_TILE_DEG
    if not tiles:
        raise RuntimeError(f"No GFC tile covers bbox {tuple(bbox)}")
    return tiles


def gfc_layer_url(tile: str, layer: str) -> str:
    """Public URL of one GFC layer tile (no authentication required)."""
    if layer not in GFC_LAYERS:
        raise ValueError(
            f"Unknown GFC layer {layer!r} (use one of {GFC_LAYERS})")
    return (f"{GFC_BASE_URL}/Hansen_{GFC_VERSION}_{layer}_{tile}.tif")


def gfc_forest_parts(
    treecover: np.ndarray,
    lossyear: np.ndarray,
    event_year: int,
    tau: int = GFC_DEFAULT_TAU,
) -> Tuple[np.ndarray, np.ndarray]:
    """Reconstruct the two annual forest masks for a storm year.

    ``lossyear`` codes the loss year as 2000 + value (0 = no loss
    through the product end).  For an event in year ``Y`` the masks
    exclude losses of 2001..Y-1 (forest as of the year preceding the
    event):

    * ``candidate`` — keeps losses of the EVENT year: the windthrow
      itself must stay inside the analysis area;
    * ``background`` — additionally removes event-year losses: a
      clean-forest background sample for adaptive-threshold statistics
      (report ed.8 §9: «пиксели с потерей в год события из фона
      исключаются, но из маски-кандидата не убираются»).  Losses AFTER
      the event year cannot be known yet and are kept in both.

    :returns: ``(candidate, background)`` boolean arrays.
    """
    tc = np.asarray(treecover)
    ly = np.asarray(lossyear).astype(np.int16)
    ftc = tc >= tau
    ymax = int(event_year) - 2000
    if ymax <= 0:  # event 2000 or earlier: no mapped losses before it
        return ftc, ftc.copy()
    candidate = ftc & ((ly == 0) | (ly >= ymax))
    background = ftc & ((ly == 0) | (ly >= ymax + 1))
    return candidate, background


def gfc_forest_candidate(treecover, lossyear, event_year,
                         tau: int = GFC_DEFAULT_TAU) -> np.ndarray:
    """Forest-candidates mask (event-year losses kept)."""
    return gfc_forest_parts(treecover, lossyear, event_year, tau)[0]


def gfc_forest_background(treecover, lossyear, event_year,
                          tau: int = GFC_DEFAULT_TAU) -> np.ndarray:
    """Clean-forest background mask (event-year losses removed)."""
    return gfc_forest_parts(treecover, lossyear, event_year, tau)[1]


def _read_remote_window(
    url: str, bbox: Tuple[float, float, float, float]
) -> Optional[Tuple[np.ndarray, Tuple[float, ...]]]:
    """Windowed read of a remote EPSG:4326 byte raster via /vsicurl/.

    GFC tiles use 40000x1 scanline blocks, so a windowed read
    transfers only the rows inside the bbox (each row of a full tile
    is ~40 KB) — downloading whole 40000x40000 tiles is neither
    needed nor acceptable.

    :returns: ``(array, window_geotransform)`` or ``None`` when the
        bbox does not intersect the raster.
    """
    _ensure_gdal()
    ds = gdal.Open(url if url.startswith("/vsicurl/") else "/vsicurl/" + url,
                   gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Cannot open remote GFC layer: {url}")
    try:
        gt = ds.GetGeoTransform()
        nx, ny = ds.RasterXSize, ds.RasterYSize
        px = gt[1]
        if px <= 0 or gt[5] >= 0:
            raise RuntimeError(
                f"Unexpected GFC geotransform {gt} (expected north-up)")
        xmin, ymin, xmax, ymax = bbox
        x0 = max(0, int(math.floor((xmin - gt[0]) / px)))
        x1 = min(nx, int(math.ceil((xmax - gt[0]) / px)))
        y0 = max(0, int(math.floor((gt[3] - ymax) / -gt[5])))
        y1 = min(ny, int(math.ceil((gt[3] - ymin) / -gt[5])))
        if x1 <= x0 or y1 <= y0:
            return None
        arr = ds.GetRasterBand(1).ReadAsArray(x0, y0, x1 - x0, y1 - y0)
        win_gt = (gt[0] + x0 * gt[1], gt[1], gt[2],
                  gt[3] + y0 * gt[5], gt[4], gt[5])
        return arr, win_gt
    finally:
        ds = None


def _write_mem_gtiff(path: str, arr: np.ndarray,
                     gt: Tuple[float, ...], epsg: int = 4326) -> None:
    """Write a small in-memory GeoTIFF (``/vsimem/``)."""
    _ensure_gdal()
    from osgeo import osr

    ds = gdal.GetDriverByName("GTiff").Create(
        path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Byte)
    if ds is None:
        raise RuntimeError(f"Cannot create in-memory raster {path}")
    ds.SetGeoTransform(tuple(gt))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(arr)
    ds = None  # flush before any VRT/Warp touches the file


def _fetch_gfc_layer(
    bbox: Tuple[float, float, float, float],
    layer: str,
    tiles: Sequence[str],
    created: List[str],
) -> str:
    """Fetch the bbox windows of one GFC layer; return a readable path.

    Windows are materialised as small ``/vsimem/`` GeoTIFFs (paths are
    appended to ``created`` for later cleanup); multi-tile AOIs are
    mosaicked with a VRT.
    """
    pieces: List[str] = []
    try:
        for tile in tiles:
            win = _read_remote_window(gfc_layer_url(tile, layer), bbox)
            if win is None:
                continue
            arr, gt = win
            piece = f"/vsimem/gfc_{layer}_{tile}.tif"
            _write_mem_gtiff(piece, arr, gt)
            pieces.append(piece)
        if not pieces:
            raise RuntimeError(
                f"GFC bbox {tuple(round(v, 3) for v in bbox)} does not "
                f"intersect the {layer} tiles {list(tiles)}")
        if len(pieces) == 1:
            return pieces[0]
        vrt = f"/vsimem/gfc_{layer}.vrt"
        if gdal.BuildVRT(vrt, pieces) is None:
            raise RuntimeError(f"Cannot mosaic GFC {layer} pieces")
        created.append(vrt)
        return vrt
    except Exception:
        for p in pieces:
            try:
                gdal.GetDriverByName("GTiff").Delete(p)
            except Exception:
                pass
        raise
    finally:
        created.extend(pieces)


def _read_mem_array(path: str) -> Tuple[np.ndarray, Tuple[float, ...]]:
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Cannot read GFC intermediate raster {path}")
    try:
        arr = ds.GetRasterBand(1).ReadAsArray()
        return arr, ds.GetGeoTransform()
    finally:
        ds = None


def build_gfc_forest_mask(
    bbox: Tuple[float, float, float, float],
    ref_info: Dict,
    out_path: str,
    event_year: int,
    tau: int = GFC_DEFAULT_TAU,
    frac_threshold: float = GFC_DEFAULT_FRAC,
    variant: str = "candidate",
    majority_size: int = 0,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> str:
    """GFW GFC forest mask for the year preceding ``event_year``.

    :param variant: ``"candidate"`` (forest-candidates: event-year
        losses kept — the detection-area mask) or ``"background""
        (event-year losses removed — the background/statistics mask of
        the coherence-DiD, report ed.8 §9).
    :param majority_size: optional despeckle (default OFF to match the
        validated rescore exactly).
    :returns: ``out_path`` (byte 255 = forest on the reference grid).
    """
    report = (lambda f, m: progress_cb(f, m) if progress_cb else None)
    cancelled = (lambda: bool(cancel_cb()) if cancel_cb else False)
    if variant not in ("candidate", "background"):
        raise ValueError(
            f"Unknown GFC mask variant {variant!r} "
            "(use 'candidate' or 'background')")
    projection = ref_info.get("projection") or ""
    if not projection:
        raise ValueError("Reference grid has no projection")

    tiles = gfc_tiles_for_bbox(bbox)
    created: List[str] = []
    warp_path = out_path + ".warp_tmp.tif"
    try:
        report(5.0,
               f"GFC: downloading windows of {len(tiles)} tile(s) "
               f"for treecover2000")
        tc_src = _fetch_gfc_layer(bbox, "treecover2000", tiles, created)
        if cancelled():
            raise OperationCancelledError()
        report(20.0, "GFC: downloading lossyear")
        ly_src = _fetch_gfc_layer(bbox, "lossyear", tiles, created)
        if cancelled():
            raise OperationCancelledError()

        report(40.0, f"GFC: reconstructing forest@{event_year - 1}")
        tc, tc_gt = _read_mem_array(tc_src)
        ly, ly_gt = _read_mem_array(ly_src)
        if ly.shape != tc.shape or ly_gt != tc_gt:
            raise RuntimeError(
                "GFC treecover/lossyear windows are not aligned — "
                "unexpected tile mismatch")
        candidate, background = gfc_forest_parts(tc, ly, event_year, tau)
        chosen = candidate if variant == "candidate" else background
        del tc, ly, candidate, background

        bool_path = f"/vsimem/gfc_{variant}_bool.tif"
        created.append(bool_path)
        _write_mem_gtiff(bool_path, chosen.astype(np.uint8), tc_gt)
        if cancelled():
            raise OperationCancelledError()

        report(65.0, "GFC: averaging onto the radar grid")
        bounds = _grid_bounds(ref_info)
        gdal.Warp(
            warp_path,
            bool_path,
            format="GTiff",
            outputBounds=bounds,
            width=ref_info["width"],
            height=ref_info["height"],
            dstSRS=projection,
            resampleAlg="average",
            outputType=gdal.GDT_Float32,
            dstNodata=0,
            multithread=True,
        )
        frac, _ = _read_mem_array(warp_path)
        forest = (frac >= float(frac_threshold)).astype(np.uint8) * 255
        del frac
        if cancelled():
            raise OperationCancelledError()

        if majority_size and majority_size >= 3:
            report(80.0, "GFC: majority filter")
            forest = majority_filter_mask(forest, majority_size)
    finally:
        for p in created:
            try:
                gdal.GetDriverByName("GTiff").Delete(p)
            except Exception:
                pass
        try:
            gdal.GetDriverByName("GTiff").Delete(warp_path)
        except Exception:
            pass

    report(90.0, "GFC: writing output")
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
    report(100.0, "GFC: done")
    return out_path


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
    event_year: Optional[int] = None,
    tau: int = GFC_DEFAULT_TAU,
    frac_threshold: float = GFC_DEFAULT_FRAC,
    gfc_variant: str = "candidate",
) -> str:
    """Build (or pass through) a forest mask on the reference grid.

    :param source: ``"gfc"`` (v1.1 — Hansen GFC reconstructed for the
        year preceding ``event_year``; retrospective mask of record,
        requires ``bbox``), ``"worldcover"`` (auto-download from
        Planetary Computer; requires ``bbox``) or ``"file"`` (use
        ``mask_file`` as-is — the detector rasterises/warps it itself).
    :param event_year: storm year ``Y`` for the GFC source: the mask
        excludes losses of 2001..Y-1 (see :func:`gfc_forest_parts`);
        ``gfc_variant`` selects ``"candidate"`` or ``"background"``.
    :returns: path of the forest mask to pass to the detector.  For
        ``source="file"`` this is simply ``mask_file``.
    """
    if source in ("gfc", "gfw", "hansen"):
        if bbox is None:
            raise ValueError("bbox is required for the GFC source")
        if event_year is None:
            raise ValueError(
                "event_year is required for the GFC source — pass the "
                "storm year Y (the mask reconstructs forest@Y-1)")
        return build_gfc_forest_mask(
            bbox, ref_info, out_path, event_year=event_year, tau=tau,
            frac_threshold=frac_threshold, variant=gfc_variant,
            majority_size=majority_size, progress_cb=progress_cb,
            cancel_cb=cancel_cb)
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
        f"Unknown forest mask source: {source!r} "
        "(use 'gfc', 'worldcover' or 'file')")
