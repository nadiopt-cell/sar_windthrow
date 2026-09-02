"""Unit tests for the windthrow detection module.

Array-level tests (index, thresholds, object filtering, polarisation
pairing) run on synthetic data with NumPy + SciPy only.  File-level
tests exercise the full GDAL chain on small synthetic GeoTIFFs and skip
automatically when the ``osgeo`` package is unavailable.

Sign convention reminder (Rüetschi et al. 2019): windthrown forest
shows a backscatter INCREASE, so WI = dVV + dVH is POSITIVE over
windthrow.
"""

import os

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources.windthrow import (
    WindthrowDetector,
    adaptive_threshold,
    background_offset_db,
    common_polarizations,
    compute_wi,
    extract_polarization,
    filter_small_objects,
    mask_from_threshold,
    median_filter_nan,
    pair_by_polarization,
    to_db_domain_chunk,
)


# ======================================================================
# Polarisation pairing
# ======================================================================
def test_extract_polarization_various_naming():
    assert extract_polarization("S1A_IW_GRDH_1SDV_20200101T164919__vv.tif") == "VV"
    assert extract_polarization("s1a-iw-grd-vv-20200101t164919-20200101t164944-031212-0397fa-001.tiff") == "VV"
    assert extract_polarization("scene1_vh_db.tif") == "VH"
    assert extract_polarization("scene1_HH.tif") == "HH"
    assert extract_polarization("vvpanel.tif") == ""  # token not delimited
    assert extract_polarization("dem.tif") == ""


def test_pair_by_polarization_groups_files():
    files = ["a_vv.tif", "b_vh.tif", "c_vv.tif", "no_pol.tif"]
    groups = pair_by_polarization(files)
    assert set(groups.keys()) == {"VV", "VH", ""}
    assert groups["VV"] == ["a_vv.tif", "c_vv.tif"]
    assert groups["VH"] == ["b_vh.tif"]
    assert groups[""] == ["no_pol.tif"]


def test_common_polarizations_prefers_vv_vh_order():
    pre = ["p_vv.tif", "p_vh.tif"]
    post = ["q_vh.tif", "q_vv.tif"]
    assert common_polarizations(pre, post) == ["VV", "VH"]
    assert common_polarizations(["p_vh.tif"], ["q_vh.tif"]) == ["VH"]
    assert common_polarizations(["p_vh.tif"], ["q_vv.tif"]) == []
    # Unknown tokens are still kept (sorted after the known ones)
    assert common_polarizations(["p_hh.tif"], ["q_hh.tif"]) == ["HH"]


# ======================================================================
# dB conversion and WI arithmetic
# ======================================================================
def test_to_db_domain_chunk_linear_and_db():
    db = np.full((8, 8), -12.0, dtype=np.float32)
    out = to_db_domain_chunk(db)
    np.testing.assert_allclose(out, db, rtol=1e-5)

    amp = np.full((8, 8), 100.0, dtype=np.float32)  # linear amplitude
    out = to_db_domain_chunk(amp)
    np.testing.assert_allclose(out, 20.0, rtol=1e-4)

    # Raw DN zeros and no-data sentinels become NaN
    dn = np.array([[0, 1], [2, -32768]], dtype=np.float32)
    out = to_db_domain_chunk(dn)
    assert np.isnan(out[0, 0])
    assert np.isnan(out[1, 1])
    assert out[0, 1] == pytest.approx(0.0, abs=1e-5)
    assert out[1, 0] == pytest.approx(10.0 * np.log10(2.0), rel=1e-5)


def test_compute_wi_sum_and_single_pol():
    d_vv = np.full((4, 4), 1.0, dtype=np.float32)
    d_vh = np.full((4, 4), 2.0, dtype=np.float32)
    np.testing.assert_allclose(compute_wi(d_vv, d_vh), 3.0)
    # NaN in either channel propagates
    d_vv_nan = d_vv.copy()
    d_vv_nan[0, 0] = np.nan
    assert np.isnan(compute_wi(d_vv_nan, d_vh)[0, 0])
    # Single-polarisation fallback
    np.testing.assert_allclose(compute_wi(None, d_vh), 2.0)
    np.testing.assert_allclose(compute_wi(d_vv, None), 1.0)
    with pytest.raises(ValueError):
        compute_wi(None, None)


def test_windthrow_is_positive_change():
    """The core physics: windthrow INCREASES backscatter -> WI > 0."""
    pre_vv = np.full((16, 16), -12.0, dtype=np.float32)
    post_vv = np.full((16, 16), -9.5, dtype=np.float32)   # +2.5 dB
    pre_vh = np.full((16, 16), -18.0, dtype=np.float32)
    post_vh = np.full((16, 16), -15.5, dtype=np.float32)  # +2.5 dB
    wi = compute_wi(post_vv - pre_vv, post_vh - pre_vh)
    assert float(wi.mean()) == pytest.approx(5.0, abs=1e-4)
    assert mask_from_threshold(wi, adaptive_threshold(0.0, 2.9)).all()


