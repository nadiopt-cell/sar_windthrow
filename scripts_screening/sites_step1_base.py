#!/usr/bin/env python3
"""Step 1: build the site universe from the Shikhov & Erlikh DB (WGS84 shape).

Outputs scripts/_cache/sites_base.json:
  - all 7 processed events (full DB attrs + geometry + HyP3 linkage + known metrics)
  - filtered candidate pool for new validation round
Geometry is stored as simplified WGS84 WKT (map-ready, small).
Run with /usr/bin/python3 (needs osgeo).
"""
import json
import os
from collections import Counter

from osgeo import ogr

ogr.UseExceptions()

GIS = "/home/z/my-project/research/shikhov_db/GIS"
CACHE = "/home/z/my-project/scripts/_cache"
os.makedirs(CACHE, exist_ok=True)

PROCESSED = [666, 694, 655, 583, 674, 608, 646]

# known headline metrics (from surviving JSONs / report изд.4); None = see report
METRICS = {
    694: {"coh_delta_auc": 0.908, "coh_delta_excess": 0.308,
          "lband_dHV_invAUC": 0.905, "lband_dHH_invAUC": "0.733-0.805",
          "note": "лучшая площадка проекта (торнадо S486)"},
    666: {"wi_PA": 0.059, "wi_UA": 0.024, "wi_fake_polygons": 2371,
          "lband_dHV_auc": 0.268,
          "note": "основное событие v1-валидации; WI = контр-пример"},
    655: {"note": "DiD-расширение"},
    583: {"note": "DiD-расширение"},
    674: {"note": "DiD-расширение; v1-джобы S1A rel108 не покрывали трек, довыполнены v2 на S1B rel62"},
    608: {"note": "DiD-расширение; v1-джобы на южном кадре, v2 на северной цепочке T034515"},
    646: {"note": "DiD-расширение"},
}


def poly_to_wkt(geom, tol=0.003, max_wkb=1_500_000):
    """Simplify multipart geometry for map output (tol ~300 m), keeping the
    real patch layout; convex hull only as a last-resort size fallback."""
    g = geom.Clone()
    while True:
        s = g.SimplifyPreserveTopology(tol)
        if s is not None and not s.IsEmpty():
            try:
                if not s.IsValid():
                    s = s.MakeValid() or s
            except Exception:  # noqa: BLE001
                pass
            if s.WkbSize() <= max_wkb:
                return s.ExportToWkt(), s
        tol *= 2
        if tol > 0.03:
            h = g.ConvexHull()
            h = h.SimplifyPreserveTopology(0.005) or h
            return h.ExportToWkt(), h


