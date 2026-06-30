"""
Patch boundary filtering for Condor Buildings Generator.

Filters buildings that fall outside the valid patch bounds.
Patch coordinates are centered at origin, with bounds [-2880, +2880] in X and Y.
"""

from typing import List, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

from ..models.geometry import Point2D, BBox, Polygon
from ..models.building import BuildingRecord
from ..config import PATCH_HALF

logger = logging.getLogger(__name__)


class FilterReason(Enum):
    """Reason for filtering a building."""
    OUTSIDE_BOUNDS = "outside_patch_bounds"
    PARTIAL_OUTSIDE = "partially_outside_patch"


@dataclass
class FilterResult:
    """Result of patch filtering."""
    kept: List[BuildingRecord]
    filtered: List[Tuple[str, FilterReason]]  # (osm_id, reason)


def filter_buildings_by_patch_bounds(
    buildings: List[BuildingRecord],
    bounds_half: float = PATCH_HALF,
    allow_partial: bool = False
) -> FilterResult:
    """
    Filter buildings that fall outside patch bounds.

    Patch bounds are [-bounds_half, +bounds_half] in both X and Y.
    Default is [-2880, +2880].

    Args:
        buildings: List of buildings to filter
        bounds_half: Half-size of patch (default 2880m)
        allow_partial: If True, keep buildings partially inside (not implemented yet)

    Returns:
        FilterResult with kept buildings and filtered IDs with reasons
    """
    kept = []
    filtered = []

    min_bound = -bounds_half
    max_bound = bounds_half

    for building in buildings:
        bbox = building.footprint.bbox

        # Check if ANY vertex is outside bounds
        is_outside = (
            bbox.min_x < min_bound or
            bbox.max_x > max_bound or
            bbox.min_y < min_bound or
            bbox.max_y > max_bound
        )

        if is_outside:
            # Determine if completely outside or partially
            is_completely_outside = (
                bbox.max_x < min_bound or
                bbox.min_x > max_bound or
                bbox.max_y < min_bound or
                bbox.min_y > max_bound
            )

            if is_completely_outside:
                reason = FilterReason.OUTSIDE_BOUNDS
            else:
                reason = FilterReason.PARTIAL_OUTSIDE

            filtered.append((building.osm_id, reason))
            logger.debug(
                f"Filtered building {building.osm_id}: {reason.value} "
                f"(bbox: [{bbox.min_x:.1f}, {bbox.min_y:.1f}] - "
                f"[{bbox.max_x:.1f}, {bbox.max_y:.1f}])"
            )
        else:
            kept.append(building)

    if filtered:
        logger.info(
            f"Filtered {len(filtered)} buildings outside patch bounds "
            f"(kept {len(kept)})"
        )

    return FilterResult(kept=kept, filtered=filtered)


def is_building_in_bounds(
    building: BuildingRecord,
    bounds_half: float = PATCH_HALF
) -> bool:
    """
    Check if a building is within patch bounds.

    Args:
        building: Building to check
        bounds_half: Half-size of patch

    Returns:
        True if building is completely within bounds
    """
    bbox = building.footprint.bbox

    return (
        bbox.min_x >= -bounds_half and
        bbox.max_x <= bounds_half and
        bbox.min_y >= -bounds_half and
        bbox.max_y <= bounds_half
    )


def get_patch_bounds_bbox(bounds_half: float = PATCH_HALF) -> BBox:
    """Get the patch bounds as a BBox."""
    return BBox(-bounds_half, -bounds_half, bounds_half, bounds_half)