# ======================================================================
# Thresholding and object filtering
# ======================================================================
def test_adaptive_threshold_and_mask():
    assert adaptive_threshold(0.3, 2.9) == pytest.approx(3.2)
    wi = np.array([[1.0, 5.0], [np.nan, 3.4]], dtype=np.float32)
    m = mask_from_threshold(wi, 3.2)
    assert m[0, 0] is np.False_ or m[0, 0] == False  # noqa: E712
    assert m[0, 1] == True  # noqa: E712
    assert m[1, 1] == True  # noqa: E712
    assert not m[1, 0]  # NaN is never flagged


def test_filter_small_objects_removes_speckle():
    mask = np.zeros((32, 32), dtype=bool)
    # Large object: 5x5 block
    mask[4:9, 4:9] = True
    # Small object: 2 pixels
    mask[20, 20] = True
    mask[20, 21] = True
    kept = filter_small_objects(mask, min_pixels=10)
    assert kept[6, 6]
    assert not kept[20, 20]
    # min_pixels=1 keeps everything
    assert filter_small_objects(mask, min_pixels=1).sum() == mask.sum()


def test_median_filter_nan_tolerates_invalid():
    from scipy.ndimage import median_filter  # noqa: F401  (scipy present)
    wi = np.full((9, 9), 2.0, dtype=np.float32)
    wi[4, 4] = 50.0          # isolated spike
    wi[0, 0] = np.nan        # invalid pixel
    out = median_filter_nan(wi, size=3)
    assert out[4, 4] == pytest.approx(2.0, abs=1e-5)  # spike suppressed
    assert np.isnan(out[0, 0])                        # NaN position kept
    assert out[5, 5] == pytest.approx(2.0, abs=1e-5)


# ======================================================================
# File-level pipeline (GDAL required)
# ======================================================================
def _write_tiff(path, arr, gt=None, proj=None, nodata=None, dtype=None):
    from osgeo import gdal, osr
    driver = gdal.GetDriverByName("GTiff")
    h, w = arr.shape
    dt = dtype or (gdal.GDT_Float32 if np.issubdtype(arr.dtype, np.floating)
                   else gdal.GDT_Byte)
    ds = driver.Create(path, w, h, 1, dt)
    if gt is None:
        # 10 m pixels, UTM-like origin
        gt = (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0)
    ds.SetGeoTransform(gt)
    if proj is None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(32633)
        proj = srs.ExportToWkt()
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.FlushCache()
    ds = None
    return path


@pytest.fixture()
def synthetic_scene(tmp_path):
    """A pair of pre/post VV+VH scenes with a windthrow blob in the post.

    Skips all GDAL-dependent tests when osgeo is unavailable (e.g. a
    plain CI runner); inside QGIS the bindings are always present.
    """
    pytest.importorskip("osgeo.gdal")
    pytest.importorskip("osgeo.ogr")
    rng = np.random.default_rng(42)
    size = 120
    pre_vv = np.full((size, size), -12.0, dtype=np.float32)
    pre_vh = np.full((size, size), -18.0, dtype=np.float32)
    post_vv = pre_vv + rng.normal(0.0, 0.4, (size, size)).astype(np.float32)
    post_vh = pre_vh + rng.normal(0.0, 0.4, (size, size)).astype(np.float32)
    # Windthrow: strong backscatter increase inside a circular blob
    yy, xx = np.ogrid[:size, :size]
    blob = (yy - 60) ** 2 + (xx - 60) ** 2 <= 10 ** 2
    post_vv[blob] += 4.0
    post_vh[blob] += 4.0

    d = str(tmp_path)
    paths = {
        "pre_vv": _write_tiff(os.path.join(d, "pre_vv.tif"), pre_vv),
        "pre_vh": _write_tiff(os.path.join(d, "pre_vh.tif"), pre_vh),
        "post_vv": _write_tiff(os.path.join(d, "post_vv.tif"), post_vv),
        "post_vh": _write_tiff(os.path.join(d, "post_vh.tif"), post_vh),
    }
    return paths, blob


