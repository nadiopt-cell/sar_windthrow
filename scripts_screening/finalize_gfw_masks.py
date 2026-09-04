#!/usr/bin/env python3
"""Финализация эксперимента с масками: GeoJSON v3 (атрибуты GFW/WC +
результаты пересчёта) + копия results JSON в repo + сводная таблица."""
import json
import shutil
from pathlib import Path

REPO = Path("/home/z/my-project/sar_windthrow")
GEO = REPO / "gis" / "windthrow_sites_map_2026-09-04.geojson"
GEO3 = REPO / "gis" / "windthrow_sites_map_gfw_2026-09-04_v3.geojson"
STATS = REPO / "data_cache" / "gfw_masks_stats_2026-09-04.json"
RESCORE = Path("/home/z/my-project/work_data/gfw_rescore/gfw_rescore_results.json")
RESCORE_OUT = REPO / "results" / "gfw_mask_rescore_2026-09-04.json"

stats = {s["id"]: s.get("stats", {}) for s in json.loads(STATS.read_text())["sites"]}
rescore = json.loads(RESCORE.read_text())

# --- итоговый results JSON --------------------------------------------
summary = {
    "step": "gfw_mask_sensitivity",
    "title": ("Чувствительность coh_delta к лесной маске фона: без маски (изд.6) "
              "vs Hansen GFC v1.12 на год события vs WorldCover-2021"),
    "generated": "2026-09-04",
    "mask_source": "Hansen/UMD GFC v1.12 (2000-2024), tau=30%, res 30 м -> 80 м average, порог доли 0.5",
    "recipe": {
        "forest_cand@Y": "treecover2000>=30 AND NOT(1<=lossyear<=Y-2001)",
        "forest_bg@Y": "treecover2000>=30 AND NOT(1<=lossyear<=Y-2000)",
    },
    "baseline_check": ("v0 всех 12 событий воспроизводит изд.6 бит-в-бит "
                       "(волна-2 из wave2_coh_delta, старые 7 из step12c did_dodo)"),
    "events": {},
}
for key, r in sorted(rescore.items(), key=lambda kv: kv[1].get("event_id", 0)):
    eid = r["event_id"]
    st = stats.get(eid, {})
    v = r.get("variants", {})
    summary["events"][key] = {
        "date": r.get("date"), "type": r.get("type"),
        "gfw": {k: st.get(k) for k in ("forest_cand_frac", "forest_bg_frac",
                                       "loss_in_Y_frac", "ring_forest_cand_frac",
                                       "treecover_med_poly", "wc2021_forest_frac")},
        "auc": {k: v.get(k, {}).get("auc") for k in v},
        "excess_median": {k: v.get(k, {}).get("excess_median") for k in v},
        "bg_px": r.get("bg_shrink"),
        "ref_forest_coverage": r.get("ref_forest_coverage"),
    }
vals = lambda k: [e["auc"][k] for e in summary["events"].values()  # noqa: E731
                  if e["auc"].get(k) is not None]
summary["summary"] = {
    "mean_auc_v0_no_mask": round(sum(vals("v0_no_mask")) / len(vals("v0_no_mask")), 4),
    "mean_auc_v1_gfw": round(sum(vals("v1_gfw_forest@Y")) / len(vals("v1_gfw_forest@Y")), 4),
    "mean_auc_v2_wc2021": (round(sum(vals("v2_wc2021")) / len(vals("v2_wc2021")), 4)
                           if vals("v2_wc2021") else None),
    "note_v2": ("id655 исключён из среднего v2: WC-2021-маска оставила 0 пикселей "
                "фона (округа ветровала 2017 в WC-2021 классифицирована как "
                "не-лес) — маска «из будущего» ломает метод"),
}
RESCORE_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print("results ->", RESCORE_OUT)
print(json.dumps(summary["summary"], ensure_ascii=False))

# --- GeoJSON v3 --------------------------------------------------------
fc = json.loads(GEO.read_text())
for feat in fc["features"]:
    p = feat["properties"]
    eid = p.get("shikhov_id") or p.get("id")
    st = stats.get(eid)
    if st:
        p["forest_mask_gfw"] = {
            "source": "Hansen GFC v1.12 treecover2000+lossyear, tau=30, 30 м",
            "epoch": f"{eid and p.get('year')} (год события)",
            "forest_cand_frac": st.get("forest_cand_frac"),
            "forest_bg_frac": st.get("forest_bg_frac"),
            "loss_in_event_year_frac": st.get("loss_in_Y_frac"),
            "ring_forest_cand_frac": st.get("ring_forest_cand_frac"),
            "treecover_med": st.get("treecover_med_poly"),
            "outside_gfw_forest": st.get("outside_gfw_forest"),
        }
        p["forest_mask_wc2021"] = {"forest_frac": st.get("wc2021_forest_frac"),
                                   "source": st.get("wc_source")}
    r = rescore.get(f"id{eid}")
    if r and "variants" in r:
        p["coh_delta_mask_sensitivity"] = {
            "auc_v0_no_mask": r["variants"]["v0_no_mask"].get("auc"),
            "auc_v1_gfw@Y": r["variants"]["v1_gfw_forest@Y"].get("auc"),
            "auc_v2_wc2021": r["variants"]["v2_wc2021"].get("auc"),
        }
fc["meta"] = {"mask_experiment": "gfw_mask_sensitivity 2026-09-04",
             "note": "v3: добавлены forest_mask_gfw / forest_mask_wc2021 / coh_delta_mask_sensitivity"}
GEO3.write_text(json.dumps(fc, ensure_ascii=False))
print("geojson v3 ->", GEO3, f"({GEO3.stat().st_size//1024} KB)")
