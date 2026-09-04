#!/usr/bin/env python3
"""Фаза A: ежегодные маски леса GFW Hansen GFC v1.12 на ГОД СОБЫТИЯ
для 12 обработанных объектов + сравнение с WorldCover-2021 (кэш скрининга).

Рецепт (Y = год события, L = lossyear 1..24, год потери = 2000+L):
  forest_cand(Y) = treecover>=tau & NOT(1<=L<=Y-2000)     # потеря в год события
                   не исключаем — это и есть кандидаты в ветровал
  forest_bg(Y)   = treecover>=tau & NOT(1<=L<=Y-2000+1)   # фон: чистый лес,
                   без внутригодовых потерь (рубка до шторма / сам ветровал)
  loss_in_Y      = (L == Y-2000)

Сетка: UTM (зона по lon), 30 м, окно = bbox полигона + 12 км.
Растры -> work_data/gfw/, статистика -> data_cache/gfw_masks_stats_2026-09-04.json

Запуск: /usr/bin/python3 gfw_remask.py [id ...]
"""
import json
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
gdal.SetCacheMax(64_000_000)

REPO = Path("/home/z/my-project/sar_windthrow")
GEO = REPO / "gis" / "windthrow_sites_map_2026-09-04.geojson"
TILES = Path("/home/z/my-project/work_data/gfc_tiles")
OUTW = Path("/home/z/my-project/work_data/gfw")
OUTW.mkdir(parents=True, exist_ok=True)
OUTJSON = REPO / "data_cache" / "gfw_masks_stats_2026-09-04.json"

BUFFER_M = 12_000.0
TAUS = (20, 30, 50)

# годы для объектов без props.year (из EVENT_META wave2 / EVENT_DATE step12c)
YEAR_FALLBACK = {696: 2017}

RANK = {666: 1, 694: 2, 655: 3, 583: 4, 674: 5, 608: 6, 646: 7,
        579: 8, 654: 9, 658: 10, 683: 11, 696: 12, 606: 13}


def utm_epsg(lon: float) -> int:
    return 32600 + int((lon + 180.0) // 6) + 1


def load_features():
    fc = json.loads(GEO.read_text())
    out = []
    for f in fc["features"]:
        p = f["properties"]
        eid = p.get("shikhov_id") or p.get("id")
        y = p.get("year") or YEAR_FALLBACK.get(eid)
        if eid not in RANK or y is None:
            continue
        out.append({"id": int(eid), "year": int(y), "props": p,
                    "geom": f["geometry"]})
    out.sort(key=lambda r: RANK[r["id"]])
    return out


def geom_to_ogr(geojson_geom, srs):
    g = ogr.CreateGeometryFromJson(json.dumps(geojson_geom))
    g.AssignSpatialReference(srs)
    return g


def warp_layer(src_glob: str, bbox4326, dst_srs, xres, yres, alg) -> np.ndarray:
    """Окно из тайла(ов) -> UTM 30 м. Двухступенчато: Translate srcWin
    (scanline-тайлы Hansen нельзя читать окном через Warp напрямую — OOM),
    затем Warp маленьких кусков."""
    import glob as _g
    if "://" in src_glob:
        srcs = [src_glob]
    else:
        srcs = sorted(_g.glob(src_glob))
    if not srcs:
        raise FileNotFoundError(src_glob)
    xmin, ymin, xmax, ymax = bbox4326
    pieces = []
    for i, src in enumerate(srcs):
        ds = gdal.Open(src)
        gts = ds.GetGeoTransform()
        nx, ny = ds.RasterXSize, ds.RasterYSize
        ds = None
        px0 = int((xmin - gts[0]) / gts[1])
        px1 = int((xmax - gts[0]) / gts[1]) + 2
        py0 = int((ymax - gts[3]) / gts[5])
        py1 = int((ymin - gts[3]) / gts[5]) + 2
        px0, px1 = max(0, px0), min(nx, px1)
        py0, py1 = max(0, py0), min(ny, py1)
        if px1 <= px0 or py1 <= py0:
            continue
        piece = f"/vsimem/_piece_{i}.tif"
        gdal.Translate(piece, src, srcWin=[px0, py0, px1 - px0, py1 - py0])
        pieces.append(piece)
    if not pieces:
        raise RuntimeError(f"bbox {bbox4326} вне тайлов {srcs}")
    mem = "/vsimem/_warp.tif"
    gdal.Warp(mem, pieces, format="GTiff",
              outputBounds=(xmin, ymin, xmax, ymax),
              outputBoundsSRS="EPSG:4326", dstSRS=dst_srs,
              xRes=xres, yRes=yres, resampleAlg=alg,
              warpMemoryLimit=128_000_000, multithread=False)
    ds = gdal.Open(mem)
    arr = ds.GetRasterBand(1).ReadAsArray()
    gt = ds.GetGeoTransform()
    ds = None
    for p in pieces:
        gdal.Unlink(p)
    gdal.Unlink(mem)
    return arr, gt


def rasterize_geom(geom_utm, gt, shape, srs_wkt) -> np.ndarray:
    ds = gdal.GetDriverByName("MEM").Create("", shape[1], shape[0], 1,
                                            gdal.GDT_Byte)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference(); srs.ImportFromWkt(srs_wkt)
    ds.SetProjection(srs_wkt)
    vds = ogr.GetDriverByName("Memory").CreateDataSource("m")
    lyr = vds.CreateLayer("m", srs)
    lyr.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    feat = ogr.Feature(lyr.GetLayerDefn())
    feat.SetGeometry(geom_utm)
    feat.SetField("v", 1)
    lyr.CreateFeature(feat)
    gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1])
    arr = ds.GetRasterBand(1).ReadAsArray()
    ds = None; vds = None
    return arr > 0


