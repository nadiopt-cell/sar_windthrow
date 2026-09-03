"""L-band windthrow detection by backscatter decline (PALSAR / PALSAR-2).

Physics (Tanase et al. 2018, "Mapping windthrows in boreal forests
using L-band SAR data", RSE 209:700-711): L-band waves penetrate the
canopy and interact with trunks and large branches.  When the canopy
is flattened, volume scattering collapses and the L-band backscatter
shows a pronounced DECLINE over damaged forest — the opposite sign of
the C-band WI (Rüetschi et al. 2019), where newly exposed, chaotically
oriented debris INCREASES the return.

Validated on two European Russia events of the 2017 season (project
sessions 02.09.2026, ALOS PALSAR annual mosaics, Planetary Computer):
    * ID666 (squall line, 950 ha): dHH invAUC 0.870 / dHV 0.732;
    * ID694 (tornado, 161 ha):    dHV invAUC 0.905 / dHH 0.733,
so both HH and HV carry the signal and HV tends to win on narrow,
high-contrast tracks.

``LbandDeclineDetector`` reuses the complete WindthrowDetector chain
(per-polarisation median composites -> image differencing -> adaptive
or fixed threshold -> median filter -> minimum-object cleanup ->
polygonisation) and only flips the difference sign, so the decline
index

    LDI = (HH_pre - HH_post) + (HV_pre - HV_post)      [dB]

is POSITIVE over windthrow, exactly like the C-band WI.  Everything
else (analysis/forest masks, background normalization, object size
filtering, output artefacts) behaves identically; the index raster is
named ``<base>_ldi.tif`` (``<base>_ldi_norm.tif`` with background
normalization).

PALSAR annual mosaics are delivered as DN; like the C-band chain, DN
are auto-converted to dB (10*log10(DN^2)).  The mosaic calibration
offset cancels in the differencing, so no per-product constant is
needed.
"""

from typing import List, Optional, Sequence

from .base import OperationCancelled  # noqa: F401  (re-export symmetry)
from .windthrow import WindthrowDetector
from ..logger import log_warning

#: Polarisation tokens carrying the L-band decline signal.
LBAND_POLS = ("hh", "hv")


class LbandDeclineDetector(WindthrowDetector):
    """L-band decline detector (Tanase et al. 2018 sign convention).

    Parameters are inherited from :class:`WindthrowDetector`; the
    threshold semantics are identical but act on the decline index
    LDI instead of WI:

    * ``threshold_mode="adaptive"`` (default): threshold =
      mean(LDI) + ``a_db``.  The offset ``a_db`` is expressed in dB
      like the paper's C-band ``a = 2.9``; for PALSAR annual mosaics
      (one pre / one post epoch, 12 months apart) practical values are
      smaller — start near 1.5-2.0 dB and adjust to the event.
    * ``threshold_mode="fixed"``: absolute LDI threshold in dB.

    :param polarizations: optional restriction of the channels used,
        e.g. ``["hv"]`` or ``["hh"]``.  Defaults to both L-band
        channels (``hh`` + ``hv``).  When none of the requested
        channels is present in the file names, the detector raises
        ``ValueError`` if the restriction was set explicitly, and
        falls back to every common channel with a warning otherwise
        (this also makes a C-band decline experiment possible without
        code changes).
    """

    def __init__(
        self,
        threshold_mode: str = "adaptive",
        a_db: float = 2.0,
        fixed_threshold_db: float = 2.0,
        min_pixels: int = 27,
        median_filter_size: int = 3,
        normalize_background: bool = True,
        polarizations: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__(
            threshold_mode=threshold_mode,
            a_db=a_db,
            fixed_threshold_db=fixed_threshold_db,
            min_pixels=min_pixels,
            median_filter_size=median_filter_size,
            normalize_background=normalize_background,
        )
        if polarizations is not None:
            pols = [str(p).lower() for p in polarizations if str(p)]
            if not pols:
                raise ValueError("polarizations must contain at least one channel")
            self.polarizations: Optional[List[str]] = pols
        else:
            self.polarizations = None

    # ------------------------------------------------------------------
    # WindthrowDetector hooks
    # ------------------------------------------------------------------
    def _delta_sign(self) -> float:
        return -1.0

    def _index_suffix(self) -> str:
        return "_ldi_norm" if self.normalize_background else "_ldi"

    def _restrict_polarizations(self, pols: List[str]) -> List[str]:
        allowed = self.polarizations if self.polarizations else list(LBAND_POLS)
        picked = [p for p in pols if p.lower() in allowed]
        if not picked:
            if self.polarizations is not None:
                raise ValueError(
                    "None of the requested L-band polarisations "
                    f"({', '.join(self.polarizations)}) was found in the "
                    f"file names (common channels: {', '.join(pols) or '—'})."
                )
            log_warning(
                "L-band decline: no HH/HV channel in the file names "
                f"(found {', '.join(pols) or '—'}); using them anyway — "
                "check that the inputs really are L-band data."
            )
            picked = pols
        return picked
