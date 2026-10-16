"""Coherence difference-in-differences (DiD) windthrow detection.

Port of the research pipeline step12b (03.09.2026) into the plugin.
Interferometric coherence (HyP3 INSAR-GAMMA, 80 m, 20x4 looks) drops
where the canopy was disturbed between the two acquisitions of a
pair.  A single pair, however, is confounded by ANY change between
the passes (harvest, static debris anomalies, the storm-wide
decorrelation tail of the event itself — the July ID666 case) and by
weather-driven seasonal drift.

The difference-in-differences removes the background level using a
CONTROL pair of the same frames taken outside the damage window:

    dcoh = coh_control - coh_prepost

which is POSITIVE over windthrow (coherence fell between the
pre/post passes but stayed high in the control pair).  Validated on
the 2017 European Russia events:

    * ID694 (tornado, 161 ha): DiD AUC 0.908, excess median +0.308,
      TPR@FPR5% 0.55 — the strongest C-band result of the project,
      on par with L-band;
    * ID666 (squall line, 950 ha): DiD AUC 0.671 — the July control
      pair is itself contaminated by storm-wide decorrelation; a
      pre-pre control is planned (see the report, ch. 10).

Practical notes baked into the implementation:

* products may be passed as unpacked directories, ``*_corr.tif``
  paths or original HyP3 ``.zip`` archives (unpacked on the fly);
* the optional HyP3 water mask layer of a product is applied only
  when it is SANE: products occasionally ship a corrupt water mask
  (our product 5748 marked 99.6 % of the frame as water), so masks
  claiming more than ``max_water_frac`` of the frame are ignored
  with a warning;
* coherence is in [0, 1], so the adaptive offset ``a_coh`` defaults
  to 0.10 (not dB); the default minimum object size is 6 pixels,
  because one 80 m pixel covers 0.64 ha (27 pixels at 10 m and
  27 pixels at 80 m are 27x apart in area).

v1.2 additions:

* **Burst InSAR products** (HyP3 ``INSAR_ISCE_BURST`` /
  ``INSAR_ISCE_MULTI_BURST``, GAMMA-processed by ASF from SLC burst
  pairs) are accepted next to the classic full-frame INSAR-GAMMA
  products.  Their granule name is parsed
  (``S1_<track>_<burst IDs>_IW_<ref>_<sec>_<pol>_INT<spacing>_<id>``)
  and the DiD is validated: control and pre/post must be the SAME
  burst footprint (same relative burst IDs, track and polarization),
  otherwise the coherence rasters cover different footprints and the
  DiD degrades to no-overlap noise.  Mixing a GAMMA frame with a
  burst product in one DiD is rejected outright.
* Burst InSAR pixel spacing follows the look selection: 20 m
  (5x1), 40 m (10x2 — the «бёрст-пары 40 м» option of the report,
  1 credit per 1–4 pairs) or 80 m (20x4).  At 40 m one pixel covers
  0.16 ha, so the 6-px default min object size (~3.8 ha at 80 m)
  shrinks to ~0.96 ha — the detector logs a hint suggesting a larger
  ``min_pixels`` (≈ 20–25 px) to keep the validated object scale.
* **Water-mask convention fixed (v1.2)**: per the ASF product guides
  (both GAMMA and ISCE), water masks encode **1 = land, 0 = water**.
  v1.0/v1.1 kept pixels where the mask was > 0 — i.e. WATER — so on
  real products land windthrow was clipped away while water bodies
  stayed detectable, and sane land-dominant masks were rejected as
  "corrupt" (the > 0 fraction was read as water).  The mask is now
  converted to a keep-land layer (255 = land) before intersecting;
  the sanity heuristic counts zeros (water) instead.  Legacy masks
  with the opposite encoding can still be consumed via
  ``sane_water_mask(..., water_value=1)``.

v1.2.2 additions:

* **Forest safeguard «вне_леса@Y»** (the article's ID654 lesson): a
  zone that was non-forest before the storm cannot produce a DiD
  windthrow signal.  Two checks are computed from data the run
  already loads and reported in ``result["forest_safeguard"]``:
  the mean pre/post coherence inside the zone (analysis mask, else
  the background forest mask, else the whole scene) — above 0.6 it
  is the bare-ground signature (ID654: 0.635, AUC 0.051) — and the
  forest share of the scene (background clean-forest@Y mask when
  available, else the analysis mask) — below 0.3 the zone is outside
  the applicability limit.  The run is NOT aborted: the flag is
  logged as a warning and shown in the GUI result box, so the user
  can judge the mask semantics (a custom AOI mask is not a forest
  map — see METHOD.md §7b).
"""

