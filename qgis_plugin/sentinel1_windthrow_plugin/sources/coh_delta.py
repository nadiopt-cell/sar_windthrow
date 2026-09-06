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
"""

import os
import re
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

#: HyP3 water-mask encoding (ASF product guides, GAMMA **and** ISCE):
#: «Pixel values of 1 indicate land and 0 indicate water».  v1.0/v1.1
#: wrongly assumed 1 = water — the v1.2 fix is documented in the
#: module docstring and RELEASE_NOTES_v1.2.0.
LAND_VALUE = 1
WATER_VALUE = 0

#: Physically plausible coherence range; anything outside (e.g. the
#: +-9999 fill of a warped control product) is treated as no-data.
COH_MIN, COH_MAX = -0.01, 1.01

# ----------------------------------------------------------------------
# HyP3 granule-name parsing (v1.2: burst support)
# ----------------------------------------------------------------------
#: GAMMA full-frame InSAR granule, e.g.
#: ``S1AB_20171111T150004_20171117T145926_VVP006_INT80_G_ueF_4D09``
#: (platforms of the pair — ``S1AB`` = ref S1A + sec S1B, ref/secondary
#: dates, pol+pass+orbit, type+spacing, processor G = GAMMA, frame
#: code, ASF product id).
_GAMMA_RE = re.compile(
    r"^S1(?P<platform>[AB]?)[AB]?_"
    r"(?P<date1>\d{8}T\d{6})_(?P<date2>\d{8}T\d{6})_"
    r"(?P<pol>[VH][VH])P\d{3}_INT(?P<spacing>\d{2})_G_"
    r"\w{3}_(?P<pid>[0-9A-Fa-f]{4})$")

#: ISCE burst / multi-burst InSAR granule, e.g.
#: ``S1_123_111111s1n02-111111s2n01-000000s3n00_IW_20240101_20240115_VV_INT80_AEB4``
#: (platform, track, relative burst IDs per subswath, mode, ref/secondary
#: dates, pol, type+spacing, ASF product id).
_ISCE_RE = re.compile(
    r"^S1(?P<platform>[AB]?)[AB]?_"
    r"(?P<track>\d{3})_"
    r"(?P<burst_ids>[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*)_"
    r"(?P<mode>IW|EW)_"
    r"(?P<date1>\d{8})_(?P<date2>\d{8})_"
    r"(?P<pol>[VH][VH])_INT(?P<spacing>\d{2})_"
    r"(?P<pid>[0-9A-Fa-f]{4})$")


def _granule_names(source: str) -> List[str]:
    """Candidate granule names for :func:`parse_hyp3_product`.

    Real HyP3 products keep the granule name in the zip stem, the
    unpacked directory name and the ``*_corr.tif`` stem (all equal);
    renamed products are handled by trying all three.
    """
    source = os.path.abspath(source)
    candidates: List[str] = []
    if os.path.isdir(source):
        candidates.append(os.path.basename(source.rstrip(os.sep)))
    else:
        base = os.path.basename(source)
        lower = base.lower()
        stem = os.path.splitext(base)[0]
        if lower.endswith(".zip"):
            candidates.append(stem)
        for suffix in (_CORR_SUFFIX, "_unw.tif", "_wm.tif",
                       "_water_mask.tif"):
            # Compare against the full basename: splitext already
            # stripped ".tif" from the stem.
            if lower.endswith(suffix):
                candidates.append(base[: -len(suffix)])
        parent = os.path.basename(os.path.dirname(source))
        if parent:
            candidates.append(parent)
        # Catch-all: the raw stem of any non-directory input (custom
        # names without a recognised layer suffix).
        candidates.append(stem)
    # Deduplicate, preserve order.
    seen = set()
    unique = []
    for name in candidates:
        key = name.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def parse_hyp3_product(source: str) -> dict:
    """Parse the HyP3 InSAR product metadata from granule names.

    Tries every candidate granule name derived from ``source``
    (directory, ``.zip``, ``*_corr.tif`` path — see
    :func:`_granule_names`) against the GAMMA and ISCE burst naming
    schemes.  Unrecognised products (user-renamed, custom) return
    ``{"family": None, ...}`` and are skipped by the DiD validation.

    :return: dict with ``family`` (``"gamma"`` / ``"isce"`` / ``None``),
        ``granule`` (the name that parsed, else the first candidate),
        ``platform``, ``track``, ``pol``, ``spacing_m``, ``pid``,
        ``date1``, ``date2``, and for ISCE ``burst_ids`` (the raw
        dash-joined token) plus ``burst_key`` (normalised footprint
        identity used for same-burst DiD validation).
    """
    result: dict = {
        "family": None, "granule": None, "platform": None, "track": None,
        "pol": None, "spacing_m": None, "pid": None,
        "date1": None, "date2": None, "burst_ids": None, "burst_key": None,
    }
    candidates = _granule_names(source)
    result["granule"] = candidates[0] if candidates else None
    for name in candidates:
        m = _ISCE_RE.match(name)
        if m:
            result.update({
                "family": "isce",
                "granule": name,
                "platform": m.group("platform") or None,
                "track": int(m.group("track")),
                "pol": m.group("pol").upper(),
                "spacing_m": int(m.group("spacing")),
                "pid": m.group("pid").upper(),
                "date1": m.group("date1"),
                "date2": m.group("date2"),
                "burst_ids": m.group("burst_ids"),
                "burst_key": m.group("burst_ids").lower(),
            })
            return result
        m = _GAMMA_RE.match(name)
        if m:
            result.update({
                "family": "gamma",
                "granule": name,
                "platform": m.group("platform") or None,
                "track": None,
                "pol": m.group("pol").upper(),
                "spacing_m": int(m.group("spacing")),
                "pid": m.group("pid").upper(),
                "date1": m.group("date1"),
                "date2": m.group("date2"),
            })
            return result
    return result


def _validate_did_pair(prepost_info: dict, control_info: dict) -> None:
    """Reject DiD product pairs that cannot share a coherence footprint.

    Rules (v1.2):

    * GAMMA frame + ISCE burst product — incompatible footprints and
      look conventions; refuse instead of producing a meaningless
      almost-fully-masked DiD.
    * two ISCE burst products — must be the SAME burst footprint: equal
      normalised burst-id token, track and polarization; the coherence
      rasters of different bursts only overlap along burst borders, so
      a mismatch degrades the DiD to edge noise.
    * different pixel spacing (e.g. 40 m pre/post vs 80 m control) —
      resampling makes the DiD statistically noisy; warn loudly.
    """
    if prepost_info["family"] != control_info["family"]:
        raise ValueError(
            "Mixing product families in one DiD is not supported: "
            f"pre/post is {prepost_info['family']} "
            f"({prepost_info['granule']}), control is "
            f"{control_info['family']} ({control_info['granule']}). "
            "Order both pairs as the same HyP3 product type — "
            "INSAR_GAMMA frames or INSAR_ISCE_BURST bursts.")
    if prepost_info["family"] == "isce":
        if prepost_info["burst_key"] != control_info["burst_key"]:
            raise ValueError(
                "Burst InSAR DiD requires the SAME burst footprint: "
                f"pre/post burst ids {prepost_info['burst_ids']} != "
                f"control burst ids {control_info['burst_ids']} "
                f"(track {prepost_info['track']} vs "
                f"{control_info['track']}, pol {prepost_info['pol']} vs "
                f"{control_info['pol']}). Order the control pair for the "
                "same burst (Vertex: same burst ID, dates outside the "
                "damage window).")
        if prepost_info["pol"] != control_info["pol"]:
            raise ValueError(
                "Burst InSAR DiD polarization mismatch: "
                f"{prepost_info['pol']} vs {control_info['pol']}.")
    if (prepost_info["spacing_m"] and control_info["spacing_m"]
            and prepost_info["spacing_m"] != control_info["spacing_m"]):
        log_warning(
            "DiD pixel-spacing mismatch: pre/post is "
            f"{prepost_info['spacing_m']} m, control is "
            f"{control_info['spacing_m']} m — the control will be "
            "resampled and the dcoh statistics noisy; prefer products "
            "with the same look selection.")


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
    water_value: int = WATER_VALUE,
) -> Optional[str]:
    """Apply the corrupt-water-mask heuristic of step12b (v1.2 semantics).

    Per the ASF product guides (GAMMA and ISCE alike) water masks
    encode **1 = land, 0 = water** — so the water fraction is the share
    of ``water_value`` (default 0) pixels.  When it exceeds
    ``max_water_frac`` the mask is considered CORRUPT (HyP3 product
    5748 claimed 99.6 % of an inland forest frame as water) and
    ``None`` is returned so the caller skips it.  A sane mask is
    returned unchanged.

    Legacy products with the opposite encoding (1 = water) can be
    consumed with ``water_value=1``.

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
            water += int((chunk == water_value).sum())
        frac = (water / total) if total else 0.0
    finally:
        ds = None
    if frac > max_water_frac:
        log_warning(
            f"Water mask {os.path.basename(water_mask_path)} claims "
            f"{frac:.1%} water (> {max_water_frac:.0%}) — corrupt, ignoring "
            "(step12b sane-mask heuristic, HyP3 convention: 1 = land, "
            f"0 = water; here water == {water_value}).")
        return None
    return water_mask_path


