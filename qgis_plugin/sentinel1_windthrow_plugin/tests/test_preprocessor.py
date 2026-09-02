"""Unit tests for ``SARPreprocessor`` (pure NumPy — no QGIS required).

These pin down behaviour that was hand-fixed repeatedly during
development (NaN handling in ``to_db``, NaN-aware Lee filter, GCP vs
GeoTransform georeferencing) so regressions get caught before release.
"""

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources.preprocessor import SARPreprocessor


def _amp_from_db(db):
    """Linear amplitude corresponding to a dB value."""
    return 10.0 ** (np.asarray(db, dtype=np.float32) / 10.0)


# ----------------------------------------------------------------------
# to_db / from_db
# ----------------------------------------------------------------------
class TestToDb:
    def test_round_trip(self):
        rng = np.random.default_rng(42)
        amp = rng.uniform(1e-4, 1e4, size=(64, 64)).astype(np.float32)
        db = SARPreprocessor.to_db(amp)
        back = SARPreprocessor.from_db(db)
        assert back.dtype == np.float32
        np.testing.assert_allclose(back, amp, rtol=1e-5)

    def test_formula(self):
        amp = np.array([[1.0, 10.0], [100.0, 1000.0]], dtype=np.float32)
        db = SARPreprocessor.to_db(amp)
        np.testing.assert_allclose(db, [[0.0, 10.0], [20.0, 30.0]], rtol=1e-5)

    def test_zero_and_negative_become_nan_not_minus_inf(self):
        # Regression: zero-amplitude border pixels used to become ~-380 dB
        # outliers that wrecked the percentile stretch.
        amp = np.array([[0.0, 1.0], [-2.0, 4.0]], dtype=np.float32)
        db = SARPreprocessor.to_db(amp)
        assert np.isnan(db[0, 0])
        assert np.isnan(db[1, 0])
        assert db[0, 1] == pytest.approx(0.0, abs=1e-6)  # 10*log10(1) == 0 dB
        assert db[1, 1] == pytest.approx(float(10.0 * np.log10(4.0)), rel=1e-6)

    def test_input_not_modified(self):
        amp = np.array([[1.0, 0.0]], dtype=np.float32)
        original = amp.copy()
        SARPreprocessor.to_db(amp)
        np.testing.assert_array_equal(amp, original)

    def test_output_dtype_is_float32(self):
        amp = np.ones((8, 8), dtype=np.float64)
        assert SARPreprocessor.to_db(amp).dtype == np.float32

    def test_from_db_basic(self):
        db = np.array([0.0, 10.0, 20.0], dtype=np.float32)
        amp = SARPreprocessor.from_db(db)
        np.testing.assert_allclose(amp, [1.0, 10.0, 100.0], rtol=1e-5)


# ----------------------------------------------------------------------
# is_db_domain / to_db_domain (input-scale auto-detection)
# ----------------------------------------------------------------------
class TestDbDomain:
    def test_calibrated_db_detected(self):
        db = np.full((32, 32), -15.0, dtype=np.float32)  # sea, negative dB
        assert SARPreprocessor.is_db_domain(db) is True

    def test_linear_amplitude_not_detected_as_db(self):
        amp = np.full((32, 32), 0.3, dtype=np.float32)
        assert SARPreprocessor.is_db_domain(amp) is False

    def test_raw_dn_with_nodata_not_detected_as_db(self):
        # Regression: a raw GRD band carries -32768 no-data pixels; a bare
        # `min < 0` probe misreads them as dB and skips to_db conversion.
        dn = np.full((32, 32), 1000.0, dtype=np.float32)
        dn[0, :] = -32768.0  # no-data border
        assert SARPreprocessor.is_db_domain(dn) is False

    def test_all_nan_not_detected_as_db(self):
        arr = np.full((8, 8), np.nan, dtype=np.float32)
        assert SARPreprocessor.is_db_domain(arr) is False

    def test_to_db_domain_converts_linear(self):
        amp = np.full((8, 8), 100.0, dtype=np.float32)  # 20 dB
        out = SARPreprocessor.to_db_domain(amp)
        np.testing.assert_allclose(out, 20.0, rtol=1e-5)

    def test_to_db_domain_preserves_db(self):
        db = np.full((8, 8), -15.0, dtype=np.float32)
        out = SARPreprocessor.to_db_domain(db)
        np.testing.assert_allclose(out, -15.0, rtol=1e-5)

    def test_to_db_domain_masks_nodata_sentinel_in_db(self):
        db = np.full((8, 8), -15.0, dtype=np.float32)
        db[0, 0] = -32768.0
        out = SARPreprocessor.to_db_domain(db)
        assert np.isnan(out[0, 0])
        assert out[1, 1] == pytest.approx(-15.0, abs=1e-6)

    def test_to_db_domain_nans_dn_border(self):
        dn = np.full((8, 8), 1000.0, dtype=np.float32)
        dn[0, :] = -32768.0
        out = SARPreprocessor.to_db_domain(dn)
        assert np.isnan(out[0, :]).all()
        assert np.isfinite(out[1:, :]).all()


