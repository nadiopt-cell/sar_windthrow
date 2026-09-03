#!/usr/bin/env python3
"""step12c: до-до контроль для ID666 + DiD на 5 новых событиях.

Мотивация: у ID666 пост-пост контроль (03.08->15.08) даёт DiD AUC 0.671,
у ступени два источника размытия:
  (1) фон prepost-пары загрязнён ветровалами того же шквала 30.07.2017
      (bg median 0.349 против 0.739 в контроле);
  (2) сама пост-пост пара не является чистым "no-change" базлайном для
      ref: поверхность ветровала (дебрис) в окне контроля менее когерентна,
      чем лес (ref 0.634 против bg 0.739), что смещает dcoh(ref) вниз.

До-до пара (обе даты ДО шторма) закрывает оба пункта: в её окне ref ещё
стоитщий лес, тот же класс поверхности, что и фон.

Анализ повторяет step12b (sampling, AUC MW, Youden, TPR@FPR5%), но:
  - пути продуктов резолвятся rglob-ом по имени (раскладка <job>/<prod>);
  - для ID666 добавляются пара dodo и DiD с до-до контролем;
  - новые события: ID655, ID583, ID674, ID608, ID646 (prepost+control).

Запуск: /usr/bin/python3 scripts/step12c_analysis.py [--offline]
"""
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()

REPO = "/home/z/my-project/plugin_work/sar_windthrow_repo"
PROD = f"{REPO}/work_data/hyp3_products"
JOBS_JSON = f"{REPO}/work_data/hyp3_jobs_list.json"
OUT_JSON = f"{REPO}/results/step12c_did_extension_{datetime.utcnow():%Y-%m-%d}.json"
TODAY = f"{datetime.utcnow():%Y-%m-%d}"

BUFFER_M = 10_000.0
EVENT_DATE = {
    666: "2017-07-30", 694: "2017-09-19", 655: "2017-07-30",
    583: "2015-07-26", 674: "2017-08-02", 608: "2016-07-13",
    646: "2017-05-29",
}

# шаг 0: подгрузить step12 как standalone-модуль (общие векторные/стат функции)
spec = importlib.util.spec_from_file_location(
    "step12", f"{REPO}/pipeline/step12_coherence_analysis.py")
step12 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step12)

event_union = step12.event_union
to_srs = step12.to_srs
rasterize = step12.rasterize
all_windthrow_mask = step12.all_windthrow_mask
rankdata_avg = step12.rankdata_avg
auc_mw = step12.auc_mw
block_stats = step12.block_stats
detection_metrics = step12.detection_metrics


def base_of(product):
    """Резолвер путёй: <продукт>_corr.tif в любой раскладке hyp3_products."""
    hits = sorted(Path(PROD).rglob(f"{product}_corr.tif"))
    if not hits:
        raise FileNotFoundError(f"{product}_corr.tif не найден под {PROD}")
    return str(hits[0])[: -len("_corr.tif")]


def analyse_pair(ref_id, pair, product):
    """Копия step12.analyse_pair с резолвером путей (rglob)."""
    base = base_of(product)
    corr_p, water_p = f"{base}_corr.tif", f"{base}_water_mask.tif"

    ds = gdal.Open(corr_p)
    w, h = ds.RasterXSize, ds.RasterYSize
    gt, proj = ds.GetGeoTransform(), osr.SpatialReference(ds.GetProjection())
    coh = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    ds = None
    valid = np.isfinite(coh) & (coh > 0)
    wds = gdal.Open(water_p)
    water = wds.GetRasterBand(1).ReadAsArray()
    wds = None
    water_frac = float((water == 1).mean())
    mask_ok = water_frac < 0.5

    union_native, n_parts = event_union(ref_id)
    union = to_srs(union_native, proj)
    ref_mask = rasterize(union, gt, w, h, proj.ExportToWkt())
    buf_mask = rasterize(union.Buffer(BUFFER_M), gt, w, h, proj.ExportToWkt())
    other_mask, n_other = all_windthrow_mask(union_native, gt, w, h, proj.ExportToWkt())
    if mask_ok:
        land = water == 0
        bg_mask = buf_mask & ~ref_mask & ~other_mask & land & valid
        ref_valid = ref_mask & valid & land
    else:
        bg_mask = buf_mask & ~ref_mask & ~other_mask & valid
        ref_valid = ref_mask & valid

    ref_coh, bg_coh = coh[ref_valid], coh[bg_mask]
    res = {
        "pair": pair, "product": product,
        "ref_parts_in_shp": n_parts,
        "grid": {"w": w, "h": h, "posting_m": abs(gt[1]),
                 "epsg": proj.GetAuthorityCode(None)},
        "ref": block_stats(ref_coh), "background": block_stats(bg_coh),
        "water_mask_used": bool(mask_ok),
        "water_mask_flagged_fraction": round(water_frac, 4),
        "other_windthrow_parts_excluded": n_other,
    }
    if res["ref"] and res["background"]:
        res["contrast_median"] = round(
            res["background"]["median"] - res["ref"]["median"], 4)
        res.update(detection_metrics(ref_coh, bg_coh))
    else:
        res["contrast_median"] = None
    return res


