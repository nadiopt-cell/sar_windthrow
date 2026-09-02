#!/usr/bin/env python3
"""Final analysis addendum: forest-proxy contrast + per-patch QA (ID666).

Isolates the windthrow signal against a FOREST background proxy
(dark C-VV pixels of the 3 km ring) instead of the mixed-land-cover
ring mean, and checks the biggest reference patches individually.
Writes download/windthrow_id666_analysis_2026-09-02.json
"""
import json
import numpy as np
from osgeo import gdal
from scipy import ndimage

gdal.UseExceptions()
W = "/home/z/my-project/work_data/warp"
OUT = "/home/z/my-project/work_data/out"


def read(path):
    ds = gdal.Open(path)
    a = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    return a


def to_db(a):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(a > 0, 10.0 * np.log10(a), np.nan)


def main():
    ref = read(f"{W}/ref_mask.tif") > 0
    bg = read(f"{W}/bg_mask.tif") > 0
    base_vv = to_db(read(f"{W}/base_vv.tif"))
    base_vh = to_db(read(f"{W}/base_vh.tif"))
    base_wi = base_vv + base_vh

    # forest proxy: within the ring, forest pixels are the dark tail of
    # the C-VV distribution (bright tail = fields / bare soil)
    thr = np.nanpercentile(base_vv[bg & ~ref], 40)  # 40% darkest ring px
    forest_ring = bg & ~ref & (base_vv < thr)
    print(f"forest-proxy: VV<{thr:.2f} dB, {forest_ring.sum() / 1e4:.0f} ha")

    res = {"forest_proxy_vv_thresh_db": round(float(thr), 2),
           "forest_proxy_ha": round(float(forest_ring.sum() * 0.01), 1)}

    # WI contrast per post date vs base, inside ref vs forest ring
    contrasts = {}
    for post_lab in ("post", "post2"):
        post_wi = (to_db(read(f"{W}/{post_lab}_vv.tif"))
                   + to_db(read(f"{W}/{post_lab}_vh.tif")))
        wi = post_wi - base_wi
        med_ref = float(np.nanmedian(wi[ref]))
        med_for = float(np.nanmedian(wi[forest_ring]))
        contrasts[post_lab] = {
            "wi_median_ref": round(med_ref, 2),
            "wi_median_forest_ring": round(med_for, 2),
            "contrast_db": round(med_ref - med_for, 2),
            "share_ref_gt_3db": round(float((wi[ref] > 3.0).mean()), 3),
            "share_ref_gt_contrast_plus_a": round(float(
                (wi[ref] > med_for + 2.9).mean()), 3),
        }
        del wi, post_wi
    res["contrast"] = contrasts

    # per-patch QA on the best variant so far (F, dry stack adaptive)
    for v in ("A", "F"):
        mask = read(f"{OUT}/{v}/id666_{v}_mask.tif") == 255
        struct = np.ones((3, 3), dtype=bool)
        ref_lab, n = ndimage.label(ref, structure=struct)
        det_counts = np.bincount(ref_lab[mask].ravel(), minlength=n + 1)
        sizes = np.bincount(ref_lab.ravel())
        hit = det_counts[1:] >= 0.3 * sizes[1:]
        # size classes of ref components
        ha = sizes[1:] * 0.01
        qa = {}
        for lo, hi in ((0, 2), (2, 5), (5, 10), (10, 10**9)):
            sel = (ha >= lo) & (ha < hi)
            qa[f"{lo}-{hi if hi < 10**9 else 'max'} ha"] = {
                "n": int(sel.sum()),
                "hit_rate": round(float(hit[sel].mean()), 3) if sel.any() else None,
            }
        res[f"patch_hit_rate_by_size_{v}"] = qa
        del ref_lab

    path = ("/home/z/my-project/download/"
            "windthrow_id666_analysis_2026-09-02.json")
    with open(path, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    print("->", path)


if __name__ == "__main__":
    main()
