# Agent Guide: Improvement Plan for `sar_windthrow` QGIS Plugin (v1.4.1)

**Repository:** https://github.com/nadiopt-cell/sar_windthrow  
**Plugin path:** `qgis_plugin/sentinel1_windthrow_plugin/`  
**Current version:** 1.4.1 (197 tests; актуализировано 01.11.2026 — исходник плагина хранится во внешнем репозитории, точная сверка констант с ним вне этой песочницы)  
**Target audience:** AI coding agent / human maintainer implementing refactors and hardening.

This document is a **executable improvement specification**. Follow tasks in priority order. Do not change detection semantics (WI formula, LDI sign, DiD definition, GFC@Y epoch rule, forest safeguard thresholds) unless a task explicitly says so. Preserve all existing tests; add new ones for every behavioural change.

---

## 0. Context for the agent

### What the plugin does
QGIS plugin for bi-temporal windthrow (storm forest damage) mapping from SAR:

| Mode | Index | Data | Notes |
|------|--------|------|--------|
| C-band WI | `WI = ΔVV + ΔVH` (dB) | Sentinel-1 RTC/GRD via Planetary Computer STAC | Rüetschi et al. 2019; adaptive threshold mean+2.9 dB |
| L-band decline | `LDI = (HH_pre−HH_post)+(HV_pre−HV_post)` | ALOS PALSAR mosaics | Tanase et al. 2018; inverted sign vs C-band |
| Coherence DiD | `dcoh = coh_control − coh_prepost` | ASF HyP3 INSAR-GAMMA / Burst InSAR | Positive over damage; same-burst validation |

Key hard-won behaviours (do **not** regress):
- GFC Hansen forest mask reconstructed on year **before** the event (v1.1).
- HyP3 water mask convention: **1 = land, 0 = water** (fixed in v1.2).
- Burst products: control and pre/post must share the same burst footprint.
- Forest safeguard «вне_леса@Y» (v1.2.2): mean pre/post coh > 0.6 **or** forest share < 0.3 → warning flag, run continues.
- Chunked row processing (`_CHUNK_ROWS = 512`) so full S1 scenes stay out of RAM.
- Pure-numpy helpers must remain importable **without** GDAL/QGIS for unit tests.

### Critical file map
```
qgis_plugin/sentinel1_windthrow_plugin/
├── __init__.py
├── sentinel1_plugin.py              # toolbar / menu
├── sentinel1_plugin_dialog.py       # 4-tab UI + QgsTask
├── logger.py                        # QgsMessageLog + stderr fallback
├── metadata.txt
├── sources/
│   ├── __init__.py                  # public API re-exports
│   ├── base.py                      # Scene, BaseSARSource, OperationCancelled
│   ├── windthrow.py                 # ~1070 lines — WI detector + many shared helpers
│   ├── coh_delta.py                 # ~1100 lines — DiD + HyP3 parsing + water mask
│   ├── forest_mask.py               # ~760 lines — GFC + WorldCover + user mask
│   ├── lband.py                     # LDI detector
│   ├── preprocessor.py              # dB, Lee, land/water
│   ├── planetary_computer.py        # STAC source
│   └── pc_client.py                 # stdlib STAC client
└── tests/
    ├── test_windthrow.py
    ├── test_coh_delta.py
    ├── test_forest_mask.py
    ├── test_lband.py
    ├── test_preprocessor.py
    ├── test_pc_client.py
    └── test_planetary_computer_source.py
```

### Non-negotiable constraints
1. **No new pip dependencies** for the plugin runtime (stdlib + numpy/scipy/GDAL already in QGIS).
2. Pure computation modules must stay testable outside QGIS (`logger` already falls back to stderr).
3. Public API in `sources/__init__.py` must stay backward-compatible or be version-bumped with release notes.
4. Detection numbers from validation (AUC, default a=2.9, min_pixels=27 at 10 m / 6 at 80 m, COH_NONFOREST_MAX=0.6, FOREST_SHARE_MIN=0.3) must not silently change.
5. Russian user-facing strings in GUI/logs may exist; keep language consistent with surrounding code.

---

## 1. Priority overview

