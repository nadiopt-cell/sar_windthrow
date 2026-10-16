"""HyP3 InSAR product naming, discovery and water-mask handling.

Moved verbatim from ``coh_delta.py`` in the v1.3.0 refactor so the
coherence-DiD detector keeps only the index/DiD logic while product
bookkeeping lives here.  Everything in this module is detector-agnostic
and unit-testable (GDAL is imported optionally; helpers that need it
raise a descriptive RuntimeError).

Covered concerns:

* granule-name parsing for both HyP3 product families — classic
  full-frame GAMMA ``INSAR_GAMMA`` (80 m) and burst-based
  ``INSAR_ISCE_BURST`` / ``INSAR_ISCE_MULTI_BURST`` (20/40/80 m);
* layer discovery for ``*_corr.tif`` and the optional water mask,
  from unpacked directories, layer paths or original ``.zip`` archives
  (unpacked on the fly);
* the corrupt-water-mask sanity heuristic (step12b, v1.2 semantics):
  per the ASF product guides (GAMMA and ISCE alike) water masks encode
  **1 = land, 0 = water** — a mask claiming more than
  ``DEFAULT_MAX_WATER_FRAC`` water is ignored with a warning;
* conversion of a water mask into a reference-grid keep-land layer
  (255 = land), the v1.2 fix of the v1.0/v1.1 sign error.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from typing import List, Optional

import numpy as np

try:
    from osgeo import gdal  # always present in the QGIS Python env
    gdal.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None  # type: ignore

from .constants import (
    _CHUNK_ROWS,
    DEFAULT_MAX_WATER_FRAC,
    LAND_VALUE,
    WATER_VALUE,
)
from .raster_utils import _same_grid_safe
from ..logger import log_warning

__all__ = [
    "_CORR_SUFFIX",
    "_ISCE_RE",
    "_GAMMA_RE",
    "_WATER_SUFFIXES",
    "_granule_names",
    "_validate_did_pair",
    "_water_mask_to_land_keep",
    "find_correlation_tif",
    "find_water_mask",
    "parse_hyp3_product",
    "sane_water_mask",
]

#: Filename suffix of the HyP3 coherence layer.
_CORR_SUFFIX = "_corr.tif"

#: Filename suffixes accepted as HyP3 water-mask layers.
_WATER_SUFFIXES = ("_wm.tif", "_water_mask.tif")

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
    the coh_delta module docstring): the previous code kept mask > 0
    through :func:`_resolve_mask_raster`, which under the real
    convention keeps WATER and clips land windthrow.
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
