# Release Notes — v1.1.0 (2026-09-06)

## GFW Hansen GFC forest mask on the year preceding the event

The rule of report ed.8 §9 («правило эпохи маски») is now built into
the plugin.  For a storm in year `Y` the forest mask is reconstructed
from Hansen GFC-2024-v1.12 as

```
forest_candidate(Y) = treecover2000 >= tau AND NOT (1 <= lossyear <= Y-2001)
forest_background(Y) = treecover2000 >= tau AND NOT (1 <= lossyear <= Y-2000)
```

* `forest_candidate` — the detection-area mask: losses of 2001..Y−1
  removed, **event-year losses kept** (they are the windthrow).
* `forest_background` — the statistics mask: event-year losses removed
  as well («пиксели с потерей в год события из фона исключаются, но из
  маски-кандидата не убираются»).

### Details

* New mask source **"GFW Hansen GFC — forest on the year before the
  event"** in the Windthrow Detection tab, with an event-year spinbox
  (2001–2024, GFC-2024-v1.12 loss years).  Aliases accepted by
  `build_forest_mask`: `gfc` / `gfw` / `hansen`.
* Parameters: `tau = 30 %` (grid 20/30/50 robust, ed.8 §9), forest
  fraction ≥ 0.5 per aggregated pixel (mirrors the validated 30 m →
  80 m rescore), optional majority filter (OFF by default to match the
  rescore).
* Layers are read through `/vsicurl/` **window by window** (the
  40000×1 scanline tiles transfer only the AOI rows — no tile
  downloads, no authentication, pure public GCS URLs).  Multi-tile
  AOIs are mosaicked via VRT.  Tile naming handles the
  upper-left-corner convention (`70N_040E` for 61–62 N — the pitfall
  caught during the rescore).
* **Coherence DiD**: `CoherenceDeltaDetector.detect_file` gained
  `background_mask_path` — a statistics-only mask (adaptive median /
  mean and their threshold come from the background sample;
  detections are NOT restricted by it).  The GFC source therefore
  writes two masks in this mode: `<base>_forest_gfc<Y>.tif`
  (candidates → analysis mask) and `<base>_forestbg_gfc<Y>.tif`
  (clean forest → background mask), exactly the validated rescore
  semantics.
* **WI / L-band modes**: the GFC candidate mask is passed as
  `forest_mask_path` (detections + threshold mean inside forest@Y−1).
* GUI: mask-source combo extended, GFC event-year spinbox (2017
  default), tooltips document the rule and the WorldCover
  "mask from the future" caveat; output-hint label lists the new
  files.  WorldCover stays available for near-real-time (monitoring)
  runs.

### Validation

Report ed.8 §9 (12 events of 2015–2017, coh_delta): GFC@Y background
shrinks 28–53 %, mean AUC 0.620 → 0.618 (unchanged within noise) —
the mask fixes background semantics, not the metric; single-epoch
WorldCover-2021 left 0 background pixels around ID655 (broke the
method).  Live `/vsicurl/` probe on ID666 (Komi): 1° window read in
~38 s per layer, forest@2017 fraction 0.60.

### Tests

+6 (GFC recipe, tile naming, layer URL, GDAL mask builder with both
variants, dispatcher argument checks, coherence background-mask
semantics) — **133 passed**.

### Files

`sources/forest_mask.py` (GFC source), `sources/coh_delta.py`
(`background_mask_path`), `sentinel1_plugin_dialog.py` (GUI),
`sources/__init__.py` (exports), `METHOD.md` §3b, `README.md`.
