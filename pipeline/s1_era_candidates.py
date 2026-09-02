#!/usr/bin/env python3
"""Кандидаты для теста плагина: события БД Shikhov 2020 в эпоху Sentinel-1.

S1A: запуск 03.04.2014, штатные IW GRD над сушей с ~октября 2014.
S1B: с сентября 2016 (потерян 23.12.2021).
Окно теста: 2015-2017 ( Leaf-on май-сентябрь предпочтителен).
"""
import os
from collections import Counter

from osgeo import ogr

ogr.UseExceptions()
GIS = "/home/z/my-project/research/shikhov_db/GIS"

ds = ogr.Open(os.path.join(GIS, "Windthrows.shp"))
lyr = ds.GetLayer(0)
recs = []
for feat in lyr:
    recs.append({k: feat.GetField(k) for k in
                 ("ID", "Storm_ID", "Storm_type", "Certainty", "Year", "Month",
                  "Date", "Date_1", "Date_2", "N_polygons", "Area", "Length",
                  "Mean_width", "Wind_gust", "Direction")})
ds = None

print("Windthrows total:", len(recs))

# --- распределение по годам ---------------------------------------------------
per_year = Counter(r["Year"] for r in recs if r["Year"] and r["Year"] > 0)
print("\nWindthrows per year (last 10 years):")
for y in sorted(per_year)[-10:]:
    print(f"  {y}: {per_year[y]:3d}")

# --- эпоха S1 -----------------------------------------------------------------
S1_FROM = 2015  # окт 2014 -> первый полный год
era = [r for r in recs if r["Year"] and r["Year"] >= S1_FROM]
era_area = sum(r["Area"] for r in era if r["Area"] and r["Area"] > 0)
print(f"\nEra S1 (>= {S1_FROM}): n={len(era)}, area={era_area:.1f} km2")

print("\nBy storm type in S1 era:", dict(Counter(r["Storm_type"] for r in era)))
print("By certainty in S1 era:", dict(Counter(r["Certainty"] for r in era)))

# --- кандидаты: точная дата, лето, крупная площадь, высокая достоверность ------
def date_str(r):
    d1, d2 = str(r["Date_1"] or ""), str(r["Date_2"] or "")
    if d1 and d2 and d1 == d2:
        return f"{d1} (exact)"
    if d1 and d2:
        return f"{d1}..{d2}"
    return d1 or d2 or "-"

cand = [r for r in era
        if r["Area"] and r["Area"] >= 0.5        # >= 50 га
        and r["Certainty"] in ("High", "Medium")
        and str(r["Date_1"] or "") not in ("", "None", "-9999")]
cand.sort(key=lambda r: -r["Area"])

print(f"\nCandidates (area>=0.5 km2, date known, Certainty High/Medium): {len(cand)}")
print(f"{'ID':>4} {'Storm':>5} {'Year':>4} {'Mon':>3} {'Date':>24} {'Area_km2':>8} {'L_km':>6} {'Type':>9} {'Cert':>7}")
for r in cand[:25]:
    print(f"{r['ID']:>4} {r['Storm_ID']:>5} {r['Year']:>4} {r['Month']:>3} "
          f"{date_str(r):>24} {r['Area']:>8.2f} {r['Length']:>6.1f} "
          f"{str(r['Storm_type']):>9} {str(r['Certainty']):>7}")

import json
with open("/home/z/my-project/research/shikhov_db/s1_era_candidates.json", "w") as f:
    json.dump([{**r, "Date_1": str(r["Date_1"]), "Date_2": str(r["Date_2"])} for r in cand],
              f, ensure_ascii=False, indent=1)
print("\nSaved -> research/shikhov_db/s1_era_candidates.json")
