#!/usr/bin/env python3
"""Diagnostic: vsicurl throughput — single vs parallel handles."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/z/my-project/plugin_work")
from osgeo import gdal  # noqa: E402
gdal.UseExceptions()
from sentinel1_windthrow_plugin.sources.planetary_computer import (  # noqa: E402
    PlanetaryComputerSource,
)
from datetime import datetime  # noqa: E402


def get_href():
    src = PlanetaryComputerSource(collection="sentinel-1-rtc")
    scenes = src.search(
        bbox=(42.68, 61.63, 43.72, 62.11),
        start_date=datetime(2017, 7, 21), end_date=datetime(2017, 7, 23, 23, 59),
        polarization="VV+VH", orbit="Descending", collection="sentinel-1-rtc")
    return scenes[0].assets["vv"]


def read_strip(href, y0, rows=512, width=5245, x0=17059):
    ds = gdal.Open(f"/vsicurl/{href}", gdal.GA_ReadOnly)  # own handle
    arr = ds.GetRasterBand(1).ReadAsArray(x0, y0, width, rows)
    ds = None
    return arr is not None


def main():
    gdal.SetConfigOption("CPL_VSIL_CURL_USE_HEAD", "NO")
    gdal.SetConfigOption("CPL_VSIL_CURL_CHUNK_SIZE", "8388608")
    gdal.SetConfigOption("GDAL_HTTP_VERSION", "2")
    gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "8")
    gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "3")
    href = get_href()

    # single strip timing
    t0 = time.time()
    ok = read_strip(href, 9841)
    dt1 = time.time() - t0
    print(f"single strip 512x5245: {dt1:.1f} s ok={ok}")

    # 4 parallel strips (disjoint rows)
    ys = [10353, 10865, 11377, 11889]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(lambda y: read_strip(href, y), ys))
    dt4 = time.time() - t0
    print(f"4 parallel strips: {dt4:.1f} s ok={sum(res)}/4 "
          f"-> speedup={dt1 * 4 / dt4:.1f}x")


if __name__ == "__main__":
    main()
