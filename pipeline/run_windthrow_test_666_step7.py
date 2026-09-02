#!/usr/bin/env python3
"""Step7 of the ID666 windthrow test (Rüetschi 2019 / plugin v0.8).

Stages (run separately — each finishes well inside the sandbox timeout):

  warp    --label pre3|pre2|pre1|base|post|masks|all
          STAC search (PC sentinel-1-rtc), download VV/VH, warp to the
          AOI grid (EPSG:32638, 10 m, snapped), build background mask
          (3 km buffer around ID666 MINUS the polygons) and the
          reference mask.  Manifest -> download/step7_warp_manifest.json

  detect  --variant A|B|C|D|all
          A pair+normOFF, B pair+normON, C stack+normOFF, D stack+normON.
          background_mask=bg_mask.tif for ALL variants (x̄ from the
          forest sample, decision 4).  Per-variant JSON -> download/.

  report  PA/UA object-based per variant vs ID666 polygons;
          summary -> download/windthrow_id666_baselines_<date>.json
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

PROJ = "/home/z/my-project"
PLUGIN_ROOT = f"{PROJ}/plugin_work"
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

from sentinel1_windthrow_plugin.sources.planetary_computer import (  # noqa: E402
    PlanetaryComputerSource,
)
from sentinel1_windthrow_plugin.sources.windthrow import (  # noqa: E402
    WindthrowDetector,
)

import numpy as np  # noqa: E402

WORK = f"{PROJ}/work_data"
RAW = f"{WORK}/raw"
WARP = f"{WORK}/warp"
OUT = f"{WORK}/out"
DL = f"{PROJ}/download"
REF_SHP = f"{PROJ}/research/shikhov_db/GIS/Windthrows.shp"

EPSG_AOI = 32638
PIX = 10.0
BUFFER_M = 3000.0
EVENT_ID = 666
COLLECTION = "sentinel-1-rtc"
ORBIT_DIR = "Descending"

#: label -> expected acquisition date (S1B descending, 12-day cycle;
#: BASE orbit 6420 = 22.07.2017 — verified against STAC at run time).
EXPECTED = {
    "pre3": "2017-06-16",
    "pre2": "2017-06-28",
    "pre1": "2017-07-10",
    "base": "2017-07-22",
    "post": "2017-08-03",
    "post2": "2017-08-15",
}
LABELS = ["pre3", "pre2", "pre1", "base", "post", "post2"]

MANIFEST = f"{DL}/step7_warp_manifest.json"


# ======================================================================
# Geometry helpers
# ======================================================================
def event_geometry_aoi():
    """Return (union_geom, buffer_geom, diff_geom) in EPSG:32638."""
    ds = ogr.Open(REF_SHP)
    lyr = ds.GetLayer(0)
    lyr.SetAttributeFilter(f"ID = {EVENT_ID}")
    feat = lyr.GetNextFeature()
    if feat is None:
        raise RuntimeError(f"Event ID={EVENT_ID} not found in {REF_SHP}")
    geom = feat.GetGeometryRef().Clone()
    ds = None

    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # lon,lat
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(EPSG_AOI)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(src, dst)
    geom.Transform(tr)

    union = None
    for i in range(geom.GetGeometryCount()):
        part = geom.GetGeometryRef(i).Clone()
        union = part if union is None else union.Union(part)
    buffer = union.Buffer(BUFFER_M)
    diff = buffer.Difference(union)
    return union, buffer, diff


def snapped_grid(geom):
    """Snap a geometry envelope to the 10 m grid -> (bounds, w, h)."""
    minx, maxx, miny, maxy = geom.GetEnvelope()
    minx = minx // PIX * PIX
    miny = miny // PIX * PIX
    maxx = (maxx // PIX + 1) * PIX
    maxy = (maxy // PIX + 1) * PIX
    w = int(round((maxx - minx) / PIX))
    h = int(round((maxy - miny) / PIX))
    return (minx, miny, maxx, maxy), w, h


def load_grid():
    """Load the AOI grid from the manifest (warp stage writes it)."""
    with open(MANIFEST) as f:
        man = json.load(f)
    return man["aoi"], man["grid_w"], man["grid_h"]


def rasterize_geom(geom, out_path, bounds, w, h, nodata=0):
    """Burn a single geometry onto the AOI grid (inside=255)."""
    if os.path.exists(out_path):
        gdal.GetDriverByName("GTiff").Delete(out_path)
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(out_path, w, h, 1, gdal.GDT_Byte,
                    options=["TILED=YES", "COMPRESS=LZW"])
    gt = (bounds[0], PIX, 0.0, bounds[3], 0.0, -PIX)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG_AOI)
    ds.SetProjection(srs.ExportToWkt())
    mem = ogr.GetDriverByName("Memory").CreateDataSource("mem")
    lyr = mem.CreateLayer("g", srs, ogr.wkbPolygon)
    feat = ogr.Feature(lyr.GetLayerDefn())
    feat.SetGeometry(geom)
    lyr.CreateFeature(feat)
    gdal.RasterizeLayer(ds, [1], lyr, burn_values=[255])
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.FlushCache()
    ds = None
    mem = None
    return out_path


# ======================================================================
# Stage: warp
# ======================================================================
def pick_scene(source, bounds_wgs84, expected_iso):
    """Search ±1 day around the expected date; return the best match."""
    d0 = datetime.strptime(expected_iso, "%Y-%m-%d")
    scenes = source.search(
        bbox=bounds_wgs84,
        start_date=d0 - timedelta(days=1),
        end_date=d0 + timedelta(days=1, hours=23, minutes=59),
        polarization="VV+VH",
        orbit=ORBIT_DIR.title(),
        collection=COLLECTION,
    )
    if not scenes:
        return None
    want_mid = d0 + timedelta(hours=12)

    def score(s):
        dt = abs((s.datetime.replace(tzinfo=None) - want_mid).total_seconds())
        return dt

    scenes.sort(key=score)
    return scenes[0]


def stage_warp(label):
    os.makedirs(WARP, exist_ok=True)
    union, buffer, diff = event_geometry_aoi()
    bounds, w, h = snapped_grid(buffer)

    # AOI bounds back to WGS84 for the STAC search
    src = osr.SpatialReference()
    src.ImportFromEPSG(EPSG_AOI)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(src, dst)
    ring = ogr.Geometry(ogr.wkbLinearRing)
    ring.AddPoint_2D(bounds[0], bounds[1])
    ring.AddPoint_2D(bounds[2], bounds[1])
    ring.AddPoint_2D(bounds[2], bounds[3])
    ring.AddPoint_2D(bounds[0], bounds[3])
    ring.AddPoint_2D(bounds[0], bounds[1])
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    poly.Transform(tr)
    b = poly.GetEnvelope()
    bbox_wgs84 = (b[0], b[2], b[1], b[3])  # (minx, miny, maxx, maxy)

    if label == "masks":
        ref_path = rasterize_geom(union, f"{WARP}/ref_mask.tif", bounds, w, h)
        bg_path = rasterize_geom(diff, f"{WARP}/bg_mask.tif", bounds, w, h)
        print(f"masks written: {ref_path}, {bg_path}")
        man = _load_manifest()
        man["masks"] = {"ref": ref_path, "bg": bg_path}
        man["aoi_bounds_32638"] = list(bounds)
        man["grid_w"], man["grid_h"] = w, h
        man["epsg"], man["pixel_m"] = EPSG_AOI, PIX
        man["buffer_m"] = BUFFER_M
        man["event_id"], man["reference_shp"] = EVENT_ID, REF_SHP
        _save_manifest(man)
        return

    expected_iso = EXPECTED[label]
    out_files = {pol: f"{WARP}/{label}_{pol.lower()}.tif"
                 for pol in ("VV", "VH")}
    if all(os.path.isfile(p) and os.path.getsize(p) > 1000
           for p in out_files.values()):
        print(f"[{label}] warp files exist — skip")
        return

    source = PlanetaryComputerSource(collection=COLLECTION)
    scene = pick_scene(source, bbox_wgs84, expected_iso)
    if scene is None:
        raise RuntimeError(
            f"[{label}] no {COLLECTION} scene near {expected_iso} "
            f"(±1 d, {ORBIT_DIR.lower()}, VV+VH) — STOP, human check needed")
    print(f"[{label}] picked {scene.id} "
          f"dt={scene.datetime.isoformat()} platform={scene.platform} "
          f"rel_orbit={scene.relative_orbit} orbit={scene.orbit_direction}")

    # Warp DIRECTLY from the remote COG (/vsicurl + signed SAS href).
    # The RTC grid is EPSG:32638 @ 10 m with 10-m-aligned origin, i.e.
    # pixel-aligned with the AOI grid — so a plain windowed copy is used
    # (only the AOI window, ~110 MB per band, no resampling needed).
    # A true gdal.Warp is the fallback for non-aligned grids.
    for pol in ("vv", "vh"):
        href = scene.assets.get(pol)
        if not href:
            raise RuntimeError(
                f"[{label}] asset '{pol}' missing in {scene.id} "
                f"(assets: {list(scene.assets)})")
        out_path = out_files[pol.upper()]
        if os.path.exists(out_path):
            gdal.GetDriverByName("GTiff").Delete(out_path)
        t1 = time.time()
        remote_path = f"/vsicurl/{href}"
        remote = gdal.Open(remote_path, gdal.GA_ReadOnly)
        if remote is None:
            raise RuntimeError(f"[{label}] cannot open remote COG ({pol})")
        rgt = remote.GetGeoTransform()
        remote = None
        same_crs = _epsg_of_proj(remote_path) == EPSG_AOI
        dx = (bounds[0] - rgt[0]) / PIX
        dy = (rgt[3] - bounds[3]) / PIX
        aligned = (abs(dx - round(dx)) < 1e-6 and abs(dy - round(dy)) < 1e-6
                   and abs(rgt[1] - PIX) < 1e-9 and abs(rgt[5] + PIX) < 1e-9
                   and same_crs)
        if aligned:
            _windowed_copy(href, int(round(dx)), int(round(dy)),
                           out_path, bounds, w, h)
        else:
            print(f"[{label}] grids not aligned (crs_match={same_crs}) "
                  "— falling back to gdal.Warp")
            gdal.Warp(out_path, remote_path, format="GTiff",
                      outputBounds=(bounds[0], bounds[1],
                                    bounds[2], bounds[3]),
                      width=w, height=h, dstNodata=0.0,
                      resampleAlg="bilinear", multithread=True)
        remote = None
        size_mb = os.path.getsize(out_path) / 1e6
        print(f"[{label}] {('copy' if aligned else 'warp')} {pol}: "
              f"{time.time() - t1:.0f} s, {size_mb:.0f} MB")

    man = _load_manifest()
    man.setdefault("scenes", {})[label] = {
        "expected_date": expected_iso,
        "id": scene.id,
        "datetime": scene.datetime.isoformat(),
        "platform": scene.platform,
        "orbit_direction": scene.orbit_direction,
        "relative_orbit": scene.relative_orbit,
        "collection": COLLECTION,
        "warp_vv": out_files["VV"],
        "warp_vh": out_files["VH"],
    }
    man["aoi_bounds_32638"] = list(bounds)
    man["grid_w"], man["grid_h"] = w, h
    man["epsg"], man["pixel_m"] = EPSG_AOI, PIX
    man["buffer_m"] = BUFFER_M
    man["event_id"], man["reference_shp"] = EVENT_ID, REF_SHP
    _save_manifest(man)
    print(f"[{label}] manifest updated; grid {w}x{h}")


def _epsg_of(wkt):
    sr = osr.SpatialReference()
    if sr.ImportFromWkt(wkt) != 0:
        return None
    return int(sr.GetAuthorityCode(None) or 0)


def _epsg_of_proj(path):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    wkt = ds.GetProjection()
    ds = None
    return _epsg_of(wkt)


def _windowed_copy(href, x0, y0, out_path, bounds, w, h, workers=4):
    """Parallel windowed copy from the remote COG (pixel-aligned grids).

    Each worker thread keeps its own /vsicurl/ dataset handle and reads
    disjoint 512-row strips; measured 4.2x speedup vs sequential reads.
    Source nodata (-32768) is mapped to 0.0 (the detector treats 0 /
    negative-below-floor values as invalid and converts to dB).
    """
    if x0 < 0 or y0 < 0 or x0 + w > _remote_size(href)[0] \
            or y0 + h > _remote_size(href)[1]:
        raise RuntimeError(
            f"AOI window outside scene: x0={x0}, y0={y0}, "
            f"scene={_remote_size(href)}")
    if os.path.exists(out_path):
        gdal.GetDriverByName("GTiff").Delete(out_path)

    strips = [(y0 + yy, min(512, h - yy)) for yy in range(0, h, 512)]
    local = threading.local()

    def read_strip(s):
        sy, rows = s
        ds = getattr(local, "ds", None)
        if ds is None:
            ds = gdal.Open(f"/vsicurl/{href}", gdal.GA_ReadOnly)
            local.ds = ds
        return ds.GetRasterBand(1).ReadAsArray(x0, sy, w, rows)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        chunks = list(ex.map(read_strip, strips))

    drv = gdal.GetDriverByName("GTiff")
    dst = drv.Create(out_path, w, h, 1, gdal.GDT_Float32,
                     options=["TILED=YES", "COMPRESS=LZW",
                              "BIGTIFF=IF_SAFER", "PREDICTOR=3"])
    dst.SetGeoTransform((bounds[0], PIX, 0.0, bounds[3], 0.0, -PIX))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG_AOI)
    dst.SetProjection(srs.ExportToWkt())
    band = dst.GetRasterBand(1)
    band.SetNoDataValue(0.0)
    for yy, chunk in enumerate(chunks):
        if chunk is None:
            raise RuntimeError(f"remote read failed for strip {yy}")
        yy_px = yy * 512
        chunk = chunk.astype(np.float32, copy=False)
        chunk[chunk == -32768.0] = 0.0
        band.WriteArray(chunk, 0, yy_px)
    band.FlushCache()
    dst = None


def _remote_size(href):
    ds = gdal.Open(f"/vsicurl/{href}", gdal.GA_ReadOnly)
    size = (ds.RasterXSize, ds.RasterYSize)
    ds = None
    return size


def _load_manifest():
    if os.path.isfile(MANIFEST):
        with open(MANIFEST) as f:
            return json.load(f)
    return {}


def _save_manifest(man):
    man["updated"] = datetime.now().isoformat(timespec="seconds")
    with open(MANIFEST, "w") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)


# ======================================================================
# Stage: detect
# ======================================================================
VARIANTS = {
    "A": {"pre": ["base"], "post": "post", "norm": False, "mode": "adaptive", "fixed": 3.0,
          "desc": "pair 22.07->03.08 (WET post), adaptive, norm OFF — baseline P.0 regime"},
    "B": {"pre": ["base"], "post": "post", "norm": True, "mode": "fixed", "fixed": 3.0,
          "desc": "pair 22.07->03.08 (WET), FIXED 3.0 dB, norm ON"},
    "C": {"pre": ["pre1", "pre2", "pre3"], "post": "post", "norm": False, "mode": "adaptive", "fixed": 3.0,
          "desc": "stack 3-pre ->03.08 (WET), adaptive, norm OFF"},
    "D": {"pre": ["pre1", "pre2", "pre3"], "post": "post", "norm": True, "mode": "fixed", "fixed": 3.0,
          "desc": "stack + FIXED 3.0 dB, norm ON, WET post"},
    "E": {"pre": ["base"], "post": "post2", "norm": False, "mode": "adaptive", "fixed": 3.0,
          "desc": "pair 22.07->15.08 (DRY post), adaptive, norm OFF — dry-pair control"},
    "F": {"pre": ["pre1", "pre2", "pre3"], "post": "post2", "norm": False, "mode": "adaptive", "fixed": 3.0,
          "desc": "stack 3-pre ->15.08 (DRY), adaptive, norm OFF"},
}
# NOTE (02.09, proved on ID666): adaptive mode is INVARIANT to the additive
# background normalization (uniform dB shift moves mean and threshold
# equally) — pair+norm+adaptive produced pixel-identical results to A
# (archived: windthrow_id666_B_adaptive_invariant.json). Hence the norm
# axis is tested under the FIXED threshold, as in monitoring/NRT use.


def stage_detect(variant):
    with open(MANIFEST) as f:
        man = json.load(f)
    cfg = VARIANTS[variant]
    pre_labels = cfg["pre"]
    pre_paths, post_paths = [], []
    for lab in pre_labels:
        pre_paths += [man["scenes"][lab]["warp_vv"],
                      man["scenes"][lab]["warp_vh"]]
    post_lab = cfg.get("post", "post")
    post_paths = [man["scenes"][post_lab]["warp_vv"],
                  man["scenes"][post_lab]["warp_vh"]]
    bg_mask = man["masks"]["bg"]

    out_dir = f"{OUT}/{variant}"
    os.makedirs(out_dir, exist_ok=True)
    out_base = f"{out_dir}/id666_{variant}"

    det = WindthrowDetector(
        threshold_mode=cfg["mode"], a_db=2.9,
        fixed_threshold_db=cfg["fixed"],
        min_pixels=27,
        median_filter_size=3, normalize_background=cfg["norm"],
    )
    t0 = time.time()
    res = det.detect_file(
        pre_paths=pre_paths, post_paths=post_paths,
        output_base=out_base,
        background_mask_path=bg_mask,
        progress_cb=lambda f, m: print(f"  {f:5.1f}% {m}", flush=True),
    )
    runtime = time.time() - t0

    # detected area from the mask raster
    ds = gdal.Open(res["mask"], gdal.GA_ReadOnly)
    m = ds.GetRasterBand(1).ReadAsArray()
    gt = ds.GetGeoTransform()
    ds = None
    px_ha = abs(gt[1] * gt[5]) / 10000.0
    det_ha = float((m == 255).sum() * px_ha)

    record = {
        "variant": variant,
        "desc": cfg["desc"],
        "pre_labels": pre_labels,
        "post_label": post_lab,
        "normalize_background": cfg["norm"],
        "params": {"threshold_mode": cfg["mode"], "a_db": 2.9,
                   "fixed_threshold_db": cfg["fixed"],
                   "min_pixels": 27, "median_filter_size": 3},
        "offset_db": res["offset_db"],
        "mean_wi": res["mean_wi"],
        "threshold_db": res["threshold_db"],
        "n_objects": res["n_objects"],
        "detected_area_ha": det_ha,
        "runtime_s": round(runtime, 1),
        "outputs": {"wi": res["wi"], "mask": res["mask"],
                    "vector": res["vector"]},
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    json_path = f"{DL}/windthrow_id666_{variant}.json"
    with open(json_path, "w") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[{variant}] DONE in {runtime:.0f} s -> {json_path}")
    print(f"  offsets={record['offset_db']} mean_wi={record['mean_wi']:.3f} "
          f"thr={record['threshold_db']:.3f} n_obj={record['n_objects']} "
          f"area={det_ha:.1f} ha")


# ======================================================================
# Stage: report
# ======================================================================
def object_metrics(det_mask, ref_mask):
    """Object-based PA/UA (overlap >= 30% of the object), 8-connected."""
    from scipy import ndimage
    struct = np.ones((3, 3), dtype=bool)
    ref_lab, n_ref = ndimage.label(ref_mask > 0, structure=struct)
    det_lab, n_det = ndimage.label(det_mask > 0, structure=struct)
    if n_ref == 0 or n_det == 0:
        return {"n_ref_components": int(n_ref),
                "n_det_objects": int(n_det),
                "pa": 0.0, "ua": 0.0, "f1": 0.0}
    ref_sizes = np.bincount(ref_lab.ravel())
    det_sizes = np.bincount(det_lab.ravel())

    # PA: reference component counts as detected when >=30% of its pixels
    # are flagged — det pixels per ref component in ONE bincount pass
    det_counts = np.bincount(ref_lab[det_mask > 0].ravel(),
                             minlength=n_ref + 1)
    tp_ref = int(np.count_nonzero(
        det_counts[1:] >= 0.3 * ref_sizes[1:]))
    # UA: detected object counts as match when >=30% of its pixels fall
    # inside the reference — ref pixels per det object in ONE pass
    ref_counts = np.bincount(det_lab[ref_mask > 0].ravel(),
                             minlength=n_det + 1)
    tp_det = int(np.count_nonzero(
        ref_counts[1:] >= 0.3 * det_sizes[1:]))
    pa = tp_ref / n_ref
    ua = tp_det / n_det
    f1 = (2 * pa * ua / (pa + ua)) if (pa + ua) > 0 else 0.0
    return {"n_ref_components": int(n_ref), "n_det_objects": int(n_det),
            "tp_ref": tp_ref, "tp_det": tp_det,
            "pa": round(pa, 4), "ua": round(ua, 4), "f1": round(f1, 4)}


def stage_report():
    with open(MANIFEST) as f:
        man = json.load(f)
    bounds = tuple(man["aoi_bounds_32638"])
    w, h = man["grid_w"], man["grid_h"]
    ref_ds = gdal.Open(man["masks"]["ref"], gdal.GA_ReadOnly)
    ref_mask = ref_ds.GetRasterBand(1).ReadAsArray() > 0
    ref_ds = None
    ref_ha = float(ref_mask.sum() * (PIX * PIX / 10000.0))

    variants = {}
    for v in ("A", "B", "C", "D", "E", "F"):
        p = f"{DL}/windthrow_id666_{v}.json"
        if not os.path.isfile(p):
            print(f"[report] missing {p} — variant {v} skipped")
            continue
        with open(p) as f:
            rec = json.load(f)
        ds = gdal.Open(rec["outputs"]["mask"], gdal.GA_ReadOnly)
        det_mask = ds.GetRasterBand(1).ReadAsArray() > 0
        ds = None
        met = object_metrics(det_mask, ref_mask)
        met.update({
            "detected_area_ha": rec["detected_area_ha"],
            "n_objects": rec["n_objects"],
            "offset_db": rec["offset_db"],
            "mean_wi": rec["mean_wi"],
            "threshold_db": rec["threshold_db"],
            "desc": rec["desc"],
            "runtime_s": rec["runtime_s"],
        })
        variants[v] = met
        print(f"[{v}] PA={met['pa']:.3f} UA={met['ua']:.3f} "
              f"F1={met['f1']:.3f} area={met['detected_area_ha']:.0f} ha "
              f"({met['n_det_objects']} obj)")

    out = {
        "event": {"id": EVENT_ID, "storm": "squall 30.07.2017",
                  "region": "north Sverdlovsk oblast",
                  "ref_area_ha": round(ref_ha, 1)},
        "aoi": {"epsg": man["epsg"], "bounds": list(bounds),
                "w": w, "h": h, "buffer_m": BUFFER_M},
        "scenes": man["scenes"],
        "metric_definition": {
            "type": "object-based, 8-connected components",
            "pa": "reference component is TP when >=30% of its pixels are flagged",
            "ua": "detected object is TP when >=30% of its pixels are inside reference",
        },
        "variants": variants,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    path = f"{DL}/windthrow_id666_baselines_{datetime.now():%Y-%m-%d}.json"
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"report -> {path}")


# ======================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["warp", "detect", "report"])
    ap.add_argument("--label", default="all",
                    help="warp: pre3|pre2|pre1|base|post|masks|all")
    ap.add_argument("--variant", default="all",
                    help="detect: A|B|C|D|all")
    args = ap.parse_args()

    # Network robustness + throughput for /vsicurl COG reads
    gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "8")
    gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "3")
    gdal.SetConfigOption("CPL_VSIL_CURL_CACHE_SIZE", "256MB")
    gdal.SetConfigOption("CPL_VSIL_CURL_USE_HEAD", "NO")
    gdal.SetConfigOption("CPL_VSIL_CURL_CHUNK_SIZE", "8388608")
    gdal.SetConfigOption("GDAL_HTTP_VERSION", "2")
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

    if args.stage == "warp":
        labels = LABELS + ["masks"] if args.label == "all" else [args.label]
        for lab in labels:
            stage_warp(lab)
    elif args.stage == "detect":
        vs = list(VARIANTS) if args.variant == "all" else [args.variant]
        for v in vs:
            js = f"{DL}/windthrow_id666_{v}.json"
            if os.path.isfile(js):
                print(f"[{v}] JSON exists — skip (delete to rerun)")
                continue
            stage_detect(v)
    else:
        stage_report()


if __name__ == "__main__":
    main()
