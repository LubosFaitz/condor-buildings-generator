"""
Way stitcher for OSM multipolygon relations.

OSM multipolygon ways may come unordered and with inconsistent
orientation. This module provides algorithms to stitch them into
properly ordered, closed rings with consistent winding.

P0 CRITICAL: This is essential for correct building footprint parsing.
"""

from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass
import logging

from ..models.geometry import Point2D
from ..utils.polygon_utils import polygon_signed_area, reverse_ring

logger = logging.getLogger(__name__)


@dataclass
class WaySegment:
    """
    A segment of a way with node IDs and optional coordinates.

    Attributes:
        way_id: OSM way ID
        node_ids: List of node IDs in order
        coords: Optional list of (x, y) coordinates (if already projected)
    """
    way_id: str
    node_ids: List[str]
    coords: Optional[List[Tuple[float, float]]] = None

    @property
    def first_node(self) -> str:
        """First node ID."""
        return self.node_ids[0] if self.node_ids else ""

    @property
    def last_node(self) -> str:
        """Last node ID."""
        return self.node_ids[-1] if self.node_ids else ""

    @property
    def is_closed(self) -> bool:
        """Check if way forms a closed loop."""
        return len(self.node_ids) >= 3 and self.first_node == self.last_node

    def reverse(self) -> 'WaySegment':
        """Return a reversed copy of this segment."""
        return WaySegment(
            way_id=self.way_id,
            node_ids=list(reversed(self.node_ids)),
            coords=list(reversed(self.coords)) if self.coords else None
        )


class StitchingError(Exception):
    """Raised when way stitching fails."""
    pass


def stitch_ways(
    ways: List[WaySegment],
    validate: bool = True
) -> List[List[str]]:
    """
    Stitch multiple ways into one or more closed rings.

    Algorithm:
    1. Build endpoint index mapping node IDs to ways
    2. Iteratively chain ways by matching endpoints
    3. Reverse ways if needed to maintain continuity
    4. Validate closure and return rings

    Args:
        ways: List of WaySegment objects to stitch
        validate: If True, validate that rings are properly closed

    Returns:
        List of rings, where each ring is a list of node IDs

    Raises:
        StitchingError: If ways cannot be stitched into closed rings
    """
    if not ways:
        return []

    # Single closed way is already a ring
    if len(ways) == 1 and ways[0].is_closed:
        return [ways[0].node_ids]

    # Build endpoint index
    # Maps node_id -> list of (way_index, is_first_endpoint)
    endpoint_index: Dict[str, List[Tuple[int, bool]]] = {}

    for i, way in enumerate(ways):
        if not way.node_ids:
            continue

        first = way.first_node
        last = way.last_node

        if first not in endpoint_index:
            endpoint_index[first] = []
        endpoint_index[first].append((i, True))

        if first != last:  # Don't double-index closed ways
            if last not in endpoint_index:
                endpoint_index[last] = []
            endpoint_index[last].append((i, False))

    # Track which ways have been used
    used: Set[int] = set()
    rings: List[List[str]] = []

    # Process ways until all are used
    while len(used) < len(ways):
        # Find an unused way to start a ring
        start_idx = None
        for i in range(len(ways)):
            if i not in used and ways[i].node_ids:
                start_idx = i
                break

        if start_idx is None:
            break

        # Build ring starting from this way
        ring_nodes: List[str] = []
        current_nodes = list(ways[start_idx].node_ids)
        used.add(start_idx)

        # Add nodes (excluding last if it will be duplicated)
        ring_nodes.extend(current_nodes[:-1] if len(current_nodes) > 1 else current_nodes)
        current_end = current_nodes[-1]

        # Keep extending until ring closes or no more connections
        max_iterations = len(ways) * 2  # Safety limit
        iterations = 0

        while iterations < max_iterations:
            iterations += 1

            # Check if ring is closed
            if ring_nodes and current_end == ring_nodes[0]:
                break

            # Find next way that connects to current end
            next_way = _find_connecting_way(
                endpoint_index, current_end, used, ways
            )

            if next_way is None:
                # No connection found
                break

            way_idx, needs_reverse = next_way
            used.add(way_idx)

            next_nodes = ways[way_idx].node_ids
            if needs_reverse:
                next_nodes = list(reversed(next_nodes))

            # Add nodes (skip first as it's the connection point)
            ring_nodes.extend(next_nodes[1:-1] if len(next_nodes) > 2 else [])
            if len(next_nodes) > 1:
                current_end = next_nodes[-1]

        # Only add ring if it naturally closed or we can verify closure
        if ring_nodes:
            # Check if ring actually closed (endpoints connected)
            is_naturally_closed = (current_end == ring_nodes[0])

            if is_naturally_closed:
                # Ring closed naturally - add closing node
                ring_nodes.append(ring_nodes[0])
                rings.append(ring_nodes)
            elif validate:
                # Ring did NOT close - this is a stitching failure
                logger.warning(
                    f"Way stitching failed: ring starting with node "
                    f"{ring_nodes[0]} did not close "
                    f"(endpoint {current_end} != start {ring_nodes[0]}). "
                    f"Ring has {len(ring_nodes)} nodes, skipping."
                )
                # DO NOT force-close - skip this malformed ring
            else:
                # Non-validating mode: force close but warn
                logger.debug(
                    f"Force-closing ring with {len(ring_nodes)} nodes"
                )
                ring_nodes.append(ring_nodes[0])
                rings.append(ring_nodes)

    # Additional validation pass
    if validate:
        valid_rings = []
        for i, ring in enumerate(rings):
            if len(ring) < 4:  # Need at least 3 unique points + closing
                logger.warning(
                    f"Ring {i} has only {len(ring)} nodes (minimum 4 required), skipping"
                )
            elif ring[0] != ring[-1]:
                logger.warning(f"Ring {i} is not properly closed, skipping")
            else:
                valid_rings.append(ring)
        return valid_rings

    return rings