import os
from typing import List, Optional, Sequence

import numpy as np

try:
    from osgeo import gdal  # always present in the QGIS Python env
    gdal.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None  # type: ignore

try:
    import scipy.ndimage
except Exception:  # pragma: no cover
    scipy = None  # type: ignore

from .base import OperationCancelled
from .constants import (
    _CHUNK_ROWS,
    COH_MAX,
    COH_MIN,
    COH_NONFOREST_MAX,
    DEFAULT_MAX_WATER_FRAC,
    DCOH_NODATA,
    FOREST_SHARE_MIN,
    LAND_VALUE,
    WATER_VALUE,
)
from .windthrow import (
    CancelCallback,
    ProgressCallback,
)
from .numpy_ops import (
    adaptive_threshold,
    filter_small_objects,
    mask_from_threshold,
    median_filter_nan,
)
from .raster_utils import (
    _create_output_vector,
    _intersect_masks,
    _pixel_area_m2,
    _read_raster_info,
    _resolve_mask_raster,
    _same_grid_safe,
    ensure_aligned,
)

# HyP3 product naming/discovery/water-mask logic moved to hyp3_utils.py
# (v1.3.0 refactor) and re-exported here for backward compatibility:
# tests import parse_hyp3_product / _validate_did_pair / ... from this
# module.
from .hyp3_utils import (  # noqa: E402
    _CORR_SUFFIX,
    _GAMMA_RE,
    _ISCE_RE,
    _WATER_SUFFIXES,
    _granule_names,
    _validate_did_pair,
    _water_mask_to_land_keep,
    find_correlation_tif,
    find_water_mask,
    parse_hyp3_product,
    sane_water_mask,
)
from ..logger import log_warning

# ======================================================================
# Pure-numpy helper (unit-testable without GDAL)
# ======================================================================
def coherence_delta_chunk(
    coh_prepost: np.ndarray, coh_control: np.ndarray
) -> np.ndarray:
    """Per-pixel DiD: ``control - prepost`` with NaN propagation.

    Pixels that are NaN in EITHER input are NaN in the output (IEEE
    semantics), so the valid-pixel handling of the caller stays
    trivial.  Windthrow (coherence drop in the pre/post pair) yields
    POSITIVE values.
    """
    with np.errstate(invalid="ignore"):
        return (np.asarray(coh_control, dtype=np.float64)
                - np.asarray(coh_prepost, dtype=np.float64))