def main():
    ds = ogr.Open(os.path.join(GIS, "Windthrows.shp"))
    lyr = ds.GetLayer(0)
    fields = ("ID", "Storm_ID", "Storm_type", "Certainty", "Year", "Month",
              "Date", "Date_1", "Date_2", "N_polygons", "Area", "Length",
              "Mean_width", "Max_width", "Wind_gust", "Direction",
              "Near_WS", "WS_dist")
    recs = []
    for feat in lyr:
        r = {k: feat.GetField(k) for k in fields}
        wkt, simp = poly_to_wkt(feat.GetGeometryRef())
        hull = feat.GetGeometryRef().ConvexHull()
        r["_wkt"] = wkt
        r["_hull_area_deg2"] = hull.GetArea()
        r["_n_vertices"] = simp.GetGeometryCount() and sum(
            p.GetPointCount() for p in simp) or simp.GetPointCount()
        cen = hull.Centroid()
        r["_lon"], r["_lat"] = cen.GetX(), cen.GetY()
        recs.append(r)
    ds = None
    print("Windthrows features:", len(recs))
    print("types:", dict(Counter(str(r["Storm_type"]) for r in recs)))

    by_id = {r["ID"]: r for r in recs}

    # --- sanity check of the 7 processed ---
    proc = []
    for pid in PROCESSED:
        r = by_id.get(pid)
        assert r is not None, f"ID{pid} not in DB!"
        proc.append(r)
        print(f"  ID{pid}: {r['Storm_type']} {r['Date_1']} area={r['Area']} km2 "
              f"n_poly={r['N_polygons']} w={r['Mean_width']}m cert={r['Certainty']}")

    # --- candidate pool (same base filter as the original s1_era script) ---
    era = [r for r in recs if r["Year"] and r["Year"] >= 2015
           and r["Area"] and r["Area"] >= 0.5
           and r["Certainty"] in ("High", "Medium")
           and str(r["Date_1"] or "") not in ("", "None", "-9999")]
    print("\nbase pool (year>=2015, area>=0.5, High/Medium, date known):", len(era))
    old = json.load(open("/home/z/my-project/research/shikhov_db/s1_era_candidates.json"))
    print("old artifact count:", len(old),
          "| set match:", {r['ID'] for r in era} == {r['ID'] for r in old})

    # --- my filters ---
    def month_of(r):
        try:
            return int(str(r["Date_1"]).split("/")[1])
        except Exception:
            return None

    f1 = [r for r in era if month_of(r) in (5, 6, 7, 8, 9)]
    print("warm season (May-Sep):", len(f1))
    f2 = [r for r in f1 if str(r["Storm_type"]).lower() != "snowstorm"]
    print("non-snowstorm:", len(f2))
    f3 = [r for r in f2 if r["ID"] not in PROCESSED]
    print("excl. processed 7:", len(f3))
    f4 = [r for r in f3 if r["Mean_width"] and r["Mean_width"] >= 150]
    print("mean_width >= 150 m:", len(f4))
    f5 = [r for r in f4 if r["Certainty"] == "High"]
    print("certainty High:", len(f5))

    def pack(r, status):
        return {
            "id": r["ID"], "storm_id": r["Storm_ID"], "type": r["Storm_type"],
            "certainty": r["Certainty"], "year": r["Year"], "month": r["Month"],
            "date": str(r["Date"]), "date_1": str(r["Date_1"]), "date_2": str(r["Date_2"]),
            "n_polygons": r["N_polygons"], "area_km2": round(r["Area"], 2) if r["Area"] else None,
            "length_km": r["Length"], "mean_width_m": r["Mean_width"],
            "max_width_m": r["Max_width"], "wind_gust": r["Wind_gust"],
            "direction": str(r["Direction"]), "near_watershed": str(r["Near_WS"]),
            "ws_dist_km": r["WS_dist"],
            "lon": round(r["_lon"], 4), "lat": round(r["_lat"], 4),
            "hull_area_deg2": round(r["_hull_area_deg2"], 6),
            "wkt": r["_wkt"], "n_vertices": r["_n_vertices"],
            "status": status,
        }

    out = {
        "generated": "2026-09-04",
        "crs": "EPSG:4326",
        "processed": [pack(r, "processed") for r in proc],
        "candidates": [pack(r, "candidate") for r in f5],
        "candidates_medium_backup": [pack(r, "candidate-medium") for r in f4 if r["Certainty"] != "High"],
        "filter_stats": {
            "windthrows_total": len(recs), "base_pool": len(era),
            "warm": len(f1), "non_snow": len(f2), "excl_processed": len(f3),
            "width150": len(f4), "high": len(f5)},
    }
    for r in out["processed"]:
        r["metrics"] = METRICS.get(r["id"], {})
    with open(f"{CACHE}/sites_base.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("\ncandidates High:")
    for r in sorted(out["candidates"], key=lambda x: -x["area_km2"]):
        print(f"  ID{r['id']:>4} {r['type']:>9} {r['date_1']} {r['area_km2']:>7.2f} km2 "
              f"w={r['mean_width_m']:>5}m L={r['length_km']}km poly={r['n_polygons']}")
    print(f"\nsaved {CACHE}/sites_base.json "
          f"({len(out['processed'])} processed + {len(out['candidates'])} candidates + "
          f"{len(out['candidates_medium_backup'])} medium-backup)")


if __name__ == "__main__":
    main()
