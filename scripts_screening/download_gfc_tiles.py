#!/usr/bin/env python3
"""Скачивание тайлов Hansen GFC v1.12 (treecover2000 + lossyear) для проекта.
Запуск: python3 download_gfc_tiles.py [tile ...]   (по умолчанию 4 тайла проекта)
"""
import subprocess
import sys
from pathlib import Path

BASE = "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12"
OUT = Path("/home/z/my-project/work_data/gfc_tiles")
OUT.mkdir(parents=True, exist_ok=True)
TILES = sys.argv[1:] or ["50N_030E", "50N_040E", "60N_030E", "60N_040E"]

for t in TILES:
    for layer in ("lossyear", "treecover2000"):
        name = f"Hansen_GFC-2024-v1.12_{layer}_{t}.tif"
        dst = OUT / name
        if dst.exists() and dst.stat().st_size > 1_000_000:
            print(f"[skip] {name} ({dst.stat().st_size/1e6:.0f} MB)")
            continue
        url = f"{BASE}/{name}"
        print(f"[get ] {name} ...", flush=True)
        r = subprocess.run(["curl", "-sS", "-C", "-", "-o", str(dst), url])
        ok = dst.exists() and dst.stat().st_size > 1_000_000
        print(f"[{'ok ' if ok and r.returncode == 0 else 'FAIL'}] {name} "
              f"({(dst.stat().st_size/1e6 if dst.exists() else 0):.0f} MB)")
print("done")
