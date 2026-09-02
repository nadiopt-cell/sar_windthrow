#!/usr/bin/env python3
"""Сборка бандлов для облачной доставки (панель загрузки у пользователя не работает).

Создаёт в download/cloud/:
  1. windthrow_worklogs_20260902.zip       — все ворклоги (внутренний + контекст + человекочитаемый)
  2. windthrow_id666_results_20260902.zip  — все JSON результатов step7 (A-F, baselines, analysis, final, manifest, статусы)
  3. windthrow_project_full_20260902.zip   — всё вместе + плагины v0.7/v0.8 + доки + чекпоинты
"""
import zipfile
from pathlib import Path

DL = Path('/home/z/my-project/download')
ROOT = Path('/home/z/my-project')
CLOUD = DL / 'cloud'
CLOUD.mkdir(exist_ok=True)

WORKLOGS = [
    (ROOT / 'worklog.md', 'worklog_full_internal.md'),
    (DL / 'ВОРКЛОГ_контекст_2026-09-02.md', 'ВОРКЛОГ_контекст_2026-09-02.md'),
    (DL / 'Ворклог_детектирование_ветровалов_плагин.md', 'Ворклог_детектирование_ветровалов_плагин.md'),
    (DL / 'README.md', 'README_download.md'),
]

RESULTS = [
    (DL / f, f) for f in sorted(
        [p.name for p in DL.glob('windthrow_id*.json')]
        + [p.name for p in DL.glob('step*_warp_manifest.json')]
        + ['windthrow_leafoff_analysis_2026-09-02.json']
    )
]

DOCS = [
    (DL / f, f) for f in [
        'Обзор_детектирование_ветровалов_SAR.docx',
        'Воркфлоу_детектирование_ветровалов_Sentinel-1.docx',
        'Источники_SAR_детектирование_ветровалов.xlsx',
    ]
]

PLUGINS = [
    (DL / f, f) for f in [
        'sentinel1_windthrow_plugin_v0.7.0.zip',
        'sentinel1_windthrow_plugin_v0.8.0.zip',
    ]
]

CHECKPOINTS = [
    (DL / f, f) for f in [
        'checkpoint_20260902_final.tar.gz',
        'checkpoint_20260902_task1_v08.tar.gz',
        'checkpoint_20260902_task3_light.tar.gz',
        'checkpoint_20260902_step8.tar.gz',
    ]
]


def make_zip(out_name, pairs):
    out = CLOUD / out_name
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for src, arc in pairs:
            if not src.exists():
                print(f'  !! пропущен (нет файла): {src}')
                continue
            z.write(src, arcname=arc)
            n += 1
    size = out.stat().st_size
    print(f'OK {out.name}: {size} bytes, {n} файлов внутри')
    return out


if __name__ == '__main__':
    make_zip('windthrow_worklogs_20260902.zip', WORKLOGS)
    make_zip('windthrow_id666_results_20260902.zip', RESULTS)
    make_zip('windthrow_project_full_20260902.zip',
             WORKLOGS + RESULTS + DOCS + PLUGINS + CHECKPOINTS)
    print('Готово. Бандлы в', CLOUD)
