# Method Note — Bi-temporal Windthrow Detection with Sentinel-1

This plugin implements (with two documented simplifications) the rapid
windthrow detection method of:

> Rüetschi, M.; Small, D.; Waser, L.T. **Rapid Detection of Windthrows
> Using Sentinel-1 C-Band SAR Data.** *Remote Sensing* 2019, 11(2), 115.
> https://doi.org/10.3390/rs11020115

## 1. Detection chain

```
S1 VV/VH files (pre window)          S1 VV/VH files (post window)
        │                                   │
        ▼                                   ▼
 align to reference grid             align to reference grid
 (bilinear warp if needed)           (bilinear warp if needed)
        │                                   │
        ▼                                   ▼
 median composite per pol            median composite per pol
        │                                   │
        └───────────────┬───────────────────┘
                        ▼
     dVV = VV_post − VV_pre ;  dVH = VH_post − VH_pre      [dB]
                        ▼
              WI = dVV + dVH                               [dB]
                        ▼
        threshold: WI > mean(WI) + a      (adaptive, paper)
                   WI > fixed value       (optional)
                        ▼
        median filter (3×3, optional) → 8-connected objects
                        ▼
        remove objects < n pixels (paper: n = 27)
                        ▼
        WI raster (float32) + mask (uint8) + polygons (area_ha)
```

## 2. Why WI is positive over windthrow

After a storm the forest canopy is replaced by a chaotic layer of
uprooted / broken trunks and branches. Rüetschi et al. measured mean
differences of +0.5 dB (VV) and +0.97 dB (VH) inside reference
windthrows versus ≈ 0 / +0.3 dB over intact forest: the increased
surface roughness and the reduced canopy attenuation raise backscatter
in **both** polarisations. Summing the two differences doubles the
separation of damaged from intact pixels.

## 3. Parameters

| Parameter | Default | Paper reference |
|-----------|---------|-----------------|
| `a` (adaptive offset) | 2.9 dB | optimum of the 108-combination grid search (2.8–3.35 tested) |
| `n` (min object) | 27 px ≈ 0.27 ha | optimum n = 27 (20–30 tested); reference areas ≥ 0.5 ha |
| windows | 1–5 pre / 1–3 post scenes | 5 pre + up to 10 post acquisitions (~2–4 weeks) |
| polarisations | VV + VH | IW GRDH dual-pol |
| forest mask | optional | detections computed inside a forest mask |

Reported accuracies of the original method: PA 0.85–0.88, UA
0.65–0.81 (areas ≥ 0.5 ha, 10 m grid).

## 3b. Forest mask (v0.9)

Detections and the adaptive-threshold mean can be restricted to forest.
Two sources are built in:

* **ESA WorldCover 10 m** (`esa-worldcover` on Planetary Computer,
  epochs 2020 / 2021): Tree-cover class (10) is warped onto the radar
  grid (nearest), cleaned with a 3×3 majority filter and written as
  `<base>_forest_wc<year>.tif`. The STAC search, SAS signing and
  windowed COG reading (`/vsicurl/`) reuse the plugin's PC client —
  no local land-cover download is required.
* **User file** — any raster (values > 0) or vector (polygons).

The forest mask is intersected with the analysis mask; when no separate
background mask is given, offsets and the mean WI are computed over the
forest sample (paper behaviour). Caveat: WorldCover epochs postdate many
storms — young regrowth inside old windthrows may be classified as
shrub/grass, so prefer the epoch closest to (but not after) the event,
or enable a closing operation in pipeline use.

Validation on event ID666 (squall 30.07.2017, ~950 ha; thresholds held
identical to the baseline run): PA unchanged (reference coverage
98–100 %), UA +13–15 % relative, false-alarm area −19…−40 % depending
on the variant. Remaining false positives are small forest-internal
disturbances — increase `n` or switch index for those.

### 3.1 Background normalization (v0.8, not in the paper)

`normalize_background=True` (default) adds a weather-shift guard:
per polarisation the offset

```
offset_pol = median(post_dB − pre_dB)   inside the background sample
```

is subtracted from the post image before differencing. The sample is
the `background_mask_path` raster/vector when given (recommended: a
forest sample EXCLUDING the windthrow itself, e.g. a 3 km buffer around
the event minus the damaged polygons), else the analysis mask, else
the whole scene. The adaptive-threshold mean WI uses the same sample.
This protects the detection against the classic failure mode where a
wet-soil/rain event between acquisitions raises the whole post image
by ~+1 dB and floods the map with false alarms; the per-polarisation
offsets are reported in the diagnostics (`offset_db`,
`*_wi_norm.tif`).

## 4. Documented simplifications

1. **Median compositing instead of LRW.** The paper merges
   acquisitions by local resolution weighting (weights ∝ 1 / local
   illuminated area). The plugin computes the pixel-wise median, which
   is robust against speckle and date-to-date variation but does not
   exploit the per-acquisition local resolution. In flat-to-moderate
   terrain the difference is small.
2. **Raw DN → dB instead of full radiometric calibration.** PC GRD
   assets are raw DN; the plugin converts with 10·log10. For
   change detection the (nearly constant) calibration factor cancels in
   the differencing; a small S1A/S1B cross-sensor offset
   (~0.1–0.3 dB) may remain. Use the PC **RTC** collection
   (calibrated γ⁰) when quantitative rigour matters — it also matches
   the γ⁰ domain of the paper.

## 5. Sign convention (important!)

Windthrow = **increase** → WI positive. If a run flags water bodies
after windy dates (wind-roughened surface) or agricultural fields
(harvest / ploughing), those are expected confusion sources — restrict
detection with a forest mask and/or raise `a` / `n`.

## 6. References

- Rüetschi, Small, Waser (2019), *Remote Sensing* 11(2):115.
- Small (2011), RTC γ⁰ methodology, *IEEE TGRS* 49(10):3799–3806.
- Shikhov, Chernokulsky, Azhigov, Semakina (2020), *Earth Syst. Sci.
  Data* 12:3489–3513 — windthrow event database for European Russia
  1986–2017 (validation source, see TESTING_PLAN.md).
- Shikhov, Abdullin, Semakina (2020), *Геодезия и картография*
  № 4, 19–30 — forest susceptibility mapping for the Ural region.
