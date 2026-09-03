#!/usr/bin/env python3
"""step12c: order HyP3 INSAR-GAMMA jobs.
  - ID666 до-до контроль (28.06 -> 10.07.2017, fully pre-storm)
  - DiD extension: 5 new events x (prepost + control)
Job parameters replicate the 4 existing SUCCEEDED jobs (looks 20x4, no extras).
"""
import json
import sys
import urllib.request
from pathlib import Path

API = "https://hyp3-api.asf.alaska.edu"
TOKEN = Path("/home/z/my-project/upload/токен.txt").read_text().strip()
REPO = "/home/z/my-project/plugin_work/sar_windthrow_repo"

PARAMS = {
    "looks": "20x4",
    "include_inc_map": False,
    "phase_filter_parameter": 0.6,
    "include_wrapped_phase": False,
    "include_los_displacement": False,
    "include_displacement_maps": False,
    "apply_water_mask": False,
    "include_look_vectors": False,
    "include_dem": False,
}

G = {
    "dodo_g1": "S1B_IW_SLC__1SDV_20170628T033446_20170628T033513_006245_00AFA4_E68E",
    "dodo_g2": "S1B_IW_SLC__1SDV_20170710T033446_20170710T033513_006420_00B493_D588",
    # ID655 (squall 30.07.2017, S1B rel149)
    "655_g1": "S1B_IW_SLC__1SDV_20170724T031922_20170724T031949_006624_00BA63_0EBE",
    "655_g2": "S1B_IW_SLC__1SDV_20170805T031922_20170805T031949_006799_00BF69_7393",
    "655_g3": "S1B_IW_SLC__1SDV_20170817T031923_20170817T031950_006974_00C483_8E1B",
    # ID583 (squall 26.07.2015, S1A rel6)
    "583_g1": "S1A_IW_SLC__1SDV_20150716T041723_20150716T041758_006831_009338_C5AF",
    "583_g2": "S1A_IW_SLC__1SDV_20150728T041724_20150728T041758_007006_009835_C1CF",
    "583_g3": "S1A_IW_SLC__1SDV_20150809T041725_20150809T041759_007181_009CED_A634",
    # ID674 (tornado 02.08.2017, S1A rel108)
    "674_g1": "S1A_IW_SLC__1SDV_20170724T041008_20170724T041037_017608_01D766_3758",
    "674_g2": "S1A_IW_SLC__1SDV_20170805T041008_20170805T041038_017783_01DCC1_56AA",
    "674_g3": "S1A_IW_SLC__1SDV_20170817T041009_20170817T041039_017958_01E20D_E432",
    # ID608 (tornado 13.07.2016, S1A rel64)
    "608_g1": "S1A_IW_SLC__1SDV_20160702T034543_20160702T034611_011964_01272B_77AD",
    "608_g2": "S1A_IW_SLC__1SDV_20160714T034543_20160714T034611_012139_012CF1_DD1E",
    "608_g3": "S1A_IW_SLC__1SDV_20160726T034544_20160726T034612_012314_013299_3244",
    # ID646 (squall 29.05.2017, S1B rel120)
    "646_g1": "S1B_IW_SLC__1SDV_20170523T033624_20170523T033649_005720_00A057_B2FD",
    "646_g2": "S1B_IW_SLC__1SDV_20170604T033625_20170604T033650_005895_00A56A_C193",
    "646_g3": "S1B_IW_SLC__1SDV_20170616T033625_20170616T033651_006070_00AA91_4175",
}

JOBS = [
    ("id666-coh-dodo",     "dodo_g1", "dodo_g2"),
    ("id655-coh-prepost",  "655_g1", "655_g2"),
    ("id655-coh-control",  "655_g2", "655_g3"),
    ("id583-coh-prepost",  "583_g1", "583_g2"),
    ("id583-coh-control",  "583_g2", "583_g3"),
    ("id674-coh-prepost",  "674_g1", "674_g2"),
    ("id674-coh-control",  "674_g2", "674_g3"),
    ("id608-coh-prepost",  "608_g1", "608_g2"),
    ("id608-coh-control",  "608_g2", "608_g3"),
    ("id646-coh-prepost",  "646_g1", "646_g2"),
    ("id646-coh-control",  "646_g2", "646_g3"),
]


def build_jobs():
    out = []
    for name, k1, k2 in JOBS:
        p = dict(PARAMS)
        p["granules"] = [G[k1], G[k2]]
        out.append({"job_type": "INSAR_GAMMA", "name": name, "job_parameters": p})
    return out


def user_info():
    req = urllib.request.Request(f"{API}/user", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def submit(payload):
    req = urllib.request.Request(
        f"{API}/jobs", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    dry = "--go" not in sys.argv
    payload = {"jobs": build_jobs()}
    print(f"{len(payload['jobs'])} jobs, ~{len(payload['jobs']) * 10} credits")
    u = user_info()
    print("user:", u.get("user_id"), "credits:", u.get("remaining_credits"))
    if dry:
        for j in payload["jobs"]:
            print(f"  [dry] {j['name']}: {j['job_parameters']['granules'][0][:31]} + "
                  f"{j['job_parameters']['granules'][1][:31]}")
        print("DRY RUN — add --go to submit")
        return
    resp = submit(payload)
    jobs = resp.get("jobs", resp) if isinstance(resp, dict) else resp
    print("submitted:")
    for j in jobs:
        print("  ", j.get("name"), "->", j.get("job_id"), j.get("status_code"))
    Path(f"{REPO}/work_data/step12c_order_response.json").write_text(
        json.dumps(resp, ensure_ascii=False, indent=1))
    print("saved work_data/step12c_order_response.json")


if __name__ == "__main__":
    main()
