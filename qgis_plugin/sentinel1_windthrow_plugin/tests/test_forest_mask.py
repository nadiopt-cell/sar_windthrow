"""Unit tests for the forest-mask providers (v0.9).

Array-level tests (class binarisation, majority filter) run on pure
NumPy.  Raster-level tests exercise the GDAL warp onto the radar
reference grid on small synthetic GeoTIFFs.  STAC interactions are
tested against stub clients — no network is touched.

Detector-integration tests verify that ``forest_mask_path`` restricts
the detections and the adaptive-threshold statistics to forest.
"""

import os

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources import forest_mask
from sentinel1_windthrow_plugin.sources.forest_mask import (
    bbox_4326,
    build_forest_mask,
    build_forest_mask_from_rasters,
    classify_forest,
    fetch_worldcover_hrefs,
    majority_filter_mask,
    read_ref_info,
)
from sentinel1_windthrow_plugin.sources.windthrow import WindthrowDetector


# ======================================================================
# Array-level primitives
# ======================================================================
def test_classify_forest_binarises_tree_class():
    arr = np.array([[10, 20], [30, 10]], dtype=np.uint8)
    out = classify_forest(arr)
    np.testing.assert_array_equal(out, np.array([[255, 0], [0, 255]]))
    # Custom class set (e.g. Tree cover + Mangroves)
    out2 = classify_forest(arr, forest_classes=(10, 30))
    np.testing.assert_array_equal(out2, np.array([[255, 0], [255, 255]]))


def test_majority_filter_removes_isolated_and_keeps_blocks():
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[4:10, 4:10] = 255          # solid block survives
    mask[12, 12] = 255              # isolated pixel removed
    out = majority_filter_mask(mask, size=3)
    assert out[6, 6] == 255
    assert out[12, 12] == 0
    assert out[2, 2] == 0


def test_majority_filter_noop_and_fallback_match():
    mask = np.zeros((8, 8), dtype=np.uint8)
    # Sizes < 3 and even sizes are returned unchanged
    assert majority_filter_mask(mask, size=1) is mask
    assert majority_filter_mask(mask, size=2) is mask
    # The NumPy fallback matches the scipy filter on the interior
    # (edge handling differs: scipy uses 'nearest', fallback wraps).
    rng = np.random.default_rng(7)
    noisy = (rng.random((24, 24)) > 0.5).astype(np.uint8) * 255
    assert forest_mask._scipy_median_filter is not None
    scipy_out = majority_filter_mask(noisy, size=3)
    original = forest_mask._scipy_median_filter
    forest_mask._scipy_median_filter = None
    try:
        fallback_out = majority_filter_mask(noisy, size=3)
    finally:
        forest_mask._scipy_median_filter = original
    np.testing.assert_array_equal(
        scipy_out[1:-1, 1:-1], fallback_out[1:-1, 1:-1])


# ======================================================================
# Reference-grid helpers (GDAL required)
# ======================================================================
@pytest.fixture()
def ref_grid(tmp_path):
    """120x120 @ 10 m EPSG:32633 grid at (500000, 5000000)."""
    pytest.importorskip("osgeo.gdal")
    from osgeo import gdal, osr
    path = str(tmp_path / "ref_grid.tif")
    ds = gdal.GetDriverByName("GTiff").Create(path, 120, 120, 1,
                                              gdal.GDT_Float32)
    ds.SetGeoTransform((500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32633)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).Fill(-12.0)
    ds = None
    return read_ref_info(path)


def test_bbox_4326_centre_of_utm_zone(ref_grid):
    lon0, lat0, lon1, lat1 = bbox_4326(ref_grid)
    # (500000, 5000000) in EPSG:32633 is ~15.0 E, ~45.2 N
    assert 14.8 < lon0 <= lon1 < 15.2
    assert 45.0 < lat0 <= lat1 < 45.4


# ======================================================================
# Raster-level builder
# ======================================================================
def _write_class_raster(path, arr, gt, epsg=32633):
    from osgeo import gdal, osr
    ds = gdal.GetDriverByName("GTiff").Create(
        path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Byte)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(arr)
    ds.GetRasterBand(1).SetNoDataValue(0)
    ds = None
    return path


