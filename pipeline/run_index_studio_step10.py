#!/usr/bin/env python3
"""step10 — index studio: which SAR observable separates windthrow?

After step7/step8/step9 the WI = dVV + dVH sum shows no usable C-band
signal on the Urals test events (leaf-on contrast −1.0 dB, leaf-off
+0.01…0.02 dB) and the false alarms are forest-internal. This studio
re-derives the index from the SAME cached composites and asks, per
candidate observable:

  dVV, dVH, WI = dVV+dVH, dpol = dVH−dVV (= Δ(VH/VV) in dB, identical
  to the log-ratio change), weighted sums (0.5/1.5 weights on each
  polarisation)

Metrics (pixel level, reference polygons vs forest background):
  * contrast = median(ref) − median(background), dB
  * ROC AUC (Mann-Whitney over samples)
  * Youden-optimal threshold + TPR/FPR (oracle, not operational)
  * adaptive threshold = mean(background) + 2.9 dB (paper) → TPR/FPR

Stage `study`  — sampling pass over chunks, JSON report.
Stage `detect` — in-memory object-level run for selected indices:
                 median 3×3 → threshold (adaptive / youden) →
                 min_pixels 27 → object-based PA/UA vs reference.

Background sample: bg ring ∩ WorldCover forest (ID666) or bg ring
(ID694, no WC mask built). Reference is always excluded.
"""

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

ROOT = "/home/z/my-project"
sys.path.insert(0, f"{ROOT}/plugin_work")

from osgeo import gdal  # noqa: E402
from scipy import ndimage  # noqa: E402

from sentinel1_windthrow_plugin.sources.windthrow import (  # noqa: E402
    filter_small_objects,
)

DL = f"{ROOT}/download"

# ---------------------------------------------------------------- events
EVENTS = {
    "666_stack": {
        "desc": "ID666 squall 30.07.2017, stack pre1-3 -> 03.08 (leaf-on, wet post)",
        "pre_vv": f"{ROOT}/work_data/out/F/id666_F_pre_VV.tif",
        "pre_vh": f"{ROOT}/work_data/out/F/id666_F_pre_VH.tif",
        "post_vv": f"{ROOT}/work_data/warp/post_vv.tif",
        "post_vh": f"{ROOT}/work_data/warp/post_vh.tif",
        "ref": f"{ROOT}/work_data/warp/ref_mask.tif",
        "bg": f"{ROOT}/work_data/warp/bg_mask.tif",
        "forest": f"{ROOT}/work_data/forest/id666_wc2020_forest.tif",
    },
    "666_pair": {
        "desc": "ID666, pair 22.07 -> 03.08 (leaf-on, wet post)",
        "pre_vv": f"{ROOT}/work_data/warp/base_vv.tif",
        "pre_vh": f"{ROOT}/work_data/warp/base_vh.tif",
        "post_vv": f"{ROOT}/work_data/warp/post_vv.tif",
        "post_vh": f"{ROOT}/work_data/warp/post_vh.tif",
        "ref": f"{ROOT}/work_data/warp/ref_mask.tif",
        "bg": f"{ROOT}/work_data/warp/bg_mask.tif",
        "forest": f"{ROOT}/work_data/forest/id666_wc2020_forest.tif",
    },
    "694_pair": {
        "desc": "ID694 tornado ~19.09.2017, pair 31.08 -> 24.09 (leaf-off)",
        "pre_vv": f"{ROOT}/work_data/warp_id694/base_vv.tif",
        "pre_vh": f"{ROOT}/work_data/warp_id694/base_vh.tif",
        "post_vv": f"{ROOT}/work_data/warp_id694/post_vv.tif",
        "post_vh": f"{ROOT}/work_data/warp_id694/post_vh.tif",
        "ref": f"{ROOT}/work_data/warp_id694/ref_mask.tif",
        "bg": f"{ROOT}/work_data/warp_id694/bg_mask.tif",
        "forest": None,
    },
}

