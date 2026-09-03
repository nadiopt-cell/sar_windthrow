#!/usr/bin/env python3
"""step12c pre-work: (a) до-до granules for ID666, (b) SLC triples for
3-5 new events to extend the DiD validation.

Outputs work_data/step12c_selection.json with:
  - id666 dodo candidate granules (S1B rel orbit 94, fully pre-storm)
  - per-event: densest part cluster, bbox, area, SLC acquisition triples
    (d1 < event < d2 < d3, same platform+pass, 12-day cadence)

CMR queries are anonymous (umm_json), same pattern as step12_coherence_sources.py.
"""
import json
import os
import re
import urllib.parse
import urllib.request
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")
from osgeo import ogr

ogr.UseExceptions()

REPO = "/home/z/my-project/plugin_work/sar_windthrow_repo"
REF_SHP = "/home/z/my-project/research/shikhov_db/GIS/Windthrows.shp"
OUT_JSON = f"{REPO}/work_data/step12c_selection.json"

CMR = "https://cmr.earthdata.nasa.gov/search"
UA = {"User-Agent": "sar-windthrow-step12c/1.0 (research; contact nadiopt)"}
CID = {"S1A": "C1214470488-ASF", "S1B": "C1327985661-ASF"}  # SLC collections

CANDIDATES = [655, 578, 583, 608, 674, 670, 606, 603, 617, 621, 646, 576]


def http_json(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def cmr_slc(platform, bbox, t0, t1, page_size=300):
    """granules of SLC collection over bbox between t0..t1 (ISO dates)."""
    params = {
        "collection_concept_id": CID[platform],
        "bounding_box": ",".join(f"{x:.4f}" for x in bbox),
        "temporal": f"{t0}T00:00:00Z,{t1}T23:59:59Z",
    }
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{CMR}/granules.umm_json?{q}&page_size={page_size}"
    d = http_json(url)
    out = []
    for item in d.get("items", []):
        name = item.get("umm", {}).get("DataGranule", {}) \
                    .get("Identifiers", [{}])[0].get("Identifier", "")
        if "_SLC__" not in name:
            continue
        out.append(name)
    return out


def parse_name(name):
    """S1A_IW_SLC__1SDV_20170616T033238_..._6660 -> dict (double underscore!)."""
    f = name.split("_")
    return {"name": name, "plat": f[0], "start": f[5], "stop": f[6],
            "abs_orbit": f[7]}


def clusters_from_shp(ref_id, bucket_deg=0.5):
    """Split event parts into ~0.5-deg buckets, return densest cluster info."""
    ds = ogr.Open(REF_SHP)
    lyr = ds.GetLayer(0)
    lyr.SetAttributeFilter(f"ID = {ref_id}")
    buckets = defaultdict(lambda: {"area": 0.0, "env": None, "n": 0})
    total_area = 0.0
    n_parts = 0
    for feat in lyr:
        g = feat.GetGeometryRef()
        total_area += g.GetArea()  # deg^2 (approx weight only)
        for i in range(g.GetGeometryCount()):
            part = g.GetGeometryRef(i)
            e = part.GetEnvelope()          # [minx,maxx,miny,maxy]
            cx, cy = (e[0] + e[1]) / 2, (e[2] + e[3]) / 2
            key = (round(cx / bucket_deg), round(cy / bucket_deg))
            b = buckets[key]
            b["area"] += part.GetArea()
            b["n"] += 1
            if b["env"] is None:
                b["env"] = list(e)
            else:
                b["env"] = [min(b["env"][0], e[0]), min(b["env"][1], e[1]),
                            max(b["env"][2], e[2]), max(b["env"][3], e[3])]
            n_parts += 1
    ds = None
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda b: b["area"])
    # 80 m posting -> 0.64 ha/px ; part area in deg^2, ~1 deg^2 lat ~ 12300 km^2
    area_km2 = best["area"] * 12321 * (111.32 / 111.32)  # rough: deg^2 * 12321 km2
    return {
        "bbox": best["env"], "n_parts_cluster": best["n"], "n_parts_total": n_parts,
        "area_deg2": round(best["area"], 5),
        "area_km2_approx": round(best["area"] * 12321, 1),
        "bbox_padded": [best["env"][0] - 0.25, best["env"][2] - 0.25,
                        best["env"][1] + 0.25, best["env"][3] + 0.25],
    }


