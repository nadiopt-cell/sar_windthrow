"""Tests for the shared postprocess pipeline stages (spec §4).

Covers the mask-raster writer (georeferencing, 0/255, nodata), the
polygonize + area_ha + min-size stage, the connected-component
cleanup and the run_postprocess convenience wrapper (path vs
open-band equivalence).  The threshold pass itself has dedicated
IO-free tests in ``test_pipeline_pure.py``.
"""

import os

import numpy as np
import pytest

from osgeo import gdal, ogr, osr

from sentinel1_windthrow_plugin.sources.pipeline import (
    PipelineConfig,
    run_postprocess,
    write_mask_raster,
    polygonize_mask,
)

NODATA = -9999.0
GT_10M = (500000.0, 10.0, 0.0, 5000000.0, 0.0, -10.0)


def _proj_wkt():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32633)
    return srs.ExportToWkt()


def _ref_info(width, height, gt=GT_10M):
    return {
        "width": width,
        "height": height,
        "geotransform": gt,
        "projection": _proj_wkt(),
    }


def _write_tiff(path, arr, nodata=None, gt=GT_10M, dtype=None):
    driver = gdal.GetDriverByName("GTiff")
    h, w = arr.shape
    if dtype is None:
        dtype = (gdal.GDT_Float32 if np.issubdtype(arr.dtype, np.floating)
                 else gdal.GDT_Byte)
    ds = driver.Create(path, w, h, 1, dtype)
    ds.SetGeoTransform(gt)
    ds.SetProjection(_proj_wkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.FlushCache()
    return path


def _read_mask(path):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    return arr, nodata, gt, proj


def _feature_infos(vector_path):
    ds = ogr.Open(vector_path)
    layer = ds.GetLayer(0)
    infos = []
    for feat in layer:
        infos.append((feat.GetFID(), feat.GetField("area_ha")))
    ds = None
    return infos


def test_write_mask_raster_georef_and_values(tmp_path):
    mask = np.zeros((12, 20), dtype=bool)
    mask[3:6, 4:9] = True
    base = str(tmp_path / "out")
    path = write_mask_raster(mask, _ref_info(20, 12), base)
    assert path == os.path.join(str(tmp_path), "out_mask.tif")
    arr, nodata, gt, proj = _read_mask(path)
    assert nodata == 0.0
    assert gt == GT_10M
    assert proj == _proj_wkt()
    assert np.array_equal(arr, mask.astype(np.uint8) * 255)


def test_write_mask_raster_shape_mismatch(tmp_path):
    mask = np.zeros((5, 5), dtype=bool)
    with pytest.raises(RuntimeError, match="reference grid"):
        write_mask_raster(mask, _ref_info(20, 12), str(tmp_path / "x"))


def test_polygonize_area_filter_and_area_ha(tmp_path):
    mask = np.zeros((20, 20), dtype=bool)
    mask[2:7, 2:7] = True   # 25 px -> 2500 m2 -> 0.25 ha (kept)
    mask[12:14, 12:14] = True  # 4 px -> 400 m2 (deleted at min_pixels=6)
    base = str(tmp_path / "out")
    mask_path = write_mask_raster(mask, _ref_info(20, 20), base)
    vector_path, n_objects = polygonize_mask(
        mask_path, _ref_info(20, 20), base, min_pixels=6)
    assert vector_path == base + ".gpkg"
    assert n_objects == 1
    infos = _feature_infos(vector_path)
    assert len(infos) == 1
    assert infos[0][1] == pytest.approx(0.25)


def test_polygonize_min_pixels_zero_keeps_all(tmp_path):
    mask = np.zeros((20, 20), dtype=bool)
    mask[12:14, 12:14] = True  # 4 px
    base = str(tmp_path / "out")
    mask_path = write_mask_raster(mask, _ref_info(20, 20), base)
    _, n_objects = polygonize_mask(
        mask_path, _ref_info(20, 20), base, min_pixels=0)
    assert n_objects == 1


def test_run_postprocess_path_and_band_equivalent(tmp_path):
    height, width = 30, 40
    # finite 0.0 background (nodata background would trip the legacy
    # majority-sentinel median quirk and inflate the blobs)
    index = np.zeros((height, width), dtype=np.float32)
    index[2:6, 2:6] = 5.0      # 4x4 = 16 px blob (median erodes corners)
    index[20:23, 30:34] = 4.0  # 3x4 = 12 px blob
    index[10, 10] = 6.0        # isolated pixel -> median-filtered away
    index[27:, 36:] = NODATA   # far corner, outside the inside-mask
    index_path = _write_tiff(str(tmp_path / "idx.tif"), index, nodata=NODATA)

    inside = np.zeros((height, width), dtype=np.uint8)
    inside[2:6, 2:6] = 255
    inside[20:23, 30:34] = 255
    inside[10, 10] = 255
    inside_path = _write_tiff(str(tmp_path / "inside.tif"), inside)

    cfg = PipelineConfig(
        threshold=2.9, min_pixels=5, median_size=3, nodata=NODATA,
        output_base=str(tmp_path / "a"), median_label="dcoh")
    res_path = run_postprocess(
        index_path, _ref_info(width, height), cfg,
        inside_mask_path=inside_path,
        progress_start=55.0, progress_span=15.0)

    cfg_b = PipelineConfig(
        threshold=2.9, min_pixels=5, median_size=3, nodata=NODATA,
        output_base=str(tmp_path / "b"), median_label="dcoh")
    idx_ds = gdal.Open(index_path, gdal.GA_ReadOnly)
    inside_ds = gdal.Open(inside_path, gdal.GA_ReadOnly)
    res_band = run_postprocess(
        idx_ds.GetRasterBand(1), _ref_info(width, height), cfg_b,
        inside_band=inside_ds.GetRasterBand(1))
    idx_ds = None
    inside_ds = None

    assert res_path.n_objects == res_band.n_objects == 2
    a, _, _, _ = _read_mask(res_path.mask_path)
    b, _, _, _ = _read_mask(res_band.mask_path)
    assert np.array_equal(a, b)
    assert a[10, 10] == 0  # median filter removed the lone pixel
    assert set(os.path.basename(p) for p in
               (res_path.mask_path, res_path.vector_path)) == {
        "a_mask.tif", "a.gpkg"}
    infos = _feature_infos(res_path.vector_path)
    assert len(infos) == 2
    areas = sorted(area for _, area in infos)
    # median keeps blob cores (edge pixels with >=5 source neighbours):
    # 4x4 -> 12 px = 0.12 ha; 3x4 -> 8 px = 0.08 ha (10 m pixels)
    assert areas[0] == pytest.approx(8 * 100.0 / 10000.0)    # 0.08 ha
    assert areas[1] == pytest.approx(12 * 100.0 / 10000.0)   # 0.12 ha


def test_run_postprocess_reports_threshold_progress(tmp_path):
    height, width = 600, 8
    index = np.zeros((height, width), dtype=np.float32)
    index_path = _write_tiff(str(tmp_path / "idx.tif"), index, nodata=NODATA)
    cfg = PipelineConfig(
        threshold=2.9, min_pixels=1, median_size=1, nodata=NODATA,
        output_base=str(tmp_path / "out"))
    calls = []
    run_postprocess(index_path, _ref_info(width, height), cfg,
                    report=lambda f, m: calls.append((f, m)),
                    progress_start=55.0, progress_span=15.0)
    assert calls and all(m == "Thresholding" for _, m in calls)
    assert calls[-1][0] == 70.0


def test_run_postprocess_inside_args_are_exclusive(tmp_path):
    index = np.zeros((4, 4), dtype=np.float32)
    index_path = _write_tiff(str(tmp_path / "idx.tif"), index, nodata=NODATA)
    inside_path = _write_tiff(str(tmp_path / "in.tif"),
                              np.ones((4, 4), dtype=np.uint8))
    cfg = PipelineConfig(threshold=1.0, min_pixels=1,
                         output_base=str(tmp_path / "out"))
    band = gdal.Open(index_path, gdal.GA_ReadOnly).GetRasterBand(1)
    with pytest.raises(ValueError, match="not both"):
        run_postprocess(band, _ref_info(4, 4), cfg,
                        inside_band=band, inside_mask_path=inside_path)
