"""Unit tests for the Planetary Computer HTTP client.

Covers the retry/backoff logic, the "search results were capped" flag
and the Range-resume download path — all against fake ``urlopen``
responses, so no network is touched.
"""

import email.message
import io
import json
import urllib.error

import pytest

from sentinel1_windthrow_plugin.sources import pc_client as pc_module
from sentinel1_windthrow_plugin.sources.planetary_computer import PlanetaryComputerSource
from sentinel1_windthrow_plugin.sources.pc_client import PCError, PlanetaryComputerClient


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------
class FakeJsonResponse:
    """Context-manager response returning one JSON payload."""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")
        self.headers = {}
        self.status = 200

    def read(self, size=-1):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeStreamResponse:
    """Context-manager response streaming bytes chunk by chunk."""

    def __init__(self, data: bytes, status: int = 200,
                 content_length=None, chunk=64 * 1024):
        self._data = data
        self._pos = 0
        self.status = status
        length = len(data) if content_length is None else content_length
        self.headers = {"Content-Length": str(length)}
        self._chunk = chunk

    def read(self, size=-1):
        step = self._chunk if size in (-1, None) else size
        chunk = self._data[self._pos:self._pos + step]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def make_http_error(code: int, retry_after=None) -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("http://fake", code, "err", hdrs,
                                  io.BytesIO(b""))


