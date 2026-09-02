# Release Notes — v0.7.0 (Windthrow edition)

## Summary

The generic "Sentinel-1 SAR Plugin" (oil spill / flood / composites)
has been refocused into the **Sentinel-1 Windthrow Detector**: all
marine / generic analysis routines were removed and a complete
bi-temporal windthrow workflow was added, following Rüetschi et al.
(2019, *Remote Sensing* 11(2):115).

## Added

- **Windthrow Detection tab**
  - Pre-storm / post-storm image stacks (file + folder pickers,
    polarisation auto-detection from file names, duplicate guard).
  - Per-polarisation **median compositing** of multi-date stacks
    (grid-mismatched inputs are warped onto the reference grid
    automatically).
  - **Windthrow Index** WI = ΔVV + ΔVH (dB) computed in streaming
    512-row bands (full-scene safe).
  - **Adaptive threshold** mean(WI) + a dB (paper optimum a = 2.9) or
    **fixed threshold** mode; mean optionally restricted to an
    analysis/forest mask (raster or vector input).
  - Optional median filter on WI (3/5/7) before thresholding.
  - **Minimum object size** filter (8-connected, paper optimum
    n = 27 px).
  - Outputs: `<base>_wi.tif` (float32), `<base>_mask.tif` (uint8),
    `<base>.gpkg`/`.shp` polygons with **area_ha** attribute, plus
    reusable pre/post composites.
  - Determinate progress (0–100) with stage labels and Cancel support.
  - Result summary dialog: mean WI, threshold used, object count.
- `sources/windthrow.py` — detection module (pure-numpy helpers kept
  unit-testable without GDAL).
- `METHOD.md` — method note with parameters and references.
- `TESTING_PLAN.md` — real-data validation protocol using the Shikhov
  et al. 2020 windthrow database (European Russia, 1986–2017).
- Settings: default WI offset `a` and default minimum object size.

## Removed

- Oil spill detection (fixed threshold + CA-CFAR).
- Flood / single-date water mask.
- RGB composite and dual-pol polarization composite.
- `sources/analyzers.py`, `tests/test_analyzers.py` and the
  development-process documents of the old plugin (spec/process/
  instructions/review/GitHub release notes).

## Changed

- Product/orbit tooltips and polarisation hints rewritten for the
  windthrow use case (RTC recommended; single orbit direction
  required; both VV+VH needed).
- Settings keys `defaultOilThresholdDb` / `defaultFloodThresholdDb`
  replaced by `defaultWiOffsetDb` / `defaultMinObjectPx`.
- Plugin renamed to "Sentinel-1 Windthrow Detector"
  (package folder `sentinel1_windthrow_plugin`).

## Compatibility

- QGIS ≥ 3.28, unchanged dependencies (numpy, scipy, GDAL; stdlib-only
  STAC client). Old saved settings keys are ignored gracefully.

## Tests

72 pytest cases (preprocessor, PC client, PC source, windthrow),
including 4 end-to-end GDAL pipeline tests on synthetic scenes
(compositing, WI, adaptive threshold, mask, polygonisation, vector
mask rasterisation).
