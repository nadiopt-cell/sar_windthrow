#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сопоставление обновлённого датасета Шихова (ветровалы_3316) со старой БД (2020 paper)
и с картой сайтов валидации sar_windthrow.

Выходы (в /home/z/my-project/upload/shikhov_upd/):
- compare_summary.json  — сводка
- shikhov_upd_s1era.gpkg — S1-эра (date >= 2014-10-01), EPSG:4326
- shikhov_upd_candidates_2018plus.gpkg — топ новых событий 2018+, EPSG:4326
"""
import json

import geopandas as gpd
import pandas as pd

SHP = "/home/z/my-project/upload/shikhov_upd/vetrovaly_3316/ветровалы_3316.shp"
SITES = "/home/z/my-project/upload/sar_windthrow/gis/windthrow_sites_map_2026-09-04.geojson"
OUTDIR = "/home/z/my-project/upload/shikhov_upd"

SITE_IDS = [666, 694, 655, 583, 674, 608, 646, 579, 683, 606, 654, 658, 696]


def main() -> None:
    gdf = gpd.read_file(SHP)
    gdf["date"] = pd.to_datetime(gdf["date"])
    gdf["year"] = gdf["date"].dt.year
    gdf["month"] = gdf["date"].dt.month
    gdf["area_ha_calc"] = gdf.to_crs(gdf.estimate_utm_crs()).area / 1e4

    print("=== 1. Период и объём ===")
    print(f"даты: {gdf['date'].min().date()} .. {gdf['date'].max().date()}")
    print(f"всего: {len(gdf)}, сумма area (га): {gdf['area'].sum():,.0f}, "
          f"расчётная (UTM): {gdf['area_ha_calc'].sum():,.0f}")
    print("\nпо декадам x тип (перв. 40 лет):")
    gdf["decade"] = (gdf["year"] // 10 * 10).astype(str) + "s"
    print(pd.crosstab(gdf["decade"], gdf["type"]).to_string())

    print("\n=== 2. S1-эра (>= 2014-10-01) ===")
    s1 = gdf[gdf["date"] >= "2014-10-01"].copy()
    print(f"n = {len(s1)}, area = {s1['area'].sum():,.0f} га")
    print(pd.crosstab(s1["year"], s1["type"]).to_string())
    print("\nточность даты в S1-эре:")
    print(s1["date_unit"].value_counts().to_string())

    print("\n=== 3. Преемственность id: наши 13 сайтов ===")
    new_ids = set(gdf["id"].dropna().astype(int))
    hit = [i for i in SITE_IDS if i in new_ids]
    miss = [i for i in SITE_IDS if i not in new_ids]
    print(f"найдены в новой БД: {hit}")
    print(f"НЕ найдены: {miss}")
    sites = gpd.read_file(SITES)
    site_props = sites.drop(columns="geometry") if len(sites.columns) > 1 else sites
    rows = []
    if hit:
        m = gdf[gdf["id"].astype("Int64").isin(hit)][
            ["id", "type", "date", "date_unit", "region", "area", "path", "fscale"]
        ].copy()
        for _, r in m.iterrows():
            old = site_props[site_props["id"] == int(r["id"])]
            old_date = old["date"].iloc[0] if len(old) else None
            old_type = old["type"].iloc[0] if len(old) else None
            rows.append({
                "id": int(r["id"]), "new_type": r["type"],
                "new_date": str(r["date"].date()), "old_date": old_date,
                "old_type": old_type,
                "match_date": None,
                "new_area_ha": float(r["area"]), "region": r["region"],
                "path_km": (None if pd.isna(r["path"]) else float(r["path"])),
                "fscale": r["fscale"],
            })
        # date match отдельно (old_date формат dd.mm.yyyy)
        for row in rows:
            od = row["old_date"]
            if isinstance(od, str) and od not in ("None", ""):
                try:
                    d, mth, y = od.split(".")
                    row["match_date"] = (f"{y}-{int(mth):02d}-{int(d):02d}" == row["new_date"])
                except ValueError:
                    row["match_date"] = None
        print(pd.DataFrame(rows).to_string())

    print("\n=== 4. Новые события 2018+ (кандидаты валидации) ===")
    newev = gdf[gdf["date"] >= "2018-01-01"].copy()
    print(f"всего 2018+: {len(newev)},.area: {newev['area'].sum():,.0f} га")
    cand = newev[(newev["area"] >= 500) & (newev["type"].isin(["шквал", "смерч", "шторм"]))].copy()
    cand = cand.sort_values("area", ascending=False)
    cols = ["id", "type", "date", "date_unit", "region", "area", "path", "diam_aver", "fscale"]
    print(f"с area >= 500 га: {len(cand)}")
    print(cand[cols].head(30).to_string())

    print("\nпо годам 2018+:")
    print(pd.crosstab(newev["year"], newev["type"]).to_string())
    print("\nпо регионам (2018+, area>=500 га) топ-15:")
    print(cand.groupby("region")["area"].agg(["count", "sum"]).sort_values("sum", ascending=False).head(15).to_string())

    # --- Сохранения ---
    s1_out = s1.to_crs(4326)
    s1_out.to_file(f"{OUTDIR}/shikhov_upd_s1era.gpkg", driver="GPKG", layer="s1era")
    cand_out = cand.head(60).to_crs(4326)
    cand_out.to_file(f"{OUTDIR}/shikhov_upd_candidates_2018plus.gpkg", driver="GPKG", layer="candidates")

    summary = {
        "period": [str(gdf["date"].min().date()), str(gdf["date"].max().date())],
        "n_total": int(len(gdf)),
        "area_ha_total_attr": float(gdf["area"].sum()),
        "area_ha_total_calc": float(gdf["area_ha_calc"].sum()),
        "by_decade_type": {str(k): {str(kk): int(vv) for kk, vv in v.items()}
                           for k, v in pd.crosstab(gdf["decade"], gdf["type"]).to_dict("index").items()},
        "s1_era": {
            "n": int(len(s1)),
            "area_ha": float(s1["area"].sum()),
            "by_year_type": {str(k): {str(kk): int(vv) for kk, vv in v.items()}
                             for k, v in pd.crosstab(s1["year"], s1["type"]).to_dict("index").items()},
            "date_unit": s1["date_unit"].value_counts().to_dict(),
        },
        "site_id_hits": {"found": hit, "missing": miss},
        "site_matches": rows,
        "new_events_2018plus": {
            "n": int(len(newev)),
            "area_ha": float(newev["area"].sum()),
            "n_candidates_area_ge_500ha": int(len(cand)),
            "top30": cand[cols].head(30).astype(str).to_dict("records"),
            "by_year_type": {str(k): {str(kk): int(vv) for kk, vv in v.items()}
                             for k, v in pd.crosstab(newev["year"], newev["type"]).to_dict("index").items()},
        },
        "artifacts": [
            f"{OUTDIR}/shikhov_upd_s1era.gpkg",
            f"{OUTDIR}/shikhov_upd_candidates_2018plus.gpkg",
        ],
    }
    with open(f"{OUTDIR}/compare_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nsaved: {OUTDIR}/compare_summary.json + 2 GPKG")


if __name__ == "__main__":
    main()
