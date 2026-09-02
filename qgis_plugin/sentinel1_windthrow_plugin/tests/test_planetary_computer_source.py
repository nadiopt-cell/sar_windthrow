"""Unit tests for ``PlanetaryComputerSource`` (search/download wrapper).

Covers the GRD/RTC product-selection behaviour added in v0.5.0:

* the requested STAC collection reaches ``client.search_items``;
* collection resolution order: explicit argument > instance default > GRD;
* ``Scene.collection`` falls back to the *requested* collection when the
  raw item lacks a ``collection`` field;
* downloads keep only the SAR measurement bands for RTC items (auxiliary
  rasters like layover/shadow masks are skipped);
* ``_guess_collection_from_href`` distinguishes RTC from GRD blob paths.

All tests run against fakes — no network, no QGIS.
"""

from datetime import datetime

import pytest

from sentinel1_windthrow_plugin.sources.planetary_computer import PlanetaryComputerSource
from sentinel1_windthrow_plugin.sources.pc_client import PlanetaryComputerClient
from sentinel1_windthrow_plugin.sources.base import Scene


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------
class RecordingClient:
    """Fake PC client recording search kwargs and returning canned items."""

    def __init__(self, items=None):
        self.items = items if items is not None else [_make_item()]
        self.calls = []
        self.last_search_truncated = False

    def search_items(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.items)

    def sign_href(self, href):
        return href  # no signing in tests


def _make_item(item_id="S1A_IW_GRDH_1SDV_20240101T060000", collection=None):
    """Build a minimal STAC item dict shaped like the real GRD/RTC ones."""
    item = {
        "id": item_id,
        "bbox": [10.0, 40.0, 11.0, 41.0],
        "properties": {
            "datetime": "2024-01-01T06:00:00Z",
            "platform": "sentinel-1",
            "sar:polarizations": ["VV", "VH"],
            "sat:orbit_state": "ascending",
            "sat:relative_orbit": 15,
        },
        "assets": {
            "vv": {"href": f"https://example.org/{item_id}_vv.tif"},
            "vh": {"href": f"https://example.org/{item_id}_vh.tif"},
        },
    }
    if collection is not None:
        item["collection"] = collection
    return item


def _make_scene(assets):
    return Scene(
        id="TEST_SCENE",
        datetime=datetime(2024, 1, 1),
        platform="sentinel-1",
        polarizations=("VV", "VH"),
        orbit_direction="ascending",
        relative_orbit=15,
        bbox=(10.0, 40.0, 11.0, 41.0),
        collection="sentinel-1-rtc",
        assets=dict(assets),
    )


def _search_kwargs():
    """Common positional arguments for :meth:`PlanetaryComputerSource.search`."""
    return dict(
        bbox=(10.0, 40.0, 11.0, 41.0),
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 31),
    )