def _water_mask_to_land_keep(
    water_mask_path: str, ref_info: dict, tmp_dir: str
) -> str:
    """Convert a HyP3 water mask into a reference-grid keep-land mask.

    HyP3 masks encode 1 = land, 0 = water (:data:`LAND_VALUE` /
    :data:`WATER_VALUE`).  The mask is warped onto the reference grid
    with nearest-neighbour resampling (bilinear would blend the land /
    water codes), missing warp coverage is filled with a dedicated
    "no-coverage" code excluded from the keep mask, and the output is
    a 0/255 byte raster: 255 = land (kept), 0 = water or unknown.

    This is the v1.2 fix of the v1.0/v1.1 water-mask sign error (see
    the module docstring): the previous code kept mask > 0 through
    :func:`_resolve_mask_raster`, which under the real convention keeps
    WATER and clips land windthrow.
    """
    if gdal is None:
        raise RuntimeError("GDAL is required to process water masks")
    os.makedirs(tmp_dir, exist_ok=True)
    if not _same_grid_safe(water_mask_path, ref_info):
        aligned = os.path.join(tmp_dir, "_wm_aligned.tif")
        if os.path.exists(aligned):
            try:
                gdal.GetDriverByName("GTiff").Delete(aligned)
            except Exception:
                pass
        gdal.Warp(
            aligned,
            water_mask_path,
            format="GTiff",
            width=ref_info["width"],
            height=ref_info["height"],
            outputBounds=(
                ref_info["geotransform"][0],
                ref_info["geotransform"][3]
                + ref_info["geotransform"][5] * ref_info["height"],
                ref_info["geotransform"][0]
                + ref_info["geotransform"][1] * ref_info["width"],
                ref_info["geotransform"][3],
            ),
            dstNodata=2,  # no warp coverage — excluded from the keep mask
            resampleAlg="nearest",
            multithread=True,
        )
    else:
        aligned = water_mask_path

    out_path = os.path.join(tmp_dir, "_wm_land_keep.tif")
    if os.path.exists(out_path):
        try:
            gdal.GetDriverByName("GTiff").Delete(out_path)
        except Exception:
            pass
    driver = gdal.GetDriverByName("GTiff")
    width, height = ref_info["width"], ref_info["height"]
    out = driver.Create(
        out_path, width, height, 1, gdal.GDT_Byte,
        options=["TILED=YES", "COMPRESS=LZW"],
    )
    if out is None:
        raise RuntimeError(f"Cannot create land-keep mask: {out_path}")
    try:
        out.SetGeoTransform(ref_info["geotransform"])
        if ref_info["projection"]:
            out.SetProjection(ref_info["projection"])
        out_band = out.GetRasterBand(1)
        out_band.SetNoDataValue(0)
        src_ds = gdal.Open(aligned, gdal.GA_ReadOnly)
        if src_ds is None:
            raise RuntimeError(f"Cannot open aligned water mask: {aligned}")
        try:
            src_band = src_ds.GetRasterBand(1)
            for y0 in range(0, height, _CHUNK_ROWS):
                rows = min(_CHUNK_ROWS, height - y0)
                chunk = src_band.ReadAsArray(0, y0, width, rows)
                keep = chunk == LAND_VALUE
                out_band.WriteArray(keep.astype(np.uint8) * 255, 0, y0)
            out_band.FlushCache()
        finally:
            src_ds = None
    finally:
        out = None
    return out_path


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
            ``product_info`` (parsed granule metadata).
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
                if bg_band is not None:
                    bg_inside = bg_band.ReadAsArray(
                        0, y0, width, rows) > 0
                    finite &= bg_inside
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
        }


def _same_grid_safe(path: str, ref_info: dict) -> bool:
    """Grid comparison that tolerates a missing/unsupported reference."""
    try:
        return _read_raster_info(path)["geotransform"] == ref_info["geotransform"] \
            and _read_raster_info(path)["width"] == ref_info["width"] \
            and _read_raster_info(path)["height"] == ref_info["height"]
    except Exception:
        return False