| Priority | Theme | Goal |
|----------|--------|------|
| **P0** | Extract shared raster + HyP3 utilities | Shrink 1000-line modules; single source of truth |
| **P0** | Structured result types | Replace ad-hoc `dict` results with TypedDict/dataclass |
| **P1** | Common detection pipeline | DRY threshold → filter → vectorize |
| **P1** | Central constants / DetectionConfig | One place for paper defaults |
| **P1** | CI (pytest + coverage) | Prevent regressions |
| **P2** | Error taxonomy + GUI surfacing | Clearer failures for users |
| **P2** | Adaptive chunking / memory | Safer on large scenes |
| **P3** | mypy, packaging polish | Long-term maintainability |

Work **P0 → P1 → P2 → P3**. After each P0/P1 task: run the full test suite and ensure 150+ tests pass.

---

## 2. P0 — Extract shared modules

### 2.1 Create `sources/raster_utils.py`

**Goal:** Move GDAL helpers that are currently private in `windthrow.py` (and duplicated/used by `coh_delta.py`) into one module.

**Move (or re-export) from `windthrow.py`:**
- `_CHUNK_ROWS`
- `WI_NODATA` (keep name or introduce generic `INDEX_NODATA = -9999.0`)
- `_read_raster_info`
- `_same_grid`
- `ensure_aligned`
- `_create_output_vector` (if present)
- `_pixel_area_m2`
- `_resolve_mask_raster`
- `_intersect_masks`
- Any other pure “open raster / warp / polygonize” helpers used by both detectors

**Rules:**
- Keep function signatures stable.
- In `windthrow.py` and `coh_delta.py`, import from `raster_utils` instead of defining locally.
- Prefer public names without leading underscore for functions that become shared API; keep private helpers private inside `raster_utils`.
- Add a short module docstring: purpose, nodata convention, chunk size rationale.

**Tests:** Existing tests must pass without change. Optionally add 2–3 unit tests for `_same_grid` / `ensure_aligned` with tiny in-memory or temp GeoTIFFs if not already covered.

### 2.2 Create `sources/hyp3_utils.py`

**Goal:** Isolate HyP3 product naming, zip handling, and water-mask logic from `coh_delta.py`.

**Move from `coh_delta.py`:**
- `_CORR_SUFFIX`, `_WATER_SUFFIXES`, `LAND_VALUE`, `WATER_VALUE`, `DEFAULT_MAX_WATER_FRAC`
- `_GAMMA_RE`, `_ISCE_RE`
- `_granule_names`
- `parse_hyp3_product`
- `find_correlation_tif`, `find_water_mask` (if defined there)
- `sane_water_mask`
- `_validate_did_pair` (or keep next to detector if it depends on detector state — prefer moving)

**Rules:**
- `parse_hyp3_product` return shape must stay compatible with current callers.
- Water-mask encoding comments (ASF guides: 1=land, 0=water) must remain in the docstring.
- Re-export public symbols from `sources/__init__.py` as today (`parse_hyp3_product`, `find_correlation_tif`, `find_water_mask`, `sane_water_mask`).

**Tests:** All `test_coh_delta.py` cases for parsing, mixed families, burst mismatch, corrupt water mask must still pass.

### 2.3 Create `sources/numpy_ops.py` (optional but recommended)

**Goal:** Pure-numpy ops shared by WI / LDI / DiD, free of GDAL.

**Candidates from `windthrow.py`:**
- `extract_polarization`, `pair_by_polarization`, `common_polarizations`
- `to_db_domain_chunk`
- `compute_wi`
- `adaptive_threshold`, `mask_from_threshold`
- `filter_small_objects`
- `background_offset_db`
- `median_filter_nan`
- Constants: `_DB_DETECT_FRACTION`, `_DB_NODATA_FLOOR`, `_MEDIAN_SENTINEL`

**Rules:**
- No `from osgeo import ...` in this file.
- scipy import remains optional with graceful degradation where already done.
- Update imports in `windthrow.py`, `lband.py`, `coh_delta.py`, and `sources/__init__.py`.