def test_build_forest_mask_from_rasters_warps_and_binarises(ref_grid,
                                                            tmp_path):
    """A coarse 50 m class raster is warped to the 10 m radar grid."""
    # 36x24 @ 50 m covering x 499500..501300, y 5000500..4999300:
    # rows 10..23 map to y 5000000..4999300 = Tree cover (10).
    gt_src = (499500.0, 50.0, 0.0, 5000500.0, 0.0, -50.0)
    src = np.full((24, 36), 30, dtype=np.uint8)     # grassland
    src[10:24, :] = 10                              # tree cover band
    src_path = _write_class_raster(
        str(tmp_path / "wc_tile.tif"), src, gt_src)

    out = build_forest_mask_from_rasters(
        [src_path], ref_grid, str(tmp_path / "forest.tif"),
        majority_size=0)
    assert os.path.isfile(out)

    from osgeo import gdal
    ds = gdal.Open(out, gdal.GA_ReadOnly)
    forest = ds.GetRasterBand(1).ReadAsArray()
    assert forest.shape == (120, 120)
    assert int(forest.max()) == 255
    # Radar rows 0..69 correspond to y 5000000..4999300 (inside the
    # tree band); the boundary row 70 rounds past the last source row
    # (GDAL nearest: src row 23.9 -> 24 = out of range -> nodata 0),
    # and rows 75..119 fall outside the source tile -> 0 as well
    assert (forest[:70, :] == 255).all()
    assert (forest[71:, :] == 0).all()
    # Grid metadata copied from the reference
    info = read_ref_info(out)
    assert info["width"] == 120 and info["height"] == 120


def test_build_forest_mask_from_rasters_majority_cleans_edges(
        ref_grid, tmp_path):
    """majority_size=3 removes isolated non-forest speckle."""
    gt_src = (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0)
    src = np.full((120, 120), 10, dtype=np.uint8)
    src[60, 60] = 30                                 # single grass pixel
    src_path = _write_class_raster(
        str(tmp_path / "wc_tile2.tif"), src, gt_src)
    out = build_forest_mask_from_rasters(
        [src_path], ref_grid, str(tmp_path / "forest_clean.tif"),
        majority_size=3)
    from osgeo import gdal
    ds = gdal.Open(out, gdal.GA_ReadOnly)
    forest = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert forest[60, 60] == 255                     # speckle filled in
    assert (forest == 255).all()


# ======================================================================
# STAC interaction (stub client, no network)
# ======================================================================
class _StubClient:
    def __init__(self, items):
        self._items = items
        self.signed = []

    def search_items(self, collection, bbox, datetime_range=None,
                     limit=500, extra_query=None):
        self.collection = collection
        self.bbox = bbox
        self.datetime_range = datetime_range
        return self._items

    def sign_href(self, href):
        self.signed.append(href)
        return href + "?sig=stub"


def test_fetch_worldcover_hrefs_filters_year_and_signs():
    items = [
        {"id": "esa_worldcover_2020_n55_e057",
         "assets": {"map": {"href": "https://pc/map2020.tif"}}},
        {"id": "esa_worldcover_2021_n55_e057",
         "assets": {"map": {"href": "https://pc/map2021.tif"}}},
        {"id": "esa_worldcover_2020_n55_e060",
         "assets": {"other": {"href": "https://pc/nomap.tif"}}},
    ]
    client = _StubClient(items)
    hrefs = fetch_worldcover_hrefs((57.0, 55.0, 58.0, 56.0),
                                   year=2020, client=client)
    assert client.collection == forest_mask.WC_COLLECTION
    assert hrefs == ["https://pc/map2020.tif?sig=stub"]


def test_fetch_worldcover_hrefs_no_items_raises():
    client = _StubClient([])
    with pytest.raises(RuntimeError, match="No ESA WorldCover"):
        fetch_worldcover_hrefs((0.0, 0.0, 1.0, 1.0), year=2020,
                               client=client)


