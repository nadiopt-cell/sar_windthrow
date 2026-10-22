#!/usr/bin/env python3
"""Evaluate a coherence score against independent damaged/intact raster masks.

Run in a QGIS Python environment with GDAL, NumPy and the plugin installed:
  python tools/evaluate_coherence.py --score score.tif \
    --damaged damaged_mask.tif --intact intact_mask.tif --out metrics.json

Reference masks must already be aligned to the score raster, with value
> 0 for their respective class. Use INDEPENDENT field/optical labels;
do not rasterize polygons produced by the coherence detector itself.
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from osgeo import gdal, osr

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_PARENT))
from sentinel1_windthrow_plugin.sources.coherence_science import (  # noqa: E402
    evaluate_reference_masks,
)


def read_raster(path):
    ds = gdal.Open(str(path), gdal.GA_ReadOnly)
    if ds is None:
        raise ValueError(f"Cannot open raster: {path}")
    band = ds.GetRasterBand(1)
    a = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    if a is None:
        raise ValueError(f"Cannot read raster: {path}")
    info = (ds.RasterXSize, ds.RasterYSize, ds.GetGeoTransform(),
            ds.GetProjection())
    return a, nodata, info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", required=True)
    parser.add_argument("--damaged", required=True)
    parser.add_argument("--intact", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--fpr", type=float, default=0.05)
    args = parser.parse_args()
    score, score_ndv, ref = read_raster(args.score)
    damaged, _, dref = read_raster(args.damaged)
    intact, _, iref = read_raster(args.intact)
    for label, info in (("damaged", dref), ("intact", iref)):
        if info[:3] != ref[:3]:
            raise ValueError(f"{label} raster grid is not aligned to score")
        s1, s2 = osr.SpatialReference(), osr.SpatialReference()
        s1.ImportFromWkt(ref[3])
        s2.ImportFromWkt(info[3])
        if not s1.IsSame(s2):
            raise ValueError(f"{label} raster CRS is not aligned to score")
    score = score.astype(np.float64)
    if score_ndv is not None:
        score[score == score_ndv] = np.nan
    result = evaluate_reference_masks(score, damaged > 0, intact > 0,
                                      fpr_target=args.fpr)
    result["score_file"] = str(Path(args.score).resolve())
    result["damaged_file"] = str(Path(args.damaged).resolve())
    result["intact_file"] = str(Path(args.intact).resolve())
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n",
                              encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