### 2.4 Acceptance criteria for P0
- [ ] `windthrow.py` and `coh_delta.py` each lose ≥200–300 lines of duplicated/helper code.
- [ ] No circular imports (`raster_utils` must not import detectors).
- [ ] `python -m pytest qgis_plugin/sentinel1_windthrow_plugin/tests -q` → all green.
- [ ] Plugin still loads in QGIS (manual or document “smoke: open dialog”).

---

## 3. P0 — Structured result types

### 3.1 Define result dataclasses / TypedDicts

Add `sources/types.py` (or `results.py`):

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class ForestSafeguard:
    triggered: bool
    mean_prepost_coh: Optional[float] = None
    forest_share: Optional[float] = None
    reason: str = ""  # e.g. "mean_coh>0.6", "forest_share<0.3", ""

@dataclass
class DetectionResult:
    index_path: str
    mask_path: str
    vector_path: str
    mode: str  # "wi" | "lband_decline" | "coh_delta"
    threshold: float
    n_objects: int
    total_area_ha: float
    forest_safeguard: Optional[ForestSafeguard] = None
    extras: Dict[str, Any] = field(default_factory=dict)
    # optional paths: pre/post composites, norm index, etc.
```

Adapt field names to **match existing keys** returned by detectors today so GUI/tests need minimal changes. Prefer:

1. Implement dataclass.
2. Add `.to_dict()` that produces the **legacy** dict shape.
3. Detectors return dataclass; dialog uses `.to_dict()` or attributes.
4. Gradually switch tests to attributes.

### 3.2 Wire into detectors
- `WindthrowDetector.run` / equivalent entry points.
- `LbandDeclineDetector`.
- `CoherenceDeltaDetector` (must populate `forest_safeguard`).

### 3.3 Acceptance
- [ ] GUI still shows paths, object count, safeguard warning.
- [ ] Tests that assert on result dict keys updated or use `.to_dict()`.
- [ ] No silent key renames without release note.

---

## 4. P1 — Common detection pipeline

### 4.1 Introduce `sources/pipeline.py`

**Template method** used by all three modes after the mode-specific **index raster** is written:

1. Optional median filter on index (`median_filter_nan`).
2. Adaptive or fixed threshold → binary mask.
3. Intersect with analysis / forest / water masks.
4. `filter_small_objects(min_pixels)`.
5. Write mask GeoTIFF.
6. Polygonize → GeoPackage/Shapefile with `area_ha`.
7. Progress/cancel callbacks at each stage.

**Sketch:**

```python
@dataclass
class PipelineConfig:
    threshold_db_or_coh: float  # absolute or already-computed adaptive value
    min_pixels: int
    median_size: int = 3
    nodata: float = -9999.0
    out_base: str = ""
    vector_format: str = "GPKG"  # or ESRI Shapefile

def run_postprocess(
    index_path: str,
    ref_info: dict,
    config: PipelineConfig,
    analysis_mask_path: Optional[str] = None,
    progress_cb: ProgressCallback = None,
    cancel_cb: CancelCallback = None,
) -> DetectionResult:
    ...
```

### 4.2 Refactor detectors to use the pipeline
- WI / LDI / DiD only compute the float index + choose threshold.
- Call shared postprocess for cleanup + vectorization.

### 4.3 Acceptance
- [ ] Identical outputs on existing fixtures (bit-identical or documented float tolerance).
- [ ] One place to fix polygonize/area bugs.
- [ ] Tests for min_pixels and median behaviour still pass.

---

## 5. P1 — Constants and DetectionConfig

### 5.1 Create `sources/constants.py`

Centralize:

```python
# Processing
CHUNK_ROWS = 512
INDEX_NODATA = -9999.0
DB_DETECT_FRACTION = 0.05
DB_NODATA_FLOOR = -100.0
MEDIAN_SENTINEL = 1.0e6

# C-band WI (Rüetschi 2019)
DEFAULT_WI_OFFSET_DB = 2.9
DEFAULT_MIN_PIXELS_10M = 27
DEFAULT_MEDIAN_SIZE = 3

