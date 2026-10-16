"""Shared GDAL raster/vector helpers (open, compare grids, warp, masks).

Moved verbatim from ``windthrow.py`` (and ``_same_grid_safe`` from
``coh_delta.py``) in the v1.3.0 refactor: both detectors and
``forest_mask.py`` used copies or cross-imports of these private
helpers.  This module must NOT import any detector — it sits at the
bottom of the dependency graph (constants -> raster_utils -> detectors).

Conventions:

* no-data of float32 index rasters is ``INDEX_NODATA`` (-9999.0; the
  historical spellings ``WI_NODATA`` / ``DCOH_NODATA`` share the value);
* chunked loops read/write ``_CHUNK_ROWS`` (512) rows per step, which
  bounds RAM to a few MB per working copy on full Sentinel-1 IW frames;
* mask rasters are byte 0/255 with 255 = keep (inside);
* ``osgeo`` import is optional at module level (unit-testable without a
  GDAL runtime); every entry point calls :func:`_ensure_gdal`.
"""

from __future__ import annotations

import os
from typing import Dict, Sequence, Tuple

import numpy as np

try:
    from osgeo import gdal, ogr, osr  # always present in the QGIS Python env
    gdal.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None  # type: ignore
    ogr = None  # type: ignore
    osr = None  # type: ignore

from .constants import _CHUNK_ROWS, INDEX_NODATA

__all__ = [
    "_ensure_gdal",
    "_read_raster_info",
    "_same_grid",
    "_same_grid_safe",
    "ensure_aligned",
    "rasterize_vector_mask",
    "_pixel_area_m2",
    "_resolve_mask_raster",
    "_intersect_masks",
    "_create_output_vector",
]


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


def _same_grid_safe(path: str, ref_info: dict) -> bool:
    """Grid comparison that tolerates a missing/unsupported reference."""
    try:
        return _read_raster_info(path)["geotransform"] == ref_info["geotransform"] \
            and _read_raster_info(path)["width"] == ref_info["width"] \
            and _read_raster_info(path)["height"] == ref_info["height"]
    except Exception:
        return False


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
        dstNodata=INDEX_NODATA,
        resampleAlg="bilinear",
        multithread=True,
    )
    return out_path


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
