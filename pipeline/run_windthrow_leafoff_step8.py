#!/usr/bin/env python3
"""Step8: leaf-off (autumn) windthrow test on Shikhov DB events.

Motivation (step7 finding F3): for ID666 (summer squall) there was NO
positive WI signal in leaf-on C-band. In leaf-off the standing forest
background drops while the debris field stays rough, so the contrast
may flip positive. Winter events are absent from the DB (snow hides
damage from Landsat), so the leaf-off test = autumn events:

  590  snowstorm S425 08.10.2015, 31.66 km2, 1062 polygons,
       Sverdlovsk oblast N (lon 58.70-59.24, lat 60.68-61.31), UTM 41N
  694  tornado S486 ~19.09.2017 (Date_1 04.09 - Date_2 02.10),
       1.61 km2, Perm Krai (lon 49.67-49.86, lat 59.36-59.40), UTM 40N

Design notes:
  * NO vector union/buffer of the reference (1062 parts OOM-killed the
    sandbox even with UnionCascaded).  The reference mask is rasterized
    directly from the (filtered) shapefile layer; the 3 km background
    ring is a distance-transform (EDT) around it.  Memory-light.
  * A long swath (ID590 ~ 69 km) may cross an RTC frame boundary, so a
    label is mosaicked from ALL same-orbit scenes of that day (windowed
    parallel copies, gaps filled from later scenes).

Stages (each finishes well inside the sandbox timeout):

  recon            STAC listing for the AOI, scene groups by
                   (orbit_direction, relative_orbit), proposed cycle
                   plan (pre3..base / post / post2).  No downloads.
  warp  --label    download VV/VH for each label of the plan + masks
                   (ref, bg ring).  Manifest ->
                   download/step8_id<E>_warp_manifest.json
  detect --variant A pair(base->post) adaptive        (Rüetschi regime)
                   B pair fixed 3.0 dB + norm ON      (monitoring)
                   C stack(3 pre)->post adaptive
                   D stack fixed 3.0 dB + norm ON
                   E pair(base->post2) adaptive
                   F stack->post2 adaptive
  report           PA/UA object-based per variant vs reference polygons;
                   -> download/windthrow_id<E>_leafoff_baselines_<date>.json
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
DL = f"{PROJ}/download"
REF_SHP = f"{PROJ}/research/shikhov_db/GIS/Windthrows.shp"

PIX = 10.0
BUFFER_M = 3000.0
SNAP_MARGIN_M = 100.0   # safety margin before grid snapping (bulge guard)
# The AOI must contain the reference PLUS the full 3 km background ring,
# hence the envelope is padded by BUFFER_M + SNAP_MARGIN_M.
AOI_PAD_M = BUFFER_M + SNAP_MARGIN_M
COLLECTION = "sentinel-1-rtc"

EVENTS = {
    590: dict(
        storm="snowstorm S425 08.10.2015, Sverdlovsk oblast N (largest "
              "autumn event of the S1 era, 31.66 km2 / 1062 polygons)",
        ref_id=590,
        epsg=32641,
        event_date="2015-10-08",
        date_1="2015-07-25",
        # pre scenes must be strictly before pre_cutoff; for 590 the storm
        # date is known (08.10) -> allow right up to the day before.
        pre_cutoff="2015-10-07",
        post_from="2015-10-09",
        post_to="2015-11-05",
        pre_days=48,
    ),
    694: dict(
        storm="tornado S486 ~19.09.2017, Perm Krai (1.61 km2, 21 polygons; "
              "Date_1 04.09 - Date_2 02.10)",
        ref_id=694,
        epsg=32640,
        event_date="2017-09-19",
        date_1="2017-09-04",
        # The tornado date is uncertain inside 04.09..02.10 -> keep the
        # pre stack strictly BEFORE Date_1 so it cannot be contaminated.
        pre_cutoff="2017-09-03",
        post_from="2017-09-20",
        post_to="2017-10-17",
        pre_days=48,
    ),
    6660: dict(
        storm="LEAF-OFF ANNUAL DIFFERENTIAL for ID666 (squall 30.07.2017, "
              "950 ha): pre = Oct 2016 (leaf-off, pre-event year), "
              "post = Oct 2017 (leaf-off, post-event). Same rel-orbit-94 "
              "descending track as step7; same-season pre/post kills the "
              "phenology confound, windthrow revealed by canopy removal",
        ref_id=666,
        epsg=32638,
        event_date="2017-10-05",
        date_1="2016-09-28",
        pre_from="2016-09-28",
        pre_cutoff="2016-10-20",
        post_from="2017-09-28",
        post_to="2017-10-20",
        pre_days=420,
        # early S1B on rel-94 was irregular: only ONE leaf-off scene
        # (07.10.2016) exists before the event year -> pair mode only
        min_pre=1,
    ),
}

VARIANT_ORDER = ["A", "B", "C", "D", "E", "F"]
LABEL_PRE = ["pre3", "pre2", "pre1", "base"]


# ======================================================================
# AOI grid (light — envelope only, no GEOS unions)
# ======================================================================
def aoi_grid(ref_id, epsg):
    """Snapped AOI grid from the shapefile envelope (lonlat -> UTM)."""
    ds = ogr.Open(REF_SHP)
    lyr = ds.GetLayer(0)
    lyr.SetAttributeFilter(f"ID = {ref_id}")
    feat = lyr.GetNextFeature()
    if feat is None:
        raise RuntimeError(f"Event ID={ref_id} not found in {REF_SHP}")
    geom = feat.GetGeometryRef()
    minx, maxx, miny, maxy = geom.GetEnvelope()  # layer CRS = 4326
    ds = None

    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(epsg)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(src, dst)
    xs, ys = [], []
    for cx, cy in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)):
        px, py, _ = tr.TransformPoint(cx, cy)
        xs.append(px)
        ys.append(py)
    m = AOI_PAD_M
    minx_u, maxx_u = min(xs) - m, max(xs) + m
    miny_u, maxy_u = min(ys) - m, max(ys) + m
    minx_u = minx_u // PIX * PIX
    miny_u = miny_u // PIX * PIX
    maxx_u = (maxx_u // PIX + 1) * PIX
    maxy_u = (maxy_u // PIX + 1) * PIX
    w = int(round((maxx_u - minx_u) / PIX))
    h = int(round((maxy_u - miny_u) / PIX))
    return (minx_u, miny_u, maxx_u, maxy_u), w, h


def bounds_to_wgs84(bounds, epsg):
    src = osr.SpatialReference()
    src.ImportFromEPSG(epsg)
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
    return (b[0], b[2], b[1], b[3])  # (minx, miny, maxx, maxy)


def _new_grid_tif(path, bounds, w, h, epsg, dtype=gdal.GDT_Byte,
                  nodata=0, options=None):
    if os.path.exists(path):
        gdal.GetDriverByName("GTiff").Delete(path)
    drv = gdal.GetDriverByName("GTiff")
    opts = options or ["TILED=YES", "COMPRESS=LZW"]
    ds = drv.Create(path, w, h, 1, dtype, options=opts)
    ds.SetGeoTransform((bounds[0], PIX, 0.0, bounds[3], 0.0, -PIX))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    return ds


def rasterize_event_masks(ref_id, warp_dir, bounds, w, h, epsg):
    """ref mask: rasterize the filtered shapefile layer directly.

    The shapefile layer is EPSG:4326 while the grid is UTM — the layer
    is re-projected into a memory overlay first (gdal.RasterizeLayer
    does NOT reproject).  bg mask: 3 km ring around ref via EDT (no
    vector buffer needed).  Returns (ref_path, bg_path, ref_ha).
    """
    os.makedirs(warp_dir, exist_ok=True)
    ref_path = f"{warp_dir}/ref_mask.tif"
    ds = _new_grid_tif(ref_path, bounds, w, h, epsg)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    shp = ogr.Open(REF_SHP)
    lyr = shp.GetLayer(0)
    lyr.SetAttributeFilter(f"ID = {ref_id}")
    src_srs = lyr.GetSpatialRef()
    if src_srs is not None:
        src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    mem = ogr.GetDriverByName("Memory").CreateDataSource("mem")
    mlyr = mem.CreateLayer("ref", srs, ogr.wkbMultiPolygon)
    mlyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    n_feat = 0
    for feat in lyr:
        geom = feat.GetGeometryRef().Clone()
        geom.Transform(osr.CoordinateTransformation(src_srs, srs))
        nf = ogr.Feature(mlyr.GetLayerDefn())
        nf.SetGeometry(geom)
        nf.SetField("id", ref_id)
        mlyr.CreateFeature(nf)
        n_feat += 1
    shp = None
    gdal.RasterizeLayer(ds, [1], mlyr, burn_values=[255])
    # read via the SAME handle (a second open may see stale disk state)
    ref = ds.GetRasterBand(1).ReadAsArray()
    ref_ha = float((ref == 255).sum() * (PIX * PIX / 10000.0))
    ds.FlushCache()
    ds = None
    mem = None
    if ref_ha <= 0:
        raise RuntimeError(f"ref mask is empty for ID={ref_id} — "
                           "rasterization failed")

    from scipy.ndimage import distance_transform_edt
    edt = distance_transform_edt(ref == 0, sampling=(PIX, PIX))
    ring = ((edt <= BUFFER_M) & (ref == 0)).astype(np.uint8) * 255

    bg_path = f"{warp_dir}/bg_mask.tif"
    ds = _new_grid_tif(bg_path, bounds, w, h, epsg)
    ds.GetRasterBand(1).WriteArray(ring)
    ds.FlushCache()
    ds = None
    print(f"masks: ref={ref_path} ({ref_ha:.0f} ha, {n_feat} feature), "
          f"bg ring={bg_path} ({float((ring == 255).sum() * PIX * PIX / 1e4):.0f} ha)")
    return ref_path, bg_path, ref_ha


def _proj_is_same(path, epsg):
    """True if the raster CRS equals the target EPSG (parameter-wise)."""
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    wkt = ds.GetProjection()
    ds = None
    sr = osr.SpatialReference()
    if sr.ImportFromWkt(wkt) != 0:
        return False
    ref = osr.SpatialReference()
    ref.ImportFromEPSG(epsg)
    return bool(sr.IsSame(ref))


# ======================================================================
# Manifest
# ======================================================================
def manifest_path(event_id):
    return f"{DL}/step8_id{event_id}_warp_manifest.json"


def _load_manifest(event_id):
    p = manifest_path(event_id)
    if os.path.isfile(p):
        with open(p) as f:
            return json.load(f)
    return {}


def _save_manifest(event_id, man):
    man["updated"] = datetime.now().isoformat(timespec="seconds")
    with open(manifest_path(event_id), "w") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)


def _fill_meta(man, event_id, ev):
    man["event_id"] = event_id
    man["storm"] = ev["storm"]
    man["event_date"] = ev["event_date"]
    man["epsg"] = ev["epsg"]
    man["pixel_m"] = PIX
    man["buffer_m"] = BUFFER_M
    man["reference_shp"] = REF_SHP
    man["collection"] = COLLECTION


# ======================================================================
# Scene planning (STAC)
# ======================================================================
def search_scenes(source, bbox_wgs84, ev):
    """All RTC scenes over the AOI in [event-pre_days, post_to]."""
    start = (datetime.strptime(ev["event_date"], "%Y-%m-%d")
             - timedelta(days=ev["pre_days"]))
    end = datetime.strptime(ev["post_to"], "%Y-%m-%d") + timedelta(days=1)
    return source.search(
        bbox=bbox_wgs84,
        start_date=start,
        end_date=end,
        polarization="VV+VH",
        orbit="Any",
        collection=COLLECTION,
    )


def group_scenes(scenes):
    """Group by (orbit_direction, relative_orbit) -> {key: [scenes]}."""
    groups = {}
    for s in scenes:
        key = (s.orbit_direction, int(s.relative_orbit))
        groups.setdefault(key, []).append(s)
    for k in groups:
        groups[k].sort(key=lambda s: s.datetime)
    return groups


def make_plan(ev, scenes):
    """Choose the best orbit group and assign labels -> scenes.

    pre  : scenes strictly before pre_cutoff (guaranteed pre-event)
    post : scenes in [post_from, post_to]
    labels: pre3 pre2 pre1 base (4 latest pre) / post (earliest) /
            post2 (next).  Missing labels are simply absent.
    """
    groups = group_scenes(scenes)
    pre_cut = datetime.strptime(ev["pre_cutoff"], "%Y-%m-%d").date()
    pre_lo_default = (datetime.strptime(ev["event_date"], "%Y-%m-%d")
                      - timedelta(days=ev["pre_days"])).date()
    pre_lo = (datetime.strptime(ev["pre_from"], "%Y-%m-%d").date()
              if ev.get("pre_from") else pre_lo_default)
    post_lo = datetime.strptime(ev["post_from"], "%Y-%m-%d").date()
    post_hi = datetime.strptime(ev["post_to"], "%Y-%m-%d").date()

    scored = []
    min_pre = ev.get("min_pre", 2)
    for key, lst in groups.items():
        pre = [s for s in lst if pre_lo <= s.datetime.date() <= pre_cut]
        post = [s for s in lst if post_lo <= s.datetime.date() <= post_hi]
        if not post or len(pre) < min_pre:
            continue
        scored.append((len(post) >= 2, min(len(pre), 4), len(post),
                       len(lst), key, pre, post))
    if not scored:
        raise RuntimeError(
            f"no orbit group with >={min_pre} pre and >=1 post scene — "
            "check recon output / widen the window")
    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]), reverse=True)
    best = scored[0]
    key, pre, post = best[4], best[5], best[6]

    plan = {}
    # right-align: with fewer than 4 pre scenes the LAST labels win
    # (base = closest to the event, then pre1, pre2, pre3)
    labels = LABEL_PRE[-min(4, len(pre)):]
    for lab, sc in zip(labels, pre[-4:]):
        plan[lab] = sc
    plan["post"] = post[0]
    if len(post) > 1:
        plan["post2"] = post[1]
    return key, plan, groups


def pick_scenes_near(source, bbox_wgs84, want_iso, key):
    """ALL same-orbit-group scenes within ±1 day of the planned date."""
    d0 = datetime.strptime(want_iso, "%Y-%m-%d")
    scenes = source.search(
        bbox=bbox_wgs84,
        start_date=d0 - timedelta(days=1),
        end_date=d0 + timedelta(days=1, hours=23, minutes=59),
        polarization="VV+VH",
        orbit="Any",
        collection=COLLECTION,
    )
    cand = [s for s in scenes
            if (s.orbit_direction, int(s.relative_orbit)) == key]
    cand.sort(key=lambda s: s.datetime)
    # dedupe by id, keep order
    seen, out = set(), []
    for s in cand:
        if s.id not in seen:
            seen.add(s.id)
            out.append(s)
    return out


# ======================================================================
# Stage: recon
# ======================================================================
def stage_recon(event_id):
    ev = EVENTS[event_id]
    ref_id = ev.get("ref_id", event_id)
    bounds, w, h = aoi_grid(ref_id, ev["epsg"])
    bbox = bounds_to_wgs84(bounds, ev["epsg"])
    print(f"=== EVENT {event_id}: {ev['storm']}")
    print(f"epsg={ev['epsg']} event={ev['event_date']} "
          f"pre_cutoff={ev['pre_cutoff']} "
          f"post={ev['post_from']}..{ev['post_to']}")
    print(f"AOI grid {w}x{h} px, bbox_wgs84="
          f"{tuple(round(x, 4) for x in bbox)}")

    source = PlanetaryComputerSource(collection=COLLECTION)
    scenes = search_scenes(source, bbox, ev)
    print(f"RTC scenes over AOI: {len(scenes)}")
    if not scenes:
        print("STOP: no RTC coverage — raw-GRD fallback would be needed")
        return
    for s in sorted(scenes, key=lambda s: s.datetime):
        print(f"  {s.datetime.isoformat()} {s.platform} "
              f"{s.orbit_direction:9s} rel={int(s.relative_orbit):3d} {s.id}")
    try:
        key, plan, groups = make_plan(ev, scenes)
    except RuntimeError as e:
        print(f"PLAN FAILED: {e}")
        return
    print(f"chosen orbit: {key[0]} rel={key[1]} "
          f"({len(groups[key])} scenes)")
    for lab in ["pre3", "pre2", "pre1", "base", "post", "post2"]:
        if lab in plan:
            s = plan[lab]
            print(f"  {lab:5s} -> {s.datetime.date()} {s.platform} "
                  f"rel={int(s.relative_orbit)} {s.id}")
    print("OK — run: warp --label all")


# ======================================================================
# Windowed parallel copy + per-day mosaic
# ======================================================================
def _remote_size(href):
    ds = gdal.Open(f"/vsicurl/{href}", gdal.GA_ReadOnly)
    size = (ds.RasterXSize, ds.RasterYSize)
    ds = None
    return size


def _remote_window_read(href, xoff, yoff, xsize, ysize, workers=4):
    """Parallel strip read of a remote COG window -> float32 array."""
    strips = [(yoff + yy, min(512, ysize - yy))
              for yy in range(0, ysize, 512)]
    local = threading.local()

    def read_strip(s):
        sy, rows = s
        ds = getattr(local, "ds", None)
        if ds is None:
            ds = gdal.Open(f"/vsicurl/{href}", gdal.GA_ReadOnly)
            local.ds = ds
        return ds.GetRasterBand(1).ReadAsArray(xoff, sy, xsize, rows)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        chunks = list(ex.map(read_strip, strips))
    out = np.empty((ysize, xsize), dtype=np.float32)
    for i, ch in enumerate(chunks):
        if ch is None:
            raise RuntimeError(f"remote read failed for strip {i}")
        out[i * 512:i * 512 + ch.shape[0]] = ch
    out[out == -32768.0] = 0.0
    return out


def warp_label_pol(pol, scenes, bounds, w, h, epsg, label):
    """Mosaic one polarization from all same-day scenes onto the grid.

    Returns (out_path, used_ids).  First scene wins, later scenes fill
    only the gaps (frame overlaps carry near-identical RTC values).
    """
    canvas = np.zeros((h, w), dtype=np.float32)
    used = []
    for sc in scenes:
        href = sc.assets.get(pol)
        if not href:
            raise RuntimeError(f"[{label}] asset '{pol}' missing in {sc.id}")
        t1 = time.time()
        remote_path = f"/vsicurl/{href}"
        rds = gdal.Open(remote_path, gdal.GA_ReadOnly)
        if rds is None:
            raise RuntimeError(f"[{label}] cannot open remote COG ({pol})")
        rgt = rds.GetGeoTransform()
        rw, rh = rds.RasterXSize, rds.RasterYSize
        rds = None
        same_crs = _proj_is_same(remote_path, epsg)
        dx = (bounds[0] - rgt[0]) / PIX
        dy = (rgt[3] - bounds[3]) / PIX
        aligned = (abs(dx - round(dx)) < 1e-6 and abs(dy - round(dy)) < 1e-6
                   and abs(rgt[1] - PIX) < 1e-9 and abs(rgt[5] + PIX) < 1e-9
                   and same_crs)
        arr = np.zeros((h, w), dtype=np.float32)
        got_any = False
        if aligned:
            x0, y0 = int(round(dx)), int(round(dy))
            ax0 = max(0, -x0)
            ay0 = max(0, -y0)
            ax1 = min(w, rw - x0)
            ay1 = min(h, rh - y0)
            if ax1 > ax0 and ay1 > ay0:
                win = _remote_window_read(
                    href, ax0 + x0, ay0 + y0, ax1 - ax0, ay1 - ay0)
                arr[ay0:ay1, ax0:ax1] = win
                got_any = True
        else:
            print(f"[{label}] {pol}: grids not aligned "
                  f"(crs_match={same_crs}) — gdal.Warp fallback")
            tmp = f"/vsimem/{label}_{pol}_{sc.id.replace('/', '_')}.tif"
            gdal.Warp(tmp, remote_path, format="GTiff",
                      dstSRS=f"EPSG:{epsg}",
                      outputBounds=(bounds[0], bounds[1],
                                    bounds[2], bounds[3]),
                      width=w, height=h, dstNodata=0.0,
                      resampleAlg="bilinear", multithread=True)
            tds = gdal.Open(tmp, gdal.GA_ReadOnly)
            arr = tds.GetRasterBand(1).ReadAsArray().astype(np.float32)
            tds = None
            gdal.Unlink(tmp)
            got_any = bool(np.any(arr != 0))
        if not got_any:
            print(f"[{label}] {pol}: {sc.id} covers none of the AOI — skip")
            continue
        take = (canvas == 0) & (arr != 0)
        canvas[take] = arr[take]
        used.append(sc.id)
        print(f"[{label}] {pol}: {sc.id} "
              f"+{take.sum() * PIX * PIX / 1e6:.1f} km2 new, "
              f"{time.time() - t1:.0f} s")
    if not used:
        raise RuntimeError(f"[{label}] no scene covered the AOI ({pol})")
    return canvas, used


def stage_warp(event_id, label):
    ev = EVENTS[event_id]
    ref_id = ev.get("ref_id", event_id)
    bounds, w, h = aoi_grid(ref_id, ev["epsg"])
    bbox = bounds_to_wgs84(bounds, ev["epsg"])
    warp_dir = f"{WORK}/warp_id{event_id}"
    os.makedirs(warp_dir, exist_ok=True)

    man = _load_manifest(event_id)

    if label == "masks":
        ref_path, bg_path, ref_ha = rasterize_event_masks(
            ref_id, warp_dir, bounds, w, h, ev["epsg"])
        man["masks"] = {"ref": ref_path, "bg": bg_path, "ref_ha": ref_ha}
        man["aoi_bounds"] = list(bounds)
        man["grid_w"], man["grid_h"] = w, h
        _fill_meta(man, event_id, ev)
        _save_manifest(event_id, man)
        return

    if not man.get("plan"):
        source = PlanetaryComputerSource(collection=COLLECTION)
        scenes = search_scenes(source, bbox, ev)
        key, plan, _ = make_plan(ev, scenes)
        man["plan_orbit"] = {"direction": key[0], "relative_orbit": key[1]}
        man["plan"] = {lab: {"id": s.id,
                             "date": s.datetime.date().isoformat(),
                             "platform": s.platform,
                             "relative_orbit": int(s.relative_orbit),
                             "datetime": s.datetime.isoformat()}
                       for lab, s in plan.items()}
        _fill_meta(man, event_id, ev)
        _save_manifest(event_id, man)

    if label == "plan":
        print(json.dumps(man["plan"], indent=2, ensure_ascii=False))
        return

    if label not in man["plan"]:
        raise RuntimeError(f"label {label} not in plan: {list(man['plan'])}")

    meta = man["plan"][label]
    key = (man["plan_orbit"]["direction"],
           man["plan_orbit"]["relative_orbit"])

    if label in man.get("scenes", {}):
        sc_meta = man["scenes"][label]
        if all(os.path.isfile(sc_meta[f"warp_{p}"])
               and os.path.getsize(sc_meta[f"warp_{p}"]) > 1000
               for p in ("vv", "vh")):
            print(f"[{label}] already downloaded — skip")
            return

    source = PlanetaryComputerSource(collection=COLLECTION)
    day_scenes = pick_scenes_near(source, bbox, meta["date"], key)
    if not day_scenes:
        raise RuntimeError(f"[{label}] no scene of orbit {key} near "
                           f"{meta['date']}")
    print(f"[{label}] {meta['date']} orbit {key[0]}/{key[1]}: "
          f"{len(day_scenes)} scene(s) "
          f"{[s.id for s in day_scenes]}")

    out_files = {}
    used_all = []
    for pol in ("vv", "vh"):
        canvas, used = warp_label_pol(pol, day_scenes, bounds, w, h,
                                      ev["epsg"], f"{label}@{event_id}")
        out_files[pol.upper()] = f"{warp_dir}/{label}_{pol}.tif"
        ds = _new_grid_tif(out_files[pol.upper()], bounds, w, h, ev["epsg"],
                           dtype=gdal.GDT_Float32, nodata=0.0,
                           options=["TILED=YES", "COMPRESS=LZW",
                                    "BIGTIFF=IF_SAFER", "PREDICTOR=3"])
        ds.GetRasterBand(1).WriteArray(canvas)
        ds.FlushCache()
        ds = None
        size_mb = os.path.getsize(out_files[pol.upper()]) / 1e6
        print(f"[{label}] {pol} saved: {size_mb:.0f} MB")
        used_all.extend(used)

    man = _load_manifest(event_id)
    man.setdefault("scenes", {})[label] = {
        "expected_date": meta["date"],
        "ids": sorted(set(used_all)),
        "id": meta["id"],
        "datetime": meta["datetime"],
        "platform": meta["platform"],
        "orbit_direction": key[0],
        "relative_orbit": key[1],
        "collection": COLLECTION,
        "warp_vv": out_files["VV"],
        "warp_vh": out_files["VH"],
    }
    man["aoi_bounds"] = list(bounds)
    man["grid_w"], man["grid_h"] = w, h
    _fill_meta(man, event_id, ev)
    _save_manifest(event_id, man)
    print(f"[{label}] manifest updated; grid {w}x{h}")


# ======================================================================
# Stage: detect
# ======================================================================
def stage_detect(event_id, variant, keep_wi=False):
    with open(manifest_path(event_id)) as f:
        man = json.load(f)
    ev = EVENTS[event_id]
    stack = [l for l in ["pre3", "pre2", "pre1"] if l in man["scenes"]]

    cfgs = {
        "A": dict(pre=["base"], post="post", norm=False, mode="adaptive",
                  fixed=3.0, desc="pair base->post adaptive, norm OFF"),
        "B": dict(pre=["base"], post="post", norm=True, mode="fixed",
                  fixed=3.0, desc="pair base->post FIXED 3.0, norm ON"),
        "C": dict(pre=stack, post="post", norm=False, mode="adaptive",
                  fixed=3.0, desc=f"stack {stack}->post adaptive, norm OFF"),
        "D": dict(pre=stack, post="post", norm=True, mode="fixed",
                  fixed=3.0, desc=f"stack {stack} FIXED 3.0, norm ON"),
        "E": dict(pre=["base"], post="post2", norm=False, mode="adaptive",
                  fixed=3.0, desc="pair base->post2 adaptive (control)"),
        "F": dict(pre=stack, post="post2", norm=False, mode="adaptive",
                  fixed=3.0, desc=f"stack {stack}->post2 adaptive"),
    }
    if variant not in cfgs:
        raise RuntimeError(f"unknown variant {variant}")
    cfg = cfgs[variant]
    if cfg["post"] == "post2" and "post2" not in man["scenes"]:
        print(f"[{variant}] post2 not available — skip")
        return
    missing = [l for l in cfg["pre"] if l not in man["scenes"]]
    if missing:
        print(f"[{variant}] missing pre labels {missing} — skip")
        return

    pre_paths, post_paths = [], []
    for lab in cfg["pre"]:
        pre_paths += [man["scenes"][lab]["warp_vv"],
                      man["scenes"][lab]["warp_vh"]]
    post_lab = cfg["post"]
    post_paths = [man["scenes"][post_lab]["warp_vv"],
                  man["scenes"][post_lab]["warp_vh"]]
    bg_mask = man["masks"]["bg"]

    out_dir = f"{WORK}/out_id{event_id}/{variant}"
    os.makedirs(out_dir, exist_ok=True)
    out_base = f"{out_dir}/id{event_id}_{variant}"

    det = WindthrowDetector(
        threshold_mode=cfg["mode"], a_db=2.9,
        fixed_threshold_db=cfg["fixed"],
        min_pixels=27, median_filter_size=3,
        normalize_background=cfg["norm"],
    )
    t0 = time.time()
    res = det.detect_file(
        pre_paths=pre_paths, post_paths=post_paths,
        output_base=out_base,
        background_mask_path=bg_mask,
        progress_cb=lambda f_, m: print(f"  {f_:5.1f}% {m}", flush=True),
    )
    runtime = time.time() - t0

    ds = gdal.Open(res["mask"], gdal.GA_ReadOnly)
    m = ds.GetRasterBand(1).ReadAsArray()
    gt = ds.GetGeoTransform()
    ds = None
    px_ha = abs(gt[1] * gt[5]) / 10000.0
    det_ha = float((m == 255).sum() * px_ha)

    record = {
        "event_id": event_id,
        "storm": ev["storm"],
        "variant": variant,
        "desc": cfg["desc"],
        "pre_labels": cfg["pre"],
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
    json_path = f"{DL}/windthrow_id{event_id}_{variant}.json"
    with open(json_path, "w") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[{variant}] DONE in {runtime:.0f} s -> {json_path}")
    print(f"  offsets={record['offset_db']} mean_wi={record['mean_wi']:.3f} "
          f"thr={record['threshold_db']:.3f} n_obj={record['n_objects']} "
          f"area={det_ha:.1f} ha")

    if not keep_wi:
        for p in (res.get("wi"), res.get("wi_norm")):
            if p and os.path.isfile(p):
                os.remove(p)
                print(f"  cleaned {os.path.basename(p)} (metrics in JSON)")


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
    det_counts = np.bincount(ref_lab[det_mask > 0].ravel(),
                             minlength=n_ref + 1)
    tp_ref = int(np.count_nonzero(
        det_counts[1:] >= 0.3 * ref_sizes[1:]))
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


def contrast_stats(man, post_lab):
    """Median WI inside ref vs bg ring for one post label (F3-style)."""
    base = man["scenes"]["base"]
    post = man["scenes"][post_lab]
    pv = gdal.Open(base["warp_vv"], gdal.GA_ReadOnly)
    h_, w_ = pv.GetRasterBand(1).YSize, pv.GetRasterBand(1).XSize
    gt = pv.GetGeoTransform()
    arrs = [pv.GetRasterBand(1).ReadAsArray()]
    pv = None
    for p in (base["warp_vh"], post["warp_vv"], post["warp_vh"]):
        d_ = gdal.Open(p, gdal.GA_ReadOnly)
        arrs.append(d_.GetRasterBand(1).ReadAsArray())
        d_ = None
    pre_vv, pre_vh, post_vv, post_vh = arrs
    valid = np.ones(arrs[0].shape, dtype=bool)
    for a in arrs:
        valid &= a != 0
    wi = (post_vv.astype(np.float32) - pre_vv) \
        + (post_vh.astype(np.float32) - pre_vh)
    del arrs, pre_vv, pre_vh, post_vv, post_vh
    rd = gdal.Open(man["masks"]["ref"], gdal.GA_ReadOnly)
    ref = rd.GetRasterBand(1).ReadAsArray() == 255
    rd = None
    bd = gdal.Open(man["masks"]["bg"], gdal.GA_ReadOnly)
    bg = bd.GetRasterBand(1).ReadAsArray() == 255
    bd = None
    m_ref = float(np.median(wi[ref & valid])) if np.any(ref & valid) else None
    m_bg = float(np.median(wi[bg & valid])) if np.any(bg & valid) else None
    contrast = None if (m_ref is None or m_bg is None) else m_ref - m_bg
    return {"pre_label": "base", "post_label": post_lab,
            "wi_median_ref_db": None if m_ref is None else round(m_ref, 3),
            "wi_median_bg_db": None if m_bg is None else round(m_bg, 3),
            "contrast_db": None if contrast is None else round(contrast, 3),
            "grid": [w_, h_], "geotransform_origin": [gt[0], gt[3]]}


def stage_report(event_id):
    with open(manifest_path(event_id)) as f:
        man = json.load(f)
    ev = EVENTS[event_id]
    ref_ds = gdal.Open(man["masks"]["ref"], gdal.GA_ReadOnly)
    ref_mask = ref_ds.GetRasterBand(1).ReadAsArray() > 0
    gt = ref_ds.GetGeoTransform()
    ref_ds = None
    ref_ha = float(ref_mask.sum() * (abs(gt[1] * gt[5]) / 10000.0))

    variants = {}
    for v in VARIANT_ORDER:
        p = f"{DL}/windthrow_id{event_id}_{v}.json"
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
        "event": {"id": event_id, "storm": ev["storm"],
                  "event_date": ev["event_date"],
                  "season": "leaf-off test (autumn)",
                  "ref_area_ha": round(ref_ha, 1)},
        "aoi": {"epsg": man["epsg"], "bounds": man["aoi_bounds"],
                "w": man["grid_w"], "h": man["grid_h"],
                "buffer_m": BUFFER_M},
        "scenes": man["scenes"],
        "plan_orbit": man.get("plan_orbit"),
        "wi_contrast": {lab: contrast_stats(man, lab)
                        for lab in ("post", "post2")
                        if "base" in man["scenes"] and lab in man["scenes"]},
        "metric_definition": {
            "type": "object-based, 8-connected components",
            "pa": "reference component is TP when >=30% of its pixels "
                  "are flagged",
            "ua": "detected object is TP when >=30% of its pixels are "
                  "inside reference",
        },
        "variants": variants,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    path = (f"{DL}/windthrow_id{event_id}_leafoff_baselines_"
            f"{datetime.now():%Y-%m-%d}.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"report -> {path}")


# ======================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["recon", "warp", "detect", "report"])
    ap.add_argument("--event", type=int, required=True, choices=list(EVENTS))
    ap.add_argument("--label", default="all",
                    help="warp: pre3|pre2|pre1|base|post|post2|masks|plan|all")
    ap.add_argument("--variant", default="all",
                    help="detect: A|B|C|D|E|F|all")
    ap.add_argument("--keep-wi", action="store_true",
                    help="do not delete wi/wi_norm rasters after detect")
    args = ap.parse_args()

    gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "8")
    gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "3")
    gdal.SetConfigOption("CPL_VSIL_CURL_CACHE_SIZE", "268435456")
    gdal.SetConfigOption("CPL_VSIL_CURL_USE_HEAD", "NO")
    gdal.SetConfigOption("CPL_VSIL_CURL_CHUNK_SIZE", "8388608")
    gdal.SetConfigOption("GDAL_HTTP_VERSION", "2")
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

    ev_id = args.event
    if args.stage == "recon":
        stage_recon(ev_id)
    elif args.stage == "warp":
        labels = (["masks"] + LABEL_PRE[-4:] + ["post", "post2"]
                  if args.label == "all" else [args.label])
        for lab in labels:
            if lab != "masks":
                man = _load_manifest(ev_id)
                if man.get("plan") and lab not in man["plan"]:
                    print(f"[{lab}] not in plan — skip")
                    continue
            try:
                stage_warp(ev_id, lab)
            except RuntimeError as e:
                print(f"[{lab}] SKIP: {e}")
    elif args.stage == "detect":
        vs = VARIANT_ORDER if args.variant == "all" else [args.variant]
        for v in vs:
            js = f"{DL}/windthrow_id{ev_id}_{v}.json"
            if os.path.isfile(js):
                print(f"[{v}] JSON exists — skip (delete to rerun)")
                continue
            stage_detect(ev_id, v, keep_wi=args.keep_wi)
    else:
        stage_report(ev_id)


if __name__ == "__main__":
    main()
