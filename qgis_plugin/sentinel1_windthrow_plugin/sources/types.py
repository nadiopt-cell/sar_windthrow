"""Structured result types shared by the detectors (v1.3.0 refactor).

``DetectionResult`` / ``ForestSafeguard`` are typed collectors for
detector outputs.  Field names mirror the legacy result-dict keys of
v1.2.2; :meth:`DetectionResult.to_dict` reproduces the legacy dict
shape exactly (same keys, same value types), so the QGIS dialog and
the test-suite keep working unchanged.

Both dataclasses implement a read-only Mapping view over ``to_dict()``
(``result["wi"]``, ``"dcoh" in result``, ``result.get(...)``), so
legacy dict-style consumers see no difference while new code can use
typed attributes.  The view is intentionally read-only: mutating a
result never made sense — detectors own their diagnostics.

This module is stdlib-only (no plugin imports) so detectors, the
pipeline and the GUI can all depend on it without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

__all__ = [
    "DetectionResult",
    "ForestSafeguard",
]


class _DictViewMixin:
    """Read-only Mapping view over :meth:`to_dict` (legacy dict access).

    Lets legacy consumers keep using ``result["key"]``,
    ``result.get("key", default)``, ``"key" in result`` and iteration
    over keys on top of the typed dataclasses.
    """

    def to_dict(self) -> Dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def keys(self):
        return self.to_dict().keys()

    def items(self):
        return self.to_dict().items()

    def values(self):
        return self.to_dict().values()


@dataclass
class ForestSafeguard(_DictViewMixin):
    """«вне_леса@Y» forest safeguard (v1.2.2, the article's ID654 case).

    A zone that was non-forest before the storm cannot produce a DiD
    windthrow signal: mean pre/post coherence above
    ``constants.COH_NONFOREST_MAX`` is the bare-ground signature and a
    forest share below ``constants.FOREST_SHARE_MIN`` puts the zone
    outside the applicability limit.  The run is NOT aborted — the flag
    is logged and surfaced in the GUI so the user can judge the mask
    semantics (a custom AOI mask is not a forest map).

    Field names = legacy keys of ``result["forest_safeguard"]``.
    """

    flagged: bool = False
    flags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    zone: str = "scene"  # "analysis" | "background" | "scene"
    coh_prepost_mean: Optional[float] = None
    forest_share: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Legacy dict shape of ``result["forest_safeguard"]``."""
        return {
            "flagged": self.flagged,
            "flags": list(self.flags),
            "reasons": list(self.reasons),
            "zone": self.zone,
            "coh_prepost_mean": self.coh_prepost_mean,
            "forest_share": self.forest_share,
        }


@dataclass
class DetectionResult(_DictViewMixin):
    """Typed collector for detector outputs of the three modes.

    Core fields are shared by every mode; mode-specific diagnostics
    travel in ``extras`` under their LEGACY key names, so ``to_dict()``
    reproduces the exact per-mode result dict of v1.2.2:

    * ``mode="wi"`` / ``"lband_decline"`` (WindthrowDetector /
      LbandDeclineDetector): ``wi``, ``mask``, ``vector``,
      ``threshold_db``, ``n_objects``, ``forest_mask`` + extras
      (``composites``, ``mean_wi``, ``offset_db``,
      ``normalize_background``);
    * ``mode="coh_delta"`` (CoherenceDeltaDetector): ``dcoh``, ``mask``,
      ``vector``, ``threshold``, ``n_objects``, ``background_mask``,
      ``forest_safeguard`` + extras (``mean_dcoh``, ``median_dcoh``,
      ``control_used``, ``water_mask_ignored``, ``pixel_size_m``,
      ``min_object_area_ha``, ``product_flavors``, ``product_info``).
    """

    mode: str  # "wi" | "lband_decline" | "coh_delta"
    index_path: str
    mask_path: str
    vector_path: str
    n_objects: int = 0
    threshold: Optional[float] = None
    forest_mask_path: Optional[str] = None
    background_mask_path: Optional[str] = None
    forest_safeguard: Optional[ForestSafeguard] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    # Legacy key of the float32 index raster / threshold per mode.
    _INDEX_KEY = {
        "wi": "wi",
        "lband_decline": "wi",
        "coh_delta": "dcoh",
    }
    _THRESHOLD_KEY = {
        "wi": "threshold_db",
        "lband_decline": "threshold_db",
        "coh_delta": "threshold",
    }

    def to_dict(self) -> Dict[str, Any]:
        """Legacy per-mode result dict (v1.2.2 shape, values identical)."""
        d: Dict[str, Any] = {self._INDEX_KEY[self.mode]: self.index_path}
        d["mask"] = self.mask_path
        d["vector"] = self.vector_path
        # Mode-specific diagnostics (legacy key names, legacy values).
        d.update(self.extras)
        d[self._THRESHOLD_KEY[self.mode]] = self.threshold
        d["n_objects"] = self.n_objects
        if self.mode == "coh_delta":
            d["background_mask"] = self.background_mask_path
            if self.forest_safeguard is not None:
                d["forest_safeguard"] = self.forest_safeguard.to_dict()
        else:
            d["forest_mask"] = self.forest_mask_path
        return d
