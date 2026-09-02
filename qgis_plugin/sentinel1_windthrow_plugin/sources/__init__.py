"""Source classes for SAR data providers.

Public API:
    BaseSARSource     — abstract base class + Scene dataclass + OperationCancelled
    PlanetaryComputerSource — Microsoft Planetary Computer STAC source
    PlanetaryComputerClient — stdlib-only PC STAC client (no pystac-client dep)
    SARPreprocessor   — SAR preprocessing utilities (dB, speckle, land mask)
    WindthrowDetector — bi-temporal WI windthrow detector
                        (Rüetschi et al. 2019: WI = ΔVV + ΔVH)
"""

from . import pc_client as _pc_client
from .base import BaseSARSource, OperationCancelled, Scene
from .pc_client import PCError, PlanetaryComputerClient
from .planetary_computer import PlanetaryComputerSource
from .preprocessor import SARPreprocessor
from .windthrow import (
    WindthrowDetector,
    background_offset_db,
    common_polarizations,
    compute_wi,
    extract_polarization,
    filter_small_objects,
    mask_from_threshold,
    median_filter_nan,
    pair_by_polarization,
)

# Route pc_client warnings (retry notices, SAS-sign failures, token-cache
# problems) into the QGIS log panel under the Sentinel1SAR tag. The hook
# stays optional inside pc_client itself so that module remains usable —
# and unit-testable — without a QGIS runtime.
from ..logger import log_warning as _pc_log_warning

_pc_client.log_hook = _pc_log_warning

__all__ = [
    "BaseSARSource",
    "OperationCancelled",
    "Scene",
    "PlanetaryComputerSource",
    "PlanetaryComputerClient",
    "PCError",
    "SARPreprocessor",
    "WindthrowDetector",
    "background_offset_db",
    "common_polarizations",
    "compute_wi",
    "extract_polarization",
    "filter_small_objects",
    "mask_from_threshold",
    "median_filter_nan",
    "pair_by_polarization",
]
