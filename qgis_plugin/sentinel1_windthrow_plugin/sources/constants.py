"""Centralised numeric constants of the plugin (single source of truth).

Extracted in the v1.3.0 refactor from ``windthrow.py`` / ``coh_delta.py`` /
``forest_mask.py`` so the detectors, the shared pipeline and the GUI read
one definition instead of drifting copies.  This module holds pure values
only: no plugin imports, no GDAL dependency — it is importable from any
layer (numpy_ops, raster_utils, hyp3_utils, detectors, tests).

SCIENCE DEFAULTS ARE FROZEN.  The values below reproduce the validated
v1.2.2 behaviour (Rüetschi et al. 2019 WI pipeline, the coherence-DiD
report, the GFC reconstruction of the article).  A refactor must keep
every value bit-identical; changing one requires re-running the
validation protocol (METHOD.md §9) and a science review.
"""

from __future__ import annotations

from typing import Tuple

# ======================================================================
# Chunked processing / no-data conventions
# ======================================================================
#: Default row-band height for chunked processing (pixels).  512 rows of
#: a full Sentinel-1 IW frame (~2500 columns) is a few MB of float32 per
#: working copy — bounds RAM for both detectors; see README "Memory".
CHUNK_ROWS = 512

#: Generic no-data value written to float32 index rasters (WI, dcoh, LDI).
INDEX_NODATA = -9999.0

#: Fraction of negative pixels marking a chunk as "already in dB"
#: (kept in sync with the preprocessor's heuristic).
DB_DETECT_FRACTION = 0.05
#: Values below this are no-data sentinels, not physical dB.
DB_NODATA_FLOOR = -100.0
#: Sentinel used to neutralise NaN inside the median filter window.
MEDIAN_SENTINEL = 1.0e6

# Original spellings kept as aliases — callers and tests import them
# from the historical modules; the values are shared through this file.
_CHUNK_ROWS = CHUNK_ROWS
WI_NODATA = INDEX_NODATA
DCOH_NODATA = INDEX_NODATA
_DB_DETECT_FRACTION = DB_DETECT_FRACTION
_DB_NODATA_FLOOR = DB_NODATA_FLOOR
_MEDIAN_SENTINEL = MEDIAN_SENTINEL

# ======================================================================
# Frozen science defaults (validated v1.2.2 configuration)
# ======================================================================
#: C-band WI adaptive offset ``a`` in dB (Rüetschi et al. 2019 optimum
#: ~2.9 dB above the mean WI).
DEFAULT_WI_OFFSET_DB = 2.9
#: Fixed-mode WI threshold (WindthrowDetector signature default).
DEFAULT_WI_FIXED_DB = 3.0
#: Minimum object size at 10 m postings (27 px ~ 0.27 ha; paper §4).
DEFAULT_MIN_PIXELS_10M = 27
#: Median-filter window applied to the index before thresholding.
DEFAULT_MEDIAN_SIZE = 3
#: Coherence DiD adaptive offset (CoherenceDeltaDetector code default;
#: the module docstring's "0.10" is stale text, the shipped value is
#: 0.25 — frozen as-is).
DEFAULT_COH_OFFSET = 0.25
#: Fixed-mode dcoh threshold (CoherenceDeltaDetector signature default).
DEFAULT_COH_FIXED = 0.25
#: Minimum object size at 80 m postings (6 px ~ 3.8 ha; ~0.96 ha at the
#: 40 m Burst InSAR spacing).
DEFAULT_MIN_PIXELS_80M = 6
#: Spatial averaging (box-mean) window applied to the index before
#: thresholding — v1.3.1, «вариант A» of the multilook experiment.
#: 1 = OFF: the frozen v1.2.2 behaviour is preserved and the feature is
#: opt-in from the detector argument / the GUI.  At the 80 m
#: INSAR-GAMMA posting a 3x3 window averages 240 m and a 5x5 one
#: 400 m; the per-pixel noise of one 12-day pair (sigma ~ 0.20
#: coherence units) falls as sqrt(N) while a windthrow patch
#: (>= 10 ha = 16 px at 80 m) stays resolved.  Measured on the 23-event
#: Shikhov re-run: 5x5 takes the ring (2-10 km) AUC from 0.720 to 0.772
#: and the frame-wide flagged area from 11.0 % to 5.1 % at an unchanged
#: threshold offset.  Caveat: targets NARROWER than the window (thin
#: squall-line strips, 1-3 px at 80 m) are diluted — see
#: testing/ОТЧЁТ_вариантА.md.
DEFAULT_SMOOTH_SIZE = 1

# ======================================================================
# Coherence DiD (coh_delta) — ranges, safeguards, water mask
# ======================================================================
#: Physically plausible coherence range; anything outside (e.g. the
#: -9999 fill of a warped control product) is treated as no-data.
COH_MIN: float = -0.01
COH_MAX: float = 1.01

#: v1.2.2 forest safeguard «вне_леса@Y» (article, ID654 case): mean
#: pre/post coherence inside the zone above this value is the
#: bare-ground / non-forest signature (ID654: 0.635 with AUC 0.051).
COH_NONFOREST_MAX = 0.6

#: v1.2.2 forest safeguard: forest share of the scene below this
#: value puts the zone outside the method applicability limit
#: (article: «при доле леса GFC@Y ниже 0,3 плагин выставляет флаг
#: вне_леса@Y»).
FOREST_SHARE_MIN = 0.3

#: A water mask claiming more than this fraction of a frame is corrupt
#: (step12b sane-mask heuristic; HyP3 product 5748 shipped a mask that
#: marked 99.6 % of an inland forest frame as water).
DEFAULT_MAX_WATER_FRAC = 0.5

#: HyP3 water-mask encoding (ASF product guides, GAMMA **and** ISCE):
#: «Pixel values of 1 indicate land and 0 indicate water».  v1.0/v1.1
#: wrongly assumed 1 = water — the v1.2 fix is documented in the
#: coh_delta module docstring and RELEASE_NOTES_v1.2.0.
LAND_VALUE = 1
WATER_VALUE = 0

# ======================================================================
# Hansen / UMD Global Forest Change (GFW) — forest_mask.py (v1.1)
# ======================================================================
#: ESA WorldCover legend — 10 = Tree cover.
DEFAULT_FOREST_CLASSES: Tuple[int, ...] = (10,)
#: Published GFC version used by the project (report ed.8 §9).
GFC_VERSION = "GFC-2024-v1.12"
#: Public Google Cloud Storage bucket of the GFC tiles (no auth needed).
GFC_BASE_URL = (
    "https://storage.googleapis.com/earthenginepartners-hansen/"
    + GFC_VERSION)
#: Layers required by the reconstruction recipe.
GFC_LAYERS = ("treecover2000", "lossyear")
#: Valid event years for GFC-2024-v1.12 (loss years 2001-2024).
GFC_LOSS_YEARS = (2001, 2024)
#: Default treecover2000 threshold in % (tau grid 20/30/50 was tested —
#: conclusions robust; tau=30 is the primary value of report ed.8 §9).
GFC_DEFAULT_TAU = 30
#: Default forest-fraction threshold of an aggregated target pixel
#: (mirrors the validated 30 m -> 80 m "average" rescore).
GFC_DEFAULT_FRAC = 0.5
