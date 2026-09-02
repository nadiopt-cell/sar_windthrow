#!/usr/bin/env python3
"""Diagnostic: dB levels per date inside ref / bg ring (ID666)."""
import numpy as np
from osgeo import gdal

gdal.UseExceptions()


def read(path):
    ds = gdal.Open(path)
    a = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    return a


def main():
    ref = read("/home/z/my-project/work_data/warp/ref_mask.tif") > 0
    bg = read("/home/z/my-project/work_data/warp/bg_mask.tif") > 0
    print("date  | VV_ref  VV_bg | VH_ref  VH_bg   (dB)")
    for lab in ("pre3", "pre2", "pre1", "base", "post"):
        vals = []
        for pol in ("vv", "vh"):
            a = read(f"/home/z/my-project/work_data/warp/{lab}_{pol}.tif")
            with np.errstate(divide="ignore", invalid="ignore"):
                db = np.where(a > 0, 10.0 * np.log10(a), np.nan)
            vals.append(np.nanmean(db[ref]))
            vals.append(np.nanmean(db[bg]))
        print(f"{lab} | {vals[0]:6.2f} {vals[1]:6.2f} | "
              f"{vals[2]:6.2f} {vals[3]:6.2f}")


if __name__ == "__main__":
    main()
