#!/usr/bin/env python3
"""Пересборка chart_scores_ed5.png (кэш потерян при сбросе песочницы)."""
import importlib.util
import json
import sys

sys.path.insert(0, "/home/z/my-project/sar_windthrow/scripts_screening")

spec = importlib.util.spec_from_file_location(
    "b5", "/home/z/my-project/sar_windthrow/scripts_screening/build_report_ed5.py")
b5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b5)

CACHE = "/home/z/my-project/scripts/_cache"
base = json.load(open(f"{CACHE}/sites_base.json"))
cmr = {r["key"]: r for r in json.load(open(f"{CACHE}/sites_cmr.json"))}
land = json.load(open(f"{CACHE}/sites_landscape.json"))
coh = json.load(open(f"{CACHE}/sites_cohort.json"))
sel = {e["id"] for e in coh["cohort"]}

ranked = []
for c in base["candidates"]:
    key = f"{c['id']}_{c['date_1'].replace('/', '')}"
    r = cmr.get(key)
    if not (r and r["chain"] and r["chain"]["quality"] == "full"):
        continue
    if not (2015 <= int(c["date_1"][:4]) <= 2017):
        continue
    l = land.get(key, {})
    ranked.append({"id": c["id"], "type": c["type"], "date_1": c["date_1"],
                   "score": b5.score(c, r["chain"], l)})

b5.build_chart(ranked, sel)
print("chart ok:", b5.CHART_PNG if hasattr(b5, "CHART_PNG") else
      "/home/z/my-project/scripts/_cache/chart_scores_ed5.png")