# index name -> (w_vv, w_vh) applied to (dVV, dVH)
INDICES = {
    "dVV": (1.0, 0.0),
    "dVH": (0.0, 1.0),
    "WI": (1.0, 1.0),
    "dpol": (-1.0, 1.0),          # dVH - dVV = Δ(VH/VV), = Δlog-ratio
    "WI_vv0.5": (0.5, 1.0),
    "WI_vv1.5": (1.5, 1.0),
    "WI_vh0.5": (1.0, 0.5),
    "WI_vh1.5": (1.0, 1.5),
}
REF_EXCLUDE_DILATE = 0     # ref already excludes 3 km ring
SAMPLE_CAP = 1_500_000     # max background samples per event
CHUNK_ROWS = 512


# ======================================================================
def read_band(path):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    ds = None
    return arr


def read_mask(path):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    arr = ds.GetRasterBand(1).ReadAsArray() > 0
    ds = None
    return arr


def roc_auc(pos, neg):
    """AUC via Mann-Whitney U (rank-based, no sklearn)."""
    pos = np.asarray(pos, dtype=np.float64)
    neg = np.asarray(neg, dtype=np.float64)
    if pos.size == 0 or neg.size == 0:
        return 0.5
    all_v = np.concatenate([pos, neg])
    order = np.argsort(all_v, kind="mergesort")
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, all_v.size + 1)
    # average ranks for ties
    sorted_v = all_v[order]
    i = 0
    while i < sorted_v.size:
        j = i
        while j + 1 < sorted_v.size and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    r_pos = ranks[: pos.size].sum()
    u = r_pos - pos.size * (pos.size + 1) / 2.0
    return float(u / (pos.size * neg.size))


def youden_threshold(pos, neg):
    """Threshold maximising TPR - FPR over the pooled samples."""
    vals = np.concatenate([pos, neg])
    labels = np.concatenate([np.ones(pos.size), np.zeros(neg.size)])
    order = np.argsort(vals)
    vals, labels = vals[order], labels[order]
    n_pos, n_neg = pos.size, neg.size
    # moving threshold between distinct values
    tpr = np.cumsum(labels) / max(1, n_pos)
    fpr = np.cumsum(1 - labels) / max(1, n_neg)
    # candidate thresholds: value itself (flag val > thr)
    j = tpr - fpr
    k = int(np.argmax(j))
    thr = float(vals[k])
    tpr_k = float(tpr[k])
    fpr_k = float(fpr[k])
    return thr, tpr_k, fpr_k


def tpr_fpr_at(pos, neg, thr):
    tpr = float((pos > thr).mean()) if pos.size else 0.0
    fpr = float((neg > thr).mean()) if neg.size else 0.0
    return tpr, fpr


