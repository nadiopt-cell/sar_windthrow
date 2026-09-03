"""Unit tests for the L-band decline detection module (v1.0).

Physics reminder (Tanase et al. 2018): over windthrow the L-band
backscatter DECLINES, so the decline index
LDI = (HH_pre - HH_post) + (HV_pre - HV_post)  [dB]
is POSITIVE over damage — the opposite sign of the C-band WI.

Array-level tests run on synthetic data only; file-level tests
exercise the full GDAL chain on small synthetic GeoTIFFs and skip
automatically when ``osgeo`` is unavailable.
"""

import os

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources.lband import (
    LBAND_POLS,
    LbandDeclineDetector,
)
from sentinel1_windthrow_plugin.sources.windthrow import WindthrowDetector


# ----------------------------------------------------------------------
# Pure-python behaviour (no GDAL required)
# ----------------------------------------------------------------------
def test_default_sign_is_inverted():
    assert LbandDeclineDetector()._delta_sign() == -1.0
    assert WindthrowDetector()._delta_sign() == 1.0


def test_default_index_suffix():
    assert LbandDeclineDetector()._index_suffix() == "_ldi_norm"
    assert LbandDeclineDetector(
        normalize_background=False)._index_suffix() == "_ldi"


def test_lband_pols_constant():
    assert LBAND_POLS == ("hh", "hv")


def test_default_offset_is_lband_tuned():
    # L-band pairs (annual mosaics) need a smaller offset than the
    # C-band paper optimum 2.9 dB.
    assert LbandDeclineDetector().a_db == pytest.approx(2.0)
    assert WindthrowDetector().a_db == pytest.approx(2.9)


def test_is_a_windthrow_detector_subclass():
    assert issubclass(LbandDeclineDetector, WindthrowDetector)


def test_polarization_restriction_hh_hv_only():
    det = LbandDeclineDetector()
    assert det._restrict_polarizations(["hh", "hv"]) == ["hh", "hv"]
    assert det._restrict_polarizations(["HH", "HV", "vv"]) == ["HH", "HV"]


def test_polarization_restriction_explicit_subset():
    det = LbandDeclineDetector(polarizations=["hv"])
    assert det.polarizations == ["hv"]
    assert det._restrict_polarizations(["hh", "hv"]) == ["hv"]


def test_polarization_restriction_missing_explicit_raises():
    det = LbandDeclineDetector(polarizations=["hv"])
    with pytest.raises(ValueError, match="hv"):
        det._restrict_polarizations(["hh"])


def test_polarization_restriction_missing_default_falls_back():
    # Default (no explicit restriction) must fall back to whatever is
    # common instead of raising — makes C-band decline experiments
    # possible with a visible warning.
    det = LbandDeclineDetector()
    assert det._restrict_polarizations(["vv", "vh"]) == ["vv", "vh"]


def test_polarization_restriction_empty_list_raises():
    with pytest.raises(ValueError):
        LbandDeclineDetector(polarizations=[""])


def test_threshold_mode_validation_inherited():
    with pytest.raises(ValueError):
        LbandDeclineDetector(threshold_mode="bogus")


# ----------------------------------------------------------------------
# File-level tests (GDAL chain)
# ----------------------------------------------------------------------
def _write_tiff(path, arr, gt=None, proj=None, dtype=None):
    osgeo = pytest.importorskip("osgeo")
    gdal, osr = osgeo.gdal, osgeo.osr
    driver = gdal.GetDriverByName("GTiff")
    h, w = arr.shape
    dt = dtype or (gdal.GDT_Float32 if np.issubdtype(arr.dtype, np.floating)
                   else gdal.GDT_Byte)
    ds = driver.Create(path, w, h, 1, dt)
    if gt is None:
        gt = (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0)
    ds.SetGeoTransform(gt)
    if proj is None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(32633)
        proj = srs.ExportToWkt()
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None
    return path


@pytest.fixture()
def synthetic_palsar_scene(tmp_path):
    """Pre/post PALSAR HH+HV scenes with a decline blob in the post.

    Windthrow: strong backscatter DECLINE inside a circular blob
    (canopy flattened).  File names carry _HH/_HV tokens so the
    polarisation pairing works like for real ALOS mosaics.
    """
    pytest.importorskip("osgeo.gdal")
    pytest.importorskip("osgeo.ogr")
    rng = np.random.default_rng(7)
    size = 120
    pre_hh = np.full((size, size), -8.0, dtype=np.float32)
    pre_hv = np.full((size, size), -13.0, dtype=np.float32)
    post_hh = pre_hh + rng.normal(0.0, 0.4, (size, size)).astype(np.float32)
    post_hv = pre_hv + rng.normal(0.0, 0.4, (size, size)).astype(np.float32)
    yy, xx = np.ogrid[:size, :size]
    blob = (yy - 60) ** 2 + (xx - 60) ** 2 <= 10 ** 2
    post_hh[blob] -= 5.0
    post_hv[blob] -= 5.0

    d = str(tmp_path)
    paths = {
        "pre_hh": _write_tiff(os.path.join(d, "mosaic_2016_HH.tif"), pre_hh),
        "pre_hv": _write_tiff(os.path.join(d, "mosaic_2016_HV.tif"), pre_hv),
        "post_hh": _write_tiff(os.path.join(d, "mosaic_2017_HH.tif"), post_hh),
        "post_hv": _write_tiff(os.path.join(d, "mosaic_2017_HV.tif"), post_hv),
    }
    return paths, blob