@pytest.fixture
def recorded_sleeps(monkeypatch):
    sleeps = []
    monkeypatch.setattr(pc_module.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def install_urlopen(monkeypatch, handler):
    monkeypatch.setattr(pc_module.urllib.request, "urlopen", handler)


# ----------------------------------------------------------------------
# Retry / backoff on JSON endpoints
# ----------------------------------------------------------------------
class TestHttpRetry:
    def test_get_retries_on_503_then_succeeds(self, monkeypatch, recorded_sleeps):
        calls = []

        def handler(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) < 3:
                raise make_http_error(503)
            return FakeJsonResponse({"ok": True})

        install_urlopen(monkeypatch, handler)
        client = PlanetaryComputerClient(max_attempts=3)
        assert client._http_get_json("http://fake") == {"ok": True}
        assert len(calls) == 3
        # Exponential backoff: 2 s after the first failure, 4 s after the second.
        assert recorded_sleeps == [2.0, 4.0]

    def test_post_retries_on_429(self, monkeypatch, recorded_sleeps):
        calls = []

        def handler(req, timeout=None):
            calls.append(1)
            if len(calls) < 2:
                raise make_http_error(429)
            return FakeJsonResponse({"features": []})

        install_urlopen(monkeypatch, handler)
        client = PlanetaryComputerClient(max_attempts=3)
        assert client._http_post_json("http://fake", {}) == {"features": []}
        assert len(calls) == 2
        assert recorded_sleeps == [2.0]

    def test_no_retry_on_permanent_404(self, monkeypatch, recorded_sleeps):
        def handler(req, timeout=None):
            raise make_http_error(404)

        install_urlopen(monkeypatch, handler)
        client = PlanetaryComputerClient(max_attempts=3)
        with pytest.raises(PCError, match="404"):
            client._http_get_json("http://fake")
        assert recorded_sleeps == []  # fatal statuses fail immediately

    def test_exhausted_attempts_raise_pcerror(self, monkeypatch, recorded_sleeps):
        def handler(req, timeout=None):
            raise make_http_error(503)

        install_urlopen(monkeypatch, handler)
        client = PlanetaryComputerClient(max_attempts=3)
        with pytest.raises(PCError, match="after 3 attempt"):
            client._http_get_json("http://fake")
        assert len(recorded_sleeps) == 2  # sleeps between the 3 attempts

    def test_retry_after_header_wins(self, monkeypatch, recorded_sleeps):
        state = {"n": 0}

        def handler(req, timeout=None):
            state["n"] += 1
            if state["n"] < 2:
                raise make_http_error(429, retry_after=7)
            return FakeJsonResponse({})

        install_urlopen(monkeypatch, handler)
        PlanetaryComputerClient(max_attempts=2)._http_get_json("http://fake")
        assert recorded_sleeps == [7.0]

    def test_network_error_is_retried(self, monkeypatch, recorded_sleeps):
        import urllib.error as ue

        state = {"n": 0}

        def handler(req, timeout=None):
            state["n"] += 1
            if state["n"] < 2:
                raise ue.URLError("connection reset")
            return FakeJsonResponse({"fine": 1})

        install_urlopen(monkeypatch, handler)
        assert PlanetaryComputerClient(max_attempts=2)._http_get_json(
            "http://fake"
        ) == {"fine": 1}


# ----------------------------------------------------------------------
# Search truncation flag
# ----------------------------------------------------------------------
def _item(item_id):
    return {
        "id": item_id,
        "properties": {"sar:polarizations": ["VV", "VH"]},
        "assets": {},
    }


class TestSearchTruncationFlag:
    def _install_pages(self, monkeypatch, post_payload, get_pages):
        def handler(req, timeout=None):
            if req.get_method() == "POST":
                return FakeJsonResponse(post_payload)
            return FakeJsonResponse(get_pages[req.full_url])

        install_urlopen(monkeypatch, handler)

    def test_flag_true_when_limit_cut_a_larger_result_set(
        self, monkeypatch, recorded_sleeps
    ):
        self._install_pages(
            monkeypatch,
            post_payload={
                "features": [_item("f1"), _item("f2")],
                "links": [{"rel": "next", "href": "http://p2"}],
            },
            get_pages={
                "http://p2": {
                    "features": [_item("f3"), _item("f4")],
                    "links": [{"rel": "next", "href": "http://p3"}],
                },
            },
        )
        client = PlanetaryComputerClient(max_attempts=1)
        items = client.search_items(collection="c", bbox=(0, 0, 1, 1), limit=3)
        assert [i["id"] for i in items] == ["f1", "f2", "f3"]
        assert client.last_search_truncated is True

    def test_flag_false_when_all_pages_fetched(self, monkeypatch, recorded_sleeps):
        self._install_pages(
            monkeypatch,
            post_payload={
                "features": [_item("f1")],
                "links": [{"rel": "next", "href": "http://p2"}],
            },
            get_pages={
                "http://p2": {"features": [_item("f2")], "links": []},
            },
        )
        client = PlanetaryComputerClient(max_attempts=1)
        items = client.search_items(collection="c", bbox=(0, 0, 1, 1), limit=500)
        assert len(items) == 2
        assert client.last_search_truncated is False

    def test_flag_false_for_single_page(self, monkeypatch, recorded_sleeps):
        self._install_pages(
            monkeypatch,
            post_payload={"features": [_item("f1")], "links": []},
            get_pages={},
        )
        client = PlanetaryComputerClient(max_attempts=1)
        client.search_items(collection="c", bbox=(0, 0, 1, 1), limit=500)
        assert client.last_search_truncated is False


# ----------------------------------------------------------------------
# Download resume / completeness (PlanetaryComputerSource._download_attempt)
# ----------------------------------------------------------------------
class TestDownloadResume:
    def test_resumes_from_partial_file(self, tmp_path, monkeypatch):
        full = b"A" * 200 + b"B" * 100  # 300-byte "asset"
        partial = tmp_path / "asset.tif"
        partial.write_bytes(full[:200])

        captured = {}

        def handler(req, timeout=None):
            captured["range"] = req.headers.get("Range")
            offset = 0
            range_hdr = req.headers.get("Range")
            if range_hdr:
                offset = int(range_hdr.split("=")[1].split("-")[0])
            return FakeStreamResponse(full[offset:], status=206 if offset else 200)

        install_urlopen(monkeypatch, handler)
        PlanetaryComputerSource._download_attempt("http://blob/x", str(partial))

        assert captured["range"] == "bytes=200-"
        assert partial.read_bytes() == full

    def test_incomplete_stream_raises_ioerror(self, tmp_path, monkeypatch):
        target = tmp_path / "new.tif"

        def handler(req, timeout=None):
            # Server promises 300 bytes but the stream dies after 10.
            return FakeStreamResponse(b"x" * 10, content_length=300)

        install_urlopen(monkeypatch, handler)
        with pytest.raises(IOError, match="Incomplete"):
            PlanetaryComputerSource._download_attempt(
                "http://blob/x", str(target)
            )

    def test_416_with_matching_size_means_already_downloaded(
        self, tmp_path, monkeypatch
    ):
        complete = tmp_path / "done.tif"
        complete.write_bytes(b"Z" * 128)

        def handler(req, timeout=None):
            raise make_http_error(416)

        install_urlopen(monkeypatch, handler)
        monkeypatch.setattr(
            PlanetaryComputerSource,
            "_head_content_length",
            staticmethod(lambda url, timeout=30.0: 128),
        )
        # Must NOT raise and must NOT touch the file.
        PlanetaryComputerSource._download_attempt(
            "http://blob/x", str(complete)
        )
        assert complete.read_bytes() == b"Z" * 128

    def test_http_download_wraps_failures_after_max_attempts(
        self, tmp_path, monkeypatch, recorded_sleeps
    ):
        def handler(req, timeout=None):
            raise make_http_error(500)

        install_urlopen(monkeypatch, handler)
        with pytest.raises(RuntimeError, match="after 2 attempt"):
            PlanetaryComputerSource._http_download(
                "http://blob/x",
                str(tmp_path / "out.tif"),
                max_attempts=2,
            )

    def test_cancellation_is_not_swallowed_by_retry(
        self, tmp_path, monkeypatch, recorded_sleeps
    ):
        from sentinel1_windthrow_plugin.sources.base import OperationCancelled

        def handler(req, timeout=None):
            raise make_http_error(503)

        install_urlopen(monkeypatch, handler)

        def cancel():
            return True

        with pytest.raises(OperationCancelled):
            PlanetaryComputerSource._http_download(
                "http://blob/x",
                str(tmp_path / "out.tif"),
                max_attempts=3,
                cancel_cb=cancel,
            )


# ----------------------------------------------------------------------
# Logging hook wiring
# ----------------------------------------------------------------------
class TestLogHook:
    def test_warn_goes_through_hook(self):
        seen = []
        old = pc_module.log_hook
        pc_module.log_hook = seen.append
        try:
            pc_module._warn("hello")
        finally:
            pc_module.log_hook = old
        assert seen == ["hello"]

    def test_package_installs_qgis_log_hook(self):
        # sources/__init__ wires pc_client warnings into logger.log_warning.
        from sentinel1_windthrow_plugin.sources import pc_client as wired_client

        assert wired_client.log_hook is not None