def test_detect_file_full_pipeline(synthetic_scene, tmp_path):
    from osgeo import gdal, ogr
    paths, blob = synthetic_scene
    out_base = str(tmp_path / "run1" / "test_event")
    det = WindthrowDetector(
        threshold_mode="adaptive", a_db=2.9, min_pixels=27,
        median_filter_size=3,
    )
    progress_seen = []

    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=out_base,
        progress_cb=lambda f, m: progress_seen.append((f, m)),
        cancel_cb=None,
    )

    # Outputs exist
    for key in ("wi", "mask", "vector"):
        assert os.path.isfile(result[key]), result[key]
    # No composites were needed (single file per polarisation)
    assert result["composites"] == []
    # The blob is detected
    assert result["n_objects"] >= 1
    assert result["threshold_db"] == pytest.approx(det.mean_wi + 2.9)

    # Mask overlaps the synthetic blob
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert (mask[blob] == 255).mean() > 0.5   # most of the blob flagged
    assert (mask[~blob] > 0).sum() < 0.05 * mask.size  # few false alarms

    # WI raster: positive inside the blob
    ds = gdal.Open(result["wi"], gdal.GA_ReadOnly)
    wi = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert wi[blob].mean() > 6.0
    assert np.isfinite(wi[~blob]).all()

    # Vector: features with positive area
    vds = ogr.Open(result["vector"])
    layer = vds.GetLayer(0)
    areas = [f.GetField("area_ha") for f in layer]
    vds = None
    assert len(areas) >= 1
    assert all(a > 0 for a in areas)
    # Blob area is ~pi*10^2 px * 100 m^2 = ~3.14 ha
    assert max(areas) == pytest.approx(3.14, rel=0.35)


def test_detect_file_multi_date_composite(synthetic_scene, tmp_path):
    """Several pre-storm dates are combined into a median composite."""
    from osgeo import gdal
    paths, blob = synthetic_scene
    d = str(tmp_path)
    # A second pre-storm date with a +2 dB offset (should be medianed out
    # against the first date where both are available).
    pre2 = gdal.Open(paths["pre_vv"], gdal.GA_ReadOnly)
    arr = pre2.GetRasterBand(1).ReadAsArray() + 2.0
    pre2 = None
    pre2_path = _write_tiff(os.path.join(d, "pre2_vv.tif"), arr)
    out_base = str(tmp_path / "run2" / "multi")
    det = WindthrowDetector(min_pixels=27, median_filter_size=3)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], pre2_path, paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=out_base,
    )
    assert any("_pre_VV.tif" in p for p in result["composites"])
    assert result["n_objects"] >= 1


def test_detect_file_no_common_polarization(synthetic_scene, tmp_path):
    paths, _ = synthetic_scene
    det = WindthrowDetector()
    with pytest.raises(ValueError, match="common polarisation"):
        det.detect_file(
            pre_paths=[paths["pre_vv"]],
            post_paths=[paths["post_vh"]],
            output_base=str(tmp_path / "x" / "out"),
        )


def test_detect_file_with_vector_mask(synthetic_scene, tmp_path):
    """A vector AOI mask restricting detection to a sub-area."""
    from osgeo import gdal, ogr, osr
    paths, blob = synthetic_scene
    d = str(tmp_path)
    # Vector mask covering the left half only (the blob sits at x=60).
    drv = ogr.GetDriverByName("GPKG")
    gpkg = os.path.join(d, "aoi.gpkg")
    if os.path.exists(gpkg):
        drv.DeleteDataSource(gpkg)
    vds = drv.CreateDataSource(gpkg)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32633)
    layer = vds.CreateLayer("aoi", srs, ogr.wkbPolygon)
    feat = ogr.Feature(layer.GetLayerDefn())
    # Grid origin: (500000, 5000000), 10 m px, 120 px wide.
    wkt = ("POLYGON ((500000 5000000, 500600 5000000, "
           "500600 4999400, 500000 4999400, 500000 5000000))")
    feat.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
    layer.CreateFeature(feat)
    vds = None

    det = WindthrowDetector(min_pixels=5, median_filter_size=3)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=str(tmp_path / "run3" / "masked"),
        analysis_mask_path=gpkg,
    )
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    # Blob centre (col 60 = x 500600) is OUTSIDE the mask (x < 500600).
    assert mask[60, 60] == 0


# ======================================================================
# v0.8 — background normalization
# ======================================================================
def test_background_offset_db_median_shift():
    """The offset equals the median post-pre difference, NaN-tolerant."""
    pre = np.full((32, 32), -12.0, dtype=np.float32)
    post = pre + 1.5
    post[0, 0] = np.nan                       # invalid pixel ignored
    assert background_offset_db(pre, post) == pytest.approx(1.5, abs=1e-5)
    # Fully invalid sample -> 0.0
    empty_pre = np.full((4, 4), np.nan, dtype=np.float32)
    empty_post = np.full((4, 4), np.nan, dtype=np.float32)
    assert background_offset_db(empty_pre, empty_post) == 0.0
    # Asymmetric shapes rejected
    with pytest.raises(ValueError):
        background_offset_db(np.zeros((2, 2), np.float32),
                             np.zeros((3, 3), np.float32))


