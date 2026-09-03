# Release Notes — v1.0.0 (03.09.2026)

First stable release: the plugin leaves the single-sensor C-band
method and ships THREE validated detection modes in one GUI, plus the
infrastructure lessons of the 01–03.09 sessions. 127 unit tests
(90 → 127) pass on the clean environment.

## New: L-band decline mode (`lband_decline`)

* ALOS PALSAR / PALSAR-2 annual mosaics (HH/HV) work out of the box
  (Planetary Computer or local GeoTIFFs; DN auto-converted to dB).
* Physics after Tanase et al. 2018: windthrown forest LOSES L-band
  backscatter, so the decline index
  `LDI = (HH_pre − HH_post) + (HV_pre − HV_post)` is positive over
  windthrow — the opposite sign of the C-band WI.
* Implemented as a subclass of the C-band detector: composites,
  background normalization, adaptive/fixed thresholds, object
  cleanup, forest mask and polygonisation are all shared.
* Validated on the 2017 European Russia events: invAUC 0.870 (dHH,
  ID666 squall line) and 0.905 (dHV, ID694 tornado).
* Default adaptive offset lowered to 2.0 dB (annual mosaics, 12
  months between epochs); a single channel can be forced with
  `polarizations=["hh"|"hv"]`.

## New: Coherence DiD mode (`coh_delta`)

* Input: two ASF HyP3 INSAR-GAMMA products (unpacked folder, `.zip`
  or `*_corr.tif`) — the pre/post pair plus a same-season control
  pair of the same frames. The control layer is auto-warped onto the
  pre/post grid.
* Metric: `dcoh = coh(control) − coh(prepost)`, positive over
  windthrow; cancels static anomalies and seasonal drift.
* Validated on the real HyP3 products of this project: AUC 0.908 /
  excess +0.308 (ID694 tornado) and AUC 0.671 / excess +0.140 (ID666
  squall line) — an exact match with the research pipeline step12b.
* Robust adaptive threshold: background MEDIAN + `a` (default 0.25),
  because autumn scenes drift the whole background upward.
* Sane water-mask heuristic: product water masks claiming > 50 %
  water are corrupt (product 5748: 99.6 %) and are ignored with a
  warning; registered no-data and non-physical values are excluded
  from statistics.
* Default minimum object size 6 px (80 m pixels: 27 px would be
  17 ha).
* Without a control pair the mode degrades to static decorrelation
  scoring (1 − coherence) with an explicit warning.

## GUI

* New "Detection method" selector on the Windthrow tab: C-band WI /
  L-band decline / Coherence DiD. Inputs adapt to the selected
  method (file stacks vs HyP3 product pickers), the method hint
  updates, the minimum-object default auto-switches 27 px (10 m)
  ↔ 6 px (80 m), and the WorldCover auto-mask is offered only where
  a radar grid exists.
* Result dialogs distinguish dB (WI/LDI) and coherence (dCoH)
  units and report ignored corrupt water masks.

## Infrastructure / safety

* HyP3 INSAR-GAMMA products downloaded before the 17.09 expiry and
  stored outside the repository (`work_data/hyp3_products/`);
  the DiD mode reads exactly this layout.
* Credit accounting documented: HyP3 Basic = 8000 credits/month
  (renewing), INSAR-GAMMA 80 m = 10 credits/pair; the planned DiD
  validation plan costs 60–100 credits (~1 % of the limit).

## Tests

* 37 new tests: L-band sign/suffix/polarisation logic and the full
  GDAL chain on synthetic PALSAR scenes; coherence product
  discovery (folder / zip / tif), the sane water-mask heuristic,
  warp-fill sentinel exclusion and the full DiD chain on synthetic
  coherence pairs.
* Total: **127 passed** on Python 3.13 + GDAL 3.10 + NumPy 2.2 +
  SciPy 1.16.

## Known limitations

* The L-band mode expects PALSAR-style annual pairs; sub-annual
  L-band stacks work but the adaptive offset may need retuning.
* The coherence mode targets HyP3 INSAR-GAMMA 80 m products; other
  coherence sources work if they are single-band [0, 1] rasters on
  a common grid, but the water-mask heuristic is HyP3-specific.
* ID666-style storm-contaminated control pairs remain the main
  confounder of the DiD mode — order controls from quiet periods.
