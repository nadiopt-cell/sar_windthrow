#!/usr/bin/env python3
"""Диагноз id654 (AUC 0.05, excess -0.40): почему когерентность внутри
полигона ВЫШЕ в prepost-паре, чем в контроле. Сравниваем coh внутри
референса и фона раздельно для prepost и control пар, плюс WorldCover
доля леса в референсе на сетке 80 м."""
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

REPO = "/home/z/my-project/sar_windthrow"
sys.path.insert(0, f"{REPO}/qgis_plugin")

spec = importlib.util.spec_from_file_location(
    "step12", f"{REPO}/pipeline/step12_coherence_analysis.py")
step12 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step12)

EID = 654
PRE = Path("/home/z/my-project/download/hyp3_products_wave2/"
           "S1BB_20170712T031830_20170724T031831_VVP012_INT80_G_ueF_3A90.zip")
CTL = Path("/home/z/my-project/download/hyp3_products_wave2/"
           "S1BB_20170724T031831_20170805T031832_VVP012_INT80_G_ueF_4FDF.zip")
TMP = Path("/home/z/my-project/work_data/wave2/_diag654")
TMP.mkdir(parents=True, exist_ok=True)


def corr_from_zip(zp: Path, tag: str) -> str:
    """Извлечь *_corr.tif из HyP3-zip -> путь."""
    with zipfile.ZipFile(zp) as z:
        member = [n for n in z.namelist() if n.endswith("_corr.tif")][0]
        out = TMP / f"{tag}_{Path(member).name}"
        if not out.exists():
            out.write_bytes(z.read(member))
        return str(out)


pre_tif = corr_from_zip(PRE, "pre")
ctl_tif = corr_from_zip(CTL, "ctl")
print("prepost corr:", Path(pre_tif).name)
print("control corr:", Path(ctl_tif).name)

# сетка по prepost
ds = gdal.Open(pre_tif)
gt, proj_wkt = ds.GetGeoTransform(), ds.GetProjection()
w, h = ds.RasterXSize, ds.RasterYSize
coh_pre = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
ds = None
ds = gdal.Open(ctl_tif)
coh_ctl = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
if (ds.GetGeoTransform() != gt) or (ds.RasterXSize != w):
    # выровнять контроль на сетку prepost
    mem = "/vsimem/ctl_warped.tif"
    gdal.Warp(mem, ctl_tif, format="GTiff", width=w, height=h,
              outputBounds=(gt[0], gt[3] + h * gt[5], gt[0] + w * gt[1], gt[3]),
              dstSRS=proj_wkt, resampleAlg="near")
    ds2 = gdal.Open(mem)
    coh_ctl = ds2.GetRasterBand(1).ReadAsArray().astype(np.float64)
    ds2 = None
ds = None

valid = (coh_pre > 0) & (coh_pre < 1) & (coh_ctl > 0) & (coh_ctl < 1)

union_native, n_parts = step12.event_union(EID)
proj = osr.SpatialReference(proj_wkt)
union = step12.to_srs(union_native, proj)
ref_mask = step12.rasterize(union, gt, w, h, proj_wkt) > 0
buf_mask = step12.rasterize(union.Buffer(step12.BUFFER_M), gt, w, h, proj_wkt) > 0
other_mask, n_other = step12.all_windthrow_mask(union_native, gt, w, h, proj_wkt)
bg_mask = buf_mask & ~ref_mask & ~(other_mask > 0) & valid

dcoh = coh_ctl - coh_pre
res = {}
for name, mask in (("ref", ref_mask & valid), ("bg", bg_mask)):
    a, b = coh_pre[mask], coh_ctl[mask]
    res[name] = {
        "n": int(mask.sum()),
        "coh_prepost_median": round(float(np.median(a)), 4),
        "coh_control_median": round(float(np.median(b)), 4),
        "dcoh_median": round(float(np.median(dcoh[mask])), 4),
        "coh_prepost_mean": round(float(a.mean()), 4),
        "coh_control_mean": round(float(b.mean()), 4),
    }
print(json.dumps(res, ensure_ascii=False, indent=1))

# геометрия/ландшафт: bbox полигона и высота
env = union.GetEnvelope()
print(f"bbox (проекция): {env[0]:.0f} {env[1]:.0f} {env[2]:.0f} {env[3]:.0f}, "
      f"частей: {n_parts}, прочих ветровалов в кольце: {n_other}")

# WorldCover 2021 лес в референсе (как в screening) — через cache landscape
lc = json.load(open("/home/z/my-project/sar_windthrow/data_cache/sites_landscape.json"))
for row in (lc if isinstance(lc, list) else lc.get("sites", [])):
    if (row.get("id") or row.get("shikhov_id")) == EID:
        print("landscape кэш:", json.dumps(row, ensure_ascii=False)[:400])
        break
