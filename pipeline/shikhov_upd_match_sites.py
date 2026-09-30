#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пространственно-временное сопоставление 13 сайтов валидации sar_windthrow
с обновлённой БД Шихова (ветровалы_3316).

Критерий: пересечение геометрий + дата события в окне [date_1 - 30 сут, date_2 + 30 сут].
IoU считается в UTM (по зоне сайта).

Выход: /home/z/my-project/upload/shikhov_upd/site_matching.json + печать.
"""
import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import base as geom_base

SHP = "/home/z/my-project/upload/shikhov_upd/vetrovaly_3316/ветровалы_3316.shp"
SITES = "/home/z/my-project/upload/sar_windthrow/gis/windthrow_sites_map_2026-09-04.geojson"
OUT = "/home/z/my-project/upload/shikhov_upd/site_matching.json"


def parse_old_date(s: str):
    if not isinstance(s, str) or "." not in s:
        return None
    try:
        d, m, y = s.split(".")
        return pd.Timestamp(int(y), int(m), int(d))
    except ValueError:
        return None


def to_ts(v):
    """Timestamp | строка dd.mm.yyyy | ISO-строка | NaT -> Timestamp | None."""
    if v is None:
        return None
    if isinstance(v, str) and "." in v:
        return parse_old_date(v)
    try:
        ts = pd.to_datetime(v, errors="coerce")
        return None if pd.isna(ts) else ts
    except Exception:
        return None


def main() -> None:
    gdf = gpd.read_file(SHP).to_crs(4326)
    gdf["date"] = pd.to_datetime(gdf["date"])
    sites = gpd.read_file(SITES)
    print(f"sites crs: {sites.crs}, n: {len(sites)}")

    results = []
    for _, site in sites.iterrows():
        sid = int(site["id"])
        d1 = to_ts(site.get("date_1"))
        d2 = to_ts(site.get("date_2"))
        if d1 is None:  # сайт 696 в v3-карте хранит даты иначе
            d1 = to_ts(site.get("date"))
        if d2 is None:
            d2 = d1
        w_start = d1 - pd.Timedelta(days=30)
        w_end = d2 + pd.Timedelta(days=30)

        # пространственный фильтр
        cand = gdf[gdf.intersects(site.geometry)]
        if len(cand):
            cand = cand[(cand["date"] >= w_start) & (cand["date"] <= w_end)]

        utm = gpd.GeoSeries([site.geometry], crs="EPSG:4326").estimate_utm_crs()
        site_utm = gpd.GeoSeries([site.geometry], crs="EPSG:4326").to_crs(utm).iloc[0]
        site_area_ha = site_utm.area / 1e4

        matches = []
        for _, f in cand.iterrows():
            f_utm = gpd.GeoSeries([f.geometry], crs="EPSG:4326").to_crs(utm).iloc[0]
            inter = site_utm.intersection(f_utm).area
            union = site_utm.union(f_utm).area
            iou = float(inter / union) if union > 0 else 0.0
            matches.append({
                "new_id": int(f["id"]), "type": f["type"],
                "date": str(f["date"].date()), "date_unit": f["date_unit"],
                "region": f["region"], "area_ha": float(f["area"]),
                "inter_ha": float(inter / 1e4), "iou": round(iou, 3),
            })
        matches.sort(key=lambda x: -x["iou"])
        results.append({
            "site_id": sid,
            "old": {
                "type": site.get("type"), "date": str(site.get("date")),
                "window": [str(d1.date()), str(d2.date())] if d1 is not None else None,
                "site_area_ha": float(site_area_ha),
            },
            "n_candidates_spatial": int(len(gdf[gdf.intersects(site.geometry)])),
            "matches": matches[:6],
            "best_iou": (matches[0]["iou"] if matches else None),
            "best_new_id": (matches[0]["new_id"] if matches else None),
        })
        print(f"site {sid} ({site.get('type')}, {site.get('date')}): "
              f"spatial={results[-1]['n_candidates_spatial']}, "
              f"matched={len(matches)}, best_iou={results[-1]['best_iou']}, "
              f"best_new_id={results[-1]['best_new_id']}")
        for mth in matches[:3]:
            print(f"    -> new_id={mth['new_id']} {mth['type']} {mth['date']} "
                  f"{mth['region']} area={mth['area_ha']:.0f} га iou={mth['iou']}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nsaved: {OUT}")

    matched_sites = [r for r in results if r["matches"]]
    print(f"\nИТОГ: сайтов с матчем: {len(matched_sites)}/{len(results)}")
    print("best IoU по сайтам:", {r["site_id"]: r["best_iou"] for r in results})


if __name__ == "__main__":
    main()
