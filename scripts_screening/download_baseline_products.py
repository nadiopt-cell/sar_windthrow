#!/usr/bin/env python3
"""Скачивание 24 HyP3-продуктов baseline (12 событий x prepost+control)
из hyp3/hyp3_download_links_2026-09-04.txt в download/hyp3_products_all/.
Докачка (curl -C -), после каждого файла — проверка zip-целостности.
Запуск: python3 download_baseline_products.py [N_files_per_call]
"""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path("/home/z/my-project/sar_windthrow")
LINKS = REPO / "hyp3" / "hyp3_download_links_2026-09-04.txt"
OUT = Path("/home/z/my-project/download/hyp3_products_all")
OUT.mkdir(parents=True, exist_ok=True)

# базовые продукты (совпадают с step12b/12c + wave2 EVENT_ZIPS)
NEEDED = {
    "id666": ("S1BB_20170722T033447_20170803T033448_VVP012_INT80_G_ueF_C4A3.zip",
              "S1BB_20170628T033446_20170710T033446_VVP012_INT80_G_ueF_4CD2.zip"),
    "id694": ("S1BB_20170912T030234_20170924T030234_VVP012_INT80_G_ueF_5748.zip",
              "S1BB_20170924T030234_20171006T030235_VVP012_INT80_G_ueF_5C8E.zip"),
    "id655": ("S1BB_20170724T031922_20170805T031922_VVP012_INT80_G_ueF_5AA7.zip",
              "S1BB_20170805T031922_20170817T031923_VVP012_INT80_G_ueF_5C98.zip"),
    "id583": ("S1AA_20150716T041723_20150728T041724_VVP012_INT80_G_ueF_6635.zip",
              "S1AA_20150728T041724_20150809T041725_VVP012_INT80_G_ueF_7C23.zip"),
    "id674": ("S1BB_20170730T040914_20170811T040915_VVP012_INT80_G_ueF_0473.zip",
              "S1BB_20170811T040915_20170823T040915_VVP012_INT80_G_ueF_AB7F.zip"),
    "id608": ("S1AA_20160702T034515_20160714T034516_VVP012_INT80_G_ueF_DC49.zip",
              "S1AA_20160714T034516_20160726T034517_VVP012_INT80_G_ueF_8850.zip"),
    "id646": ("S1BB_20170523T033624_20170604T033625_VVP012_INT80_G_ueF_105D.zip",
              "S1BB_20170604T033625_20170616T033625_VVP012_INT80_G_ueF_B8C4.zip"),
    "id579": ("S1AA_20150622T041605_20150704T041604_VVP012_INT80_G_ueF_07A7.zip",
              "S1AA_20150704T041604_20150716T041605_VVP012_INT80_G_ueF_8A5D.zip"),
    "id654": ("S1BB_20170712T031830_20170724T031831_VVP012_INT80_G_ueF_3A90.zip",
              "S1BB_20170724T031831_20170805T031832_VVP012_INT80_G_ueF_4FDF.zip"),
    "id658": ("S1BB_20170722T033447_20170803T033448_VVP012_INT80_G_ueF_4BC7.zip",
              "S1BB_20170803T033448_20170815T033448_VVP012_INT80_G_ueF_082D.zip"),
    "id683": ("S1BB_20170730T040849_20170811T040850_VVP012_INT80_G_ueF_E9F3.zip",
              "S1BB_20170811T040850_20170823T040850_VVP012_INT80_G_ueF_168D.zip"),
    "id696": ("S1AA_20170712T041007_20170724T041008_VVP012_INT80_G_ueF_083E.zip",
              "S1AA_20170724T041008_20170805T041008_VVP012_INT80_G_ueF_DB8F.zip"),
}
WANT = {z for pair in NEEDED.values() for z in pair}

# парсинг links -> {zip_name: url}
urls = {}
job = None
for line in LINKS.read_text().splitlines():
    m = re.match(r"^--- (\S+)", line.strip())
    if m:
        job = m.group(1)
        continue
    m = re.match(r"^\s+(S1[AB][AB]_\S+\.zip)\s+\(", line)
    if m:
        cur_zip = m.group(1)
        continue
    m = re.match(r"^\s+(https://\S+)\s*$", line)
    if m and job and cur_zip:
        urls[cur_zip] = m.group(1)

missing = WANT - set(urls)
if missing:
    print("НЕТ ССЫЛОК:", sorted(missing)); sys.exit(1)

batch = int(sys.argv[1]) if len(sys.argv) > 1 else 99
done = 0
for z in sorted(WANT):
    dst = OUT / z
    if dst.exists() and dst.stat().st_size > 1_000_000:
        try:
            with zipfile.ZipFile(dst) as zf:
                bad = zf.testzip()
            if bad is None:
                print(f"[ok] {z} (уже скачан, CRC ok)"); continue
            print(f"[перезапуск] {z}: повреждён ({bad})"); dst.unlink()
        except zipfile.BadZipFile:
            print(f"[перезапуск] {z}: BadZipFile"); dst.unlink()
    url = urls[z]
    r = subprocess.run(["curl", "-sS", "-C", "-", "-o", str(dst), url])
    if r.returncode != 0:
        print(f"[FAIL curl] {z} rc={r.returncode}"); continue
    try:
        with zipfile.ZipFile(dst) as zf:
            bad = zf.testzip()
        print(f"[ok] {z} ({dst.stat().st_size/1e6:.1f} MB, CRC {'ok' if bad is None else 'FAIL:' + str(bad)})")
        done += 1
    except zipfile.BadZipFile:
        print(f"[FAIL zip] {z}")
    if done >= batch:
        break

have = sum(1 for pair in NEEDED.values() for z in pair
           if (OUT / z).exists() and (OUT / z).stat().st_size > 1_000_000)
print(f"\nитого готово: {have}/24")
