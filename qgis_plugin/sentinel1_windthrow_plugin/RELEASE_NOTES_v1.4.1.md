# v1.4.1 — engineering correctness pack over v1.3.1

Base: the installed v1.3.1 (opt-in `smooth_size` NaN-aware box mean kept
untouched, default OFF — the frozen v1.2.2 behaviour is unchanged).
This release adapts the correct engineering parts of the external
v1.4.0 review build and rejects its uncalibrated defaults.

## Taken (verified locally)

1. **N-01 — NaN-aware median filter.** The old `median_filter_nan` filled
   NaNs with `+1e6`; majority-NaN windows passed the positive threshold and
   formed spurious polygons at NoData/mask borders. Replaced by a
   sliding-window `nanmedian` (identical boundaries between chunked and
   full-tile processing; scipy fast path for all-finite tiles).
2. **G-01/G-02 — CRS-aware alignment.** `_same_grid` now compares CRS
   (`osr.SpatialReference.IsSame`) in addition to shape/affine; `gdal.Warp`
   receives an explicit `dstSRS`, `srcNodata` and verifies its own output;
   rotated reference grids are refused instead of silently misregistered.
3. **G-03 — categorical masks via nearest neighbour**, bilinear reserved
   for continuous coherence/radiometry rasters. Rasterize gets `outputSRS`.
4. **G-04 — object areas in EPSG:6933.** OGR `GetArea()` in a geographic
   CRS returns degrees²; EPSG:3857 metres additionally inflate boreal
   areas 2–3x (the same error was found during the 2026-10 D5 evaluation).
   `area_ha` now comes from the global equal-area projection.
5. **S-01 — explicit temporal hypotheses** (`sources/coherence_science.py`):
   `event_drop` (default; frozen sign, project validation corpus) and
   `persistent_gain` (experimental; requires a pre/pre baseline strictly
   before the post/post target pairs). The persistent-gain high-coherence
   "non-forest" heuristic is recorded as diagnostics only, never as a
   classification.
6. **Preflight checks** (v1.4.0): GAMMA orbit-code/polarization mismatch
   rejection, burst track mismatch rejection, `validate_temporal_pair`
   chronology/baseline warnings.
7. **Provenance** — `*_science.json` (schema 2) written per run: hypothesis,
   products and metadata, masks, medians, threshold, filter parameters,
   `candidate_map_unvalidated` status; plus `plugin_version`.
8. `tools/evaluate_coherence.py` + rank-AUC with ties
   (`evaluate_reference_masks`) for reference-mask diagnostics.

## Rejected (from v1.4.0, with reasons)

1. **Default UI hypothesis = persistent_gain.** On the project measurement
   corpus the persistent-gain family does not survive the object level:
   PA 0.056 vs PA 0.560 for event drop (reference polygons, 8 events),
   UA = 0 % over 466 sampled objects (20 events) while its pixel AUC ~0.78.
   The pixel signal does not become evidence. Defaults remain
   `event_drop` and `a = 0.25`.
2. **Default offset a = 0.05.** Uncalibrated; at the project default
   0.25 the corpus precision is already low (0.4–1.7 %). The UI keeps
   0.25 with an honest tooltip.
3. The v1.4.0 safeguard *wiping* in persistent-gain mode was NOT taken:
   numeric diagnostics stay, marked informational.
4. The old "validated AUC 0.908 / FAR 8-14 %" tooltips are gone; project
   numbers are stated honestly instead.

## Tests

- GDAL-free suite: green without `osgeo` (144 passed, 47 skipped in the
  reference run); `test_pipeline.py` is now properly skipped without GDAL.
- Contract fix: a zip without `*_corr.tif` raises `FileNotFoundError`
  when GDAL is present, `RuntimeError` without GDAL (was: unconditional
  RuntimeError caught by a FileNotFoundError-expecting test).

## Honesty statement

Outputs are **candidate forest-disturbance maps**. Neither mode is a
validated windthrow classifier; the project independent evaluation
(D5.1: UA = 0 % over 466 objects; D5.2 blind re-digitization in progress)
defines the boundary of what the coherence signal currently proves.
