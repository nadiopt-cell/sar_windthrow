#!/usr/bin/env python3
"""Финальная когорта волны-2 по правилу пользователя:
3 торнадо + 2 шквала, 2015-2017, ТОЛЬКО full-цепочки (12-дневные).
Для каждого события разрешает точные имена SLC-гранул (pre/post/control)
через CMR и проверяет покрытие hull >= 0.85.
Выход: scripts/_cache/sites_cohort.json
Запуск: /usr/bin/python3 (нужен osgeo)."""
import json
import sys
import time
from datetime import datetime, timedelta

from osgeo import ogr

sys.path.insert(0, "/home/z/my-project/scripts")
import sites_step2_cmr as s2  # noqa: E402  (переиспользуем http_json, cmr_slc, parse_ring...)

ogr.UseExceptions()
CACHE = "/home/z/my-project/scripts/_cache"


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
    return round(s, 3)


def resolve_granules(cand, chain):
    """Точные гранулы SLC для дат pre/post/control: узкое окно +-1 день,
    фильтр по платформе/IW/относительной орбите, выбор максимального покрытия."""
    geom = ogr.CreateGeometryFromWkt(cand["wkt"])
    hull = geom.ConvexHull()
    env = hull.GetEnvelope()
    bbox = [env[0] - 0.05, env[2] - 0.05, env[1] + 0.05, env[3] + 0.05]
    ref = s2.bbox_poly(bbox)
    out = {}
    for role in ("pre", "post", "control"):
        d = datetime.strptime(chain[role], "%Y-%m-%d")
        entries = s2.cmr_slc(bbox, d - timedelta(days=1), d + timedelta(days=1))
        time.sleep(0.35)
        cands = []
        for e in entries:
            try:
                t = datetime.strptime(e["time_start"][:19], "%Y-%m-%dT%H:%M:%S")
            except Exception:  # noqa: BLE001
                continue
            if t.date() != d.date():
                continue
            plat = "S1B" if e["title"].startswith("S1B_") else "S1A"
            if plat != chain["platform"] or "_IW_" not in e["title"]:
                continue
            poly = s2.entry_polygon(e, ref)
            if poly is None:
                continue
            try:
                cov = poly.Intersection(hull).GetArea() / hull.GetArea()
            except Exception:  # noqa: BLE001
                cov = 0.0
            dom = (e.get("orbit_calculated_spatial_domains") or [{}])[0]
            try:
                rel = int(dom.get("orbit_number")) % 175
            except Exception:  # noqa: BLE001
                rel = None
            cands.append({"title": e["title"], "cov": cov, "rel": rel, "t": t.isoformat()})
        ok = [c for c in cands
              if chain["rel_orbit"] is None or c["rel"] == chain["rel_orbit"]]
        pool = ok or cands
        if not pool:
            raise RuntimeError(f"ID{cand['id']} {role}: гранул не найдено")
        pick = max(pool, key=lambda c: c["cov"])
        pick["cov"] = round(pick["cov"], 3)
        pick["rel_match"] = bool(ok)
        if pick["cov"] < 0.85:
            raise RuntimeError(f"ID{cand['id']} {role}: coverage {pick['cov']} < 0.85")
        out[role] = pick
    plats = {out[r]["title"][:4] for r in out}
    if len(plats) != 1:
        raise RuntimeError(f"ID{cand['id']}: разные платформы {plats}")
    return out


def main():
    base = json.load(open(f"{CACHE}/sites_base.json"))
    cmr = {r["key"]: r for r in json.load(open(f"{CACHE}/sites_cmr.json"))}
    land = json.load(open(f"{CACHE}/sites_landscape.json"))

    full_ranked = []
    for c in base["candidates"]:
        key = f"{c['id']}_{c['date_1'].replace('/', '')}"
        r = cmr.get(key)
        if not (r and r["chain"] and r["chain"]["quality"] == "full"):
            continue
        year = int(c["date_1"][:4])
        if not (2015 <= year <= 2017):
            continue
        l = land.get(key, {})
        s = score(c, r["chain"], l)
        full_ranked.append({"score": s, "cand": c, "chain": r["chain"], "land": l})

    full_ranked.sort(key=lambda x: -x["score"])
    print("Все full-кандидаты 2015-2017 (отсортированы по скору):")
    for x in full_ranked:
        c, ch, l = x["cand"], x["chain"], x["land"]
        print(f"  {x['score']:.3f} id{c['id']:>4}(storm {c['storm_id']}) {c['type']:>8} "
              f"{c['date_1']} {c['area_km2']:>5} км2 лес={l.get('forest_frac')} "
              f"вода={l.get('water_frac')} уклон={l.get('slope_mean_deg')} "
              f"{ch['platform']} {ch['pre']}|{ch['post']}|{ch['control']}")

    tor = [x for x in full_ranked if x["cand"]["type"] == "tornado"][:3]
    squ = [x for x in full_ranked if x["cand"]["type"] == "squall"][:2]
    cohort = tor + squ
    if len(tor) < 3 or len(squ) < 2:
        raise RuntimeError(f"не набралась когорта: торнадо {len(tor)}, шквалов {len(squ)}")

    print("\nФИНАЛЬНАЯ КОГОРТА (3 торнадо + 2 шквала):")
    out_cohort = []
    for x in cohort:
        c, ch, l = x["cand"], x["chain"], x["land"]
        grans = resolve_granules(c, ch)
        print(f"  id{c['id']} {c['type']} {c['date_1']} score={x['score']}")
        for role in ("pre", "post", "control"):
            g = grans[role]
            print(f"    {role:>7}: {g['title']} cov={g['cov']} rel={g['rel']}")
        out_cohort.append({
            "id": c["id"], "storm_id": c["storm_id"], "key": x["key"] if "key" in x else f"{c['id']}_{c['date_1'].replace('/', '')}",
            "type": c["type"], "date_1": c["date_1"], "area_km2": c["area_km2"],
            "length_km": c.get("length_km"), "mean_width_m": c.get("mean_width_m"),
            "max_width_m": c.get("max_width_m"), "wind_gust": c.get("wind_gust"),
            "direction": c.get("direction"), "certainty": c.get("certainty"),
            "lon": c.get("lon"), "lat": c.get("lat"),
            "score": x["score"], "chain": ch, "landscape": l, "granules": grans,
        })

    jobs = []
    for e in out_cohort:
        for kind, roles in (("prepost", ("pre", "post")), ("control", ("post", "control"))):
            jobs.append({
                "name": f"id{e['id']}-coh-{kind}",
                "granules": [e["granules"][r]["title"] for r in roles],
                "credits": 10,
            })
    res = {"generated": datetime.now().isoformat(timespec="seconds"),
           "rule": "3 tornado + 2 squall, 2015-2017, quality=full only",
           "n_full_candidates": len(full_ranked), "cohort": out_cohort,
           "planned_jobs": jobs,
           "planned_cost_credits": sum(j["credits"] for j in jobs)}
    with open(f"{CACHE}/sites_cohort.json", "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\nПланируется джоб: {len(jobs)}, стоимость {res['planned_cost_credits']} кредитов")
    for j in jobs:
        print(f"  {j['name']}: {j['granules'][0][15:23]} -> {j['granules'][1][15:23]}")
    print("saved", f"{CACHE}/sites_cohort.json")


if __name__ == "__main__":
    main()