# Coherence DiD
# v1.4.1: порог ЗАМОРОЖЕН на +0,25 после центрирования на медиану лесного фона своего домена
# (сцена — в плагине; кольцо 1–10 км вокруг эталона события — в валидационных прогонах; см. Табл. 2 статьи)
DEFAULT_COH_OFFSET = 0.25
DEFAULT_MIN_PIXELS_80M = 6
COH_MIN, COH_MAX = -0.01, 1.01
COH_NONFOREST_MAX = 0.6
FOREST_SHARE_MIN = 0.3
DEFAULT_MAX_WATER_FRAC = 0.5
LAND_VALUE = 1
WATER_VALUE = 0

# GFC
GFC_DEFAULT_TAU = 30  # treecover2000 %
# ... keep existing GFC_* names in sync with forest_mask.py
```

### 5.2 DetectionConfig dataclass
User-facing / settings-tab parameters in one object (defaults from constants). Persist via `QgsSettings` as today; map settings keys → `DetectionConfig` fields in the dialog.

### 5.3 Acceptance
- [ ] Magic numbers removed from detector bodies (or imported from constants).
- [ ] Changing a default in one place updates behaviour and docs references.

---

## 6. P1 — Continuous integration

### 6.1 Add project CI files (repo root or `qgis_plugin/`)

**Minimal GitHub Actions workflow** (`.github/workflows/test-plugin.yml`):

- Trigger: push/PR to paths under `qgis_plugin/`.
- Jobs:
  1. **Pure unit** (no GDAL): install numpy/scipy; run tests marked or filtered as pure-numpy if you introduce markers.
  2. **Full** (with GDAL): use a container or `ubuntu` + `libgdal-dev` + `gdal-bin` + pip `gdal` matching system, or document QGIS-based CI if preferred.

Practical baseline many QGIS plugins use:
- `pytest`
- `pytest-cov`
- Dependency install: `numpy`, `scipy`; GDAL via apt + env `GDAL_VERSION`.

### 6.2 `pytest.ini` or `pyproject.toml`
```ini
[pytest]
testpaths = qgis_plugin/sentinel1_windthrow_plugin/tests
pythonpath = qgis_plugin
addopts = -q --tb=short
```

### 6.3 Acceptance
- [ ] CI runs on every PR touching the plugin.
- [ ] Coverage report artifact (optional target ≥80% on `sources/`).

---

## 7. P2 — Errors and UX

### 7.1 Exception hierarchy
In `sources/errors.py`:

```python
class WindthrowPluginError(Exception): ...
class InputValidationError(WindthrowPluginError): ...
class ProductMismatchError(InputValidationError): ...  # burst / family
class DataAccessError(WindthrowPluginError): ...
class CancelledError(WindthrowPluginError): ...  # or keep OperationCancelled
```

Map existing `ValueError` messages for mixed HyP3 families / burst mismatch to `ProductMismatchError` with the same human-readable text.

### 7.2 GUI
- Catch typed errors in dialog/task finished slot; show short message + “details in log”.
- When `forest_safeguard.triggered`, keep non-blocking warning (current design).

### 7.3 Logging
- Prefer `log_warning` / `log_critical` over bare `print` or swallowed `except Exception: pass`.
- When deleting temp warps, log at debug/info if delete fails; never hide detection errors.

---

## 8. P2 — Memory and performance

### 8.1 Adaptive chunk rows
```python
def choose_chunk_rows(width: int, dtype_size: int = 4, max_bytes: int = 64 * 1024**2) -> int:
    # aim ~max_bytes per chunk row-band; clamp 128..2048
