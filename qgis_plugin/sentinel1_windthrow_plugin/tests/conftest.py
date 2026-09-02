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
