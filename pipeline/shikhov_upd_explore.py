#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разведка обновлённого датасета Шихова: ветровалы_3316.shp (Google Drive, 2026-09-06).

Выход: /home/z/my-project/upload/shikhov_upd/explore_summary.json + печатная сводка.
"""
import json
import sys

import geopandas as gpd
import pandas as pd

SHP = "/home/z/my-project/upload/shikhov_upd/vetrovaly_3316/ветровалы_3316.shp"
OUT = "/home/z/my-project/upload/shikhov_upd/explore_summary.json"


def main() -> None:
    gdf = gpd.read_file(SHP)
    print(f"features: {len(gdf)}")
    print(f"crs: {gdf.crs}")
    print(f"geometry types:\n{gdf.geom_type.value_counts().to_string()}")

    bounds = gdf.total_bounds
    print(f"bounds (native crs): {bounds}")

    # Площадь в исходной проекции и в равноплощадной
    area_km2 = gdf.to_crs(gdf.estimate_utm_crs()).area / 1e6
    print(f"area: total={area_km2.sum():.1f} km2, mean={area_km2.mean():.3f}, "
          f"median={area_km2.median():.4f}, min={area_km2.min():.6f}, max={area_km2.max():.1f}")

    print("\n--- schema ---")
    print(gdf.dtypes.to_string())

    print("\n--- head(3) ---")
    with pd.option_context("display.max_columns", None, "display.width", 250):
        print(gdf.drop(columns="geometry").head(3).to_string())

    # Категориальные/ключевые поля: распределения
    summary = {
        "path": SHP,
        "n_features": int(len(gdf)),
        "crs": str(gdf.crs),
        "geom_types": gdf.geom_type.value_counts().to_dict(),
        "bounds_native": [float(x) for x in bounds],
        "area_km2": {
            "total": float(area_km2.sum()),
            "mean": float(area_km2.mean()),
            "median": float(area_km2.median()),
            "min": float(area_km2.min()),
            "max": float(area_km2.max()),
        },
        "columns": {c: str(t) for c, t in gdf.dtypes.items()},
        "sample_rows": gdf.drop(columns="geometry").head(5).astype(str).to_dict("records"),
        "value_counts": {},
        "date_ranges": {},
    }

    for col in gdf.columns:
        if col == "geometry":
            continue
        s = gdf[col]
        nuniq = s.nunique(dropna=True)
        nnull = int(s.isna().sum())
        if nuniq <= 60:
            vc = s.value_counts(dropna=True).head(40)
            summary["value_counts"][col] = {str(k): int(v) for k, v in vc.items()}
            print(f"\n--- {col} ({nuniq} uniq, {nnull} null) ---")
            print(vc.to_string())
        else:
            # числовые диапазоны
            try:
                num = pd.to_numeric(s)
                summary["date_ranges"][col] = {
                    "min": (None if pd.isna(num.min()) else float(num.min())),
                    "max": (None if pd.isna(num.max()) else float(num.max())),
                    "n_unique": int(nuniq),
                    "n_null": nnull,
                }
                print(f"\n--- {col}: numeric, {nuniq} uniq, {nnull} null, "
                      f"min={num.min()}, max={num.max()}")
            except (ValueError, TypeError):
                print(f"\n--- {col}: {nuniq} uniq, {nnull} null (not numeric, not categorical)")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\nsaved: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
