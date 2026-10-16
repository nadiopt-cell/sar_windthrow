"""Pytest configuration for the Sentinel-1 plugin unit tests.

The QGIS plugin folder is itself a Python package named
``sentinel1_windthrow_plugin``, so the directory *above* it must be on
``sys.path`` for ``import sentinel1_windthrow_plugin`` to resolve. Running pytest
from inside the plugin folder does not provide that automatically.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
# .../sentinel1_windthrow_plugin/tests -> .../sentinel1_windthrow_plugin -> parent
_PROJECT_PARENT = os.path.dirname(os.path.dirname(_TESTS_DIR))
if _PROJECT_PARENT not in sys.path:
    sys.path.insert(0, _PROJECT_PARENT)

# Lightweight-CI mode (WINDTHROW_PURE_CI=1, see
# .github/workflows/test-plugin.yml): the pure job has no GDAL runtime,
# so every test module whose SOURCE touches the raster stack is skipped
# at collection time (a conservative source scan: "gdal"/"osgeo"
# tokens).  IO-free modules (pc_client, planetary_computer mocks, the
# read-only pipeline threshold tests) still run and guard the pure
# numpy/scipy paths on every push.
if os.environ.get("WINDTHROW_PURE_CI"):
    import pathlib

    def _touches_raster_stack(path):
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        return "gdal" in text or "osgeo" in text

    collect_ignore = [
        entry.name
        for entry in sorted(pathlib.Path(_TESTS_DIR).glob("test_*.py"))
        if _touches_raster_stack(entry)
    ]
else:
    collect_ignore = []
