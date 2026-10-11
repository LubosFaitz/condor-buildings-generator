"""
Terrain data model for Condor Buildings Generator.

Provides TerrainTriangle and TerrainMesh classes for representing
terrain surfaces and computing height intersections.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Set
import math

from .geometry import Point2D, Point3D, BBox


@dataclass
class TerrainTriangle:
    """
    A single terrain triangle with precomputed plane equation.

    The plane equation is: nx*x + ny*y + nz*z = d
    This allows fast computation of Z at any (x,y) point.

    Attributes:
        v0, v1, v2: Triangle vertices
        bbox: 2D bounding box for spatial queries
        normal: Unit normal vector (nx, ny, nz)
        d: Plane equation constant
        index: Original index in terrain mesh
    """
    v0: Point3D
    v1: Point3D
    v2: Point3D
    bbox: BBox
    normal: Tuple[float, float, float]
    d: float
    index: int = 0

    def z_at_xy(self, x: float, y: float) -> Optional[float]:
        """
        Compute Z at (x,y) using plane equation.

        Returns:
            Z value at the given (x,y), or None if point is outside
            triangle or plane is vertical.
        """
        nx, ny, nz = self.normal

        # Check for vertical/degenerate plane
        if abs(nz) < 1e-9:
            return None

        # Solve for z: nx*x + ny*y + nz*z = d
        z = (self.d - nx * x - ny * y) / nz
        return z

    def contains_point_2d(self, p: Point2D) -> bool:
        """
        Check if 2D point is inside triangle (XY projection).

        Uses sign of cross products method.
        """
        return _point_in_triangle_2d(
            p,
            Point2D(self.v0.x, self.v0.y),
            Point2D(self.v1.x, self.v1.y),
            Point2D(self.v2.x, self.v2.y)
        )

    @staticmethod
    def from_vertices(v0: Point3D, v1: Point3D, v2: Point3D,
                      index: int = 0) -> Optional['TerrainTriangle']:
        """
        Create a TerrainTriangle from three vertices.

        Returns:
            TerrainTriangle with precomputed plane equation,
            or None if triangle is degenerate.
        """
        # Compute edge vectors
        e1 = Point3D(v1.x - v0.x, v1.y - v0.y, v1.z - v0.z)
        e2 = Point3D(v2.x - v0.x, v2.y - v0.y, v2.z - v0.z)

        # Cross product for normal
        nx = e1.y * e2.z - e1.z * e2.y
        ny = e1.z * e2.x - e1.x * e2.z
        nz = e1.x * e2.y - e1.y * e2.x

        # Normalize
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-9:
            return None  # Degenerate triangle

        nx /= length
        ny /= length
        nz /= length

        # Plane constant: n dot v0
        d = nx * v0.x + ny * v0.y + nz * v0.z

        # Bounding box
        xs = [v0.x, v1.x, v2.x]
        ys = [v0.y, v1.y, v2.y]
        bbox = BBox(min(xs), min(ys), max(xs), max(ys))

        return TerrainTriangle(
            v0=v0, v1=v1, v2=v2,
            bbox=bbox,
            normal=(nx, ny, nz),
            d=d,
            index=index
        )


@dataclass
class TerrainMesh:
    """
    Terrain mesh with spatial index for fast triangle lookups.

    The terrain is represented as a collection of triangles derived
    from a quad grid (2 triangles per quad). A grid-based spatial
    index enables efficient queries for triangles overlapping a given
    bounding box.

    Attributes:
        vertices: All terrain vertices
        quads: Original quad face indices (v0, v1, v2, v3)
        triangles: Derived triangles (2 per quad)
        bbox: Overall bounding box
        grid_step: Terrain grid spacing (default 30m for Condor)
        z_min, z_max: Elevation range

    Spatial index:
        grid_cells: Maps (cell_x, cell_y) -> set of triangle indices
        cell_size: Size of spatial index cells (default 2x grid_step)
    """
    vertices: List[Point3D]
    quads: List[Tuple[int, int, int, int]]
    triangles: List[TerrainTriangle]
    bbox: BBox
    grid_step: float = 30.0
    z_min: float = 0.0
    z_max: float = 0.0

    # Spatial index
    grid_cells: Dict[Tuple[int, int], Set[int]] = field(default_factory=dict)
    cell_size: float = 60.0  # 2x terrain grid for good coverage

    def get_triangles_in_bbox(self, query_bbox: BBox) -> Set[int]:
        """
        Get indices of triangles that may intersect the given bbox.

        Uses spatial index for fast lookup.

        Args:
            query_bbox: Bounding box to query

        Returns:
            Set of triangle indices
        """
        if not self.grid_cells:
            # Fallback: return all triangles if no spatial index
            return set(range(len(self.triangles)))

        candidates = set()

        min_cx = int(math.floor(query_bbox.min_x / self.cell_size))
        max_cx = int(math.floor(query_bbox.max_x / self.cell_size))
        min_cy = int(math.floor(query_bbox.min_y / self.cell_size))
        max_cy = int(math.floor(query_bbox.max_y / self.cell_size))

        for cx in range(min_cx, max_cx + 1):
            for cy in range(min_cy, max_cy + 1):
                key = (cx, cy)
                if key in self.grid_cells:
                    candidates.update(self.grid_cells[key])

        return candidates

    def build_spatial_index(self, cell_size: Optional[float] = None) -> None:
        """
        Build or rebuild the spatial index.

        Args:
            cell_size: Optional custom cell size (default 2x grid_step)
        """
        if cell_size is not None:
            self.cell_size = cell_size

        self.grid_cells.clear()

        for i, tri in enumerate(self.triangles):
            # Determine which cells this triangle overlaps
            min_cx = int(math.floor(tri.bbox.min_x / self.cell_size))
            max_cx = int(math.floor(tri.bbox.max_x / self.cell_size))
            min_cy = int(math.floor(tri.bbox.min_y / self.cell_size))
            max_cy = int(math.floor(tri.bbox.max_y / self.cell_size))

            for cx in range(min_cx, max_cx + 1):
                for cy in range(min_cy, max_cy + 1):
                    key = (cx, cy)
                    if key not in self.grid_cells:
                        self.grid_cells[key] = set()
                    self.grid_cells[key].add(i)

    @staticmethod
    def from_quads(vertices: List[Point3D],
                   quads: List[Tuple[int, int, int, int]],
                   grid_step: float = 30.0) -> 'TerrainMesh':
        """
        Create TerrainMesh from vertices and quad faces.

        Each quad is split into 2 triangles.

        Args:
            vertices: List of 3D vertices
            quads: List of quad face indices (0-indexed)
            grid_step: Terrain grid spacing

        Returns:
            TerrainMesh with triangles and spatial index
        """
        triangles = []
        tri_index = 0

        for quad in quads:
            v0, v1, v2, v3 = [vertices[i] for i in quad]

            # Split quad into two triangles: (0,1,2) and (0,2,3)
            tri1 = TerrainTriangle.from_vertices(v0, v1, v2, tri_index)
            if tri1:
                triangles.append(tri1)
                tri_index += 1

            tri2 = TerrainTriangle.from_vertices(v0, v2, v3, tri_index)
            if tri2:
                triangles.append(tri2)
                tri_index += 1

        # Compute overall bbox and elevation range
        xs = [v.x for v in vertices]
        ys = [v.y for v in vertices]
        zs = [v.z for v in vertices]

        bbox = BBox(min(xs), min(ys), max(xs), max(ys))

        mesh = TerrainMesh(
            vertices=vertices,
            quads=quads,
            triangles=triangles,
            bbox=bbox,
            grid_step=grid_step,
            z_min=min(zs),
            z_max=max(zs)
        )

        # Build spatial index
        mesh.build_spatial_index()

        return mesh


def _point_in_triangle_2d(p: Point2D, v0: Point2D, v1: Point2D, v2: Point2D) -> bool:
    """
    Check if 2D point is inside triangle using sign of cross products.

    Returns True if point is inside or on edge.
    """
    def sign(p1: Point2D, p2: Point2D, p3: Point2D) -> float:
        return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y)

    d1 = sign(p, v0, v1)
    d2 = sign(p, v1, v2)
    d3 = sign(p, v2, v0)

    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)

    return not (has_neg and has_pos)
