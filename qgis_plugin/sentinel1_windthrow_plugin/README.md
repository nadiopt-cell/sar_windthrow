# Sentinel-1 Windthrow Detector for QGIS

[![QGIS](https://img.shields.io/badge/QGIS-%E2%89%A5%203.28-589632?logo=qgis&logoColor=white)](https://qgis.org)
[![Version](https://img.shields.io/badge/version-0.9.0-blue.svg)]()
[![License: CC0](https://img.shields.io/badge/license-CC0%201.0-lightgrey.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-90%20passed-success.svg)]()

Search, download and preprocess Sentinel-1 SAR imagery in QGIS and map
**windthrow** (storm-damaged forest) with the bi-temporal
**Windthrow Index** method of Rüetschi, Small & Waser (2019,
*Remote Sensing* 11(2):115).

## What it does

| Tab                 | What it does                                                                    |
|---------------------|----------------------------------------------------------------------------------|
| Search & Download   | STAC search on Microsoft Planetary Computer (GRD **or** RTC) + COG download      |
| Preprocess          | Linear→dB, Lee speckle filter, optional land/water mask                          |
| Windthrow Detection | Pre/post composites → **WI = ΔVV + ΔVH** → threshold → object filter → polygons; optional **forest mask** (ESA WorldCover auto-download or your own file, v0.9)  |
| Settings            | Default folders + detection parameters, persisted in `QgsSettings`               |

Outputs of a detection run:

* `<base>_wi.tif` — Windthrow Index raster (float32, dB; **positive =
  backscatter increase = potential windthrow**)
* `<base>_mask.tif` — cleaned uint8 mask (255 = detected windthrow)
* `<base>.gpkg` (or `.shp`) — vectorised objects with an `area_ha`
  attribute
* `<base>_pre_<pol>.tif` / `<base>_post_<pol>.tif` — median composites
  (written when several dates are combined per polarisation)

## The method in 60 seconds

1. **Input stacks.** Collect 1–5 pre-storm and 1–3 post-storm scenes of
   the *same orbit direction* (post window ideally ≤ 2 weeks after the
   storm, matching the "map within ~2 weeks" target of the paper).
2. **Compositing.** Per polarisation, all dates of a window are merged
   into a pixel-wise **median** composite (a robust approximation of
   the local-resolution-weighted compositing in the paper). A single
   date per window is used directly.
3. **Differencing.** dVV = VV_post − VV_pre, dVH = VH_post − VH_pre
   (dB). Windthrown forest scatters *more* (chaotic trunks/branches,
   reduced canopy attenuation), so the index
   **WI = dVV + dVH is positive** over damage.
4. **Threshold.** Adaptive mode (paper default): flag pixels with
   WI > mean(WI) + a, with a ≈ **2.9 dB** (paper optimum; tested range
   2.8–3.35). The mean can be restricted to a forest mask. Fixed mode:
   one absolute WI threshold.
5. **Object filter.** 8-connected objects smaller than **n pixels**
   (paper optimum n = 27 px ≈ 0.27 ha at 10 m) are removed.
6. **Vectorisation.** The mask is polygonised (8-connected) and written
   with per-object area in hectares.

Reported performance of the original method (Swiss training area /
German validation area): PA 0.85–0.88, UA 0.65–0.81 for windthrow
areas ≥ 0.5 ha.

## Requirements

- QGIS **≥ 3.28** (PyQt bundled; no extra Qt install)
- No `pip install` required — STAC access uses the Python standard
  library (`urllib.request` + `json`).
- `numpy`, `scipy`, GDAL (`osgeo`) — all shipped with QGIS 3.16+.

The plugin does **not** depend on pystac-client, planetary-computer,
SNAP, snappy, torch or tensorflow.

## Installation

1. **Plugins → Manage and Install Plugins → Install from ZIP**.
2. Select `sentinel1_windthrow_plugin_v0.7.0.zip`.
3. The toolbar button and the **Plugins → Sentinel-1 Windthrow** menu
   entry appear.

## Recommended workflow

### 1. Search & Download

- Set the AOI around the storm-affected area (draw on map, canvas
  extent, or layer extent).
- **Product:** *RTC* (radiometrically terrain corrected γ⁰) is the
  best match for the paper's processing and is strongly recommended in
  terrain with relief; *GRD* (σ⁰) is acceptable in flat boreal
  lowlands. Never mix GRD and RTC within one run.
- **Orbit direction:** pick one (Ascending *or* Descending) and use
  only it for both windows. Check which pass has more acquisitions
  near your event.
- Search the **pre-storm window** (e.g. storm − 3 weeks … storm − 1
  day), download 1–5 scenes per orbit direction, then repeat for the
  **post-storm window** (storm + 1 day … + 2–3 weeks).

### 2. Preprocess (recommended but optional)

Run *Convert to dB* + *Lee speckle filter* (kernel 3–5) on the
downloaded measurement files. The Windthrow tab also accepts raw
downloads — it converts to dB automatically — but pre-filtering gives
cleaner results.

### 3. Windthrow Detection

- Add the pre-storm files (VV + VH of each date) to the *Pre-storm*
  list and the post-storm files to the *Post-storm* list. Files are
  auto-paired by the polarisation token in the file name; several
  dates of one polarisation are median-composited.
- Optionally restrict the analysis to a forest mask (raster or vector,
  e.g. a forest-stand layer) — this is what removes most false alarms
  over agricultural land, exactly like the forest mask in the paper.
- Keep **Background normalization** on (default, v0.8) when rain,
  snowmelt or strong soil-moisture change may have occurred between
  acquisitions: the weather-driven dB shift is measured per
  polarisation as `median(post − pre)` inside the analysis mask and
  removed from the post image before differencing. The WI output is
  then `<base>_wi_norm.tif`.
- Choose parameters (defaults follow the paper: adaptive a = 2.9 dB,
  median 3×3, min 27 px) and run. Results are loaded to the map and
  the polygon layer carries `area_ha` per object.

### 4. Field triage

Sort the polygon layer by `area_ha`, cross-check the largest objects
on the WI raster, and export the GeoPackage for field inspection.

## Architecture

```
sentinel1_windthrow_plugin/
├── __init__.py                  # classFactory entry point
├── sentinel1_plugin.py          # main plugin class (toolbar/menu)
├── sentinel1_plugin_dialog.py   # 4-tab QDialog + QgsTask subclasses
├── logger.py                    # QgsMessageLog wrapper (QGIS-optional)
├── metadata.txt / icon.svg / LICENSE
├── README.md / METHOD.md / TESTING_PLAN.md / RELEASE_NOTES_v0.7.0.md / RELEASE_NOTES_v0.8.0.md
├── ui/
│   └── draw_rectangle_tool.py   # "Draw on map" AOI tool
├── sources/
│   ├── base.py                  # BaseSARSource, Scene, OperationCancelled
│   ├── pc_client.py             # stdlib-only PC STAC client (retry/backoff)
│   ├── planetary_computer.py    # search + resumable download
│   ├── preprocessor.py          # SARPreprocessor (dB, Lee, land mask)
│   └── windthrow.py             # WindthrowDetector + compositing helpers
└── tests/                       # pytest suite (runs without QGIS)
```

All long operations run in background `QgsTask` threads with progress
bars and Cancel buttons; downloads resume via HTTP `Range` requests;
network calls retry 429/5xx with exponential backoff.

## Memory notes

WI / threshold / mask generation is streamed in 512-row bands, so a
full-size IW scene never sits in RAM. The connected-component cleanup
(one uint8 mask + one int32 label array, ≈ 2 GB peak on a full
250 × 170 km scene) is the only RAM-heavy step; on memory-limited
machines, clip the AOI first or raise `min_pixels`.

## Limitations (v0.7.0)

- Sentinel-1 GRD products are not radiometrically calibrated in the
  plugin (raw DN → dB); an inter-sensor (S1A/S1B) offset of ~0.1–0.3 dB
  may remain. Prefer the PC **RTC** collection for quantitative work.
- Compositing uses the pixel-wise median, not the paper's
  local-resolution-weighted merging (LRW needs the local illuminated
  area product).
- Only Planetary Computer is wired in; ASF / Copernicus Data Space are
  not (yet).
- No automatic decorrelation / co-registration: same-track S1 GRD and
  PC RTC products are assumed pixel-aligned (they normally are, thanks
  to S1's orbit control); any input on a different grid is warped
  bilinearly onto the reference grid.
- Confusion sources to check before field work: agricultural
  harvest/ploughing, flooded grassland, wind-roughened lakes, snowfall
  between dates, salvage logging inside the post window.

## Testing

```bash
python -m pytest tests -v          # from the parent of the plugin folder
```

Array-level tests run with numpy + scipy only; the file-level pipeline
tests run wherever GDAL bindings are available and skip otherwise.
See `TESTING_PLAN.md` for the real-data validation protocol
(European Russia / Urals case studies from the Shikhov et al. 2020
windthrow database).

## License

Released into the public domain under [CC0 1.0](LICENSE).
