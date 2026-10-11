"""
Flat roof generator for Condor Buildings Generator.

Generates flat roof surfaces with proper hole handling
using triangulation. Falls back gracefully on triangulation failure.

UV Mapping:
- Flat roofs use separate tiling textures (flat_roof_1..6), NOT the houses atlas
- Planar projection aligned to building orientation (longest edge)
- 1 meter in world space = UV coordinate 0-1
- UVs can exceed 1.0 in both directions for texture repetition
- Each building's UVs are rotated to align with its longest edge,
  breaking visual uniformity across differently-oriented buildings
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
import logging
import math

from ..models.geometry import Point2D, Polygon
from ..models.mesh import MeshData
from ..models.building import BuildingRecord
from ..processing.footprint import compute_longest_edge_axis
from ..utils.triangulation import (
    triangulate_polygon,
    triangulate_with_holes,
    TriangulationError,
)

logger = logging.getLogger(__name__)


@dataclass
class FlatRoofConfig:
    """Configuration for flat roof generation."""
    pass  # Reserved for future options


def _compute_aligned_uv(point: Point2D, cx: float, cy: float,
                         cos_a: float, sin_a: float) -> Tuple[float, float]:
    """
    Compute UV coordinates aligned to building orientation.

    Translates point relative to centroid, then rotates by the building's
    longest edge angle. Scale: 1 meter = 1.0 UV (unchanged).

    Args:
        point: World-space point
        cx, cy: Centroid of the building footprint
        cos_a, sin_a: Pre-computed cos/sin of the longest edge angle

    Returns:
        (u, v) tuple
    """
    dx = point.x - cx
    dy = point.y - cy
    u = dx * cos_a + dy * sin_a
    v = -dx * sin_a + dy * cos_a
    return u, v


def _get_orientation_params(ring: List[Point2D]) -> Tuple[float, float, float, float]:
    """
    Compute centroid and rotation parameters for a building ring.

    Args:
        ring: Polygon outer ring vertices

    Returns:
        (cx, cy, cos_a, sin_a) tuple
    """
    # Centroid
    n = len(ring)
    # Skip closing vertex for centroid computation
    if n > 1 and ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        points = ring[:-1]
    else:
        points = ring
    n = len(points)
    cx = sum(p.x for p in points) / n
    cy = sum(p.y for p in points) / n

    # Longest edge angle
    angle_deg = compute_longest_edge_axis(ring)
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    return cx, cy, cos_a, sin_a


def generate_flat_roof(
    building: BuildingRecord,
    config: Optional[FlatRoofConfig] = None,
    use_global_uv: bool = False,
) -> MeshData:
    """
    Generate flat roof mesh for a building.

    Uses triangulation to handle complex footprints and holes.
    Falls back to outer ring only if triangulation with holes fails.

    UV mapping modes:
    - use_global_uv=False (default): Planar projection aligned to building's longest edge
    - use_global_uv=True: Global planar projection using world coordinates (for terrain texture)

    1 meter = 1.0 UV coordinate. Textures tile at 1m intervals.

    Args:
        building: Building record with footprint and heights
        config: Optional configuration
        use_global_uv: If True, use world coordinates as UVs (no per-building rotation)

    Returns:
        MeshData with roof geometry
    """
    mesh = MeshData(osm_id=building.osm_id)

    roof_z = building.wall_top_z  # Flat roof at eave height
    footprint = building.footprint

    # Compute orientation from outer ring (used for UV alignment)
    if use_global_uv:
        # Global projection: identity transform (no rotation, no centering)
        orient = (0.0, 0.0, 1.0, 0.0)  # cx=0, cy=0, cos=1, sin=0
    else:
        orient = _get_orientation_params(footprint.outer_ring)

    if footprint.has_holes:
        # Try triangulation with holes
        try:
            _generate_roof_with_holes(mesh, footprint, roof_z, orient)
        except TriangulationError as e:
            logger.warning(
                f"Building {building.osm_id}: Triangulation with holes failed: {e}. "
                f"Falling back to outer ring only."
            )
            building.warnings.append(f"Flat roof: holes ignored due to {e}")
            _generate_simple_roof(mesh, footprint.outer_ring, roof_z, orient)
    else:
        # Simple case: no holes
        try:
            _generate_simple_roof(mesh, footprint.outer_ring, roof_z, orient)
        except TriangulationError as e:
            logger.warning(
                f"Building {building.osm_id}: Triangulation failed: {e}"
            )
            building.warnings.append(f"Flat roof: triangulation failed: {e}")
            # Last resort: try a fan triangulation from centroid
            _generate_fan_roof(mesh, footprint.outer_ring, roof_z, orient)

    return mesh


def _generate_roof_with_holes(
    mesh: MeshData,
    footprint: Polygon,
    roof_z: float,
    orient: Tuple[float, float, float, float],
) -> None:
    """
    Generate triangulated roof surface with holes and UV coordinates.

    UV mapping: planar projection aligned to building orientation.

    Args:
        mesh: MeshData to add to
        footprint: Polygon with outer ring and holes
        roof_z: Roof elevation
        orient: (cx, cy, cos_a, sin_a) orientation parameters
    """
    cx, cy, cos_a, sin_a = orient

    # Triangulate with hole bridging
    merged_vertices, triangles = triangulate_with_holes(
        footprint.outer_ring,
        footprint.holes
    )

    # Add vertices and UVs to mesh
    # Oriented planar projection: 1m world space = 1.0 UV (textures tile at 1m)
    vertex_indices = []
    uv_indices = []
    for point in merged_vertices:
        v_idx = mesh.add_vertex(point.x, point.y, roof_z)
        vertex_indices.append(v_idx)

        u, v = _compute_aligned_uv(point, cx, cy, cos_a, sin_a)
        uv_idx = mesh.add_uv(u, v)
        uv_indices.append(uv_idx)

    # Add triangles with UVs. tessellate_polygon / earcut don't guarantee a
    # consistent winding, so force CCW-as-seen-from-above (+Z) — a flat roof
    # must face up, otherwise some courtyard-roof triangles render inverted /
    # back-face culled (Lubos's report, v0.8.14). Swapping b<->c reorders the
    # vertex and UV corners together, so UVs stay attached to their vertices.
    for a, b, c in triangles:
        pa, pb, pc = merged_vertices[a], merged_vertices[b], merged_vertices[c]
        cross_z = (pb.x - pa.x) * (pc.y - pa.y) - (pb.y - pa.y) * (pc.x - pa.x)
        if cross_z < 0.0:
            b, c = c, b
        mesh.add_triangle_with_uvs(
            vertex_indices[a], vertex_indices[b], vertex_indices[c],
            uv_indices[a], uv_indices[b], uv_indices[c]
        )


def _generate_simple_roof(
    mesh: MeshData,
    ring: List[Point2D],
    roof_z: float,
    orient: Tuple[float, float, float, float],
) -> None:
    """
    Generate triangulated roof surface for simple polygon (no holes) with UVs.

    UV mapping: planar projection aligned to building orientation.

    Args:
        mesh: MeshData to add to
        ring: Outer ring vertices
        roof_z: Roof elevation
        orient: (cx, cy, cos_a, sin_a) orientation parameters
    """
    cx, cy, cos_a, sin_a = orient

    # Skip closing vertex if present
    n = len(ring)
    if n > 0 and ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        ring = ring[:-1]
        n -= 1

    if n < 3:
        return

    # Triangulate
    triangles = triangulate_polygon(ring)

    # Add vertices and UVs to mesh
    # Oriented planar projection: 1m world space = 1.0 UV (textures tile at 1m)
    vertex_indices = []
    uv_indices = []
    for point in ring:
        v_idx = mesh.add_vertex(point.x, point.y, roof_z)
        vertex_indices.append(v_idx)

        u, v = _compute_aligned_uv(point, cx, cy, cos_a, sin_a)
        uv_idx = mesh.add_uv(u, v)
        uv_indices.append(uv_idx)

    # Add triangles with UVs
    for a, b, c in triangles:
        mesh.add_triangle_with_uvs(
            vertex_indices[a], vertex_indices[b], vertex_indices[c],
            uv_indices[a], uv_indices[b], uv_indices[c]
        )


def _generate_fan_roof(
    mesh: MeshData,
    ring: List[Point2D],
    roof_z: float,
    orient: Tuple[float, float, float, float],
) -> None:
    """
    Generate fan triangulation from centroid with UVs (fallback).

    This works for convex polygons and is a last resort for
    when ear clipping fails.

    UV mapping: planar projection aligned to building orientation.

    Args:
        mesh: MeshData to add to
        ring: Outer ring vertices
        roof_z: Roof elevation
        orient: (cx, cy, cos_a, sin_a) orientation parameters
    """
    cx, cy, cos_a, sin_a = orient

    # Skip closing vertex if present
    n = len(ring)
    if n > 0 and ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        ring = ring[:-1]
        n -= 1

    if n < 3:
        return

    # Add centroid vertex with UV (centroid maps to UV origin)
    center_idx = mesh.add_vertex(cx, cy, roof_z)
    center_uv_idx = mesh.add_uv(0.0, 0.0)

    # Add ring vertices with UVs
    # Oriented planar projection: 1m world space = 1.0 UV (textures tile at 1m)
    vertex_indices = []
    uv_indices = []
    for point in ring:
        v_idx = mesh.add_vertex(point.x, point.y, roof_z)
        vertex_indices.append(v_idx)

        u, v = _compute_aligned_uv(point, cx, cy, cos_a, sin_a)
        uv_idx = mesh.add_uv(u, v)
        uv_indices.append(uv_idx)

    # Create fan triangles with UVs
    for i in range(n):
        j = (i + 1) % n
        mesh.add_triangle_with_uvs(
            center_idx, vertex_indices[i], vertex_indices[j],
            center_uv_idx, uv_indices[i], uv_indices[j]
        )


def generate_flat_cap(
    ring: List[Point2D],
    z: float,
    osm_id: Optional[str] = None,
    facing_up: bool = True
) -> MeshData:
    """
    Generate a flat cap surface (for gable end caps, etc.).

    Args:
        ring: Ring vertices
        z: Z elevation
        osm_id: Optional building ID
        facing_up: If True, normals face +Z; if False, face -Z

    Returns:
        MeshData with cap geometry
    """
    mesh = MeshData(osm_id=osm_id)

    # Skip closing vertex if present
    n = len(ring)
    if n > 0 and ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        ring = ring[:-1]
        n -= 1

    if n < 3:
        return mesh

    try:
        triangles = triangulate_polygon(ring)
    except TriangulationError:
        # Fallback to fan
        _generate_fan_roof(mesh, ring, z)
        return mesh

    # Add vertices
    vertex_indices = []
    for point in ring:
        idx = mesh.add_vertex(point.x, point.y, z)
        vertex_indices.append(idx)

    # Add triangles (reverse order if facing down)
    for a, b, c in triangles:
        if facing_up:
            mesh.add_triangle(
                vertex_indices[a],
                vertex_indices[b],
                vertex_indices[c]
            )
        else:
            mesh.add_triangle(
                vertex_indices[c],
                vertex_indices[b],
                vertex_indices[a]
            )

    return mesh
