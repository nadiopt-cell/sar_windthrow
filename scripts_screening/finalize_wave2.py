#!/usr/bin/env python3
"""Финализация волны-2: диагноз в results JSON + обновление GeoJSON
(5 shortlisted -> processed с метриками coh_delta)."""
import json
from pathlib import Path

RES = Path("/home/z/my-project/work_data/wave2/wave2_coh_delta_results.json")
GEO_OLD = Path("/home/z/my-project/sar_windthrow/gis/windthrow_sites_map_2026-09-04.geojson")
GEO_NEW = Path("/home/z/my-project/download/windthrow_sites_map_2026-09-04_v2.geojson")

results = json.loads(RES.read_text())

# --- 1. диагноз id654 + заметка id696 в results JSON ------------------
results["id654"]["diagnosis"] = {
    "coh_prepost_median_ref": 0.6353,
    "coh_control_median_ref": 0.2846,
    "coh_prepost_median_bg": 0.5192,
    "coh_control_median_bg": 0.5854,
    "verdict": ("Внутри полигона когерентность prepost-пары 0.635 — сигнатура "
                "голого грунта, а не стоящего леса (лес даёт 0.3-0.5): на дату "
                "события участок уже был безлесным, след торнадо 16.07.2017 в "
                "когерентности отсутствует. Инверсия DiD вызвана изменением в "
                "КОНТРОЛЬНОМ окне (24.07-05.08, coh 0.285) — зарастание/уборка. "
                "WorldCover-2021 показывает 95-100% леса — территория заросла "
                "за 4 года; скринингу нужна растительность на дату события."),
}
results["id696"]["note"] = ("Шквал 0.65 км2, ширина следа сравнима с пикселем 80 м "
                            "(106 ref-пикселей) — размывание сигнала, как в уроке "
                            "step12c для узких треков; сигнал отсутствует "
                            "(excess -0.014).")
RES.write_text(json.dumps(results, ensure_ascii=False, indent=1))
print("results JSON обновлён:", RES)

# --- 2. GeoJSON: 5 shortlisted -> processed ---------------------------
SRC = GEO_NEW if GEO_NEW.exists() else GEO_OLD   # v2 уже частично обновлён — идем от неё
fc = json.loads(SRC.read_text())
n_moved = 0
for feat in fc["features"]:
    p = feat["properties"]
    eid = p.get("shikhov_id") or p.get("id")
    key = f"id{eid}"
    if p.get("status") == "shortlisted" and key in results and "auc" in results[key]:
        r = results[key]
        p["status"] = "processed"
        p["coh_delta"] = {
            "auc": r["auc"],
            "excess_median": r["excess_median"],
            "tpr_at_fpr5pct": r["tpr_at_fpr5pct"],
            "ref_pixels": r["ref_pixels"],
            "prepost": r["prepost_zip"],
            "control": r["control_zip"],
            "event_date": r["date"],
            "event_type": r["type"],
        }
        n_moved += 1
    if key == "id654" and p.get("status") == "processed":
        p["coh_delta_note"] = ("AUC 0.051 инверсный: полигон на дату события "
                               "безлесный (coh prepost 0.635) — см. диагноз")
    if key == "id606" and p.get("status") == "shortlisted":
        p["status"] = "reserve"
        p["reserve_note"] = ("Исключён из волны-2: SLC-цепочка stretched (24 дня), "
                             "полной пары для 18.06.2016 нет; заменён событием "
                             "id696 (шквал 23.07.2017, full-цепочка S1A)")

# id696: новая фича processed (в старом GeoJSON её нет — когорта заменила id606)
if not any((f["properties"].get("shikhov_id") or f["properties"].get("id")) == 696
           for f in fc["features"]):
    base = json.loads(Path("/home/z/my-project/sar_windthrow/data_cache/sites_base.json").read_text())
    src = next(c for c in base["candidates"] if c["id"] == 696)
    r = results["id696"]
    from shapely import wkt as _wkt
    geom = _wkt.loads(src["wkt"]).__geo_interface__
    fc["features"].append({
        "type": "Feature",
        "geometry": geom,
        "properties": {
            "id": 696, "storm_id": src["storm_id"], "type": src["type"],
            "status": "processed",
            "area_km2": src["area_km2"], "length_km": src["length_km"],
            "mean_width_m": src["mean_width_m"], "max_width_m": src["max_width_m"],
            "date_1": src["date_1"], "lon": src["lon"], "lat": src["lat"],
            "coh_delta": {
                "auc": r["auc"], "excess_median": r["excess_median"],
                "tpr_at_fpr5pct": r["tpr_at_fpr5pct"], "ref_pixels": r["ref_pixels"],
                "prepost": r["prepost_zip"], "control": r["control_zip"],
                "event_date": r["date"], "event_type": r["type"],
            },
            "coh_delta_note": r.get("note", ""),
        },
    })
    print("добавлена фича id696 (processed)")
n_moved = sum(1 for f in fc["features"]
              if f["properties"].get("status") == "processed"
              and f["properties"].get("coh_delta"))
json.dump(fc, open(GEO_NEW, "w"), ensure_ascii=False)
print(f"GeoJSON v2: {n_moved} фич с coh_delta; файл {GEO_NEW.stat().st_size} байт")

# статистика по всем
from collections import Counter
print(Counter(f["properties"].get("status") for f in fc["features"]))
