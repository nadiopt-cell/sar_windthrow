#!/usr/bin/env python3
"""Validate the v1.0 plugin CoherenceDeltaDetector on real HyP3 products.

Runs the PLUGIN code path (sources/coh_delta.py) on the ID694 and ID666
product pairs and recomputes the step12b statistics (Mann-Whitney AUC,
excess median, TPR@FPR5%) on the produced dcoh raster.  Reference
numbers from step12b (results/step12_coherence_analysis_2026-09-03.json):
    ID694: AUC 0.908, excess +0.308, TPR@FPR5% 0.55
    ID666: AUC 0.671, excess +0.140
"""
import importlib.util
import json
import sys

import numpy as np
from osgeo import gdal, ogr, osr

REPO = "/home/z/my-project/plugin_work/sar_windthrow_repo"
sys.path.insert(0, f"{REPO}/qgis_plugin")

from sentinel1_windthrow_plugin.sources.coh_delta import (  # noqa: E402
    CoherenceDeltaDetector,
)

# pipeline/step12_coherence_analysis.py has no package __init__; load it
# as a standalone module for the vector helpers + AUC.
spec = importlib.util.spec_from_file_location(
    "step12", f"{REPO}/pipeline/step12_coherence_analysis.py")
step12 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step12)

OUT_DIR = f"{REPO}/work_data/v1_validation"
PAIRS = {
    694: (f"{REPO}/work_data/hyp3_products/id694-coh-prepost",
          f"{REPO}/work_data/hyp3_products/id694-coh-control",
          {"auc": 0.908, "excess": 0.308, "tpr5": 0.55}),
    666: (f"{REPO}/work_data/hyp3_products/id666-coh-prepost",
          f"{REPO}/work_data/hyp3_products/id666-coh-control",
          {"auc": 0.671, "excess": 0.140, "tpr5": None}),
}


def validate(ref_id: int) -> dict:
    prepost, control, ref_vals = PAIRS[ref_id]
    det = CoherenceDeltaDetector(min_pixels=6, median_filter_size=3)
    result = det.detect_file(
        prepost_products=[prepost],
        control_products=[control],
        output_base=f"{OUT_DIR}/id{ref_id}_did",
    )
    print(f"\n=== ID{ref_id}: плагин ===")
    print(json.dumps(
        {k: result[k] for k in
         ("threshold", "mean_dcoh", "n_objects", "control_used",
          "water_mask_ignored")}, ensure_ascii=False, indent=1))

    # --- statistics on the plugin's own dcoh raster -------------------
    ds = gdal.Open(result["dcoh"])
    w, h = ds.RasterXSize, ds.RasterYSize
    gt, proj_wkt = ds.GetGeoTransform(), ds.GetProjection()
    dcoh = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    ds = None
    valid = dcoh != -9999.0

    union_native, _n = step12.event_union(ref_id)
    proj = osr.SpatialReference(proj_wkt)
    union = step12.to_srs(union_native, proj)
    ref_mask = step12.rasterize(union, gt, w, h, proj_wkt) > 0
    buf_mask = step12.rasterize(union.Buffer(step12.BUFFER_M), gt, w, h,
                                proj_wkt) > 0
    other_mask, n_other = step12.all_windthrow_mask(
        union_native, gt, w, h, proj_wkt)
    bg_mask = buf_mask & ~ref_mask & ~(other_mask > 0) & valid
    ref_v, bg_v = dcoh[ref_mask & valid], dcoh[bg_mask]

    res = {
        "ref_pixels": int(ref_v.size),
        "bg_pixels": int(bg_v.size),
        "other_windthrow_parts_excluded": int(n_other),
    }
    if ref_v.size and bg_v.size:
        res["excess_median"] = round(
            float(np.median(ref_v) - np.median(bg_v)), 4)
        auc = step12.auc_mw(bg_v, ref_v)
        res["auc"] = round(float(auc), 4)
        t5 = float(np.percentile(bg_v, 95))
        res["tpr_at_fpr5pct"] = round(float((ref_v >= t5).mean()), 4)
    res["reference_step12b"] = ref_vals
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def main() -> None:
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    out = {}
    for rid in (694, 666):
        out[f"id{rid}"] = validate(rid)
    with open(f"{OUT_DIR}/v1_plugin_validation.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nСохранено: {OUT_DIR}/v1_plugin_validation.json")


if __name__ == "__main__":
    main()