def frac(mask: np.ndarray, poly: np.ndarray) -> float:
    n = int(poly.sum())
    if n == 0:
        return float("nan")
    return round(float((mask & poly).sum()) / n, 4)


def save_tif(path, arr, gt, srs_wkt):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(path), arr.shape[1], arr.shape[0], 1,
                    gdal.GDT_Byte, options=["COMPRESS=LZW"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(srs_wkt)
    ds.GetRasterBand(1).WriteArray(arr.astype(np.uint8))
    ds = None


# --- Planetary Computer (WorldCover-2021, COG — оконное чтение) ----------
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{}"
UA = {"User-Agent": "windthrow-gfw-remask/1.0"}


def _get_json(url, tries=3):
    import time
    import urllib.request
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def wc_window(bbox):
    """href COG-asset esa-worldcover-2021 для bbox + SAS-токен."""
    import urllib.request
    tok = _get_json(SAS.format("esa-worldcover")).get("token", "")
    bbs = ",".join(f"{x:.4f}" for x in bbox)
    url = f"{STAC}?collections=esa-worldcover&bbox={bbs}&limit=5" \
          f"&datetime=2021-01-01T00:00:00Z/2021-12-31T23:59:59Z"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        items = json.load(r).get("features", [])
    if not items:
        return None
    href = items[0]["assets"]["map"]["href"]
    return f"{href}?{tok}", items[0]["id"]


def process(ev: dict) -> dict:
    eid, Y = ev["id"], ev["year"]
    p = ev["props"]
    lat, lon = p["lat"], p["lon"]
    epsg = utm_epsg(lon)
    srs_src = osr.SpatialReference()
    srs_src.ImportFromEPSG(4326)
    srs_src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    geom = geom_to_ogr(ev["geom"], srs_src)
    env = geom.GetEnvelope()  # (minx, maxx, miny, maxy)
    xmin, xmax, ymin, ymax = env[0], env[1], env[2], env[3]
    pad = BUFFER_M / (111_320.0 * np.cos(np.radians(lat)))  # lon-градусы
    padlat = BUFFER_M / 111_320.0
    bbox = [xmin - pad, ymin - padlat, xmax + pad, ymax + padlat]

    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(epsg)
    dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    xres = yres = 30.0

    tc30, gt = warp_layer(str(TILES / f"Hansen_GFC-2024-v1.12_treecover2000_*.tif"),
                          bbox, dst_srs, xres, yres, "bilinear")
    loss30, _ = warp_layer(str(TILES / f"Hansen_GFC-2024-v1.12_lossyear_*.tif"),
                           bbox, dst_srs, xres, yres, "near")
    h, w = loss30.shape

    geom_utm = geom.Clone()
    geom_utm.TransformTo(dst_srs)
    ring_utm = geom_utm.Buffer(BUFFER_M)

    poly = rasterize_geom(geom_utm, gt, (h, w), dst_srs.ExportToWkt())
    ring = rasterize_geom(ring_utm, gt, (h, w), dst_srs.ExportToWkt())

    L = loss30.astype(np.int16)
    L0 = (L == 0)
    valid = (tc30 >= 0) & (L >= 0)
    ymax = Y - 2000
    out = {"id": eid, "year": Y, "epsg": epsg, "type": p.get("type"),
           "grid": {"res_m": 30, "w": w, "h": h},
           "tile": TILES.name}
    stats = {}
    for tau in TAUS:
        ftc = (tc30 >= tau) & valid
        # кандидаты: исключаем ТОЛЬКО потери ДО года события (1..ymax-1);
        # потеря в год события (L==ymax) — сам ветровал, из кандидатов не убираем
        fcand = ftc & (L0 | (L >= ymax))
        # фон: чистый лес на дату события — исключаем и внутригодовые потери
        fbg = ftc & (L0 | (L >= ymax + 1))
        if tau == 30:
            stats["forest_cand_frac"] = frac(fcand, poly)
            stats["forest_bg_frac"] = frac(fbg, poly)
            stats["loss_in_Y_frac"] = frac((L == ymax) & ftc, poly)
            stats["ring_forest_cand_frac"] = frac(fcand, ring & ~poly)
            srs_wkt = dst_srs.ExportToWkt()
            save_tif(OUTW / f"id{eid}_fcand30.tif", fcand, gt, srs_wkt)
            save_tif(OUTW / f"id{eid}_fbg30.tif", fbg, gt, srs_wkt)
            save_tif(OUTW / f"id{eid}_poly30.tif", poly, gt, srs_wkt)
        stats[f"forest_cand_frac_tau{tau}"] = frac(fcand, poly)
    stats["treecover_med_poly"] = (round(float(np.median(tc30[poly])), 1)
                                   if poly.any() else None)
    # события-флаги
    stats["outside_gfw_forest"] = bool(stats["forest_cand_frac"] < 0.30)
    # --- WC-2021: свежее окно из PC (COG) -> 30 м UTM, класс 10/95 = лес;
    # fallback — кэш скрининга sites_landscape.json
    wc, wc_src, wc_err = None, None, None
    try:
        wcw = wc_window(bbox)
        if wcw:
            href, item_id = wcw
            wc30, _ = warp_layer(href, bbox, dst_srs, xres, yres, "near")
            wcf = np.isin(wc30, (10, 95))
            save_tif(OUTW / f"id{eid}_wc30.tif", wcf, gt, dst_srs.ExportToWkt())
            wc = frac(wcf, poly)
            wc_src = item_id
    except Exception as e:  # noqa: BLE001
        wc_err = str(e)[:100]
    if wc is None:
        try:
            lc = json.loads((REPO / "data_cache" / "sites_landscape.json").read_text())
            for k, row in lc.items():
                if (row.get("id") or row.get("shikhov_id")) == eid:
                    wc = row.get("forest_frac")
                    wc_src = f"cache:{k}"
                    break
        except Exception:
            pass
    stats["wc2021_forest_frac"] = wc
    stats["wc_source"] = wc_src
    if wc_err is not None:
        stats["wc_error"] = wc_err
    if wc is not None and wc == wc:
        stats["wc2021_minus_gfw"] = round(wc - stats["forest_cand_frac"], 3)
    out["stats"] = {k: v for k, v in stats.items()}
    return out


def main():
    ids = {int(a) for a in sys.argv[1:]} or None
    feats = load_features()
    if ids:
        feats = [f for f in feats if f["id"] in ids]
    # merge: сохраняем результаты предыдущих порций
    rows = []
    if OUTJSON.exists():
        try:
            old = json.loads(OUTJSON.read_text()).get("sites", [])
            done_ids = {f["id"] for f in feats}
            rows = [r for r in old if r.get("id") not in done_ids]
        except Exception:
            pass
    for ev in feats:
        try:
            r = process(ev)
            s = r["stats"]
            rows.append(r)
            print(f"id{ev['id']} Y{ev['year']}: GFW лес(кандидат) "
                  f"{s['forest_cand_frac']:.2f} | фон-лес {s['forest_bg_frac']:.2f} | "
                  f"loss(Y) {s['loss_in_Y_frac']:.2f} | WC2021 {s['wc2021_forest_frac']} "
                  f"| outside={s['outside_gfw_forest']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"id{ev['id']}: FAIL {type(e).__name__}: {e}", flush=True)
            rows.append({"id": ev["id"], "year": ev["year"],
                         "error": f"{type(e).__name__}: {e}"})
        OUTJSON.write_text(json.dumps({"generated": "2026-09-04",
                                       "source": "Hansen GFC v1.12 (2000-2024), tau=30",
                                       "sites": rows}, ensure_ascii=False, indent=1))
    print(f"\nsaved: {OUTJSON}")


if __name__ == "__main__":
    main()