def analyse_did(ref_id, prepost_prod, control_prod):
    """dcoh = coh(control) - coh(prepost); копия step12 с резолвером путей."""
    pre_path = f"{base_of(prepost_prod)}_corr.tif"
    ctl_path = f"{base_of(control_prod)}_corr.tif"

    ds = gdal.Open(pre_path)
    w, h = ds.RasterXSize, ds.RasterYSize
    gt, proj_wkt = ds.GetGeoTransform(), ds.GetProjection()
    proj = osr.SpatialReference(proj_wkt)
    coh_pre = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    ds = None
    ds = gdal.Open(ctl_path)
    coh_ctl = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    gt_ctl = ds.GetGeoTransform()
    ds = None

    same_grid = gt_ctl == gt and coh_ctl.shape == coh_pre.shape
    if not same_grid:
        ds = gdal.Warp("", ctl_path, format="MEM", width=w, height=h,
                       outputBounds=(gt[0], gt[3] + h * gt[5],
                                     gt[0] + w * gt[1], gt[3]),
                       resampleAlg="bilinear")
        coh_ctl = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
        ds = None
    dcoh = coh_ctl - coh_pre
    # nodata=0 в обоих растрах даёт dcoh=0 точно; у пар одной цепочки зоны
    # nodata совпадают -> масса нулей ломает медианы (баг первого прогона)
    valid = np.isfinite(dcoh) & (coh_pre > 0) & (coh_ctl > 0)

    union_native, n_parts = event_union(ref_id)
    union = to_srs(union_native, proj)
    ref_mask = rasterize(union, gt, w, h, proj_wkt)
    buf_mask = rasterize(union.Buffer(BUFFER_M), gt, w, h, proj_wkt)
    other_mask, n_other = all_windthrow_mask(union_native, gt, w, h, proj_wkt)
    bg_mask = buf_mask & ~ref_mask & ~other_mask & valid
    ref_valid = ref_mask & valid

    ref_v, bg_v = dcoh[ref_valid], dcoh[bg_mask]
    res = {
        "control_warped_to_prepost_grid": not same_grid,
        "ref": block_stats(ref_v), "background": block_stats(bg_v),
        "other_windthrow_parts_excluded": n_other,
    }
    if len(ref_v) and len(bg_v):
        res["did_excess_median"] = round(
            float(np.median(ref_v) - np.median(bg_v)), 4)
        auc = auc_mw(bg_v, ref_v)
        res["auc_dcoh"] = round(auc, 4)
        # хвост фона, "равный ветровалу": доля bg с dcoh выше медианы ref
        res["bg_tail_above_ref_median"] = round(
            float((bg_v >= np.median(ref_v)).mean()), 4)
        pool = np.concatenate([ref_v, bg_v])
        grid = np.unique(np.percentile(pool, np.linspace(0.1, 99.9, 400)))
        tpr = np.array([(ref_v >= t).mean() for t in grid])
        fpr = np.array([(bg_v >= t).mean() for t in grid])
        k = int(np.argmax(tpr - fpr))
        t5 = np.percentile(bg_v, 95)
        res["youden"] = {"thr": round(float(grid[k]), 4),
                         "tpr": round(float(tpr[k]), 4),
                         "fpr": round(float(fpr[k]), 4)}
        res["tpr_at_fpr5pct"] = round(float((ref_v >= t5).mean()), 4)
    return res


def product_of(jobs, name):
    j = jobs[name]
    fn = j["files"][0]["filename"]
    return fn[:-4] if fn.endswith(".zip") else fn


# v2-джобы: первые кадры ID674 (S1A rel108) и ID608 (южный rel64) не покрыли AOI
JOB_NAME = {}
for e in (655, 583, 646):
    for p in ("prepost", "control"):
        JOB_NAME[(e, p)] = f"id{e}-coh-{p}"