# ======================================================================
# Stage: study
# ======================================================================
def study(event_key, stride_hint=1):
    cfg = EVENTS[event_key]
    ref = read_mask(cfg["ref"])
    bg = read_mask(cfg["bg"])
    forest = read_mask(cfg["forest"]) > 0 if cfg.get("forest") else None
    bgf = bg & forest if forest is not None else bg
    bgf &= ~ref

    ref_px = int(ref.sum())
    bgf_px = int(bgf.sum())
    stride = max(1, bgf_px // SAMPLE_CAP, stride_hint)
    print(f"[{event_key}] ref {ref_px} px, bg-forest {bgf_px} px, "
          f"sample stride {stride}")

    samples = {name: {"ref": [], "bg": []} for name in INDICES}
    h, w = ref.shape
    ds = {k: gdal.Open(cfg[k], gdal.GA_ReadOnly)
          for k in ("pre_vv", "pre_vh", "post_vv", "post_vh")}
    for y0 in range(0, h, CHUNK_ROWS):
        rows = min(CHUNK_ROWS, h - y0)
        sl = np.s_[y0:y0 + rows]
        d_vv = (ds["post_vv"].GetRasterBand(1).ReadAsArray(0, y0, w, rows)
                - ds["pre_vv"].GetRasterBand(1).ReadAsArray(0, y0, w, rows)
                ).astype(np.float32)
        d_vh = (ds["post_vh"].GetRasterBand(1).ReadAsArray(0, y0, w, rows)
                - ds["pre_vh"].GetRasterBand(1).ReadAsArray(0, y0, w, rows)
                ).astype(np.float32)
        ref_c = ref[sl]
        bgf_c = bgf[sl]
        if stride > 1:
            ref_c = ref_c[::stride, ::stride]
            bgf_c = bgf_c[::stride, ::stride]
            d_vv_s = d_vv[::stride, ::stride]
            d_vh_s = d_vh[::stride, ::stride]
        else:
            d_vv_s, d_vh_s = d_vv, d_vh
        for name, (wv, wh) in INDICES.items():
            idx = (wv * d_vv_s + wh * d_vh_s).astype(np.float32)
            vals_r = idx[ref_c & np.isfinite(idx)]
            vals_b = idx[bgf_c & np.isfinite(idx)]
            if vals_r.size:
                samples[name]["ref"].append(vals_r)
            if vals_b.size:
                samples[name]["bg"].append(vals_b)
        if (y0 // CHUNK_ROWS) % 4 == 0:
            print(f"  row {y0}/{h}", flush=True)
    for k in list(ds):
        ds[k] = None

    report = {}
    for name in INDICES:
        pos = np.concatenate(samples[name]["ref"]) if samples[name]["ref"] \
            else np.array([])
        neg = np.concatenate(samples[name]["bg"]) if samples[name]["bg"] \
            else np.array([])
        if pos.size < 100 or neg.size < 100:
            print(f"[{name}] insufficient samples, skipped")
            continue
        med_r = float(np.median(pos))
        med_b = float(np.median(neg))
        auc = roc_auc(pos, neg)
        y_thr, y_tpr, y_fpr = youden_threshold(pos, neg)
        a_thr = float(np.mean(neg) + 2.9)
        a_tpr, a_fpr = tpr_fpr_at(pos, neg, a_thr)
        report[name] = {
            "weights_vv_vh": list(INDICES[name]),
            "n_ref": int(pos.size), "n_bg": int(neg.size),
            "median_ref_db": round(med_r, 3),
            "median_bg_db": round(med_b, 3),
            "contrast_db": round(med_r - med_b, 3),
            "auc": round(auc, 4),
            "youden": {"thr": round(y_thr, 3), "tpr": round(y_tpr, 4),
                       "fpr": round(y_fpr, 4)},
            "adaptive_2.9": {"thr": round(a_thr, 3),
                             "tpr": round(a_tpr, 4),
                             "fpr": round(a_fpr, 4)},
        }
        print(f"  {name:10s} contrast={med_r - med_b:+.3f} dB  "
              f"AUC={auc:.4f}  youden thr={y_thr:+.2f} "
              f"(tpr {y_tpr:.3f}/fpr {y_fpr:.3f})  "
              f"adaptive thr={a_thr:+.2f} (tpr {a_tpr:.3f}/"
              f"fpr {a_fpr:.3f})")
    return {
        "event": event_key, "desc": cfg["desc"],
        "ref_px": ref_px, "bg_forest_px": bgf_px,
        "stride": int(stride),
        "background": "bg ring ∩ WC2020 forest" if forest is not None
                      else "bg ring",
        "indices": report,
    }


# ======================================================================
# Stage: detect (object-level, in-memory)
# ======================================================================
def detect_index(event_key, index, thr_mode):
    cfg = EVENTS[event_key]
    wv, wh = INDICES[index]
    print(f"[{event_key}/{index}/{thr_mode}] loading arrays...")
    d_vv = read_band(cfg["post_vv"]) - read_band(cfg["pre_vv"])
    d_vh = read_band(cfg["post_vh"]) - read_band(cfg["pre_vh"])
    idx = (wv * d_vv + wh * d_vh).astype(np.float32)
    del d_vv, d_vh

    ref = read_mask(cfg["ref"])
    bg = read_mask(cfg["bg"])
    forest = read_mask(cfg["forest"]) > 0 if cfg.get("forest") else None
    bgf = bg & forest if forest is not None else bg
    bgf &= ~ref

    # threshold
    valid = np.isfinite(idx)
    vals_bg = idx[bgf & valid]
    thr_adaptive = float(np.mean(vals_bg) + 2.9)
    # youden from subsampled arrays
    stride = max(1, int(vals_bg.size // SAMPLE_CAP))
    sub = vals_bg[::stride]
    ref_vals = idx[ref & valid]
    y_thr, _, _ = youden_threshold(ref_vals, sub)
    thr = thr_adaptive if thr_mode == "adaptive" else float(y_thr)

    # median filter 3x3 on NaN-safe copy
    z = idx.copy()
    z[~valid] = -999.0
    z = ndimage.median_filter(z, size=3)
    flagged = (z > thr) & valid
    if forest is not None:
        flagged &= forest
    del z

    objs = filter_small_objects(flagged, 27)
    met = object_metrics(objs, ref)
    area = float(objs.sum() * 0.01)   # 10 m pixels -> 0.01 ha/px
    res = {
        "event": event_key, "index": index,
        "weights_vv_vh": list(INDICES[index]),
        "thr_mode": thr_mode, "thr_db": round(thr, 3),
        "thr_adaptive_db": round(thr_adaptive, 3),
        "thr_youden_db": round(float(y_thr), 3),
        "detected_area_ha": round(area, 1),
        **met,
    }
    print(f"  -> PA={met['pa']:.3f} UA={met['ua']:.3f} "
          f"F1={met['f1']:.3f} area={area:.0f} ha "
          f"({met['n_det_objects']} obj, thr {thr:.2f} dB)")
    return res


def object_metrics(det_mask, ref_mask):
    """Object-based PA/UA (overlap >= 30%), 8-connected (step7 def)."""
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
    tp_ref = int(np.count_nonzero(det_counts[1:] >= 0.3 * ref_sizes[1:]))
    ref_counts = np.bincount(det_lab[ref_mask > 0].ravel(),
                             minlength=n_det + 1)
    tp_det = int(np.count_nonzero(ref_counts[1:] >= 0.3 * det_sizes[1:]))
    pa = tp_ref / n_ref
    ua = tp_det / n_det
    f1 = (2 * pa * ua / (pa + ua)) if (pa + ua) > 0 else 0.0
    return {"n_ref_components": int(n_ref), "n_det_objects": int(n_det),
            "tp_ref": tp_ref, "tp_det": tp_det,
            "pa": round(pa, 4), "ua": round(ua, 4), "f1": round(f1, 4)}


# ======================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["study", "detect"])
    ap.add_argument("--event", default="all",
                    help="study: 666_stack|666_pair|694_pair|all")
    ap.add_argument("--index", default="WI",
                    help="detect: index name (see INDICES)")
    ap.add_argument("--thr", default="adaptive",
                    choices=["adaptive", "youden"])
    args = ap.parse_args()
    out_path = (f"{DL}/windthrow_index_studio_step10_"
                f"{datetime.now():%Y-%m-%d}.json")

    if args.stage == "study":
        events = list(EVENTS) if args.event == "all" else [args.event]
        results = {}
        if os.path.isfile(out_path):
            with open(out_path) as f:
                results = json.load(f).get("events", {})
        for ev in events:
            results[ev] = study(ev)
            payload = {
                "purpose": "step10 index studio: pixel-level separation "
                           "of reference windthrow vs forest background "
                           "per candidate SAR observable",
                "indices": {k: list(v) for k, v in INDICES.items()},
                "metric_notes": {
                    "contrast_db": "median(ref) - median(bg forest)",
                    "auc": "Mann-Whitney ROC AUC",
                    "adaptive_2.9": "thr = mean(bg)+2.9 dB (paper regime)",
                    "youden": "oracle threshold, not operational",
                },
                "events": results,
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
            with open(out_path, "w") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"  -> {out_path}")
    else:
        res = detect_index(args.event, args.index, args.thr)
        det_path = (f"{DL}/windthrow_step10_detect_"
                    f"{args.event}_{args.index}_{args.thr}.json")
        with open(det_path, "w") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"  -> {det_path}")


if __name__ == "__main__":
    main()
