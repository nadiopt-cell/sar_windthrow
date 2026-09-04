#!/usr/bin/env python3
"""coh_delta (DiD) анализ волны-2 для 5 событий: id579/654/658/683/696.

Запуск: /usr/bin/python3 wave2_coh_delta.py [id ...]   (без аргументов — все 5)
Детектор — код плагина v1.0 (CoherenceDeltaDetector.detect_file), вход —
HyP3 .zip (corr-слой извлекается автоматически). Статистика — хелперы
step12 (эталон Shikhov Windthrows.shp, фон = кольцо 10 км минус прочие
ветровалы), AUC Манна-Уитни, excess median, TPR@FPR5%.
Промежуточный JSON сохраняется после каждого события (можно перезапускать).
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
from osgeo import gdal

REPO = "/home/z/my-project/sar_windthrow"
MANIFEST = Path("/home/z/my-project/download/hyp3_download_links_2026-09-04.json")
ZIPDIR = Path("/home/z/my-project/download/hyp3_products_wave2")
OUT_BASE = Path("/home/z/my-project/work_data/wave2")
RESULTS = OUT_BASE / "wave2_coh_delta_results.json"

sys.path.insert(0, f"{REPO}/qgis_plugin")
from sentinel1_windthrow_plugin.sources.coh_delta import CoherenceDeltaDetector  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "step12", f"{REPO}/pipeline/step12_coherence_analysis.py")
step12 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step12)

EVENT_META = {  # даты/тип из data_cache/sites_cohort.json (date_1)
    579: {"date": "2015-07-02", "type": "торнадо", "platform": "S1A"},
    654: {"date": "2017-07-16", "type": "торнадо", "platform": "S1B"},
    658: {"date": "2017-07-29", "type": "шквал", "platform": "S1B"},
    683: {"date": "2017-08-01", "type": "торнадо", "platform": "S1B"},
    696: {"date": "2017-07-23", "type": "шквал", "platform": "S1A"},
}

# Пары (prepost, control) по кэшу отбора sites_cohort.json. ВАЖНО: в заказе
# HyP3 имена джоб id683/id696 перепутаны (id683-coh-* содержит цепочку id696
# и наоборот) — поэтому маппинг задан явно по гранулам цепочек, а не по
# именам джоб.
EVENT_ZIPS = {
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


def event_zips(eid: int):
    """zip-пути (prepost, control) события — явный маппинг по цепочкам."""
    pre, ctl = EVENT_ZIPS[eid]
    for p in (pre, ctl):
        if not (ZIPDIR / p).exists():
            raise RuntimeError(f"id{eid}: нет файла {p}")
    return ZIPDIR / pre, ZIPDIR / ctl


def analyze(eid: int) -> dict:
    pre_zip, ctl_zip = event_zips(eid)
    print(f"\n=== id{eid} ({EVENT_META[eid]['type']} {EVENT_META[eid]['date']}) ===")
    print(f"  prepost: {pre_zip.name}")
    print(f"  control: {ctl_zip.name}")

    det = CoherenceDeltaDetector(min_pixels=6, median_filter_size=3)
    t0 = time.time()
    res = det.detect_file(
        prepost_products=[str(pre_zip)],
        control_products=[str(ctl_zip)],
        output_base=str(OUT_BASE / f"id{eid}_did"),
    )
    print(f"  детектор: {time.time()-t0:.0f} с | порог {res['threshold']:.3f}, "
          f"mean_dcoh {res['mean_dcoh']:.3f}, объектов {res['n_objects']}, "
          f"вода-маска проигнорирована: {bool(res['water_mask_ignored'])}")

    # --- статистика на dcoh-растре детектора ---------------------------
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
    buf_mask = step12.rasterize(union.Buffer(step12.BUFFER_M), gt, w, h, proj_wkt) > 0
    other_mask, n_other = step12.all_windthrow_mask(union_native, gt, w, h, proj_wkt)
    bg_mask = buf_mask & ~ref_mask & ~(other_mask > 0) & valid
    ref_v, bg_v = dcoh[ref_mask & valid], dcoh[bg_mask]

    out = {
        "event_id": eid,
        **EVENT_META[eid],
        "prepost_zip": pre_zip.name,
        "control_zip": ctl_zip.name,
        "ref_parts": int(n_parts),
        "ref_pixels": int(ref_v.size),
        "bg_pixels": int(bg_v.size),
        "other_windthrow_parts_excluded": int(n_other),
        "detector": {"threshold": round(float(res["threshold"]), 4),
                     "mean_dcoh": round(float(res["mean_dcoh"]), 4),
                     "n_objects": int(res["n_objects"]),
                     "water_mask_ignored": [str(x) for x in res["water_mask_ignored"]]},
        "dcoh": res["dcoh"], "mask": res["mask"], "vector": res["vector"],
    }
    if ref_v.size and bg_v.size:
        out["excess_median"] = round(float(np.median(ref_v) - np.median(bg_v)), 4)
        out["auc"] = round(float(step12.auc_mw(bg_v, ref_v)), 4)
        t5 = float(np.percentile(bg_v, 95))
        out["tpr_at_fpr5pct"] = round(float((ref_v >= t5).mean()), 4)
        out["median_ref"] = round(float(np.median(ref_v)), 4)
        out["median_bg"] = round(float(np.median(bg_v)), 4)
    print(json.dumps({k: v for k, v in out.items()
                      if k in ("auc", "excess_median", "tpr_at_fpr5pct",
                               "ref_pixels", "bg_pixels", "median_ref", "median_bg")},
                     ensure_ascii=False))
    return out


def main():
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    ids = [int(a) for a in sys.argv[1:]] or sorted(EVENT_META)
    for eid in ids:
        key = f"id{eid}"
        if key in results and results[key].get("auc") is not None:
            print(f"[skip] {key}: уже посчитан (auc={results[key]['auc']})")
            continue
        try:
            results[key] = analyze(eid)
        except Exception as e:
            print(f"[FAIL] {key}: {type(e).__name__}: {e}")
            results[key] = {"event_id": eid, "error": f"{type(e).__name__}: {e}"}
        RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=1))
        print(f"[сохранено] {RESULTS}")
    print("\n=== сводка ===")
    for key, r in sorted(results.items()):
        if "error" in r:
            print(f"{key}: ОШИБКА {r['error'][:80]}")
        else:
            print(f"{key}: AUC {r['auc']}, excess {r['excess_median']}, "
                  f"TPR@FPR5 {r['tpr_at_fpr5pct']}, ref_px {r['ref_pixels']}")


if __name__ == "__main__":
    main()
