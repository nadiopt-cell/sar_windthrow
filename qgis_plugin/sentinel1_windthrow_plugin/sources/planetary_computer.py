"""Planetary Computer SAR source — stdlib only, no pystac-client.

Implements :class:`BaseSARSource` using the Microsoft Planetary Computer
STAC API to search for Sentinel-1 GRD scenes and download Cloud-Optimized
GeoTIFF (COG) assets.

This module deliberately avoids the ``pystac-client`` dependency. The
QGIS Python environment on Windows ships without it, and asking users
to ``pip install`` into the bundled interpreter is fragile. Instead we
talk to the STAC catalogue directly via ``urllib.request`` + ``json``,
following the same pattern as the reference ``dem_comparator`` plugin.

All HTTP calls are delegated to :class:`PlanetaryComputerClient`
(``pc_client.py``), which also handles SAS-token signing so the
downloaded GeoTIFFs are accessible anonymously.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..logger import log_info, log_warning
from .base import BaseSARSource, OperationCancelled, Scene
from .pc_client import PCError, PlanetaryComputerClient

# Progress and cancellation callback types (re-exported for convenience).
ProgressCallback = Optional[Callable[[int], None]]
CancelCallback = Optional[Callable[[], bool]]

# Asset keys that contain visual previews (NOT measurement data). We pull
# these out of the generic ``assets`` dict so the download path doesn't grab
# them along with the real SAR bands. Module-level constant: building the
# set inside the per-item loop re-created it on every scene for no reason.
_PREVIEW_ASSET_KEYS = frozenset({
    "rendered_preview", "preview", "thumbnail",
    "quicklook", "quick-look", "browse",
})

# SAR measurement bands we actually want on disk. Both GRD and RTC items
# also carry auxiliary rasters (calibration/noise schemas, layover-shadow
# masks, incidence-angle maps) that would otherwise be downloaded for
# nothing — a full-scene incidence map alone is tens of MB. If an item's
# assets contain none of these keys (unknown schema), we fall back to
# downloading everything non-preview rather than nothing at all.
_MEASUREMENT_ASSET_KEYS = frozenset({"vv", "vh", "hh", "hv"})

#: User-Agent sent with every raw HTTP request made by this module.
_REQUEST_USER_AGENT = "QGIS-Sentinel1-Plugin/0.5"


def _sleep_interruptible(seconds: float, cancel_cb: CancelCallback) -> None:
    """Sleep in small slices so cancellation is noticed promptly.

    Used between download retry attempts — a plain ``time.sleep`` would
    leave the user staring at a stuck progress bar after pressing Cancel.
    Raises :class:`OperationCancelled` when ``cancel_cb`` turns True.
    """
    deadline = time.time() + max(0.0, seconds)
    while True:
        if cancel_cb and cancel_cb():
            raise OperationCancelled("Cancelled while waiting for retry backoff")
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def _default_token_cache_path() -> Optional[str]:
    """Pick a writable token-cache path under the QGIS profile directory.

    Falls back to ``None`` (no on-disk cache) if the QGIS settings dir
    cannot be resolved — in that case tokens are still cached in memory
    for the lifetime of the client.
    """
    try:
        from qgis.core import QgsApplication

        get_dir = getattr(
            QgsApplication,
            "qgisSettingsDirPath",
            getattr(QgsApplication, "qgisSettingsDir", None),
        )
        if get_dir is None:
            return None
        base = get_dir()
        if not base:
            return None
        return os.path.join(base, "sentinel1_plugin", "pc_tokens.json")
    except Exception:
        return None


class PlanetaryComputerSource(BaseSARSource):
    """Search and download Sentinel-1 scenes from Planetary Computer.

    Two product collections are supported (selectable per search via the
    ``collection`` argument):

    * ``sentinel-1-grd`` — the standard Ground Range Detected product
      (sigma0). Correct choice over open water and flat terrain: oil-spill
      screening, ship detection.
    * ``sentinel-1-rtc`` — Radiometric Terrain Corrected gamma0. The DEM
      based correction removes layover / radar-shadow artifacts that make
      threshold water detectors hallucinate "flooded" slopes; use it for
      flood mapping and land/water masks in hilly or mountainous AOIs.
      Note the absolute backscatter levels differ slightly (sigma0 vs
      gamma0), so dB thresholds may need retuning when switching products.

    The class is light — it wraps a single :class:`PlanetaryComputerClient`
    that handles HTTP, SAS signing and pagination. Constructing the source
    never touches the network; the first HTTP call happens on
    :meth:`search` or :meth:`download`.
    """

    DEFAULT_COLLECTION = "sentinel-1-grd"
    RTC_COLLECTION = "sentinel-1-rtc"

    def __init__(
        self,
        client: Optional[PlanetaryComputerClient] = None,
        collection: Optional[str] = None,
    ) -> None:
        # Constructing the client never raises — it only sets up caches.
        # This means the plugin dialog can always be opened, even on a
        # machine with no network. The first actual HTTP call happens
        # only when the user clicks "Search".
        if client is None:
            client = PlanetaryComputerClient(
                token_cache_path=_default_token_cache_path(),
            )
        self._client = client
        #: Collection used when a search does not override it explicitly.
        self.collection = collection or self.DEFAULT_COLLECTION
        #: Mirrors ``PlanetaryComputerClient.last_search_truncated`` after
        #: the most recent :meth:`search` — read by the dialog to warn the
        #: user when the result set was capped by the search limit.
        self.last_search_truncated: bool = False

    # ------------------------------------------------------------------
    # BaseSARSource implementation
    # ------------------------------------------------------------------
    def search(
        self,
        bbox: Tuple[float, float, float, float],
        start_date: datetime,
        end_date: datetime,
        polarization: str = "VV+VH",
        orbit: str = "Any",
        progress_cb: ProgressCallback = None,
        cancel_cb: CancelCallback = None,
        collection: Optional[str] = None,
    ) -> List[Scene]:
        """Search for Sentinel-1 scenes matching the criteria.

        :param collection: STAC collection to query —
            ``"sentinel-1-grd"`` (default) or ``"sentinel-1-rtc"``
            (terrain-corrected gamma0). See the class docstring for when
            each product is appropriate.

        Polarisation and orbit direction are applied as **post-filters**
        on the returned items, which is more robust across STAC API
        versions than relying on the ``query`` parameter (whose syntax
        varies between STAC API extensions).
        """
        if progress_cb:
            progress_cb(0)

        # Resolve which collection to query (None -> instance default).
        collection = collection or self.collection

        # Build STAC datetime range: "YYYY-MM-DDTHH:MM:SSZ/YYYY-MM-DDTHH:MM:SSZ"
        start_iso = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        datetime_range = f"{start_iso}/{end_iso}"

        try:
            items = self._client.search_items(
                collection=collection,
                bbox=bbox,
                datetime_range=datetime_range,
                limit=500,
            )
        except PCError as exc:
            raise RuntimeError(f"STAC search failed: {exc}") from exc

        # Propagate the client's "result set was capped" flag so the UI
        # can warn the user instead of silently showing fewer scenes than
        # actually matched the query.
        self.last_search_truncated = bool(
            getattr(self._client, "last_search_truncated", False)
        )

        log_info(
            f"STAC returned {len(items)} raw items for collection={collection}, "
            f"bbox={bbox}, "
            f"datetime={datetime_range}, pol={polarization}, orbit={orbit}"
        )

        if progress_cb:
            progress_cb(50)

        # Post-filter by polarisation and orbit direction.
        wanted_pols = self._parse_polarization_filter(polarization)
        wanted_orbit = self._parse_orbit_filter(orbit)

        # Sentinel-1 GRD on Planetary Computer uses the following STAC
        # property names (verified against the live API, August 2026):
        #   * ``sar:polarizations``    — list of strings, e.g. ["VV", "VH"]
        #   * ``sat:orbit_state``      — "ascending" / "descending"
        #   * ``sat:relative_orbit``   — integer
        # Older versions of the collection exposed ``polarizations`` /
        # ``orbit_direction`` / ``relative_orbit`` without the prefix —
        # we accept both for backwards compatibility.
        scenes: List[Scene] = []
        seen_ids: set = set()
        skipped_no_pol = 0
        skipped_pol_mismatch = 0
        skipped_orbit_mismatch = 0
        skipped_duplicate = 0
        for item in items:
            if cancel_cb and cancel_cb():
                raise OperationCancelled("Search cancelled by user")

            item_id = str(item.get("id", ""))
            # The Planetary Computer STAC API sometimes returns the same
            # scene twice (collection-level + product-level record).
            # Deduplicate by id to keep the UI clean.
            if item_id and item_id in seen_ids:
                skipped_duplicate += 1
                continue
            if item_id:
                seen_ids.add(item_id)

            props = item.get("properties", {}) or {}
            item_pols_raw = (
                props.get("sar:polarizations")
                or props.get("polarizations")
                or []
            )
            item_pols = tuple(str(p).upper() for p in item_pols_raw)
            item_orbit = str(
                props.get("sat:orbit_state")
                or props.get("orbit_direction")
                or ""
            ).lower()
            item_rel_orbit = (
                props.get("sat:relative_orbit")
                if props.get("sat:relative_orbit") is not None
                else props.get("relative_orbit")
            )

            # Polarisation filter
            if wanted_pols:
                if not item_pols:
                    # Scene has no listed polarisations — keep it (avoid
                    # silently dropping everything if the API changes).
                    skipped_no_pol += 1
                    pass
                elif not set(item_pols).issuperset(wanted_pols):
                    skipped_pol_mismatch += 1
                    continue
            # Orbit filter
            if wanted_orbit and item_orbit and item_orbit != wanted_orbit:
                skipped_orbit_mismatch += 1
                continue

            # Extract assets dict: asset name -> signed href
            assets: Dict[str, str] = {}
            preview_url: Optional[str] = None
            thumbnail_url: Optional[str] = None
            for asset_key, asset in (item.get("assets") or {}).items():
                href = asset.get("href")
                if not href:
                    continue
                try:
                    signed_href = self._client.sign_href(href)
                except PCError as exc:
                    log_warning(
                        f"Failed to sign asset {asset_key} of {item.get('id')}: {exc}"
                    )
                    signed_href = href  # fall back to unsigned (may 403)
                key_lower = asset_key.lower()
                if key_lower in _PREVIEW_ASSET_KEYS:
                    # Prefer rendered_preview > thumbnail > others
                    if key_lower == "rendered_preview" and not preview_url:
                        preview_url = signed_href
                    elif key_lower == "thumbnail" and not thumbnail_url:
                        thumbnail_url = signed_href
                    elif not preview_url:
                        preview_url = signed_href
                    continue  # don't put previews in the regular assets dict
                assets[asset_key] = signed_href

            # Parse acquisition datetime.
            acq_dt = self._parse_datetime(props.get("datetime") or item.get("datetime"))

            scene = Scene(
                id=str(item.get("id", "")),
                datetime=acq_dt,
                platform=str(props.get("platform", "sentinel-1")),
                polarizations=item_pols,
                orbit_direction=item_orbit,
                relative_orbit=item_rel_orbit,
                bbox=tuple(item.get("bbox") or ()),  # type: ignore[arg-type]
                collection=str(item.get("collection") or collection),
                assets=assets,
                preview_url=preview_url,
                thumbnail_url=thumbnail_url,
            )
            scenes.append(scene)

        if progress_cb:
            progress_cb(100)

        log_info(
            f"Search post-filter: kept {len(scenes)}/{len(items)} scenes "
            f"(skipped: {skipped_duplicate} duplicates, "
            f"{skipped_pol_mismatch} pol-mismatch, "
            f"{skipped_orbit_mismatch} orbit-mismatch, "
            f"{skipped_no_pol} no-pol-info)"
        )

        return scenes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_polarization_filter(polarization: str) -> set:
        """Convert UI string ("VV", "VH", "VV+VH") into a set of pols."""
        if not polarization:
            return set()
        if polarization == "VV+VH":
            return {"VV", "VH"}
        return {polarization}

    @staticmethod
    def _parse_orbit_filter(orbit: str) -> str:
        """Convert UI string ("Any", "Ascending", "Descending") into the
        lowercase form used by the Planetary Computer STAC property."""
        if not orbit or orbit == "Any":
            return ""
        return orbit.lower()

    @staticmethod
    def _parse_datetime(value) -> datetime:
        """Parse a STAC ``datetime`` string into a :class:`datetime`.

        Returns ``datetime.utcnow()`` as a last-resort fallback if the
        value is missing or unparseable, so we never raise from inside
        the search loop.
        """
        if not value:
            return datetime.utcnow()
        if isinstance(value, datetime):
            return value
        try:
            # STAC uses ISO-8601 with a trailing ``Z``. Python's
            # ``fromisoformat`` (3.11+) accepts ``Z``; for older
            # versions we strip it.
            text = str(value)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text)
        except Exception:
            try:
                return datetime.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                return datetime.utcnow()

    def download(
        self,
        scene: Scene,
        dest_dir: str,
        progress_cb: ProgressCallback = None,
        cancel_cb: CancelCallback = None,
    ) -> List[str]:
        """Download all assets of a scene to ``dest_dir``.

        Returns list of absolute file paths written.

        Per-asset byte-level progress is aggregated into a 0..100 range
        and reported via ``progress_cb``. Each asset's contribution to
        the total is weighted by its ``Content-Length`` when available,
        falling back to equal-share weighting otherwise.
        """
        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir, exist_ok=True)

        downloaded: List[str] = []
        asset_list = list(scene.assets.items())

        # Keep only the SAR measurement bands (vv/vh/hh/hv). RTC items also
        # carry auxiliary rasters (layover-shadow mask, incidence angles)
        # and GRD items carry calibration/noise schemas — nobody asked to
        # spend bandwidth on those. If nothing matches (unknown schema),
        # download everything as before rather than nothing at all.
        measurement = [
            (name, href) for name, href in asset_list
            if name.lower() in _MEASUREMENT_ASSET_KEYS
        ]
        if measurement:
            asset_list = measurement

        total_assets = max(1, len(asset_list))

        # ---- First pass: HEAD each asset to learn Content-Length. ----
        # If HEAD fails or server doesn't return Content-Length, fall back
        # to equal-share weighting (each asset = 1/N of total progress).
        sizes: List[int] = []
        for asset_name, href in asset_list:
            size = self._head_content_length(href)
            sizes.append(size)
        known_total = sum(s for s in sizes if s > 0)
        unknown_count = sum(1 for s in sizes if s <= 0)
        # Reserve 10% of progress for unknown-size assets (split equally).
        if known_total > 0 and unknown_count > 0:
            unknown_share = max(known_total // 10, 1024 * 1024)  # 1 MB min
            effective_total = known_total + unknown_share * unknown_count
        elif known_total > 0:
            effective_total = known_total
        else:
            # All unknown — equal share.
            effective_total = total_assets

        cumulative = 0
        for idx, (asset_name, href) in enumerate(asset_list, start=1):
            if cancel_cb and cancel_cb():
                raise OperationCancelled(
                    f"Download cancelled at asset {asset_name}"
                )

            # Determine output filename: use scene id + asset name + .tif.
            # NOTE: ``scene.id`` comes from the Planetary Computer STAC
            # response, not from user input, so path traversal is not a
            # realistic threat here. The character strip below is defence
            # in depth for Windows-illegal characters — and since it also
            # replaces '/' and '\', any accidental '..\'-style sequence in
            # a future id format is neutralised as well.
            safe_name = f"{scene.id}_{asset_name}.tif"
            safe_name = "".join(
                c if c not in '<>:"/\\|?*' else "_" for c in safe_name
            )
            out_path = os.path.join(dest_dir, safe_name)

            this_size = sizes[idx - 1]
            this_share = (
                this_size if this_size > 0
                else (effective_total // total_assets if effective_total > 0 else 0)
            )

            def _byte_cb(done_bytes: int, total_bytes: int) -> None:
                if not progress_cb:
                    return
                if total_bytes > 0:
                    cur = cumulative + min(done_bytes, total_bytes)
                else:
                    cur = cumulative + done_bytes
                pct = int((cur / max(1, effective_total)) * 100) if effective_total > 0 else 0
                pct = max(0, min(100, pct))
                try:
                    progress_cb(pct)
                except Exception:
                    pass

            try:
                self._http_download(
                    href, out_path,
                    byte_progress_cb=_byte_cb,
                    cancel_cb=cancel_cb,
                )
            except Exception as exc:  # pragma: no cover
                raise RuntimeError(
                    f"Failed to download asset {asset_name}: {exc}"
                ) from exc

            downloaded.append(out_path)
            cumulative += this_share
            if progress_cb:
                pct = int((cumulative / max(1, effective_total)) * 100) if effective_total > 0 else int(idx / total_assets * 100)
                pct = max(0, min(100, pct))
                try:
                    progress_cb(pct)
                except Exception:
                    pass

        return downloaded

    @staticmethod
    def _head_content_length(url: str, timeout: float = 30.0) -> int:
        """Issue a HEAD request and return Content-Length, or -1 if unknown.

        Uses urllib so no new dependency is needed. Failures are silently
        mapped to -1 (caller will fall back to equal-share weighting).
        """
        try:
            req = urllib.request.Request(
                url, method="HEAD",
                headers={"User-Agent": _REQUEST_USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cl = resp.headers.get("Content-Length")
                if cl:
                    try:
                        return int(cl)
                    except ValueError:
                        return -1
        except Exception:
            pass
        return -1

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _http_download(
        url: str,
        dest_path: str,
        byte_progress_cb: Optional[Callable[[int, int], None]] = None,
        cancel_cb: CancelCallback = None,
        max_attempts: int = 3,
    ) -> None:
        """Stream ``url`` to ``dest_path`` with retry and resume.

        Compared to a naive single-shot download this adds:

        * **Retry with backoff** — transient failures (HTTP 429/5xx on the
          initial GET, connection resets, read timeouts mid-stream) are
          retried up to ``max_attempts`` times. Sentinel-1 GRD assets are
          100–500 MB; a network blip at 95 % used to throw the whole
          transfer away.
        * **Resume via ``Range``** — when ``dest_path`` already holds a
          partial file (an interrupted attempt here or a previous plugin
          session), the next attempt asks the server for
          ``Range: bytes=N-`` and appends instead of starting over.
          Azure blob storage (which backs Planetary Computer) honours
          Range requests.
        * **Completeness check** — when the server advertises
          ``Content-Length``, the final file size is validated; a short
          file counts as a failed attempt and triggers another one.

        A server that ignores ``Range`` answers ``200`` with the full
        body — we then truncate and start from scratch, which is always
        correct. A ``416`` answer means the local file is already at
        least as large as the remote object: verified with HEAD and
        treated as "already downloaded" on a size match.

        Parameters
        ----------
        byte_progress_cb
            Optional callable invoked after every chunk with
            ``(bytes_downloaded, total_bytes)`` in absolute bytes from
            the start of the file. ``total_bytes`` is -1 if the server
            did not advertise Content-Length.
        cancel_cb
            Optional callable returning True to abort the download.
        """
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                delay = float(min(2 ** (attempt - 1), 30))  # 2s, 4s, … cap 30s
                log_warning(
                    f"Download of '{os.path.basename(dest_path)}' attempt "
                    f"{attempt}/{max_attempts} in {delay:.0f} s "
                    f"(previous error: {last_exc})"
                )
                _sleep_interruptible(delay, cancel_cb)
            try:
                PlanetaryComputerSource._download_attempt(
                    url, dest_path, byte_progress_cb, cancel_cb
                )
                return
            except OperationCancelled:
                # Cancellation is a user decision, not a failure — leave
                # the partial file in place so the next run can resume.
                raise
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(
            f"Failed to download {url} after {max_attempts} attempt(s); "
            f"last error: {last_exc}"
        ) from last_exc

    @staticmethod
    def _download_attempt(
        url: str,
        dest_path: str,
        byte_progress_cb: Optional[Callable[[int, int], None]] = None,
        cancel_cb: CancelCallback = None,
    ) -> None:
        """One full streaming GET (with Range resume). Raises on failure."""
        existing = os.path.getsize(dest_path) if os.path.isfile(dest_path) else 0
        headers = {"User-Agent": _REQUEST_USER_AGENT}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"

        req = urllib.request.Request(url, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing > 0:
                # "Range Not Satisfiable": the local file is already at
                # least as long as the remote object. Verify with HEAD;
                # a size match means it was already fully downloaded.
                remote_size = PlanetaryComputerSource._head_content_length(url)
                if remote_size > 0 and existing >= remote_size:
                    return
                # Stale leftover from a different/older asset — remove so
                # the next attempt starts clean.
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
            raise

        with response:
            status = getattr(response, "status", 200) or 200
            if status == 206:
                # Server honoured the Range request — append to what we have.
                mode, base = "ab", existing
            else:
                # Full-body answer (fresh start, or server ignored Range).
                mode, base = "wb", 0
            content_length = int(response.headers.get("Content-Length", -1) or -1)
            total_bytes = (
                base + content_length if content_length >= 0 else -1
            )

            chunk_size = 1024 * 64  # 64 KB — keeps memory flat
            downloaded = base
            with open(dest_path, mode) as out_file:
                while True:
                    if cancel_cb and cancel_cb():
                        raise OperationCancelled("Download cancelled by user")
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    if byte_progress_cb:
                        try:
                            byte_progress_cb(downloaded, total_bytes)
                        except Exception:
                            # Progress callback must never break the download.
                            pass

            # Completeness check: EOF before Content-Length bytes arrived.
            if content_length >= 0 and downloaded - base < content_length:
                raise IOError(
                    f"Incomplete download of '{dest_path}': received "
                    f"{downloaded - base} of {content_length} expected bytes"
                )
