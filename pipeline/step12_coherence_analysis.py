#!/usr/bin/env python3
"""step12b: C-band coherence analysis on HyP3 INSAR-GAMMA products (both events).

For each event the prepost pair (straddling the event) and the post-post
control pair are compared:

  ID666  storm 30.07.2017 (Komi):   prepost 2017-07-22 -> 2017-08-03,
                                    control 2017-08-03 -> 2017-08-15
  ID694  tornado 19.09.2017 (Perm): prepost 2017-09-12 -> 2017-09-24,
                                    control 2017-09-24 -> 2017-10-06

Method (mirrors step7/9/11 sampling):
  - reference polygons: Shikhov et al. 2020 (ESSD) Windthrows.shp, ID filter
  - coherence raster: HyP3 corr.tif, 80 m posting, gamma0 pair processing
    (20x4 looks, INT80)
  - background: 10 km buffer around ref polygons MINUS ref MINUS water
    (product water_mask.tif: 1 = water)
  - inverted detection (L-band step11 logic): windthrow => coherence DROP,
    score = 1 - coh
  - metrics: medians/means, contrast, Mann-Whitney AUC (rank-based, ties
    averaged, no scipy), Youden oracle threshold, TPR@FPR5%

Control pairs anchor the sanity check: both dates after the event, no
change expected over windthrow => AUC ~ 0.5, contrast ~ 0.

Runs with /usr/bin/python3 (osgeo gdal 3.10.x + numpy).
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()

PROJ = os.environ.get("SAR_WT_PROJ", "/home/z/my-project/plugin_work/sar_windthrow_repo")
DL = f"{PROJ}/../.." if False else "/home/z/my-project/download"
REF_SHP = "/home/z/my-project/research/shikhov_db/GIS/Windthrows.shp"
PROD = f"{PROJ}/work_data/hyp3_products"
JOBS_JSON = f"{PROJ}/work_data/hyp3_jobs_list.json"
OUT_JSON = f"{PROJ}/results/step12_coherence_analysis_2026-09-03.json"

BUFFER_M = 10_000.0
EVENT_DATE = {666: "2017-07-30", 694: "2017-09-19"}
PAIR_FILE = {
    (666, "prepost"): "S1BB_20170722T033447_20170803T033448_VVP012_INT80_G_ueF_C4A3",
    (666, "control"): "S1BB_20170803T033448_20170815T033448_VVP012_INT80_G_ueF_172D",
    (694, "prepost"): "S1BB_20170912T030234_20170924T030234_VVP012_INT80_G_ueF_5748",
    (694, "control"): "S1BB_20170924T030234_20171006T030235_VVP012_INT80_G_ueF_5C8E",
}
# Static cross-references for the comparison block
LBAND = {
    666: {"dHH_invAUC": 0.8703, "dHV_invAUC": 0.7318, "src": "step11b"},
    694: {"dHH_invAUC": [0.7327, 0.8053], "dHV_invAUC": [0.905, 0.5938],
          "src": "step11c (d2017-2016, d2018-2017)"},
}


# ----------------------------------------------------------------------
# Vector helpers
# ----------------------------------------------------------------------
def event_union(ref_id):
    """Union of all Windthrows.shp parts for event ref_id, native CRS."""
    ds = ogr.Open(REF_SHP)
    lyr = ds.GetLayer(0)
    lyr.SetAttributeFilter(f"ID = {ref_id}")
    union = None
    n = 0
    for feat in lyr:
        g = feat.GetGeometryRef().Clone()
        n += 1
        union = g if union is None else union.Union(g)
    ds = None
    if union is None:
        raise RuntimeError(f"Event ID={ref_id} not found in {REF_SHP}")
    return union, n


def to_srs(geom, srs_dst):
    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromWkt(srs_dst.ExportToWkt())
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    g = geom.Clone()
    g.Transform(osr.CoordinateTransformation(src, dst))
    return g


def rasterize(geom, gt, w, h, proj_wkt):
    """Burn geometry onto an in-memory raster grid -> bool mask."""
    mem = ogr.GetDriverByName("Memory").CreateDataSource("m")
    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj_wkt)
    lyr = mem.CreateLayer("g", srs, ogr.wkbPolygon)
    feat = ogr.Feature(lyr.GetLayerDefn())
    feat.SetGeometry(geom)
    lyr.CreateFeature(feat)
    drv = gdal.GetDriverByName("MEM")
    r = drv.Create("", w, h, 1, gdal.GDT_Byte)
    r.SetGeoTransform(gt)
    r.SetProjection(proj_wkt)
    gdal.RasterizeLayer(r, [1], lyr, burn_values=[255])
    mask = r.ReadAsArray().astype(bool)
    r = None
    mem = None
    return mask


# ----------------------------------------------------------------------
# Stats helpers (step10/11 style, no scipy)
# ----------------------------------------------------------------------
def rankdata_avg(a):
    """Average-tie ranks (scipy rankdata equivalent, no scipy)."""
    sorter = np.argsort(a, kind="mergesort")
    inv = np.empty(len(a), np.intp)
    inv[sorter] = np.arange(len(a), dtype=np.intp)
    arr = a[sorter]
    obs = np.r_[True, arr[1:] != arr[:-1]]
    dense = obs.cumsum()[inv]                    # 1-based dense rank per element
    count = np.r_[np.nonzero(obs)[0], len(obs)]  # block starts + total
    return 0.5 * (count[dense] + count[dense - 1] + 1)


def auc_mw(bg_score, ref_score):
    """Mann-Whitney AUC = P(ref_score > bg_score) + 0.5*P(equal)."""
    n1, n2 = len(ref_score), len(bg_score)
    allv = np.concatenate([bg_score, ref_score])   # [bg (n2), ref (n1)]
    ranks = rankdata_avg(allv)
    r1 = ranks[n2:].sum()          # ref block is the tail (skip n2 bg ranks)
    u = r1 - n1 * (n1 + 1) / 2.0
    return float(u / (n1 * n2))


def block_stats(v):
    if len(v) == 0:
        return {}
    q25, q75 = np.percentile(v, [25, 75])
    return {"n_px": int(len(v)), "median": round(float(np.median(v)), 4),
            "mean": round(float(v.mean()), 4), "p25": round(float(q25), 4),
            "p75": round(float(q75), 4)}


def detection_metrics(ref_coh, bg_coh):
    """Inverted detection: windthrow = coherence drop => score = 1 - coh."""
    ref_s, bg_s = 1.0 - ref_coh, 1.0 - bg_coh
    auc = auc_mw(bg_s, ref_s)
    # Youden oracle on a shared threshold grid (quantiles of the pooled score)
    pool = np.concatenate([ref_s, bg_s])
    grid = np.unique(np.percentile(pool, np.linspace(0.1, 99.9, 400)))
    tpr = np.array([(ref_s >= t).mean() for t in grid])
    fpr = np.array([(bg_s >= t).mean() for t in grid])
    j = tpr - fpr
    k = int(np.argmax(j))
    # TPR at FPR = 5 %
    t5 = np.percentile(bg_s, 95)
    tpr5 = float((ref_s >= t5).mean())
    return {
        "auc_score_1minuscoh": round(auc, 4),
        "youden": {"thr": round(float(grid[k]), 4),
                   "tpr": round(float(tpr[k]), 4),
                   "fpr": round(float(fpr[k]), 4),
                   "j": round(float(j[k]), 4)},
        "tpr_at_fpr5pct": round(tpr5, 4),
    }


def all_windthrow_mask(geom_ref_union_native, gt, w, h, proj_wkt):
    """Mask of ALL Windthrows.shp polygons (any event) on the given grid.

    The ID666 ring contains windthrows of OTHER events of the same
    30.07.2017 derecho - they would contaminate the background.
    geom_ref_union_native must be in the shapefile CRS (EPSG:4326).
    """
    ds = ogr.Open(REF_SHP)
    lyr = ds.GetLayer(0)
    env = geom_ref_union_native.GetEnvelope()   # native lon/lat bbox
    lyr.SetSpatialFilterRect(env[0] - 0.15, env[2] - 0.15,
                             env[1] + 0.15, env[3] + 0.15)
    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromWkt(proj_wkt)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(src, dst)
    mem = ogr.GetDriverByName("Memory").CreateDataSource("m")
    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj_wkt)
    lyr2 = mem.CreateLayer("g", srs, ogr.wkbPolygon)
    n = 0
    for feat in lyr:
        g = feat.GetGeometryRef().Clone()
        for i in range(g.GetGeometryCount()):
            part = g.GetGeometryRef(i).Clone()
            part.Transform(tr)
            f2 = ogr.Feature(lyr2.GetLayerDefn())
            f2.SetGeometry(part)
            lyr2.CreateFeature(f2)
            n += 1
    ds = None
    drv = gdal.GetDriverByName("MEM")
    r = drv.Create("", w, h, 1, gdal.GDT_Byte)
    r.SetGeoTransform(gt)
    r.SetProjection(proj_wkt)
    gdal.RasterizeLayer(r, [1], lyr2, burn_values=[255])
    mask = r.ReadAsArray().astype(bool)
    r = None
    mem = None
    return mask, n


# ----------------------------------------------------------------------
# Per-pair analysis
# ----------------------------------------------------------------------
def analyse_pair(ref_id, pair, product):
    base = os.path.join(PROD, product, product, product)
    corr_p, water_p = f"{base}_corr.tif", f"{base}_water_mask.tif"

    ds = gdal.Open(corr_p)
    w, h = ds.RasterXSize, ds.RasterYSize
    gt, proj = ds.GetGeoTransform(), osr.SpatialReference(ds.GetProjection())
    coh = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    ds = None
    valid = np.isfinite(coh) & (coh > 0)          # nodata = 0
    wds = gdal.Open(water_p)
    water = wds.GetRasterBand(1).ReadAsArray()
    wds = None
    if water.shape != coh.shape:
        raise RuntimeError(f"water mask grid mismatch for {product}")
    # 1 = water. Some products ship a CORRUPT mask (e.g. 5748 flags 99.6 %
    # of a Perm-forest frame as water) - treat masks that flag > 50 % of
    # the frame as water as corrupt and fall back to no water masking.
    water_frac = float((water == 1).mean())
    mask_ok = water_frac < 0.5

    union_native, n_parts = event_union(ref_id)
    union = to_srs(union_native, proj)
    ref_mask = rasterize(union, gt, w, h, proj.ExportToWkt())
    buf_mask = rasterize(union.Buffer(BUFFER_M), gt, w, h, proj.ExportToWkt())
    other_mask, n_other = all_windthrow_mask(union_native, gt, w, h, proj.ExportToWkt())
    if mask_ok:
        land = water == 0
        bg_mask = buf_mask & ~ref_mask & ~other_mask & land & valid
        ref_valid = ref_mask & valid & land
    else:
        bg_mask = buf_mask & ~ref_mask & ~other_mask & valid
        ref_valid = ref_mask & valid

    ref_coh = coh[ref_valid]
    bg_coh = coh[bg_mask]
    res = {
        "pair": pair,
        "product": product,
        "ref_parts_in_shp": n_parts,
        "grid": {"w": w, "h": h, "posting_m": abs(gt[1]),
                 "epsg": proj.GetAuthorityCode(None)},
        "ref": block_stats(ref_coh),
        "background": block_stats(bg_coh),
        "water_mask_used": bool(mask_ok),
        "water_mask_flagged_fraction": round(water_frac, 4),
        "other_windthrow_parts_excluded": n_other,
    }
    if res["ref"] and res["background"]:
        res["contrast_median"] = round(
            res["background"]["median"] - res["ref"]["median"], 4)
        res.update(detection_metrics(ref_coh, bg_coh))
    else:
        res["contrast_median"] = None
    return res


# ----------------------------------------------------------------------
# Stage did: difference-in-differences (control corr warped onto prepost grid)
# ----------------------------------------------------------------------
def analyse_did(ref_id, prepost_prod, control_prod):
    """dcoh = coh(control) - coh(prepost) on the common grid.

    Cancels the static debris anomaly and the seasonal background shift;
    the residual ref-vs-bg contrast is the event-specific decorrelation.
    """
    def base_of(p):
        return os.path.join(PROD, p, p, p)

    pre_path, ctl_path = f"{base_of(prepost_prod)}_corr.tif", f"{base_of(control_prod)}_corr.tif"
    ds = gdal.Open(pre_path)
    w, h = ds.RasterXSize, ds.RasterYSize
    gt, proj_wkt = ds.GetGeoTransform(), ds.GetProjection()
    proj = osr.SpatialReference(proj_wkt)
    coh_pre = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    ds = None
    ds = gdal.Open(ctl_path)
    coh_ctl = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    gt_ctl = ds.GetGeoTransform()
    ds = None

    same_grid = gt_ctl == gt and coh_ctl.shape == coh_pre.shape
    if not same_grid:
        ds = gdal.Warp("", ctl_path, format="MEM", width=w, height=h,
                       outputBounds=(gt[0], gt[3] + h * gt[5],
                                     gt[0] + w * gt[1], gt[3]),
                       resampleAlg="bilinear")
        coh_ctl = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
        ds = None
    dcoh = coh_ctl - coh_pre
    valid = np.isfinite(dcoh)

    union_native, n_parts = event_union(ref_id)
    union = to_srs(union_native, proj)
    ref_mask = rasterize(union, gt, w, h, proj_wkt)
    buf_mask = rasterize(union.Buffer(BUFFER_M), gt, w, h, proj_wkt)
    other_mask, n_other = all_windthrow_mask(union_native, gt, w, h, proj_wkt)
    bg_mask = buf_mask & ~ref_mask & ~other_mask & valid
    ref_valid = ref_mask & valid

    ref_v, bg_v = dcoh[ref_valid], dcoh[bg_mask]
    res = {
        "control_warped_to_prepost_grid": not same_grid,
        "ref": block_stats(ref_v),
        "background": block_stats(bg_v),
        "other_windthrow_parts_excluded": n_other,
    }
    if len(ref_v) and len(bg_v):
        # positive = windthrow decorrelated MORE than background in the
        # prepost pair (event excess decorrelation)
        res["did_excess_median"] = round(float(np.median(ref_v) - np.median(bg_v)), 4)
        # detection score = dcoh itself (bigger = more event decorrelation)
        auc = auc_mw(bg_v, ref_v)   # AUC = P(ref_dcoh > bg_dcoh)
        res["auc_dcoh"] = round(auc, 4)
        pool = np.concatenate([ref_v, bg_v])
        grid = np.unique(np.percentile(pool, np.linspace(0.1, 99.9, 400)))
        tpr = np.array([(ref_v >= t).mean() for t in grid])
        fpr = np.array([(bg_v >= t).mean() for t in grid])
        k = int(np.argmax(tpr - fpr))
        t5 = np.percentile(bg_v, 95)
        res["youden"] = {"thr": round(float(grid[k]), 4),
                         "tpr": round(float(tpr[k]), 4),
                         "fpr": round(float(fpr[k]), 4)}
        res["tpr_at_fpr5pct"] = round(float((ref_v >= t5).mean()), 4)
    return res


# ----------------------------------------------------------------------
def main():
    with open(JOBS_JSON) as f:
        jobs = {j["name"]: j for j in json.load(f)["jobs"]}

    out = {
        "step": "12b",
        "title": "C-band coherence on HyP3 INSAR-GAMMA products (ID666, ID694)",
        "generated": datetime.utcnow().isoformat() + "Z",
        "method": {
            "reference": "Shikhov et al. 2020 (ESSD) Windthrows.shp, ID filter",
            "background": (f"buffer {int(BUFFER_M//1000)} km minus ALL Shikhov windthrow "
                           "polygons (same-derecho neighbours excluded); product water_mask "
                           "applied only when sane (<50 % of frame flagged water; "
                           "mask of product 5748 is corrupt: 99.6 % -> ignored)"),
            "detection": "inverted (windthrow = coherence DROP), score = 1 - coh",
            "did": "dcoh = coh(control pair) - coh(prepost pair), control warped "
                   "onto prepost grid; cancels static debris anomaly + seasonal shift",
            "metrics": "Mann-Whitney AUC (rank/ties-avg), Youden oracle, TPR@FPR5%",
            "sanity_anchor": "control pairs (post-post): expect AUC ~ 0.5, contrast ~ 0",
        },
        "products": {},
        "events": {},
    }
    for name, j in sorted(jobs.items()):
        out["products"][name] = {
            "job_id": j["job_id"], "status": j["status_code"],
            "request_time": j["request_time"],
            "expiration_time": j.get("expiration_time"),
            "credit_cost": j.get("credit_cost"),
            "granules": j["job_parameters"]["granules"],
            "looks": j["job_parameters"].get("looks"),
            "product_zip": j["files"][0]["filename"] if j.get("files") else None,
        }

    for ref_id in (666, 694):
        ev = {"event_date": EVENT_DATE[ref_id], "pairs": {}}
        for pair in ("prepost", "control"):
            product = PAIR_FILE[(ref_id, pair)]
            print(f"[run] ID{ref_id} {pair}: {product}", flush=True)
            ev["pairs"][pair] = analyse_pair(ref_id, pair, product)
            p = ev["pairs"][pair]
            print(f"      ref med {p['ref'].get('median')} bg med {p['background'].get('median')} "
                  f"contrast {p.get('contrast_median')} AUC {p.get('auc_score_1minuscoh')}",
                  flush=True)
        pp, ct = ev["pairs"]["prepost"], ev["pairs"]["control"]
        ev["delta_check"] = {
            "contrast_prepost_minus_control": round(
                (pp.get("contrast_median") or 0) - (ct.get("contrast_median") or 0), 4),
            "auc_prepost": pp.get("auc_score_1minuscoh"),
            "auc_control": ct.get("auc_score_1minuscoh"),
        }
        print(f"[run] ID{ref_id} DID (control minus prepost) ...", flush=True)
        ev["did"] = analyse_did(ref_id, PAIR_FILE[(ref_id, "prepost")],
                                PAIR_FILE[(ref_id, "control")])
        d = ev["did"]
        print(f"      did excess {d.get('did_excess_median')} AUC(dcoh) {d.get('auc_dcoh')} "
              f"TPR@FPR5% {d.get('tpr_at_fpr5pct')}", flush=True)
        ev["lband_reference"] = LBAND[ref_id]
        out["events"][f"ID{ref_id}"] = ev

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[ok ] -> {OUT_JSON}", flush=True)


if __name__ == "__main__":
    main()
