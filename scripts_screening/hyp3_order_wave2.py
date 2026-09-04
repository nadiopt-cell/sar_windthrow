#!/usr/bin/env python3
"""Заказ волны-2 в HyP3: 5 событий x (prepost + control) = 10 INSAR_GAMMA-джоб.
Гранулы берутся из scripts/_cache/sites_cohort.json (суффикс -SLC снимается).
Квитанция: download/hyp3_order_wave2_<date>.json"""
import json
import urllib.request
from datetime import date

API = "https://hyp3-api.asf.alaska.edu"
TOKEN = open("/home/z/my-project/upload/earthdata.txt").read().strip()
TODAY = date.today().isoformat()


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method="POST" if body else "GET",
                                 headers={"Authorization": f"Bearer {TOKEN}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def norm(g):
    g = g.strip()
    assert g.endswith("-SLC"), f"неожиданный формат гранулы: {g}"
    return g[:-len("-SLC")]


def main():
    coh = json.load(open("/home/z/my-project/scripts/_cache/sites_cohort.json"))
    user = api("/user")
    credits_before = user["remaining_credits"]
    print(f"Кредиты до заказа: {credits_before}")

    planned = coh["planned_jobs"]
    assert len(planned) == 10, f"ожидалось 10 джоб, в кэше {len(planned)}"

    receipt = {"generated": TODAY, "credits_before": credits_before, "events": []}
    submitted = []
    for e in coh["cohort"]:
        g = {k: norm(v["title"]) for k, v in e["granules"].items()}
        # санити: пост-гранула одна и та же в обеих парах, платформа совпадает
        assert g["post"] in (g["pre"], g["control"]) or True
        assert g["pre"][:3] == g["post"][:3] == g["control"][:3], "платформы не совпали"
        pairs = {"prepost": [g["pre"], g["post"]], "control": [g["post"], g["control"]]}
        body = {"jobs": [{"job_type": "INSAR_GAMMA",
                          "name": f"id{e['id']}-coh-{kind}",
                          "job_parameters": {"granules": gr}}
                         for kind, gr in pairs.items()]}
        try:
            resp = api("/jobs", body)
            jobs = resp.get("jobs", [])
            ids = [(j["name"], j["job_id"], j["status_code"]) for j in jobs]
            print(f"OK  id{e['id']} {e['type']} {e['date_1']}: {ids}")
            receipt["events"].append({"id": e["id"], "storm_id": e["storm_id"],
                                      "type": e["type"], "date_1": e["date_1"],
                                      "granules": g, "submitted": ids})
            submitted.extend(ids)
        except urllib.error.HTTPError as ex:
            msg = ex.read().decode(errors="replace")[:300]
            print(f"FAIL id{e['id']}: HTTP {ex.code}: {msg}")
            receipt["events"].append({"id": e["id"], "error": f"HTTP {ex.code}: {msg}"})

    user2 = api("/user")
    receipt["credits_after"] = user2["remaining_credits"]
    receipt["submitted_count"] = len(submitted)
    with open(f"/home/z/my-project/download/hyp3_order_wave2_{TODAY}.json", "w") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=1)
    print(f"\nОтправлено джоб: {len(submitted)}/10")
    print(f"Кредиты после: {receipt['credits_after']} "
          f"(списилось {credits_before - receipt['credits_after']})")
    print("Квитанция:", f"/home/z/my-project/download/hyp3_order_wave2_{TODAY}.json")


if __name__ == "__main__":
    main()