# ----------------------------------------------------------------------
# Collection selection on search
# ----------------------------------------------------------------------
class TestSearchCollection:
    def test_default_collection_is_grd(self):
        client = RecordingClient()
        source = PlanetaryComputerSource(client=client)
        scenes = source.search(**_search_kwargs())

        assert len(scenes) == 1
        assert client.calls[0]["collection"] == "sentinel-1-grd"

    def test_explicit_rtc_overrides_default(self):
        client = RecordingClient(items=[_make_item()])
        source = PlanetaryComputerSource(client=client)
        source.search(collection="sentinel-1-rtc", **_search_kwargs())

        assert client.calls[0]["collection"] == "sentinel-1-rtc"

    def test_instance_default_can_be_rtc(self):
        client = RecordingClient()
        source = PlanetaryComputerSource(client=client, collection="sentinel-1-rtc")
        source.search(**_search_kwargs())

        assert client.calls[0]["collection"] == "sentinel-1-rtc"

    def test_explicit_argument_beats_instance_default(self):
        client = RecordingClient()
        source = PlanetaryComputerSource(client=client, collection="sentinel-1-rtc")
        source.search(collection="sentinel-1-grd", **_search_kwargs())

        assert client.calls[0]["collection"] == "sentinel-1-grd"

    def test_scene_collection_falls_back_to_requested(self):
        """Items without their own ``collection`` inherit the requested one."""
        client = RecordingClient(items=[_make_item(collection=None)])
        source = PlanetaryComputerSource(client=client)
        scenes = source.search(collection="sentinel-1-rtc", **_search_kwargs())

        assert scenes[0].collection == "sentinel-1-rtc"

    def test_scene_collection_prefers_item_field(self):
        client = RecordingClient(items=[_make_item(collection="sentinel-1-grd")])
        source = PlanetaryComputerSource(client=client)
        scenes = source.search(collection="sentinel-1-rtc", **_search_kwargs())

        assert scenes[0].collection == "sentinel-1-grd"

    def test_truncation_flag_propagates(self):
        client = RecordingClient()
        client.last_search_truncated = True
        source = PlanetaryComputerSource(client=client)

        assert source.last_search_truncated is False
        source.search(**_search_kwargs())
        assert source.last_search_truncated is True


# ----------------------------------------------------------------------
# Measurement-band filter on download
# ----------------------------------------------------------------------
class TestDownloadMeasurementFilter:
    def _source_with_stub_downloads(self, written):
        source = PlanetaryComputerSource(client=RecordingClient())
        source._head_content_length = lambda url: 100

        def _fake_download(href, out_path, byte_progress_cb=None, cancel_cb=None):
            written.append(out_path)

        source._http_download = _fake_download
        return source

    def test_auxiliary_assets_are_skipped(self, tmp_path):
        written = []
        source = self._source_with_stub_downloads(written)
        scene = _make_scene({
            "vv": "https://example.org/vv.tif",
            "vh": "https://example.org/vh.tif",
            "layover_shadow_mask": "https://example.org/lsm.tif",
            "incidence_angles": "https://example.org/inc.tif",
            "noise_power_lut": "https://example.org/noise.xml",
        })

        downloaded = source.download(scene, str(tmp_path))

        names = {p.rsplit("_", 1)[-1] for p in downloaded}
        assert names == {"vv.tif", "vh.tif"}

    def test_unknown_schema_downloads_everything(self, tmp_path):
        """When no measurement band matches, fall back to all assets."""
        written = []
        source = self._source_with_stub_downloads(written)
        scene = _make_scene({
            "calibration": "https://example.org/cal.xml",
            "schema-noise": "https://example.org/noise.xml",
        })

        downloaded = source.download(scene, str(tmp_path))

        assert len(downloaded) == 2


# ----------------------------------------------------------------------
# Collection guessing from blob hrefs (SAS fallback path)
# ----------------------------------------------------------------------
class TestGuessCollectionFromHref:
    @pytest.mark.parametrize(
        "href,expected",
        [
            (
                "https://sentinel1euwest.blob.core.windows.net/sentinel-1-rtc/"
                "S1A_IW_RTC/S1A_vv.tif",
                "sentinel-1-rtc",
            ),
            (
                "https://sentinel1euwest.blob.core.windows.net/sentinel-1-grd/"
                "S1A_IW_GRDH_1SDV/S1A_vv.tif",
                "sentinel-1-grd",
            ),
            (
                "https://sentinel1euwest.blob.core.windows.net/grd-not-a-container/"
                "x.tif",
                "sentinel-1-grd",
            ),
            (
                "https://elevationeuwest.blob.core.windows.net/copernicus-dem-90/"
                "Copernicus_DSM_COG_10.tif",
                "cop-dem-glo-90",
            ),
            ("https://example.org/somewhere/else.tif", ""),
        ],
    )
    def test_guessing(self, href, expected):
        assert PlanetaryComputerClient._guess_collection_from_href(href) == expected
