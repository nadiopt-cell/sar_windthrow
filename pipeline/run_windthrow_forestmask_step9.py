#!/usr/bin/env python3
"""step9 — forest-mask validation (plugin v0.9) on event ID666.

Reuses the step7 warp cache (work_data/warp) and reference masks and
answers the question: how much do UA/PA improve when detections are
restricted to forest?

Forest mask sources tested
  wc         — ESA WorldCover 2020, Tree cover (class 10), majority 3
  wc_closed  — wc + morphological closing 11x11 (bridges regrowth gaps
               inside old windthrows)
  vh185      — VH-proxy: median pre VH > -18.5 dB (+ sensitivity
               vh180 / vh190 written for reference)

Variants (step7 definitions, identical thresholds -> clean comparison):
  A — pair 22.07->03.08 (WET), adaptive, norm OFF
  C — stack 3-pre ->03.08 (WET), adaptive, norm OFF
  D — stack + FIXED 3.0 dB, norm ON

Background statistics stay on the step7 bg ring (3 km minus reference)
for ALL runs, so adaptive thresholds are identical to step7 and any
metric change is caused by the forest restriction alone.

Stages:
  mask                  — build forest masks + coverage diagnostics
  detect --variant A --mode wc[|wc_closed|vh185|all]
  report                — object-based PA/UA vs step7 baselines -> JSON
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

import numpy as np

ROOT = "/home/z/my-project"
sys.path.insert(0, f"{ROOT}/plugin_work")

from osgeo import gdal  # noqa: E402
from scipy import ndimage  # noqa: E402

from sentinel1_windthrow_plugin.sources import forest_mask as fm  # noqa: E402
from sentinel1_windthrow_plugin.sources.windthrow import (  # noqa: E402
    WindthrowDetector,
    build_median_composite,
)

MANIFEST = f"{ROOT}/download/step7_warp_manifest.json"
WARP = f"{ROOT}/work_data/warp"
FOREST = f"{ROOT}/work_data/forest"
OUT_FM = f"{ROOT}/work_data/out_fm"
DL = f"{ROOT}/download"
PIX = 10.0
GRID = {"width": 5245, "height": 5108}

VARIANTS = {
    "A": {"pre_stack": False, "post": "post", "norm": False,
          "mode": "adaptive",
          "desc": "pair 22.07->03.08 (WET), adaptive, norm OFF"},
    "C": {"pre_stack": True, "post": "post", "norm": False,
          "mode": "adaptive",
          "desc": "stack 3-pre ->03.08 (WET), adaptive, norm OFF"},
    "D": {"pre_stack": True, "post": "post", "norm": True,
          "mode": "fixed",
          "desc": "stack + FIXED 3.0 dB, norm ON (WET post)"},
}
MODES = ("wc", "wc_closed", "vh185")
FOREST_PATHS = {
    "wc": f"{FOREST}/id666_wc2020_forest.tif",
    "wc_closed": f"{FOREST}/id666_wc2020_forest_closed.tif",
    "vh180": f"{FOREST}/id666_vh180_forest.tif",
    "vh185": f"{FOREST}/id666_vh185_forest.tif",
    "vh190": f"{FOREST}/id666_vh190_forest.tif",
}
STACK_PRE_VV = f"{WARP}/stack_pre_VV.tif"
STACK_PRE_VH = f"{WARP}/stack_pre_VH.tif"


# ======================================================================
def load_manifest():
    with open(MANIFEST) as f:
        return json.load(f)


def read_band(path):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    arr = ds.GetRasterBand(1).ReadAsArray()
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ds = None
    return arr, gt, proj


def write_byte_mask(path, arr, gt, proj):
    driver = gdal.GetDriverByName("GTiff")
    if os.path.exists(path):
        driver.Delete(path)
    ds = driver.Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Byte,
                       options=["TILED=YES", "COMPRESS=LZW"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.SetNoDataValue(0)
    band.FlushCache()
    ds = None
    return path


# ======================================================================
# Stage: mask
# ======================================================================
def stage_mask():
    os.makedirs(FOREST, exist_ok=True)
    man = load_manifest()
    ref_info = fm.read_ref_info(man["scenes"]["post"]["warp_vv"])
    bbox = fm.bbox_4326(ref_info)
    print(f"AOI bbox 4326: {[round(x, 4) for x in bbox]}", flush=True)

    diag = {"bbox_4326": [round(x, 5) for x in bbox], "masks": {}}

    # --- 1) ESA WorldCover 2020 --------------------------------------
    wc_path = FOREST_PATHS["wc"]
    if not os.path.isfile(wc_path):
        t0 = time.time()
        fm.build_worldcover_forest_mask(
            bbox, ref_info, wc_path, year=2020, majority_size=3,
            progress_cb=lambda f, m: print(f"  {f:5.1f}% {m}", flush=True))
        print(f"[wc] built in {time.time() - t0:.0f} s")
    else:
        print(f"[wc] exists, skip: {wc_path}")

    # --- 2) closed variant (binary closing 11x11) --------------------
    wc_closed = FOREST_PATHS["wc_closed"]
    if not os.path.isfile(wc_closed):
        arr, gt, proj = read_band(wc_path)
        closed = ndimage.binary_closing(
            arr > 0, structure=np.ones((11, 11)))
        write_byte_mask(wc_closed, closed.astype(np.uint8) * 255, gt, proj)
        print(f"[wc_closed] built: {wc_closed}")
    else:
        print(f"[wc_closed] exists, skip")

    # --- 3) VH-proxy masks from the 3-pre median composite -----------
    pre_vh = f"{ROOT}/work_data/out/F/id666_F_pre_VH.tif"
    if not os.path.isfile(pre_vh):
        raise RuntimeError(f"pre composite not found: {pre_vh}")
    arr, gt, proj = read_band(pre_vh)
    finite = arr[np.isfinite(arr)]
    print(f"[vh] pre-composite VH: median={np.median(finite):.2f} dB, "
          f"p10={np.percentile(finite, 10):.2f}, "
          f"p90={np.percentile(finite, 90):.2f}")
    for thr, path in ((-18.0, FOREST_PATHS["vh180"]),
                      (-18.5, FOREST_PATHS["vh185"]),
                      (-19.0, FOREST_PATHS["vh190"])):
        if not os.path.isfile(path):
            mask = (arr > thr).astype(np.uint8) * 255
            mask = fm.majority_filter_mask(mask, 3)
            write_byte_mask(path, mask, gt, proj)
        print(f"[vh] {thr:+.1f} dB -> {os.path.basename(path)}")

    # --- 4) coverage diagnostics --------------------------------------
    ref_arr, _, _ = read_band(man["masks"]["ref"])
    ref = ref_arr > 0
    ref_ha = ref.sum() * PIX * PIX / 1e4
    aoi_ha = ref.size * PIX * PIX / 1e4
    diag["ref_area_ha"] = round(ref_ha, 1)
    diag["aoi_area_ha"] = round(aoi_ha, 1)
    for name, path in (("wc", wc_path), ("wc_closed", wc_closed),
                       ("vh180", FOREST_PATHS["vh180"]),
                       ("vh185", FOREST_PATHS["vh185"]),
                       ("vh190", FOREST_PATHS["vh190"])):
        arr, _, _ = read_band(path)
        forest = arr > 0
        cov = float((ref & forest).sum()) / max(1, int(ref.sum()))
        share = float(forest.sum()) / forest.size
        diag["masks"][name] = {
            "path": path,
            "forest_share_aoi": round(share, 4),
            "forest_area_ha": round(forest.sum() * PIX * PIX / 1e4, 1),
            "ref_coverage": round(cov, 4),
        }
        print(f"  {name:10s}: forest {share * 100:5.1f}% of AOI, "
              f"ref coverage {cov * 100:5.1f}%")
    out = f"{DL}/step9_forest_mask_diag_{datetime.now():%Y-%m-%d}.json"
    with open(out, "w") as f:
        json.dump(diag, f, ensure_ascii=False, indent=2)
    print(f"diag -> {out}")


# ======================================================================
# Stage: detect
# ======================================================================
def run_detect(variant, mode):
    man = load_manifest()
    cfg = VARIANTS[variant]
    if mode == "none":
        forest_path = None
    else:
        forest_path = FOREST_PATHS[mode]
        if not os.path.isfile(forest_path):
            raise RuntimeError(f"forest mask missing: {forest_path} "
                               f"(run stage mask first)")

    if cfg["pre_stack"]:
        # Shared 3-pre median composites (built once, from the step7
        # stack definition; F's composites are exactly median(pre1..3))
        for pol, dst in (("VV", STACK_PRE_VV), ("VH", STACK_PRE_VH)):
            if not os.path.isfile(dst):
                src = f"{ROOT}/work_data/out/F/id666_F_pre_{pol}.tif"
                if os.path.isfile(src):
                    shutil.copyfile(src, dst)
                else:
                    build_median_composite(
                        [man["scenes"][lab][f"warp_{pol.lower()}"]
                         for lab in ("pre1", "pre2", "pre3")],
                        fm.read_ref_info(man["scenes"]["post"]["warp_vv"]),
                        f"{WARP}/_tmp", dst)
        pre_paths = [STACK_PRE_VV, STACK_PRE_VH]
    else:
        pre_paths = [man["scenes"]["base"]["warp_vv"],
                     man["scenes"]["base"]["warp_vh"]]
    post_paths = [man["scenes"][cfg["post"]]["warp_vv"],
                  man["scenes"][cfg["post"]]["warp_vh"]]
    bg_mask = man["masks"]["bg"]

    out_dir = f"{OUT_FM}/{variant}_{mode}"
    os.makedirs(out_dir, exist_ok=True)
    out_base = f"{out_dir}/id666_{variant}_{mode}"
    mask_path = f"{out_base}_mask.tif"
    json_path = f"{DL}/windthrow_id666_{variant}_{mode}_step9.json"
    if os.path.isfile(mask_path) and os.path.isfile(json_path):
        print(f"[{variant}/{mode}] done already, skip")
        return

    det = WindthrowDetector(
        threshold_mode=cfg["mode"], a_db=2.9, fixed_threshold_db=3.0,
        min_pixels=27, median_filter_size=3,
        normalize_background=cfg["norm"])
    t0 = time.time()
    res = det.detect_file(
        pre_paths=pre_paths, post_paths=post_paths,
        output_base=out_base,
        background_mask_path=bg_mask,
        forest_mask_path=forest_path,
        progress_cb=lambda f, m: print(f"  {f:5.1f}% {m}", flush=True))
    runtime = time.time() - t0

    arr, gt, _ = read_band(res["mask"])
    px_ha = abs(gt[1] * gt[5]) / 1e4
    det_ha = float((arr == 255).sum() * px_ha)
    record = {
        "variant": variant, "mask_mode": mode,
        "desc": cfg["desc"],
        "forest_mask": forest_path,
        "params": {"threshold_mode": cfg["mode"], "a_db": 2.9,
                   "fixed_threshold_db": 3.0, "min_pixels": 27,
                   "median_filter_size": 3},
        "offset_db": res["offset_db"], "mean_wi": res["mean_wi"],
        "threshold_db": res["threshold_db"],
        "n_objects": res["n_objects"],
        "detected_area_ha": round(det_ha, 1),
        "runtime_s": round(runtime, 1),
        "outputs": {"wi": res["wi"], "mask": res["mask"],
                    "vector": res["vector"]},
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with open(json_path, "w") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"[{variant}/{mode}] DONE {runtime:.0f} s -> {json_path}")
    print(f"  mean_wi={record['mean_wi']:.3f} "
          f"thr={record['threshold_db']:.3f} "
          f"n_obj={record['n_objects']} area={det_ha:.1f} ha")


# ======================================================================
# Stage: report
# ======================================================================
def object_metrics(det_mask, ref_mask):
    """Object-based PA/UA (overlap >= 30% of the object), 8-connected."""
    struct = np.ones((3, 3), dtype=bool)
    ref_lab, n_ref = ndimage.label(ref_mask > 0, structure=struct)
    det_lab, n_det = ndimage.label(det_mask > 0, structure=struct)
    if n_ref == 0 or n_det == 0:
        return {"n_ref_components": int(n_ref), "n_det_objects": int(n_det),
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


def stage_report():
    man = load_manifest()
    ref_arr, _, _ = read_band(man["masks"]["ref"])
    ref = ref_arr > 0
    ref_ha = ref.sum() * PIX * PIX / 1e4

    variants = {}
    for variant in VARIANTS:
        rows = {}
        # baseline "none": step7 result mask (same grid)
        base_json = f"{DL}/windthrow_id666_{variant}.json"
        if os.path.isfile(base_json):
            with open(base_json) as f:
                rec = json.load(f)
            arr, _, _ = read_band(rec["outputs"]["mask"])
            met = object_metrics(arr > 0, ref)
            met["detected_area_ha"] = rec["detected_area_ha"]
            met["mean_wi"] = rec["mean_wi"]
            met["threshold_db"] = rec["threshold_db"]
            rows["none"] = met
        for mode in MODES:
            p = f"{DL}/windthrow_id666_{variant}_{mode}_step9.json"
            if not os.path.isfile(p):
                print(f"[report] missing {p}")
                continue
            with open(p) as f:
                rec = json.load(f)
            arr, _, _ = read_band(rec["outputs"]["mask"])
            met = object_metrics(arr > 0, ref)
            met["detected_area_ha"] = rec["detected_area_ha"]
            met["mean_wi"] = rec["mean_wi"]
            met["threshold_db"] = rec["threshold_db"]
            rows[mode] = met
        variants[variant] = rows
        for mode, met in rows.items():
            print(f"[{variant}/{mode:9s}] PA={met['pa']:.3f} "
                  f"UA={met['ua']:.3f} F1={met['f1']:.3f} "
                  f"area={met['detected_area_ha']:.0f} ha "
                  f"({met['n_det_objects']} obj)")

    diag_path = f"{DL}/step9_forest_mask_diag_{datetime.now():%Y-%m-%d}.json"
    diag = {}
    if os.path.isfile(diag_path):
        with open(diag_path) as f:
            diag = json.load(f)

    out = {
        "event": {"id": 666, "storm": "squall 30.07.2017",
                  "region": "north Sverdlovsk oblast",
                  "ref_area_ha": round(ref_ha, 1)},
        "purpose": ("v0.9 forest mask validation: object-based PA/UA "
                    "without vs with forest restriction; thresholds "
                    "identical to step7 (stats on the bg ring)"),
        "forest_masks": diag.get("masks", {}),
        "variants": variants,
        "metric_definition": {
            "type": "object-based, 8-connected components",
            "pa": "reference component is TP when >=30% of its pixels "
                  "are flagged",
            "ua": "detected object is TP when >=30% of its pixels are "
                  "inside reference",
        },
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    path = (f"{DL}/windthrow_id666_forestmask_step9_"
            f"{datetime.now():%Y-%m-%d}.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"report -> {path}")


# ======================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["mask", "detect", "report"])
    ap.add_argument("--variant", default="all",
                    help="detect: A|C|D|all")
    ap.add_argument("--mode", default="all",
                    help="detect: wc|wc_closed|vh185|none|all")
    args = ap.parse_args()

    if args.stage == "mask":
        stage_mask()
    elif args.stage == "detect":
        variants = list(VARIANTS) if args.variant == "all" else \
            [args.variant.upper()]
        modes = list(MODES) if args.mode == "all" else [args.mode]
        for v in variants:
            for m in modes:
                run_detect(v, m)
    else:
        stage_report()


if __name__ == "__main__":
    main()
