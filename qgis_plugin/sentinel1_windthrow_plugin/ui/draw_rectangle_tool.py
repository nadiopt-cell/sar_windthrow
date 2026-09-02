"""Map tool that draws an axis-aligned rectangle on the canvas.

Used by the Search & Download tab "Draw on map" button. Emits
:attr:`DrawnRectangleTool.extentDrawn` with the rectangle in the
canvas's CRS (typically the project CRS) when the user finishes a drag.

The tool uses only QGIS built-ins (``QgsMapToolEmitPoint`` +
``QgsRubberBand``), so no third-party dependency is required.

Implementation notes
--------------------
* Subclassing :class:`QgsMapToolEmitPoint` disables QGIS's built-in
  zoom-rectangle and routes mouse events to us cleanly.
* The rubber band is created lazily on the *first* move event of a drag
  (press only stores the start point). Creating it in ``canvasPressEvent``
  and pre-populating it with points causes QGIS 3.34+ to collapse
  press→release into a single instant event, silently discarding the
  drag as a "zero-area click".
* The release position is read from ``event.pixelPoint()`` rather than
  ``event.pos()`` — the latter sometimes still reports the press
  position on certain QGIS builds.
"""

from __future__ import annotations

from qgis.core import QgsPointXY, QgsRectangle, QgsWkbTypes
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor


class DrawnRectangleTool(QgsMapToolEmitPoint):
    """Map tool: click and drag to define a rectangle.

    Signals
    -------
    extentDrawn(QgsRectangle, str)
        Emitted when the user finishes a drag. Arguments are the drawn
        rectangle and the canvas CRS authid (e.g. ``"EPSG:4326"``).
    toolDeactivated()
        Emitted when QGIS replaces this tool with another one. Lets the
        dialog uncheck its "Draw on map" button.
    """

    extentDrawn = pyqtSignal(object, str)   # (QgsRectangle, crs.authid())
    toolDeactivated = pyqtSignal()

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self._rubber: QgsRubberBand | None = None
        self._start_point: QgsPointXY | None = None
        self._dragging: bool = False
        # Crosshair cursor so the user immediately sees the tool is active.
        self.setCursor(QCursor(Qt.CrossCursor))

    # ---------------------------------------------------------------- draw
    def canvasPressEvent(self, event) -> None:  # noqa: N802 - QGIS API
        if event.button() != Qt.LeftButton:
            return
        # Only record the start point here. Creating the rubber band in
        # press and pre-populating it with points is what caused QGIS to
        # collapse press→release into a single instant event.
        try:
            sp = self.toMapCoordinates(event.pos())
        except Exception:
            return
        self._start_point = sp
        self._dragging = False

    def canvasMoveEvent(self, event) -> None:  # noqa: N802 - QGIS API
        # Fires on every mouse move — keep it cheap.
        if self._start_point is None:
            return
        try:
            ep = self.toMapCoordinates(event.pos())
        except Exception:
            return
        # Lazy rubber-band creation on first real drag move.
        if not self._dragging:
            self._ensure_rubber()
            self._dragging = True
        self._update_rubber(self._start_point, ep)

    def canvasReleaseEvent(self, event) -> None:  # noqa: N802 - QGIS API
        if self._start_point is None:
            self._reset_state()
            return
        # Read release position from pixelPoint() — under QGIS 3.34+
        # event.pos() sometimes still reports the press position, which
        # made start == end and the rectangle was discarded as zero-area.
        try:
            pixel = event.pixelPoint() if hasattr(event, "pixelPoint") else event.pos()
            ep = self.toMapCoordinates(pixel)
        except Exception:
            self._reset_state()
            return
        sp = self._start_point
        rect = QgsRectangle(
            min(sp.x(), ep.x()),
            min(sp.y(), ep.y()),
            max(sp.x(), ep.x()),
            max(sp.y(), ep.y()),
        )
        crs_authid = self.canvas.mapSettings().destinationCrs().authid()
        # Treat genuine drag vs. simple click. Threshold small enough to
        # work for both geographic (degrees) and projected (metres) CRS.
        if rect.width() < 1e-9 and rect.height() < 1e-9:
            self._reset_state()
            return
        self.extentDrawn.emit(rect, crs_authid)
        self._reset_state()

    def deactivate(self) -> None:  # noqa: N802 - QGIS API
        self._reset_state()
        # Notify listeners (the dialog) so the draw button can be unchecked.
        self.toolDeactivated.emit()
        super().deactivate()

    # --------------------------------------------------------------- utils
    def _ensure_rubber(self) -> None:
        if self._rubber is not None:
            return
        self._rubber = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber.setStrokeColor(QColor(255, 80, 80, 200))
        self._rubber.setFillColor(QColor(255, 80, 80, 40))
        self._rubber.setWidth(2)
        self._rubber.setIconSize(0)  # no vertex icons
        self._rubber.setVisible(True)

    def _update_rubber(self, p0: QgsPointXY, p1: QgsPointXY) -> None:
        """Repaint the rubber band as an axis-aligned rectangle p0..p1."""
        assert self._rubber is not None
        x0, y0 = p0.x(), p0.y()
        x1, y1 = p1.x(), p1.y()
        corners = [
            QgsPointXY(x0, y0),
            QgsPointXY(x1, y0),
            QgsPointXY(x1, y1),
            QgsPointXY(x0, y1),
        ]
        # reset + re-add is more robust than movePoint across QGIS builds
        self._rubber.reset(QgsWkbTypes.PolygonGeometry)
        for corner in corners:
            self._rubber.addPoint(corner)
        self._rubber.show()

    def _reset_state(self) -> None:
        """Hide the rubber band and forget the drag state (kept ready for next)."""
        if self._rubber is not None:
            self._rubber.hide()
            self._rubber.reset(QgsWkbTypes.PolygonGeometry)
            self._rubber = None
        self._start_point = None
        self._dragging = False


__all__ = ["DrawnRectangleTool"]
