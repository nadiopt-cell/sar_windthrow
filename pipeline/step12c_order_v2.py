#!/usr/bin/env python3
"""step12c fix: re-order 4 INSAR jobs for events whose first-order frames
did not cover the AOI:
  - ID674: S1A rel108 had nodata at the tornado track -> S1B rel62 (full cover)
  - ID608: wrong (southern) frame ordered -> northern T034515 chain (52/52)
"""
import json
import sys
import urllib.request
from pathlib import Path

API = "https://hyp3-api.asf.alaska.edu"
TOKEN = Path("/home/z/my-project/upload/токен.txt").read_text().strip()
REPO = "/home/z/my-project/plugin_work/sar_windthrow_repo"

PARAMS = {
    "looks": "20x4", "include_inc_map": False, "phase_filter_parameter": 0.6,
    "include_wrapped_phase": False, "include_los_displacement": False,
    "include_displacement_maps": False, "apply_water_mask": False,
    "include_look_vectors": False, "include_dem": False,
}

G = {
    # ID674 S1B rel62 (full cluster coverage, footprints 100 %)
    "674_g1": "S1B_IW_SLC__1SDV_20170730T040914_20170730T040939_006712_00BCE3_43C3",
    "674_g2": "S1B_IW_SLC__1SDV_20170811T040915_20170811T040940_006887_00C1F8_9BD8",
    "674_g3": "S1B_IW_SLC__1SDV_20170823T040915_20170823T040940_007062_00C70A_7590",
    # ID608 S1A rel64 NORTHERN frames (T034515/16/17)
    "608_g1": "S1A_IW_SLC__1SDV_20160702T034515_20160702T034545_011964_01272B_D9E3",
    "608_g2": "S1A_IW_SLC__1SDV_20160714T034516_20160714T034546_012139_012CF1_B741",
    "608_g3": "S1A_IW_SLC__1SDV_20160726T034517_20160726T034546_012314_013299_0DEB",
}

JOBS = [
    ("id674-coh-prepost-v2", "674_g1", "674_g2"),
    ("id674-coh-control-v2", "674_g2", "674_g3"),
    ("id608-coh-prepost-v2", "608_g1", "608_g2"),
    ("id608-coh-control-v2", "608_g2", "608_g3"),
]


def main():
    dry = "--go" not in sys.argv
    jobs = [{"job_type": "INSAR_GAMMA", "name": name,
             "job_parameters": {**PARAMS, "granules": [G[k1], G[k2]]}}
            for name, k1, k2 in JOBS]
    req = urllib.request.Request(f"{API}/user",
                                 headers={"Authorization": f"Bearer {TOKEN}"})
    u = json.load(urllib.request.urlopen(req, timeout=30))
    print("credits:", u["remaining_credits"], "| jobs:", len(jobs))
    if dry:
        for j in jobs:
            print("  [dry]", j["name"])
        return
    req = urllib.request.Request(
        f"{API}/jobs", method="POST", data=json.dumps({"jobs": jobs}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=60))
    for j in resp.get("jobs", resp):
        print("  ", j.get("name"), "->", j.get("job_id"), j.get("status_code"))
    p = Path(f"{REPO}/work_data/step12c_order_response_v2.json")
    p.write_text(json.dumps(resp, ensure_ascii=False, indent=1))
    print("saved", p)


if __name__ == "__main__":
    main()
