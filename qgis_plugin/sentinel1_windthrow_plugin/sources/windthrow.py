"""Bi-temporal windthrow detection for Sentinel-1 SAR imagery.

Implements the rapid change-detection method of Rüetschi, Small & Waser
(2019, "Rapid Detection of Windthrows Using Sentinel-1 C-Band SAR Data",
Remote Sensing 11(2):115), adapted to a lightweight QGIS plugin:

1.  Two stacks of pre-processed SAR images in dB (or raw amplitude /
    DN, which are auto-converted) cover the pre-storm and post-storm
    windows.  Per polarisation, the stack is combined into a single
    composite by a pixel-wise median (a robust approximation of the
    local-resolution-weighted compositing used in the paper).
2.  Image differencing per polarisation:
        dVV = VV_post - VV_pre     dVH = VH_post - VH_pre      [dB]
    Windthrown areas show a BACKSCATTER INCREASE (chaotically oriented
    trunks and branches raise surface roughness and reduce canopy
    attenuation), so the Windthrow Index
        WI = dVV + dVH                                             [dB]
    is positive over windthrow.
3.  A pixel is flagged when WI exceeds a threshold.  Two modes:
        * adaptive (paper default): threshold = mean(WI) + a, with the
          mean taken over the analysis mask (forest) or the whole
          scene; ``a`` is a dB offset, optimum ~2.9 dB in the paper;
        * fixed: a user-defined absolute WI threshold in dB.
4.  Connected-component cleanup removes flagged objects smaller than
    ``min_pixels`` (paper optimum n = 27 pixels at 10 m ~ 0.27 ha).
5.  The cleaned mask is vectorised (gdal.Polygonize, 8-connected) into
    a GeoPackage / Shapefile with an ``area_ha`` attribute per object.

v0.8 addition — background normalization (``normalize_background=True``):
weather-driven changes between acquisitions (wet soil after rain,
snowmelt, vegetation growth) shift the whole post-storm image by a
common dB offset and flood the detection with false alarms.  Before
differencing, per polarisation the median difference
``median(post - pre)`` inside the background mask (forest sample; when
absent — the analysis mask or the whole scene) is subtracted from the
post-storm image, centring the background WI on ~0 dB.  The WI raster
is then written as ``<base>_wi_norm.tif``.

All heavy work is chunked row-band processing through GDAL so that
full-size Sentinel-1 IW scenes never need to be fully resident in RAM
(except one uint8 mask + one int32 label array during cleanup; see the
README "Memory" note).  Pure-numpy helpers are kept free of GDAL
imports so they can be unit-tested without a GDAL runtime.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from osgeo import gdal, ogr, osr  # always present in the QGIS Python env
    gdal.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None  # type: ignore
    ogr = None  # type: ignore
    osr = None  # type: ignore

try:
    import scipy.ndimage
except Exception:  # pragma: no cover
    scipy = None  # type: ignore

from .base import OperationCancelled
from .constants import (  # single source of truth (v1.3.0 refactor)
    _CHUNK_ROWS,
    _DB_DETECT_FRACTION,
    _DB_NODATA_FLOOR,
    _MEDIAN_SENTINEL,
    WI_NODATA,
)

# Pure-numpy helpers moved to numpy_ops.py (v1.3.0 refactor) and
# re-exported here for backward compatibility: tests and downstream
# code import them from this module.
from .numpy_ops import (  # noqa: E402
    adaptive_threshold,
    background_offset_db,
    common_polarizations,
    compute_wi,
    extract_polarization,
    filter_small_objects,
    mask_from_threshold,
    median_filter_nan,
    pair_by_polarization,
    to_db_domain_chunk,
)
from .types import DetectionResult

# Shared postprocess stages (v1.3.0 refactor, spec §4):
from .pipeline import (
    polygonize_mask,
    remove_small_objects,
    threshold_pass,
    write_mask_raster,
)

# GDAL-level helpers moved to raster_utils.py (v1.3.0 refactor) and
# re-exported here for backward compatibility (coh_delta and forest_mask
# import them from this module; tests exercise them via the detector).
from .raster_utils import (  # noqa: E402
    _create_output_vector,
    _ensure_gdal,
    _intersect_masks,
    _pixel_area_m2,
    _read_raster_info,
    _resolve_mask_raster,
    _same_grid,
    ensure_aligned,
    rasterize_vector_mask,
)

ProgressCallback = Optional[Callable[[float, str], None]]
CancelCallback = Optional[Callable[[], bool]]


# ======================================================================
# GDAL-level helpers
# ======================================================================
def build_median_composite(
    files: Sequence[str],
    ref_info: Dict,
    tmp_dir: str,
    output_path: str,
    progress_cb: ProgressCallback = None,
    cancel_cb: CancelCallback = None,
) -> str:
    """Pixel-wise median composite of aligned dB rasters (chunked).

    ``files`` must contain at least two rasters (for a single raster the
    caller uses it directly).  Every input is aligned to ``ref_info``
    first, converted to dB per chunk, and combined with ``nanmedian``.
    Returns the absolute path of the written float32 GeoTIFF.
    """
    _ensure_gdal()
    if len(files) < 2:
        raise ValueError("build_median_composite needs >= 2 files")
    aligned = [ensure_aligned(f, ref_info, tmp_dir) for f in files]

    datasets = []
    try:
        for p in aligned:
            ds = gdal.Open(p, gdal.GA_ReadOnly)
            if ds is None:
                raise RuntimeError(f"Cannot open raster: {p}")
            datasets.append(ds)

        width, height = ref_info["width"], ref_info["height"]
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(output_path):
            try:
                gdal.GetDriverByName("GTiff").Delete(output_path)
            except Exception:
                pass
        driver = gdal.GetDriverByName("GTiff")
        dst = driver.Create(
            output_path, width, height, 1, gdal.GDT_Float32,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER",
                     "PREDICTOR=3"],
        )
        if dst is None:
            raise RuntimeError(f"Cannot create output file: {output_path}")
        try:
            dst.SetGeoTransform(ref_info["geotransform"])
            if ref_info["projection"]:
                dst.SetProjection(ref_info["projection"])
            band = dst.GetRasterBand(1)
            band.SetNoDataValue(WI_NODATA)

            n_files = len(datasets)
            for y0 in range(0, height, _CHUNK_ROWS):
                if cancel_cb is not None and cancel_cb():
                    raise OperationCancelled()
                rows = min(_CHUNK_ROWS, height - y0)
                stack = np.empty((n_files, rows, width), dtype=np.float32)
                for i, ds in enumerate(datasets):
                    chunk = ds.GetRasterBand(1).ReadAsArray(
                        0, y0, width, rows
                    )
                    if chunk is None:
                        raise RuntimeError(
                            f"Failed to read rows {y0}..{y0 + rows} of {aligned[i]}"
                        )
                    stack[i] = to_db_domain_chunk(chunk)
                with np.errstate(invalid="ignore"):
                    med = np.nanmedian(stack, axis=0)
                med = med.astype(np.float32, copy=False)
                med[~np.isfinite(med)] = WI_NODATA
                band.WriteArray(med, 0, y0)
                if progress_cb is not None:
                    frac = (y0 + rows) / float(height) * 100.0
                    progress_cb(frac, "Compositing")
                del stack
            band.FlushCache()
        finally:
            dst = None
    finally:
        datasets.clear()

    return os.path.abspath(output_path)


# ======================================================================
# Main detector
# ======================================================================
class WindthrowDetector:
    """Bi-temporal WI windthrow detector (Rüetschi et al. 2019).

    Parameters mirror the paper's decision tree: ``a_db`` (dB offset
    above the mean WI, adaptive mode) or ``fixed_threshold_db`` (fixed
    mode), ``min_pixels`` (minimum object size in pixels), and an
    optional median filter applied to WI before thresholding.
    """

    def __init__(
        self,
        threshold_mode: str = "adaptive",
        a_db: float = 2.9,
        fixed_threshold_db: float = 3.0,
        min_pixels: int = 27,
        median_filter_size: int = 3,
        normalize_background: bool = True,
    ) -> None:
        if threshold_mode not in ("adaptive", "fixed"):
            raise ValueError("threshold_mode must be 'adaptive' or 'fixed'")
        self.threshold_mode = threshold_mode
        self.a_db = float(a_db)
        self.fixed_threshold_db = float(fixed_threshold_db)
        self.min_pixels = int(min_pixels)
        self.median_filter_size = int(median_filter_size)
        self.normalize_background = bool(normalize_background)
        # Diagnostics filled in by detect_file():
        self.mean_wi: Optional[float] = None
        self.threshold_used: Optional[float] = None
        self.n_objects: int = 0
        self.composites: List[str] = []
        # Per-polarisation background offsets (dB) applied to the post
        # image when normalize_background is enabled (v0.8).
        self.offset_db: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Hooks for index-family subclasses (v1.0: L-band decline).  The
    # base class implements the C-band WI (backscatter INCREASE over
    # windthrow); subclasses flip the sign / rename the outputs without
    # duplicating the compositing-threshold-polygonize chain.
    # ------------------------------------------------------------------
    def _delta_sign(self) -> float:
        """Sign applied to the summed per-polarisation difference.

        +1.0 for the C-band WI (windthrown forest scatters MORE).
        Subclasses may return -1.0 (backscatter DECLINE over windthrow,
        L-band physics of Tanase et al. 2018).
        """
        return 1.0

    def _index_suffix(self) -> str:
        """File-name suffix of the float32 index raster."""
        return "_wi_norm" if self.normalize_background else "_wi"

    def _result_mode(self) -> str:
        """Mode tag of the structured :class:`DetectionResult` (v1.3.0).

        The C-band base class reports "wi"; the L-band subclass
        overrides this to "lband_decline".  Both share the legacy
        result keys (``wi`` / ``threshold_db``).
        """
        return "wi"

    def _restrict_polarizations(self, pols: List[str]) -> List[str]:
        """Filter the common polarisation list (no-op in the base class)."""
        return pols

    # ------------------------------------------------------------------
    def detect_file(
        self,
        pre_paths: Sequence[str],
        post_paths: Sequence[str],
        output_base: str,
        analysis_mask_path: Optional[str] = None,
        progress_cb: ProgressCallback = None,
        cancel_cb: CancelCallback = None,
        background_mask_path: Optional[str] = None,
        forest_mask_path: Optional[str] = None,
    ) -> Dict[str, object]:
        """Run the full detection chain and write raster + vector outputs.

        :param pre_paths: SAR files covering the pre-storm window.
        :param post_paths: SAR files covering the post-storm window.
        :param output_base: output path base (no extension); produces
            ``<base>_wi.tif`` (or ``<base>_wi_norm.tif`` when background
            normalization is on), ``<base>_mask.tif``, ``<base>.gpkg``
            and, when a stack is composited, ``<base>_pre_<pol>.tif`` /
            ``<base>_post_<pol>.tif``.
        :param analysis_mask_path: optional raster or vector; detection
            flags are restricted to pixels inside (value > 0 / polygon
            areas).
        :param background_mask_path: optional raster or vector defining
            the background/forest sample used for the adaptive-threshold
            mean WI and, when ``normalize_background`` is on, for the
            per-polarisation median post−pre offsets.  Falls back to
            ``analysis_mask_path`` and then to the whole scene.  For the
            Rüetschi-style forest mean pass a forest sample that EXCLUDES
            known windthrow polygons (e.g. a buffer around them minus
            the polygons themselves).
        :param forest_mask_path: optional raster or vector defining the
            FOREST area (v0.9); detections are restricted to the
            intersection with ``analysis_mask_path``, and — when no
            ``background_mask_path`` is given — the adaptive-threshold
            mean is computed over the forest only (paper behaviour).
            ESA WorldCover rasters built by
            ``sources.forest_mask`` are directly usable here.
        :return: dict with keys ``wi``, ``mask``, ``vector``,
            ``composites``, ``threshold_db``, ``mean_wi``, ``offset_db``,
            ``normalize_background``.
        """
        _ensure_gdal()
        report = (lambda f, m: progress_cb(f, m) if progress_cb else None)
        cancelled = (lambda: bool(cancel_cb()) if cancel_cb else False)

        # ---- 1. Validate inputs and pick polarisations ----------------
        if not pre_paths or not post_paths:
            raise ValueError("Both pre-storm and post-storm file lists are required")
        for p in list(pre_paths) + list(post_paths):
            if not os.path.isfile(p):
                raise ValueError(f"File not found: {p}")
        pols = self._restrict_polarizations(
            common_polarizations(pre_paths, post_paths))
        if not pols:
            raise ValueError(
                "No common polarisation found between the pre- and "
                "post-storm file names (looked for vv/vh/hh/hv tokens)."
            )

        pre_by_pol = pair_by_polarization(pre_paths)
        post_by_pol = pair_by_polarization(post_paths)

        base_dir = os.path.dirname(os.path.abspath(output_base)) or "."
        os.makedirs(base_dir, exist_ok=True)
        tmp_dir = os.path.join(base_dir, "_windthrow_tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        base_name = os.path.basename(output_base)
        if base_name.lower().endswith((".tif", ".tiff", ".gpkg", ".shp")):
            base_name = os.path.splitext(base_name)[0]
            output_base = os.path.join(base_dir, base_name)

        # Reference grid: the first post-storm file of the first pol.
        ref_info = _read_raster_info(post_by_pol[pols[0]][0])
        width, height = ref_info["width"], ref_info["height"]

        # Optional analysis mask (detection restriction, flags only).
        mask_raster: Optional[str] = None
        if analysis_mask_path:
            if not os.path.isfile(analysis_mask_path):
                raise ValueError(f"Mask file not found: {analysis_mask_path}")
            mask_raster = _resolve_mask_raster(
                analysis_mask_path, ref_info, tmp_dir)

        # Optional background/forest sample mask (statistics only).
        bg_mask_raster: Optional[str] = None
        if background_mask_path:
            if not os.path.isfile(background_mask_path):
                raise ValueError(
                    f"Background mask file not found: {background_mask_path}")
            bg_mask_raster = _resolve_mask_raster(
                background_mask_path, ref_info, tmp_dir)

        # Optional forest mask (v0.9) — restricts the analysis area to
        # forest: detections and (by default) the adaptive-threshold
        # statistics live inside the forest ∩ analysis-mask intersection.
        forest_raster: Optional[str] = None
        if forest_mask_path:
            if not os.path.isfile(forest_mask_path):
                raise ValueError(
                    f"Forest mask file not found: {forest_mask_path}")
            forest_raster = _resolve_mask_raster(
                forest_mask_path, ref_info, tmp_dir)
            if mask_raster:
                mask_raster = _intersect_masks(
                    mask_raster, forest_raster, ref_info, tmp_dir)
            else:
                mask_raster = forest_raster

        # ---- 2. Composites per period / polarisation ------------------
        comp_paths: Dict[Tuple[str, str], str] = {}
        self.composites = []
        comp_span = 40.0 / (2.0 * len(pols))
        offset_span = (10.0 / len(pols)) if self.normalize_background else 0.0
        done_steps = 0
        for period, by_pol in (("pre", pre_by_pol), ("post", post_by_pol)):
            for pol in pols:
                files = by_pol[pol]
                if len(files) == 1:
                    comp_paths[(period, pol)] = ensure_aligned(
                        files[0], ref_info, tmp_dir)
                else:
                    out = f"{output_base}_{period}_{pol}.tif"
                    build_median_composite(
                        files, ref_info, tmp_dir, out,
                        progress_cb=lambda f, m, d0=done_steps * comp_span:
                            report(d0 + (f / 100.0) * comp_span, m),
                        cancel_cb=cancel_cb,
                    )
                    comp_paths[(period, pol)] = out
                    self.composites.append(out)
                done_steps += 1
                report(done_steps * comp_span, "Compositing")

        # ---- 2b. Background normalization offsets (v0.8) --------------
        # Per polarisation: offset = median(post - pre) inside the
        # background sample (bg mask, else analysis mask, else whole
        # scene).  The offset is subtracted from the post image before
        # differencing, centring the background WI on ~0 dB.
        offsets: Dict[str, float] = {}
        if self.normalize_background:
            stats_ds_h = None
            stats_band_h = None
            if bg_mask_raster:
                stats_ds_h = gdal.Open(bg_mask_raster, gdal.GA_ReadOnly)
                stats_band_h = stats_ds_h.GetRasterBand(1)
            elif mask_raster:
                stats_ds_h = gdal.Open(mask_raster, gdal.GA_ReadOnly)
                stats_band_h = stats_ds_h.GetRasterBand(1)
            pol_done = 0
            try:
                for pol in pols:
                    if cancelled():
                        raise OperationCancelled()
                    pre_p = gdal.Open(comp_paths[("pre", pol)], gdal.GA_ReadOnly)
                    post_p = gdal.Open(comp_paths[("post", pol)], gdal.GA_ReadOnly)
                    if pre_p is None or post_p is None:
                        raise RuntimeError(
                            f"Cannot open composites for offset ({pol})")
                    samples: List[np.ndarray] = []
                    for y0 in range(0, height, _CHUNK_ROWS):
                        if cancelled():
                            raise OperationCancelled()
                        rows = min(_CHUNK_ROWS, height - y0)
                        pre_c = to_db_domain_chunk(
                            pre_p.GetRasterBand(1).ReadAsArray(0, y0, width, rows))
                        post_c = to_db_domain_chunk(
                            post_p.GetRasterBand(1).ReadAsArray(0, y0, width, rows))
                        with np.errstate(invalid="ignore"):
                            delta = post_c - pre_c
                        if stats_band_h is not None:
                            inside = stats_band_h.ReadAsArray(
                                0, y0, width, rows) > 0
                            delta = delta[inside]
                        vals = delta[np.isfinite(delta)]
                        if vals.size:
                            samples.append(vals)
                    pre_p = None
                    post_p = None
                    if samples:
                        all_vals = np.concatenate(samples)
                        if all_vals.size > 8_000_000:
                            stride = int(np.ceil(
                                all_vals.size / 8_000_000.0))
                            all_vals = all_vals[::stride]
                        offsets[pol] = float(np.median(all_vals))
                    else:
                        offsets[pol] = 0.0
                    self.offset_db = dict(offsets)
                    pol_done += 1
                    report(40.0 + pol_done * offset_span,
                           "Background normalization")
            finally:
                stats_ds_h = None
                stats_band_h = None

        # ---- 3. WI raster + running statistics (chunked) --------------
        suffix = self._index_suffix()
        wi_path = f"{output_base}{suffix}.tif"
        if os.path.exists(wi_path):
            try:
                gdal.GetDriverByName("GTiff").Delete(wi_path)
            except Exception:
                pass
        driver = gdal.GetDriverByName("GTiff")
        wi_ds = driver.Create(
            wi_path, width, height, 1, gdal.GDT_Float32,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER",
                     "PREDICTOR=3"],
        )
        if wi_ds is None:
            raise RuntimeError(f"Cannot create output file: {wi_path}")
        wi_ds.SetGeoTransform(ref_info["geotransform"])
        if ref_info["projection"]:
            wi_ds.SetProjection(ref_info["projection"])
        wi_band = wi_ds.GetRasterBand(1)
        wi_band.SetNoDataValue(WI_NODATA)

        datasets: Dict[Tuple[str, str], object] = {}
        mask_ds = None
        mask_band = None
        bg_mask_ds = None
        bg_mask_band = None
        try:
            for key, path in comp_paths.items():
                ds = gdal.Open(path, gdal.GA_ReadOnly)
                if ds is None:
                    raise RuntimeError(f"Cannot open composite: {path}")
                datasets[key] = ds

            if mask_raster:
                mask_ds = gdal.Open(mask_raster, gdal.GA_ReadOnly)
                mask_band = mask_ds.GetRasterBand(1)
            # Adaptive-threshold statistics come from the background
            # sample (v0.8) when provided, else from the analysis mask
            # (v0.7.0 behaviour), else from the whole scene.
            if bg_mask_raster:
                bg_mask_ds = gdal.Open(bg_mask_raster, gdal.GA_ReadOnly)
                bg_mask_band = bg_mask_ds.GetRasterBand(1)
            stats_band = bg_mask_band if bg_mask_band is not None else mask_band

            running_sum = 0.0
            running_sumsq = 0.0
            running_count = 0

            for y0 in range(0, height, _CHUNK_ROWS):
                if cancelled():
                    raise OperationCancelled()
                rows = min(_CHUNK_ROWS, height - y0)
                wi_chunk = np.zeros((rows, width), dtype=np.float32)
                valid_chunk = np.ones((rows, width), dtype=bool)
                for pol in pols:
                    pre_chunk = datasets[("pre", pol)].GetRasterBand(1).ReadAsArray(
                        0, y0, width, rows)
                    post_chunk = datasets[("post", pol)].GetRasterBand(1).ReadAsArray(
                        0, y0, width, rows)
                    if pre_chunk is None or post_chunk is None:
                        raise RuntimeError(
                            f"Failed to read chunk rows {y0}..{y0 + rows}")
                    pre_db = to_db_domain_chunk(pre_chunk)
                    post_db = to_db_domain_chunk(post_chunk)
                    if self.normalize_background and pol in offsets:
                        post_db = post_db - offsets[pol]
                    with np.errstate(invalid="ignore"):
                        delta = post_db - pre_db
                    delta = delta.astype(np.float32, copy=False)
                    good = np.isfinite(delta)
                    if self._delta_sign() < 0:
                        delta = -delta
                    wi_chunk[good] += delta[good]
                    # A pixel is valid only when EVERY polarisation yields
                    # a finite difference (paper sums VV + VH).
                    valid_chunk &= good
                wi_chunk[~valid_chunk] = WI_NODATA
                wi_band.WriteArray(wi_chunk, 0, y0)

                # Adaptive-threshold statistics (sample-limited).
                finite = wi_chunk != WI_NODATA
                if stats_band is not None:
                    inside = stats_band.ReadAsArray(0, y0, width, rows) > 0
                    finite &= inside
                vals = wi_chunk[finite].astype(np.float64, copy=False)
                if vals.size:
                    running_sum += float(vals.sum())
                    running_sumsq += float(np.square(vals).sum())
                    running_count += int(vals.size)
                report(50.0 + 20.0 * (y0 + rows) / float(height),
                       "Windthrow Index")
                del wi_chunk

            wi_band.FlushCache()
            if running_count == 0:
                raise RuntimeError(
                    "No valid overlapping pixels found — check that the "
                    "pre- and post-storm images cover the same area."
                )
            mean_wi = running_sum / running_count
            self.mean_wi = mean_wi
            if self.threshold_mode == "adaptive":
                threshold = adaptive_threshold(mean_wi, self.a_db)
            else:
                threshold = self.fixed_threshold_db
            self.threshold_used = threshold

            # ---- 4. Threshold pass via the shared pipeline (v1.3.0) ----
            # Chunked NaN-aware median filter + thresholding + inside-
            # mask restriction now live in sources/pipeline.py (spec
            # §4); the in-memory WI band is only read here and the
            # legacy progress mapping (70-80 %) / "WI" warning label
            # are preserved.
            full_mask = threshold_pass(
                wi_band, width, height, threshold,
                nodata=WI_NODATA, median_size=self.median_filter_size,
                inside_band=mask_band, report=report, cancelled=cancelled,
                progress_start=70.0, progress_span=10.0, median_label="WI")
        finally:
            wi_ds = None
            datasets.clear()
            mask_ds = None
            mask_band = None
            bg_mask_ds = None
            bg_mask_band = None

        # ---- 5-7. Shared postprocess pipeline (v1.3.0, spec §4) --------
        # speckle cleanup -> mask raster -> polygonize; stage bodies
        # moved verbatim to sources/pipeline.py (the one place to fix
        # polygonize/area bugs).
        full_mask = remove_small_objects(full_mask, self.min_pixels)
        mask_path = write_mask_raster(full_mask, ref_info, output_base)
        vector_path, self.n_objects = polygonize_mask(
            mask_path, ref_info, output_base, self.min_pixels)

        # ---- 8. Best-effort cleanup of temporaries ---------------------
        try:
            for f in os.listdir(tmp_dir):
                try:
                    os.remove(os.path.join(tmp_dir, f))
                except OSError:
                    pass
            os.rmdir(tmp_dir)
        except OSError:
            pass

        report(100.0, "Done")
        # v1.3.0: typed result (sources/types.py).  DetectionResult is a
        # read-only Mapping view over its legacy dict shape, so the GUI
        # and the tests keep using result["wi"] / result.get(...) as
        # before; use attributes / .to_dict() in new code.
        return DetectionResult(
            mode=self._result_mode(),
            index_path=os.path.abspath(wi_path),
            mask_path=os.path.abspath(mask_path),
            vector_path=os.path.abspath(vector_path),
            n_objects=self.n_objects,
            threshold=self.threshold_used,
            forest_mask_path=(os.path.abspath(forest_raster)
                              if forest_raster else None),
            extras={
                "composites": list(self.composites),
                "mean_wi": self.mean_wi,
                "offset_db": dict(self.offset_db),
                "normalize_background": self.normalize_background,
            },
        )