def test_build_worldcover_mask_prefixes_vsicurl(ref_grid, tmp_path,
                                                monkeypatch):
    """Signed URLs are wrapped in /vsicurl/ for windowed COG reads."""
    captured = {}

    def _fake_builder(paths, ref_info, out_path, **kwargs):
        captured["paths"] = list(paths)
        captured["out_path"] = out_path
        return out_path

    monkeypatch.setattr(forest_mask, "build_forest_mask_from_rasters",
                        _fake_builder)
    client = _StubClient([
        {"id": "esa_worldcover_2020_n55_e057",
         "assets": {"map": {"href": "https://pc/map.tif"}}}])
    out = forest_mask.build_worldcover_forest_mask(
        (57.0, 55.0, 58.0, 56.0), ref_grid,
        str(tmp_path / "forest.tif"), year=2020, client=client)
    assert out.endswith("forest.tif")
    assert captured["paths"] == ["/vsicurl/https://pc/map.tif?sig=stub"]


def test_build_forest_mask_dispatcher(tmp_path, ref_grid):
    # "file" source passes the path through
    src = np.full((8, 8), 10, dtype=np.uint8)
    mask_file = _write_class_raster(
        str(tmp_path / "own_forest.tif"), src,
        (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0))
    assert build_forest_mask(
        "file", ref_grid, str(tmp_path / "x.tif"),
        mask_file=mask_file) == mask_file
    with pytest.raises(ValueError, match="not found"):
        build_forest_mask("file", ref_grid, str(tmp_path / "x.tif"),
                          mask_file=str(tmp_path / "missing.tif"))
    with pytest.raises(ValueError, match="Unknown forest mask source"):
        build_forest_mask("globe30", ref_grid, str(tmp_path / "x.tif"))


# ======================================================================
# Detector integration — forest mask restricts detections & statistics
# ======================================================================
def _write_float_tiff(path, arr):
    from osgeo import gdal, osr
    ds = gdal.GetDriverByName("GTiff").Create(
        path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32633)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(arr)
    ds = None
    return path


@pytest.fixture()
def two_blob_scene(tmp_path):
    """Pre/post VV+VH with windthrow blobs inside and outside forest.

    Grid: 120x120 @ 10 m EPSG:32633.  Blob A centred at (row 60, col 30)
    — designed to sit OUTSIDE the forest (left half); blob B at
    (row 60, col 90) — INSIDE the forest (right half).
    """
    pytest.importorskip("osgeo.gdal")
    rng = np.random.default_rng(11)
    size = 120
    pre_vv = np.full((size, size), -12.0, dtype=np.float32)
    pre_vh = np.full((size, size), -18.0, dtype=np.float32)
    post_vv = pre_vv + rng.normal(0.0, 0.4, (size, size)).astype(np.float32)
    post_vh = pre_vh + rng.normal(0.0, 0.4, (size, size)).astype(np.float32)
    yy, xx = np.ogrid[:size, :size]
    blob_a = (yy - 60) ** 2 + (xx - 30) ** 2 <= 8 ** 2
    blob_b = (yy - 60) ** 2 + (xx - 90) ** 2 <= 8 ** 2
    for blob in (blob_a, blob_b):
        post_vv[blob] += 4.0
        post_vh[blob] += 4.0
    d = str(tmp_path)
    paths = {
        "pre_vv": _write_float_tiff(os.path.join(d, "pre_vv.tif"), pre_vv),
        "pre_vh": _write_float_tiff(os.path.join(d, "pre_vh.tif"), pre_vh),
        "post_vv": _write_float_tiff(os.path.join(d, "post_vv.tif"), post_vv),
        "post_vh": _write_float_tiff(os.path.join(d, "post_vh.tif"), post_vh),
    }
    return paths, blob_a, blob_b


def test_detect_file_forest_mask_restricts_detections(
        two_blob_scene, tmp_path):
    """Only the blob inside the forest mask is detected."""
    from osgeo import gdal
    paths, blob_a, blob_b = two_blob_scene
    # Forest = right half (cols 60..119): blob B inside, blob A outside.
    forest = np.zeros((120, 120), dtype=np.uint8)
    forest[:, 60:] = 255
    forest_path = _write_class_raster(
        str(tmp_path / "forest_right.tif"), forest,
        (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0))

    det = WindthrowDetector(min_pixels=27, median_filter_size=3)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=str(tmp_path / "fm" / "run"),
        forest_mask_path=forest_path,
    )
    assert result["forest_mask"] and os.path.isfile(result["forest_mask"])
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    # Blob B (forest) flagged; blob A (field) not flagged
    assert (mask[blob_b] == 255).mean() > 0.5
    assert (mask[blob_a] == 255).mean() < 0.05


