#!/usr/bin/env python3
"""Step 2: CMR check for candidate events — find S1 SLC chains (same platform,
same time-of-day = same relative orbit) covering the damage track with the
pre/post/control pattern needed for coherence DiD.

Pattern per event with storm date D:
  pre  < D  (>= D-10)   post = pre+12d > D (<= D+9)   control = post+12d
Output: scripts/_cache/sites_cmr.json
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from osgeo import ogr

ogr.UseExceptions()

CACHE = "/home/z/my-project/scripts/_cache"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
SLEEP = 0.35


def http_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            last = RuntimeError(f"HTTP {e.code}: {body}")
            time.sleep(2 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def parse_ring(ring_str, ref_poly):
    """CMR JSON ring: flat 'x1 x2 x3 ...' string. Order (lat lon) vs (lon lat)
    is ambiguous -> pick the interpretation intersecting the query bbox best."""
    flat = [float(x) for x in ring_str.replace(",", " ").split()]
    if len(flat) < 6:
        return None
    if len(flat) % 2:
        flat = flat[:-1]
    variants = []
    for swap in (False, True):
        pts = []
        for i in range(0, len(flat), 2):
            a, b = flat[i], flat[i + 1]
            pts.append((b, a) if swap else (a, b))
        if any(abs(lo) > 180 or abs(la) > 90 for lo, la in pts):
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for lon, lat in pts:
            ring.AddPoint_2D(lon, lat)
        poly = ogr.Geometry(ogr.wkbPolygon)
        poly.AddGeometry(ring)
        if not poly.IsValid():
            poly = poly.MakeValid() or poly.Buffer(0)
        variants.append(poly)
    if not variants:
        return None
    if len(variants) == 1:
        return variants[0]
    try:
        return max(variants, key=lambda g: g.Intersection(ref_poly).GetArea())
    except Exception:  # noqa: BLE001
        return variants[0]


def entry_polygon(entry, ref_poly):
    rings = entry.get("polygons") or []
    if not rings or not rings[0]:
        return None
    return parse_ring(rings[0][0], ref_poly)


def bbox_poly(bbox):
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for lon, lat in ((bbox[0], bbox[1]), (bbox[2], bbox[1]),
                     (bbox[2], bbox[3]), (bbox[0], bbox[3]), (bbox[0], bbox[1])):
        ring.AddPoint_2D(lon, lat)
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    return poly


def entry_direction(entry):
    for a in entry.get("attributes") or []:
        name = str(a.get("name", "")).lower()
        if "scend" in name or name in ("ascdesc", "asc_desc"):
            v = str(a.get("value", "")).strip().upper()
            if v.startswith("A"):
                return "ASC"
            if v.startswith("D"):
                return "DESC"
    return None


def cmr_slc(bbox, t0, t1):
    q = {
        "collection_concept_id": ["C1214470488-ASF", "C1327985661-ASF"],
        "bounding_box": ",".join(f"{x:.3f}" for x in bbox),
        "temporal": f"{t0.strftime('%Y-%m-%d')},{t1.strftime('%Y-%m-%d')}",
        "page_size": "200",
    }
    url = CMR + "?" + urllib.parse.urlencode(q, doseq=True)
    return http_json(url).get("feed", {}).get("entry", [])


def analyze_event(cand, hull):
    D = datetime.strptime(cand["date_1"], "%Y/%m/%d")
    env = hull.GetEnvelope()  # (minx, maxx, miny, maxy)
    bbox = [env[0] - 0.05, env[2] - 0.05, env[1] + 0.05, env[3] + 0.05]
    ref = bbox_poly(bbox)
    entries = cmr_slc(bbox, D - timedelta(days=16), D + timedelta(days=42))
    time.sleep(SLEEP)

    acq = []
    for e in entries:
        try:
            t = datetime.strptime(e["time_start"][:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:  # noqa: BLE001
            continue
        plat = "S1B" if e["title"].startswith("S1B_") else "S1A"
        mode = "IW" if "_IW_" in e["title"] else ("EW" if "_EW_" in e["title"] else "SM")
        rel = None
        dom = (e.get("orbit_calculated_spatial_domains") or [{}])[0]
        try:
            rel = int(dom.get("orbit_number")) % 175
        except Exception:  # noqa: BLE001
            pass
        poly = entry_polygon(e, ref)
        if poly is None:
            continue
        try:
            cov = poly.Intersection(hull).GetArea() / hull.GetArea() if hull.GetArea() else 0
        except Exception:  # noqa: BLE001
            cov = 0.0
        acq.append({"t": t, "plat": plat, "mode": mode, "rel_orbit": rel,
                    "dir": entry_direction(e), "cov": cov, "title": e["title"]})

    # group into chains: same platform, same relative orbit (orbit_number % 175);
    # fallback bucket = time-of-day ±90 s
    chains = {}
    for a in acq:
        rel = a.get("rel_orbit")
        if rel is None:
            tod = a["t"].hour * 3600 + a["t"].minute * 60 + a["t"].second
            rel = f"tod{int(round(tod / 90))}"
        key = (a["plat"], a["mode"], rel)
        chains.setdefault(key, []).append(a)

    best = None
    for key, items in chains.items():
        items.sort(key=lambda x: x["t"])
        # frame-boundary events: 2 granules per pass -> keep max-coverage per date
        by_date = {}
        for a in items:
            d = a["t"].date()
            if d not in by_date or a["cov"] > by_date[d]["cov"]:
                by_date[d] = a
        seq = sorted(by_date.values(), key=lambda x: x["t"])
        for i in range(len(seq) - 2):
            t1, t2, t3 = (seq[i], seq[i + 1], seq[i + 2])
            d1, d2 = (t2["t"] - t1["t"]).days, (t3["t"] - t2["t"]).days
            # 12-дневный шаг = full; 15-27 дней (SLC через цикл, 2015-2016) = stretched
            if not (10 <= d1 <= 27 and 10 <= d2 <= 27):
                continue
            if not (t1["t"] < D <= t2["t"]):
                continue
            covs = [t1["cov"], t2["cov"], t3["cov"]]
            mincov = min(covs)
            base_q = "full" if (d1 <= 14 and d2 <= 14) else "stretched"
            qual = (base_q if mincov >= 0.95
                    else ("partial" if mincov >= 0.85 else "none"))
            if qual == "none":
                continue
            dirs = {x["dir"] for x in (t1, t2, t3) if x["dir"]}
            if t1["mode"] != "IW":
                continue  # EW/SM SLC не годятся для стандартной схемы
            cand_chain = {
                "platform": t1["plat"],
                "direction": dirs.pop() if len(dirs) == 1 else (list(dirs)[0] if dirs else None),
                "rel_orbit": t1.get("rel_orbit"),
                "pre": t1["t"].strftime("%Y-%m-%d"),
                "post": t2["t"].strftime("%Y-%m-%d"),
                "control": t3["t"].strftime("%Y-%m-%d"),
                "pair_interval_days": d1,
                "control_interval_days": d2,
                "min_coverage": round(mincov, 3),
                "coverages": [round(c, 3) for c in covs],
                "quality": qual,
                "gap_days_pre": (D - t1["t"]).days,
                "gap_days_post": (t2["t"] - D).days,
            }
            if best is None or (qual == "full" and best["quality"] != "full") or \
               (qual == "stretched" and best["quality"] not in ("full", "stretched")) or \
               (qual == best["quality"] and mincov > best["min_coverage"]):
                best = cand_chain
    return {"id": cand["id"], "key": f"{cand['id']}_{cand['date_1'].replace('/', '')}",
            "storm_date": D.strftime("%Y-%m-%d"), "n_slc_in_window": len(acq),
            "chain": best}


def main():
    base = json.load(open(f"{CACHE}/sites_base.json"))
    out = []
    cands = base["candidates"]
    for n, cand in enumerate(cands, 1):
        geom = ogr.CreateGeometryFromWkt(cand["wkt"])
        hull = geom.ConvexHull()
        res = analyze_event(cand, hull)
        q = res["chain"]["quality"] if res["chain"] else "none"
        tail = ""
        if res["chain"]:
            tail = (f"{res['chain']['platform']} {res['chain']['pre']}|"
                    f"{res['chain']['post']}|{res['chain']['control']} "
                    f"cov={res['chain']['min_coverage']}")
        print(f"[{n:>2}/{len(cands)}] ID{res['id']:>4} {res['storm_date']} -> "
              f"{q:<7} slc={res['n_slc_in_window']:>2} {tail}")
        out.append(res)

    full = [r for r in out if r["chain"] and r["chain"]["quality"] == "full"]
    stretched = [r for r in out if r["chain"] and r["chain"]["quality"] == "stretched"]
    part = [r for r in out if r["chain"] and r["chain"]["quality"] == "partial"]
    print(f"\nfull: {len(full)} | stretched(24d): {len(stretched)} | partial: {len(part)} "
          f"| none: {len(out) - len(full) - len(stretched) - len(part)}")
    with open(f"{CACHE}/sites_cmr.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved", f"{CACHE}/sites_cmr.json")


if __name__ == "__main__":
    main()