@pytest.fixture()
def shifted_scene(synthetic_scene):
    """synthetic_scene with a uniform +1.5 dB weather shift on post."""
    from osgeo import gdal
    paths, blob = synthetic_scene
    d = os.path.dirname(paths["post_vv"])
    out = {}
    for pol in ("vv", "vh"):
        src = paths[f"post_{pol}"]
        ds = gdal.Open(src, gdal.GA_ReadOnly)
        arr = ds.GetRasterBand(1).ReadAsArray() + 1.5
        ds = None
        out[pol] = _write_tiff(
            os.path.join(d, f"post_wet_{pol}.tif"), arr)
    paths_wet = {
        "pre_vv": paths["pre_vv"], "pre_vh": paths["pre_vh"],
        "post_vv": out["vv"], "post_vh": out["vh"],
    }
    return paths_wet, blob


def test_normalize_background_removes_shift_and_detects_blob(
        shifted_scene, tmp_path):
    """+1.5 dB weather shift is removed; the blob stays detectable."""
    from osgeo import gdal
    paths, blob = shifted_scene
    det = WindthrowDetector(
        min_pixels=27, median_filter_size=3, normalize_background=True)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=str(tmp_path / "norm" / "run"),
    )
    # Diagnostics: per-polarisation offsets recovered the wet shift
    assert result["offset_db"]["VV"] == pytest.approx(1.5, abs=0.1)
    assert result["offset_db"]["VH"] == pytest.approx(1.5, abs=0.1)
    assert det.offset_db["VV"] == pytest.approx(1.5, abs=0.1)
    # Normalised background WI is centred on ~0 dB
    assert abs(float(result["mean_wi"])) < 0.5
    # Output file naming
    assert result["wi"].endswith("_wi_norm.tif")
    assert os.path.isfile(result["wi"])
    # The windthrow blob survives normalization (+8 dB over background)
    assert result["n_objects"] >= 1
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert (mask[blob] == 255).mean() > 0.5


def test_normalize_background_false_matches_v070(shifted_scene, tmp_path):
    """norm=False reproduces v0.7.0 behaviour: _wi.tif, no offsets."""
    from osgeo import gdal
    paths, blob = shifted_scene
    det = WindthrowDetector(
        min_pixels=27, median_filter_size=3, normalize_background=False)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=str(tmp_path / "raw" / "run"),
    )
    assert result["wi"].endswith("_wi.tif")
    assert os.path.isfile(result["wi"])
    assert not os.path.isfile(
        str(tmp_path / "raw" / "run_wi_norm.tif"))
    assert result["offset_db"] == {}
    assert result["normalize_background"] is False
    # The un-normalised mean WI carries the full weather shift
    # (+1.5 dB per polarisation -> ~+3 dB on the two-pol WI).
    assert float(result["mean_wi"]) == pytest.approx(3.0, abs=0.5)
    assert result["threshold_db"] == pytest.approx(
        float(result["mean_wi"]) + 2.9)


def test_background_mask_limits_stats_and_keeps_detection(
        shifted_scene, tmp_path):
    """A background sample outside the blob drives offsets and mean WI.

    The background mask covers the left quarter only (no blob pixels),
    while detection runs over the whole scene — the blob is still found
    and the statistics are not dragged up by the windthrow signal.
    """
    from osgeo import gdal, osr
    paths, blob = shifted_scene
    d = str(tmp_path)
    # Raster background mask: left quarter of the 120 px grid.
    bg = np.zeros((120, 120), dtype=np.uint8)
    bg[:, :30] = 1
    bg_path = _write_tiff(os.path.join(d, "bg_mask.tif"), bg)
    det = WindthrowDetector(
        min_pixels=27, median_filter_size=3, normalize_background=True)
    result = det.detect_file(
        pre_paths=[paths["pre_vv"], paths["pre_vh"]],
        post_paths=[paths["post_vv"], paths["post_vh"]],
        output_base=str(tmp_path / "bgm" / "run"),
        background_mask_path=bg_path,
    )
    # Offsets come from pure background: the wet shift, no blob bias
    assert result["offset_db"]["VV"] == pytest.approx(1.5, abs=0.1)
    # Background mean WI is centred on ~0 (blob excluded from sample)
    assert abs(float(result["mean_wi"])) < 0.3
    # Detection is NOT restricted by the background mask: blob found
    assert result["n_objects"] >= 1
    ds = gdal.Open(result["mask"], gdal.GA_ReadOnly)
    mask = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    assert (mask[blob] == 255).mean() > 0.5
