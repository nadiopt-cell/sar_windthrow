"""Unit tests for the coherence DiD module (v1.0, port of step12b).

Sign convention: dcoh = coh_control - coh_prepost is POSITIVE over
windthrow (the damage-window pair decorrelates while the control pair
stays coherent).

v1.2: burst InSAR (ISCE) product parsing and DiD validation, and the
water-mask convention fix — HyP3 masks encode 1 = land, 0 = water
(ASF product guides), so the old tests that assumed 1 = water were
rewritten.
"""

import os
import zipfile

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources.coh_delta import (
    DCOH_NODATA,
    CoherenceDeltaDetector,
    _validate_did_pair,
    coherence_delta_chunk,
    find_correlation_tif,
    find_water_mask,
    parse_hyp3_product,
    sane_water_mask,
)


# ----------------------------------------------------------------------
# Pure-numpy helper
# ----------------------------------------------------------------------
def test_coherence_delta_sign():
    # Control pair coherent (0.8), pre/post decorrelated over damage
    # (0.3): dcoh must be POSITIVE there.
    dcoh = coherence_delta_chunk(
        np.array([0.3, 0.8]), np.array([0.8, 0.8]))
    assert dcoh[0] == pytest.approx(0.5)
    assert dcoh[1] == pytest.approx(0.0)


def test_coherence_delta_nan_propagates():
    dcoh = coherence_delta_chunk(
        np.array([np.nan, 0.5, 0.5]), np.array([0.8, np.nan, 0.8]))
    assert np.isnan(dcoh[0])
    assert np.isnan(dcoh[1])
    assert dcoh[2] == pytest.approx(0.3)


def test_detector_defaults_match_step12b_findings():
    det = CoherenceDeltaDetector()
    assert det.threshold_mode == "adaptive"
    # 0.25 above the background median keeps the false-alarm rate near
    # 8-14 % on the validated ID694/ID666 events (0.10 flags ~30 % on
    # the drifted autumn scene).
    assert det.a_coh == pytest.approx(0.25)
    assert det.min_pixels == 6  # 80 m pixels: 27 px would be 17 ha
    assert det.median_filter_size == 3
    with pytest.raises(ValueError):
        CoherenceDeltaDetector(threshold_mode="bogus")


# ----------------------------------------------------------------------
# Product discovery
# ----------------------------------------------------------------------
def _make_corr_tiff(path, arr, pixel=80.0):
    osgeo = pytest.importorskip("osgeo")
    gdal, osr = osgeo.gdal, osgeo.osr
    driver = gdal.GetDriverByName("GTiff")
    h, w = arr.shape
    ds = driver.Create(path, w, h, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((500000.0, pixel, 0.0, 5000000.0, 0.0, -pixel))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32633)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(arr.astype(np.float32))
    band.FlushCache()
    ds = None
    return path


@pytest.fixture()
def coh_pair_dirs(tmp_path):
    """Two unpacked 'products' with a decorrelation blob in pre/post.

    The control pair is uniformly coherent; the pre/post pair lost
    coherence inside the blob (windthrow).
    """
    pytest.importorskip("osgeo.gdal")
    size = 100
    yy, xx = np.ogrid[:size, :size]
    blob = (yy - 50) ** 2 + (xx - 50) ** 2 <= 10 ** 2
    prepost = np.full((size, size), 0.8, dtype=np.float32)
    prepost[blob] = 0.2
    control = np.full((size, size), 0.8, dtype=np.float32)

    prepost_dir = tmp_path / "id694-coh-prepost" / "S1BB_pair_5748"
    control_dir = tmp_path / "id694-coh-control" / "S1BB_pair_5C8E"
    prepost_dir.mkdir(parents=True)
    control_dir.mkdir(parents=True)
    _make_corr_tiff(str(prepost_dir / "S1BB_pair_5748_corr.tif"), prepost)
    _make_corr_tiff(str(control_dir / "S1BB_pair_5C8E_corr.tif"), control)
    return {
        "prepost_dir": str(prepost_dir),
        "control_dir": str(control_dir),
        "prepost_tif": str(prepost_dir / "S1BB_pair_5748_corr.tif"),
        "control_tif": str(control_dir / "S1BB_pair_5C8E_corr.tif"),
        "blob": blob,
        "prepost_arr": prepost,
        "control_arr": control,
    }


