#!/usr/bin/env python3
"""Фаза B: пересчёт coh_delta (DiD) для 12 событий с лесными масками фона.

Варианты фона (кольцо 10 км минус референс минус прочие ветровалы, как в ed.6):
  v0 — без лесной маски (baseline изд.6)
  v1 — фон AND GFW-лес@Y      (Hansen GFC v1.12, реконструкция на год события)
  v2 — фон AND WC-2021-лес    (маска «из будущего» — изолирует эффект эпохи)

Маски 30 м -> сетка dcoh 80 м (average), пиксель фона = лес при доле >= 0.5.
AUC Манна-Уитни, excess median, TPR@FPR5% — из step12.

Запуск: /usr/bin/python3 gfw_rescore.py [id ...]   (инкрементально, можно
перезапускать; готовые события пропускаются)
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()
gdal.SetCacheMax(64_000_000)

REPO = Path("/home/z/my-project/sar_windthrow")
sys.path.insert(0, str(REPO / "qgis_plugin"))
from sentinel1_windthrow_plugin.sources.coh_delta import CoherenceDeltaDetector  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "step12", str(REPO / "pipeline" / "step12_coherence_analysis.py"))
step12 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step12)

ZIPDIR = Path("/home/z/my-project/download/hyp3_products_all")
GFW = Path("/home/z/my-project/work_data/gfw")
OUT_BASE = Path("/home/z/my-project/work_data/gfw_rescore")
OUT_BASE.mkdir(parents=True, exist_ok=True)
RESULTS = OUT_BASE / "gfw_rescore_results.json"

EVENT_META = {  # даты/тип/платформа (sites_cohort + step12c)
    666: {"date": "2017-07-30", "type": "шквал", "platform": "S1B"},
    694: {"date": "2017-09-19", "type": "торнадо", "platform": "S1B"},
    655: {"date": "2017-07-30", "type": "шквал", "platform": "S1B"},
    583: {"date": "2015-07-26", "type": "шквал", "platform": "S1A"},
    674: {"date": "2017-08-02", "type": "торнадо", "platform": "S1B"},
    608: {"date": "2016-07-13", "type": "торнадо", "platform": "S1A"},
    646: {"date": "2017-05-29", "type": "шквал", "platform": "S1B"},
    579: {"date": "2015-07-02", "type": "торнадо", "platform": "S1A"},
    654: {"date": "2017-07-16", "type": "торнадо", "platform": "S1B"},
    658: {"date": "2017-07-29", "type": "шквал", "platform": "S1B"},
    683: {"date": "2017-08-01", "type": "торнадо", "platform": "S1B"},
    696: {"date": "2017-07-23", "type": "шквал", "platform": "S1A"},
}

EVENT_ZIPS = {
    666: ("S1BB_20170722T033447_20170803T033448_VVP012_INT80_G_ueF_C4A3.zip",
          "S1BB_20170628T033446_20170710T033446_VVP012_INT80_G_ueF_4CD2.zip"),
    694: ("S1BB_20170912T030234_20170924T030234_VVP012_INT80_G_ueF_5748.zip",
          "S1BB_20170924T030234_20171006T030235_VVP012_INT80_G_ueF_5C8E.zip"),
    655: ("S1BB_20170724T031922_20170805T031922_VVP012_INT80_G_ueF_5AA7.zip",
          "S1BB_20170805T031922_20170817T031923_VVP012_INT80_G_ueF_5C98.zip"),
    583: ("S1AA_20150716T041723_20150728T041724_VVP012_INT80_G_ueF_6635.zip",
          "S1AA_20150728T041724_20150809T041725_VVP012_INT80_G_ueF_7C23.zip"),
    674: ("S1BB_20170730T040914_20170811T040915_VVP012_INT80_G_ueF_0473.zip",
          "S1BB_20170811T040915_20170823T040915_VVP012_INT80_G_ueF_AB7F.zip"),
    608: ("S1AA_20160702T034515_20160714T034516_VVP012_INT80_G_ueF_DC49.zip",
          "S1AA_20160714T034516_20160726T034517_VVP012_INT80_G_ueF_8850.zip"),
    646: ("S1BB_20170523T033624_20170604T033625_VVP012_INT80_G_ueF_105D.zip",
          "S1BB_20170604T033625_20170616T033625_VVP012_INT80_G_ueF_B8C4.zip"),
    579: ("S1AA_20150622T041605_20150704T041604_VVP012_INT80_G_ueF_07A7.zip",
          "S1AA_20150704T041604_20150716T041605_VVP012_INT80_G_ueF_8A5D.zip"),
    654: ("S1BB_20170712T031830_20170724T031831_VVP012_INT80_G_ueF_3A90.zip",
          "S1BB_20170724T031831_20170805T031832_VVP012_INT80_G_ueF_4FDF.zip"),
    658: ("S1BB_20170722T033447_20170803T033448_VVP012_INT80_G_ueF_4BC7.zip",
          "S1BB_20170803T033448_20170815T033448_VVP012_INT80_G_ueF_082D.zip"),
    683: ("S1BB_20170730T040849_20170811T040850_VVP012_INT80_G_ueF_E9F3.zip",
          "S1BB_20170811T040850_20170823T040850_VVP012_INT80_G_ueF_168D.zip"),
    696: ("S1AA_20170712T041007_20170724T041008_VVP012_INT80_G_ueF_083E.zip",
          "S1AA_20170724T041008_20170805T041008_VVP012_INT80_G_ueF_DB8F.zip"),
}


def warp_mask_frac(src: Path, gt, w, h, proj_wkt) -> np.ndarray:
    """Маска 30 м -> сетка dcoh (average -> доля леса 0..1)."""
    mem = "/vsimem/_mask_frac.tif"
    gdal.Warp(mem, str(src), format="GTiff", dstSRS=proj_wkt,
              outputBounds=(gt[0], gt[3] + h * gt[5], gt[0] + w * gt[1], gt[3]),
              width=w, height=h, resampleAlg="average",
              outputType=gdal.GDT_Float32, warpMemoryLimit=128_000_000,
              multithread=False)
    ds = gdal.Open(mem)
    arr = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    gdal.Unlink(mem)
    return arr


def metrics(ref_v, bg_v):
    if not (ref_v.size and bg_v.size):
        return {}
    t5 = float(np.percentile(bg_v, 95))
    return {"auc": round(float(step12.auc_mw(bg_v, ref_v)), 4),
            "excess_median": round(float(np.median(ref_v) - np.median(bg_v)), 4),
            "tpr_at_fpr5pct": round(float((ref_v >= t5).mean()), 4),
            "median_ref": round(float(np.median(ref_v)), 4),
            "median_bg": round(float(np.median(bg_v)), 4),
            "ref_px": int(ref_v.size), "bg_px": int(bg_v.size)}


def analyze(eid: int) -> dict:
    pre_zip, ctl_zip = EVENT_ZIPS[eid]
    for p in (pre_zip, ctl_zip):
        if not (ZIPDIR / p).exists():
            raise RuntimeError(f"id{eid}: нет {p}")
    print(f"=== id{eid} ({EVENT_META[eid]['type']} {EVENT_META[eid]['date']}) ===",
          flush=True)

    det = CoherenceDeltaDetector(min_pixels=6, median_filter_size=3)
    t0 = time.time()
    res = det.detect_file(
        prepost_products=[str(ZIPDIR / pre_zip)],
        control_products=[str(ZIPDIR / ctl_zip)],
        output_base=str(OUT_BASE / f"id{eid}_did"))
    print(f"  детектор {time.time()-t0:.0f} с: порог {res['threshold']:.3f}, "
          f"объектов {res['n_objects']}", flush=True)

    ds = gdal.Open(res["dcoh"])
    w, h = ds.RasterXSize, ds.RasterYSize
    gt, proj_wkt = ds.GetGeoTransform(), ds.GetProjection()
    dcoh = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    ds = None
    valid = dcoh != -9999.0

    union_native, n_parts = step12.event_union(eid)
    proj = __import__("osgeo").osr.SpatialReference(proj_wkt)
    union = step12.to_srs(union_native, proj)
    ref_mask = step12.rasterize(union, gt, w, h, proj_wkt) > 0
    buf_mask = step12.rasterize(union.Buffer(step12.BUFFER_M), gt, w, h,
                                proj_wkt) > 0
    other_mask, n_other = step12.all_windthrow_mask(union_native, gt, w, h,
                                                    proj_wkt)
    bg0 = buf_mask & ~ref_mask & ~(other_mask > 0) & valid

    # маски 30 м -> 80 м (доля леса)
    fbg80 = warp_mask_frac(GFW / f"id{eid}_fbg30.tif", gt, w, h, proj_wkt) >= 0.5
    fcand80 = warp_mask_frac(GFW / f"id{eid}_fcand30.tif", gt, w, h,
                             proj_wkt) >= 0.5
    wc80 = warp_mask_frac(GFW / f"id{eid}_wc30.tif", gt, w, h, proj_wkt) >= 0.5

    out = {"event_id": eid, **EVENT_META[eid],
           "prepost_zip": pre_zip, "control_zip": ctl_zip,
           "ref_parts": int(n_parts),
           "other_windthrow_parts_excluded": int(n_other),
           "detector": {"threshold": round(float(res["threshold"]), 4),
                        "n_objects": int(res["n_objects"])},
           "variants": {}}

    ref0 = ref_mask & valid
    out["variants"]["v0_no_mask"] = metrics(dcoh[ref0], dcoh[bg0])
    bg1 = bg0 & fbg80
    out["variants"]["v1_gfw_forest@Y"] = metrics(dcoh[ref0], dcoh[bg1])
    bg2 = bg0 & wc80
    out["variants"]["v2_wc2021"] = metrics(dcoh[ref0], dcoh[bg2])

    # покрытие референса лесом (GFW@Y vs WC-2021)
    npoly = int(ref0.sum())
    out["ref_forest_coverage"] = {
        "gfw_cand@Y": round(float((ref0 & fcand80).sum()) / max(npoly, 1), 4),
        "wc2021": round(float((ref0 & wc80).sum()) / max(npoly, 1), 4),
        "ref_px": npoly}
    out["bg_shrink"] = {"v0": int(bg0.sum()), "v1_gfw": int(bg1.sum()),
                        "v2_wc2021": int(bg2.sum())}
    for k, v in out["variants"].items():
        print(f"  {k}: AUC {v.get('auc')} excess {v.get('excess_median')} "
              f"bg_px {v.get('bg_px')}", flush=True)
    return out


def main():
    ids = [int(a) for a in sys.argv[1:]] or sorted(EVENT_META)
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    for eid in ids:
        key = f"id{eid}"
        if key in results and "variants" in results[key]:
            print(f"[skip] {key}")
            continue
        try:
            results[key] = analyze(eid)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {key}: {type(e).__name__}: {e}", flush=True)
            results[key] = {"event_id": eid,
                            "error": f"{type(e).__name__}: {e}"}
        RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("\n=== сводка (AUC: v0 -> v1 GFW@Y -> v2 WC-2021) ===")
    for key, r in sorted(results.items()):
        if "error" in r:
            print(f"{key}: ОШИБКА {r['error'][:70]}")
            continue
        v = r["variants"]
        f = lambda x: v.get(x, {}).get("auc")  # noqa: E731
        print(f"{key}: {f('v0_no_mask')} -> {f('v1_gfw_forest@Y')} -> "
              f"{f('v2_wc2021')} (bg {r['bg_shrink']['v0']}/"
              f"{r['bg_shrink']['v1_gfw']}/{r['bg_shrink']['v2_wc2021']})")


if __name__ == "__main__":
    main()