# ======================================================================
# Main detector
# ======================================================================
class CoherenceDeltaDetector:
    """DiD coherence detector over two HyP3 InSAR products.

    Since v1.2 both product families are accepted: the classic
    full-frame GAMMA ``INSAR_GAMMA`` (80 m) and the burst-based ISCE
    ``INSAR_ISCE_BURST`` / ``INSAR_ISCE_MULTI_BURST`` (20/40/80 m —
    40 m = 10x2 looks, the report's «бёрст-пары 40 м» option).  The
    DiD pair is validated up front (same burst footprint for burst
    products, no family mixing — see :func:`_validate_did_pair`).

    Parameters
    ----------
    threshold_mode:
        ``"adaptive"`` (default) — threshold = median(dcoh) + ``a_coh``
        over the analysis mask (or the whole scene); ``"fixed"`` —
        absolute dcoh threshold.  The MEDIAN is used instead of the
        mean because HyP3 scenes with strong seasonal drift (autumn
        freeze-up) shift the whole dcoh background upward — the ID694
        validation scene shows a +0.33 background offset with a
        +0.31 event excess on top; a mean-based threshold would flag
        a third of the frame, the median-based one stays at the
        background level.
    a_coh:
        Adaptive offset in coherence units above the background median
        (default 0.25).  On the validated events this corresponds to a
        false-alarm rate of ~8 % (ID694) / ~14 % (ID666, confounded by
        storm-wide decorrelation); 0.10 flags ~30 % of the frame and
        is only useful for clean summer pairs.
    fixed_threshold:
        Absolute dcoh threshold for the fixed mode.
    min_pixels:
        Minimum object size in pixels (8-connected).  Default 6 px
        ~ 3.8 ha at the native 80 m pixel of INSAR-GAMMA 80 m.  At the
        40 m posting of 10x2-look Burst InSAR products one pixel is
        0.16 ha — scale ``min_pixels`` up (≈ 20–25 px) to keep the
        validated object size, or accept finer, more fragmented
        objects.
    median_filter_size:
        Optional median filter on dcoh before thresholding.
    """

    def __init__(
        self,
        threshold_mode: str = "adaptive",
        a_coh: float = 0.25,
        fixed_threshold: float = 0.25,
        min_pixels: int = 6,
        median_filter_size: int = 3,
    ) -> None:
        if threshold_mode not in ("adaptive", "fixed"):
            raise ValueError("threshold_mode must be 'adaptive' or 'fixed'")
        self.threshold_mode = threshold_mode
        self.a_coh = float(a_coh)
        self.fixed_threshold = float(fixed_threshold)
        self.min_pixels = int(min_pixels)
        self.median_filter_size = int(median_filter_size)
        # Diagnostics filled in by detect_file():
        self.mean_dcoh: Optional[float] = None
        self.median_dcoh: Optional[float] = None
        self.threshold_used: Optional[float] = None
        self.n_objects: int = 0
        self.water_mask_ignored: List[str] = []
        # v1.2.2: forest safeguard «вне_леса@Y» (None until a run).
        self.forest_safeguard: Optional[dict] = None

    # ------------------------------------------------------------------
    def detect_file(
        self,
        prepost_products: Sequence[str],
        control_products: Sequence[str],
        output_base: str,
        analysis_mask_path: Optional[str] = None,
        background_mask_path: Optional[str] = None,
        progress_cb: ProgressCallback = None,
        cancel_cb: CancelCallback = None,
    ) -> dict:
        """Run the DiD chain and write raster + vector outputs.

        :param prepost_products: HyP3 product(s) of the damage-window
            pair (directory, ``*_corr.tif`` or ``.zip``).
        :param control_products: product(s) of the control pair.  When
            empty, the score degrades to ``1 - coh_prepost`` (static
            decorrelation) and a warning is logged — the DiD needs the
            control pair to be robust.
        :param output_base: output path base; produces
            ``<base>_dcoh.tif``, ``<base>_mask.tif`` and ``<base>.gpkg``.
        :param analysis_mask_path: optional raster/vector restriction
            of the detection area (also used for the adaptive mean).
        :param background_mask_path: v1.1 — optional mask restricting
            ONLY the background sample of the adaptive statistics
            (median/mean and their threshold); detections are NOT
            restricted by it.  This is the GFW-rescore semantics
            (report ed.8 §9): pass the GFC clean-forest@Y mask here
            (``build_forest_mask(..., "gfc", gfc_variant="background")``)
            and the GFC forest-candidates@Y mask as the analysis mask —
            «пиксели с потерей в год события из фона исключаются, но
            из маски-кандидата не убираются».
        :return: dict with ``dcoh``, ``mask``, ``vector``,
            ``threshold``, ``mean_dcoh``, ``n_objects``,
            ``control_used``, ``water_mask_ignored``,
            ``background_mask``; v1.2 adds ``pixel_size_m``,
            ``min_object_area_ha``, ``product_flavors`` and
            ``product_info`` (parsed granule metadata); v1.2.2 adds
            ``forest_safeguard`` (dict with ``flagged``, ``flags``,
            ``reasons``, ``zone``, ``coh_prepost_mean`` and
            ``forest_share`` — the «вне_леса@Y» checks of the
            article's ID654 lesson).
        """
        if gdal is None:
            raise RuntimeError("GDAL (osgeo) is required for coherence detection")
        report = (lambda f, m: progress_cb(f, m) if progress_cb else None)
        cancelled = (lambda: bool(cancel_cb()) if cancel_cb else False)
        use_did = bool(control_products)

        tmp_dir = os.path.join(
            os.path.dirname(os.path.abspath(output_base)) or ".",
            "_coh_tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        base_name = os.path.basename(output_base)
        if base_name.lower().endswith((".tif", ".tiff", ".gpkg", ".shp")):
            base_name = os.path.splitext(base_name)[0]
            output_base = os.path.join(
                os.path.dirname(os.path.abspath(output_base)), base_name)

        # ---- 1. Resolve product layers ---------------------------------
        report(2.0, "Resolving HyP3 products")
        prepost_tif = find_correlation_tif(prepost_products[0], tmp_dir)
        control_tif = (find_correlation_tif(control_products[0], tmp_dir)
                       if use_did else None)
        if not use_did:
            log_warning(
                "No control product supplied — scoring 1 - coherence of "
                "the pre/post pair only; results are NOT robust against "
                "static and seasonal decorrelation (use the DiD).")

        # ---- 1b. Product flavour + DiD pairing validation (v1.2) -------
        prepost_info = parse_hyp3_product(prepost_products[0])
        control_info = (parse_hyp3_product(control_products[0])
                        if use_did else {"family": None})
        if use_did and prepost_info["family"] and control_info["family"]:
            _validate_did_pair(prepost_info, control_info)

        # ---- 2. Grid: prepost defines it, control is warped on it ------
        ref_info = _read_raster_info(prepost_tif)
        width, height = ref_info["width"], ref_info["height"]
        if control_tif and not _same_grid_safe(control_tif, ref_info):
            report(6.0, "Warping control pair onto the pre/post grid")
            control_tif = ensure_aligned(control_tif, ref_info, tmp_dir)

        # ---- 2b. Posting guidance (v1.2: burst pairs at 40 m) ----------
        px_size_m = abs(float(ref_info["geotransform"][1]))
        px_area_ha = (px_size_m * abs(float(ref_info["geotransform"][5]))
                      / 10000.0)
        if (px_size_m < 45.0
                and self.min_pixels * px_area_ha < 2.0):
            log_warning(
                f"Pixel size is {px_size_m:.0f} m (Burst InSAR {int(px_size_m)} m "
                f"class) while min_pixels={self.min_pixels} keeps objects of "
                f"≈ {self.min_pixels * px_area_ha:.2f} ha. The validated "
                "object scale is ≈ 3.8 ha (6 px at 80 m) — consider "
                "min_pixels ≈ 20–25 px at 40 m.")

        # ---- 3. Masks ----------------------------------------------------
        mask_raster: Optional[str] = None
        if analysis_mask_path:
            if not os.path.isfile(analysis_mask_path):
                raise ValueError(
                    f"Mask file not found: {analysis_mask_path}")
            mask_raster = _resolve_mask_raster(
                analysis_mask_path, ref_info, tmp_dir)

        # v1.1: statistics-only background mask (GFW rescore semantics —
        # clean forest@Y for the adaptive-threshold sample).
        bg_mask_raster: Optional[str] = None
        if background_mask_path:
            if not os.path.isfile(background_mask_path):
                raise ValueError(
                    f"Background mask file not found: "
                    f"{background_mask_path}")
            bg_mask_raster = _resolve_mask_raster(
                background_mask_path, ref_info, tmp_dir)

        self.water_mask_ignored = []
        for product in (prepost_products[0], control_products[0] if use_did else None):
            if not product:
                continue
            wm = find_water_mask(product, tmp_dir)
            if not wm:
                continue
            sane = sane_water_mask(wm)
            if sane is None:
                self.water_mask_ignored.append(os.path.abspath(wm))
                continue
            # v1.2: HyP3 masks are 1 = land / 0 = water (ASF product
            # guides) — convert to a keep-land layer instead of the old
            # (inverted) _resolve_mask_raster pass-through.
            wm_resolved = _water_mask_to_land_keep(sane, ref_info, tmp_dir)
            if mask_raster is not None:
                mask_raster = _intersect_masks(
                    mask_raster, wm_resolved, ref_info, tmp_dir)
            else:
                mask_raster = wm_resolved

        # ---- 4. dcoh raster + running statistics -----------------------
        dcoh_path = f"{output_base}_dcoh.tif"
        if os.path.exists(dcoh_path):
            try:
                gdal.GetDriverByName("GTiff").Delete(dcoh_path)
            except Exception:
                pass
        driver = gdal.GetDriverByName("GTiff")
        dcoh_ds = driver.Create(
            dcoh_path, width, height, 1, gdal.GDT_Float32,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER",
                     "PREDICTOR=3"],
        )
        if dcoh_ds is None:
            raise RuntimeError(f"Cannot create output file: {dcoh_path}")
        dcoh_ds.SetGeoTransform(ref_info["geotransform"])
        if ref_info["projection"]:
            dcoh_ds.SetProjection(ref_info["projection"])
        dcoh_band = dcoh_ds.GetRasterBand(1)
        dcoh_band.SetNoDataValue(DCOH_NODATA)

        pre_ds = gdal.Open(prepost_tif, gdal.GA_ReadOnly)
        if pre_ds is None:
            raise RuntimeError(f"Cannot open coherence layer: {prepost_tif}")
        ctl_ds = gdal.Open(control_tif, gdal.GA_ReadOnly) if control_tif else None
        pre_band = pre_ds.GetRasterBand(1)
        pre_nd = pre_band.GetNoDataValue()
        ctl_band = None
        ctl_nd = None
        if ctl_ds is not None:
            ctl_band = ctl_ds.GetRasterBand(1)
            ctl_nd = ctl_band.GetNoDataValue()

        mask_ds = None
        mask_band = None
        if mask_raster:
            mask_ds = gdal.Open(mask_raster, gdal.GA_ReadOnly)
            mask_band = mask_ds.GetRasterBand(1)
        bg_ds = None
        bg_band = None
        if bg_mask_raster:
            bg_ds = gdal.Open(bg_mask_raster, gdal.GA_ReadOnly)
            bg_band = bg_ds.GetRasterBand(1)

        running_sum = 0.0
        running_count = 0
        median_sample: List[np.ndarray] = []
        # v1.2.2 forest safeguard accumulators («вне_леса@Y»): mean
        # pre/post coherence inside the zone + forest share of the
        # scene (see module docstring and METHOD.md §7b).
        pre_zone_sum = 0.0
        pre_zone_count = 0
        forest_px = 0
        forest_total = 0

        def _sanitize(coh: np.ndarray, ndv) -> np.ndarray:
            """Registered no-data and non-physical values -> NaN."""
            coh = coh.astype(np.float64, copy=True)
            if ndv is not None:
                coh[coh == ndv] = np.nan
            coh[(coh < COH_MIN) | (coh > COH_MAX)] = np.nan
            return coh

        try:
            for y0 in range(0, height, _CHUNK_ROWS):
                if cancelled():
                    raise OperationCancelled()
                rows = min(_CHUNK_ROWS, height - y0)
                coh_pp = _sanitize(
                    pre_band.ReadAsArray(0, y0, width, rows), pre_nd)
                if ctl_band is not None:
                    coh_ct = _sanitize(
                        ctl_band.ReadAsArray(0, y0, width, rows), ctl_nd)
                    chunk = coherence_delta_chunk(coh_pp, coh_ct)
                else:
                    chunk = 1.0 - coh_pp  # static decorrelation fallback
                chunk = chunk.astype(np.float32, copy=False)
                chunk[np.isnan(chunk)] = DCOH_NODATA
                dcoh_band.WriteArray(chunk, 0, y0)

                finite = chunk != DCOH_NODATA
                inside = None
                if mask_band is not None:
                    inside = mask_band.ReadAsArray(
                        0, y0, width, rows) > 0
                    finite &= inside
                bg_inside = None
                if bg_band is not None:
                    bg_inside = bg_band.ReadAsArray(
                        0, y0, width, rows) > 0
                    finite &= bg_inside

                # v1.2.2 — safeguard zone («внутри полигона» of the
                # article): the analysis mask first, else the
                # background forest mask, else the whole scene.
                zone = inside if inside is not None else bg_inside
                pre_ok = np.isfinite(coh_pp)
                if zone is not None:
                    pre_ok &= zone
                pre_vals = coh_pp[pre_ok]
                if pre_vals.size:
                    pre_zone_sum += float(pre_vals.sum())
                    pre_zone_count += int(pre_vals.size)
                # v1.2.2 — forest share: the background (clean
                # forest@Y) mask when available, else the analysis
                # mask itself.
                share_zone = bg_inside if bg_inside is not None else inside
                if share_zone is not None:
                    forest_px += int(np.count_nonzero(share_zone))
                    forest_total += int(share_zone.size)

                vals = chunk[finite].astype(np.float64, copy=False)
                if vals.size:
                    running_sum += float(vals.sum())
                    running_count += int(vals.size)
                    # Robust centre: subsampled values for the median
                    # (chunked running median is not needed — a 4M
                    # sample approximates it to ~1e-4).
                    if vals.size > 4096:
                        stride = int(np.ceil(vals.size / 4096.0))
                        vals = vals[::stride]
                    median_sample.append(vals)
                report(10.0 + 40.0 * (y0 + rows) / float(height),
                       "Coherence DiD")
        finally:
            dcoh_ds = None
            pre_ds = None
            pre_band = None
            ctl_ds = None
            ctl_band = None
            mask_ds = None
            mask_band = None
            bg_ds = None
            bg_band = None

        if running_count == 0:
            raise RuntimeError(
                "No valid pixels — check that both products cover the "
                "same frames (or supply an analysis mask with valid "
                "coherence).")
        mean_dcoh = running_sum / running_count
        self.mean_dcoh = mean_dcoh
        if median_sample:
            self.median_dcoh = float(np.median(np.concatenate(median_sample)))
        else:
            self.median_dcoh = mean_dcoh
        threshold = (adaptive_threshold(self.median_dcoh, self.a_coh)
                     if self.threshold_mode == "adaptive"
                     else self.fixed_threshold)
        self.threshold_used = threshold

        # ---- 4b. Forest safeguard «вне_леса@Y» (v1.2.2) ---------------
        # The article's ID654 lesson: a zone that was non-forest
        # before the storm (bare ground after an in-year clearcut:
        # pre/post coherence 0.635, AUC 0.051 with excess inversion)
        # cannot produce a DiD windthrow signal.  Flag it instead of
        # silently returning an empty / inverted detection.
        safeguard = {
            "flagged": False,
            "flags": [],
            "reasons": [],
            "zone": ("analysis" if mask_raster
                     else "background" if bg_mask_raster else "scene"),
            "coh_prepost_mean": None,
            "forest_share": None,
        }
        if pre_zone_count:
            coh_mean = pre_zone_sum / pre_zone_count
            safeguard["coh_prepost_mean"] = round(coh_mean, 4)
            if coh_mean > COH_NONFOREST_MAX:
                safeguard["flagged"] = True
                safeguard["flags"].append("вне_леса@Y")
                safeguard["reasons"].append(
                    f"mean pre/post coherence in the {safeguard['zone']} "
                    f"zone is {coh_mean:.3f} > {COH_NONFOREST_MAX} — "
                    "bare-ground / non-forest signature before the "
                    "storm (ID654 case)")
        if forest_total:
            share = forest_px / float(forest_total)
            safeguard["forest_share"] = round(share, 4)
            if share < FOREST_SHARE_MIN:
                safeguard["flagged"] = True
                if "вне_леса@Y" not in safeguard["flags"]:
                    safeguard["flags"].append("вне_леса@Y")
                safeguard["reasons"].append(
                    f"mask covers {share:.2f} of the scene < "
                    f"{FOREST_SHARE_MIN} — forest share below the "
                    "applicability limit (вне_леса@Y)")
        if safeguard["flagged"]:
            log_warning("Forest safeguard вне_леса@Y: "
                        + "; ".join(safeguard["reasons"]))
        self.forest_safeguard = safeguard

        # ---- 5. Threshold pass (median filter, halo) --------------------
        full_mask = np.zeros((height, width), dtype=bool)
        r = self.median_filter_size // 2 if self.median_filter_size >= 3 else 0
        filter_active = self.median_filter_size >= 3 and scipy is not None
        if self.median_filter_size >= 3 and scipy is None:
            log_warning("scipy unavailable — dcoh median filter skipped")
        dcoh_rd = gdal.Open(dcoh_path, gdal.GA_ReadOnly)
        dcoh_band_rd = dcoh_rd.GetRasterBand(1)
        mask_rd = gdal.Open(mask_raster, gdal.GA_ReadOnly) if mask_raster else None
        mask_band_rd = mask_rd.GetRasterBand(1) if mask_rd else None
        try:
            for y0 in range(0, height, _CHUNK_ROWS):
                if cancelled():
                    raise OperationCancelled()
                rows = min(_CHUNK_ROWS, height - y0)
                ry0, ry1 = max(0, y0 - r), min(height, y0 + rows + r)
                tile = dcoh_band_rd.ReadAsArray(0, ry0, width, ry1 - ry0)
                invalid = tile == DCOH_NODATA
                tile = tile.astype(np.float32, copy=False)
                tile[invalid] = np.nan
                if filter_active:
                    tile = median_filter_nan(tile, self.median_filter_size)
                centre = tile[y0 - ry0: y0 - ry0 + rows]
                flagged = mask_from_threshold(centre, threshold)
                flagged &= ~np.isnan(centre)
                if mask_band_rd is not None:
                    inside = mask_band_rd.ReadAsArray(
                        0, y0, width, rows) > 0
                    flagged &= inside
                full_mask[y0:y0 + rows] = flagged
                report(55.0 + 15.0 * (y0 + rows) / float(height),
                       "Thresholding")
        finally:
            dcoh_rd = None
            dcoh_band_rd = None
            mask_rd = None
            mask_band_rd = None

        # ---- 6. Object cleanup ------------------------------------------
        if self.min_pixels > 1:
            full_mask = filter_small_objects(full_mask, self.min_pixels)

        # ---- 7. Mask raster ----------------------------------------------
        mask_path = f"{output_base}_mask.tif"
        mask_out = driver.Create(
            mask_path, width, height, 1, gdal.GDT_Byte,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
        )
        if mask_out is None:
            raise RuntimeError(f"Cannot create output file: {mask_path}")
        try:
            mask_out.SetGeoTransform(ref_info["geotransform"])
            if ref_info["projection"]:
                mask_out.SetProjection(ref_info["projection"])
            mband = mask_out.GetRasterBand(1)
            mband.WriteArray(full_mask.astype(np.uint8) * 255)
            mband.SetNoDataValue(0)
            mband.FlushCache()
            try:
                mband.ComputeStatistics(False)
            except Exception:
                pass
        finally:
            mask_out = None

        # ---- 8. Polygonise + attribute + size filter ---------------------
        vector_path = (output_base
                       if output_base.lower().endswith((".gpkg", ".shp"))
                       else output_base + ".gpkg")
        vds, layer = _create_output_vector(vector_path, ref_info["projection"])
        px_area_m2 = _pixel_area_m2(
            ref_info["geotransform"], ref_info["projection"])
        try:
            mask_src = gdal.Open(mask_path, gdal.GA_ReadOnly)
            band = mask_src.GetRasterBand(1)
            gdal.Polygonize(band, band, layer, -1, ["8CONNECTED=8"], None)
            min_area_m2 = (self.min_pixels * px_area_m2
                           if self.min_pixels > 0 else 0.0)
            fid_area = []
            layer.ResetReading()
            for feat in layer:
                geom = feat.GetGeometryRef()
                area_m2 = geom.GetArea() if geom is not None else 0.0
                fid_area.append((feat.GetFID(), area_m2))
            n_kept = 0
            for fid, area_m2 in fid_area:
                if area_m2 < min_area_m2:
                    layer.DeleteFeature(fid)
                else:
                    f = layer.GetFeature(fid)
                    if f is not None:
                        f.SetField("area_ha", area_m2 / 10000.0)
                        layer.SetFeature(f)
                        f = None
                    n_kept += 1
            layer.ResetReading()
            mask_src = None
            self.n_objects = n_kept
        finally:
            vds = None

        # ---- 9. Temp cleanup ----------------------------------------------
        try:
            for name in os.listdir(tmp_dir):
                try:
                    os.remove(os.path.join(tmp_dir, name))
                except OSError:
                    pass
            os.rmdir(tmp_dir)
        except OSError:
            pass

        report(100.0, "Done")
        return {
            "dcoh": os.path.abspath(dcoh_path),
            "mask": os.path.abspath(mask_path),
            "vector": os.path.abspath(vector_path),
            "threshold": self.threshold_used,
            "mean_dcoh": self.mean_dcoh,
            "median_dcoh": self.median_dcoh,
            "n_objects": self.n_objects,
            "control_used": use_did,
            "water_mask_ignored": list(self.water_mask_ignored),
            "background_mask": (os.path.abspath(bg_mask_raster)
                                if bg_mask_raster else None),
            # v1.2: product flavour + posting diagnostics
            "pixel_size_m": round(px_size_m, 3),
            "min_object_area_ha": round(
                self.min_pixels * px_area_ha, 3),
            "product_flavors": {
                "prepost": prepost_info["family"],
                "control": (control_info["family"] if use_did else None),
            },
            "product_info": {
                "prepost": prepost_info,
                "control": (control_info if use_did else None),
            },
            # v1.2.2: forest safeguard «вне_леса@Y» (article ID654).
            "forest_safeguard": safeguard,
        }