```
Use in raster loops instead of fixed 512 when beneficial; keep 512 as default constant.

### 8.2 GFC /vsicurl/
- Document GDAL config recommendations (`GDAL_DISABLE_READDIR_ON_OPEN`, `CPL_VSIL_CURL_CACHE_SIZE`) in METHOD.md or README.
- Avoid re-opening the same tile URL repeatedly inside one mask build (cache dataset handles per tile id for the duration of the call).

### 8.3 background_offset_db
- Prefer masked views / fewer full copies when sampling medians on large arrays (keep deterministic stride behaviour for tests).

---

## 9. P3 — Typing, docs, packaging

### 9.1 mypy
- Add `mypy.ini` with gradual strictness on `sources/`.
- Fix obvious `dict` / `Any` hotspots after TypedDict migration.

### 9.2 metadata.txt
- Fill `repository=` and `tracker=` with GitHub URLs.
- Keep `about=` informative but not longer than QGIS UI comfortably shows; detail stays in README.

### 9.3 Release process
- After refactors: bump version only if public behaviour or ZIP layout changes.
- Update `RELEASE_NOTES_vX.Y.Z.md` and `plugin_dist/` via existing `build_plugin_zip.py`.
- Re-run TESTING_PLAN.md smoke steps.

### 9.4 README / METHOD.md
- After extracting modules, update “Architecture” tree in plugin README.
- Cross-link constants to paper values (do not rewrite scientific claims).

---

## 10. Suggested implementation order (checklist for the agent)

```text
[ ] 1. Create sources/constants.py; switch imports (no behaviour change)
[ ] 2. Create sources/raster_utils.py; move helpers; fix imports
[ ] 3. Create sources/hyp3_utils.py; move parse/water/burst validation helpers
[ ] 4. Create sources/numpy_ops.py; move pure numpy helpers
[ ] 5. Run full pytest; fix breaks
[ ] 6. Add sources/types.py (DetectionResult, ForestSafeguard); adapters to_dict
[ ] 7. Wire detectors to return DetectionResult
[ ] 8. Add sources/pipeline.py; refactor three detectors to shared postprocess
[ ] 9. Pytest + manual QGIS smoke
[ ] 10. Add pytest.ini + GitHub Actions workflow
[ ] 11. errors.py + dialog handling
[ ] 12. Adaptive chunking (optional)
[ ] 13. metadata.txt repository/tracker; architecture docs
[ ] 14. Optional: mypy on sources/
```

---

## 11. Commands the agent should use

```bash
# From repo root (adjust if plugin is tested in isolation)
cd qgis_plugin
python -m pytest sentinel1_windthrow_plugin/tests -q

# Or with coverage
python -m pytest sentinel1_windthrow_plugin/tests --cov=sentinel1_windthrow_plugin/sources --cov-report=term-missing

# Build distributable ZIP (existing script)
python build_plugin_zip.py
```

When GDAL is missing in the agent environment: still run pure-numpy tests; document GDAL-required tests as skipped only if necessary — prefer installing GDAL in the sandbox when possible.

---

## 12. What NOT to do

- Do not add torch/tensorflow/SNAP/snappy/pystac-client as hard dependencies.
- Do not change default threshold 2.9 dB, min_pixels paper defaults, or safeguard 0.6 / 0.3 without an explicit science decision and release note.
- Do not invert water mask convention again.
- Do not require Earthdata/HyP3 credentials inside the main WI path (HyP3 is only for coh_delta inputs the user already downloaded).
- Do not remove chunked processing in favour of full-array loads.
- Do not rewrite the entire GUI in one pass; prefer backend refactors first.

---

## 13. Definition of done (overall)

The improvement effort is done when:

1. Shared logic lives in `raster_utils` / `hyp3_utils` / `numpy_ops` / `pipeline` with detectors focused on index formation.
2. Results are structured (`DetectionResult`) with legacy dict compatibility if needed.
3. Constants are centralized.
4. CI runs pytest on the plugin tests.
5. All previous tests pass; new tests cover extracted helpers and pipeline.
6. Plugin ZIP still installs in QGIS ≥ 3.28 and completes a WI dry run on sample data per TESTING_PLAN.md.
7. Short release note documents refactor-only vs any user-visible change.

---

## 14. Reference snippets (behaviour locks)

**WI (do not change formula):**
```text
dVV = VV_post - VV_pre
dVH = VH_post - VH_pre
WI = dVV + dVH   # positive → candidate windthrow
```

**DiD (do not change formula):**
```text
dcoh = coh_control - coh_prepost   # positive → candidate windthrow
```

**Forest safeguard (v1.2.2):**
```text
trigger if mean(coh_prepost in zone) > 0.6
       or forest_share < 0.3
# warn only; do not abort
```

**Water mask (v1.2+):**
```text
ASF: 1 = land, 0 = water
keep land for analysis
```

---

*End of agent guide. Implement incrementally, test after each extraction, keep science defaults frozen.*
