"""Scientifically explicit coherence hypotheses and reference-data evaluation.

The short-lived loss of coherence of a pair straddling a disturbance and
persistent changes between exclusively pre-event and post-event pairs are
different remote-sensing questions. They must not share an implicit sign.

This module only uses NumPy and is suitable for GDAL-free testing.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np

EVENT_DROP = "event_drop"
PERSISTENT_GAIN = "persistent_gain"
MODES = (EVENT_DROP, PERSISTENT_GAIN)


def coherence_score_chunk(target: np.ndarray, baseline: np.ndarray,
                          mode: str = EVENT_DROP) -> np.ndarray:
    """Return positive scores for the explicitly selected hypothesis.

    event_drop: baseline (comparable control pair) - cross-event pair;
    persistent_gain: post/post target pair - pre/pre baseline pair.
    A pair of coherence rasters is NOT sufficient to infer storm causality.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown coherence mode: {mode}")
    target = np.asarray(target, dtype=np.float64)
    baseline = np.asarray(baseline, dtype=np.float64)
    if target.shape != baseline.shape:
        raise ValueError("Coherence target and baseline shapes differ")
    with np.errstate(invalid="ignore"):
        return ((baseline - target) if mode == EVENT_DROP
                else (target - baseline))


def _auc_positive_greater(positive: np.ndarray,
                          negative: np.ndarray) -> float:
    """Rank AUC with half credit for tied scores, no sklearn dependency."""
    pos = np.asarray(positive, dtype=np.float64)
    neg = np.asarray(negative, dtype=np.float64)
    if not pos.size or not neg.size:
        raise ValueError("Both reference classes need valid observations")
    values = np.concatenate((pos, neg))
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos)+1)/2)
                 / (len(pos) * len(neg)))


def evaluate_reference_masks(score: np.ndarray,
                             damaged: np.ndarray,
                             intact: np.ndarray,
                             fpr_target: float = 0.05) -> dict:
    """Diagnostic discrimination, not an accuracy/causality certificate.

    Expects disjoint reference masks prepared independently of the SAR map.
    Statistical independence of pixels is NOT assumed. Report uncertainty
    using site/block-held-out validation, not naive per-pixel p values.
    """
    a = np.asarray(score, dtype=np.float64)
    d = np.asarray(damaged, dtype=bool)
    n = np.asarray(intact, dtype=bool)
    if not (a.shape == d.shape == n.shape):
        raise ValueError("Score and reference masks must have matching shapes")
    if np.any(d & n):
        raise ValueError("Damaged and intact reference masks overlap")
    if not 0 < fpr_target < 1:
        raise ValueError("fpr_target must be between 0 and 1")
    good = np.isfinite(a)
    pos, neg = a[d & good], a[n & good]
    if pos.size < 2 or neg.size < 2:
        raise ValueError("At least two valid pixels per class are required")
    # Use the next representable value to ensure the strict > comparison
    # excludes background values tied exactly at the FPR quantile.
    threshold = float(np.quantile(neg, 1.0 - fpr_target))
    return {
        "positive_count": int(pos.size),
        "negative_count": int(neg.size),
        "auc": _auc_positive_greater(pos, neg),
        "median_damaged": float(np.median(pos)),
        "median_intact": float(np.median(neg)),
        "median_excess": float(np.median(pos) - np.median(neg)),
        "threshold_at_target_fpr": threshold,
        "achieved_fpr": float(np.mean(neg > threshold)),
        "tpr_at_target_fpr": float(np.mean(pos > threshold)),
        "target_fpr": float(fpr_target),
        "limitations": ("Exploratory pixel statistics only; conduct spatially "
                        "independent, event-held-out validation and compare "
                        "with backscatter, seasonality and null baselines."),
    }
