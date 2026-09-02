# -*- coding: utf-8 -*-
"""Извлечение деталей методики Rüetschi 2019 из полнотекстового HTML (page_reader JSON)."""
import json, re, html as ihtml

SRC = "/home/z/my-project/tool-results/read_1788117400954_0e0df60d89a4.txt"

raw = open(SRC, "r", encoding="utf-8").read()
raw = re.sub(r"(?m)^\s*\d+[→\t]", "", raw)
dec = json.JSONDecoder()
data, _ = dec.raw_decode(raw.lstrip())
htm = data["data"]["html"]

# Грубая очистка HTML -> текст
txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", htm)
txt = re.sub(r"<[^>]+>", " ", txt)
txt = ihtml.unescape(txt)
txt = re.sub(r"\s+", " ", txt)
print("Длина текста:", len(txt))

out = open("/home/z/my-project/research/ruetschi2019_fulltext.txt", "w", encoding="utf-8")
out.write(txt)
out.close()

KEYS = ["WI", "threshold", "composite", "forest mask", "sieve", "orbit file",
        "thermal noise", "calibration", "terrain correct", "speckle", "sigma",
        "days before", "days after", "polarisation", "polarization",
        "incidence", "morpholog", "minimum extent", "0.5 ha", "paramet",
        "normali", "geometr", "resampl", "kernel", "dB"]

seen = set()
frags = []
for key in KEYS:
    for m in re.finditer(re.escape(key), txt, re.IGNORECASE):
        s = max(0, m.start() - 260)
        e = min(len(txt), m.end() + 300)
        frag = txt[s:e].strip()
        k = frag[100:180]
        if k in seen:
            continue
        seen.add(k)
        frags.append((key, frag))

# Группируем по ключу, ограничиваем объём
from collections import defaultdict
byk = defaultdict(list)
for key, frag in frags:
    byk[key].append(frag)

for key in KEYS:
    lst = byk.get(key, [])
    print("\n" + "=" * 90)
    print("### " + key + f"  ({len(lst)} фрагм.)")
    for frag in lst[:6]:
        print("  >>", frag[:520])
