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
"""

import os
import tempfile
import zipfile
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
from .windthrow import (
    CancelCallback,
    ProgressCallback,
    _CHUNK_ROWS,
    _create_output_vector,
    _pixel_area_m2,
    _read_raster_info,
    _resolve_mask_raster,
    _intersect_masks,
    adaptive_threshold,
    ensure_aligned,
    filter_small_objects,
    mask_from_threshold,
    median_filter_nan,
)
from ..logger import log_warning

#: No-data value written to the float32 dcoh raster.
DCOH_NODATA = -9999.0

#: Filename suffix of the HyP3 coherence layer.
_CORR_SUFFIX = "_corr.tif"

#: Filename suffixes accepted as HyP3 water-mask layers.
_WATER_SUFFIXES = ("_wm.tif", "_water_mask.tif")

#: A water mask claiming more than this fraction of a frame is corrupt.
DEFAULT_MAX_WATER_FRAC = 0.5

#: Physically plausible coherence range; anything outside (e.g. the
#: +-9999 fill of a warped control product) is treated as no-data.
COH_MIN, COH_MAX = -0.01, 1.01


# ======================================================================
# Product discovery helpers
# ======================================================================
def find_correlation_tif(source: str, tmp_dir: Optional[str] = None) -> str:
    """Return the ``*_corr.tif`` path of one HyP3 product.

    :param source: unpacked product directory, a direct
        ``*_corr.tif`` path, or the original HyP3 ``.zip`` (the
        coherence layer is extracted to ``tmp_dir``).
    :raises FileNotFoundError: when no coherence layer is present.
    """
    source = os.path.abspath(source)
    if os.path.isfile(source):
        lower = source.lower()
        if lower.endswith(_CORR_SUFFIX):
            return source
        if lower.endswith(".zip"):
            if gdal is None:
                raise RuntimeError("GDAL is required to unpack HyP3 products")
            out_dir = os.path.join(
                tmp_dir or tempfile.mkdtemp(prefix="hyp3_zip_"), "_corr")
            os.makedirs(out_dir, exist_ok=True)
            with zipfile.ZipFile(source) as z:
                members = [n for n in z.namelist()
                           if n.lower().endswith(_CORR_SUFFIX)]
                if not members:
                    raise FileNotFoundError(
                        f"No *_corr.tif inside {source}")
                target = z.extract(members[0], out_dir)
                return os.path.abspath(target)
        raise ValueError(
            f"Expected a *_corr.tif, a product directory or a .zip: {source}")
    if os.path.isdir(source):
        hits: List[str] = []
        for root, _dirs, files in os.walk(source):
            for name in sorted(files):
                if name.lower().endswith(_CORR_SUFFIX):
                    hits.append(os.path.join(root, name))
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise FileNotFoundError(
                f"No *_corr.tif layer found under {source}")
        raise ValueError(
            f"Multiple coherence layers under {source}: pick one:\n  "
            + "\n  ".join(hits))
    raise FileNotFoundError(f"HyP3 product not found: {source}")


def find_water_mask(source: str, tmp_dir: Optional[str] = None) -> Optional[str]:
    """Return the water-mask layer path of one HyP3 product, else ``None``.

    Same lookup rules as :func:`find_correlation_tif` but the layer is
    optional: unpacked directories are searched recursively for
    ``*_wm.tif`` / ``*_water_mask.tif``; zips get the layer extracted.
    """
    source = os.path.abspath(source)
    if os.path.isfile(source):
        lower = source.lower()
        if lower.endswith(_WATER_SUFFIXES):
            return source
        if lower.endswith(".zip"):
            with zipfile.ZipFile(source) as z:
                members = [n for n in z.namelist()
                           if n.lower().endswith(_WATER_SUFFIXES)]
                if not members:
                    return None
                out_dir = os.path.join(
                    tmp_dir or tempfile.mkdtemp(prefix="hyp3_zip_"), "_wm")
                os.makedirs(out_dir, exist_ok=True)
                return os.path.abspath(z.extract(members[0], out_dir))
        return None
    if os.path.isdir(source):
        for root, _dirs, files in os.walk(source):
            for name in sorted(files):
                if name.lower().endswith(_WATER_SUFFIXES):
                    return os.path.join(root, name)
    return None


def sane_water_mask(
    water_mask_path: Optional[str],
    max_water_frac: float = DEFAULT_MAX_WATER_FRAC,
) -> Optional[str]:
    """Apply the corrupt-water-mask heuristic of step12b.

    Reads the fraction of >0 (water) pixels; when it exceeds
    ``max_water_frac`` the mask is considered CORRUPT (HyP3 product
    5748 claimed 99.6 % water) and ``None`` is returned so the caller
    skips it.  A sane mask is returned unchanged.

    :raises FileNotFoundError: when ``water_mask_path`` does not exist.
    """
    if not water_mask_path:
        return None
    if not os.path.isfile(water_mask_path):
        raise FileNotFoundError(f"Water mask not found: {water_mask_path}")
    if gdal is None:
        raise RuntimeError("GDAL is required to check water masks")
    ds = gdal.Open(water_mask_path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Cannot open water mask: {water_mask_path}")
    try:
        band = ds.GetRasterBand(1)
        width, height = ds.RasterXSize, ds.RasterYSize
        water = 0
        total = 0
        for y0 in range(0, height, _CHUNK_ROWS):
            rows = min(_CHUNK_ROWS, height - y0)
            chunk = band.ReadAsArray(0, y0, width, rows)
            total += int(chunk.size)
            water += int((chunk > 0).sum())
        frac = (water / total) if total else 0.0
    finally:
        ds = None
    if frac > max_water_frac:
        log_warning(
            f"Water mask {os.path.basename(water_mask_path)} claims "
            f"{frac:.1%} water (> {max_water_frac:.0%}) — corrupt, ignoring "
            "(step12b sane-mask heuristic).")
        return None
    return water_mask_path


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
    """DiD coherence detector over two HyP3 INSAR-GAMMA products.

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
        ~ 3.8 ha at the native 80 m pixel of INSAR-GAMMA 80 m.
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

    # ------------------------------------------------------------------
    def detect_file(
        self,
        prepost_products: Sequence[str],
        control_products: Sequence[str],
        output_base: str,
        analysis_mask_path: Optional[str] = None,
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
        :return: dict with ``dcoh``, ``mask``, ``vector``,
            ``threshold``, ``mean_dcoh``, ``n_objects``,
            ``control_used``, ``water_mask_ignored``.
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

        # ---- 2. Grid: prepost defines it, control is warped on it ------
        ref_info = _read_raster_info(prepost_tif)
        width, height = ref_info["width"], ref_info["height"]
        if control_tif and not _same_grid_safe(control_tif, ref_info):
            report(6.0, "Warping control pair onto the pre/post grid")
            control_tif = ensure_aligned(control_tif, ref_info, tmp_dir)

        # ---- 3. Masks ----------------------------------------------------
        mask_raster: Optional[str] = None
        if analysis_mask_path:
            if not os.path.isfile(analysis_mask_path):
                raise ValueError(
                    f"Mask file not found: {analysis_mask_path}")
            mask_raster = _resolve_mask_raster(
                analysis_mask_path, ref_info, tmp_dir)

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
            wm_resolved = _resolve_mask_raster(sane, ref_info, tmp_dir)
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

        running_sum = 0.0
        running_count = 0
        median_sample: List[np.ndarray] = []

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
                if mask_band is not None:
                    inside = mask_band.ReadAsArray(
                        0, y0, width, rows) > 0
                    finite &= inside
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
        }


def _same_grid_safe(path: str, ref_info: dict) -> bool:
    """Grid comparison that tolerates a missing/unsupported reference."""
    try:
        return _read_raster_info(path)["geotransform"] == ref_info["geotransform"] \
            and _read_raster_info(path)["width"] == ref_info["width"] \
            and _read_raster_info(path)["height"] == ref_info["height"]
    except Exception:
        return False