JOB_NAME[(674, "prepost")] = "id674-coh-prepost-v2"
JOB_NAME[(674, "control")] = "id674-coh-control-v2"
JOB_NAME[(608, "prepost")] = "id608-coh-prepost-v2"
JOB_NAME[(608, "control")] = "id608-coh-control-v2"


def main():
    offline = "--offline" in sys.argv
    with open(JOBS_JSON) as f:
        jobs = {j["name"]: j for j in json.load(f)["jobs"]}

    need = ["id666-coh-dodo"] + [JOB_NAME[(e, p)]
            for e in (655, 583, 674, 608, 646) for p in ("prepost", "control")]
    missing = [n for n in need if jobs.get(n, {}).get("status_code") != "SUCCEEDED"]
    if missing and not offline:
        print("[wait] ещё не готово:", ", ".join(missing))
        sys.exit(2)

    out = {
        "step": "12c",
        "title": "До-до контроль ID666 + DiD extension to 5 new events",
        "generated": datetime.utcnow().isoformat() + "Z",
        "motivation": {
            "id666_post_post_did_auc_0_671": (
                "(1) prepost background contaminated by same-derecho windthrow "
                "(bg 0.349 vs control 0.739); (2) post-post control is not a "
                "clean no-change baseline for ref: debris surface is less "
                "coherent than forest in the control window (0.634 vs 0.739)"),
            "dodo_fix": "control pair fully before the storm: ref is standing "
                        "forest there, same surface class as background",
        },
        "products": {n: {"job_id": jobs[n]["job_id"],
                         "granules": jobs[n]["job_parameters"]["granules"],
                         "product": product_of(jobs, n)} for n in need},
        "events": {},
    }

    # --- ID666: dodo pair + dodo DiD -------------------------------------
    ev = {"event_date": EVENT_DATE[666]}
    dodo_prod = product_of(jobs, "id666-coh-dodo")
    print(f"[run] ID666 dodo pair: {dodo_prod}", flush=True)
    ev["pairs"] = {"dodo": analyse_pair(666, "dodo", dodo_prod)}
    p = ev["pairs"]["dodo"]
    print(f"      ref med {p['ref'].get('median')} bg med {p['background'].get('median')} "
          f"contrast {p.get('contrast_median')} AUC {p.get('auc_score_1minuscoh')}", flush=True)
    print("[run] ID666 DID with DODO control (dodo minus prepost) ...", flush=True)
    ev["did_dodo"] = analyse_did(
        666, product_of(jobs, "id666-coh-prepost"), dodo_prod)
    d = ev["did_dodo"]
    print(f"      did excess {d.get('did_excess_median')} AUC {d.get('auc_dcoh')} "
          f"bg_tail {d.get('bg_tail_above_ref_median')} TPR@5% {d.get('tpr_at_fpr5pct')}", flush=True)
    # повтор пост-пост DiD на тех же функциях (контроль воспроизводимости)
    print("[run] ID666 DID with post-post control (re-run) ...", flush=True)
    ev["did_postpost"] = analyse_did(
        666, product_of(jobs, "id666-coh-prepost"),
        product_of(jobs, "id666-coh-control"))
    out["events"]["ID666"] = ev

    # --- новые события ----------------------------------------------------
    for eid in (655, 583, 674, 608, 646):
        ev = {"event_date": EVENT_DATE[eid], "pairs": {}}
        prods = {p: product_of(jobs, JOB_NAME[(eid, p)])
                 for p in ("prepost", "control")}
        for p, prod in prods.items():
            print(f"[run] ID{eid} {p}: {prod}", flush=True)
            ev["pairs"][p] = analyse_pair(eid, p, prod)
            r = ev["pairs"][p]
            print(f"      ref med {r['ref'].get('median')} bg med {r['background'].get('median')} "
                  f"contrast {r.get('contrast_median')} AUC {r.get('auc_score_1minuscoh')}", flush=True)
        print(f"[run] ID{eid} DID (control minus prepost) ...", flush=True)
        ev["did"] = analyse_did(eid, prods["prepost"], prods["control"])
        d = ev["did"]
        print(f"      did excess {d.get('did_excess_median')} AUC {d.get('auc_dcoh')} "
              f"bg_tail {d.get('bg_tail_above_ref_median')} TPR@5% {d.get('tpr_at_fpr5pct')}", flush=True)
        out["events"][f"ID{eid}"] = ev

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[ok ] -> {OUT_JSON}", flush=True)


if __name__ == "__main__":
    main()