def test_detect_file_forest_mask_intersects_analysis_mask(
        two_blob_scene, tmp_path):
    """forest ∩ analysis: blob in the intersection survives, else not."""
    from osgeo import gdal
    paths, blob_a, blob_b = two_blob_scene
    # Analysis = left half (cols 0..59), forest = cols 30..119:
    # intersection = cols 30..59 -> blob A (col 30) inside, B outside.
    analysis = np.zeros((120, 120), dtype=np.uint8)
    analysis[:, :60] = 255
    analysis_path = _write_class_raster(
        str(tmp_path / "aoi.tif"), analysis,
        (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0))
    forest = np.zeros((120, 120), dtype=np.uint8)
    forest[:, 30:] = 255
    forest_path = _write_class_raster(
        str(tmp_path / "forest_right.tif"), forest,
        (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0))

    det = WindthrowDetector(min_pixels=27, median_filter_size=3)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=str(tmp_path / "ix" / "run"),
        analysis_mask_path=analysis_path,
        forest_mask_path=forest_path,
    )
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert (mask[blob_a] == 255).mean() > 0.2
    assert (mask[blob_b] == 255).mean() < 0.05


def test_detect_file_forest_mask_drives_statistics(two_blob_scene,
                                                   tmp_path):
    """Adaptive stats and offsets come from the forest sample.

    Post carries a +1.5 dB/polarisation weather shift; the forest mask
    excludes blob B, so the offsets/mean computed over the forest are
    clean and the out-of-forest blob produces no detections.
    """
    from osgeo import gdal
    paths, blob_a, blob_b = two_blob_scene
    d = os.path.dirname(paths["post_vv"])
    wet = {}
    for pol, level in (("vv", 1.5), ("vh", 1.5)):
        ds = gdal.Open(paths[f"post_{pol}"], gdal.GA_ReadOnly)
        arr = ds.GetRasterBand(1).ReadAsArray() + level
        ds = None
        wet[pol] = _write_float_tiff(
            os.path.join(d, f"post_wet_{pol}.tif"), arr.astype(np.float32))

    # Forest = left half only (no blob pixels inside): blob A sits at
    # col 30 — inside the forest!  Use a left-half mask WITHOUT the blob:
    # cols 0..20 (blob A starts at col 22).
    forest = np.zeros((120, 120), dtype=np.uint8)
    forest[:, :20] = 255
    forest_path = _write_class_raster(
        str(tmp_path / "forest_left.tif"), forest,
        (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0))

    det = WindthrowDetector(min_pixels=27, median_filter_size=3,
                            normalize_background=True)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[wet["vv"], wet["vh"]],
        output_base=str(tmp_path / "stats" / "run"),
        forest_mask_path=forest_path,
    )
    # Offsets recovered over the pure-forest sample
    assert result["offset_db"]["VV"] == pytest.approx(1.5, abs=0.2)
    assert result["offset_db"]["VH"] == pytest.approx(1.5, abs=0.2)
    # Mean WI over the forest sample is centred on ~0
    assert abs(float(result["mean_wi"])) < 0.5
    # Both blobs lie outside the forest sample -> nothing detected
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert mask.max() == 0


def test_detect_file_without_forest_mask_unchanged(two_blob_scene,
                                                   tmp_path):
    """No forest mask given -> detections over the whole scene."""
    from osgeo import gdal
    paths, blob_a, blob_b = two_blob_scene
    det = WindthrowDetector(min_pixels=27, median_filter_size=3)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=str(tmp_path / "nofm" / "run"),
    )
    assert result["forest_mask"] is None
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert (mask[blob_a] == 255).mean() > 0.5
    assert (mask[blob_b] == 255).mean() > 0.5