def _find_connecting_way(
    endpoint_index: Dict[str, List[Tuple[int, bool]]],
    target_node: str,
    used: Set[int],
    ways: List[WaySegment]
) -> Optional[Tuple[int, bool]]:
    """
    Find an unused way that connects to the target node.

    Args:
        endpoint_index: Map of node_id -> [(way_idx, is_first)]
        target_node: Node ID to connect to
        used: Set of already-used way indices
        ways: List of all ways

    Returns:
        (way_index, needs_reverse) or None if no connection found
    """
    if target_node not in endpoint_index:
        return None

    for way_idx, is_first in endpoint_index[target_node]:
        if way_idx in used:
            continue

        # If target matches first endpoint, way goes in normal direction
        # If target matches last endpoint, way needs to be reversed
        needs_reverse = not is_first
        return (way_idx, needs_reverse)

    return None


def stitch_ways_to_points(
    ways: List[WaySegment],
    node_coords: Dict[str, Tuple[float, float]]
) -> List[List[Point2D]]:
    """
    Stitch ways and convert to Point2D coordinates.

    Args:
        ways: List of WaySegment objects
        node_coords: Map of node_id -> (x, y) coordinates

    Returns:
        List of rings as Point2D lists
    """
    node_id_rings = stitch_ways(ways)
    point_rings = []

    for ring in node_id_rings:
        points = []
        for node_id in ring:
            if node_id in node_coords:
                x, y = node_coords[node_id]
                points.append(Point2D(x, y))
            else:
                logger.warning(f"Missing coordinates for node {node_id}")

        if len(points) >= 3:
            point_rings.append(points)

    return point_rings


def normalize_ring_orientation(
    ring: List[Point2D],
    should_be_ccw: bool
) -> List[Point2D]:
    """
    Ensure ring has correct winding orientation.

    Args:
        ring: List of Point2D vertices
        should_be_ccw: True for outer rings (CCW), False for holes (CW)

    Returns:
        Ring with correct orientation (may be reversed)
    """
    if len(ring) < 3:
        return ring

    area = polygon_signed_area(ring)
    is_ccw = area > 0

    if is_ccw != should_be_ccw:
        return reverse_ring(ring)

    return ring


def validate_ring(ring: List[Point2D]) -> List[str]:
    """
    Validate a ring for common issues.

    Args:
        ring: List of Point2D vertices

    Returns:
        List of issue descriptions (empty if valid)
    """
    issues = []

    if len(ring) < 3:
        issues.append(f"Ring has fewer than 3 vertices ({len(ring)})")
        return issues

    # Check closure
    if ring[0].x != ring[-1].x or ring[0].y != ring[-1].y:
        issues.append("Ring is not closed")

    # Check for duplicate consecutive vertices
    for i in range(len(ring) - 1):
        if ring[i].x == ring[i+1].x and ring[i].y == ring[i+1].y:
            issues.append(f"Duplicate consecutive vertex at index {i}")

    # Check for zero area
    area = abs(polygon_signed_area(ring))
    if area < 1e-6:
        issues.append("Ring has zero or near-zero area")

    # Check for self-intersection (simplified check)
    # Full self-intersection test is expensive; we do a basic check
    unique_points = set()
    for p in ring[:-1]:  # Exclude closing point
        key = (round(p.x, 6), round(p.y, 6))
        if key in unique_points:
            issues.append("Ring may have self-intersection (duplicate point)")
            break
        unique_points.add(key)

    return issues
