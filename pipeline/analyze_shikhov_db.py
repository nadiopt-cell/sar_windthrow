#!/usr/bin/env python3
"""Анализ временного покрытия БД Shikhov et al. 2020 ESSD (figshare 12073278).

Вопрос: сколько событий ветровала приходится на эпоху Sentinel-1
(S1A: запуск 03.04.2014, штатные IW GRD с октября 2014; S1B: с сентября 2016,
потерян в декабре 2021), и какие события-кандидаты пригодны для теста плагина.

Выход: сводка по годам + список событий 2015-2017 с площадью, датой, типом.
"""
import json
import os
import re
from collections import Counter, defaultdict

from osgeo import ogr

GIS = "/home/z/my-project/research/shikhov_db/GIS"
OUT = "/home/z/my-project/research/shikhov_db"


def read_layer(name):
    ds = ogr.Open(os.path.join(GIS, name))
    lyr = ds.GetLayer(0)
    defn = lyr.GetLayerDefn()
    fields = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    rows = []
    for feat in lyr:
        rec = {f: feat.GetField(f) for f in fields}
        geom = feat.GetGeometryRef()
        rec["_area_km2_geom"] = geom.GetArea() / 1e6 if geom else None
        rows.append(rec)
    ds = None
    return fields, rows


fields, events = read_layer("Storm_events.shp")
print("Storm_events fields:", fields)
print("n storms:", len(events))

# ---- разбор полей с датами -------------------------------------------------
def pick(rec, *names):
    for n in names:
        for k in rec:
            if k.upper() == n.upper():
                return rec[k]
    return None


year_re = re.compile(r"(19|20)\d{2}")


def event_years(rec):
    """Извлечь год(ы) из полей даты (date, date_start/date_end или строка-диапазон)."""
    cand = []
    for k, v in rec.items():
        if k.startswith("_"):
            continue
        if v is None:
            continue
        if "date" in k.lower() or "time" in k.lower():
            s = str(v)
            cand.extend(int(y) for y in year_re.findall(s))
    return sorted(set(cand))


# --- распределение штормов по годам -----------------------------------------
per_year = Counter()
for e in events:
    ys = event_years(e)
    if ys:
        per_year[ys[0]] += 1

print("\nStorms per year:")
for y in sorted(per_year):
    bar = "#" * max(1, per_year[y] // 2)
    print(f"  {y}: {per_year[y]:4d} {bar}")

# --- события в эпоху S1 (>= 2015) -------------------------------------------
s1_era = [e for e in events if event_years(e) and event_years(e)[0] >= 2015]
print(f"\nStorms with year >= 2015: {len(s1_era)}")

print("\nStorm events >= 2015 (ID, years, area_km2, path_km, type-ish fields):")
for e in sorted(s1_era, key=lambda r: event_years(r)[0]):
    interesting = {}
    for k, v in e.items():
        if k.startswith("_") or v in (None, "", -9999, "?9999"):
            continue
        ks = k.lower()
        if any(t in ks for t in ("date", "type", "certainty", "storm")):
            interesting[k] = v
    print(" ", interesting)

# сохраняем полный список в JSON
slim = []
for e in events:
    slim.append({k: v for k, v in e.items() if not k.startswith("_area")})
with open(os.path.join(OUT, "storms_summary.json"), "w", encoding="utf-8") as f:
    json.dump(slim, f, ensure_ascii=False, indent=1, default=str)
print("\nSaved -> research/shikhov_db/storms_summary.json")
