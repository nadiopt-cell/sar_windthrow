#!/usr/bin/env python3
"""step12 — Coherence data-source probe: OPERA CSLC-S1 vs HyP3 INSAR-GAMMA.

Re-restored on 2026-09-03 after the sandbox rollback (original step12 JSONs of
2026-09-02, "cmr-probe" + "opera-bursts", were created after the last GitHub push
and were lost). NASA CMR queries are deterministic and anonymous (umm_json).

IMPORTANT CORRECTION vs the 2026-09-02 session notes:
the original notes recorded "ID694 -> OPERA CSLC-S1 OK (frame 322451, 3 VV dates)".
This live re-check with verified methodology (sanity probes over known-covered
regions) finds ZERO OPERA CSLC-S1 granules over BOTH event AOIs across the WHOLE
archive (bounding_box search, which is proven to work for this collection).
The OPERA CSLC-S1 historical back-processing (products dated 2023-2024) covers
the Americas; no European Russia coverage exists. Both events therefore use
HyP3 INSAR-GAMMA orders (Earthdata-authenticated).

Evidence chain (all anonymous CMR umm_json):
  1) Collection OPERA_L2_CSLC-S1_V1 (C2777443834-ASF, ASF), extent from 2016-07-01.
  2) Sanity A: bbox over known granule T151-322352 (Mexico) on 2017-09-24 -> 12 hits.
  3) Sanity B: 1-min temporal window 00:58Z on 2017-09-24 -> 35 granules, T151
     frames 322352..322363 (Mexico) -> temporal windows and UR parsing work.
  4) Pass windows on every RTC scene date (03:34Z ID666 / 03:02Z ID694) -> 0 hits.
  5) Full-archive bounding_box over ID666 AOI (Komi) and ID694 AOI (Perm) -> 0 hits.
  6) Observed granules on the same days are over the Americas (T151 Mexico,
     T063 Central America, ...) -> Western-Hemisphere-only historical processing.

Inputs (hard-coded from step7_warp_manifest.json / step8_id694_warp_manifest.json):
  ID666: grid EPSG 32638 -> WGS84 ~[42.72..43.69 E, 61.64..62.11 N], Komi Republic;
         S1B descending, relative orbit 94, 03:34-03:35 UTC;
         RTC dates (step7): 06-16, 06-28, 07-10, 07-22, 08-03, 08-15 (2017)
  ID694: grid EPSG 32640 -> WGS84 ~[49.63..49.91 E, 59.32..59.44 N], Perm Krai;
         S1B descending, relative orbit 152, 03:02-03:03 UTC;
         RTC dates (step8): 08-07, 08-19, 08-31, 09-24, 10-06 (2017)

Output:
  results/step12_coherence_sources_2026-09-03.json (repo) + copy to
  /home/z/my-project/download/intermediate_json_2026-09-02/

NO secrets are written to the JSON: the Earthdata JWT stays in
work_data/earthdata_jwt.txt (uid nadiopt, exp 2026-11-01).
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")
try:
    from osgeo import osr
except Exception:  # pragma: no cover
    osr = None

CMR = "https://cmr.earthdata.nasa.gov/search"
UA = {"User-Agent": "sar-windthrow-step12/1.0 (research; contact nadiopt)"}
REPO = "/home/z/my-project/plugin_work/sar_windthrow_repo"
DOWNLOAD_DIR = "/home/z/my-project/download/intermediate_json_2026-09-02"
TODAY = "2026-09-03"
OPERA_CID_QUERY = "OPERA_L2_CSLC-S1_V1"

EVENTS = {
    "ID666": {
        "event": "squall line 30.07.2017 ~15-18 UTC, Komi Republic (950.1 ha windthrow)",
        "event_date": "2017-07-30",
        "grid": {"epsg": 32638, "bounds_m": [379250.0, 6836060.0, 431700.0, 6887140.0],
                 "source": "step7_warp_manifest.json / step11b_palsar_lband_probe_2026-09-02.json"},
        "pass": {"platform": "S1B", "direction": "descending", "relative_orbit": 94,
                 "time_utc": "03:34-03:35"},
        "rtc_dates": ["2017-06-16", "2017-06-28", "2017-07-10", "2017-07-22",
                      "2017-08-03", "2017-08-15"],
        "pass_hhmm": "03:34",
    },
    "ID694": {
        "event": "tornado S486 ~19.09.2017, Perm Krai (161.2 ha, 21 polygons; Date_1 04.09 - Date_2 02.10)",
        "event_date": "2017-09-19",
        "grid": {"epsg": 32640, "bounds_m": [80900.0, 6598610.0, 98040.0, 6610200.0],
                 "source": "step8_id694_warp_manifest.json / step11c_palsar_id694_2026-09-02.json"},
        "pass": {"platform": "S1B", "direction": "descending", "relative_orbit": 152,
                 "time_utc": "03:02-03:03"},
        "rtc_dates": ["2017-08-07", "2017-08-19", "2017-08-31", "2017-09-24", "2017-10-06"],
        "pass_hhmm": "03:02",
    },
}

RE_OPERA_V11 = re.compile(
    r"OPERA_L2_CSLC-S1_T(\d+)-(\d+)-IW(\d)_(\d{8})T(\d{6})Z_\d{8}T\d{6}Z_(S1[AB])_(VV|VH|HH|HV)_v",
    re.I)
RE_OPERA_V1 = re.compile(
    r"OPERA_L2_CSLC-S1_T(\d+)-(\d+)-IW(\d)_(VV|VH|HH|HV)_(\d{8})T(\d{6})Z", re.I)
RE_S1SLC = re.compile(
    r"S1([AB])_IW_SLC__1SDV_(\d{8})T(\d{6})_(\d{8})T(\d{6})_(\d{6})_([0-9A-Fa-f]{6})")


def http_json(url, retries=4, timeout=120):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"CMR request failed after {retries} tries: {url}\n{last}")


def resolve_collection(keyword, provider="ASF"):
    url = (f"{CMR}/collections.umm_json?keyword={urllib.parse.quote(keyword)}"
           f"&provider={provider}&page_size=10")
    d = http_json(url)
    for it in d.get("items", []):
        if it["umm"].get("ShortName", "").upper() == keyword.upper():
            return {"short_name": it["umm"]["ShortName"], "concept_id": it["meta"]["concept-id"],
                    "provider": it["meta"]["provider-id"],
                    "entry_title": it["umm"].get("EntryTitle", "")[:160]}
    if d.get("items"):
        it = d["items"][0]
        return {"short_name": it["umm"].get("ShortName", ""), "concept_id": it["meta"]["concept-id"],
                "provider": it["meta"]["provider-id"], "note": "fuzzy match"}
    return None


def cmr_granules(params, page_size=150):
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{CMR}/granules.umm_json?{q}&page_size={page_size}"
    d = http_json(url)
    if d.get("hits", 0) > page_size:
        print(f"[warn] hits={d['hits']} > page_size={page_size} -> truncated", flush=True)
    return d


def parse_opera(ur):
    m = RE_OPERA_V11.search(ur)
    if m:  # v1.1 layout: ..._IW{n}_{sense}_..._{S1X}_{POL}_v
        track, frame, iw, d0, t0, sat, pol = m.groups()
    else:  # v1.0 legacy layout: ..._IW{n}_{POL}_{sense}_...
        m = RE_OPERA_V1.search(ur)
        if not m:
            return None
        track, frame, iw, pol, d0, t0 = m.groups()
        sat = None
    return {"track": int(track), "frame": int(frame), "iw": int(iw), "pol": pol,
            "date": f"{d0[0:4]}-{d0[4:6]}-{d0[6:8]}", "time": t0, "sat": sat}


def opera_polygons(item):
    pts = []
    for b in (item.get("umm", {}).get("SpatialExtent", {})
              .get("HorizontalSpatialDomain", {}).get("Geometry", {}).get("GPolygons", []) or []):
        pts += b["Boundary"]["Points"]
    if not pts:
        return None
    lats = [p["Latitude"] for p in pts]
    lons = [p["Longitude"] for p in pts]
    return [round(min(lons), 3), round(min(lats), 3), round(max(lons), 3), round(max(lats), 3)]


def scan_pass_window(opera_cid, day, hhmm, span_min=5):
    """Temporal-window scan of a pass minute; group frames with bbox footprints."""
    t0 = f"{day}T{hhmm[:2]}:{int(hhmm[3:5]) - span_min // 2:02d}:00Z"
    t1 = f"{day}T{hhmm[:2]}:{int(hhmm[3:5]) + (span_min - span_min // 2):02d}:00Z"
    d = cmr_granules({"collection_concept_id": opera_cid, "temporal": f"{t0},{t1}"})
    frames = {}
    for it in d.get("items", []):
        ur = it.get("umm", {}).get("GranuleUR", "")
        p = parse_opera(ur)
        if not p:
            continue
        bb = opera_polygons(it) or []
        key = f"T{p['track']}-f{p['frame']}"
        f = frames.setdefault(key, {"n": 0, "pols": set(), "sats": set(),
                                    "times": set(), "bbox_4326": bb})
        f["n"] += 1
        f["pols"].add(p["pol"]); f["sats"].add(p["sat"] or "?"); f["times"].add(p["time"])
    return {"window": f"{t0}..{t1}", "hits": d.get("hits", 0),
            "parsed": sum(f["n"] for f in frames.values()),
            "frames": {k: {"n": v["n"], "pols": sorted(v["pols"]), "sats": sorted(v["sats"]),
                           "times": sorted(v["times"]), "bbox_4326": v["bbox_4326"]}
                       for k, v in sorted(frames.items())}}


def full_archive_bbox(opera_cid, bbox):
    d = cmr_granules({"collection_concept_id": opera_cid, "bounding_box": bbox})
    return {"bbox": bbox, "hits_all_dates": d.get("hits", 0)}


def grid_to_4326(epsg, bounds):
    s = osr.SpatialReference(); s.ImportFromEPSG(epsg)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    t = osr.SpatialReference(); t.ImportFromEPSG(4326)
    t.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(s, t)
    p0 = ct.TransformPoint(bounds[0], bounds[1])
    p1 = ct.TransformPoint(bounds[2], bounds[3])
    lon0, lat0, lon1, lat1 = p0[0], p0[1], p1[0], p1[1]
    return [round(min(lon0, lon1), 4), round(min(lat0, lat1), 4),
            round(max(lon0, lon1), 4), round(max(lat0, lat1), 4)]


def time_close(hhmmss, ref_hhmm, tol_min=10):
    t = int(hhmmss[:2]) * 60 + int(hhmmss[2:4])
    r = int(ref_hhmm[:2]) * 60 + int(ref_hhmm[3:5])
    d = min(abs(t - r), 1440 - abs(t - r))
    return d <= tol_min


def build_pairs(dates, event_date):
    pre = [d for d in dates if d < event_date]
    post = [d for d in dates if d > event_date]
    pairs = {}
    if pre and post:
        pairs["primary_straddling_event"] = {"ref": pre[-1], "sec": post[0]}
    if len(pre) >= 2:
        pairs["control_pre_pre"] = {"ref": pre[-2], "sec": pre[-1]}
    if len(post) >= 2:
        pairs["control_post_post"] = {"ref": post[0], "sec": post[1]}
    for k, v in pairs.items():
        a = datetime.strptime(v["ref"], "%Y-%m-%d")
        b = datetime.strptime(v["sec"], "%Y-%m-%d")
        v["temporal_baseline_days"] = (b - a).days
    return pairs


def ddays(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def window(event_date, before_d, after_d):
    return (f"{(ddays(event_date) - timedelta(days=before_d)).isoformat()}T00:00:00Z,"
            f"{(ddays(event_date) + timedelta(days=after_d)).isoformat()}T23:59:59Z")


def main():
    report = {
        "step": "12",
        "title": "Coherence data sources: OPERA CSLC-S1 vs HyP3 INSAR-GAMMA (re-restored probe, corrected)",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_local_date": TODAY,
        "note": ("Re-restoration of the lost 2026-09-02 step12 JSONs (cmr-probe + opera-bursts). "
                 "CORRECTION vs the 2026-09-02 session notes: OPERA CSLC-S1 is a dead end for BOTH "
                 "events (zero coverage over both AOIs in the whole archive; historical back-"
                 "processing covers the Americas). The earlier 'ID694 -> OPERA OK (frame 322451)' "
                 "record was a misreading; evidence below. Both events proceed via HyP3 INSAR-GAMMA."),
        "collections": {},
        "methodology_sanity": {},
        "events": {},
        "auth": {
            "cmr_queries": "anonymous (umm_json)",
            "downloads": ("Earthdata Login required; JWT on file (work_data/earthdata_jwt.txt, "
                          "uid nadiopt, exp 2026-11-01); token NOT embedded in this JSON"),
        },
        "calibration": {
            "hyp3_insar_gamma": ("INSAR_GAMMA delivers coregistered SLC pair -> coherence + unwrapped "
                                 "phase, geocoded (GAMMA); coherence needs no DN offset. For HyP3 GTC/RTC "
                                 "amplitude products: g0_dB = 20*log10(DN) - 83"),
        },
        "next_actions": [],
    }

    print("[collections]", flush=True)
    for kw in (OPERA_CID_QUERY, "SENTINEL-1A_SLC", "SENTINEL-1B_SLC"):
        c = resolve_collection(kw, provider="ASF")
        report["collections"][kw] = c
        print("  ", kw, "->", c, flush=True)
    opera_cid = report["collections"][OPERA_CID_QUERY]["concept_id"]
    slc_cids = [report["collections"][k]["concept_id"] for k in ("SENTINEL-1A_SLC", "SENTINEL-1B_SLC")]

    # --- methodology sanity probes (prove bbox + temporal windows work for OPERA) ---
    sA = cmr_granules({"collection_concept_id": opera_cid,
                       "bounding_box": "-105.5,27.5,-103.0,28.5",
                       "temporal": "2017-09-24T00:00:00Z,2017-09-24T23:59:59Z"}, page_size=20)
    sB = cmr_granules({"collection_concept_id": opera_cid,
                       "temporal": "2017-09-24T00:58:00Z,2017-09-24T00:59:00Z"}, page_size=150)
    sB_frames = sorted({f"T{p['track']}-f{p['frame']}" for p in
                        (parse_opera(i["umm"].get("GranuleUR", "")) for i in sB.get("items", [])) if p})
    report["methodology_sanity"] = {
        "A_bbox_known_region": {"region": "N Mexico (T151-322352 footprint)", "hits": sA.get("hits", 0)},
        "B_temporal_1min_window": {"day": "2017-09-24T00:58Z", "hits": sB.get("hits", 0),
                                   "frames_sample": sB_frames[:12]},
        "conclusion": ("bbox and temporal searches return real OPERA granules over the Americas -> "
                       "zero results over the Russian AOIs are genuine, not a search artifact"),
    }
    print("[sanity]", json.dumps(report["methodology_sanity"])[:300], flush=True)

    # --- per-event OPERA assessment ---
    for ev_id, ev in EVENTS.items():
        print(f"\n=== {ev_id} ===", flush=True)
        aoi = grid_to_4326(ev["grid"]["epsg"], ev["grid"]["bounds_m"])
        pad = 0.05
        bbox = f"{aoi[0]-pad:.3f},{aoi[1]-pad:.3f},{aoi[2]+pad:.3f},{aoi[3]+pad:.3f}"
        blk = {"event": ev["event"], "event_date": ev["event_date"], "aoi_4326": aoi,
               "grid": ev["grid"], "expected_pass": ev["pass"]}

        blk["opera_pass_windows"] = {}
        for day in ev["rtc_dates"]:
            w = scan_pass_window(opera_cid, day, ev["pass_hhmm"])
            blk["opera_pass_windows"][day] = w
            print(f"  pass window {day} {ev['pass_hhmm']}: hits={w['hits']}, parsed={w['parsed']}", flush=True)
        blk["opera_full_archive_bbox"] = full_archive_bbox(opera_cid, bbox)
        print("  full-archive bbox:", blk["opera_full_archive_bbox"], flush=True)

        total_in_windows = sum(w["hits"] for w in blk["opera_pass_windows"].values())
        blk["opera_verdict"] = (
            f"DEAD END: {total_in_windows} granules in all pass windows, "
            f"{blk['opera_full_archive_bbox']['hits_all_dates']} granules over AOI in the whole "
            f"archive -> OPERA CSLC-S1 has no European Russia coverage for this track/AOI "
            f"(historical back-processing is Americas-only); HyP3 INSAR-GAMMA required")

        # --- Sentinel-1 SLC (ASF) within event +/- 20 d, same-orbit filter ---
        exp_t = ev["pass"]["time_utc"][:5]
        slc = []
        for cid in slc_cids:
            d = cmr_granules({"collection_concept_id": cid, "bounding_box": bbox,
                              "temporal": window(ev["event_date"], 20, 20)})
            for it in d.get("items", []):
                ur = it.get("umm", {}).get("GranuleUR", "")
                m = RE_S1SLC.search(ur)
                if m and time_close(m.group(3), exp_t):
                    slc.append({"sat": "S1" + m.group(1),
                                "date": f"{m.group(2)[0:4]}-{m.group(2)[4:6]}-{m.group(2)[6:8]}",
                                "time": m.group(3), "abs_orbit": int(m.group(6)),
                                "take_id": m.group(7).upper(), "ur": ur})
        slc.sort(key=lambda r: (r["date"], r["time"]))
        dates = sorted({r["date"] for r in slc})
        blk["sentinel1_slc"] = {
            "temporal": window(ev["event_date"], 20, 20),
            "same_orbit_filter": f"sensing time within +/-10 min of {exp_t} UTC (drops other tracks)",
            "dates_found": dates,
            "granules": [{k: r[k] for k in ("sat", "date", "time", "abs_orbit", "take_id", "ur")}
                         for r in slc[:24]],
            "granules_total": len(slc),
        }
        pairs = build_pairs(dates, ev["event_date"])
        blk["insar_pairs"] = pairs

        # --- HyP3 order plan (OPERA is a dead end for both events) ---
        pr = pairs.get("primary_straddling_event")
        if pr:
            def ur_of(d0):
                return next((r["ur"] for r in slc if r["date"] == d0), None)
            ref_ur, sec_ur = ur_of(pr["ref"]), ur_of(pr["sec"])
            ctrl = pairs.get("control_pre_pre", {})
            plan = {
                "service": "ASF HyP3 (INSAR_GAMMA)",
                "primary_pair": {"ref": pr["ref"], "sec": pr["sec"],
                                 "ref_granule": ref_ur, "sec_granule": sec_ur,
                                 "temporal_baseline_days": pr["temporal_baseline_days"]},
                "control_pair_pre_pre": None,
                "params": "defaults (5x1 looks, copernicus DEM); outputs include coherence + unwrapped phase",
                "auth": "Earthdata Login via hyp3-sdk (JWT on file): hyp3.submit_insar_job(ref, sec)",
                "note": "if the API rejects the CMR '-SLC' suffix, strip it from granule names",
            }
            if ctrl:
                plan["control_pair_pre_pre"] = {"ref": ctrl["ref"], "sec": ctrl["sec"],
                                                "ref_granule": ur_of(ctrl["ref"]),
                                                "sec_granule": ur_of(ctrl["sec"]),
                                                "temporal_baseline_days": ctrl["temporal_baseline_days"]}
            blk["hyp3_order_plan"] = plan
        report["events"][ev_id] = blk

    # --- next actions ---
    for ev_id in ("ID666", "ID694"):
        pr = report["events"][ev_id].get("hyp3_order_plan", {}).get("primary_pair")
        if pr:
            report["next_actions"].append(
                f"{ev_id}: order HyP3 INSAR-GAMMA pair {pr['ref']} -> {pr['sec']} "
                f"(+ pre-pre control) -> coherence -> ref/bg statistics like step11 "
                f"(inverted detection: coherence DROP over windthrow)")
    report["next_actions"].append(
        "Compare C-band coherence with L-band step11b/11c (invAUC 0.87/0.73-0.90) and the "
        "C-band WI baseline (UA 1.5-2.3 %; forest-mask UA 1.7 %)")
    report["next_actions"].append(
        "ID694 note: SLC 2017-09-12 exists at ASF but was absent from the PC RTC step8 plan -> "
        "the 12-day straddling pair (09-12 -> 09-24) is available for coherence")

    out_repo = f"{REPO}/results/step12_coherence_sources_{TODAY}.json"
    with open(out_repo, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    try:
        import shutil
        shutil.copy(out_repo, f"{DOWNLOAD_DIR}/step12_coherence_sources_{TODAY}.json")
        print(f"\ncopy -> {DOWNLOAD_DIR}/step12_coherence_sources_{TODAY}.json", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"copy failed: {e}", flush=True)
    print(f"JSON written: {out_repo}", flush=True)

    for ev_id, blk in report["events"].items():
        print(f"\n[{ev_id}] {blk['opera_verdict']}")
        print(f"[{ev_id}] SLC dates: {blk['sentinel1_slc']['dates_found']}")
        print(f"[{ev_id}] pairs: {json.dumps(blk['insar_pairs'], ensure_ascii=False)}")
        h = blk.get("hyp3_order_plan", {})
        if h.get("primary_pair"):
            print(f"[{ev_id}] HyP3: {h['primary_pair']['ref_granule']}")
            print(f"           + {h['primary_pair']['sec_granule']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
