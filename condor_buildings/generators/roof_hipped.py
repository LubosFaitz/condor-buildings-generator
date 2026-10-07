"""
Hipped roof generator for Condor Buildings Generator.

Generates hipped (four-slope) roofs for quadrilateral footprints using
an analytical solution based on BLOSM's quadrangle algorithm.

The algorithm computes edge events (where adjacent edge bisectors meet)
to determine ridge endpoints, then creates 4 roof faces:
- 2 triangular hips (at the edges with minimum distance to event)
- 2 trapezoidal slopes (connecting the ridge endpoints)

Design assumptions:
- Footprint has exactly 4 vertices (quadrilateral)
- Footprint is convex and has no holes
- Same constraints as gabled roofs apply

Special case:
- Square footprints (equal edge distances) produce pyramidal roofs
  with a single apex instead of a ridge

Reference: BLOSM roof_hipped.py generateRoofQuadrangle()
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
import math
import logging

from ..models.geometry import Point2D, Point3D
from ..models.mesh import MeshData
from ..models.building import BuildingRecord
from ..processing.footprint import get_unique_vertex_count
from ..config import (
    ROOF_OVERHANG_LOD0,
    DEBUG_HIPPED_ROOFS,
    HIPPED_HEIGHT_FIXED,
)
from .uv_mapping import select_building_variations, get_roof_v_range

logger = logging.getLogger(__name__)


# Auxiliary indices for quadrangle iteration (CCW order)
_PREV_INDICES = (3, 0, 1, 2)
_INDICES = (0, 1, 2, 3)
_NEXT_INDICES = (1, 2, 3, 0)
_OPPOSITE_INDICES = (2, 3, 0, 1)

# Tolerance for square detection (relative difference in distances)
# Increased from 0.01 to 0.15 so near-square buildings use the more robust
# pyramidal roof instead of producing degenerate short ridges ("diamond" look)
SQUARE_TOLERANCE = 0.15


@dataclass
class HippedRoofConfig:
    """Configuration for hipped roof generation."""
    overhang: float = 0.0  # Roof overhang in meters
    double_sided_roof: bool = True  # Duplicate faces for underside visibility


@dataclass
class EdgeGeometry:
    """Computed geometry for quadrilateral edges."""
    vectors: List[Tuple[float, float]]  # Edge vectors (dx, dy)
    lengths: List[float]  # Edge lengths
    cos_angles: List[float]  # Cosines of interior angles
    sin_angles: List[float]  # Sines of interior angles
    distances: List[float]  # Distance from edge to edge event


def generate_hipped_roof(
    building: BuildingRecord,
    config: Optional[HippedRoofConfig] = None
) -> MeshData:
    """
    Generate hipped roof mesh for a building using BLOSM's analytical algorithm.

    The algorithm:
    1. Compute edge vectors, lengths, and angles for the quadrilateral
    2. Calculate distance from each edge to its "edge event" (bisector intersection)
    3. Find the two edges with minimum distance (these define ridge endpoints)
    4. Generate 4 roof faces: 2 triangular hips + 2 trapezoidal slopes

    Special case: For square footprints, generates pyramidal roof (single apex).

    Geometry: The roof is positioned so that the slope plane passes through
    wall_top_z at the original (non-expanded) footprint boundary. When there
    is overhang, the eave corners are BELOW wall_top_z due to the slope.

    Args:
        building: Building record with footprint and heights
        config: Optional configuration

    Returns:
        MeshData with roof geometry
    """
    if config is None:
        config = HippedRoofConfig()

    mesh = MeshData(osm_id=building.osm_id)

    footprint = building.footprint
    outer_ring = footprint.outer_ring

    # Get 4 unique vertices (remove closing vertex if present)
    # This is the ORIGINAL footprint (at wall boundary)
    original_verts = _get_quadrilateral_vertices(outer_ring)
    if original_verts is None:
        logger.warning(f"Building {building.osm_id}: Not a valid quadrilateral for hipped roof")
        return mesh

    # Compute edge geometry on ORIGINAL footprint first
    # This gives us the distances needed for slope calculation
    original_edge_geom = _compute_edge_geometry(original_verts)
    if original_edge_geom is None:
        logger.warning(f"Building {building.osm_id}: Failed to compute edge geometry")
        return mesh

    # Fixed roof height (Phase 1 requirement)
    roof_height = HIPPED_HEIGHT_FIXED
    building.ridge_height_m = roof_height

    # The wall_top_z is where the roof slope plane should intersect the wall
    wall_top_z = building.wall_top_z

    # Compute slope (tan_pitch) using original footprint geometry
    # For hipped roof: slope = height / max_distance_to_ridge
    original_distances = original_edge_geom.distances
    max_dist = max(original_distances)
    tan_pitch = roof_height / max_dist if max_dist > 0.01 else 1.0

    # Ridge Z is at wall_top_z + roof_height
    ridge_z = wall_top_z + roof_height

    # Calculate eave_z for the expanded footprint
    # The overhang extends beyond the wall, so eave corners are LOWER
    # eave_z = wall_top_z - tan_pitch * overhang
    if config.overhang > 0:
        eave_z = wall_top_z - tan_pitch * config.overhang
        verts_2d = _expand_footprint(original_verts, config.overhang)
        # Recompute edge geometry for expanded footprint
        edge_geom = _compute_edge_geometry(verts_2d)
        if edge_geom is None:
            logger.warning(f"Building {building.osm_id}: Failed to compute edge geometry for expanded footprint")
            return mesh
    else:
        eave_z = wall_top_z
        verts_2d = original_verts
        edge_geom = original_edge_geom

    # Get texture variations
    roof_index, _ = select_building_variations(building.seed)

    # Check for square case (pyramidal roof) using original geometry
    is_square = _is_square_footprint(original_edge_geom.distances)

    # Record face count before roof generation
    faces_before = len(mesh.faces)

    if is_square:
        # Pyramidal roof: single apex at center
        _generate_pyramidal_roof(
            mesh, verts_2d, eave_z, ridge_z, roof_index
        )
    else:
        # Standard hipped roof: ridge between two edge events
        # Pass tan_pitch calculated from original footprint for consistent geometry
        # Validate ridge vertices before committing - fall back to pyramidal if degenerate
        if _validate_hipped_ridge(verts_2d, edge_geom, tan_pitch, eave_z):
            _generate_hipped_roof_quadrangle(
                mesh, verts_2d, edge_geom, eave_z, roof_height, roof_index, tan_pitch
            )
        else:
            logger.debug(
                f"Building {building.osm_id}: hipped ridge degenerate, "
                f"falling back to pyramidal"
            )
            _generate_pyramidal_roof(
                mesh, verts_2d, eave_z, ridge_z, roof_index
            )

    faces_after = len(mesh.faces)
    roof_face_count = faces_after - faces_before

    # Duplicate faces for double-sided visibility
    if config.double_sided_roof and roof_face_count > 0:
        _duplicate_faces_reversed(mesh, faces_before, faces_after)

    # Debug logging
    if DEBUG_HIPPED_ROOFS:
        vertex_count = get_unique_vertex_count(outer_ring)
        roof_type = "pyramidal" if is_square else "hipped"
        eave_drop = wall_top_z - eave_z
        logger.info(
            f"HIPPED DEBUG [{building.osm_id}]: "
            f"verts={vertex_count}, "
            f"type={roof_type}, "
            f"overhang={config.overhang:.2f}m, "
            f"wall_top_z={wall_top_z:.2f}m, "
            f"eave_z={eave_z:.2f}m (drop={eave_drop:.2f}m), "
            f"ridge_z={ridge_z:.2f}m, "
            f"tan_pitch={tan_pitch:.3f}, "
            f"faces={roof_face_count} (x2 for double-sided)"
        )

    return mesh


def _get_quadrilateral_vertices(ring: List[Point2D]) -> Optional[List[Tuple[float, float]]]:
    """
    Extract 4 unique vertices from the footprint ring.

    Args:
        ring: Footprint outer ring

    Returns:
        List of 4 (x, y) tuples in CCW order, or None if not a quadrilateral
    """
    # Remove closing vertex if present
    verts = [(p.x, p.y) for p in ring]
    if len(verts) > 1 and verts[0] == verts[-1]:
        verts = verts[:-1]

    if len(verts) != 4:
        return None

    return verts


def _expand_footprint(
    verts: List[Tuple[float, float]],
    overhang: float
) -> List[Tuple[float, float]]:
    """
    Expand the footprint outward by the overhang distance.

    Uses the bisector direction at each vertex to expand uniformly.

    Args:
        verts: List of 4 (x, y) vertices
        overhang: Distance to expand outward

    Returns:
        Expanded vertices
    """
    n = len(verts)
    expanded = []

    for i in range(n):
        prev_i = (i - 1) % n
        next_i = (i + 1) % n

        # Current vertex
        vx, vy = verts[i]

        # Edge vectors pointing away from current vertex
        # Previous edge: from prev to current
        prev_dx = vx - verts[prev_i][0]
        prev_dy = vy - verts[prev_i][1]
        prev_len = math.sqrt(prev_dx * prev_dx + prev_dy * prev_dy)

        # Next edge: from current to next
        next_dx = verts[next_i][0] - vx
        next_dy = verts[next_i][1] - vy
        next_len = math.sqrt(next_dx * next_dx + next_dy * next_dy)

        if prev_len < 0.001 or next_len < 0.001:
            expanded.append((vx, vy))
            continue

        # Normalize
        prev_dx /= prev_len
        prev_dy /= prev_len
        next_dx /= next_len
        next_dy /= next_len

        # Outward normals (perpendicular, pointing outward for CCW polygon)
        # For edge from A to B, outward normal is (dy, -dx) for CCW
        prev_nx = prev_dy
        prev_ny = -prev_dx
        next_nx = next_dy
        next_ny = -next_dx

        # Bisector direction (average of outward normals)
        bisect_x = prev_nx + next_nx
        bisect_y = prev_ny + next_ny
        bisect_len = math.sqrt(bisect_x * bisect_x + bisect_y * bisect_y)

        if bisect_len < 0.001:
            # Edges are parallel, use one normal
            bisect_x = prev_nx
            bisect_y = prev_ny
            bisect_len = 1.0

        bisect_x /= bisect_len
        bisect_y /= bisect_len

        # Calculate expansion factor based on angle
        # For a corner with interior angle θ, move by overhang / sin(θ/2)
        # The bisector already accounts for direction, so we adjust magnitude
        dot = prev_nx * next_nx + prev_ny * next_ny
        # cos(angle between normals) = dot, angle between normals = π - interior_angle
        # sin(interior_angle/2) = cos((π - interior_angle)/2) = cos(π/2 - interior_angle/2)
        # For simplicity, use: expansion = overhang / cos(half_angle_between_edges)
        half_angle_cos = math.sqrt((1 + dot) / 2)
        if half_angle_cos < 0.1:
            half_angle_cos = 0.1  # Prevent extreme expansion

        expansion = overhang / half_angle_cos

        # Expand vertex
        new_x = vx + bisect_x * expansion
        new_y = vy + bisect_y * expansion
        expanded.append((new_x, new_y))

    return expanded


def _compute_edge_geometry(verts: List[Tuple[float, float]]) -> Optional[EdgeGeometry]:
    """
    Compute edge vectors, lengths, angles, and distances for the quadrilateral.

    Based on BLOSM's generateRoofQuadrangle() algorithm.

    Args:
        verts: List of 4 (x, y) vertices in CCW order

    Returns:
        EdgeGeometry with computed values, or None if computation fails
    """
    vectors = []
    lengths = []
    cos_angles = []
    sin_angles = []
    distances = []

    # Compute edge vectors and lengths
    for i, next_i in zip(_INDICES, _NEXT_INDICES):
        dx = verts[next_i][0] - verts[i][0]
        dy = verts[next_i][1] - verts[i][1]
        vectors.append((dx, dy))
        lengths.append(math.sqrt(dx * dx + dy * dy))

    # Check for degenerate edges
    for length in lengths:
        if length < 0.01:
            return None

    # Compute interior angles (cosine and sine)
    for prev_i, i in zip(_PREV_INDICES, _INDICES):
        # Dot product of current edge with previous edge (negated for interior angle)
        # cos(angle) = -dot(v[i], v[prev]) / (len[i] * len[prev])
        dot = vectors[i][0] * vectors[prev_i][0] + vectors[i][1] * vectors[prev_i][1]
        cos_angle = -dot / (lengths[i] * lengths[prev_i])

        # Cross product Z component (for 2D, this gives signed area)
        # sin(angle) = -cross(v[i], v[prev]).z / (len[i] * len[prev])
        cross_z = vectors[i][0] * vectors[prev_i][1] - vectors[i][1] * vectors[prev_i][0]
        sin_angle = -cross_z / (lengths[i] * lengths[prev_i])

        cos_angles.append(cos_angle)
        sin_angles.append(sin_angle)

    # Check for valid angles (sin should be positive for convex CCW polygon)
    for sin_angle in sin_angles:
        if sin_angle <= 0.001:
            return None

    # Compute distance from each edge to its edge event
    # distance[i] = length[i] / ((1+cos[i])/sin[i] + (1+cos[i+1])/sin[i+1])
    for i, next_i in zip(_INDICES, _NEXT_INDICES):
        denom = (1 + cos_angles[i]) / sin_angles[i] + (1 + cos_angles[next_i]) / sin_angles[next_i]
        if abs(denom) < 0.001:
            return None
        dist = lengths[i] / denom
        distances.append(dist)

    return EdgeGeometry(
        vectors=vectors,
        lengths=lengths,
        cos_angles=cos_angles,
        sin_angles=sin_angles,
        distances=distances
    )


def _is_square_footprint(distances: List[float]) -> bool:
    """
    Check if the footprint is approximately square.

    For a square, all edge event distances are equal.

    Args:
        distances: List of 4 edge event distances

    Returns:
        True if approximately square
    """
    if len(distances) != 4:
        return False

    # Compare adjacent distances (0 vs 1, which would make a ridge)
    d0, d1 = distances[0], distances[1]
    if max(d0, d1) < 0.01:
        return True

    relative_diff = abs(d0 - d1) / max(d0, d1)
    return relative_diff < SQUARE_TOLERANCE


def _get_ridge_vertex(
    base_vert: Tuple[float, float],
    edge_idx: int,
    edge_geom: EdgeGeometry,
    tan_pitch: float,
    eave_z: float
) -> Tuple[float, float, float]:
    """
    Compute the 3D position of a ridge vertex.

    The ridge vertex is at the edge event location, raised by the roof pitch.

    Based on BLOSM's getRoofVert() function.

    Args:
        base_vert: Starting vertex (x, y) on the footprint
        edge_idx: Index of the edge (0-3)
        edge_geom: Computed edge geometry
        tan_pitch: Tangent of the roof pitch angle
        eave_z: Z coordinate at eave level

    Returns:
        (x, y, z) position of ridge vertex
    """
    dist = edge_geom.distances[edge_idx]
    length = edge_geom.lengths[edge_idx]
    vec = edge_geom.vectors[edge_idx]
    cos_a = edge_geom.cos_angles[edge_idx]
    sin_a = edge_geom.sin_angles[edge_idx]

    # Direction perpendicular to edge (inward): cross(z_axis, vector) = (-vy, vx)
    perp_x = -vec[1]
    perp_y = vec[0]

    # Offset along the edge direction
    along_factor = (1 + cos_a) / sin_a

    # Total offset = (dist / length) * (perp + along_factor * vec)
    factor = dist / length
    offset_x = factor * (perp_x + along_factor * vec[0])
    offset_y = factor * (perp_y + along_factor * vec[1])

    # 3D position
    x = base_vert[0] + offset_x
    y = base_vert[1] + offset_y
    z = eave_z + dist * tan_pitch

    return (x, y, z)


def _validate_hipped_ridge(
    verts: List[Tuple[float, float]],
    edge_geom: EdgeGeometry,
    tan_pitch: float,
    eave_z: float
) -> bool:
    """
    Validate that computed ridge vertices fall within the footprint bounding box.

    For near-square or degenerate quadrilaterals, the BLOSM algorithm can produce
    ridge vertices that are displaced outside the footprint, causing a "diamond"
    visual artifact. This function detects such cases.

    Args:
        verts: Footprint vertices (x, y)
        edge_geom: Computed edge geometry
        tan_pitch: Tangent of roof pitch
        eave_z: Z at eave level

    Returns:
        True if ridge geometry is valid, False if degenerate
    """
    distances = edge_geom.distances
    min_idx1 = min(_INDICES, key=lambda i: distances[i])
    min_idx2 = _OPPOSITE_INDICES[min_idx1]

    ridge_v1 = _get_ridge_vertex(verts[min_idx1], min_idx1, edge_geom, tan_pitch, eave_z)
    ridge_v2 = _get_ridge_vertex(verts[min_idx2], min_idx2, edge_geom, tan_pitch, eave_z)

    # Compute bounding box with tolerance
    min_x = min(v[0] for v in verts)
    max_x = max(v[0] for v in verts)
    min_y = min(v[1] for v in verts)
    max_y = max(v[1] for v in verts)
    tolerance = max(max_x - min_x, max_y - min_y) * 0.1  # 10% margin

    for rv in [ridge_v1, ridge_v2]:
        if (rv[0] < min_x - tolerance or rv[0] > max_x + tolerance or
                rv[1] < min_y - tolerance or rv[1] > max_y + tolerance):
            return False

    return True


def _generate_hipped_roof_quadrangle(
    mesh: MeshData,
    verts: List[Tuple[float, float]],
    edge_geom: EdgeGeometry,
    eave_z: float,
    roof_height: float,
    roof_index: int,
    tan_pitch: float
) -> None:
    """
    Generate hipped roof geometry for a non-square quadrilateral.

    Creates 4 faces: 2 triangular hips + 2 trapezoidal slopes.

    Args:
        mesh: MeshData to add geometry to
        verts: Footprint vertices (x, y) - may be expanded for overhang
        edge_geom: Computed edge geometry for the (possibly expanded) footprint
        eave_z: Z coordinate at eave level (may be below wall_top_z if overhang)
        roof_height: Height of roof above wall_top_z (not above eave_z)
        roof_index: Roof texture pattern index
        tan_pitch: Tangent of roof pitch (calculated from original footprint)
    """
    distances = edge_geom.distances

    # Find edges with minimum distance (ridge endpoints)
    min_idx1 = min(_INDICES, key=lambda i: distances[i])
    min_idx1_next = _NEXT_INDICES[min_idx1]
    min_idx2 = _OPPOSITE_INDICES[min_idx1]
    min_idx2_next = _NEXT_INDICES[min_idx2]

    # tan_pitch is passed from caller (calculated from original footprint)
    # This ensures consistent slope regardless of overhang

    # Compute ridge vertices
    ridge_v1 = _get_ridge_vertex(verts[min_idx1], min_idx1, edge_geom, tan_pitch, eave_z)
    ridge_v2 = _get_ridge_vertex(verts[min_idx2], min_idx2, edge_geom, tan_pitch, eave_z)

    # Add footprint vertices at eave level
    v_indices = []
    for vx, vy in verts:
        v_indices.append(mesh.add_vertex(vx, vy, eave_z))

    # Add ridge vertices
    ridge_idx1 = mesh.add_vertex(ridge_v1[0], ridge_v1[1], ridge_v1[2])
    ridge_idx2 = mesh.add_vertex(ridge_v2[0], ridge_v2[1], ridge_v2[2])

    # Get UV range for roof texture
    v_min, v_max = get_roof_v_range(roof_index)

    # Compute UV coordinates for each face
    # For hipped roofs, we need to map UVs based on face geometry

    # Triangle 1 (hip at min_idx1): vertices min_idx1, min_idx1_next, ridge_v1
    _add_hip_triangle_with_uvs(
        mesh,
        v_indices[min_idx1],
        v_indices[min_idx1_next],
        ridge_idx1,
        edge_geom.lengths[min_idx1],
        edge_geom.distances[min_idx1],
        tan_pitch,
        v_min, v_max
    )

    # Trapezoid 1 (slope from min_idx1_next to min_idx2)
    _add_slope_quad_with_uvs(
        mesh,
        v_indices[min_idx1_next],
        v_indices[min_idx2],
        ridge_idx2,
        ridge_idx1,
        edge_geom.lengths[min_idx1_next],
        edge_geom.distances[min_idx1],
        edge_geom.distances[min_idx2],
        tan_pitch,
        v_min, v_max
    )

    # Triangle 2 (hip at min_idx2): vertices min_idx2, min_idx2_next, ridge_v2
    _add_hip_triangle_with_uvs(
        mesh,
        v_indices[min_idx2],
        v_indices[min_idx2_next],
        ridge_idx2,
        edge_geom.lengths[min_idx2],
        edge_geom.distances[min_idx2],
        tan_pitch,
        v_min, v_max
    )

    # Trapezoid 2 (slope from min_idx2_next to min_idx1)
    _add_slope_quad_with_uvs(
        mesh,
        v_indices[min_idx2_next],
        v_indices[min_idx1],
        ridge_idx1,
        ridge_idx2,
        edge_geom.lengths[min_idx2_next],
        edge_geom.distances[min_idx2],
        edge_geom.distances[min_idx1],
        tan_pitch,
        v_min, v_max
    )


def _add_hip_triangle_with_uvs(
    mesh: MeshData,
    v0: int, v1: int, v_ridge: int,
    edge_length: float,
    distance: float,
    tan_pitch: float,
    v_min: float, v_max: float
) -> None:
    """
    Add a triangular hip face with UV coordinates.

    UV mapping:
    - U: along the base edge, aspect ratio preserved
    - V: from eave (v_min) to ridge (v_max)

    The U span is calculated as the aspect ratio of the triangle (base/height),
    similar to how gabled roofs calculate their UV mapping. This ensures
    consistent texture scaling regardless of the footprint shape.

    Args:
        mesh: MeshData to add to
        v0, v1: Base edge vertex indices (at eave)
        v_ridge: Ridge vertex index
        edge_length: Length of base edge
        distance: Distance to ridge (perpendicular to edge, 2D)
        tan_pitch: Tangent of roof pitch (unused, kept for API compatibility)
        v_min, v_max: V range for roof texture
    """
    # UV coordinates
    # Base vertices at v_min (eave)
    # Ridge vertex at v_max (ridge), centered on U

    # Use 2D aspect ratio for UV mapping (similar to gabled roofs)
    # u_span = edge_length / distance gives the aspect ratio of the triangle
    u_span = edge_length / distance if distance > 0.01 else 1.0

    uv0 = mesh.add_uv(0.0, v_min)
    uv1 = mesh.add_uv(u_span, v_min)
    uv_ridge = mesh.add_uv(u_span / 2.0, v_max)

    # Add triangle (CCW winding for upward normal)
    mesh.add_triangle_with_uvs(v0, v1, v_ridge, uv0, uv1, uv_ridge)


def _add_slope_quad_with_uvs(
    mesh: MeshData,
    v0: int, v1: int, v2: int, v3: int,
    edge_length: float,
    dist1: float, dist2: float,
    tan_pitch: float,
    v_min: float, v_max: float
) -> None:
    """
    Add a trapezoidal slope face with UV coordinates.

    Vertex order (CCW): v0 (eave), v1 (eave), v2 (ridge), v3 (ridge)

    UV mapping:
    - U: along the base edge, aspect ratio preserved
    - V: from eave (v_min) to ridge (v_max)

    The U span is calculated using 2D distances (similar to gabled roofs),
    ensuring consistent texture scaling regardless of the footprint shape.
    The trapezoidal shape is preserved by offsetting ridge UVs proportionally.

    Args:
        mesh: MeshData to add to
        v0, v1: Base edge vertex indices (at eave)
        v2, v3: Ridge edge vertex indices
        edge_length: Length of base edge
        dist1, dist2: Distances from ridge vertices to their edges (2D)
        tan_pitch: Tangent of roof pitch (unused, kept for API compatibility)
        v_min, v_max: V range for roof texture
    """
    # Use 2D distances for UV mapping (similar to gabled roofs)
    max_dist = max(dist1, dist2)
    if max_dist < 0.01:
        max_dist = 1.0

    # U span based on aspect ratio (edge_length / perpendicular_distance)
    u_span = edge_length / max_dist

    # UV offsets for ridge vertices (trapezoidal shape)
    # The ridge is shorter than the eave, so we offset inward proportionally
    # offset = (dist / max_dist) * (u_span / 2) gives the inward offset
    u_offset1 = (dist1 / max_dist) * (u_span / 2.0)
    u_offset2 = (dist2 / max_dist) * (u_span / 2.0)

    uv0 = mesh.add_uv(0.0, v_min)
    uv1 = mesh.add_uv(u_span, v_min)
    uv2 = mesh.add_uv(u_span - u_offset2, v_max)
    uv3 = mesh.add_uv(u_offset1, v_max)

    # Add quad (CCW winding)
    mesh.add_quad_with_uvs(v0, v1, v2, v3, uv0, uv1, uv2, uv3)


def _generate_pyramidal_roof(
    mesh: MeshData,
    verts: List[Tuple[float, float]],
    eave_z: float,
    apex_z: float,
    roof_index: int
) -> None:
    """
    Generate pyramidal roof for square footprints.

    Creates 4 triangular faces meeting at a central apex.

    Args:
        mesh: MeshData to add geometry to
        verts: Footprint vertices (x, y)
        eave_z: Z coordinate at eave level
        apex_z: Z coordinate of apex
        roof_index: Roof texture pattern index
    """
    # Compute center (apex location)
    center_x = sum(v[0] for v in verts) / 4.0
    center_y = sum(v[1] for v in verts) / 4.0

    # Add footprint vertices at eave level
    v_indices = []
    for vx, vy in verts:
        v_indices.append(mesh.add_vertex(vx, vy, eave_z))

    # Add apex vertex
    apex_idx = mesh.add_vertex(center_x, center_y, apex_z)

    # Get UV range
    v_min, v_max = get_roof_v_range(roof_index)

    # Create 4 triangular faces
    for i in range(4):
        next_i = (i + 1) % 4

        # Edge length for UV
        dx = verts[next_i][0] - verts[i][0]
        dy = verts[next_i][1] - verts[i][1]
        edge_len = math.sqrt(dx * dx + dy * dy)

        # Distance to apex (for UV scaling)
        mid_x = (verts[i][0] + verts[next_i][0]) / 2.0
        mid_y = (verts[i][1] + verts[next_i][1]) / 2.0
        dist_to_apex = math.sqrt((center_x - mid_x)**2 + (center_y - mid_y)**2)

        # UV coordinates
        u_span = edge_len / dist_to_apex if dist_to_apex > 0.01 else 1.0

        uv0 = mesh.add_uv(0.0, v_min)
        uv1 = mesh.add_uv(u_span, v_min)
        uv_apex = mesh.add_uv(u_span / 2.0, v_max)

        # Add triangle (CCW winding)
        mesh.add_triangle_with_uvs(
            v_indices[i], v_indices[next_i], apex_idx,
            uv0, uv1, uv_apex
        )


def _duplicate_faces_reversed(
    mesh: MeshData,
    start_idx: int,
    end_idx: int
) -> None:
    """
    Duplicate faces with reversed winding for double-sided visibility.

    Args:
        mesh: MeshData to modify
        start_idx: Index of first face to duplicate (inclusive)
        end_idx: Index of last face to duplicate (exclusive)
    """
    faces_to_dup = mesh.faces[start_idx:end_idx]
    uvs_to_dup = mesh.face_uvs[start_idx:end_idx] if len(mesh.face_uvs) >= end_idx else []

    for i, face in enumerate(faces_to_dup):
        reversed_face = face[::-1]
        mesh.faces.append(reversed_face)

        if i < len(uvs_to_dup):
            reversed_uvs = uvs_to_dup[i][::-1]
            mesh.face_uvs.append(reversed_uvs)


# =============================================================================
# PUBLIC API
# =============================================================================

def generate_hipped_roof_lod0(building: BuildingRecord) -> MeshData:
    """
    Generate LOD0 hipped roof with overhang and double-sided faces.

    Args:
        building: Building record

    Returns:
        MeshData with roof geometry
    """
    config = HippedRoofConfig(
        overhang=ROOF_OVERHANG_LOD0,
        double_sided_roof=True
    )
    return generate_hipped_roof(building, config)


def generate_hipped_roof_lod1(building: BuildingRecord) -> MeshData:
    """
    Generate LOD1 hipped roof without overhang.

    Args:
        building: Building record

    Returns:
        MeshData with roof geometry
    """
    config = HippedRoofConfig(
        overhang=0.0,
        double_sided_roof=True
    )
    return generate_hipped_roof(building, config)
