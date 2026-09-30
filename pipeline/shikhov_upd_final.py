#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Финальная сводка по обновлённой БД Шихова:
1) таблица старый-новый для 13 сайтов валидации (area, дата, тип);
2) обзорная карта S1-эры (>=2014-10) с нашими сайтами;
3) шорт-лист новых кандидатов валидации 2018+ (европ. приоритет).

Выходы: upload/shikhov_upd/final_summary.json, sites_old_vs_new.csv,
shikhov_upd_overview.png, validation_shortlist.csv
"""
import json

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

SHP = "/home/z/my-project/upload/shikhov_upd/vetrovaly_3316/ветровалы_3316.shp"
SITES = "/home/z/my-project/upload/sar_windthrow/gis/windthrow_sites_map_2026-09-04.geojson"
OLD_CAND = "/home/z/my-project/upload/sar_windthrow/results/shikhov_db/s1_era_candidates.json"
MATCH = "/home/z/my-project/upload/shikhov_upd/site_matching.json"
OUTDIR = "/home/z/my-project/upload/shikhov_upd"

TYPE_MAP = {"шквал": "squall", "смерч": "tornado", "шторм": "storm", "снегопад": "snowfall"}


def main() -> None:
    gdf = gpd.read_file(SHP).to_crs(4326)
    gdf["date"] = pd.to_datetime(gdf["date"])
    gdf["year"] = gdf["date"].dt.year
    sites = gpd.read_file(SITES)
    old_cand = pd.DataFrame(json.load(open(OLD_CAND, encoding="utf-8")))
    match = {int(r["site_id"]): r for r in json.load(open(MATCH, encoding="utf-8"))}

    # --- 1. Таблица старый vs новый ---
    rows = []
    for _, s in sites.iterrows():
        sid = int(s["id"])
        oc = old_cand[old_cand["ID"] == sid]
        old_area = float(oc["Area"].iloc[0]) if len(oc) else None  # км2
        m = match[sid]
        best = m["matches"][0] if m["matches"] else None
        rows.append({
            "site_id": sid,
            "old_type": s.get("type"), "old_date": str(s.get("date")),
            "old_area_km2": old_area,
            "new_id": best["new_id"] if best else None,
            "new_type": best["type"] if best else None,
            "new_type_en": TYPE_MAP.get(best["type"]) if best else None,
            "new_date": best["date"] if best else None,
            "new_area_ha": best["area_ha"] if best else None,
            "new_area_km2": (best["area_ha"] / 100.0) if best else None,
            "iou": best["iou"] if best else None,
            "region": best["region"] if best else None,
            "date_shift_days": (
                abs((pd.Timestamp(best["date"]) - pd.to_datetime(str(s.get("date")), dayfirst=True, errors="coerce")).days)
                if best and s.get("date") and "." in str(s.get("date")) else None
            ),
        })
    tbl = pd.DataFrame(rows)
    tbl.to_csv(f"{OUTDIR}/sites_old_vs_new.csv", index=False, encoding="utf-8-sig")
    print(tbl.to_string())
    area_ratio = (tbl["new_area_km2"] / tbl["old_area_km2"]).describe()
    print("\nотношение площадей new/old:\n", area_ratio.to_string())

    # --- 2. Карта S1-эры ---
    s1 = gdf[gdf["date"] >= "2014-10-01"]
    fig, ax = plt.subplots(figsize=(13, 8), constrained_layout=True)
    colors = {"шквал": "#2166ac", "смерч": "#b2182b", "шторм": "#7fbc41", "снегопад": "#8073ac"}
    from matplotlib.lines import Line2D
    handles = []
    for t, c in colors.items():
        sub = s1[s1["type"] == t]
        if len(sub):
            sub.plot(ax=ax, color=c, markersize=2, linewidth=0, alpha=0.55)
            handles.append(Line2D([0], [0], marker="o", linestyle="", markersize=7,
                                  markerfacecolor=c, markeredgecolor="none", alpha=0.8,
                                  label=f"{t} ({TYPE_MAP[t]}, n={len(sub)})"))
    sites.plot(ax=ax, facecolor="none", edgecolor="black", linewidth=1.6,
               marker="*", markersize=210)
    handles.append(Line2D([0], [0], marker="*", linestyle="", markersize=13,
                          markerfacecolor="none", markeredgecolor="black", linewidth=1.6,
                          label="наши 13 сайтов валидации"))
    ax.set_title(f"БД Шихова (обновл.): ветровалы S1-эры 2014–2026, n={len(s1)}\n"
                 f"полигонов всего: {len(gdf)} (1984–2026), площадь S1-эры: "
                 f"{s1['area'].sum()/100:.0f} км²")
    ax.set_xlabel("долгота, °")
    ax.set_ylabel("широта, °")
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.0, -0.12),
              ncol=3, fontsize=9, frameon=False)
    fig.savefig(f"{OUTDIR}/shikhov_upd_overview.png", dpi=160)
    print(f"map saved: {OUTDIR}/shikhov_upd_overview.png")

    # --- 3. Шорт-лист новых кандидатов 2018+ ---
    newev = gdf[gdf["date"] >= "2018-01-01"].copy()
    cand = newev[
        (newev["area"] >= 300)
        & (newev["type"].isin(["шквал", "смерч", "шторм"]))
        & (newev["date_unit"] == "D")
    ].copy()
    # приоритет: европейская часть (лесная зона, S1-покрытие плотнее)
    eu_regions = [
        "Архангельская", "Костромская", "Кировская", "Вологодская", "Смоленская",
        "Брянская", "Тверская", "Ярославская", "Московская", "Владимирская",
        "Нижегородская", "Псковская", "Новгородская", "Ленинградская", "Коми",
        "Карелия", "Мурманская", "Пермский", "Свердловская", "Удмуртия", "Марий",
        "Чувашия", "Татарстан", "Башкортостан", "Воронежская", "Липецкая", "Тульская",
        "Калужская", "Ивановская", "Рязанская", "Орловская", "Курская", "Белгородская",
        "Тверская", "Дмитров", "Краснодарский", "Ростовская", "Вологда", "Кострома",
    ]
    def is_eu(r):
        return any(e in str(r) for e in eu_regions)
    cand["eurussia"] = cand["region"].map(is_eu)
    cand = cand.sort_values(["eurussia", "area"], ascending=[False, False])
    short = cand[[
        "id", "type", "date", "date_unit", "region", "area", "path", "diam_aver", "fscale", "eurussia"
    ]].head(40)
    short.to_csv(f"{OUTDIR}/validation_shortlist.csv", index=False, encoding="utf-8-sig")
    print("\nШорт-лист кандидатов валидации (2018+, area>=300 га, дата D, европ. приоритет):")
    print(short.to_string())

    summary = {
        "tbl_sites": tbl.astype(str).to_dict("records"),
        "area_ratio_new_old": {k: float(v) for k, v in area_ratio.items()},
        "n_shortlist": int(len(short)),
        "shortlist_top15": short.head(15).astype(str).to_dict("records"),
    }
    with open(f"{OUTDIR}/final_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nsaved: {OUTDIR}/final_summary.json")


if __name__ == "__main__":
    main()
