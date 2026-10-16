"""B4 (spec "P2 - Errors and UX") tests: typed exception hierarchy,
legacy ValueError compatibility of the mapped raise sites, and the
adaptive chunk-row helper (spec 8.1).

This module is pure-python on purpose (no raster stack imports) so it
runs in both CI jobs.  Import note: hyp3_utils defers its heavy
optional imports, so _validate_did_pair / find_correlation_tif are
exercisable without a QGIS runtime.
"""

import os

import pytest

from sentinel1_windthrow_plugin.sources.base import OperationCancelled
from sentinel1_windthrow_plugin.sources.constants import CHUNK_ROWS
from sentinel1_windthrow_plugin.sources.errors import (
    CancelledError,
    DataAccessError,
    InputValidationError,
    ProductMismatchError,
    WindthrowPluginError,
)
from sentinel1_windthrow_plugin.sources.hyp3_utils import (
    _validate_did_pair,
    find_correlation_tif,
)
from sentinel1_windthrow_plugin.sources.raster_utils import (
    CHUNK_ROWS_MAX,
    CHUNK_ROWS_MIN,
    choose_chunk_rows,
)


# ----------------------------------------------------------------------
# 1. Hierarchy shape (spec 7.1)
# ----------------------------------------------------------------------

def test_hierarchy_shape():
    assert issubclass(InputValidationError, WindthrowPluginError)
    assert issubclass(ProductMismatchError, InputValidationError)
    assert issubclass(DataAccessError, WindthrowPluginError)
    assert issubclass(CancelledError, WindthrowPluginError)


def test_input_validation_is_value_error_for_legacy_compat():
    # Legacy consumers (tests, third-party glue) do
    # `except ValueError` / `pytest.raises(ValueError)` - the typed
    # hierarchy must stay compatible with that.
    assert issubclass(InputValidationError, ValueError)
    assert issubclass(ProductMismatchError, ValueError)


def test_operation_cancelled_is_part_of_typed_hierarchy():
    assert issubclass(OperationCancelled, CancelledError)
    assert issubclass(OperationCancelled, WindthrowPluginError)
    # Same class object the detectors raise (identity, not a copy).
    import sentinel1_windthrow_plugin.sources.base as base_mod

    assert base_mod.OperationCancelled is OperationCancelled


def test_sources_package_reexports_errors():
    from sentinel1_windthrow_plugin import sources as pkg

    assert pkg.WindthrowPluginError is WindthrowPluginError
    assert pkg.ProductMismatchError is ProductMismatchError
    assert pkg.CancelledError is CancelledError
    for name in ("WindthrowPluginError", "InputValidationError",
                 "ProductMismatchError", "DataAccessError",
                 "CancelledError"):
        assert name in pkg.__all__


def test_typed_message_survives_str():
    # The dialog shows f"{exc}" for typed errors (spec 7.2) - the
    # human-readable text must be exactly what was passed in.
    msg = "Mixing product families in one DiD is not supported."
    assert str(ProductMismatchError(msg)) == msg


# ----------------------------------------------------------------------
# 2. Mapped raise sites keep legacy ValueError texts (spec 7.1)
# ----------------------------------------------------------------------

def _isce_info(granule, burst_key, pol="vv"):
    return {
        "family": "isce",
        "granule": granule,
        "burst_key": burst_key,
        "burst_ids": [burst_key],
        "track": 56,
        "pol": pol,
        "spacing_m": 10,
    }


def test_family_mismatch_raises_typed_value_error():
    pre = _isce_info("pre", "t056_iw2")
    ctl = dict(_isce_info("ctl", "t056_iw2"), family="gamma")
    with pytest.raises(ProductMismatchError, match="Mixing product families"):
        _validate_did_pair(pre, ctl)
    with pytest.raises(ValueError, match="Mixing product families"):
        _validate_did_pair(pre, ctl)


def test_burst_footprint_mismatch_raises_typed_value_error():
    pre = _isce_info("pre", "t056_iw2")
    ctl = _isce_info("ctl", "t056_iw3")
    with pytest.raises(ProductMismatchError,
                       match="SAME burst footprint"):
        _validate_did_pair(pre, ctl)
    with pytest.raises(ValueError, match="SAME burst footprint"):
        _validate_did_pair(pre, ctl)


def test_polarization_mismatch_raises_typed_value_error():
    pre = _isce_info("pre", "t056_iw2", pol="vv")
    ctl = _isce_info("ctl", "t056_iw2", pol="vh")
    with pytest.raises(ProductMismatchError, match="polarization mismatch"):
        _validate_did_pair(pre, ctl)
    with pytest.raises(ValueError, match="polarization mismatch"):
        _validate_did_pair(pre, ctl)


def test_valid_pair_passes():
    _validate_did_pair(_isce_info("pre", "t056_iw2"),
                       _isce_info("ctl", "t056_iw2"))


def test_multiple_correlation_layers_is_input_validation(tmp_path):
    d = tmp_path / "product"
    d.mkdir()
    for name in ("S1A_IW1_corr.tif", "S1B_IW2_corr.tif"):
        (d / name).write_bytes(b"")
    with pytest.raises(InputValidationError,
                       match="Multiple coherence layers"):
        find_correlation_tif(str(d))
    with pytest.raises(ValueError, match="Multiple coherence layers"):
        find_correlation_tif(str(d))


def test_missing_correlation_layer_stays_file_not_found():
    # Legacy FileNotFoundError raise intentionally left as-is (spec 7.1
    # maps only the family/burst mismatches).
    from sentinel1_windthrow_plugin.sources.hyp3_utils import (
        find_correlation_tif as _f,
    )

    with pytest.raises(FileNotFoundError):
        _f(str(_nonexistent_dir()))


def _nonexistent_dir():
    import tempfile

    return os.path.join(tempfile.gettempdir(),
                        "no_such_hyp3_product_dir_expected_ever")


# ----------------------------------------------------------------------
# 3. choose_chunk_rows (spec 8.1) - helper ready, wiring deferred
# ----------------------------------------------------------------------

def test_choose_chunk_rows_exact_budget_point():
    # 64 MiB / (32768 px * 4 B) == 512 rows exactly - the default point.
    assert choose_chunk_rows(32768) == 512


def test_choose_chunk_rows_clamped():
    assert choose_chunk_rows(1) == CHUNK_ROWS_MAX        # tiny width
    assert choose_chunk_rows(10_000_000) == CHUNK_ROWS_MIN  # huge width
    for w in (1, 100, 5_000, 32_768, 1_000_000, 10_000_000):
        assert CHUNK_ROWS_MIN <= choose_chunk_rows(w) <= CHUNK_ROWS_MAX


def test_choose_chunk_rows_monotonic_and_defaults():
    rows_prev = None
    for w in (256, 1024, 4096, 16384, 65536):
        rows = choose_chunk_rows(w)
        if rows_prev is not None:
            assert rows <= rows_prev
        rows_prev = rows
    # Degenerate width falls back to the wired default constant.
    assert choose_chunk_rows(0) == CHUNK_ROWS
    assert choose_chunk_rows(-5) == CHUNK_ROWS
    # dtype and budget scale the result as documented.
    assert choose_chunk_rows(32768, dtype_size=8) == 256
    assert choose_chunk_rows(32768, max_bytes=32 * 1024**2) == 256
