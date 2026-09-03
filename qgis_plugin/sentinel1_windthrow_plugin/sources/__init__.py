"""Source classes for SAR data providers.

Public API:
    BaseSARSource     — abstract base class + Scene dataclass + OperationCancelled
    PlanetaryComputerSource — Microsoft Planetary Computer STAC source
    PlanetaryComputerClient — stdlib-only PC STAC client (no pystac-client dep)
    SARPreprocessor   — SAR preprocessing utilities (dB, speckle, land mask)
    WindthrowDetector — bi-temporal WI windthrow detector
                        (Rüetschi et al. 2019: WI = ΔVV + ΔVH)
    LbandDeclineDetector — L-band backscatter decline detector (v1.0,
                        Tanase et al. 2018: LDI = ΔHH + ΔHV, inverted sign)
    CoherenceDeltaDetector — coherence DiD detector over HyP3 INSAR-GAMMA
                        products (v1.0, dcoh = coh_control − coh_prepost)
    forest_mask       — forest-mask providers (v0.9): ESA WorldCover via
                        PC STAC / user file, on the radar reference grid
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
from . import forest_mask as forest_mask
from .forest_mask import (
    DEFAULT_FOREST_CLASSES,
    bbox_4326,
    build_forest_mask,
    build_forest_mask_from_rasters,
    build_worldcover_forest_mask,
    classify_forest,
    fetch_worldcover_hrefs,
    majority_filter_mask,
    read_ref_info,
)
from .lband import LbandDeclineDetector
from .coh_delta import CoherenceDeltaDetector

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
    "LbandDeclineDetector",
    "CoherenceDeltaDetector",
    "background_offset_db",
    "common_polarizations",
    "compute_wi",
    "extract_polarization",
    "filter_small_objects",
    "mask_from_threshold",
    "median_filter_nan",
    "pair_by_polarization",
    "forest_mask",
    "DEFAULT_FOREST_CLASSES",
    "bbox_4326",
    "build_forest_mask",
    "build_forest_mask_from_rasters",
    "build_worldcover_forest_mask",
    "classify_forest",
    "fetch_worldcover_hrefs",
    "majority_filter_mask",
    "read_ref_info",
]
