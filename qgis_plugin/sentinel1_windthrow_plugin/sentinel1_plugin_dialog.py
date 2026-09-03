"""Sentinel1PluginDialog — main plugin dialog with a tabbed interface.

The dialog hosts four tabs:
    1. Search & Download  — STAC search + COG download (Planetary Computer)
    2. Preprocess         — dB conversion, speckle filter, land mask
    3. Windthrow Detection — bi-temporal WI change detection
                             (Rüetschi et al. 2019) + vectorisation
    4. Settings           — default folders, parameters, About
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Tuple

from qgis.PyQt.QtCore import Qt, QDate, QObject, QUrl, QByteArray, QBuffer, QIODevice
from qgis.PyQt.QtGui import QIcon, QPixmap, QImage
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsSettings,
    QgsTask,
)

from .logger import format_exception, log_info, log_warning
from .sources import (
    OperationCancelled,
    PlanetaryComputerSource,
    SARPreprocessor,
    Scene,
    WindthrowDetector,
    common_polarizations,
    extract_polarization,
    pair_by_polarization,
)
from .sources.coh_delta import CoherenceDeltaDetector
from .sources.lband import LbandDeclineDetector
from .sources import forest_mask
from .ui import DrawnRectangleTool


# ======================================================================
# Worker tasks for long operations
# ======================================================================
class SearchTask(QgsTask):
    """Search for Sentinel-1 scenes via ``PlanetaryComputerSource``."""

    def __init__(
        self,
        description: str,
        source: PlanetaryComputerSource,
        bbox: Tuple[float, float, float, float],
        start_date: datetime,
        end_date: datetime,
        polarization: str,
        orbit: str,
        collection: str = "",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(description, QgsTask.CanCancel)
        self.source = source
        self.bbox = bbox
        self.start_date = start_date
        self.end_date = end_date
        self.polarization = polarization
        self.orbit = orbit
        # STAC collection id ("sentinel-1-grd" / "sentinel-1-rtc"); empty
        # string lets the source use its default.
        self.collection = collection or ""
        self.scenes: List[Scene] = []
        self.exception: Optional[Exception] = None

    def run(self) -> bool:
        try:
            self.scenes = self.source.search(
                bbox=self.bbox,
                start_date=self.start_date,
                end_date=self.end_date,
                polarization=self.polarization,
                orbit=self.orbit,
                progress_cb=self.setProgress,
                cancel_cb=self.isCanceled,
                collection=self.collection or None,
            )
        except Exception as exc:  # pragma: no cover - runtime errors only
            self.exception = exc
            return False
        return not self.isCanceled()

    def finished(self, result: bool) -> None:  # pragma: no cover - GUI hook
        if result:
            log_info(f"Search completed: {len(self.scenes)} scenes found")
        elif self.exception is not None:
            log_warning(f"Search failed: {self.exception}")
        else:
            log_info("Search cancelled by user")


class DownloadTask(QgsTask):
    """Download a single scene's assets.

    Per-asset byte-level progress is reported via the standard
    :pyattr:`QgsTask.progressChanged` signal (0..100). The dialog connects
    to this signal to update the per-row progress bar.
    """

    def __init__(
        self,
        description: str,
        source: PlanetaryComputerSource,
        scene: Scene,
        dest_dir: str,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(description, QgsTask.CanCancel)
        self.source = source
        self.scene = scene
        self.dest_dir = dest_dir
        self.download_paths: List[str] = []
        self.exception: Optional[Exception] = None

    def run(self) -> bool:
        try:
            self.download_paths = self.source.download(
                scene=self.scene,
                dest_dir=self.dest_dir,
                progress_cb=self.setProgress,
                cancel_cb=self.isCanceled,
            )
        except Exception as exc:  # pragma: no cover
            self.exception = exc
            return False
        return not self.isCanceled()

    def finished(self, result: bool) -> None:  # pragma: no cover - GUI hook
        if result:
            log_info(
                f"Downloaded scene {self.scene.id}: {len(self.download_paths)} files"
            )
        elif self.exception is not None:
            log_warning(f"Download failed for {self.scene.id}: {self.exception}")
        else:
            log_info(f"Download cancelled for {self.scene.id}")


class PreprocessTask(QgsTask):
    """Preprocess a single SAR GeoTIFF file."""

    def __init__(
        self,
        description: str,
        input_path: str,
        output_path: str,
        to_db: bool = False,
        speckle: bool = False,
        speckle_kernel: int = 5,
        land_mask: bool = False,
        land_mask_threshold_db: float = -20.0,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(description, QgsTask.CanCancel)
        self.input_path = input_path
        self.output_path = output_path
        self.to_db = to_db
        self.speckle = speckle
        self.speckle_kernel = speckle_kernel
        self.land_mask = land_mask
        self.land_mask_threshold_db = land_mask_threshold_db
        self.exception: Optional[Exception] = None

    def run(self) -> bool:
        try:
            SARPreprocessor.process_file(
                input_path=self.input_path,
                output_path=self.output_path,
                to_db=self.to_db,
                speckle=self.speckle,
                speckle_kernel=self.speckle_kernel,
                land_mask=self.land_mask,
                land_mask_threshold_db=self.land_mask_threshold_db,
            )
        except Exception as exc:  # pragma: no cover
            self.exception = exc
            return False
        return not self.isCanceled()

    def finished(self, result: bool) -> None:  # pragma: no cover - GUI hook
        if result:
            log_info(f"Preprocessing completed: {self.output_path}")
        elif self.exception is not None:
            log_warning(
                f"Preprocessing failed for {self.input_path}: {self.exception}"
            )
        else:
            log_info(f"Preprocessing cancelled for {self.input_path}")


class AnalysisTask(QgsTask):
    """Run a windthrow detection routine on a QgsTask.

    The actual routine is supplied as a callable to keep this class
    generic. The callable must accept no arguments and return the
    output path (string). It is invoked from ``run()`` inside the
    background thread.
    """

    def __init__(
        self,
        description: str,
        work,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(description, QgsTask.CanCancel)
        self._work = work
        self.output_path: Optional[str] = None
        self.exception: Optional[Exception] = None

    def run(self) -> bool:
        try:
            self.output_path = self._work()
        except Exception as exc:  # pragma: no cover
            self.exception = exc
            return False
        return not self.isCanceled()

    def finished(self, result: bool) -> None:  # pragma: no cover - GUI hook
        if result:
            log_info(f"Analysis completed: {self.output_path}")
        elif self.exception is not None:
            log_warning(f"Analysis failed: {self.exception}")
        else:
            log_info("Analysis cancelled by user")


# ----------------------------------------------------------------------
# Module-level helper: measurement TIFF discovery (shared by the
# Windthrow tab's "Add folder..." button).
# ----------------------------------------------------------------------
_AUX_MARKERS = (
    "manifest", "schema", "calibration", "noise", "product",
    "tiepoint", "tie-point", "tie_point", "annotation",
    "preview", "thumbnail", "quick-look", "quicklook",
    "browse", "report", "summary", "icon", "overlay",
    "map-overlay", "ql.png",
)
_POL_RE = None  # compiled lazily


def _list_measurement_tifs(folder: str) -> List[str]:
    """Return SAR measurement TIFFs under ``folder`` (recursive).

    Applies the same rules as the Preprocess tab's file-list refresh:
    ``.tif/.tiff`` files whose name carries a delimited polarisation
    token (vv/vh/hh/hv) and none of the auxiliary markers.
    """
    import re as _re

    global _POL_RE
    if _POL_RE is None:
        _POL_RE = _re.compile(r"(?:^|[-_])(vv|vh|hh|hv)(?:[-_.]|$)",
                              _re.IGNORECASE)
    found: List[str] = []
    if not folder or not os.path.isdir(folder):
        return found
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        files.sort()
        for name in files:
            lower = name.lower()
            if not lower.endswith((".tif", ".tiff")):
                continue
            if any(m in lower for m in _AUX_MARKERS):
                continue
            stem = os.path.splitext(name)[0]
            if _POL_RE.search(stem) is None:
                continue
            found.append(os.path.join(root, name))
    return found


# ======================================================================
# Main dialog
# ======================================================================
class Sentinel1PluginDialog(QDialog):
    """Main plugin dialog with four tabs.

    All long operations (search, download, preprocessing, analysis) are
    dispatched to ``QgsApplication.taskManager()`` via ``QgsTask``
    subclasses defined above. The dialog itself only builds the UI and
    wires signals.
    """

    #: Settings keys (kept in one place so save/load stays in sync).
    SETTINGS_GROUP = "Sentinel1SAR"
    KEY_DOWNLOAD_DIR = f"{SETTINGS_GROUP}/downloadDir"
    KEY_PREPROCESS_INPUT_DIR = f"{SETTINGS_GROUP}/preprocessInputDir"
    KEY_PREPROCESS_OUTPUT_DIR = f"{SETTINGS_GROUP}/preprocessOutputDir"
    KEY_DEFAULT_SPECKLE_KERNEL = f"{SETTINGS_GROUP}/defaultSpeckleKernel"
    KEY_DEFAULT_LAND_MASK_DB = f"{SETTINGS_GROUP}/defaultLandMaskDb"
    KEY_DEFAULT_WI_OFFSET_DB = f"{SETTINGS_GROUP}/defaultWiOffsetDb"
    KEY_DEFAULT_MIN_OBJECT_PX = f"{SETTINGS_GROUP}/defaultMinObjectPx"
    KEY_ADD_TO_MAP = f"{SETTINGS_GROUP}/addToMap"
    #: STAC collection id of the selected product (GRD / RTC).
    KEY_PRODUCT = f"{SETTINGS_GROUP}/product"
    #: Default product — standard Sentinel-1 GRD on Planetary Computer.
    DEFAULT_PRODUCT_ID = "sentinel-1-grd"

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Sentinel-1 Windthrow Detector"))
        self.resize(1000, 720)

        # QGIS interface — optional but required for canvas/layer-based AOI.
        # Stored as None when the dialog is opened outside QGIS (e.g. tests).
        self._iface = iface

        # Map tool for the "Draw on map" button (created lazily on first use).
        self._draw_tool = None

        # Data source — created once and reused for all searches/downloads.
        try:
            self._source = PlanetaryComputerSource()
        except Exception as exc:  # pragma: no cover - depends on env
            log_warning(f"Could not initialise PlanetaryComputerSource: {exc}")
            self._source = None  # type: ignore[assignment]

        # State
        self._search_results: List[Scene] = []
        # Anchor for the running search task. QgsTaskManager owns the C++
        # object, but the SIP/Python wrapper must be kept alive from here —
        # otherwise it can be garbage-collected before the completion
        # callbacks fire and the task crashes with "wrapped C/C++ object
        # has been deleted". Download/preprocess/analysis tasks are
        # anchored the same way (see the lists below).
        self._active_search_task: Optional[SearchTask] = None
        self._active_download_tasks: List[DownloadTask] = []
        self._active_preprocess_tasks: List[PreprocessTask] = []
        self._prep_failure_messages: List[str] = []
        self._prep_success_count: int = 0
        self._active_analysis_task: Optional[AnalysisTask] = None
        self._settings = QgsSettings()

        # Async thumbnail loader. One QNetworkAccessManager instance serves
        # all preview image requests; replies are tracked by scene id.
        self._nam = QNetworkAccessManager(self)
        self._nam.setRedirectPolicy(QNetworkRequest.NoLessSafeRedirectPolicy)
        # Map: scene_id -> QLabel (the preview cell). Used to dispatch the
        # finished reply to the right row.
        self._pending_preview_replies: dict = {}
        # Map: row index -> QProgressBar. Used to update per-row download
        # progress when the corresponding DownloadTask emits progressChanged.
        self._row_progress_bars: dict = {}
        # Map: DownloadTask -> row index. Used to look up the row when the
        # task emits progress or finishes.
        self._task_row_map: dict = {}

        self._build_ui()
        self._load_settings()
        log_info("Main dialog created")

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        from qgis.PyQt.QtWidgets import QTabWidget
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        # 1) Search & Download
        self.page_search = self._build_search_tab()
        self.tabs.addTab(self.page_search, "Search & Download")

        # 2) Preprocess
        self.page_preprocess = self._build_preprocess_tab()
        self.tabs.addTab(self.page_preprocess, "Preprocess")

        # 3) Windthrow Detection
        self.page_windthrow = self._build_windthrow_tab()
        self.tabs.addTab(self.page_windthrow, "Windthrow Detection")

        # 4) Settings
        self.page_settings = self._build_settings_tab()
        self.tabs.addTab(self.page_settings, "Settings")

        # Close button at the bottom of the dialog
        self.button_box = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    # ------------------------------------------------------------------
    # Tab 1 — Search & Download
    # ------------------------------------------------------------------
    def _build_search_tab(self) -> QWidget:
        """Build the Search & Download tab."""
        page = QWidget(self)
        layout = QVBoxLayout(page)

        # ----- AOI group -----
        aoi_group = QGroupBox("Area of Interest (AOI)", page)
        aoi_layout = QFormLayout(aoi_group)
        self.min_lon_edit = QLineEdit(page)
        self.min_lon_edit.setPlaceholderText("e.g. 30.0")
        self.max_lon_edit = QLineEdit(page)
        self.max_lon_edit.setPlaceholderText("e.g. 40.0")
        self.min_lat_edit = QLineEdit(page)
        self.min_lat_edit.setPlaceholderText("e.g. 40.0")
        self.max_lat_edit = QLineEdit(page)
        self.max_lat_edit.setPlaceholderText("e.g. 50.0")
        lon_row = QHBoxLayout()
        lon_row.addWidget(QLabel("Min Lon:", page))
        lon_row.addWidget(self.min_lon_edit)
        lon_row.addWidget(QLabel("Max Lon:", page))
        lon_row.addWidget(self.max_lon_edit)
        lat_row = QHBoxLayout()
        lat_row.addWidget(QLabel("Min Lat:", page))
        lat_row.addWidget(self.min_lat_edit)
        lat_row.addWidget(QLabel("Max Lat:", page))
        lat_row.addWidget(self.max_lat_edit)
        aoi_layout.addRow(lon_row)
        aoi_layout.addRow(lat_row)

        # ----- AOI helper buttons -----
        # Three shortcuts for picking the bbox from the QGIS map:
        #   * Canvas extent — copy the current map view extent.
        #   * Draw on map   — rubber-band a rectangle on the canvas.
        #   * Layer extent  — copy the active layer's extent.
        # All three transform the picked rectangle to EPSG:4326 and fill
        # the four line edits above. They require a QGIS interface (the
        # dialog can also be opened outside QGIS for testing, in which
        # case the buttons auto-disable).
        aoi_buttons_row = QHBoxLayout()
        self.aoi_canvas_btn = QPushButton("Use canvas extent", page)
        self.aoi_canvas_btn.setToolTip(
            "Fill the AOI fields with the current map canvas extent "
            "(reprojected to EPSG:4326)."
        )
        self.aoi_draw_btn = QPushButton("Draw on map", page)
        self.aoi_draw_btn.setCheckable(True)
        self.aoi_draw_btn.setToolTip(
            "Click to activate, then drag a rectangle on the map. "
            "Click again to cancel."
        )
        self.aoi_layer_btn = QPushButton("Use active layer extent", page)
        self.aoi_layer_btn.setToolTip(
            "Fill the AOI fields with the extent of the currently active "
            "layer in the Layers panel (reprojected to EPSG:4326)."
        )
        aoi_buttons_row.addWidget(self.aoi_canvas_btn)
        aoi_buttons_row.addWidget(self.aoi_draw_btn)
        aoi_buttons_row.addWidget(self.aoi_layer_btn)
        aoi_layout.addRow("Pick from map:", aoi_buttons_row)

        layout.addWidget(aoi_group)

        # ----- Filters group -----
        filters_group = QGroupBox("Search Filters", page)
        filters_layout = QFormLayout(filters_group)
        self.start_date_edit = QDateEdit(page)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addMonths(-1))
        self.end_date_edit = QDateEdit(page)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        self.product_combo = QComboBox(page)
        # data() holds the STAC collection id handed to the source; the
        # label stays short for the narrow form column.
        self.product_combo.addItem("GRD — ground range", "sentinel-1-grd")
        self.product_combo.addItem("RTC — terrain corrected", "sentinel-1-rtc")
        self.product_combo.setToolTip(
            "<b>GRD</b> — standard Sentinel-1 product (σ⁰, ground range "
            "detected). No terrain correction: in hilly terrain slopes can "
            "look like change (layover / radar shadow). Acceptable for "
            "windthrow in flat terrain — boreal lowlands are usually fine.\n"
            "<b>RTC</b> — radiometrically terrain corrected (γ⁰, computed "
            "from a DEM). Matches the Rüetschi et al. 2019 method "
            "(calibrated γ⁰); <b>preferred for windthrow</b>, and strongly "
            "recommended in any terrain with relief.\n"
            "Note: do not mix GRD and RTC images within one detection run."
        )
        self.orbit_combo = QComboBox(page)
        self.orbit_combo.addItems(["Any", "Ascending", "Descending"])
        self.orbit_combo.setToolTip(
            "Ascending — satellite flies south→north (~18:00 local time).\n"
            "Descending — satellite flies north→south (~06:00 local time).\n"
            "Any — keep both.\n\n"
            "For change detection pick the same direction on both dates."
        )
        filters_layout.addRow("Start date:", self.start_date_edit)
        filters_layout.addRow("End date:", self.end_date_edit)
        filters_layout.addRow("Product:", self.product_combo)
        filters_layout.addRow("Orbit direction:", self.orbit_combo)
        # Hints for polarisation / orbit (most users are not SAR experts).
        # Visible right below the dropdowns. Polarisation is informational
        # only — Sentinel-1 IW GRD scenes are dual-pol (VV + VH) and the
        # plugin always downloads both channels together. The narrower
        # choice (process only VV / only VH) lives on the Preprocess tab.
        hint_label = QLabel(
            "<small>"
            "<b>Polarisation (informational):</b> Sentinel-1 IW scenes "
            "ship with both channels together. The plugin downloads "
            "VV + VH for every selected scene. Windthrow detection needs "
            "<b>both channels</b> (WI = dVV + dVH); keep the "
            "<b>Polarisation</b> combo on the <b>Preprocess</b> tab at "
            "its default <i>Both VV + VH</i>."
            "<br>"
            "<b>VV</b> — co-pol (vertical transmit + vertical receive). "
            "Higher SNR; stable general-purpose channel."
            "<br>"
            "<b>VH</b> — cross-pol (vertical transmit + horizontal receive). "
            "More sensitive to volume scattering — reacts strongly to the "
            "chaotic scattering of windthrown trees."
            "<br>"
            "<b>Orbit:</b> <i>Ascending</i> = south→north (~18:00 local time), "
            "<i>Descending</i> = north→south (~06:00 local time). "
            "<b>For windthrow pick one direction and use only it</b> for "
            "both the pre- and post-storm windows."
            "</small>",
            page,
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #666;")
        filters_layout.addRow("", hint_label)
        layout.addWidget(filters_group)

        # ----- Search button + progress + cancel -----
        search_row = QHBoxLayout()
        self.search_button = QPushButton("Search", page)
        self.search_progress = QProgressBar(page)
        self.search_progress.setVisible(False)
        # Cancel lives right next to the progress bar — relying on the
        # tiny entry in QGIS's global task-bar panel was too hidden.
        self.search_cancel_btn = QPushButton("Cancel", page)
        self.search_cancel_btn.setVisible(False)
        self.search_cancel_btn.setToolTip("Abort the running STAC search.")
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.search_progress, 1)
        search_row.addWidget(self.search_cancel_btn)
        layout.addLayout(search_row)

        # ----- Status label (visible feedback after a search) -----
        # Shows "Found N scenes" / "No scenes found" / "Search failed: ..."
        # so the user is never left wondering if the plugin worked.
        self.search_status_label = QLabel("", page)
        self.search_status_label.setWordWrap(True)
        self.search_status_label.setStyleSheet("padding: 2px 4px;")
        layout.addWidget(self.search_status_label)

        # ----- Results table -----
        self.results_table = QTableWidget(page)
        self.results_table.setColumnCount(7)
        self.results_table.setHorizontalHeaderLabels(
            ["Preview", "ID", "Date", "Polarization", "Orbit", "Rel. Orbit", "Progress"]
        )
        header = self.results_table.horizontalHeader()
        # Preview: fixed 100px wide
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.results_table.setColumnWidth(0, 100)
        # ID: stretch
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        # Date / Polarization / Orbit / Rel. Orbit: size to content
        for col in range(2, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # Progress: fixed 150px wide
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.results_table.setColumnWidth(6, 150)
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.ExtendedSelection)
        # Reasonable row height so the preview thumbnail is visible.
        self.results_table.verticalHeader().setDefaultSectionSize(80)
        self.results_table.setWordWrap(True)
        layout.addWidget(self.results_table, 1)

        # ----- Download button + per-batch progress + cancel -----
        download_row = QHBoxLayout()
        self.download_button = QPushButton("Download Selected", page)
        self.download_button.setEnabled(False)
        self.download_progress = QProgressBar(page)
        self.download_progress.setVisible(False)
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_progress.setFormat("%p%")
        # Status label next to the progress bar — shows "Downloading 2 of 5..."
        # and per-file byte counts when available.
        self.download_status_label = QLabel("", page)
        self.download_status_label.setStyleSheet("color: #555;")
        self.download_cancel_btn = QPushButton("Cancel", page)
        self.download_cancel_btn.setVisible(False)
        self.download_cancel_btn.setToolTip(
            "Abort all running scene downloads.\n"
            "Partially downloaded files are kept and resumed on the next try."
        )
        download_row.addWidget(self.download_button)
        download_row.addWidget(self.download_progress, 1)
        download_row.addWidget(self.download_status_label, 1)
        download_row.addWidget(self.download_cancel_btn)
        layout.addLayout(download_row)

        # ----- Connections -----
        self.search_button.clicked.connect(self._on_search_clicked)
        self.search_cancel_btn.clicked.connect(self._on_search_cancel)
        self.download_button.clicked.connect(self._on_download_clicked)
        self.download_cancel_btn.clicked.connect(self._on_download_cancel)
        self.results_table.itemSelectionChanged.connect(self._update_download_button_state)

        # AOI helper buttons. Auto-disable when running outside QGIS.
        has_iface = self._iface is not None
        self.aoi_canvas_btn.setEnabled(has_iface)
        self.aoi_draw_btn.setEnabled(has_iface)
        self.aoi_layer_btn.setEnabled(has_iface)
        self.aoi_canvas_btn.clicked.connect(self._on_use_canvas_extent)
        self.aoi_draw_btn.clicked.connect(self._on_draw_on_map_toggled)
        self.aoi_layer_btn.clicked.connect(self._on_use_layer_extent)

        return page

    def _update_download_button_state(self) -> None:
        """Enable the Download button iff at least one row is selected."""
        selected = self.results_table.selectionModel().selectedRows()
        self.download_button.setEnabled(bool(selected))

    # ------------------------------------------------------------------
    # AOI helpers (canvas extent / draw rectangle / layer extent)
    # ------------------------------------------------------------------
    def _on_use_canvas_extent(self) -> None:
        """Fill AOI fields with the current map canvas extent (reprojected to WGS84)."""
        if self._iface is None:
            return
        try:
            canvas = self._iface.mapCanvas()
            extent = canvas.extent()
            src_crs = canvas.mapSettings().destinationCrs()
            self._fill_aoi_from_extent(extent, src_crs)
        except Exception as exc:
            log_warning(f"_on_use_canvas_extent failed: {exc}")
            QMessageBox.warning(self, "AOI", f"Could not read canvas extent: {exc}")

    def _on_use_layer_extent(self) -> None:
        """Fill AOI fields with the active layer's extent (reprojected to WGS84)."""
        if self._iface is None:
            return
        try:
            layer = self._iface.activeLayer()
            if layer is None:
                QMessageBox.information(
                    self, "AOI",
                    "No active layer. Select a layer in the Layers panel first.",
                )
                return
            extent = layer.extent()
            src_crs = layer.crs()
            self._fill_aoi_from_extent(extent, src_crs)
        except Exception as exc:
            log_warning(f"_on_use_layer_extent failed: {exc}")
            QMessageBox.warning(self, "AOI", f"Could not read layer extent: {exc}")

    def _on_draw_on_map_toggled(self, checked: bool) -> None:
        """Activate or deactivate the rectangle draw tool on the map canvas."""
        if self._iface is None:
            return
        canvas = self._iface.mapCanvas()
        if checked:
            if self._draw_tool is None:
                self._draw_tool = DrawnRectangleTool(canvas)
                self._draw_tool.extentDrawn.connect(self._on_extent_drawn)
                self._draw_tool.toolDeactivated.connect(self._on_draw_tool_deactivated)
            canvas.setMapTool(self._draw_tool)
            canvas.setFocus()
            # Hide the dialog so the user can see the whole canvas while
            # dragging. They can re-raise the dialog from the taskbar.
            self.showMinimized()
        else:
            canvas.unsetMapTool(self._draw_tool)

    def _on_extent_drawn(self, rect: "QgsRectangle", crs_authid: str) -> None:
        """Called when the user finishes dragging a rectangle on the canvas."""
        try:
            src_crs = QgsCoordinateReferenceSystem(crs_authid)
            self._fill_aoi_from_extent(rect, src_crs)
        except Exception as exc:
            log_warning(f"_on_extent_drawn failed: {exc}")
            QMessageBox.warning(self, "AOI", f"Could not apply drawn extent: {exc}")
        finally:
            # Bring the dialog back to the front so the user can hit Search.
            self.showNormal()
            self.raise_()
            self.activateWindow()
            # Uncheck the button to reflect that the tool is no longer active.
            self.aoi_draw_btn.setChecked(False)

    def _on_draw_tool_deactivated(self) -> None:
        """QGIS replaced our tool with another one — uncheck the button."""
        self.aoi_draw_btn.setChecked(False)
        # If the dialog was minimised while drawing, bring it back.
        if self.isMinimized():
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _fill_aoi_from_extent(self, extent: "QgsRectangle", src_crs: "QgsCoordinateReferenceSystem") -> None:
        """Reproject ``extent`` to EPSG:4326 and populate the four AOI line edits."""
        if extent is None or extent.isEmpty():
            QMessageBox.warning(self, "AOI", "The picked extent is empty.")
            return
        dst_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs.authid() == dst_crs.authid():
            wgs84_extent = extent
        else:
            transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
            wgs84_extent = transform.transformBoundingBox(extent)
        # Round to 6 decimals (~0.1 m at the equator) — keeps the UI tidy.
        self.min_lon_edit.setText(f"{wgs84_extent.xMinimum():.6f}")
        self.max_lon_edit.setText(f"{wgs84_extent.xMaximum():.6f}")
        self.min_lat_edit.setText(f"{wgs84_extent.yMinimum():.6f}")
        self.max_lat_edit.setText(f"{wgs84_extent.yMaximum():.6f}")
        log_info(
            f"AOI filled from {src_crs.authid()} extent: "
            f"lon=[{wgs84_extent.xMinimum():.4f}, {wgs84_extent.xMaximum():.4f}], "
            f"lat=[{wgs84_extent.yMinimum():.4f}, {wgs84_extent.yMaximum():.4f}]"
        )

    # ------------------------------------------------------------------
    # Tab 2 — Preprocess
    # ------------------------------------------------------------------
    def _build_preprocess_tab(self) -> QWidget:
        """Build the Preprocess tab."""
        page = QWidget(self)
        layout = QVBoxLayout(page)

        # ----- Input / Output group -----
        io_group = QGroupBox("Input / Output", page)
        io_layout = QVBoxLayout(io_group)

        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("Input folder:", page))
        self.prep_input_edit = QLineEdit(page)
        self.prep_input_edit.setPlaceholderText(
            "Folder with downloaded SAR scenes (*.tif)"
        )
        input_row.addWidget(self.prep_input_edit, 1)
        self.prep_input_browse_btn = QPushButton("Browse...", page)
        input_row.addWidget(self.prep_input_browse_btn)
        io_layout.addLayout(input_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output folder:", page))
        self.prep_output_edit = QLineEdit(page)
        self.prep_output_edit.setPlaceholderText(
            "Folder for preprocessed results"
        )
        output_row.addWidget(self.prep_output_edit, 1)
        self.prep_output_browse_btn = QPushButton("Browse...", page)
        output_row.addWidget(self.prep_output_browse_btn)
        io_layout.addLayout(output_row)
        layout.addWidget(io_group)

        # ----- Options group -----
        opts_group = QGroupBox("Processing Options", page)
        opts_layout = QVBoxLayout(opts_group)
        self.to_db_chk = QCheckBox("Convert to decibels (dB)", page)
        self.to_db_chk.setChecked(True)
        opts_layout.addWidget(self.to_db_chk)
        self.speckle_chk = QCheckBox("Apply speckle filter (Lee)", page)
        opts_layout.addWidget(self.speckle_chk)
        speckle_row = QHBoxLayout()
        speckle_row.addWidget(QLabel("Kernel size:", page))
        self.speckle_kernel_spin = QSpinBox(page)
        self.speckle_kernel_spin.setRange(3, 21)
        self.speckle_kernel_spin.setSingleStep(2)  # odd only
        self.speckle_kernel_spin.setValue(5)
        speckle_row.addWidget(self.speckle_kernel_spin)
        speckle_row.addStretch()
        opts_layout.addLayout(speckle_row)
        self.land_mask_chk = QCheckBox("Create land/water mask", page)
        opts_layout.addWidget(self.land_mask_chk)
        land_row = QHBoxLayout()
        land_row.addWidget(QLabel("Threshold (dB):", page))
        self.land_mask_threshold_spin = QDoubleSpinBox(page)
        self.land_mask_threshold_spin.setRange(-50.0, 0.0)
        self.land_mask_threshold_spin.setSingleStep(0.5)
        self.land_mask_threshold_spin.setValue(-20.0)
        land_row.addWidget(self.land_mask_threshold_spin)
        land_row.addStretch()
        opts_layout.addLayout(land_row)
        layout.addWidget(opts_group)

        # ----- File list -----
        files_group = QGroupBox("Files to Process", page)
        files_layout = QVBoxLayout(files_group)

        # Pol filter row: lets user hide VH or VV channels they don't want
        # to process. Default = Both.
        pol_filter_row = QHBoxLayout()
        pol_filter_row.addWidget(QLabel("Polarization:", page))
        self.prep_pol_filter_combo = QComboBox(page)
        self.prep_pol_filter_combo.addItems(["Both VV + VH", "VV only", "VH only"])
        self.prep_pol_filter_combo.setToolTip(
            "Filter the file list by polarization.\n"
            "Both VV + VH — show all measurement TIFFs (default).\n"
            "VV only — hide VH channel files.\n"
            "VH only — hide VV channel files.\n\n"
            "Tip: VV is co-pol (vertical transmit + vertical receive), "
            "best for general land/water analysis. VH is cross-pol, "
            "useful for ship/ice detection and some crop classifications."
        )
        pol_filter_row.addWidget(self.prep_pol_filter_combo)
        pol_filter_row.addStretch()
        files_layout.addLayout(pol_filter_row)

        # Helper note above the file list — explains what's shown.
        files_hint_label = QLabel(
            "<small>Only SAR measurement bands (VV/VH/HH/HV) are listed. "
            "Manifests, schemas, calibration tables and tie-points are hidden "
            "automatically.</small>",
            page,
        )
        files_hint_label.setWordWrap(True)
        files_hint_label.setStyleSheet("color: #666;")
        files_layout.addWidget(files_hint_label)

        self.prep_file_list = QListWidget(page)
        self.prep_file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        files_layout.addWidget(self.prep_file_list)
        refresh_row = QHBoxLayout()
        self.prep_refresh_btn = QPushButton("Refresh List", page)
        refresh_row.addWidget(self.prep_refresh_btn)
        refresh_row.addStretch()
        files_layout.addLayout(refresh_row)
        layout.addWidget(files_group, 1)

        # ----- Process button + progress + cancel -----
        proc_row = QHBoxLayout()
        self.prep_run_btn = QPushButton("Run Preprocessing", page)
        self.prep_progress = QProgressBar(page)
        self.prep_progress.setVisible(False)
        self.prep_cancel_btn = QPushButton("Cancel", page)
        self.prep_cancel_btn.setVisible(False)
        self.prep_cancel_btn.setToolTip("Abort the remaining preprocessing tasks.")
        proc_row.addWidget(self.prep_run_btn)
        proc_row.addWidget(self.prep_progress, 1)
        proc_row.addWidget(self.prep_cancel_btn)
        layout.addLayout(proc_row)

        # ----- Connections -----
        self.prep_input_browse_btn.clicked.connect(self._on_prep_browse_input)
        self.prep_output_browse_btn.clicked.connect(self._on_prep_browse_output)
        self.prep_refresh_btn.clicked.connect(self._on_prep_refresh)
        self.prep_run_btn.clicked.connect(self._on_prep_run)
        self.prep_cancel_btn.clicked.connect(self._on_prep_cancel)

        # Auto-refresh on input folder change or pol filter change
        self.prep_input_edit.textChanged.connect(self._on_prep_refresh)
        self.prep_pol_filter_combo.currentIndexChanged.connect(self._on_prep_refresh)

        return page

    # ----- Preprocess slots -----
    def _on_prep_browse_input(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select input folder", self.prep_input_edit.text() or "",
            QFileDialog.ShowDirsOnly,
        )
        if path:
            self.prep_input_edit.setText(path)

    def _on_prep_browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select output folder", self.prep_output_edit.text() or "",
            QFileDialog.ShowDirsOnly,
        )
        if path:
            self.prep_output_edit.setText(path)

    def _on_prep_refresh(self) -> None:
        """Populate the file list with SAR measurement TIFFs only.

        Recurses into subfolders (to support both flat Planetary Computer
        download layouts and classical .SAFE directory structures).

        What counts as "measurement":
            * File ends with .tif / .tiff
            * File name (without extension) contains a polarization token
              (vv / vh / hh / hv) surrounded by separators (start of name,
              "-", "_") on the left and (separator or end of name) on the
              right. This matches both Planetary Computer flat naming
              (``..._vv.tif``) and classical .SAFE naming
              (``s1a-iw-grd-vv-...001.tif``).
            * AND the file name does NOT contain any of the auxiliary
              markers: manifest, schema, calibration, noise, product,
              tiepoint, tie-point, annotation, preview, thumbnail,
              quick-look, quicklook, browse, report, summary, icon,
              overlay.

        The polarization combo further filters the result to VV only /
        VH only / both.
        """
        import re

        folder = self.prep_input_edit.text().strip()
        self.prep_file_list.clear()
        if not folder or not os.path.isdir(folder):
            return

        # Markers that indicate an auxiliary / metadata file, NOT a SAR
        # measurement band. Sentinel-1 .SAFE products and Planetary
        # Computer flat exports both ship these alongside the real bands.
        AUX_MARKERS = (
            "manifest", "schema", "calibration", "noise", "product",
            "tiepoint", "tie-point", "tie_point", "annotation",
            "preview", "thumbnail", "quick-look", "quicklook",
            "browse", "report", "summary", "icon", "overlay",
            "map-overlay", "ql.png",
        )

        # Regex that matches a polarization token surrounded by separators.
        # Examples that match:
        #   "..._vv.tif"     — _vv at end (Planetary Computer flat)
        #   "s1a-iw-grd-vv-20250101t...001.tif"  — -vv- in middle (classical SAFE)
        #   "vv.tif"         — vv at start
        # Examples that DON'T match:
        #   "vvpanel.tif"    — "vv" not delimited
        #   "schema-vv.tif"  — would match the regex but is rejected earlier
        #                       by the AUX_MARKERS check (contains "schema").
        POL_RE = re.compile(
            r"(?:^|[-_])(vv|vh|hh|hv)(?:[-_.]|$)",
            re.IGNORECASE,
        )

        # Get the pol-filter selection. Index 0 = both, 1 = VV, 2 = VH.
        try:
            pol_idx = self.prep_pol_filter_combo.currentIndex()
        except Exception:
            pol_idx = 0
        wanted_pol = None  # None = both
        if pol_idx == 1:
            wanted_pol = "vv"
        elif pol_idx == 2:
            wanted_pol = "vh"

        found: List[str] = []
        skipped_aux = 0
        skipped_pol_suffix = 0
        skipped_pol_filter = 0
        try:
            for root, dirs, files in os.walk(folder):
                dirs.sort()
                files.sort()
                for name in files:
                    lower = name.lower()
                    if not lower.endswith((".tif", ".tiff")):
                        continue
                    # Reject auxiliary / metadata files even if they have
                    # a .tif extension (Planetary Computer ships XML
                    # schemas with .tif suffix in some cases).
                    if any(m in lower for m in AUX_MARKERS):
                        skipped_aux += 1
                        continue
                    # Require the name to contain a polarization token.
                    stem = os.path.splitext(name)[0]
                    m = POL_RE.search(stem)
                    if m is None:
                        skipped_pol_suffix += 1
                        continue
                    matched_pol = m.group(1).lower()
                    # Apply user's pol-filter selection.
                    if wanted_pol is not None and matched_pol != wanted_pol:
                        skipped_pol_filter += 1
                        continue
                    found.append(os.path.join(root, name))
        except OSError as exc:
            log_warning(f"Cannot walk input folder: {exc}")
            return

        for path in found:
            self.prep_file_list.addItem(path)

        log_info(
            f"Preprocess file list refreshed: {len(found)} measurement TIFF(s) "
            f"under '{folder}' "
            f"(skipped: {skipped_aux} aux, {skipped_pol_suffix} non-pol-suffix, "
            f"{skipped_pol_filter} filtered by pol choice)"
        )

    def _on_prep_run(self) -> None:
        """Run preprocessing on the selected files."""
        if self._active_preprocess_tasks:
            QMessageBox.information(
                self, "Busy", "Preprocessing is already running. Please wait."
            )
            return

        input_folder = self.prep_input_edit.text().strip()
        output_folder = self.prep_output_edit.text().strip()
        if not input_folder or not os.path.isdir(input_folder):
            QMessageBox.warning(self, "Invalid input", "Please choose a valid input folder.")
            return
        if not output_folder:
            QMessageBox.warning(self, "Invalid output", "Please choose an output folder.")
            return
        try:
            os.makedirs(output_folder, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self, "Output error",
                f"Cannot create output folder:\n{output_folder}\n\n{exc}",
            )
            return

        selected_items = self.prep_file_list.selectedItems()
        if not selected_items:
            # Fall back to all listed files
            selected_items = [
                self.prep_file_list.item(i)
                for i in range(self.prep_file_list.count())
            ]
        if not selected_items:
            QMessageBox.information(
                self, "No files",
                "No measurement .tif/.tiff files found in the input folder.\n\n"
                "If you downloaded a SAFE product, point the input folder at the "
                "extracted .SAFE folder (the plugin will find the measurement "
                "TIFFs inside).",
            )
            return

        to_db = self.to_db_chk.isChecked()
        speckle = self.speckle_chk.isChecked()
        kernel = self.speckle_kernel_spin.value()
        land_mask = self.land_mask_chk.isChecked()
        threshold = self.land_mask_threshold_spin.value()

        # Build tasks
        self._active_preprocess_tasks.clear()
        self._prep_failure_messages: List[str] = []
        self._prep_success_count = 0
        for item in selected_items:
            input_path = item.text()
            base = os.path.basename(input_path)
            stem, _ = os.path.splitext(base)
            if land_mask:
                out_name = f"{stem}_mask.tif"
            else:
                suffix = []
                if to_db:
                    suffix.append("db")
                if speckle:
                    suffix.append(f"lee{kernel}")
                out_name = (stem + "_" + "_".join(suffix) + ".tif") if suffix else f"{stem}_out.tif"
            output_path = os.path.join(output_folder, out_name)

            task = PreprocessTask(
                description=f"Preprocessing {base}",
                input_path=input_path,
                output_path=output_path,
                to_db=to_db,
                speckle=speckle,
                speckle_kernel=kernel,
                land_mask=land_mask,
                land_mask_threshold_db=threshold,
            )
            task.taskCompleted.connect(lambda t=task: self._on_prep_task_finished(t, True))
            task.taskTerminated.connect(lambda t=task: self._on_prep_task_finished(t, False))
            self._active_preprocess_tasks.append(task)

        # UI feedback
        self.prep_run_btn.setEnabled(False)
        self.prep_progress.setVisible(True)
        self.prep_cancel_btn.setVisible(True)
        self.prep_progress.setRange(0, len(self._active_preprocess_tasks))
        self.prep_progress.setValue(0)
        log_info(
            f"Preprocessing started: {len(self._active_preprocess_tasks)} file(s) "
            f"→ output folder '{output_folder}'"
        )
        for task in self._active_preprocess_tasks:
            QgsApplication.taskManager().addTask(task)

    def _on_prep_task_finished(self, task: "PreprocessTask", success: bool) -> None:
        """Called when a single preprocessing task completes."""
        if success:
            self._prep_success_count += 1
            log_info(f"OK: {task.input_path} → {task.output_path}")
            # Load the freshly written GeoTIFF into the project so the user
            # doesn't have to hunt for it via Layer → Add Raster Layer.
            if self.add_to_map_chk.isChecked():
                self._add_raster_to_map(
                    task.output_path, os.path.basename(task.output_path)
                )
        else:
            err = task.exception
            msg = str(err) if err is not None else "unknown error"
            self._prep_failure_messages.append(f"{os.path.basename(task.input_path)}: {msg}")
            if err is not None:
                log_warning(f"FAIL: {task.input_path}: {msg}\n{format_exception(err)}")
            else:
                log_warning(f"FAIL: {task.input_path}: {msg}")
        try:
            self._active_preprocess_tasks.remove(task)
        except ValueError:
            pass

        total = self.prep_progress.maximum() or 1
        remaining = len(self._active_preprocess_tasks)
        self.prep_progress.setValue(total - remaining)

        if not self._active_preprocess_tasks:
            self.prep_run_btn.setEnabled(True)
            self.prep_progress.setVisible(False)
            self.prep_cancel_btn.setVisible(False)

            ok = self._prep_success_count
            fail = len(self._prep_failure_messages)
            if fail == 0:
                msg = f"All {ok} file(s) processed successfully."
            else:
                preview = "\n".join(self._prep_failure_messages[:5])
                more = "" if fail <= 5 else f"\n... and {fail - 5} more"
                msg = (
                    f"Done: {ok} succeeded, {fail} failed.\n\n"
                    f"Failed files:\n{preview}{more}\n\n"
                    f"See QGIS Log Panel (tag: Sentinel1SAR) for details."
                )
            log_info(f"Preprocessing finished: {ok} ok, {fail} fail")
            if fail > 0:
                QMessageBox.warning(self, "Preprocessing finished with errors", msg)
            else:
                QMessageBox.information(self, "Preprocessing complete", msg)

    # ------------------------------------------------------------------
    # Tab 3 — Windthrow Detection
    # ------------------------------------------------------------------
    def _build_windthrow_tab(self) -> QWidget:
        """Build the Windthrow Detection tab (bi-temporal WI method).

        Implements the rapid detection chain of Rüetschi et al. (2019):
        pre/post composites per polarisation → image differencing →
        WI = dVV + dVH (dB) → adaptive or fixed threshold → minimum
        object size filter → WI raster + mask + vector output.
        """
        page = QWidget(self)
        layout = QVBoxLayout(page)

        # ----- Method summary -----
        method_hint = QLabel(
            "<small><b>Method — Rüetschi et al. 2019 (Remote Sensing 11(2):115):</b> "
            "per-polarisation pre/post composites → image differencing → "
            "<b>WI = (VV<sub>post</sub> − VV<sub>pre</sub>) + "
            "(VH<sub>post</sub> − VH<sub>pre</sub>)</b> in dB. Windthrown "
            "forest scatters <b>more</b> after the storm (chaotic trunks and "
            "branches), so damaged areas show a <b>positive</b> WI. A pixel "
            "is flagged when WI &gt; mean(WI) + a (adaptive) or WI &gt; a "
            "fixed value; objects smaller than n pixels are discarded. "
            "Use 1–5 pre-storm and 1–3 post-storm scenes of the <b>same "
            "orbit direction</b>, one window ≤ 2–3 weeks each. Inputs may be "
            "raw downloads (auto-converted to dB) or Preprocess-tab outputs "
            "(recommended).</small>",
            page,
        )
        method_hint.setWordWrap(True)
        method_hint.setStyleSheet("color: #555;")
        self.wt_method_hint = method_hint
        layout.addWidget(method_hint)

        # ----- Pre-storm / post-storm file stacks -----
        stacks_row = QHBoxLayout()
        self.wt_pre_list, pre_group = self._build_stack_group(
            "Pre-storm images (before the storm)",
            "SAR GeoTIFFs of the pre-storm window.\n"
            "VV and VH files of one or several dates; several files of the "
            "same polarisation are combined into a median composite.",
            page,
        )
        self.wt_post_list, post_group = self._build_stack_group(
            "Post-storm images (after the storm)",
            "SAR GeoTIFFs of the post-storm window (storm + 1 day … + 2 weeks).\n"
            "The first file defines the output grid; other inputs are warped "
            "onto it if needed.",
            page,
        )
        self.wt_pre_group = pre_group
        self.wt_post_group = post_group
        stacks_row.addWidget(pre_group, 1)
        stacks_row.addWidget(post_group, 1)
        layout.addLayout(stacks_row)

        # ----- Coherence products (v1.0, method == "coh") -----
        # Hidden unless the Coherence DiD method is selected; the
        # pre/post stack lists above are hidden in that case instead.
        coh_group = QGroupBox("HyP3 InSAR products (unpacked folder, .zip or *_corr.tif)", page)
        coh_form = QFormLayout(coh_group)
        self.wt_coh_prepost_edit = QLineEdit(coh_group)
        self.wt_coh_prepost_edit.setPlaceholderText(
            "Pre/post pair product — the damage window, e.g. "
            "id694-coh-prepost/ (contains *_corr.tif)"
        )
        coh_prepost_browse = QPushButton("Browse...", coh_group)
        coh_prepost_row = QHBoxLayout()
        coh_prepost_row.addWidget(self.wt_coh_prepost_edit, 1)
        coh_prepost_row.addWidget(coh_prepost_browse)
        coh_form.addRow("Pre/post pair:", coh_prepost_row)
        self.wt_coh_control_edit = QLineEdit(coh_group)
        self.wt_coh_control_edit.setPlaceholderText(
            "Control pair product (same frames, outside the damage "
            "window) — optional but strongly recommended"
        )
        coh_control_browse = QPushButton("Browse...", coh_group)
        coh_control_row = QHBoxLayout()
        coh_control_row.addWidget(self.wt_coh_control_edit, 1)
        coh_control_row.addWidget(coh_control_browse)
        coh_form.addRow("Control pair:", coh_control_row)

        self.wt_coh_a_spin = QDoubleSpinBox(coh_group)
        self.wt_coh_a_spin.setRange(0.0, 1.0)
        self.wt_coh_a_spin.setSingleStep(0.01)
        self.wt_coh_a_spin.setValue(0.25)
        self.wt_coh_a_spin.setToolTip(
            "Offset above the background MEDIAN of dcoh (coherence units, "
            "not dB). On the validated events 0.25 keeps the false-alarm "
            "rate near 8-14 %; lower values flood the output on scenes "
            "with seasonal coherence drift (e.g. autumn freeze-up)."
        )
        coh_form.addRow("Offset a (adaptive):", self.wt_coh_a_spin)
        self.wt_coh_fixed_spin = QDoubleSpinBox(coh_group)
        self.wt_coh_fixed_spin.setRange(-1.0, 1.0)
        self.wt_coh_fixed_spin.setSingleStep(0.05)
        self.wt_coh_fixed_spin.setValue(0.25)
        self.wt_coh_fixed_spin.setToolTip(
            "Absolute dcoh threshold (coherence units). Only pixels "
            "where the control pair is more coherent than the pre/post "
            "pair by this margin are flagged."
        )
        coh_form.addRow("Fixed threshold:", self.wt_coh_fixed_spin)
        coh_hint = QLabel(
            "<small>dcoh = coh(control) − coh(prepost) is <b>positive</b> "
            "over windthrow. The control pair removes static low-coherence "
            "anomalies and seasonal drift. A corrupt product water mask "
            "(&gt; 50 % water) is ignored automatically. Default min "
            "object size is 6 px — one 80 m pixel covers 0.64 ha.</small>",
            coh_group,
        )
        coh_hint.setWordWrap(True)
        coh_hint.setStyleSheet("color: #555;")
        coh_form.addRow("", coh_hint)
        coh_prepost_browse.clicked.connect(self._on_wt_browse_coh_prepost)
        coh_control_browse.clicked.connect(self._on_wt_browse_coh_control)
        coh_group.setVisible(False)
        self.wt_coh_group = coh_group
        layout.addWidget(coh_group)

        # ----- Parameters -----
        params_group = QGroupBox("Detection Parameters", page)
        params_form = QFormLayout(params_group)

        # Detection method (v1.0): C-band WI, L-band decline or
        # coherence DiD — the two new modes reuse the same threshold /
        # object-size / output machinery.
        self.wt_method_combo = QComboBox(page)
        self.wt_method_combo.addItem(
            "Sentinel-1 C-band WI (Rüetschi 2019)", "wi")
        self.wt_method_combo.addItem(
            "L-band decline — PALSAR (Tanase 2018)", "lband")
        self.wt_method_combo.addItem(
            "Coherence DiD — HyP3 InSAR (step12b)", "coh")
        self.wt_method_combo.setToolTip(
            "C-band WI: amplitude increase on Sentinel-1 (default).\n"
            "L-band decline: backscatter drop on ALOS PALSAR — the "
            "opposite sign, validated invAUC up to 0.905.\n"
            "Coherence DiD: coherence drop between the pre/post InSAR "
            "pair relative to a same-season control pair — validated "
            "AUC 0.908 on the 2017 tornado event."
        )
        params_form.addRow("Detection method:", self.wt_method_combo)

        self.wt_mode_combo = QComboBox(page)
        self.wt_mode_combo.addItem(
            "Adaptive — mean(WI) + a  (Rüetschi 2019)", "adaptive")
        self.wt_mode_combo.addItem("Fixed WI threshold", "fixed")
        self.wt_mode_combo.setToolTip(
            "Adaptive: the threshold follows the scene's mean WI inside the "
            "analysis mask (or the whole scene), which compensates changing "
            "weather/wind conditions between dates. Fixed: one absolute WI "
            "value for all scenes — useful when iterating on a known event."
        )
        params_form.addRow("Threshold mode:", self.wt_mode_combo)

        self.wt_a_spin = QDoubleSpinBox(page)
        self.wt_a_spin.setRange(0.0, 10.0)
        self.wt_a_spin.setSingleStep(0.1)
        self.wt_a_spin.setValue(2.9)
        self.wt_a_spin.setSuffix(" dB")
        self.wt_a_spin.setToolTip(
            "Offset above the mean WI. The paper's optimum was a = 2.9 dB "
            "(tested range 2.8–3.35): lower a → more detected areas but "
            "more false alarms; higher a → fewer, more reliable objects."
        )
        params_form.addRow("Offset a (adaptive):", self.wt_a_spin)

        self.wt_fixed_spin = QDoubleSpinBox(page)
        self.wt_fixed_spin.setRange(-20.0, 20.0)
        self.wt_fixed_spin.setSingleStep(0.5)
        self.wt_fixed_spin.setValue(3.0)
        self.wt_fixed_spin.setSuffix(" dB")
        self.wt_fixed_spin.setToolTip(
            "Absolute WI threshold. Positive values flag backscatter "
            "INCREASES (windthrow). Typical working range +2…+5 dB."
        )
        params_form.addRow("Fixed threshold:", self.wt_fixed_spin)

        self.wt_median_combo = QComboBox(page)
        self.wt_median_combo.addItem("None", 0)
        self.wt_median_combo.addItem("3 × 3 (recommended)", 3)
        self.wt_median_combo.addItem("5 × 5", 5)
        self.wt_median_combo.addItem("7 × 7", 7)
        self.wt_median_combo.setCurrentIndex(1)
        self.wt_median_combo.setToolTip(
            "Median filter on WI before thresholding — suppresses isolated "
            "speckle spikes and consolidates windthrow edges."
        )
        params_form.addRow("Median filter on WI:", self.wt_median_combo)

        self.wt_min_px_spin = QSpinBox(page)
        self.wt_min_px_spin.setRange(1, 100000)
        self.wt_min_px_spin.setValue(27)
        self.wt_min_px_spin.setToolTip(
            "Minimum number of flagged pixels per object (8-connected). "
            "The paper's optimum n = 27 px ≈ 0.27 ha at 10 m pixel size. "
            "The reference datasets in the paper start at 0.5 ha."
        )
        params_form.addRow("Min object size (px):", self.wt_min_px_spin)

        # Background normalization (v0.8)
        self.wt_norm_chk = QCheckBox(
            "Background normalization (remove weather shift)", page)
        self.wt_norm_chk.setChecked(True)
        self.wt_norm_chk.setToolTip(
            "v0.8: compensates weather-driven radiometric changes (wet soil "
            "after rain, snowmelt, vegetation growth) that shift the whole "
            "post-storm image and flood the detection with false alarms. "
            "Per polarisation, the median difference post − pre inside the "
            "analysis mask is subtracted from the post image before "
            "computing WI. Recommended ON for rain-affected pairs. The WI "
            "output becomes <base>_wi_norm.tif."
        )
        params_form.addRow("", self.wt_norm_chk)

        # Analysis mask (forest restriction, optional)
        self.wt_mask_chk = QCheckBox("Restrict detection to forest mask", page)
        self.wt_mask_chk.setToolTip(
            "Optional forest mask: detections and the adaptive-threshold "
            "mean are computed only inside it — exactly like the forest "
            "mask in the paper, and it removes most false alarms over "
            "agricultural fields and clearcuts."
        )
        params_form.addRow("", self.wt_mask_chk)
        self.wt_mask_source_combo = QComboBox(page)
        self.wt_mask_source_combo.addItem(
            "Custom file (raster / vector)", "file")
        self.wt_mask_source_combo.addItem(
            "ESA WorldCover 10 m (auto-download)", "worldcover")
        self.wt_mask_source_combo.setToolTip(
            "v0.9 forest mask source. \u2018Custom file\u2019 uses your own "
            "raster (values &gt; 0 = forest) or vector layer. \u2018ESA "
            "WorldCover\u2019 searches the esa-worldcover collection on "
            "Planetary Computer for the AOI, takes the 10 m Tree-cover "
            "class and resamples it onto the radar grid — no local "
            "land-cover file needed."
        )
        params_form.addRow("Mask source:", self.wt_mask_source_combo)
        self.wt_wc_year_combo = QComboBox(page)
        for _year in (2021, 2020):
            self.wt_wc_year_combo.addItem(str(_year), _year)
        self.wt_wc_year_combo.setToolTip(
            "ESA WorldCover map epoch. Pick the year closest to (but not "
            "after) the storm event — regrowth may reclassify old "
            "windthrows as shrub/grass in later epochs."
        )
        self.wt_wc_year_combo.setEnabled(False)
        params_form.addRow("WorldCover year:", self.wt_wc_year_combo)
        self.wt_mask_edit = QLineEdit(page)
        self.wt_mask_edit.setPlaceholderText(
            "Forest mask / AOI (GeoTIFF, GeoPackage, Shapefile…)"
        )
        wt_mask_browse = QPushButton("Browse...", page)
        wt_mask_row = QHBoxLayout()
        wt_mask_row.addWidget(self.wt_mask_edit, 1)
        wt_mask_row.addWidget(wt_mask_browse)
        params_form.addRow("Mask file:", wt_mask_row)
        wt_mask_browse.clicked.connect(self._on_wt_browse_mask)
        self.wt_mask_edit.setEnabled(False)

        def _sync_mask_source() -> None:
            """Enable exactly the widgets of the active mask source."""
            is_file = (
                self.wt_mask_source_combo.currentData() or "file") == "file"
            on = self.wt_mask_chk.isChecked()
            self.wt_mask_edit.setEnabled(on and is_file)
            wt_mask_browse.setEnabled(on and is_file)
            self.wt_wc_year_combo.setEnabled(on and not is_file)

        self.wt_mask_chk.toggled.connect(_sync_mask_source)
        self.wt_mask_source_combo.currentIndexChanged.connect(
            _sync_mask_source)

        layout.addWidget(params_group)

        # ----- Output -----
        out_group = QGroupBox("Output", page)
        out_form = QFormLayout(out_group)
        self.wt_output_edit = QLineEdit(page)
        self.wt_output_edit.setPlaceholderText(
            "Output base path, e.g. D:/windthrow/karelia_2017"
        )
        wt_out_browse = QPushButton("Browse...", page)
        wt_out_row = QHBoxLayout()
        wt_out_row.addWidget(self.wt_output_edit, 1)
        wt_out_row.addWidget(wt_out_browse)
        out_form.addRow("Output base:", wt_out_row)
        wt_out_browse.clicked.connect(self._on_wt_browse_output)
        out_hint = QLabel(
            "<small>Writes <code>&lt;base&gt;_wi.tif</code> (Windthrow Index, dB) "
            "or <code>&lt;base&gt;_wi_norm.tif</code> (with background "
            "normalization), "
            "<code>&lt;base&gt;_mask.tif</code> (uint8 mask), "
            "<code>&lt;base&gt;.gpkg</code> (polygons, attribute "
            "<code>area_ha</code>), and when several scenes are composited, "
            "<code>&lt;base&gt;_pre_&lt;pol&gt;.tif</code> / "
            "<code>&lt;base&gt;_post_&lt;pol&gt;.tif</code>; in WorldCover "
            "mask mode also <code>&lt;base&gt;_forest_wc&lt;year&gt;.tif</code> "
            "(the forest mask).</small>",
            page,
        )
        out_hint.setWordWrap(True)
        out_hint.setStyleSheet("color: #666;")
        out_form.addRow("", out_hint)
        layout.addWidget(out_group)

        # ----- Run + progress + cancel -----
        run_row = QHBoxLayout()
        self.analysis_run_btn = QPushButton("Run Detection", page)
        self.analysis_progress = QProgressBar(page)
        self.analysis_progress.setVisible(False)
        self.analysis_cancel_btn = QPushButton("Cancel", page)
        self.analysis_cancel_btn.setVisible(False)
        self.analysis_cancel_btn.setToolTip("Abort the running detection.")
        run_row.addWidget(self.analysis_run_btn)
        run_row.addWidget(self.analysis_progress, 1)
        run_row.addWidget(self.analysis_cancel_btn)
        layout.addLayout(run_row)

        # ----- Connections -----
        self.wt_mode_combo.currentIndexChanged.connect(
            self._on_wt_mode_changed
        )
        self.wt_method_combo.currentIndexChanged.connect(
            self._sync_method_widgets
        )
        self.wt_mode_combo.currentIndexChanged.connect(
            self._sync_method_widgets
        )
        self.analysis_run_btn.clicked.connect(self._on_windthrow_run)
        self.analysis_cancel_btn.clicked.connect(self._on_windthrow_cancel)
        self._sync_method_widgets()

        return page

    # ----- Windthrow helpers -----
    def _build_stack_group(
        self, title: str, tooltip: str, parent: QWidget
    ) -> Tuple[QListWidget, QGroupBox]:
        """Build one pre/post file-stack group: list + add/remove buttons."""
        group = QGroupBox(title, parent)
        group.setToolTip(tooltip)
        vbox = QVBoxLayout(group)
        lst = QListWidget(parent)
        lst.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lst.setToolTip(tooltip)
        vbox.addWidget(lst)
        row = QHBoxLayout()
        add_btn = QPushButton("Add files...", parent)
        add_dir_btn = QPushButton("Add folder...", parent)
        rm_btn = QPushButton("Remove selected", parent)
        clr_btn = QPushButton("Clear", parent)
        for b in (add_btn, add_dir_btn, rm_btn, clr_btn):
            row.addWidget(b)
        row.addStretch()
        vbox.addLayout(row)

        def _add_paths(paths) -> None:
            existing = {lst.item(i).data(Qt.UserRole)
                        for i in range(lst.count())}
            for p in paths:
                if p in existing:
                    continue
                pol = extract_polarization(p)
                label = f"{os.path.basename(p)}   [{pol or '?'}]"
                item = QListWidgetItem(label, lst)
                item.setData(Qt.UserRole, p)
                item.setToolTip(p)

        add_btn.clicked.connect(
            lambda: _add_paths(
                QFileDialog.getOpenFileNames(
                    lst, "Select SAR GeoTIFF files", "",
                    "GeoTIFF (*.tif *.tiff);;All files (*)",
                )[0]
            )
        )
        add_dir_btn.clicked.connect(
            lambda: _add_paths(_list_measurement_tifs(
                QFileDialog.getExistingDirectory(
                    lst, "Select folder with SAR GeoTIFFs",
                    self.prep_input_edit.text() or "",
                    QFileDialog.ShowDirsOnly,
                )
            ))
        )
        rm_btn.clicked.connect(self._remove_selected_items(lst))
        clr_btn.clicked.connect(lst.clear)
        return lst, group

    @staticmethod
    def _remove_selected_items(lst: QListWidget):
        """Return a slot that removes the currently selected list items."""

        def _remove() -> None:
            for item in lst.selectedItems():
                lst.takeItem(lst.row(item))

        return _remove

    def _on_wt_mode_changed(self) -> None:
        """Enable the parameter field matching the threshold mode."""
        adaptive = self.wt_mode_combo.currentData() == "adaptive"
        self.wt_a_spin.setEnabled(adaptive)
        self.wt_fixed_spin.setEnabled(not adaptive)

    # ----- Detection method switching (v1.0) -----
    _METHOD_HINTS = {
        "wi":
            "<small><b>Method — Rüetschi et al. 2019 (Remote Sensing "
            "11(2):115):</b> per-polarisation pre/post composites → image "
            "differencing → <b>WI = (VV<sub>post</sub> − VV<sub>pre</sub>) "
            "+ (VH<sub>post</sub> − VH<sub>pre</sub>)</b> in dB. "
            "Windthrown forest scatters <b>more</b> after the storm "
            "(chaotic trunks and branches), so damaged areas show a "
            "<b>positive</b> WI. A pixel is flagged when WI &gt; mean(WI) "
            "+ a (adaptive) or WI &gt; a fixed value; objects smaller "
            "than n pixels are discarded. Use 1–5 pre-storm and 1–3 "
            "post-storm scenes of the <b>same orbit direction</b>, one "
            "window ≤ 2–3 weeks each.</small>",
        "lband":
            "<small><b>Method — Tanase et al. 2018 (RSE 209:700–711):</b> "
            "L-band (ALOS PALSAR / PALSAR-2) penetrates the canopy, so a "
            "flattened stand LOSES volume scattering: the decline index "
            "<b>LDI = (HH<sub>pre</sub> − HH<sub>post</sub>) + "
            "(HV<sub>pre</sub> − HV<sub>post</sub>)</b> in dB is "
            "<b>positive</b> over windthrow — the opposite sign of the "
            "C-band WI. Validated on the 2017 events: invAUC 0.870 (dHH, "
            "squall line) and 0.905 (dHV, tornado). Annual PALSAR mosaics "
            "work out of the box; a forest mask is strongly recommended "
            "(regrowth fields confuse the threshold).</small>",
        "coh":
            "<small><b>Method — coherence DiD (project step12b, 2026):</b> "
            "interferometric coherence of the pre/post pair (HyP3 "
            "INSAR-GAMMA 80 m) drops over disturbed forest. "
            "<b>dcoh = coh(control) − coh(prepost)</b> cancels static "
            "anomalies and seasonal drift and is <b>positive</b> over "
            "windthrow. Validated: AUC 0.908 on the 2017 tornado (161 ha) "
            "— the strongest C-band result of the project. Supply a "
            "control pair of the SAME frames from outside the damage "
            "window for robust results.</small>",
    }

    def _sync_method_widgets(self) -> None:
        """Show exactly the inputs of the active detection method."""
        method = self.wt_method_combo.currentData() or "wi"
        is_coh = method == "coh"
        is_radar = method in ("wi", "lband")
        adaptive = self.wt_mode_combo.currentData() == "adaptive"
        # Method description
        hint = self._METHOD_HINTS.get(method)
        if hint:
            self.wt_method_hint.setText(hint)
        # Input groups
        self.wt_pre_group.setVisible(is_radar)
        self.wt_post_group.setVisible(is_radar)
        self.wt_coh_group.setVisible(is_coh)
        # Threshold widgets: dB offsets for radar modes, coherence
        # offsets live inside the coherence group.
        self.wt_a_spin.setEnabled(is_radar and adaptive)
        self.wt_fixed_spin.setEnabled(is_radar and not adaptive)
        self.wt_coh_a_spin.setEnabled(adaptive)
        self.wt_coh_fixed_spin.setEnabled(not adaptive)
        self.wt_norm_chk.setEnabled(is_radar)
        # The WorldCover auto-mask needs a post image on the radar grid;
        # coherence products use the 80 m InSAR grid instead.
        wc_index = 1
        if not is_radar:
            self.wt_mask_source_combo.setCurrentIndex(0)
        self.wt_mask_source_combo.model().item(wc_index).setEnabled(is_radar)
        self.wt_mask_source_combo.setEnabled(is_radar)
        # Minimum object size: 27 px @10 m ≈ 6 px @80 m.
        if is_coh and self.wt_min_px_spin.value() == 27:
            self.wt_min_px_spin.setValue(6)
        elif not is_coh and self.wt_min_px_spin.value() == 6:
            self.wt_min_px_spin.setValue(27)

    def _on_wt_browse_coh_prepost(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select the pre/post pair product (unpacked HyP3 folder)")
        if path:
            self.wt_coh_prepost_edit.setText(path)

    def _on_wt_browse_coh_control(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select the control pair product (unpacked HyP3 folder)")
        if path:
            self.wt_coh_control_edit.setText(path)

    def _on_wt_browse_mask(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select analysis mask (raster or vector)", "",
            "All supported (*.tif *.tiff *.gpkg *.shp *.geojson);;"
            "All files (*)",
        )
        if path:
            self.wt_mask_edit.setText(path)

    def _on_wt_browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Select output base (extension .gpkg or .shp)", "",
            "GeoPackage output (*.gpkg);;Shapefile output (*.shp);;"
            "All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith((".gpkg", ".shp")):
            path = path + ".gpkg"
        self.wt_output_edit.setText(path)

    def _on_windthrow_run(self) -> None:
        """Validate the tab inputs and start the detection task."""
        if self._active_analysis_task is not None:
            QMessageBox.information(
                self, "Busy", "A detection is already running. Please wait."
            )
            return

        method = self.wt_method_combo.currentData() or "wi"
        if method == "coh":
            return self._run_coh_detection()

        pre_paths = [
            self.wt_pre_list.item(i).data(Qt.UserRole)
            for i in range(self.wt_pre_list.count())
        ]
        post_paths = [
            self.wt_post_list.item(i).data(Qt.UserRole)
            for i in range(self.wt_post_list.count())
        ]
        if not pre_paths or not post_paths:
            QMessageBox.warning(
                self, "Missing images",
                "Add at least one pre-storm AND one post-storm image.",
            )
            return
        pols = common_polarizations(pre_paths, post_paths)
        if not pols:
            QMessageBox.warning(
                self, "No common polarisation",
                "The pre- and post-storm file names have no polarisation "
                "token (vv/vh/hh/hv) in common.\n"
                "Make sure the file names contain the channel token "
                "(e.g. ..._vv.tif and ..._vh.tif).",
            )
            return

        output_base = self.wt_output_edit.text().strip()
        if not output_base:
            QMessageBox.warning(
                self, "Missing output", "Please select an output base path."
            )
            return
        if output_base.lower().endswith((".gpkg", ".shp", ".tif", ".tiff")):
            output_base = os.path.splitext(output_base)[0]

        mask_path = None
        mask_source = "file"
        wc_year = 2020
        if self.wt_mask_chk.isChecked():
            mask_source = self.wt_mask_source_combo.currentData() or "file"
            if mask_source == "file":
                mask_path = self.wt_mask_edit.text().strip()
                if not mask_path or not os.path.isfile(mask_path):
                    QMessageBox.warning(
                        self, "Invalid mask",
                        "The forest mask file does not exist. Uncheck the "
                        "mask option or pick a valid file.",
                    )
                    return
            else:
                wc_year = int(self.wt_wc_year_combo.currentData() or 2020)

        common_detector_kwargs = dict(
            threshold_mode=self.wt_mode_combo.currentData() or "adaptive",
            a_db=self.wt_a_spin.value(),
            fixed_threshold_db=self.wt_fixed_spin.value(),
            min_pixels=self.wt_min_px_spin.value(),
            median_filter_size=int(self.wt_median_combo.currentData() or 0),
            normalize_background=self.wt_norm_chk.isChecked(),
        )
        if method == "lband":
            detector = LbandDeclineDetector(**common_detector_kwargs)
        else:
            detector = WindthrowDetector(**common_detector_kwargs)
        pol_label = "+".join(pols)

        # The task object is created after the closure; resolve it lazily
        # so the detector can report progress / honour cancellation.
        holder: dict = {}

        def _work() -> str:
            task = holder.get("task")
            progress_cb = (
                (lambda f, m: task.setProgress(float(f))) if task else None
            )
            cancel_cb = task.isCanceled if task else None
            # v0.9: auto-download of the forest mask (ESA WorldCover)
            # takes ~0-20% of the progress bar, the detection the rest.
            forest_path = None
            if mask_source == "worldcover":
                ref_path = pair_by_polarization(post_paths)[pols[0]][0]
                ref_info = forest_mask.read_ref_info(ref_path)
                bbox = forest_mask.bbox_4326(ref_info)
                forest_path = forest_mask.build_forest_mask(
                    "worldcover",
                    ref_info,
                    f"{output_base}_forest_wc{wc_year}.tif",
                    bbox=bbox,
                    year=wc_year,
                    progress_cb=(
                        (lambda f, m: task.setProgress(20.0 * float(f) / 100.0))
                        if task else None),
                    cancel_cb=cancel_cb,
                )
            detect_progress = (
                (lambda f, m: task.setProgress(
                    20.0 + 0.8 * float(f))) if task else None)
            return detector.detect_file(
                pre_paths=pre_paths,
                post_paths=post_paths,
                output_base=output_base,
                analysis_mask_path=mask_path,
                progress_cb=detect_progress,
                cancel_cb=cancel_cb,
                forest_mask_path=forest_path,
            )

        mask_label = {
            "file": "forest mask file",
            "worldcover": f"WorldCover {wc_year}",
        }.get(mask_source, "") if self.wt_mask_chk.isChecked() else ""
        desc = (f"Windthrow detection ({pol_label}, "
                f"{os.path.basename(output_base)}"
                + (f", {mask_label}" if mask_label else "") + ")")
        task = AnalysisTask(description=desc, work=_work)
        holder["task"] = task
        task.detector = detector
        task.taskCompleted.connect(
            lambda: self._on_windthrow_finished(task, True))
        task.taskTerminated.connect(
            lambda: self._on_windthrow_finished(task, False))

        self._active_analysis_task = task
        self.analysis_run_btn.setEnabled(False)
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setRange(0, 100)
        self.analysis_progress.setValue(0)
        self.analysis_cancel_btn.setVisible(True)
        log_info(f"Windthrow detection started: {desc}")
        QgsApplication.taskManager().addTask(task)

    def _run_coh_detection(self) -> None:
        """Validate the coherence-DiD inputs and start the task (v1.0)."""
        prepost = self.wt_coh_prepost_edit.text().strip()
        control = self.wt_coh_control_edit.text().strip()
        if not prepost:
            QMessageBox.warning(
                self, "Missing product",
                "Select the pre/post pair product (an unpacked HyP3 "
                "folder containing *_corr.tif, a .zip or the layer path).",
            )
            return
        if not os.path.exists(prepost):
            QMessageBox.warning(
                self, "Invalid product",
                f"The pre/post product path does not exist:\n{prepost}")
            return
        if control and not os.path.exists(control):
            QMessageBox.warning(
                self, "Invalid product",
                f"The control product path does not exist:\n{control}")
            return
        output_base = self.wt_output_edit.text().strip()
        if not output_base:
            QMessageBox.warning(
                self, "Missing output", "Please select an output base path.")
            return
        if output_base.lower().endswith((".gpkg", ".shp", ".tif", ".tiff")):
            output_base = os.path.splitext(output_base)[0]

        mask_path = None
        if (self.wt_mask_chk.isChecked()
                and (self.wt_mask_source_combo.currentData() or "file") == "file"):
            mask_path = self.wt_mask_edit.text().strip()
            if not mask_path or not os.path.isfile(mask_path):
                QMessageBox.warning(
                    self, "Invalid mask",
                    "The analysis mask file does not exist. Uncheck the "
                    "mask option or pick a valid file.",
                )
                return

        detector = CoherenceDeltaDetector(
            threshold_mode=self.wt_mode_combo.currentData() or "adaptive",
            a_coh=self.wt_coh_a_spin.value(),
            fixed_threshold=self.wt_coh_fixed_spin.value(),
            min_pixels=self.wt_min_px_spin.value(),
            median_filter_size=int(self.wt_median_combo.currentData() or 0),
        )
        desc = (f"Coherence DiD ({os.path.basename(prepost)}"
                + (f" vs {os.path.basename(control)}" if control else " — no control")
                + f", {os.path.basename(output_base)})")

        holder: dict = {}

        def _work() -> dict:
            task = holder.get("task")
            progress_cb = (
                (lambda f, m: task.setProgress(float(f))) if task else None
            )
            cancel_cb = task.isCanceled if task else None
            return detector.detect_file(
                prepost_products=[prepost],
                control_products=[control] if control else [],
                output_base=output_base,
                analysis_mask_path=mask_path,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )

        task = AnalysisTask(description=desc, work=_work)
        holder["task"] = task
        task.detector = detector
        task.taskCompleted.connect(
            lambda: self._on_windthrow_finished(task, True))
        task.taskTerminated.connect(
            lambda: self._on_windthrow_finished(task, False))

        self._active_analysis_task = task
        self.analysis_run_btn.setEnabled(False)
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setRange(0, 100)
        self.analysis_progress.setValue(0)
        self.analysis_cancel_btn.setVisible(True)
        log_info(f"Coherence DiD detection started: {desc}")
        QgsApplication.taskManager().addTask(task)

    def _on_windthrow_cancel(self) -> None:
        """Cancel the running windthrow detection task."""
        task = self._active_analysis_task
        if task is not None:
            log_info("Windthrow detection cancellation requested by user")
            task.cancel()

    def _on_windthrow_finished(self, task: "AnalysisTask", success: bool) -> None:
        self._active_analysis_task = None
        self.analysis_run_btn.setEnabled(True)
        self.analysis_progress.setVisible(False)
        self.analysis_cancel_btn.setVisible(False)
        if success:
            result = task.output_path if isinstance(task.output_path, dict) else {}
            vector_path = result.get("vector", "")
            # Load all artifacts when the Settings option is enabled.
            if self.add_to_map_chk.isChecked():
                for key, label in (("wi", "WI"), ("dcoh", "dCoH"),
                                   ("mask", "Mask"),
                                   ("forest_mask", "Forest")):
                    p = result.get(key)
                    if p and os.path.isfile(p):
                        self._add_raster_to_map(p, os.path.basename(p))
                if vector_path and os.path.isfile(vector_path):
                    self._add_vector_to_map(
                        vector_path, os.path.basename(vector_path))
            # Result dictionaries differ per method: radar modes use
            # threshold_db/mean_wi (dB), coherence DiD uses
            # threshold/mean_dcoh (coherence units).
            is_coh_result = "dcoh" in result
            thr = result.get("threshold_db", result.get("threshold"))
            mean = result.get("mean_wi", result.get("median_dcoh"))
            n_obj = result.get("n_objects")
            units = "" if is_coh_result else " dB"
            index_name = "dCoH" if is_coh_result else "WI"
            stat_label = ("Median dCoH" if is_coh_result else "Mean WI")
            forest_note = ("\nForest mask: " + str(result.get("forest_mask"))
                           if result.get("forest_mask") else "")
            wm_note = ""
            ignored = result.get("water_mask_ignored") or []
            if ignored:
                wm_note = ("\nIgnored corrupt water mask(s): "
                           + ", ".join(os.path.basename(p) for p in ignored))
            index_path = result.get("dcoh") or result.get("wi") or ""
            QMessageBox.information(
                self, "Windthrow detection complete",
                f"Detected objects: {n_obj}\n"
                f"{stat_label}: {mean:.3f}{units}\n"
                f"Threshold used: {thr:.3f}{units}\n\n"
                f"Outputs:\n{index_path}\n{result.get('mask')}\n"
                f"{vector_path}" + forest_note + wm_note,
            )
            log_info(
                f"Windthrow detection finished: {n_obj} objects, "
                f"threshold {thr:.3f}{units} (mean {index_name} "
                f"{mean:.3f}{units})"
            )
        else:
            msg = str(task.exception) if task.exception else "Task was cancelled."
            if task.exception is not None:
                log_warning(
                    f"Windthrow detection failed: {msg}\n"
                    f"{format_exception(task.exception)}"
                )
            QMessageBox.warning(self, "Windthrow detection failed", msg)

    def _add_vector_to_map(self, path: str, name: str = "") -> bool:
        """Load a written vector file into the current project."""
        if self._iface is None:
            return False
        try:
            from qgis.core import QgsVectorLayer
            layer = QgsVectorLayer(path, name or os.path.basename(path), "ogr")
            if not layer.isValid():
                log_warning(f"Result is not a valid vector layer: {path}")
                return False
            QgsProject.instance().addMapLayer(layer)
            log_info(f"Added vector result to map: {path}")
            return True
        except Exception as exc:
            log_warning(f"Could not add vector result to map ({path}): {exc}")
            return False


    # ------------------------------------------------------------------
    # Tab 4 — Settings + About
    # ------------------------------------------------------------------
    def _build_settings_tab(self) -> QWidget:
        """Build the Settings tab."""
        page = QWidget(self)
        layout = QVBoxLayout(page)

        # ----- Default folders group -----
        folders_group = QGroupBox("Default Folders", page)
        folders_form = QFormLayout(folders_group)

        # Download dir
        self.set_download_edit = QLineEdit(page)
        self.set_download_edit.setPlaceholderText("Default download folder")
        dl_browse = QPushButton("Browse...", page)
        dl_row = QHBoxLayout()
        dl_row.addWidget(self.set_download_edit, 1)
        dl_row.addWidget(dl_browse)
        folders_form.addRow("Download folder:", dl_row)
        dl_browse.clicked.connect(lambda: self._browse_folder_into(self.set_download_edit))

        # Preprocess input
        self.set_prep_in_edit = QLineEdit(page)
        self.set_prep_in_edit.setPlaceholderText("Default preprocess input folder")
        pi_browse = QPushButton("Browse...", page)
        pi_row = QHBoxLayout()
        pi_row.addWidget(self.set_prep_in_edit, 1)
        pi_row.addWidget(pi_browse)
        folders_form.addRow("Preprocess input:", pi_row)
        pi_browse.clicked.connect(lambda: self._browse_folder_into(self.set_prep_in_edit))

        # Preprocess output
        self.set_prep_out_edit = QLineEdit(page)
        self.set_prep_out_edit.setPlaceholderText("Default preprocess output folder")
        po_browse = QPushButton("Browse...", page)
        po_row = QHBoxLayout()
        po_row.addWidget(self.set_prep_out_edit, 1)
        po_row.addWidget(po_browse)
        folders_form.addRow("Preprocess output:", po_row)
        po_browse.clicked.connect(lambda: self._browse_folder_into(self.set_prep_out_edit))
        layout.addWidget(folders_group)

        # ----- Default parameters group -----
        params_group = QGroupBox("Default Parameters", page)
        params_form = QFormLayout(params_group)
        self.set_speckle_kernel_spin = QSpinBox(page)
        self.set_speckle_kernel_spin.setRange(3, 21)
        self.set_speckle_kernel_spin.setSingleStep(2)
        self.set_speckle_kernel_spin.setValue(5)
        params_form.addRow("Speckle kernel:", self.set_speckle_kernel_spin)

        self.set_land_mask_spin = QDoubleSpinBox(page)
        self.set_land_mask_spin.setRange(-50.0, 0.0)
        self.set_land_mask_spin.setSingleStep(0.5)
        self.set_land_mask_spin.setValue(-20.0)
        params_form.addRow("Land mask threshold (dB):", self.set_land_mask_spin)

        self.set_wi_offset_spin = QDoubleSpinBox(page)
        self.set_wi_offset_spin.setRange(0.0, 10.0)
        self.set_wi_offset_spin.setSingleStep(0.1)
        self.set_wi_offset_spin.setValue(2.9)
        self.set_wi_offset_spin.setToolTip(
            "Default offset above the mean WI in the Windthrow tab "
            "(Rüetschi et al. 2019 optimum: 2.9 dB)."
        )
        params_form.addRow("WI threshold offset a (dB):", self.set_wi_offset_spin)

        self.set_min_px_spin = QSpinBox(page)
        self.set_min_px_spin.setRange(1, 100000)
        self.set_min_px_spin.setValue(27)
        self.set_min_px_spin.setToolTip(
            "Default minimum object size in pixels (27 px ≈ 0.27 ha "
            "at 10 m pixel size)."
        )
        params_form.addRow("Min object size (px):", self.set_min_px_spin)

        # Auto-load results into the project after Preprocess / Analysis
        # finishes (mirrors what SCP and similar plugins do).
        self.add_to_map_chk = QCheckBox(
            "Add results to the map automatically", page
        )
        self.add_to_map_chk.setChecked(True)
        self.add_to_map_chk.setToolTip(
            "After a Preprocess or Quick Analysis run finishes, load each "
            "output GeoTIFF into the current QGIS project."
        )
        params_form.addRow("", self.add_to_map_chk)
        layout.addWidget(params_group)

        # ----- Buttons -----
        btn_row = QHBoxLayout()
        self.set_save_btn = QPushButton("Save Settings", page)
        self.set_reset_btn = QPushButton("Reset to Defaults", page)
        self.set_about_btn = QPushButton("About...", page)
        btn_row.addWidget(self.set_save_btn)
        btn_row.addWidget(self.set_reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.set_about_btn)
        layout.addLayout(btn_row)

        layout.addStretch()

        # ----- Connections -----
        self.set_save_btn.clicked.connect(self._on_save_settings)
        self.set_reset_btn.clicked.connect(self._on_reset_settings)
        self.set_about_btn.clicked.connect(self._on_about)

        return page

    def _browse_folder_into(self, edit: "QLineEdit") -> None:
        """Open a folder dialog and put the chosen path into ``edit``."""
        path = QFileDialog.getExistingDirectory(
            self, "Select folder", edit.text() or "", QFileDialog.ShowDirsOnly,
        )
        if path:
            edit.setText(path)

    def _add_raster_to_map(self, path: str, name: str = "") -> bool:
        """Load a written GeoTIFF into the current project.

        Returns True when the layer was added. Returns False (logged, not
        raised) when the dialog runs outside QGIS — the plugin is still
        fully usable for batch file processing in that mode — or when
        GDAL produced a layer QGIS cannot read back.
        """
        if self._iface is None:
            return False
        try:
            layer = QgsRasterLayer(path, name or os.path.basename(path))
            if not layer.isValid():
                log_warning(f"Result is not a valid raster layer: {path}")
                return False
            QgsProject.instance().addMapLayer(layer)
            log_info(f"Added result to map: {path}")
            return True
        except Exception as exc:
            log_warning(f"Could not add result to map ({path}): {exc}")
            return False

    def _on_save_settings(self) -> None:
        self._save_settings()
        QMessageBox.information(self, "Settings", "Settings saved.")

    def _on_reset_settings(self) -> None:
        """Reset the Settings tab fields to default values (without saving)."""
        self.set_download_edit.clear()
        self.set_prep_in_edit.clear()
        self.set_prep_out_edit.clear()
        self.set_speckle_kernel_spin.setValue(5)
        self.set_land_mask_spin.setValue(-20.0)
        self.set_wi_offset_spin.setValue(2.9)
        self.set_min_px_spin.setValue(27)
        self.add_to_map_chk.setChecked(True)
        self.product_combo.setCurrentIndex(
            self.product_combo.findData(self.DEFAULT_PRODUCT_ID)
        )

    def _on_about(self) -> None:
        QMessageBox.information(
            self, "About — Sentinel-1 Windthrow Detector",
            "<h3>Sentinel-1 Windthrow Detector</h3>"
            "<p>Version 0.7.0</p>"
            "<p>Rapid windthrow (storm forest damage) mapping with "
            "Sentinel-1: STAC search and download on Microsoft Planetary "
            "Computer (GRD / RTC), preprocessing (dB, Lee speckle, land "
            "mask) and bi-temporal change detection after Rüetschi et al. "
            "2019 (Remote Sensing 11(2):115): Windthrow Index "
            "WI = dVV + dVH, adaptive threshold (mean + a dB), minimum "
            "object filter and vectorisation to GeoPackage with per-object "
            "area in hectares.</p>"
            "<p><b>Dependencies:</b> numpy, scipy, GDAL (osgeo). "
            "STAC access uses only the Python standard library "
            "(no pystac-client / planetary-computer needed).</p>"
            "<p><b>QGIS:</b> 3.28+.</p>",
        )

    # ==================================================================
    # Search & Download slot implementations
    # ==================================================================
    def _on_search_clicked(self) -> None:
        """Handle the Search button press."""
        if self._source is None:
            QMessageBox.critical(
                self, "Source unavailable",
                "PlanetaryComputerSource could not be initialised.",
            )
            return

        # Validate AOI
        try:
            min_lon = float(self.min_lon_edit.text())
            max_lon = float(self.max_lon_edit.text())
            min_lat = float(self.min_lat_edit.text())
            max_lat = float(self.max_lat_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid AOI", "Please enter numeric coordinates.")
            return
        if min_lon > max_lon or min_lat > max_lat:
            QMessageBox.warning(self, "Invalid AOI", "Min must be less than Max.")
            return
        bbox = (min_lon, min_lat, max_lon, max_lat)

        start_date = self.start_date_edit.date().toPyDate()
        end_date = self.end_date_edit.date().toPyDate()
        if start_date > end_date:
            QMessageBox.warning(
                self, "Invalid date range",
                "Start date must be before or equal to end date.",
            )
            return

        # Soft guard rails: a very large AOI and/or a very long period
        # matches thousands of scenes; the client silently caps the result
        # at 500 and the request can take minutes. Better to let the user
        # reconsider up front than to show them a truncated table later.
        span_lon = abs(max_lon - min_lon)
        span_lat = abs(max_lat - min_lat)
        period_days = (end_date - start_date).days
        if (span_lon > 5.0 and span_lat > 5.0) or period_days > 366:
            reply = QMessageBox.question(
                self,
                "Large search requested",
                "The AOI and/or date range is very large:\n\n"
                f"  • bbox ≈ {span_lon:.1f}° × {span_lat:.1f}°\n"
                f"  • period ≈ {period_days} days\n\n"
                "The search may take a long time and the result list will "
                "be capped at 500 scenes. Continue anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        # Polarization is no longer a search-time user choice — Sentinel-1
        # IW GRD scenes on Planetary Computer are dual-pol (VV + VH) and the
        # plugin always downloads both channels together. We still pass
        # "VV+VH" downstream so the search filters out any rare single-pol
        # product that would be missing one of the two channels.
        polarization = "VV+VH"
        orbit = self.orbit_combo.currentText()
        # GRD vs RTC — the STAC collection id stored in the combo's data.
        collection = self.product_combo.currentData() or "sentinel-1-grd"
        product_short = "GRD" if collection.endswith("grd") else "RTC"

        # UI feedback
        self.search_button.setEnabled(False)
        self.search_progress.setVisible(True)
        self.search_progress.setRange(0, 100)
        self.search_progress.setValue(0)
        self.search_cancel_btn.setVisible(True)
        self.results_table.setRowCount(0)
        self._search_results.clear()
        self.download_button.setEnabled(False)
        # Reset status label with a "searching..." message so the user
        # immediately sees that the button press did something.
        self.search_status_label.setText(
            f"Searching… bbox=({min_lon:.3f}, {min_lat:.3f})–({max_lon:.3f}, {max_lat:.3f}), "
            f"dates={start_date.isoformat()}..{end_date.isoformat()}, "
            f"product={product_short}, pol=VV+VH (auto), orbit={orbit}"
        )
        self.search_status_label.setStyleSheet(
            "padding: 2px 4px; color: #555;"
        )

        task = SearchTask(
            description="Searching Sentinel-1 scenes",
            source=self._source,
            bbox=bbox,
            start_date=start_dt,
            end_date=end_dt,
            polarization=polarization,
            orbit=orbit,
            collection=collection,
        )
        # Anchor the Python wrapper on ``self`` BEFORE handing the C++
        # object to the task manager. Without this reference the SIP
        # wrapper can be garbage-collected while the manager still owns
        # the underlying QgsTask — a classic source of
        # "RuntimeError: wrapped C/C++ object of type QgsTask has been
        # deleted" crashes in PyQGIS plugins.
        self._active_search_task = task
        # QgsTask signals: taskCompleted / taskTerminated.
        task.taskCompleted.connect(lambda: self._on_search_finished(task, True))
        task.taskTerminated.connect(lambda: self._on_search_finished(task, False))
        QgsApplication.taskManager().addTask(task)

    def _on_search_cancel(self) -> None:
        """Cancel the running search task directly from the dialog."""
        task = self._active_search_task
        if task is not None:
            log_info("Search cancellation requested by user")
            task.cancel()

    def _on_search_finished(self, task: "SearchTask", success: bool) -> None:
        """Handle completion of the search task."""
        self._active_search_task = None
        self.search_button.setEnabled(True)
        self.search_progress.setVisible(False)
        self.search_cancel_btn.setVisible(False)

        if not success:
            msg = str(task.exception) if task.exception else "Search was cancelled."
            if task.exception is not None:
                log_warning(
                    f"Search failed: {msg}\n{format_exception(task.exception)}"
                )
            # Show the error inline (always visible) AND as a modal dialog
            # so the user can't miss it.
            self.search_status_label.setText(f"❌ Search failed: {msg}")
            self.search_status_label.setStyleSheet(
                "padding: 2px 4px; color: #b00; font-weight: bold;"
            )
            QMessageBox.warning(self, "Search failed", msg)
            return

        # IMPORTANT: copy results from the task into the dialog state.
        # Without this the table would always be empty.
        self._search_results = list(task.scenes)
        self._populate_results_table()

        # Visible status: how many scenes were found. If zero, add a hint
        # about what might have gone wrong (filters too restrictive /
        # date range too narrow / fresh data not yet published).
        n = len(self._search_results)
        truncated = bool(getattr(task.source, "last_search_truncated", False))
        if n == 0:
            self.search_status_label.setText(
                "⚠ No scenes found. Try widening the date range, "
                "enlarging the AOI, or relaxing the polarisation / orbit filter. "
                "Open the QGIS Log Messages panel (Sentinel1SAR tab) "
                "to see how many raw items STAC returned."
            )
            self.search_status_label.setStyleSheet(
                "padding: 2px 4px; color: #c70; font-weight: bold;"
            )
        else:
            self.search_status_label.setText(
                f"✓ Found {n} scene{'s' if n != 1 else ''}. "
                "Select rows below and click Download Selected."
            )
            self.search_status_label.setStyleSheet(
                "padding: 2px 4px; color: #070; font-weight: bold;"
            )

        # The catalogue had more matches than we can show — say so instead
        # of letting a capped result set masquerade as a complete one.
        if truncated:
            current = self.search_status_label.text()
            self.search_status_label.setText(
                f"{current}\n⚠ Result list was capped at the {n}-scene search "
                "limit — more scenes matched. Narrow the AOI or date range "
                "to see the rest."
            )
            log_warning(
                f"STAC search was truncated at the limit ({n} scenes shown)"
            )

        log_info(f"Displayed {n} search results")

    def _populate_results_table(self) -> None:
        """Fill the results table with current ``self._search_results``.

        Columns:
            0 — Preview (QLabel with pixmap; loaded async)
            1 — ID
            2 — Date
            3 — Polarization
            4 — Orbit
            5 — Rel. Orbit
            6 — Progress (QProgressBar, shown when downloading this row)
        """
        n = len(self._search_results)
        self.results_table.setRowCount(n)
        # Clear any stale per-row progress bars / pending replies.
        self._row_progress_bars.clear()
        self._pending_preview_replies.clear()

        for row, scene in enumerate(self._search_results):
            # ---- Preview cell ----
            preview_label = QLabel(self.results_table)
            preview_label.setAlignment(Qt.AlignCenter)
            preview_label.setMinimumSize(96, 72)
            preview_label.setStyleSheet(
                "background: #f0f0f0; border: 1px solid #ccc; color: #888;"
            )
            preview_label.setText("…")
            preview_label.setToolTip(
                f"Footprint: bbox={list(scene.bbox)}\n"
                f"Click 'Show on map' (next to Download) to draw this on the QGIS canvas."
            )
            self.results_table.setCellWidget(row, 0, preview_label)

            # Kick off async thumbnail load.
            url = scene.preview_url or scene.thumbnail_url
            if url:
                self._fetch_preview_async(scene.id, url, preview_label)
            else:
                # No preview URL — draw a tiny footprint diagram instead.
                self._draw_footprint_placeholder(preview_label, scene.bbox)

            # ---- Text cells ----
            text_cells = [
                scene.id,
                scene.datetime.strftime("%Y-%m-%d"),
                ", ".join(scene.polarizations) if scene.polarizations else "",
                scene.orbit_direction or "",
                str(scene.relative_orbit) if scene.relative_orbit is not None else "",
            ]
            for col, value in enumerate(text_cells, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                self.results_table.setItem(row, col, item)

            # ---- Progress cell ----
            bar = QProgressBar(self.results_table)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setVisible(False)
            bar.setFormat("%p%")
            self.results_table.setCellWidget(row, 6, bar)
            self._row_progress_bars[row] = bar

        self.results_table.resizeRowsToContents()

    def _fetch_preview_async(
        self, scene_id: str, url: str, label: QLabel
    ) -> None:
        """Asynchronously download the preview thumbnail and set it on ``label``.

        Uses ``QNetworkAccessManager`` so the request runs in Qt's event
        loop without blocking the UI. The reply is dispatched to
        :py:meth:`_on_preview_reply` via the ``finished`` signal.
        """
        try:
            req = QNetworkRequest(QUrl(url))
            req.setAttribute(QNetworkRequest.User, scene_id)
            req.setRawHeader(b"User-Agent", b"QGIS-Sentinel1-Plugin/0.5")
            reply = self._nam.get(req)
            # Store reply so we can abort it if the dialog is closed first.
            self._pending_preview_replies[scene_id] = (reply, label)
            reply.finished.connect(
                lambda r=reply, sid=scene_id: self._on_preview_reply(sid, r)
            )
        except Exception as exc:
            log_warning(f"Could not start preview fetch for {scene_id}: {exc}")
            label.setText("—")
            label.setToolTip(f"Preview fetch failed: {exc}")

    def _on_preview_reply(self, scene_id: str, reply: QNetworkReply) -> None:
        """Handle a finished preview-image HTTP reply."""
        # Always clean up the reply object.
        try:
            entry = self._pending_preview_replies.pop(scene_id, None)
            label = entry[1] if entry else None
            if label is None:
                reply.deleteLater()
                return

            if reply.error() != QNetworkReply.NoError:
                log_warning(
                    f"Preview fetch failed for {scene_id}: {reply.errorString()}"
                )
                # Fallback: draw a tiny footprint diagram from the bbox.
                scene = next(
                    (s for s in self._search_results if s.id == scene_id), None
                )
                if scene is not None:
                    self._draw_footprint_placeholder(label, scene.bbox)
                else:
                    label.setText("—")
                reply.deleteLater()
                return

            data: QByteArray = reply.readAll()
            if data.isEmpty():
                label.setText("—")
                reply.deleteLater()
                return

            img = QImage()
            if not img.loadFromData(data):
                log_warning(f"Preview image data could not be decoded for {scene_id}")
                label.setText("—")
                reply.deleteLater()
                return

            pix = QPixmap.fromImage(img)
            # Scale to fit the cell while preserving aspect ratio.
            scaled = pix.scaled(
                96, 72,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            label.setPixmap(scaled)
            label.setStyleSheet(
                "background: #fff; border: 1px solid #ccc;"
            )
            label.setToolTip(
                f"Preview of {scene_id}\n"
                f"Image size: {img.width()}x{img.height()}"
            )
        finally:
            reply.deleteLater()

    @staticmethod
    def _draw_footprint_placeholder(label: QLabel, bbox) -> None:
        """Draw a tiny footprint rectangle on ``label`` as a fallback.

        Used when no preview URL is available or the fetch failed. The
        diagram is a simple equirectangular projection of the scene's
        bbox on a 96x72 canvas.
        """
        try:
            from qgis.PyQt.QtGui import QPainter, QPen, QColor, QBrush
            from qgis.PyQt.QtCore import QRectF

            pix = QPixmap(96, 72)
            pix.fill(QColor("#ffffff"))
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing, True)
            # Draw a light world map outline (very stylized).
            p.setPen(QPen(QColor("#cccccc"), 1))
            p.setBrush(QBrush(QColor("#f5f5f5")))
            p.drawRect(0, 0, 95, 71)
            # Draw the bbox rectangle.
            if bbox and len(bbox) == 4:
                min_lon, min_lat, max_lon, max_lat = bbox
                # Equirectangular: x = (lon + 180) / 360, y = (90 - lat) / 180
                x0 = int((min_lon + 180) / 360 * 96)
                x1 = int((max_lon + 180) / 360 * 96)
                y0 = int((90 - max_lat) / 180 * 72)
                y1 = int((90 - min_lat) / 180 * 72)
                p.setPen(QPen(QColor("#c0392b"), 2))
                p.setBrush(QBrush(QColor(192, 57, 43, 80)))
                p.drawRect(QRectF(min(x0, x1), min(y0, y1),
                                  abs(x1 - x0), abs(y1 - y0)))
            p.end()
            label.setPixmap(pix)
            label.setToolTip(
                f"Footprint (no preview image available).\n"
                f"bbox = {list(bbox) if bbox else 'unknown'}"
            )
            label.setStyleSheet("background: #fff; border: 1px solid #ccc;")
        except Exception:
            label.setText("—")

    def _on_download_clicked(self) -> None:
        """Handle Download Selected — start one DownloadTask per selected row.

        Each task reports its own progress (0..100) via ``progressChanged``;
        the dialog wires that signal to the corresponding row's progress
        bar in column 6. An overall progress bar (``download_progress``)
        shows the average across all running tasks.
        """
        if self._source is None:
            QMessageBox.critical(self, "Source unavailable", "STAC source is not available.")
            return

        rows = self.results_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "No selection", "Please select rows to download.")
            return

        row_indices = sorted({idx.row() for idx in rows})
        scenes_to_download = [
            (row_idx, self._search_results[row_idx])
            for row_idx in row_indices
        ]

        # Default folder: stored setting or empty
        default_dir = self._settings.value(self.KEY_DOWNLOAD_DIR, "", type=str) or ""
        dest_dir = QFileDialog.getExistingDirectory(
            self, "Select download directory", default_dir, QFileDialog.ShowDirsOnly,
        )
        if not dest_dir:
            return

        # Save the chosen directory as the new default.
        try:
            self._settings.setValue(self.KEY_DOWNLOAD_DIR, dest_dir)
        except Exception:
            pass

        # Show overall progress bar.
        self.download_button.setEnabled(False)
        self.search_button.setEnabled(False)
        self.download_progress.setVisible(True)
        self.download_cancel_btn.setVisible(True)
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_status_label.setText(
            f"Starting {len(scenes_to_download)} download(s)…"
        )

        # Reset per-row bars.
        self._task_row_map.clear()
        self._active_download_tasks.clear()
        self._download_total_scenes = len(scenes_to_download)
        self._download_done_scenes = 0
        self._download_failed_scenes: List[str] = []

        for row_idx, scene in scenes_to_download:
            # Show the per-row bar and reset it.
            bar = self._row_progress_bars.get(row_idx)
            if bar is not None:
                bar.setVisible(True)
                bar.setValue(0)

            task = DownloadTask(
                description=f"Downloading scene {scene.id}",
                source=self._source,
                scene=scene,
                dest_dir=dest_dir,
            )
            # Remember which row this task belongs to so we can update
            # the right per-row bar from progressChanged.
            self._task_row_map[id(task)] = row_idx
            # progressChanged emits a double 0..100.
            task.progressChanged.connect(
                lambda val, tid=id(task): self._on_download_progress(tid, val)
            )
            task.taskCompleted.connect(lambda t=task: self._on_download_finished(t, True))
            task.taskTerminated.connect(lambda t=task: self._on_download_finished(t, False))
            self._active_download_tasks.append(task)
            QgsApplication.taskManager().addTask(task)

    def _on_download_cancel(self) -> None:
        """Cancel all running scene downloads directly from the dialog.

        Partially downloaded files stay on disk; the Range-resume logic in
        ``PlanetaryComputerSource._http_download`` picks them up on the
        next attempt instead of starting from zero.
        """
        tasks = list(self._active_download_tasks)
        if not tasks:
            return
        log_info(f"Cancellation requested for {len(tasks)} download task(s)")
        for task in tasks:
            task.cancel()

    def _on_prep_cancel(self) -> None:
        """Cancel the remaining preprocessing tasks."""
        tasks = list(self._active_preprocess_tasks)
        if not tasks:
            return
        log_info(f"Cancellation requested for {len(tasks)} preprocess task(s)")
        for task in tasks:
            task.cancel()

    def _on_download_progress(self, task_id: int, value: float) -> None:
        """Update per-row and overall progress for an active download.

        ``value`` is 0..100 (from ``QgsTask.progressChanged``). Per-row bar
        shows this scene's percentage; the overall bar averages across all
        running tasks weighted by their per-scene progress.
        """
        row_idx = self._task_row_map.get(task_id)
        if row_idx is None:
            return
        bar = self._row_progress_bars.get(row_idx)
        if bar is not None:
            bar.setValue(int(value))

        # Overall: (sum of per-task progress) / total tasks, expressed as %.
        # i.e. each scene contributes equally to the overall bar; a scene
        # at 50% contributes 0.5/N to the overall percentage.
        total = getattr(self, "_download_total_scenes", 1) or 1
        done = getattr(self, "_download_done_scenes", 0)
        # Sum of progress across still-active tasks (0..100 each).
        active_sum = 0
        active_count = 0
        for t in self._active_download_tasks:
            try:
                active_sum += float(t.progress())
                active_count += 1
            except Exception:
                pass
        # Completed scenes count as 100% each.
        overall = (
            (done * 100.0 + active_sum) / (total * 100.0) * 100.0
            if total > 0 else 0.0
        )
        overall = max(0.0, min(100.0, overall))
        self.download_progress.setValue(int(overall))
        # Update the textual status.
        remaining = total - done - active_count
        self.download_status_label.setText(
            f"Downloading… {done}/{total} done, {active_count} active, "
            f"{remaining} queued  ({int(value)}% on current)"
        )

    def _on_download_finished(self, task: "DownloadTask", success: bool) -> None:
        """Handle completion of a single download task."""
        if success:
            self._download_done_scenes = getattr(self, "_download_done_scenes", 0) + 1
            log_info(f"Download OK: {task.scene.id}")
        else:
            err = task.exception
            msg = str(err) if err is not None else "unknown error"
            self._download_failed_scenes.append(f"{task.scene.id}: {msg}")
            if err is not None:
                # Full traceback — "Connection reset by peer" alone says
                # nothing about which of the 12 retry paths broke.
                log_warning(
                    f"Download FAIL: {task.scene.id}: {msg}\n{format_exception(err)}"
                )
            else:
                log_warning(f"Download FAIL: {task.scene.id}: {msg}")
            # Count failed as "done" too, so overall bar progresses.
            self._download_done_scenes = getattr(self, "_download_done_scenes", 0) + 1

        # Mark the per-row bar as 100% / failed.
        row_idx = self._task_row_map.pop(id(task), None)
        if row_idx is not None:
            bar = self._row_progress_bars.get(row_idx)
            if bar is not None:
                bar.setValue(100 if success else 0)
                bar.setFormat("done" if success else "FAIL")
                bar.setStyleSheet(
                    "" if success else
                    "QProgressBar::chunk { background-color: #c0392b; }"
                )

        try:
            self._active_download_tasks.remove(task)
        except ValueError:
            pass

        # Update overall bar.
        total = getattr(self, "_download_total_scenes", 1) or 1
        done = getattr(self, "_download_done_scenes", 0)
        pct = int(done / total * 100)
        self.download_progress.setValue(pct)
        self.download_status_label.setText(
            f"Downloaded {done}/{total}…"
        )

        if not self._active_download_tasks:
            self.download_button.setEnabled(True)
            self.search_button.setEnabled(True)
            self.download_progress.setVisible(False)
            self.download_cancel_btn.setVisible(False)

            ok = done - len(self._download_failed_scenes)
            fail = len(self._download_failed_scenes)
            if fail == 0:
                msg = f"All {ok} scene(s) downloaded successfully."
                log_info(f"Download finished: {ok} ok, 0 fail")
                QMessageBox.information(self, "Download complete", msg)
            else:
                preview = "\n".join(self._download_failed_scenes[:5])
                more = "" if fail <= 5 else f"\n... and {fail - 5} more"
                msg = (
                    f"Done: {ok} succeeded, {fail} failed.\n\n"
                    f"Failed:\n{preview}{more}\n\n"
                    f"See QGIS Log Panel (tag: Sentinel1SAR) for details."
                )
                log_warning(f"Download finished: {ok} ok, {fail} fail")
                QMessageBox.warning(self, "Download finished with errors", msg)
            self.download_status_label.setText("")

    # ==================================================================
    # Settings persistence
    # ==================================================================
    def _load_settings(self) -> None:
        """Read settings from QgsSettings into the UI fields.

        Called once after the UI is built. Settings keys are
        case-sensitive; defaults are applied when keys are missing.
        """
        s = self._settings
        self.set_download_edit.setText(s.value(self.KEY_DOWNLOAD_DIR, "", type=str) or "")
        self.set_prep_in_edit.setText(s.value(self.KEY_PREPROCESS_INPUT_DIR, "", type=str) or "")
        self.set_prep_out_edit.setText(s.value(self.KEY_PREPROCESS_OUTPUT_DIR, "", type=str) or "")
        self.set_speckle_kernel_spin.setValue(int(s.value(self.KEY_DEFAULT_SPECKLE_KERNEL, 5)))
        self.set_land_mask_spin.setValue(float(s.value(self.KEY_DEFAULT_LAND_MASK_DB, -20.0)))
        self.set_wi_offset_spin.setValue(float(s.value(self.KEY_DEFAULT_WI_OFFSET_DB, 2.9)))
        self.set_min_px_spin.setValue(int(s.value(self.KEY_DEFAULT_MIN_OBJECT_PX, 27)))
        # "Add to map" defaults to True; only an explicit stored False turns
        # it off (QgsSettings stores bools as strings, so compare loosely).
        self.add_to_map_chk.setChecked(
            str(s.value(self.KEY_ADD_TO_MAP, "true")).lower() not in ("false", "0")
        )
        # Product (GRD / RTC): match the stored STAC collection id against
        # the combo's data roles; fall back to GRD when missing/unknown.
        saved_product = str(
            s.value(self.KEY_PRODUCT, self.DEFAULT_PRODUCT_ID) or self.DEFAULT_PRODUCT_ID
        )
        product_idx = self.product_combo.findData(saved_product)
        self.product_combo.setCurrentIndex(product_idx if product_idx >= 0 else 0)

        # Pre-fill the preprocess tab from settings if those fields are empty
        if not self.prep_input_edit.text():
            self.prep_input_edit.setText(s.value(self.KEY_PREPROCESS_INPUT_DIR, "", type=str) or "")
        if not self.prep_output_edit.text():
            self.prep_output_edit.setText(s.value(self.KEY_PREPROCESS_OUTPUT_DIR, "", type=str) or "")

        # Pre-fill the windthrow parameters from settings
        self.wt_a_spin.setValue(float(s.value(self.KEY_DEFAULT_WI_OFFSET_DB, 2.9)))
        self.wt_min_px_spin.setValue(int(s.value(self.KEY_DEFAULT_MIN_OBJECT_PX, 27)))
        self.speckle_kernel_spin.setValue(int(s.value(self.KEY_DEFAULT_SPECKLE_KERNEL, 5)))

        # Refresh preprocess file list once if a folder is set
        if self.prep_input_edit.text():
            self._on_prep_refresh()

    def _save_settings(self) -> None:
        """Persist UI fields into QgsSettings."""
        s = self._settings
        s.setValue(self.KEY_DOWNLOAD_DIR, self.set_download_edit.text())
        s.setValue(self.KEY_PREPROCESS_INPUT_DIR, self.set_prep_in_edit.text())
        s.setValue(self.KEY_PREPROCESS_OUTPUT_DIR, self.set_prep_out_edit.text())
        s.setValue(self.KEY_DEFAULT_SPECKLE_KERNEL, self.set_speckle_kernel_spin.value())
        s.setValue(self.KEY_DEFAULT_LAND_MASK_DB, self.set_land_mask_spin.value())
        s.setValue(self.KEY_DEFAULT_WI_OFFSET_DB, self.set_wi_offset_spin.value())
        s.setValue(self.KEY_DEFAULT_MIN_OBJECT_PX, self.set_min_px_spin.value())
        s.setValue(self.KEY_ADD_TO_MAP, self.add_to_map_chk.isChecked())
        s.setValue(
            self.KEY_PRODUCT,
            self.product_combo.currentData() or self.DEFAULT_PRODUCT_ID,
        )
        s.sync()

    # ==================================================================
    # Close
    # ==================================================================
    def closeEvent(self, event):
        """Persist UI settings when the dialog is closed."""
        try:
            self._save_settings()
        except Exception as exc:  # pragma: no cover
            log_warning(f"Could not save settings on close: {exc}")
        super().closeEvent(event)