def find_triples(names, ev_date):
    """Group by (platform, abs orbit), find d1<ev<d2<d3 with 12-day steps."""
    acq = [parse_name(n) for n in names]
    groups = defaultdict(list)
    for a in acq:
        t = datetime.strptime(a["start"], "%Y%m%dT%H%M%S")
        rel = int(a["abs_orbit"]) % 175          # absolute orbit changes every cycle!
        groups[(a["plat"], rel)].append((t, a))
    ev = datetime.strptime(ev_date, "%Y-%m-%d")
    triples = []
    for (plat, rel), items in groups.items():
        times = sorted(set(t.date() for t, a in items))     # compare DATES (sec drift!)
        for i in range(len(times) - 2):
            d1, d2, d3 = times[i], times[i + 1], times[i + 2]
            if (d2 - d1).days == 12 and (d3 - d2).days == 12 and d1 < ev.date() < d2:
                gap = max((ev.date() - d1).days, (d2 - ev.date()).days)
                triples.append({
                    "platform": plat, "rel_orbit": rel,
                    "d1": d1.strftime("%Y-%m-%d"), "d2": d2.strftime("%Y-%m-%d"),
                    "d3": d3.strftime("%Y-%m-%d"),
                    "gap_days": gap,
                    "g1": None, "g2": None, "g3": None,
                })
                for k, dt in zip(("g1", "g2", "g3"), (d1, d2, d3)):
                    for t, a in items:
                        if t.date() == dt:
                            triples[-1][k] = a["name"]
                triples[-1]["pass_hour"] = next(
                    t.hour for t, a in items if t.date() == d1)
    return sorted(triples, key=lambda x: x["gap_days"])


def main():
    out = {"generated": datetime.utcnow().isoformat() + "Z",
           "purpose": "step12c: dodo granules ID666 + SLC triples for new DiD events"}

    # --- (a) ID666 dodo: S1B rel orbit 94, descending 03:34, pre-storm window
    bbox666 = [42.5, 61.4, 43.9, 62.3]
    dodo = {}
    t0, t1 = "2017-06-20", "2017-07-22"
    g = cmr_slc("S1B", bbox666, t0, t1)
    dodo["dodo_window"] = sorted(g)
    print(f"[cmr] S1B {t0}..{t1}: {len(g)} granules")
    for n in sorted(g):
        print("   ", n)
    out["id666_dodo"] = dodo

    # --- (b) new events: densest cluster + SLC triples
    events = {}
    for eid in CANDIDATES:
        c = clusters_from_shp(eid)
        if c is None:
            print(f"[shp] ID{eid}: no parts")
            continue
        # event date from shapefile attribute (DD.MM.YYYY)
        ds = ogr.Open(REF_SHP)
        lyr = ds.GetLayer(0)
        lyr.SetAttributeFilter(f"ID = {eid}")
        f = next(iter(lyr))
        ev_date = datetime.strptime(f.GetField("Date"), "%d.%m.%Y").strftime("%Y-%m-%d")
        stype = f.GetField("Storm_type")
        area = f.GetField("Area")
        ds = None
        bb = c["bbox_padded"]
        names = []
        for plat in ("S1A", "S1B"):
            try:
                g = cmr_slc(plat, bb, (ev_date and
                            (datetime.strptime(ev_date, "%Y-%m-%d") - timedelta(days=16)).strftime("%Y-%m-%d")),
                            (datetime.strptime(ev_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d"))
                names += g
            except Exception as e:
                print(f"[cmr-err] {plat} ID{eid}: {e}")
        triples = find_triples(names, ev_date)
        events[f"ID{eid}"] = {
            "event_date": ev_date, "storm_type": stype, "area_km2_db": area,
            "cluster": c, "n_slc_found": len(names), "triples": triples[:3],
        }
        print(f"[evt] ID{eid} {ev_date} {stype} {area} km2 | cluster parts {c['n_parts_cluster']}/{c['n_parts_total']} "
              f"area~{c['area_km2_approx']} km2 | SLC {len(names)} | triples {len(triples)}")
        for t in triples[:2]:
            print(f"      {t['platform']} {t['d1']}->{t['d2']}->{t['d3']} gap {t['gap_days']}d "
                  f"g1={'OK' if t['g1'] else '-'} g2={'OK' if t['g2'] else '-'} g3={'OK' if t['g3'] else '-'}")

    out["events"] = events
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[ok ] -> {OUT_JSON}")


if __name__ == "__main__":
    main()
