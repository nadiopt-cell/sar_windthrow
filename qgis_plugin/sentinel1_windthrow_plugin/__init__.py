"""Sentinel-1 SAR Plugin — entry point for QGIS.

The `classFactory` function is required by QGIS: it is called when the
plugin is loaded and must return a plugin instance.
"""


def classFactory(iface):
    """Create and return the plugin instance.

    :param iface: QGIS interface object (QgsInterface).
    """
    from .sentinel1_plugin import Sentinel1Plugin

    return Sentinel1Plugin(iface)