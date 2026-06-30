"""
Floor Z solver for Condor Buildings Generator.

Computes the ground elevation (floor_z) for buildings by finding
the true minimum terrain height under the building footprint.

Critical algorithm: samples footprint interior (inside outer ring
AND NOT inside any hole) and finds minimum Z from terrain.
"""

from typing import List, Optional, Tuple, Set
from dataclasses import dataclass
import math
import logging

from ..models.geometry import Point2D, BBox, Polygon
from ..models.terrain import TerrainMesh, TerrainTriangle
from .spatial_index import GridSpatialIndex
from ..config import FLOOR_Z_EPSILON

logger = logging.getLogger(__name__)


@dataclass
class FloorZResult:
    """Result of floor Z computation."""
    floor_z: float
    min_z: float
    max_z: float
    sample_count: int
    samples_in_footprint: int
    terrain_triangles_checked: int


class FloorZSolver:
    """
    Solves floor Z for buildings using terrain mesh.

    Uses a grid sampling approach within the building footprint
    to find the true minimum terrain elevation.
    """

    def __init__(
        self,
        terrain: TerrainMesh,
        spatial_index: Optional[GridSpatialIndex] = None,
        sample_spacing: float = 5.0,
        floor_z_epsilon: float = FLOOR_Z_EPSILON
    ):
        """
        Initialize solver.

        Args:
            terrain: Terrain mesh
            spatial_index: Optional pre-built spatial index
            sample_spacing: Distance between sample points (meters)
            floor_z_epsilon: Offset below terrain to avoid z-fighting
        """
        self.terrain = terrain
        self.sample_spacing = sample_spacing
        self.floor_z_epsilon = floor_z_epsilon

        # Build spatial index if not provided
        if spatial_index is not None:
            self.spatial_index = spatial_index
        else:
            self.spatial_index = GridSpatialIndex(terrain.triangles)

    def solve(self, polygon: Polygon) -> FloorZResult:
        """
        Compute floor Z for a building footprint.

        Algorithm:
        1. Generate grid sample points within footprint bbox
        2. Filter to points inside outer ring AND NOT inside any hole
        3. Query terrain Z at each valid sample point
        4. Return minimum Z (with epsilon offset)

        Args:
            polygon: Building footprint

        Returns:
            FloorZResult with computed elevation
        """
        bbox = polygon.bbox

        # Generate sample points within bbox
        sample_points = self._generate_sample_grid(bbox)

        # Filter to points inside footprint (outer AND NOT holes)
        valid_samples: List[Point2D] = []

        for point in sample_points:
            if self._point_in_footprint(point, polygon):
                valid_samples.append(point)

        if not valid_samples:
            # Fallback: use bbox corners and center
            valid_samples = [
                Point2D(bbox.min_x, bbox.min_y),
                Point2D(bbox.max_x, bbox.min_y),
                Point2D(bbox.max_x, bbox.max_y),
                Point2D(bbox.min_x, bbox.max_y),
                bbox.center,
            ]
            # Filter these too
            valid_samples = [
                p for p in valid_samples
                if self._point_in_footprint(p, polygon)
            ]

        # Query terrain at sample points
        z_values: List[float] = []
        triangles_checked = 0

        for point in valid_samples:
            z, num_checked = self._query_terrain_z(point)
            triangles_checked += num_checked

            if z is not None:
                z_values.append(z)

        if not z_values:
            # Last resort: query at footprint centroid
            centroid = self._compute_centroid(polygon.outer_ring)
            z, _ = self._query_terrain_z(centroid)

            if z is not None:
                z_values.append(z)

        if not z_values:
            logger.warning(
                f"Could not determine floor Z for footprint at "
                f"({bbox.center.x:.1f}, {bbox.center.y:.1f})"
            )
            return FloorZResult(
                floor_z=0.0,
                min_z=0.0,
                max_z=0.0,
                sample_count=len(sample_points),
                samples_in_footprint=len(valid_samples),
                terrain_triangles_checked=triangles_checked,
            )

        min_z = min(z_values)
        max_z = max(z_values)

        # Apply epsilon offset (slightly below terrain to avoid z-fighting)
        floor_z = min_z - self.floor_z_epsilon

        return FloorZResult(
            floor_z=floor_z,
            min_z=min_z,
            max_z=max_z,
            sample_count=len(sample_points),
            samples_in_footprint=len(valid_samples),
            terrain_triangles_checked=triangles_checked,
        )

    def _generate_sample_grid(self, bbox: BBox) -> List[Point2D]:
        """Generate a grid of sample points within bounding box."""
        points = []

        x = bbox.min_x
        while x <= bbox.max_x:
            y = bbox.min_y
            while y <= bbox.max_y:
                points.append(Point2D(x, y))
                y += self.sample_spacing
            x += self.sample_spacing

        return points

    def _point_in_footprint(self, point: Point2D, polygon: Polygon) -> bool:
        """
        Check if point is inside footprint (outer AND NOT any hole).

        This is the critical "inside_outer AND NOT inside_any_hole" logic.
        """
        # Must be inside outer ring
        if not _point_in_ring(point, polygon.outer_ring):
            return False

        # Must NOT be inside any hole
        for hole in polygon.holes:
            if _point_in_ring(point, hole):
                return False

        return True

    def _query_terrain_z(self, point: Point2D) -> Tuple[Optional[float], int]:
        """
        Query terrain elevation at a point.

        Args:
            point: 2D point to query

        Returns:
            (z_value or None, number of triangles checked)
        """
        # Get candidate triangles from spatial index
        candidates = self.spatial_index.query_point(point)

        for tri_idx in candidates:
            triangle = self.terrain.triangles[tri_idx]

            if triangle.contains_point_2d(point):
                z = triangle.z_at_xy(point.x, point.y)
                if z is not None:
                    return (z, len(candidates))

        return (None, len(candidates))

    def _compute_centroid(self, ring: List[Point2D]) -> Point2D:
        """Compute centroid of a polygon ring."""
        if not ring:
            return Point2D(0, 0)

        n = len(ring)
        if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
            n -= 1

        if n == 0:
            return Point2D(0, 0)

        sum_x = sum(ring[i].x for i in range(n))
        sum_y = sum(ring[i].y for i in range(n))

        return Point2D(sum_x / n, sum_y / n)


def _point_in_ring(point: Point2D, ring: List[Point2D]) -> bool:
    """
    Check if point is inside a polygon ring using ray casting.

    Args:
        point: Point to test
        ring: Polygon vertices (closed ring)

    Returns:
        True if point is inside
    """
    if len(ring) < 3:
        return False

    n = len(ring)
    inside = False

    j = n - 1
    for i in range(n):
        xi, yi = ring[i].x, ring[i].y
        xj, yj = ring[j].x, ring[j].y

        if ((yi > point.y) != (yj > point.y)) and \
           (point.x < (xj - xi) * (point.y - yi) / (yj - yi) + xi):
            inside = not inside

        j = i

    return inside


def compute_floor_z(
    polygon: Polygon,
    terrain: TerrainMesh,
    spatial_index: Optional[GridSpatialIndex] = None,
    sample_spacing: float = 5.0
) -> float:
    """
    Convenience function to compute floor Z for a single building.

    Args:
        polygon: Building footprint
        terrain: Terrain mesh
        spatial_index: Optional pre-built spatial index
        sample_spacing: Distance between sample points

    Returns:
        Floor Z elevation (meters)
    """
    solver = FloorZSolver(terrain, spatial_index, sample_spacing)
    result = solver.solve(polygon)
    return result.floor_z
