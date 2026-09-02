"""Sentinel1Plugin — main plugin class.

Responsibilities:
- register / unregister the plugin in the QGIS GUI (menu + toolbar)
- own the main dialog instance
"""

import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .logger import log_info
from .sentinel1_plugin_dialog import Sentinel1PluginDialog

PLUGIN_NAME = "Sentinel-1 Windthrow Detector"
PLUGIN_MENU = "&Sentinel-1 Windthrow"


class Sentinel1Plugin:
    """QGIS plugin entry point implementing the standard plugin protocol."""

    def __init__(self, iface):
        """Store the QGIS interface reference.

        :param iface: An instance of QgsInterface (QGIS main window hooks).
        """
        self.iface = iface
        self.dlg = None
        self.action = None
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName(PLUGIN_NAME)

    # ------------------------------------------------------------------
    # QGIS plugin interface
    # ------------------------------------------------------------------

    def initGui(self):
        """Create the toolbar action and menu entries (called on load)."""
        icon = QIcon(_plugin_icon_path())
        self.action = QAction(icon, self.tr(PLUGIN_NAME), self.iface.mainWindow())
        self.action.setObjectName("mActionSentinel1Windthrow")
        self.action.triggered.connect(self.run)

        self.iface.addPluginToMenu(PLUGIN_MENU, self.action)
        self.toolbar.addAction(self.action)

        log_info("Plugin loaded successfully")

    def unload(self):
        """Remove the plugin UI from QGIS (called on unload)."""
        if self.action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self.action)
            self.iface.removeToolBarIcon(self.action)

        try:
            del self.toolbar
        except AttributeError:
            pass

    def run(self):
        """Show the main dialog, reusing the existing instance if present."""
        if self.dlg is None:
            self.dlg = Sentinel1PluginDialog(self.iface, parent=self.iface.mainWindow())

        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def tr(message: str) -> str:
        """Translate ``message`` using the plugin translation context."""
        return QCoreApplication.translate("Sentinel1Plugin", message)


def _plugin_icon_path() -> str:
    """Return the absolute path to the plugin icon file."""
    return os.path.join(os.path.dirname(__file__), "icon.svg")