def test_lband_detect_file_full_pipeline(synthetic_palsar_scene, tmp_path):
    from osgeo import gdal, ogr
    paths, blob = synthetic_palsar_scene
    out_base = str(tmp_path / "run1" / "lband_event")
    det = LbandDeclineDetector(
        threshold_mode="adaptive", a_db=2.0, min_pixels=27,
        median_filter_size=3,
    )
    result = det.detect_file(
        pre_paths=[paths["pre_hh"], paths["pre_hv"]],
        post_paths=[paths["post_hh"], paths["post_hv"]],
        output_base=out_base,
    )
    # Output artefacts use the LDI suffix, not WI.
    assert result["wi"] == out_base + "_ldi_norm.tif"
    assert os.path.isfile(result["wi"])
    assert os.path.isfile(result["mask"])
    assert os.path.isfile(result["vector"])
    assert result["vector"].endswith(".gpkg")

    _ds_ldi = gdal.Open(result["wi"])

    ldi = _ds_ldi.GetRasterBand(1).ReadAsArray()

    _ds_ldi = None
    # Background LDI hovers near 0 dB (post minus pre noise), damage is
    # strongly positive after the sign flip.
    assert np.isfinite(ldi[~blob]).all()
    assert np.nanmedian(ldi[blob]) > 4.0
    assert abs(np.nanmedian(ldi[~blob])) < 1.5

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
    layer = ds.GetLayer(0)
    assert layer.GetFeatureCount() >= 1


def test_lband_single_polarisation_hv(synthetic_palsar_scene, tmp_path):
    from osgeo import gdal
    paths, blob = synthetic_palsar_scene
    out_base = str(tmp_path / "run_hv" / "hv_event")
    det = LbandDeclineDetector(polarizations=["hv"], a_db=2.0,
                               min_pixels=27)
    result = det.detect_file(
        pre_paths=[paths["pre_hv"]],
        post_paths=[paths["post_hv"]],
        output_base=out_base,
    )
    _ds_ldi = gdal.Open(result["wi"])
    ldi = _ds_ldi.GetRasterBand(1).ReadAsArray()
    _ds_ldi = None
    assert np.nanmedian(ldi[blob]) > 4.0
    _ds_mask = gdal.Open(result["mask"])
    mask = _ds_mask.GetRasterBand(1).ReadAsArray()
    _ds_mask = None
    assert ((mask > 0) & blob).sum() > 0


def test_lband_explicit_missing_pol_raises(synthetic_palsar_scene,
                                           tmp_path):
    paths, _blob = synthetic_palsar_scene
    det = LbandDeclineDetector(polarizations=["vv"])
    with pytest.raises(ValueError, match="vv"):
        det.detect_file(
            pre_paths=[paths["pre_hh"], paths["pre_hv"]],
            post_paths=[paths["post_hh"], paths["post_hv"]],
            output_base=str(tmp_path / "run_err" / "event"),
        )


def test_lband_forest_mask_restricts_detection(synthetic_palsar_scene,
                                               tmp_path):
    from osgeo import gdal
    paths, blob = synthetic_palsar_scene
    # Forest everywhere except a band crossing the blob top.
    size = 120
    forest = np.ones((size, size), dtype=np.uint8)
    forest[:50, :] = 0  # non-forest band overlapping the blob (rows 50-70)
    forest_path = _write_tiff(
        os.path.join(str(tmp_path), "forest.tif"), forest)
    out_base = str(tmp_path / "run_forest" / "event")
    det = LbandDeclineDetector(a_db=2.0, min_pixels=27)
    result = det.detect_file(
        pre_paths=[paths["pre_hh"], paths["pre_hv"]],
        post_paths=[paths["post_hh"], paths["post_hv"]],
        output_base=out_base,
        forest_mask_path=forest_path,
    )
    _ds_mask = gdal.Open(result["mask"])
    mask = _ds_mask.GetRasterBand(1).ReadAsArray()
    _ds_mask = None
    assert (mask[:50, :] > 0).sum() == 0
