"""Logging helpers built on top of ``QgsMessageLog``.

All plugin modules should report through these helpers so that logs land
in one place with a single, well-known tag.

The ``qgis.core`` import is guarded so the pure-computation modules
(``sources/preprocessor.py``, ``sources/analyzers.py``,
``sources/pc_client.py``) stay importable — and unit-testable — outside
a QGIS runtime. When QGIS is unavailable the helpers fall back to
``stderr``.
"""

import sys
import traceback

try:
    from qgis.core import Qgis, QgsMessageLog
except ImportError:  # pragma: no cover - exercised only outside QGIS
    Qgis = None
    QgsMessageLog = None

#: Tag used for all plugin messages in the QGIS log panel.
PLUGIN_TAG = "Sentinel1SAR"


def _emit(level_name: str, message: str, qgis_level) -> None:
    """Deliver ``message`` to QgsMessageLog when available, else stderr."""
    if QgsMessageLog is not None and Qgis is not None:
        QgsMessageLog.logMessage(message, PLUGIN_TAG, qgis_level)
    else:  # pragma: no cover - exercised only outside QGIS
        print(f"[{PLUGIN_TAG}][{level_name}] {message}", file=sys.stderr)


def log_info(message: str) -> None:
    """Log an informational message."""
    _emit("INFO", message, Qgis.Info if Qgis is not None else None)


def log_warning(message: str) -> None:
    """Log a warning message."""
    _emit("WARNING", message, Qgis.Warning if Qgis is not None else None)


def log_critical(message: str) -> None:
    """Log a critical message."""
    _emit("CRITICAL", message, Qgis.Critical if Qgis is not None else None)


def format_exception(exc: BaseException) -> str:
    """Return the full traceback text for an exception object.

    Background tasks stash the original exception on the task (e.g.
    ``task.exception``); by the time the GUI slot runs, Python's implicit
    ``sys.exc_info()`` context is gone. This helper recovers the stored
    traceback so failure logs include where the error actually happened
    instead of just ``str(exc)``.
    """
    try:
        return "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).strip()
    except Exception:  # pragma: no cover - formatting must never raise
        return f"{type(exc).__name__}: {exc}"
