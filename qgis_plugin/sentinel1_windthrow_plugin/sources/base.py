"""Base classes for SAR data sources.

Defines the ``Scene`` value object and the ``BaseSARSource`` abstract
interface every data provider must implement (SPEC "Ключевые классы").
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

#: Callbacks used to report progress (0..100) and cancellation while a
#: long operation runs inside a QgsTask.
ProgressCallback = Optional[Callable[[int], None]]
CancelCallback = Optional[Callable[[], bool]]


class OperationCancelled(Exception):
    """Raised when a long operation is cancelled by the user."""


@dataclass
class Scene:
    """A single SAR scene returned by a STAC search."""

    id: str
    datetime: datetime
    platform: str
    polarizations: Tuple[str, ...]
    orbit_direction: str
    relative_orbit: Optional[int]
    bbox: Tuple[float, float, float, float]
    collection: str
    #: Mapping of asset name (e.g. "vv", "vh") to a signed download URL.
    assets: Dict[str, str] = field(default_factory=dict)
    #: Optional signed URL to a small preview PNG/JPG (rendered_preview).
    #: Loaded asynchronously into the results-table thumbnail column.
    preview_url: Optional[str] = None
    #: Optional signed URL to a tiny thumbnail (~few KB).
    thumbnail_url: Optional[str] = None


class BaseSARSource(ABC):
    """Abstract source of SAR scenes.

    Implementations provide two operations: searching the catalog and
    downloading scene assets to a local folder.
    """

    @abstractmethod
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
        """Return scenes intersecting ``bbox`` within the date range.

        :param polarization: "VV", "VH" or "VV+VH".
        :param orbit: "Any", "Ascending" or "Descending".
        :param progress_cb: optional 0..100 progress reporter.
        :param cancel_cb: optional callable returning True to cancel.
        :param collection: provider-specific product/collection identifier
            (e.g. GRD vs RTC for Planetary Computer). ``None`` selects the
            provider's default collection.
        """

    @abstractmethod
    def download(
        self,
        scene: Scene,
        dest_dir: str,
        progress_cb: ProgressCallback = None,
        cancel_cb: CancelCallback = None,
    ) -> List[str]:
        """Download the scene assets into ``dest_dir``.

        :return: absolute paths of the written files.
        :raises OperationCancelled: when ``cancel_cb`` returns True.
        """
