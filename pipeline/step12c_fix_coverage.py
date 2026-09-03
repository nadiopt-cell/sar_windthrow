#!/usr/bin/env python3
"""Check actual SLC footprints (CMR umm geometry) vs event cluster polygons.

Events to fix:
  - ID674 (tornado 02.08.2017, cluster lon 33.25 lat 56.66): S1A rel108 frame
    has nodata at the AOI -> try S1B rel62 triple (30.07/11.08/23.08)
  - ID608 (tornado 13.07.2016, cluster ~35.9E 55.4N): S1A rel64 frame does not
    cover the AOI -> list ALL SLC over the bbox and test footprints
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

UA = {"User-Agent": "sar-windthrow-step12c-fix/1.0"}
CID = {"S1A": "C1214470488-ASF", "S1B": "C1327985661-ASF"}

CASES = {
    "ID674": {
        "cluster_bbox": [33.20, 56.61, 33.31, 56.72],   # dense cluster +-0.05
        "t0": "2017-07-15", "t1": "2017-09-05",
    },
    "ID608": {
        "cluster_bbox": [35.55, 55.30, 36.25, 55.45],   # cluster +-0.05
        "t0": "2016-06-25", "t1": "2016-08-15",
    },
}


def http_json(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def granule_ring(item):
    """Return list of (lon, lat) rings of the granule footprint."""
    se = item.get("umm", {}).get("SpatialExtent", {})
    if isinstance(se, list):
        se = se[0] if se else {}
    geoms = (se.get("HorizontalSpatialDomain", {})
               .get("Geometry", {}).get("GPolygons", []))
    rings = []
    for gp in geoms:
        ring = [(p.get("Longitude"), p.get("Latitude"))
                for p in gp.get("Boundary", {}).get("Points", [])]
        if ring:
            rings.append(ring)
    return rings


def ring_intersects_bbox(ring, bbox):
    """Ring-vs-bbox overlap test via any-point-in-bbox + bbox-point-in-ring."""
    minx, miny, maxx, maxy = bbox
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    if not lons:
        return False
    if max(lons) < minx or min(lons) > maxx or max(lats) < miny or min(lats) > maxy:
        return False
    # more precise: ring polygon vs bbox via ogr
    from osgeo import ogr
    ogr.UseExceptions()
    poly = ogr.Geometry(ogr.wkbPolygon)
    lr = ogr.Geometry(ogr.wkbLinearRing)
    for lon, lat in ring:
        lr.AddPoint_2D(lon, lat)
    lr.AddPoint_2D(ring[0][0], ring[0][1])
    poly.AddGeometry(lr)
    bb = ogr.Geometry(ogr.wkbPolygon)
    lr2 = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)):
        lr2.AddPoint_2D(x, y)
    lr2.AddPoint_2D(minx, miny)
    bb.AddGeometry(lr2)
    return bool(poly.Intersects(bb)) and not poly.Within(
        ogr.CreateGeometryFromWkt("POINT (0 0)").Buffer(0.0001))


def bbox_cover_frac(ring, bbox, n=12):
    """Fraction of a n x n grid over the cluster bbox inside the ring."""
    from osgeo import ogr
    ogr.UseExceptions()
    poly = ogr.Geometry(ogr.wkbPolygon)
    lr = ogr.Geometry(ogr.wkbLinearRing)
    for lon, lat in ring:
        lr.AddPoint_2D(lon, lat)
    lr.AddPoint_2D(ring[0][0], ring[0][1])
    poly.AddGeometry(lr)
    minx, miny, maxx, maxy = bbox
    hit = 0
    tot = 0
    for i in range(n):
        for j in range(n):
            x = minx + (i + 0.5) * (maxx - minx) / n
            y = miny + (j + 0.5) * (maxy - miny) / n
            pt = ogr.Geometry(ogr.wkbPoint)
            pt.AddPoint_2D(x, y)
            tot += 1
            hit += bool(poly.Contains(pt))
    return hit / tot


def main():
    for case, cfg in CASES.items():
        print(f"\n=== {case} cluster bbox {cfg['cluster_bbox']}")
        for plat in ("S1A", "S1B"):
            params = {
                "collection_concept_id": CID[plat],
                "bounding_box": ",".join(str(x) for x in cfg["cluster_bbox"]),
                "temporal": (f"{cfg['t0']}T00:00:00Z,{cfg['t1']}T23:59:59Z"),
            }
            q = "&".join(f"{k}={urllib.parse.quote(str(v))}"
                         for k, v in params.items())
            d = http_json(f"https://cmr.earthdata.nasa.gov/search/granules.umm_json?{q}&page_size=100")
            print(f"  {plat}: hits={d.get('hits')}")
            for it in d.get("items", []):
                name = (it.get("umm", {}).get("DataGranule", {})
                           .get("Identifiers", [{}])[0].get("Identifier", ""))
                if "_SLC__" not in name:
                    continue
                f = name.split("_")
                rings = granule_ring(it)
                frac = max((bbox_cover_frac(r, cfg["cluster_bbox"]) for r in rings), default=0)
                print(f"    {f[5]} orb {f[7]} rel{int(f[7])%175:3d} cover={frac:.2f} {name[:44]}")


if __name__ == "__main__":
    main()
