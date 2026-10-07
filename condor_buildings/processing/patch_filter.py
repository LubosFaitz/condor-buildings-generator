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
    allow_partial: bool = False,
    patch_id=None,
    heightmaps_dir=None
) -> FilterResult:
    """
    Filter buildings that fall outside patch bounds.

    Patch bounds are [-bounds_half, +bounds_half] in both X and Y.
    Default is [-2880, +2880].

    Args:
        buildings: List of buildings to filter
        bounds_half: Half-size of patch (default 2880m)
        allow_partial: If True, keep buildings partially inside (not implemented yet)
        patch_id, heightmaps_dir: to tell whether the neighbour exists (see owns_point)

    Returns:
        FilterResult with kept buildings and filtered IDs with reasons
    """
    kept = []
    filtered = []

    min_bound = -bounds_half
    max_bound = bounds_half

    for building in buildings:
        bbox = building.footprint.bbox

        # A building belongs to the patch its CENTRE stands in: built there whole
        # (a part over the border takes its height from the neighbouring patch),
        # the neighbouring patch leaves it out - every building is built exactly once.
        # A centre exactly on the landscape's outer right/top edge stays here.
        center = bbox.center
        is_outside = not owns_point(center.x, center.y, patch_id, heightmaps_dir,
                                    bounds_half)

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


def owns_point(
    x: float,
    y: float,
    patch_id=None,
    heightmaps_dir=None,
    bounds_half: float = PATCH_HALF
) -> bool:
    """
    Does the point (x, y) belong to this patch?

    Inside the patch -> yes, outside -> no (the neighbour it stands in builds it).
    Exactly ON the border: the left/bottom edge belongs to this patch, the right/top
    edge to the neighbour there, so a point on the line is built ONCE. If that
    neighbour is not in the landscape (no heightmap h<id>.txt), this patch keeps it,
    so nothing on the landscape's outer edge is lost.
    Layout: +X neighbour = x number - 1, +Y neighbour = y number + 1 (see fences.py).
    """
    import os

    def _nb_missing(dx, dy):
        if not (patch_id and heightmaps_dir):
            return True
        try:
            pid = str(patch_id)
            nid = f"{int(pid[:3]) + dx:03d}{int(pid[3:]) + dy:03d}"
        except ValueError:
            return True
        return not any(os.path.exists(os.path.join(heightmaps_dir, f"{c}{nid}.txt"))
                       for c in ("h", "H"))

    def _owns(v, dx, dy):
        if -bounds_half <= v < bounds_half:
            return True
        return v == bounds_half and _nb_missing(dx, dy)

    return _owns(x, -1, 0) and _owns(y, 0, 1)
