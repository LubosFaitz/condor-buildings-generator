"""
Spatial indexing for terrain triangle queries.

Provides efficient lookup of terrain triangles that may intersect
with building footprints, using a grid-based spatial index.
"""

from abc import ABC, abstractmethod
from typing import List, Set, Tuple, Dict, Optional
from dataclasses import dataclass
import math

from ..models.geometry import Point2D, BBox
from ..models.terrain import TerrainTriangle


class SpatialIndex(ABC):
    """Abstract base class for spatial indexing."""

    @abstractmethod
    def query(self, bbox: BBox) -> List[int]:
        """
        Query triangles that may intersect the given bounding box.

        Args:
            bbox: Bounding box to query

        Returns:
            List of triangle indices that may intersect
        """
        pass

    @abstractmethod
    def query_point(self, point: Point2D) -> List[int]:
        """
        Query triangles that may contain the given point.

        Args:
            point: Point to query

        Returns:
            List of triangle indices that may contain the point
        """
        pass


class GridSpatialIndex(SpatialIndex):
    """
    Grid-based spatial index for terrain triangles.

    Divides the terrain into a regular grid and maps each cell
    to the triangles that intersect it. Optimized for the 30m
    Condor terrain grid.
    """

    def __init__(
        self,
        triangles: List[TerrainTriangle],
        cell_size: float = 30.0,
        bounds: Optional[BBox] = None
    ):
        """
        Initialize spatial index.

        Args:
            triangles: List of terrain triangles to index
            cell_size: Size of grid cells (default 30m matches terrain)
            bounds: Optional bounds to use (computed from triangles if None)
        """
        self.triangles = triangles
        self.cell_size = cell_size

        # Compute bounds if not provided
        if bounds is not None:
            self.bounds = bounds
        elif triangles:
            min_x = min(t.bbox.min_x for t in triangles)
            min_y = min(t.bbox.min_y for t in triangles)
            max_x = max(t.bbox.max_x for t in triangles)
            max_y = max(t.bbox.max_y for t in triangles)
            self.bounds = BBox(min_x, min_y, max_x, max_y)
        else:
            self.bounds = BBox(0, 0, 0, 0)

        # Build grid
        self.grid: Dict[Tuple[int, int], Set[int]] = {}
        self._build_index()

    def _build_index(self) -> None:
        """Build the spatial index grid."""
        for tri_idx, triangle in enumerate(self.triangles):
            # Get all cells that this triangle overlaps
            cells = self._get_cells_for_bbox(triangle.bbox)

            for cell in cells:
                if cell not in self.grid:
                    self.grid[cell] = set()
                self.grid[cell].add(tri_idx)

    def _get_cell(self, x: float, y: float) -> Tuple[int, int]:
        """Get grid cell coordinates for a point."""
        cell_x = int((x - self.bounds.min_x) / self.cell_size)
        cell_y = int((y - self.bounds.min_y) / self.cell_size)
        return (cell_x, cell_y)

    def _get_cells_for_bbox(self, bbox: BBox) -> List[Tuple[int, int]]:
        """Get all grid cells that overlap a bounding box."""
        min_cell = self._get_cell(bbox.min_x, bbox.min_y)
        max_cell = self._get_cell(bbox.max_x, bbox.max_y)

        cells = []
        for cx in range(min_cell[0], max_cell[0] + 1):
            for cy in range(min_cell[1], max_cell[1] + 1):
                cells.append((cx, cy))

        return cells

    def query(self, bbox: BBox) -> List[int]:
        """
        Query triangles that may intersect the given bounding box.

        Args:
            bbox: Bounding box to query

        Returns:
            List of triangle indices (deduplicated)
        """
        cells = self._get_cells_for_bbox(bbox)
        result_set: Set[int] = set()

        for cell in cells:
            if cell in self.grid:
                result_set.update(self.grid[cell])

        return list(result_set)

    def query_point(self, point: Point2D) -> List[int]:
        """
        Query triangles that may contain the given point.

        Args:
            point: Point to query

        Returns:
            List of triangle indices
        """
        cell = self._get_cell(point.x, point.y)

        if cell in self.grid:
            return list(self.grid[cell])

        return []

    def get_stats(self) -> Dict[str, int]:
        """Get index statistics."""
        total_entries = sum(len(tris) for tris in self.grid.values())

        return {
            'num_triangles': len(self.triangles),
            'num_cells': len(self.grid),
            'total_entries': total_entries,
            'avg_per_cell': total_entries // max(1, len(self.grid)),
        }


def create_terrain_index(
    triangles: List[TerrainTriangle],
    cell_size: float = 30.0
) -> GridSpatialIndex:
    """
    Create a spatial index for terrain triangles.

    Args:
        triangles: List of terrain triangles
        cell_size: Grid cell size (default 30m)

    Returns:
        GridSpatialIndex instance
    """
    return GridSpatialIndex(triangles, cell_size)
