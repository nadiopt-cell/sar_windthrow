#!/usr/bin/env python3
"""Диагностика CRS/сетки RTC-сцены для события 694 (Пермский край)."""
import sys

sys.path.insert(0, '/home/z/my-project/scripts')
sys.path.insert(0, '/home/z/my-project/plugin_work')
from osgeo import gdal  # noqa: E402
gdal.UseExceptions()

from run_windthrow_leafoff_step8 import (  # noqa: E402
    EVENTS, aoi_grid, bounds_to_wgs84, pick_scenes_near,
)
from sentinel1_windthrow_plugin.sources.planetary_computer import (  # noqa: E402
    PlanetaryComputerSource,
)
from datetime import datetime

ev = EVENTS[694]
bounds, w, h = aoi_grid(694, ev['epsg'])
print('grid:', w, 'x', h, 'bounds:', [round(x) for x in bounds])
bbox = bounds_to_wgs84(bounds, ev['epsg'])

src = PlanetaryComputerSource(collection='sentinel-1-rtc')
scenes = pick_scenes_near(src, bbox, '2017-09-24', ('descending', 152))
print('scenes:', [s.id for s in scenes])
s = scenes[0]
href = s.assets['vv']
print('href:', href[:110], '...')

ds = gdal.Open(f'/vsicurl/{href}', gdal.GA_ReadOnly)
print('size:', ds.RasterXSize, 'x', ds.RasterYSize)
print('gt:', ds.GetGeoTransform())
wkt = ds.GetProjection()
sr = __import__('osr').SpatialReference()
sr.ImportFromWkt(wkt)
print('epsg:', sr.GetAuthorityCode(None), '| name:', sr.GetName())
print('dtype:', gdal.GetDataTypeName(ds.GetRasterBand(1).DataType),
      '| nodata:', ds.GetRasterBand(1).GetNoDataValue())
# проверка выравнивания с AOI-сеткой
rgt = ds.GetGeoTransform()
dx = (bounds[0] - rgt[0]) / 10.0
dy = (rgt[3] - bounds[3]) / 10.0
print('dx:', dx, 'dy:', dy, '(int?', abs(dx - round(dx)) < 1e-6,
      abs(dy - round(dy)) < 1e-6, ')')
print('px size:', rgt[1], rgt[5])
# выборка значений в центре сцены
band = ds.GetRasterBand(1)
arr = band.ReadAsArray(ds.RasterXSize // 2, ds.RasterYSize // 2, 64, 64)
print('sample min/max/mean:', float(arr.min()), float(arr.max()),
      float(arr.mean()))