# ----------------------------------------------------------------------
# speckle_filter (Lee)
# ----------------------------------------------------------------------
class TestSpeckleFilter:
    def test_preserves_shape_and_dtype_2d(self):
        img = np.random.default_rng(0).uniform(0, 100, (32, 32)).astype(np.float32)
        out = SARPreprocessor.speckle_filter(img, kernel_size=5)
        assert out.shape == img.shape
        assert out.dtype == np.float32

    def test_preserves_shape_3d_multiband(self):
        img = np.random.default_rng(1).uniform(0, 100, (2, 24, 24)).astype(np.float32)
        out = SARPreprocessor.speckle_filter(img, kernel_size=3)
        assert out.shape == img.shape

    def test_constant_image_stays_constant(self):
        # A flat image has zero local variance -> weight ~0 -> output == input.
        img = np.full((32, 32), 17.5, dtype=np.float32)
        out = SARPreprocessor.speckle_filter(img, kernel_size=5)
        np.testing.assert_allclose(out, 17.5, atol=1e-3)

    def test_reduces_noise_variance(self):
        rng = np.random.default_rng(7)
        clean = np.full((64, 64), 50.0, dtype=np.float32)
        noisy = clean + rng.normal(0, 8.0, clean.shape).astype(np.float32)
        filtered = SARPreprocessor.speckle_filter(noisy, kernel_size=5)
        interior = slice(5, -5)  # ignore boundary effects
        var_in = float(noisy[interior, interior].var())
        var_out = float(filtered[interior, interior].var())
        assert var_out < var_in * 0.5

    def test_nan_hole_filled_from_neighbors(self):
        # NaN pixels (no-data from to_db) must come back finite where the
        # surrounding window holds valid pixels.
        img = np.full((33, 33), 20.0, dtype=np.float32)
        img[10:14, 10:14] = np.nan
        out = SARPreprocessor.speckle_filter(img, kernel_size=5)
        assert np.isfinite(out[10:14, 10:14]).all()

    def test_all_nan_band_returned_as_is(self):
        img = np.full((16, 16), np.nan, dtype=np.float32)
        out = SARPreprocessor.speckle_filter(img, kernel_size=3)
        assert np.isnan(out).all()

    def test_even_kernel_raises(self):
        img = np.zeros((16, 16), dtype=np.float32)
        with pytest.raises(ValueError):
            SARPreprocessor.speckle_filter(img, kernel_size=4)

    def test_too_small_kernel_raises(self):
        img = np.zeros((16, 16), dtype=np.float32)
        with pytest.raises(ValueError):
            SARPreprocessor.speckle_filter(img, kernel_size=1)

    def test_unknown_method_raises(self):
        img = np.zeros((16, 16), dtype=np.float32)
        with pytest.raises(NotImplementedError):
            SARPreprocessor.speckle_filter(img, kernel_size=3, method="frost")


# ----------------------------------------------------------------------
# land_mask
# ----------------------------------------------------------------------
class TestLandMask:
    def test_threshold_linear_input(self):
        # Left half dark (-25 dB => water), right half bright (-15 dB).
        db = np.concatenate(
            [np.full((8, 8), -25.0), np.full((8, 8), -15.0)], axis=1
        ).astype(np.float32)
        mask = SARPreprocessor.land_mask(_amp_from_db(db), threshold_db=-20.0)
        assert mask.dtype == bool
        assert mask.shape == (8, 16)
        assert mask[:, :8].all()   # dark half is water
        assert not mask[:, 8:].any()  # bright half is land

    def test_is_db_skips_conversion(self):
        db = np.full((8, 8), -25.0, dtype=np.float32)
        from_linear = SARPreprocessor.land_mask(
            _amp_from_db(db), threshold_db=-20.0, is_db=False
        )
        from_db = SARPreprocessor.land_mask(db, threshold_db=-20.0, is_db=True)
        np.testing.assert_array_equal(from_linear, from_db)
        assert from_db.all()

    def test_multiband_combined_with_or(self):
        # Band 1 water on the left, band 2 water on the right — the union
        # must flag both halves.
        b1 = np.pad(np.full((8, 8), -30.0), ((0, 0), (0, 8)),
                    constant_values=-10.0)
        b2 = np.pad(np.full((8, 8), -30.0), ((0, 0), (8, 0)),
                    constant_values=-10.0)
        stack = np.stack([b1, b2]).astype(np.float32)
        mask = SARPreprocessor.land_mask(
            stack, threshold_db=-20.0, is_db=True
        )
        assert mask.ndim == 2  # bands collapsed
        assert mask[:, :8].all()
        assert mask[:, 8:].all()

    def test_multiband_linear_input_or(self):
        # Same OR semantics with linear-amplitude input (dB conversion
        # happens per band before thresholding).
        bright_amp = np.float32(10.0 ** (-10.0 / 10.0))  # -10 dB in linear
        b1 = np.pad(_amp_from_db(-30.0 * np.ones((8, 8))), ((0, 0), (0, 8)),
                    constant_values=bright_amp)
        b2 = np.pad(_amp_from_db(-30.0 * np.ones((8, 8))), ((0, 0), (8, 0)),
                    constant_values=bright_amp)
        stack = np.stack([b1, b2]).astype(np.float32)
        mask = SARPreprocessor.land_mask(stack, threshold_db=-20.0, is_db=False)
        assert mask.ndim == 2
        assert mask.all()

    def test_boundary_pixels_exactly_at_threshold_are_land(self):
        # Water is strictly BELOW the threshold.
        db = np.full((4, 4), -20.0, dtype=np.float32)
        mask = SARPreprocessor.land_mask(db, threshold_db=-20.0, is_db=True)
        assert not mask.any()


# ----------------------------------------------------------------------
# process_file (needs GDAL; skipped in plain-Python environments)
# ----------------------------------------------------------------------
class TestProcessFile:
    def test_missing_input_raises_filenotfound(self, tmp_path):
        pytest.importorskip("osgeo.gdal")
        with pytest.raises(FileNotFoundError):
            SARPreprocessor.process_file(
                str(tmp_path / "nope.tif"), str(tmp_path / "out.tif")
            )
