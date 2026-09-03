"""Unit tests for the coherence DiD module (v1.0, port of step12b).

Sign convention: dcoh = coh_control - coh_prepost is POSITIVE over
windthrow (the damage-window pair decorrelates while the control pair
stays coherent).
"""

import os
import zipfile

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources.coh_delta import (
    DCOH_NODATA,
    CoherenceDeltaDetector,
    coherence_delta_chunk,
    find_correlation_tif,
    find_water_mask,
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
# ----------------------------------------------------------------------
def test_sane_water_mask_accepts_reasonable_mask(tmp_path):
    pytest.importorskip("osgeo.gdal")
    arr = np.zeros((100, 100), dtype=np.uint8)
    arr[:10, :] = 1  # 10% water
    p = _make_corr_tiff(str(tmp_path / "wm.tif"), arr)
    assert sane_water_mask(p) == p


def test_sane_water_mask_rejects_corrupt_mask(tmp_path):
    pytest.importorskip("osgeo.gdal")
    arr = np.ones((100, 100), dtype=np.uint8)  # 100% water — corrupt
    p = _make_corr_tiff(str(tmp_path / "wm_corrupt.tif"), arr)
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
    # Corrupt mask: everything is "water" (99.6% case of product 5748).
    corrupt = np.ones((100, 100), dtype=np.uint8)
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
    from osgeo import gdal
    pytest.importorskip("osgeo.gdal")
    # Sane mask: water band at the top (10%) — no detections may land
    # inside it.
    water = np.zeros((100, 100), dtype=np.uint8)
    water[:10, :] = 1
    wm_path = os.path.join(
        coh_pair_dirs["prepost_dir"], "S1BB_pair_5748_water_mask.tif")
    _make_corr_tiff(wm_path, water)
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
    assert (mask[:10, :] > 0).sum() == 0


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
