#!/usr/bin/env python3
"""Step 3: landscape proxies for CMR-feasible candidates via Planetary Computer:
  - ESA WorldCover 2021 (10 m, EPSG:4326): forest / water / wetland fractions
    inside the damage-track hull;
  - Copernicus GLO-30 DEM (30 m): mean & p90 slope inside the hull.
Run with /usr/bin/python3 (osgeo + numpy). Output: _cache/sites_landscape.json
"""
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()

# speed up /vsicurl on Azure COGs
gdal.SetConfigOption("GDAL_INGESTED_BYTES_AT_OPEN", "2000000")
gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "3")
gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "2")

CACHE = "/home/z/my-project/scripts/_cache"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{}"
UA = {"User-Agent": "windthrow-site-screener/1.0"}

# WorldCover classes
TREE = {10, 95}
WATER = {80}
WET = {90}


def get_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            import time
            time.sleep(2 * (i + 1))
    raise last


def pc_token(collection):
    return get_json(SAS.format(collection)).get("token", "")


def pc_item(collection, bbox, dt=None):
    """STAC search: GET first, POST fallback. Returns (item, assets) or (None, None)."""
    bbs = ",".join(f"{x:.4f}" for x in bbox)
    params = {"collections": collection, "bbox": bbs, "limit": "5"}
    if dt:
        params["datetime"] = dt
    try:
        url = STAC + "?" + urllib.parse.urlencode(params)
        d = get_json(url)
        feats = d.get("features") or []
    except Exception:  # noqa: BLE001
        feats = []
    if not feats:
        body = json.dumps({"collections": [collection],
                           "bbox": [round(x, 4) for x in bbox],
                           **({"datetime": dt} if dt else {}), "limit": 5}).encode()
        req = urllib.request.Request(STAC, data=body,
                                     headers={"Content-Type": "application/json", **UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.load(r)
        feats = d.get("features") or []
    if not feats:
        return None, None
    it = feats[0]
    return it, it["assets"]


def read_window(href, bbox, ref_srs_wgs84=True, max_px=2500):
    """Windowed read of a WGS84 COG for bbox (overview if possible).
    Returns (array, geotransform)."""
    ds = gdal.Open(href, gdal.GA_ReadOnly)
    gt = ds.GetGeoTransform()
    rb = ds.GetRasterBand(1)
    x0 = int((bbox[0] - gt[0]) / gt[1])
    x1 = int((bbox[2] - gt[0]) / gt[1])
    y0 = int((bbox[3] - gt[3]) / gt[5])   # top -> row
    y1 = int((bbox[1] - gt[3]) / gt[5])   # bottom -> row
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    xa, ya = max(xa, 0), max(ya, 0)
    xb = min(xb, rb.XSize - 1)
    yb = min(yb, rb.YSize - 1)
    nx, ny = xb - xa + 1, yb - ya + 1
    if nx <= 0 or ny <= 0:
        return None, None

    # pick the finest overview whose window fits max_px
    cand_bands = []
    n_ov = rb.GetOverviewCount()
    for i in range(n_ov):
        ov = rb.GetOverview(i)
        if ov is not None and ov.XSize > 0:
            cand_bands.append((ov.XSize, ov))
    cand_bands.sort(key=lambda t: -t[0])
    chosen, step = rb, 1
    for ov_x, ov in cand_bands:
        s = max(1, round(rb.XSize / ov_x))
        if nx // s <= max_px and ny // s <= max_px:
            chosen, step = ov, s
            break
    ax, ay = xa // step, ya // step
    anx, any_ = nx // step, ny // step
    if anx <= 0 or any_ <= 0:
        return None, None
    arr = chosen.ReadAsArray(ax, ay, anx, any_)
    gt2 = (gt[0] + ax * gt[1] * step, gt[1] * step, gt[2],
           gt[3] + ay * gt[5] * step, gt[4], gt[5] * step)
    return arr, gt2


def hull_mask(wkt, gt, shape):
    """Rasterize the hull WKT into the array grid -> boolean mask."""
    nx, ny = shape[1], shape[0]
    drv = ogr.GetDriverByName("Memory")
    vds = drv.CreateDataSource("m")
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    lyr = vds.CreateLayer("hull", srs, ogr.wkbPolygon)
    feat = ogr.Feature(lyr.GetLayerDefn())
    feat.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
    lyr.CreateFeature(feat)
    tgt_ds = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Byte)
    tgt_ds.SetGeoTransform(gt)
    tgt_ds.SetProjection(srs.ExportToWkt())
    gdal.RasterizeLayer(tgt_ds, [1], lyr, burn_values=[1])
    return tgt_ds.ReadAsArray().astype(bool)


def slope_stats(dem, gt, mask, lat0):
    """Slope (degrees) from a DEM window via per-index gradients."""
    dy_m = 110_540.0
    dx_m = 111_320.0 * max(np.cos(np.deg2rad(lat0)), 0.2)
    dx_px = abs(gt[1]) * dx_m   # meters per pixel
    dy_px = abs(gt[5]) * dy_m
    gz, gx = np.gradient(dem.astype(np.float32))
    tan_s = np.hypot(gx / dx_px, gz / dy_px)  # dZ per meter horizontal
    slope = np.degrees(np.arctan(tan_s))
    vals = slope[mask & np.isfinite(slope)]
    if vals.size == 0:
        return None, None
    return float(np.mean(vals)), float(np.percentile(vals, 90))


def process_candidate(cand, wc_tok, dem_tok):
    geom = ogr.CreateGeometryFromWkt(cand["wkt"])
    hull = geom.ConvexHull()
    env = hull.GetEnvelope()
    bbox = [env[0], env[2], env[1], env[3]]
    rec = {"id": cand["id"], "key": cand["id"].__str__() + "_" + cand["date_1"].replace("/", "")}

    # --- WorldCover ---
    try:
        item, assets = pc_item("esa-worldcover", bbox,
                               dt="2021-01-01T00:00:00Z/2021-12-31T23:59:59Z")
        href = assets["map"]["href"]
        arr, gt = read_window(f"{href}?{wc_tok}", bbox, max_px=900)
        if arr is not None and gt[1] > 0:
            mask = hull_mask(cand["wkt"], gt, arr.shape)
            cls, cnt = np.unique(arr[mask], return_counts=True)
            tally = {int(c): int(n) for c, n in zip(cls, cnt)}
            total = sum(tally.values()) or 1
            rec["wc_item"] = item["id"]
            rec["forest_frac"] = round(sum(tally.get(c, 0) for c in TREE) / total, 3)
            rec["water_frac"] = round(tally.get(80, 0) / total, 3)
            rec["wetland_frac"] = round(tally.get(90, 0) / total, 3)
            rec["crop_frac"] = round(tally.get(40, 0) / total, 3)
            rec["wc_classes_top"] = sorted(tally.items(), key=lambda kv: -kv[1])[:4]
    except Exception as e:  # noqa: BLE001
        rec["wc_error"] = str(e)[:120]

    # --- GLO-30 slope ---
    try:
        item, _assets = pc_item("cop-dem-glo-30", bbox)
        # PC SAS отдаёт 403 на этот контейнер; берём публичную копию AWS Open Data
        href = f"https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/{item['id']}/{item['id']}.tif"
        arr, gt = read_window(href, bbox, max_px=600)
        if arr is not None:
            mask = hull_mask(cand["wkt"], gt, arr.shape)
            m, p90 = slope_stats(arr, gt, mask, cand["lat"])
            rec["slope_mean_deg"] = round(m, 2) if m is not None else None
            rec["slope_p90_deg"] = round(p90, 2) if p90 is not None else None
    except Exception as e:  # noqa: BLE001
        rec["dem_error"] = str(e)[:120]
    return rec


def main():
    base = json.load(open(f"{CACHE}/sites_base.json"))
    cmr = json.load(open(f"{CACHE}/sites_cmr.json"))
    feasible = {r["key"] for r in cmr if r["chain"]}
    cands = [c for c in base["candidates"]
             if f"{c['id']}_{c['date_1'].replace('/', '')}" in feasible]

    wc_tok = pc_token("esa-worldcover")
    dem_tok = pc_token("cop-dem-glo-30")
    print("tokens ok:", bool(wc_tok), bool(dem_tok), flush=True)

    out = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(process_candidate, c, wc_tok, dem_tok): c for c in cands}
        for fut in as_completed(futs):
            rec = fut.result()
            out[rec["key"]] = rec
            print(f"ID{rec['id']:>4}: forest={rec.get('forest_frac')} "
                  f"water={rec.get('water_frac')} wet={rec.get('wetland_frac')} "
                  f"slope={rec.get('slope_mean_deg')}/{rec.get('slope_p90_deg')} "
                  f"{'| WC err: ' + rec['wc_error'] if 'wc_error' in rec else ''}"
                  f"{'| DEM err: ' + rec['dem_error'] if 'dem_error' in rec else ''}",
                  flush=True)

    with open(f"{CACHE}/sites_landscape.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved", f"{CACHE}/sites_landscape.json")


if __name__ == "__main__":
    main()
