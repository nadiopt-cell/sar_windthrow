#!/usr/bin/env python3
"""Скачивание 10 продуктов HyP3 волны-2 (id579/654/658/683/696 x prepost+control).
Фильтр по имени джобы, повторные попытки, CRC-проверка zipfile."""
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

MANIFEST = Path('/home/z/my-project/download/hyp3_download_links_2026-09-04.json')
OUTDIR = Path('/home/z/my-project/download/hyp3_products_wave2')
WAVE2_PREFIXES = ('id579-', 'id654-', 'id658-', 'id683-', 'id696-')

OUTDIR.mkdir(parents=True, exist_ok=True)
files = json.loads(MANIFEST.read_text())['files']
w2 = [f for f in files if (f['job_name'] or '').startswith(WAVE2_PREFIXES)]
print(f"волна-2 файлов: {len(w2)}, суммарно {sum(f['size_mb'] for f in w2):.0f} MB")

ok, fail = [], []
for f in w2:
    fn = f['filename']
    dest = OUTDIR / fn
    if dest.exists() and dest.stat().st_size > 10_000_000:
        try:
            if zipfile.ZipFile(dest).testzip() is None:
                print(f"[SKIP CRC OK] {fn}")
                ok.append(fn)
                continue
        except zipfile.BadZipFile:
            print(f"[CRC BAD, перекачиваю] {fn}")
            dest.unlink()
    url = f['url']
    for attempt in range(3):
        try:
            t0 = time.time()
            tmp = dest.with_suffix('.part')
            # curl с докачкой (-C -) и ретраями; таймаут на зависание 120с
            r = subprocess.run(
                ['curl', '-sS', '-C', '-', '--retry', '2', '--speed-limit', '10240',
                 '--speed-time', '60', '-o', str(tmp), url],
                capture_output=True, text=True, timeout=540)
            if r.returncode != 0:
                raise IOError(f"curl rc={r.returncode}: {r.stderr.strip()[:120]}")
            tmp.rename(dest)
            dt = time.time() - t0
            with zipfile.ZipFile(dest) as z:
                bad = z.testzip()
            if bad:
                raise IOError(f"CRC fail: {bad}")
            print(f"[OK {dt:5.0f}s] {fn} ({dest.stat().st_size/1e6:.0f} MB, CRC OK)", flush=True)
            ok.append(fn)
            break
        except Exception as e:
            print(f"[попытка {attempt+1} ошибка] {fn}: {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    else:
        fail.append(fn)

print(f"\nитог: OK={len(ok)}, FAIL={len(fail)}")
if fail:
    print("провальные:", *fail, sep='\n  ')
    sys.exit(1)
