"""Microsoft Planetary Computer client — STAC + SAS, stdlib only.

Why this module exists
----------------------

The QGIS Python environment on Windows ships *without* ``pystac-client``
and there is no easy way for a user to ``pip install`` into the bundled
interpreter (it lives under ``C:\\PROGRA~1\\QGIS34~1.11\\apps\\Python312``
and is not on ``PATH``). Bundling the package inside the plugin folder
is fragile because ``pystac-client`` pulls in ``requests``, ``urllib3``,
``certifi`` and several other wheels that must match the QGIS Python
ABI.

The reference plugin ``dem_comparator`` solves this by talking to the
Planetary Computer STAC API directly with ``urllib.request`` + ``json``
from the standard library. We follow the same approach here so that the
Sentinel-1 plugin works on a clean QGIS install with **zero** extra
dependencies.

The client exposes three operations:

* :meth:`PlanetaryComputerClient.search_items` — POST to ``/search``
  with ``bbox`` + ``datetime`` and follow the ``next`` pagination link.
* :meth:`PlanetaryComputerClient.sign_href` — sign a blob URL via the
  ``/api/sas/v1/sign`` endpoint so the GeoTIFF download succeeds.
* :meth:`PlanetaryComputerClient.get_sas_token` — fetch a per-collection
  SAS token (used as a fallback when the sign endpoint is unreachable).

All HTTP calls use ``urllib.request`` from the standard library, so no
third-party package is required. Transient server-side failures
(HTTP 429 / 5xx, brief network drops) are retried automatically with
exponential backoff — PC endpoints occasionally answer 429/503 for a
few seconds and a single blind attempt made searches fail spuriously.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
#: Logging hook — assigned by the plugin package at import time so that
#: messages land in the QGIS log panel under the ``Sentinel1SAR`` tag.
#: Kept optional on purpose: this module must stay importable without a
#: QGIS runtime (unit tests), so when no hook is set warnings fall back
#: to ``stderr``.
log_hook: Optional[Callable[[str], None]] = None


def _warn(message: str) -> None:
    """Route a warning through ``log_hook`` when available, else stderr."""
    if log_hook is not None:
        try:
            log_hook(message)
            return
        except Exception:
            pass
    print(f"[Sentinel1SAR][WARNING] {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
#: HTTP statuses worth retrying: rate limiting plus transient gateway /
#: server errors. Anything else (auth, bad request, not found) is fatal
#: on the first attempt — retrying cannot help there.
_RETRYABLE_HTTP_CODES = frozenset({408, 429, 500, 502, 503, 504})

# Endpoint base URLs (current at time of writing — August 2026).
STAC_ROOT = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS_BASE = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
# Sign endpoint: takes any blob URL from a PC-hosted collection and returns
# a properly signed user-delegation SAS URL. Works for ALL collections, so
# we use it universally (some Sentinel-1 GRD assets return HTTP 403 without
# signing).
SIGN_ENDPOINT = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"


class PCError(Exception):
    """Raised when the PC catalogue or token endpoint fails."""


class PlanetaryComputerClient:
    """Lightweight PC STAC client using only the Python standard library.

    A single instance is meant to be reused across searches/downloads inside
    one plugin session. Token caches live on the instance, so repeated calls
    within a session reuse the same SAS tokens.
    """

    #: How long (seconds) a signed URL / SAS token is considered fresh.
    #: PC tokens typically live ~60 minutes; we refresh a bit earlier.
    _TOKEN_TTL_SEC = 50 * 60

    def __init__(
        self,
        token_cache_path: Optional[str] = None,
        timeout: int = 60,
        user_agent: str = "QGIS-Sentinel1-Plugin/0.5 (PC)",
        max_attempts: int = 3,
    ) -> None:
        self._token_cache: Dict[str, Tuple[str, float]] = {}
        self._token_cache_path = token_cache_path
        if token_cache_path:
            self._load_token_cache()
        # Cache of blob-href -> (signed_href, expiry_ts). The sign endpoint
        # is called per-asset; caching avoids re-hitting it for the same
        # tile within a run.
        self._signed_href_cache: Dict[str, Tuple[str, float]] = {}
        self.timeout = timeout
        self.user_agent = user_agent
        #: How many times each HTTP request is attempted before failing.
        self.max_attempts = max(1, int(max_attempts))
        #: Set by :meth:`search_items` — True when the catalogue had more
        #: matching scenes than ``limit`` allowed. The UI reads this to
        #: warn the user instead of silently showing a capped result set.
        self.last_search_truncated: bool = False

    # -------------------------------------------------------------- tokens
    def get_sas_token(self, collection: str) -> str:
        """Return a SAS token for ``collection`` (cached for ~50 minutes)."""
        cached = self._token_cache.get(collection)
        if cached is not None and cached[1] > time.time() + 60:
            return cached[0]
        url = f"{SAS_BASE}/{collection}"
        body = self._http_get_json(url)
        token = body.get("token")
        if not token:
            raise PCError(
                f"PC SAS endpoint returned no token for {collection}: {body!r}"
            )
        self._token_cache[collection] = (token, time.time() + self._TOKEN_TTL_SEC)
        self._save_token_cache()
        return token

    # ---------------------------------------------------------- STAC search
    def search_items(
        self,
        collection: str,
        bbox: Tuple[float, float, float, float],
        datetime_range: Optional[str] = None,
        limit: int = 500,
        extra_query: Optional[Dict[str, object]] = None,
    ) -> List[dict]:
        """Return raw STAC item dicts intersecting ``bbox`` (WGS84).

        :param datetime_range: STAC-style datetime filter,
            e.g. ``"2024-01-01T00:00:00Z/2024-02-01T00:00:00Z"``.
        :param limit: hard upper bound on the number of items returned
            (also used as the per-page ``limit`` request parameter).
        :param extra_query: optional STAC ``query`` extension payload.
        """
        xmin, ymin, xmax, ymax = bbox
        url = f"{STAC_ROOT}/search"
        body: Dict[str, object] = {
            "collections": [collection],
            "bbox": [xmin, ymin, xmax, ymax],
            "limit": min(limit, 1000),
        }
        if datetime_range:
            body["datetime"] = datetime_range
        if extra_query:
            body["query"] = extra_query

        self.last_search_truncated = False
        raw = self._http_post_json(url, body)
        items: List[dict] = list(raw.get("features", []))
        if not items:
            return []

        # PC uses cursor pagination via the ``next`` link — follow it
        # until exhausted or until we hit ``limit`` items.
        next_link = self._next_link(raw)
        while next_link and len(items) < limit:
            more = self._http_get_json(next_link)
            new_features = more.get("features", [])
            if not new_features:
                break
            items.extend(new_features)
            next_link = self._next_link(more)
            if len(items) >= limit:
                items = items[:limit]
                break

        # We stopped at ``limit`` while the catalogue still offered a
        # ``next`` page — the true result set is larger than what we
        # return. Flag it so callers can tell the user (a silently
        # capped search looks exactly like a complete one).
        self.last_search_truncated = bool(next_link) and len(items) >= limit
        return items

    @staticmethod
    def _next_link(payload: dict) -> Optional[str]:
        """Pull the ``links[rel=next].href`` (if any) from a STAC response."""
        for link in payload.get("links", []):
            if link.get("rel") == "next":
                href = link.get("href")
                if href:
                    return href
        return None

    # --------------------------------------------------------------- sign
    def sign_href(self, href: str) -> str:
        """Sign a PC blob URL so it can be downloaded anonymously.

        Tries the ``/api/sas/v1/sign`` endpoint first (the recommended
        way — it returns a user-delegation SAS that works for every
        collection). Falls back to appending the collection-level SAS
        token if the sign endpoint is unreachable.

        Idempotent: if ``href`` already looks signed (contains ``sig=``),
        it is returned unchanged.
        """
        # Already signed?
        if any(p in href for p in ("sig=", "st=", "se=")):
            return href

        cached = self._signed_href_cache.get(href)
        if cached is not None and cached[1] > time.time() + 60:
            return cached[0]

        # The sign endpoint expects the blob URL as a query parameter.
        quoted = urllib.parse.quote(href, safe="")
        url = f"{SIGN_ENDPOINT}?href={quoted}"
        try:
            body = self._http_get_json(url)
        except PCError as exc:
            # The sign endpoint is the primary path — if it fails the user
            # deserves to know *why* their download later 403s, not just
            # the bare HTTP status. Log and fall through to the fallback.
            _warn(f"SAS sign endpoint failed for {href}: {exc}")
            # Fallback: try the collection-level token approach.
            try:
                collection = self._guess_collection_from_href(href)
                if collection:
                    token = self.get_sas_token(collection)
                    _warn(
                        f"Using collection-level SAS token fallback "
                        f"for '{collection}' (sign endpoint unavailable)"
                    )
                    return self._append_token(href, token)
            except Exception as fallback_exc:
                _warn(f"SAS token fallback also failed for {href}: {fallback_exc}")
            # Last resort: return the unsigned URL — the caller will see
            # a 403 from the download and surface a clear error.
            _warn(f"Returning UNSIGNED href for {href} — download may fail with 403")
            return href

        signed = body.get("href") or body.get("signedHref") or body.get("url")
        if not signed:
            raise PCError(
                f"PC sign endpoint returned no signed href for {href}: {body!r}"
            )
        self._signed_href_cache[href] = (signed, time.time() + self._TOKEN_TTL_SEC)
        return signed

    @staticmethod
    def _append_token(href: str, token: str) -> str:
        """Append the SAS token to ``href`` (idempotent)."""
        if not token or token in href:
            return href
        sep = "&" if "?" in href else "?"
        return f"{href}{sep}{token}"

    @staticmethod
    def _guess_collection_from_href(href: str) -> str:
        """Best-effort guess of the PC collection name from a blob URL.

        Used only as a fallback when the sign endpoint is unreachable.
        Sentinel-1 GRD and RTC assets both live under
        ``sentinel1euwest.blob.core.windows.net`` — the more specific
        container-path prefixes are listed first so they win the match.
        """
        mapping = {
            # Specific container paths first (startswith match).
            "sentinel1euwest.blob.core.windows.net/sentinel-1-rtc": "sentinel-1-rtc",
            "elevationeuwest.blob.core.windows.net/copernicus-dem-90": "cop-dem-glo-90",
            "elevationeuwest.blob.core.windows.net/copernicus-dem": "cop-dem-glo-30",
            "nasademeuwest.blob.core.windows.net/nasadem-cog": "nasadem",
            "alosdem.blob.core.windows.net": "alos-dem",
            # Bare-host catch-all last.
            "sentinel1euwest.blob.core.windows.net": "sentinel-1-grd",
        }
        for prefix, coll in mapping.items():
            if href.startswith(f"https://{prefix}"):
                return coll
        return ""

    # --------------------------------------------------------------- HTTP
    def _open_with_retry(self, req: urllib.request.Request):
        """Open ``req`` retrying transient failures (429 / 5xx / network).

        Uses exponential backoff (2 s, 4 s, … capped at 60 s) and honours
        a numeric ``Retry-After`` header when the server sends one. A
        non-retryable HTTP status raises :class:`PCError` immediately;
        after ``max_attempts`` exhausted attempts a :class:`PCError`
        wrapping the last error is raised.
        """
        method = req.get_method()
        last_error: Optional[Exception] = None
        retry_after: Optional[str] = None

        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                delay = self._retry_delay(attempt - 1, retry_after)
                retry_after = None
                _warn(
                    f"{method} {req.full_url} failed ({last_error}); "
                    f"retrying in {delay:.0f} s "
                    f"(attempt {attempt}/{self.max_attempts})"
                )
                time.sleep(delay)
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRYABLE_HTTP_CODES:
                    raise PCError(
                        f"PC {method} {req.full_url} -> {exc.code} {exc.reason}"
                    ) from exc
                # Transient — remember and fall through to the next try.
                last_error = exc
                if exc.headers is not None:
                    retry_after = exc.headers.get("Retry-After")
            except (urllib.error.URLError, OSError) as exc:
                # Connection reset / DNS hiccup / timeout — worth retrying.
                last_error = exc

        raise PCError(
            f"PC {method} {req.full_url} failed after "
            f"{self.max_attempts} attempt(s): {last_error}"
        ) from last_error

    @staticmethod
    def _retry_delay(failed_attempt: int, retry_after: Optional[str]) -> float:
        """Backoff for the next retry after ``failed_attempt`` failures.

        Exponential (2, 4, 8 … capped at 60 s); a numeric ``Retry-After``
        header takes precedence when present.
        """
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), 60.0))
            except ValueError:
                pass  # HTTP-date form or garbage — use exponential backoff
        return min(float(2 ** max(1, failed_attempt)), 60.0)

    def _http_get_json(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with self._open_with_retry(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _http_post_json(self, url: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": self.user_agent,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self._open_with_retry(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ----------------------------------------------------- token persistence
    def _load_token_cache(self) -> None:
        if not self._token_cache_path or not os.path.exists(self._token_cache_path):
            return
        try:
            with open(self._token_cache_path, "r", encoding="utf-8") as fh:
                self._token_cache = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt cache is not fatal — we just lose the warm start.
            # Say so instead of silently pretending nothing happened.
            _warn(
                f"Discarding unreadable PC token cache "
                f"'{self._token_cache_path}': {exc}"
            )
            self._token_cache = {}

    def _save_token_cache(self) -> None:
        if not self._token_cache_path:
            return
        try:
            os.makedirs(os.path.dirname(self._token_cache_path) or ".", exist_ok=True)
            with open(self._token_cache_path, "w", encoding="utf-8") as fh:
                json.dump(self._token_cache, fh)
        except OSError as exc:
            # Non-fatal: tokens stay valid in memory for this session, but
            # the next session will have to re-request them.
            _warn(f"Could not save PC token cache to '{self._token_cache_path}': {exc}")


__all__ = ["PlanetaryComputerClient", "PCError"]
