# Release Notes — v0.9.0

## Forest mask (ESA WorldCover, v0.9)

False alarms of the WI detector are dominated by non-forest land cover
(agricultural fields, clearcuts). v0.9 adds a **forest mask** that
restricts both the detections and the adaptive-threshold mean to forest,
exactly like the forest mask in Rüetschi et al. (2019).

### New in the Windthrow tab

* **"Restrict detection to forest mask"** now offers two sources:
  * **ESA WorldCover 10 m (auto-download)** — the plugin searches the
    `esa-worldcover` collection on Microsoft Planetary Computer for your
    AOI, takes the Tree-cover class (10), resamples it onto the radar
    grid (nearest), applies a 3×3 majority filter and writes
    `<base>_forest_wc<year>.tif`. Epoch 2020 / 2021 selectable; pick the
    year closest to (but not after) the storm.
  * **Custom file** — your own raster (values > 0 = forest) or vector
    (polygons = forest), rasterised/warped automatically.
* When a forest mask is active, the adaptive-threshold mean and the
  background-normalisation offsets are computed over the forest sample
  (unless a separate background mask is supplied).
* The forest mask is intersected with any analysis mask; the resolved
  mask is reported in the completion message and loaded to the map.

### API changes

* `WindthrowDetector.detect_file(..., forest_mask_path=...)` — new
  optional parameter; detections restricted to its intersection with
  `analysis_mask_path`; result dict gains `"forest_mask"`.
* New module `sources/forest_mask.py`: `build_worldcover_forest_mask`,
  `build_forest_mask_from_rasters`, `classify_forest`,
  `majority_filter_mask`, `bbox_4326`, `read_ref_info`,
  `fetch_worldcover_hrefs`, `build_forest_mask` dispatcher.

### Validation (step9, event ID666 — squall 30.07.2017, ~950 ha)

Same thresholds as the step7 baseline (background statistics on the
3 km ring), only the detection area changes:

| Variant | Mask | PA | UA | Detected area |
|---------|------|-----|-----|---------------|
| A (pair, adaptive) | none | 0.126 | 0.015 | 9 506 ha |
| A | WorldCover 2020 | 0.126 | **0.017** | **7 730 ha (−19 %)** |
| C (stack, adaptive) | none | 0.111 | 0.020 | 7 335 ha |
| C | WorldCover 2020 | 0.109 | **0.023** | **4 744 ha (−35 %)** |
| D (stack, fixed+norm) | none | 0.084 | 0.023 | 5 740 ha |
| D | WorldCover 2020 | 0.084 | **0.026** | **3 427 ha (−40 %)** |

* **PA is untouched** (reference coverage by the mask 98–100 %) — the
  mask removes false alarms, never true polygons.
* **UA improves by 13–15 % relative** and the false-alarm area drops by
  up to 40 %; the remaining FPs are small forest-internal objects
  (clearcuts, wet depressions) — larger `min_pixels` / index redesign
  are the next levers.
* A VH-threshold proxy (median pre VH > −18.5 dB) is nearly a no-op on
  summer taiga scenes (VH is high everywhere) — WorldCover is the
  recommended source.

### Tests

76 → **90 passed** (14 new: class binarisation, majority filter incl.
NumPy fallback, warp-to-grid builder, bbox_4326, STAC stubs, detector
restriction / intersection / statistics / backward-compatibility).
