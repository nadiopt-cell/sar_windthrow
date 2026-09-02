#!/usr/bin/env python3
"""Диагностика геометрии события 590 (после OOM на последовательном union)."""
import sys
import time

sys.path.insert(0, '/home/z/my-project/scripts')
import run_windthrow_leafoff_step8 as s8  # noqa: E402

t0 = time.time()
u, b, d = s8.event_geometry_aoi(590, 32641)
print(f'geom ok in {time.time() - t0:.1f} s')
print('union parts:', u.GetGeometryCount(), 'area_km2:', u.GetArea() / 1e6)
bounds, w, h = s8.snapped_grid(b)
print('grid', w, 'x', h)
print('bbox wgs84', [round(x, 4) for x in s8.bounds_to_wgs84(bounds, 32641)])
