#!/usr/bin/env python3
"""Step 4: score candidates, pick top-5, emit map-ready GeoJSON with
(a) 7 processed events (+ live HyP3 job linkage) and (b) shortlisted candidates.
Run with /usr/bin/python3. Output: download/windthrow_sites_map_2026-09-04.geojson
"""
import json
import urllib.request
from pathlib import Path

from osgeo import ogr

ogr.UseExceptions()

CACHE = "/home/z/my-project/scripts/_cache"
OUT = "/home/z/my-project/download/windthrow_sites_map_2026-09-04.geojson"
HY3_TOKEN = Path("/home/z/my-project/upload/токен.txt").read_text().strip()


def wkt_to_geom(wkt):
    g = ogr.CreateGeometryFromWkt(wkt)
    return json.loads(g.ExportToJson())


def hyp3_jobs():
    req = urllib.request.Request(
        "https://hyp3-api.asf.alaska.edu/jobs",
        headers={"Authorization": f"Bearer {HY3_TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["jobs"]


def hyp3_link(jobs):
    """group jobs by event idXXX -> {prepost, control} granule pairs."""
    ev = {}
    for j in jobs:
        name = j.get("name") or ""
        if not name.startswith("id"):
            continue
        eid = name.split("-")[0][2:]
        g = j.get("job_parameters", {}).get("granules", [])
        kind = "control" if "control" in name else ("prepost" if "prepost" in name else "other")
        rec = ev.setdefault(str(eid), {})
        rec[kind] = {"granules": g, "job_id": j.get("job_id"),
                     "status": j.get("status_code")}
    return ev


def score(cand, chain, land):
    area_n = min(1.0, (cand["area_km2"] or 0) / 4.0)
    w = cand["mean_width_m"] or 0
    width_n = max(0.0, min(1.0, (w - 150) / 350.0))
    forest = land.get("forest_frac")
    forest_n = forest if forest is not None else 0.5
    wf = land.get("water_frac")
    water_n = 1.0 if wf is None else max(0.0, 1.0 - min(1.0, wf * 10))
    q = {"full": 1.0, "stretched": 0.6, "partial": 0.3}.get(chain["quality"], 0.0)
    sm = land.get("slope_mean_deg")
    slope_n = 1.0 if sm is None else max(0.0, 1.0 - (sm - 3.0) / 5.0)
    g = cand.get("wind_gust")
    gust_n = min(1.0, (g - 15) / 15.0) if g and g > 0 else 0.5
    tornado_bonus = 0.05 if cand["type"] == "tornado" else 0.0
    s = (0.30 * forest_n + 0.25 * q + 0.15 * area_n + 0.10 * water_n +
         0.10 * slope_n + 0.05 * width_n + 0.05 * gust_n + tornado_bonus)
    parts = {"forest": round(0.30 * forest_n, 3), "chain": round(0.25 * q, 3),
             "size": round(0.15 * area_n, 3), "water": round(0.10 * water_n, 3),
             "slope": round(0.10 * slope_n, 3), "width": round(0.05 * width_n, 3),
             "gust": round(0.05 * gust_n, 3), "tornado_bonus": tornado_bonus}
    return round(s, 3), parts


def main():
    base = json.load(open(f"{CACHE}/sites_base.json"))
    cmr = {r["key"]: r for r in json.load(open(f"{CACHE}/sites_cmr.json"))}
    land = json.load(open(f"{CACHE}/sites_landscape.json"))
    hy3 = hyp3_link(hyp3_jobs())

    # ---------- scored candidates ----------
    scored = []
    for c in base["candidates"]:
        key = f"{c['id']}_{c['date_1'].replace('/', '')}"
        r = cmr.get(key)
        if not (r and r["chain"]):
            continue
        l = land.get(key, {})
        s, parts = score(c, r["chain"], l)
        scored.append((s, c, r["chain"], l, parts))
    scored.sort(key=lambda t: -t[0])

    # diversity: max 3 of one storm type in the top-5
    top, cnt = [], {}
    for item in scored:
        t = item[1]["type"]
        if cnt.get(t, 0) >= 3:
            continue
        top.append(item)
        cnt[t] = cnt.get(t, 0) + 1
        if len(top) == 5:
            break

    print("SCORED (feasible):")
    for s, c, ch, l, p in scored:
        mark = " <-- TOP-5" if any(x[1] is c for x in top) else ""
        print(f"  {s:.3f} ID{c['id']:>4} {c['type']:>8} {c['date_1']} "
              f"{c['area_km2']:>5} km2 forest={l.get('forest_frac')} "
              f"water={l.get('water_frac')} slope={l.get('slope_mean_deg')} "
              f"chain={ch['quality']}({ch['pair_interval_days']}d){mark}")

    # ---------- features ----------
    feats = []
    for c in base["processed"]:
        props = {k: v for k, v in c.items() if k not in ("wkt", "n_vertices",
                                                         "hull_area_deg2")}
        props["hyp3"] = hy3.get(str(c["id"]), {})
        if c["id"] == 694:
            props["date_note"] = ("проектная дата события ~19.09.2017 "
                                  "(пары 12/24.09); Date_1 в БД = 04.09.2017 — оценка")
        props["metrics_note"] = ("детальные метрики — отчёт изд.4 (гл. валидации); "
                                 "DiD 7 событий: средний AUC 0.642 -> 0.690")
        props["result_summary"] = {
            "coh_delta": "AUC 0.908 на ID694; DiD-среднее 0.690",
            "lband": "ID694 dHV invAUC 0.905; ID666 не сработал (годовые мозаики)",
            "wind_index": "контр-пример: PA 0.059 / UA 0.024 на ID666",
        }
        feats.append({"type": "Feature", "geometry": wkt_to_geom(c["wkt"]),
                      "properties": props})

    short_ids = set()
    for s, c, ch, l, parts in top:
        key = f"{c['id']}_{c['date_1'].replace('/', '')}"
        if key in short_ids:
            continue
        short_ids.add(key)
        props = {k: v for k, v in c.items() if k not in ("wkt", "n_vertices",
                                                         "hull_area_deg2")}
        props["status"] = "shortlisted"
        props["score"] = s
        props["score_parts"] = parts
        props["slc_chain"] = ch
        props["landscape_proxy_2021"] = {k: v for k, v in l.items()
                                         if k not in ("wc_classes_top",)}
        props["cost_credits"] = 20
        props["source_db"] = "Shikhov & Erlikh 2020 (ESSD), doi:10.5194/essd-12-1933-2020"
        feats.append({"type": "Feature", "geometry": wkt_to_geom(c["wkt"]),
                      "properties": props})

    fc = {
        "type": "FeatureCollection",
        "name": "sar_windthrow_sites",
        "generated": "2026-09-04",
        "description": ("Sentinel-1 ветровалы: 7 обработанных событий валидации "
                        "(status=processed) + топ-5 кандидатов следующей партии "
                        "(status=shortlisted). База Шихова-Эрлиха 2020."),
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": feats,
    }
    with open(OUT, "w") as f:
        json.dump(fc, f, ensure_ascii=False)
    import os
    print(f"\nsaved {OUT} ({os.path.getsize(OUT) / 1e6:.2f} MB, "
          f"{len(feats)} features: 7 processed + {len(feats) - 7} shortlisted)")


if __name__ == "__main__":
    main()
