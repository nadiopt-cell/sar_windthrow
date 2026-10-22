"""Pure-numpy scientific contract tests, including counterexamples."""

import numpy as np
import pytest

from sentinel1_windthrow_plugin.sources.coherence_science import (
    EVENT_DROP, PERSISTENT_GAIN, coherence_score_chunk,
    evaluate_reference_masks,
)
from sentinel1_windthrow_plugin.sources.hyp3_utils import (
    parse_hyp3_product, _validate_did_pair, validate_temporal_pair,
)
from sentinel1_windthrow_plugin.sources.errors import ProductMismatchError
from sentinel1_windthrow_plugin.sources.raster_utils import _same_grid


def test_storm_crossing_pair_has_transient_decorrelation():
    target = np.array([[0.12, 0.19, np.nan]])
    control = np.array([[0.34, 0.20, 0.50]])
    s = coherence_score_chunk(target, control, EVENT_DROP)
    assert s[0, 0] == pytest.approx(0.22)
    assert s[0, 1] == pytest.approx(0.01)
    assert np.isnan(s[0, 2])


def test_post_event_coherence_gain_has_opposite_sign():
    postpost = np.array([[0.23, 0.14]])
    prepre = np.array([[0.12, 0.13]])
    assert coherence_score_chunk(postpost, prepre, PERSISTENT_GAIN)[0, 0] == pytest.approx(0.11)
    assert coherence_score_chunk(postpost, prepre, EVENT_DROP)[0, 0] == pytest.approx(-0.11)


def test_score_unknown_mode_or_mismatched_shape_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        coherence_score_chunk(np.ones(2), np.ones(2), "automatic")
    with pytest.raises(ValueError, match="shapes"):
        coherence_score_chunk(np.ones(2), np.ones(3))


def test_evaluation_perfect_separation_and_ties():
    scores = np.array([[0.9, 0.8, 0.1, 0.2],
                       [0.3, 0.3, 0.3, 0.3]])
    damaged = np.zeros_like(scores, dtype=bool)
    damaged[0, :2] = True
    intact = ~damaged
    metrics = evaluate_reference_masks(scores, damaged, intact)
    assert metrics["auc"] == pytest.approx(1)
    assert metrics["positive_count"] == 2
    assert metrics["negative_count"] == 6
    assert metrics["achieved_fpr"] <= 0.05


def test_evaluation_does_not_overstate_neutral_signal():
    scores = np.ones((2, 4)) * 0.15
    damaged = np.zeros_like(scores, dtype=bool)
    damaged[:, :2] = True
    intact = ~damaged
    metrics = evaluate_reference_masks(scores, damaged, intact)
    assert metrics["auc"] == pytest.approx(0.5)
    assert metrics["median_excess"] == pytest.approx(0)
    assert metrics["tpr_at_target_fpr"] == pytest.approx(0)


def test_evaluation_rejects_overlapping_reference_masks():
    with pytest.raises(ValueError, match="overlap"):
        evaluate_reference_masks(np.ones((3, 3)), np.ones((3, 3)),
                                 np.ones((3, 3)))


def test_persistent_gain_requires_baseline_before_target():
    target = {"date1": "20240122", "date2": "20240203"}
    earlier = {"date1": "20240101", "date2": "20240113"}
    assert validate_temporal_pair(target, earlier, PERSISTENT_GAIN) == []
    with pytest.raises(ProductMismatchError, match="strictly BEFORE"):
        validate_temporal_pair(earlier, target, PERSISTENT_GAIN)


def test_temporal_baseline_mismatch_warning():
    target = {"date1": "20240201", "date2": "20240213"}
    earlier = {"date1": "20240101", "date2": "20240107"}
    issues = validate_temporal_pair(target, earlier, PERSISTENT_GAIN)
    assert "baseline mismatch" in issues[0].lower()


def test_gamma_mismatched_orbit_rejected():
    a = parse_hyp3_product("S1AB_20171111T150004_20171117T145926_VVP006_INT80_G_ueF_4D09.zip")
    b = parse_hyp3_product("S1AB_20171010T150004_20171016T145926_VVP025_INT80_G_ueF_4D09.zip")
    assert a["orbit_code"] == "P006"
    with pytest.raises(ProductMismatchError, match="orbit"):
        _validate_did_pair(a, b)


def test_same_pixels_different_crs_is_not_same_grid():
    reference = {"width": 3, "height": 4,
                 "geotransform": (1, 2, 0, 9, 0, -2),
                 "projection": 'GEOGCS["WGS 84"]'}
    other = dict(reference, projection='PROJCS["UTM zone 33"]')
    assert not _same_grid(reference, other)


def test_invalid_coherence_border_does_not_produce_false_positive_halo():
    from sentinel1_windthrow_plugin.sources.numpy_ops import median_filter_nan
    from sentinel1_windthrow_plugin.sources.numpy_ops import mask_from_threshold
    tile = np.full((7, 7), np.nan, dtype=np.float32)
    tile[3, 3] = 0.1
    filtered = median_filter_nan(tile, 3)
    assert filtered[3, 3] == pytest.approx(0.1)
    assert np.isnan(filtered[2, 2])
    assert not mask_from_threshold(filtered, 0.25).any()
