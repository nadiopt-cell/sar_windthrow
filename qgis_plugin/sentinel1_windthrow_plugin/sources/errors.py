"""Typed exception hierarchy for the sentinel1-windthrow plugin.

Spec "P2 - Errors and UX" (7.1): one base class every deliberate plugin
error derives from, so the QGIS dialog can distinguish "user made a
mistake / product pair is wrong" (short human-readable message, details
in the log) from an unexpected bug (full traceback in the log).

Import-graph position: leaf module - imports nothing from this package,
stdlib only.  Safe to import from every sources/ module, from the
dialog, and in the pure-CI job (no GDAL, no numpy).

Backward-compatibility note
---------------------------
``InputValidationError`` also subclasses :class:`ValueError` because the
detectors historically raised plain ``ValueError`` for bad input and
existing tests / call sites use ``except ValueError`` /
``pytest.raises(ValueError, ...)``.  Mapping the legacy raises to typed
errors (spec 7.1: "Map existing ValueError messages for mixed HyP3
families / burst mismatch to ProductMismatchError with the same
human-readable text") therefore keeps every legacy consumer working
unchanged.
"""

__all__ = [
    "WindthrowPluginError",
    "InputValidationError",
    "ProductMismatchError",
    "DataAccessError",
    "CancelledError",
]


class WindthrowPluginError(Exception):
    """Base class for every error the plugin raises on purpose.

    The dialog catches this class to show a short, human-readable
    message and point at the QGIS log for the full traceback (spec 7.2).
    """


class InputValidationError(WindthrowPluginError, ValueError):
    """Invalid input: bad option value, missing file, wrong product
    structure.

    Also a :class:`ValueError` on purpose - see the module docstring.
    """


class ProductMismatchError(InputValidationError):
    """The HyP3 product pair cannot form a valid DiD: mixed product
    families (INSAR_GAMMA vs INSAR_ISCE_BURST), different burst
    footprints, or a polarization mismatch.

    Carries the exact same human-readable text the legacy ``ValueError``
    raises used, including the actionable "Order ..." advice.
    """


class DataAccessError(WindthrowPluginError):
    """A product, raster or vector layer cannot be opened or read.

    Reserved for typed IO errors in new code; the legacy
    ``FileNotFoundError`` / ``RuntimeError`` raises in the detectors are
    intentionally left as-is (same messages, zero behaviour change).
    """


class CancelledError(WindthrowPluginError):
    """A long operation was cancelled by the user.

    :class:`sources.base.OperationCancelled` (the name every detector
    and the dialog already use) subclasses this, so cancellation stays
    distinguishable from a real failure in the typed hierarchy.
    """