def test_find_correlation_tif_direct(coh_pair_dirs):
    assert find_correlation_tif(coh_pair_dirs["prepost_tif"]) \
        == coh_pair_dirs["prepost_tif"]


def test_find_correlation_tif_in_directory(coh_pair_dirs):
    assert find_correlation_tif(coh_pair_dirs["prepost_dir"]) \
        == coh_pair_dirs["prepost_tif"]


def test_find_correlation_tif_missing_raises(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        find_correlation_tif(str(d))


def test_find_correlation_tif_multiple_hits_raises(tmp_path):
    pytest.importorskip("osgeo.gdal")
    arr = np.full((8, 8), 0.5, dtype=np.float32)
    _make_corr_tiff(str(tmp_path / "a_corr.tif"), arr)
    _make_corr_tiff(str(tmp_path / "b_corr.tif"), arr)
    with pytest.raises(ValueError, match="Multiple"):
        find_correlation_tif(str(tmp_path))


def test_find_correlation_tif_from_zip(coh_pair_dirs, tmp_path):
    zip_path = tmp_path / "S1BB_pair_5748.zip"
    tif = coh_pair_dirs["prepost_tif"]
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(tif, "S1BB_pair_5748/S1BB_pair_5748_corr.tif")
    resolved = find_correlation_tif(str(zip_path))
    assert os.path.basename(resolved).endswith("_corr.tif")
    assert os.path.isfile(resolved)


def test_find_correlation_tif_zip_without_corr_raises(tmp_path):
    zip_path = tmp_path / "empty.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("readme.txt", "nothing here")
    with pytest.raises(FileNotFoundError):
        find_correlation_tif(str(zip_path))


def test_find_correlation_tif_bad_extension_raises(tmp_path):
    f = tmp_path / "foo.txt"
    f.write_text("x")
    with pytest.raises(ValueError):
        find_correlation_tif(str(f))


def test_find_water_mask_none(tmp_path, coh_pair_dirs):
    assert find_water_mask(coh_pair_dirs["prepost_dir"]) is None


# ----------------------------------------------------------------------
# Sane water-mask heuristic (step12b: product 5748 shipped 99.6% water)
# HyP3 convention (ASF guides, GAMMA and ISCE): 1 = land, 0 = water.
# ----------------------------------------------------------------------
def test_sane_water_mask_accepts_reasonable_mask(tmp_path):
    pytest.importorskip("osgeo.gdal")
    arr = np.ones((100, 100), dtype=np.uint8)  # land everywhere...
    arr[:10, :] = 0  # ...except a 10 % water band
    p = _make_corr_tiff(str(tmp_path / "wm.tif"), arr)
    assert sane_water_mask(p) == p


def test_sane_water_mask_rejects_corrupt_mask(tmp_path):
    pytest.importorskip("osgeo.gdal")
    arr = np.zeros((100, 100), dtype=np.uint8)  # 100 % water — corrupt
    p = _make_corr_tiff(str(tmp_path / "wm_corrupt.tif"), arr)
    assert sane_water_mask(p) is None


def test_sane_water_mask_legacy_water_value(tmp_path):
    """Legacy masks (pre-2024 GSHHG convention, 1 = water) via override."""
    pytest.importorskip("osgeo.gdal")
    arr = np.zeros((100, 100), dtype=np.uint8)
    arr[:10, :] = 1  # 10 % "water" under the legacy encoding
    p = _make_corr_tiff(str(tmp_path / "wm_legacy.tif"), arr)
    assert sane_water_mask(p, water_value=1) == p
    # Under the default (water == 0) the same mask is 90 % water -> corrupt.
    assert sane_water_mask(p) is None


def test_sane_water_mask_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        sane_water_mask(str(tmp_path / "nope.tif"))


def test_sane_water_mask_none_passthrough():
    assert sane_water_mask(None) is None


# ----------------------------------------------------------------------
# Full detection chain
# ----------------------------------------------------------------------
def test_did_detects_decorrelation_blob(coh_pair_dirs, tmp_path):
    from osgeo import gdal, ogr
    out_base = str(tmp_path / "run" / "id694_did")
    det = CoherenceDeltaDetector(threshold_mode="adaptive", a_coh=0.10,
                                 min_pixels=6, median_filter_size=3)
    result = det.detect_file(
        prepost_products=[coh_pair_dirs["prepost_dir"]],
        control_products=[coh_pair_dirs["control_dir"]],
        output_base=out_base,
    )
    assert result["control_used"] is True
    assert os.path.isfile(result["dcoh"])
    assert os.path.isfile(result["mask"])
    assert os.path.isfile(result["vector"])
    assert result["vector"].endswith(".gpkg")

    _ds_dcoh = gdal.Open(result["dcoh"])

    dcoh = _ds_dcoh.GetRasterBand(1).ReadAsArray()

    _ds_dcoh = None
    blob = coh_pair_dirs["blob"]
    # DiD: +0.6 inside the blob, ~0 in the background.
    assert np.nanmedian(dcoh[blob]) == pytest.approx(0.6, abs=0.01)
    assert abs(np.nanmedian(dcoh[~blob])) < 0.01

    _ds_mask = gdal.Open(result["mask"])

    mask = _ds_mask.GetRasterBand(1).ReadAsArray()

    _ds_mask = None
    detected = mask > 0
    recall = (detected & blob).sum() / float(blob.sum())
    precision = (detected & blob).sum() / float(max(detected.sum(), 1))
    assert recall > 0.6
    assert precision > 0.8
    assert result["n_objects"] >= 1

    ds = ogr.Open(result["vector"])
    assert ds.GetLayer(0).GetFeatureCount() >= 1


def test_no_control_falls_back_to_static_decorrelation(
        coh_pair_dirs, tmp_path):
    from osgeo import gdal
    out_base = str(tmp_path / "run_noctl" / "event")
    det = CoherenceDeltaDetector(min_pixels=6)
    result = det.detect_file(
        prepost_products=[coh_pair_dirs["prepost_dir"]],
        control_products=[],
        output_base=out_base,
    )
    assert result["control_used"] is False
    # 1 - coherence: 0.8 over damage vs 0.2 background — blob detected.
    _ds_dcoh = gdal.Open(result["dcoh"])
    dcoh = _ds_dcoh.GetRasterBand(1).ReadAsArray()
    _ds_dcoh = None
    blob = coh_pair_dirs["blob"]
    assert np.nanmedian(dcoh[blob]) == pytest.approx(0.8, abs=0.01)
    _ds_mask = gdal.Open(result["mask"])
    mask = _ds_mask.GetRasterBand(1).ReadAsArray()
    _ds_mask = None
    assert ((mask > 0) & blob).sum() > 0


def test_fixed_threshold_mode(coh_pair_dirs, tmp_path):
    from osgeo import gdal
    out_base = str(tmp_path / "run_fixed" / "event")
    det = CoherenceDeltaDetector(threshold_mode="fixed",
                                 fixed_threshold=0.3, min_pixels=6)
    result = det.detect_file(
        prepost_products=[coh_pair_dirs["prepost_dir"]],
        control_products=[coh_pair_dirs["control_dir"]],
        output_base=out_base,
    )
    assert result["threshold"] == pytest.approx(0.3)
    _ds_mask = gdal.Open(result["mask"])
    mask = _ds_mask.GetRasterBand(1).ReadAsArray()
    _ds_mask = None
    blob = coh_pair_dirs["blob"]
    assert ((mask > 0) & blob).sum() > 0
    assert ((mask > 0) & ~blob).sum() == 0


def test_corrupt_water_mask_is_ignored(coh_pair_dirs, tmp_path):
    from osgeo import gdal
    pytest.importorskip("osgeo.gdal")
    # Corrupt mask: everything is "water" (99.6 % case of product 5748).
    # HyP3 convention: water == 0.
    corrupt = np.zeros((100, 100), dtype=np.uint8)
    wm_path = os.path.join(
        coh_pair_dirs["prepost_dir"], "S1BB_pair_5748_water_mask.tif")
    _make_corr_tiff(wm_path, corrupt)
    out_base = str(tmp_path / "run_wm" / "event")
    det = CoherenceDeltaDetector(min_pixels=6)
    result = det.detect_file(
        prepost_products=[coh_pair_dirs["prepost_dir"]],
        control_products=[coh_pair_dirs["control_dir"]],
        output_base=out_base,
    )
    assert len(result["water_mask_ignored"]) == 1
    # Detections are unaffected — the blob is still found.
    _ds_mask = gdal.Open(result["mask"])
    mask = _ds_mask.GetRasterBand(1).ReadAsArray()
    _ds_mask = None
    assert ((mask > 0) & coh_pair_dirs["blob"]).sum() > 0


def test_sane_water_mask_restricts_detection(coh_pair_dirs, tmp_path):
    """Non-vacuous check of the keep-land conversion (v1.2 fix).

    Water band at the top rows (mask == 0) contains a SECOND
    decorrelation blob; only the land blob may be detected.  Under the
    pre-v1.2 (inverted) semantics the water blob was kept and the land
    one clipped, so this test actually pins the convention.
    """
    from osgeo import gdal
    pytest.importorskip("osgeo.gdal")
    # Water band at the top (0 = water), land below (1 = land).
    water = np.ones((100, 100), dtype=np.uint8)
    water[:10, :] = 0
    wm_path = os.path.join(
        coh_pair_dirs["prepost_dir"], "S1BB_pair_5748_water_mask.tif")
    _make_corr_tiff(wm_path, water)
    # Second decorrelation blob INSIDE the water band.
    ds = gdal.Open(coh_pair_dirs["prepost_tif"], gdal.GA_Update)
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray()
    water_blob = np.zeros_like(arr, dtype=bool)
    water_blob[2:8, 40:60] = True
    arr[water_blob] = 0.2
    band.WriteArray(arr)
    band.FlushCache()
    ds = None
    out_base = str(tmp_path / "run_wm2" / "event")
    det = CoherenceDeltaDetector(min_pixels=6)
    result = det.detect_file(
        prepost_products=[coh_pair_dirs["prepost_dir"]],
        control_products=[coh_pair_dirs["control_dir"]],
        output_base=out_base,
    )
    assert result["water_mask_ignored"] == []
    _ds_mask = gdal.Open(result["mask"])
    mask = _ds_mask.GetRasterBand(1).ReadAsArray()
    _ds_mask = None
    # Nothing detected inside the water band (the water blob is masked)…
    assert (mask[:10, :] > 0).sum() == 0
    # …and the land blob is still detected (pre-v1.2 it was NOT).
    assert ((mask > 0) & coh_pair_dirs["blob"]).sum() > 0


def test_nodata_written_to_dcoh_raster(coh_pair_dirs, tmp_path):
    pytest.importorskip("osgeo.gdal")
    from osgeo import gdal
    # NaN gap in the pre/post coherence.
    prepost_path = coh_pair_dirs["prepost_tif"]
    ds = gdal.Open(prepost_path, gdal.GA_Update)
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray()
    arr[80:90, 80:90] = np.nan
    band.WriteArray(arr)
    band.FlushCache()
    ds = None
    out_base = str(tmp_path / "run_nan" / "event")
    det = CoherenceDeltaDetector(min_pixels=6)
    result = det.detect_file(
        prepost_products=[coh_pair_dirs["prepost_dir"]],
        control_products=[coh_pair_dirs["control_dir"]],
        output_base=out_base,
    )
    _ds_dcoh = gdal.Open(result["dcoh"])
    dcoh = _ds_dcoh.GetRasterBand(1).ReadAsArray()
    _ds_dcoh = None
    assert (dcoh[80:90, 80:90] == DCOH_NODATA).all()
    assert (dcoh[80:90, 80:90] != DCOH_NODATA).sum() == 0


def test_warp_fill_sentinel_excluded_from_stats(coh_pair_dirs, tmp_path):
    """Control products warped onto another grid carry +-9999 fills.

    The fill must not poison the adaptive threshold (ID666 case:
    mean_dcoh -95 before the fix) nor create garbage detections.
    """
    pytest.importorskip("osgeo.gdal")
    from osgeo import gdal
    # Control pair: same blob geometry but with a warp-fill block.
    size = 100
    control = np.full((size, size), 0.8, dtype=np.float32)
    control[:12, :] = -9999.0  # warp-fill sentinel rows
    ctl_dir = tmp_path / "ctl_fill"
    ctl_dir.mkdir(parents=True)
    _make_corr_tiff(str(ctl_dir / "S1BB_pair_172D_corr.tif"), control)
    out_base = str(tmp_path / "run_fill" / "event")
    det = CoherenceDeltaDetector(min_pixels=6)
    result = det.detect_file(
        prepost_products=[coh_pair_dirs["prepost_dir"]],
        control_products=[str(ctl_dir)],
        output_base=out_base,
    )
    # Mean must stay near 0 (background cancels), not sink to -hundreds.
    assert -0.05 < result["mean_dcoh"] < 0.05
    dcoh_ds = gdal.Open(result["dcoh"])
    dcoh = dcoh_ds.GetRasterBand(1).ReadAsArray()
    dcoh_ds = None
    assert (dcoh[:12, :] == DCOH_NODATA).all()
    # Blob still detected.
    mask_ds = gdal.Open(result["mask"])
    mask = mask_ds.GetRasterBand(1).ReadAsArray()
    mask_ds = None
    assert ((mask > 0) & coh_pair_dirs["blob"]).sum() > 0


# ----------------------------------------------------------------------
# v1.1: statistics-only background mask (GFW rescore semantics)
# ----------------------------------------------------------------------
def test_background_mask_shifts_stats_not_detections(coh_pair_dirs,
                                                     tmp_path):
    """``background_mask_path`` restricts ONLY the adaptive statistics.

    The top 60% of the frame carries a seasonal drift (dcoh +0.25);
    the background mask excludes it from the statistics sample, so the
    median (and the threshold) drops to the clean-forest level and the
    drift area becomes detectable — while detections themselves are NOT
    restricted by the background mask (report ed.8 §9: event-year
    losses stay in the candidates, only the background sample is
    cleaned).
    """
    pytest.importorskip("osgeo.gdal")
    from osgeo import gdal, osr
    size = 100
    drift = np.zeros((size, size), dtype=bool)
    drift[:60, :] = True                      # seasonal-drift half

    # Rewrite the pre/post layer with the drift half less coherent.
    prepost = coh_pair_dirs["prepost_arr"].copy()   # 0.8 bg, blob 0.2
    prepost[drift] = 0.55
    drift_dir = tmp_path / "id-drift-prepost" / "S1BB_pair_DR1F"
    drift_dir.mkdir(parents=True)
    _make_corr_tiff(str(drift_dir / "S1BB_pair_DR1F_corr.tif"), prepost)

    # Background mask = clean (non-drift) half only.
    bg_mask = np.zeros((size, size), dtype=np.uint8)
    bg_mask[~drift] = 255
    mask_path = str(tmp_path / "bg_clean_half.tif")
    ds = gdal.GetDriverByName("GTiff").Create(
        mask_path, size, size, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((500000.0, 80.0, 0.0, 5000000.0, 0.0, -80.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32633)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(bg_mask)
    ds.GetRasterBand(1).SetNoDataValue(0)
    ds = None

    out_plain = str(tmp_path / "run_plain" / "event")
    det_plain = CoherenceDeltaDetector(a_coh=0.1, min_pixels=6)
    res_plain = det_plain.detect_file(
        prepost_products=[str(drift_dir)],
        control_products=[coh_pair_dirs["control_dir"]],
        output_base=out_plain,
    )
    # Median over the whole frame = drift level (60 % of pixels).
    assert det_plain.median_dcoh == pytest.approx(0.25, abs=0.02)
    assert det_plain.threshold_used == pytest.approx(0.35, abs=0.03)

    out_masked = str(tmp_path / "run_masked" / "event")
    det_masked = CoherenceDeltaDetector(a_coh=0.1, min_pixels=6)
    res_masked = det_masked.detect_file(
        prepost_products=[str(drift_dir)],
        control_products=[coh_pair_dirs["control_dir"]],
        output_base=out_masked,
        background_mask_path=mask_path,
    )
    # Statistics now come from the clean half only -> median ~0.
    assert det_masked.median_dcoh == pytest.approx(0.0, abs=0.02)
    assert det_masked.threshold_used == pytest.approx(0.1, abs=0.03)
    assert res_masked["background_mask"] and os.path.isfile(
        res_masked["background_mask"])
    # ...and detections are NOT restricted: the drifted area (above the
    # lowered threshold) is detected, i.e. the mask did not clip it.
    mask_ds = gdal.Open(res_masked["mask"])
    mask = mask_ds.GetRasterBand(1).ReadAsArray()
    mask_ds = None
    assert (mask[drift] > 0).mean() > 0.5
    # With the whole-frame statistics the drifted area stays BELOW the
    # higher threshold — the mask changed the outcome.
    mask_ds = gdal.Open(res_plain["mask"])
    mask_plain = mask_ds.GetRasterBand(1).ReadAsArray()
    mask_ds = None
    assert (mask_plain[drift] > 0).mean() < 0.05

    # Missing file -> ValueError
    det_bad = CoherenceDeltaDetector(a_coh=0.1, min_pixels=6)
    with pytest.raises(ValueError, match="Background mask file not found"):
        det_bad.detect_file(
            prepost_products=[str(drift_dir)],
            control_products=[coh_pair_dirs["control_dir"]],
            output_base=str(tmp_path / "run_bad" / "event"),
            background_mask_path=str(tmp_path / "missing.tif"))


# ----------------------------------------------------------------------
# v1.2: HyP3 granule parsing (GAMMA vs ISCE burst)
# ----------------------------------------------------------------------
GAMMA_GRANULE = "S1AB_20171111T150004_20171117T145926_VVP006_INT80_G_ueF_4D09"
ISCE_SINGLE = "S1_123_111111s1n02_IW_20240101_20240115_VV_INT40_AEB4"
ISCE_MULTI = ("S1_123_111111s1n02-111111s2n01-000000s3n00"
              "_IW_20240101_20240115_VV_INT40_AEB4")


def test_parse_gamma_granule():
    for source in (f"/x/{GAMMA_GRANULE}.zip", f"/x/{GAMMA_GRANULE}_corr.tif"):
        info = parse_hyp3_product(source)
        assert info["family"] == "gamma"
        assert info["granule"] == GAMMA_GRANULE
        assert info["platform"] == "A"
        assert info["pol"] == "VV"
        assert info["spacing_m"] == 80
        assert info["pid"] == "4D09"
        assert info["date1"] == "20171111T150004"
        assert info["burst_key"] is None


def test_parse_isce_burst_granule():
    info = parse_hyp3_product(f"/x/{ISCE_SINGLE}.zip")
    assert info["family"] == "isce"
    assert info["track"] == 123
    assert info["burst_key"] == "111111s1n02"
    assert info["pol"] == "VV"
    assert info["spacing_m"] == 40
    assert info["date1"] == "20240101"
    assert info["date2"] == "20240115"


def test_parse_isce_multi_burst_granule():
    info = parse_hyp3_product(f"/orders/{ISCE_MULTI}")
    assert info["family"] == "isce"
    assert info["burst_key"] == (
        "111111s1n02-111111s2n01-000000s3n00")
    assert info["spacing_m"] == 40


def test_parse_custom_product_yields_none_family():
    info = parse_hyp3_product("/home/user/my_pair_5748_corr.tif")
    assert info["family"] is None
    assert info["granule"] == "my_pair_5748"


def test_validate_did_pair_accepts_same_burst():
    prepost = parse_hyp3_product(f"/pp/{ISCE_SINGLE}.zip")
    control = parse_hyp3_product(
        f"/ctl/S1_123_111111s1n02_IW_20240601_20240613_VV_INT40_C334.zip")
    _validate_did_pair(prepost, control)  # must not raise


def test_validate_did_pair_rejects_burst_mismatch():
    prepost = parse_hyp3_product(f"/pp/{ISCE_SINGLE}.zip")
    control = parse_hyp3_product(
        "/ctl/S1_123_999999s1n02_IW_20240601_20240613_VV_INT40_C334.zip")
    with pytest.raises(ValueError, match="SAME burst footprint"):
        _validate_did_pair(prepost, control)


def test_validate_did_pair_rejects_family_mixing():
    prepost = parse_hyp3_product(f"/pp/{ISCE_SINGLE}.zip")
    control = parse_hyp3_product(f"/ctl/{GAMMA_GRANULE}.zip")
    with pytest.raises(ValueError, match="Mixing product families"):
        _validate_did_pair(prepost, control)


def test_validate_did_pair_rejects_polarization_mismatch():
    prepost = parse_hyp3_product(f"/pp/{ISCE_SINGLE}.zip")
    control = parse_hyp3_product(
        "/ctl/S1_123_111111s1n02_IW_20240601_20240613_HH_INT40_C334.zip")
    with pytest.raises(ValueError, match="polarization mismatch"):
        _validate_did_pair(prepost, control)


def test_validate_did_pair_warns_on_spacing_mismatch():
    # Different look selection (40 m vs 80 m): allowed but noisy —
    # the validator must NOT raise, only warn (logged).
    prepost = parse_hyp3_product(f"/pp/{ISCE_SINGLE}.zip")
    control = parse_hyp3_product(
        "/ctl/S1_123_111111s1n02_IW_20240601_20240613_VV_INT80_C334.zip")
    _validate_did_pair(prepost, control)


# ----------------------------------------------------------------------
# v1.2: end-to-end detection on burst-flavoured products (40 m posting)
# ----------------------------------------------------------------------
def test_burst_products_end_to_end(coh_pair_dirs, tmp_path):
    """40 m burst products run through the whole DiD chain.

    Product directories carry real ISCE granule names (INT40); the
    detector must parse both sides, validate the same-burst pairing,
    detect the blob and report the flavour + posting diagnostics.
    """
    from osgeo import gdal
    pytest.importorskip("osgeo.gdal")
    pp_dir = tmp_path / "bursts" / ISCE_SINGLE
    ctl_dir = tmp_path / "bursts" / (
        "S1_123_111111s1n02_IW_20240601_20240613_VV_INT40_C334")
    pp_dir.mkdir(parents=True)
    ctl_dir.mkdir(parents=True)
    _make_corr_tiff(str(pp_dir / (ISCE_SINGLE + "_corr.tif")),
                    coh_pair_dirs["prepost_arr"], pixel=40.0)
    _make_corr_tiff(str(ctl_dir / (
        "S1_123_111111s1n02_IW_20240601_20240613_VV_INT40_C334"
        + "_corr.tif")),
        coh_pair_dirs["control_arr"], pixel=40.0)
    out_base = str(tmp_path / "run_burst" / "event")
    det = CoherenceDeltaDetector(min_pixels=6)
    result = det.detect_file(
        prepost_products=[str(pp_dir)],
        control_products=[str(ctl_dir)],
        output_base=out_base,
    )
    assert result["product_flavors"] == {"prepost": "isce",
                                         "control": "isce"}
    assert result["pixel_size_m"] == pytest.approx(40.0)
    assert result["min_object_area_ha"] == pytest.approx(6 * 0.16)
    assert result["product_info"]["prepost"]["pid"] == "AEB4"
    _ds_mask = gdal.Open(result["mask"])
    mask = _ds_mask.GetRasterBand(1).ReadAsArray()
    _ds_mask = None
    assert ((mask > 0) & coh_pair_dirs["blob"]).sum() > 0
    assert ((mask > 0) & ~coh_pair_dirs["blob"]).sum() == 0
