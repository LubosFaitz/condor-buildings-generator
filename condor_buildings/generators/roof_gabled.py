"""
Gabled roof generator for Condor Buildings Generator.

Generates gabled roofs using an OBB-based approach optimized for rectangles:
1. Compute the OBB (oriented bounding box) along the ridge direction
2. Build roof slopes from expanded footprint (with overhang)
3. Roof meets wall at original footprint boundary (slope calculation)
4. Double-sided roof faces for underside visibility

Design assumptions (Milestone A):
- Footprint has exactly 4 vertices (rectangle)
- Footprint is convex and has no holes
- This eliminates all roof-mesh self-intersection issues

Geometry v4 (v0.2.4):
- Roof is ONLY the 2 slope quads (no gable end caps)
- Gable walls are now part of walls.py (pentagonal walls)
- Overhang extends beyond wall, with natural Z drop due to slope
- Double-sided roof faces for underside visibility

DESIGN NOTE: Roof underside visibility
We use double-sided faces (duplicated with reversed winding) instead of
soffit/fascia connector geometry. This achieves:
- Same visual result (roof visible from below)
- Fewer vertices (reuse existing roof vertices)
- Simpler geometry (no edge seams at wall-roof junction)
- Better performance for flight sim viewing distances
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
import math
import logging
import random

from ..models.geometry import Point2D, Point3D, Polygon, BBox
from ..models.mesh import MeshData
from ..models.building import BuildingRecord, RoofDirectionSource
from ..processing.footprint import compute_longest_edge_axis, compute_obb, get_unique_vertex_count
from ..config import ROOF_OVERHANG_LOD0, ROOF_PITCH_MIN, ROOF_PITCH_MAX, DEBUG_GABLED_ROOFS, GABLE_HEIGHT_FIXED
from .uv_mapping import select_building_variations, compute_roof_slope_uvs

logger = logging.getLogger(__name__)


@dataclass
class GabledRoofConfig:
    """Configuration for gabled roof generation."""
    overhang: float = 0.0  # Roof overhang in meters
    include_gable_walls: bool = False  # DEPRECATED: Gable walls now in walls.py
    double_sided_roof: bool = True  # Duplicate roof faces with reversed winding for underside visibility


def generate_gabled_roof(
    building: BuildingRecord,
    config: Optional[GabledRoofConfig] = None
) -> MeshData:
    """
    Generate gabled roof mesh for a building using OBB-based approach.

    Geometry v3 design:
    - Roof slopes are built from expanded footprint (with overhang)
    - At original footprint boundary, roof_z = wall_top_z (perfect contact)
    - At overhang edge, roof_z < wall_top_z (natural drop due to slope)
    - Gable walls use original footprint (coplanar with rectangular walls)
    - Double-sided roof faces for underside visibility (no soffit geometry)

    Args:
        building: Building record with footprint and heights
        config: Optional configuration

    Returns:
        MeshData with roof geometry
    """
    if config is None:
        config = GabledRoofConfig()

    mesh = MeshData(osm_id=building.osm_id)

    footprint = building.footprint
    outer_ring = footprint.outer_ring

    # Get ridge direction (from OSM or longest axis)
    ridge_direction = _get_ridge_direction(building, outer_ring)

    # Compute OBB along ridge direction
    obb = compute_obb(outer_ring, ridge_direction)

    # Phase 1: Fixed gable height of 3.0m
    # Pitch becomes a consequence: pitch = atan(GABLE_HEIGHT_FIXED / half_width)
    # OBB 'width' = dimension perpendicular to ridge (the span)
    original_half_width = obb['width'] / 2.0
    roof_half_width = original_half_width + config.overhang  # Include overhang

    # Fixed ridge height (Phase 1 requirement)
    ridge_height = GABLE_HEIGHT_FIXED
    building.ridge_height_m = ridge_height

    eave_z = building.wall_top_z
    ridge_z = eave_z + ridge_height

    # Derived pitch (for logging/reference only, not used in calculation)
    if original_half_width > 0.01:
        derived_pitch_deg = math.degrees(math.atan(ridge_height / original_half_width))
    else:
        derived_pitch_deg = 45.0  # fallback

    # Compute slope for roof Z calculation
    # slope = tan(pitch) = ridge_height / original_half_width
    # At u = original_half_width: z = wall_top_z (roof touches wall)
    # At u = roof_half_width (with overhang): z = wall_top_z - tan(pitch) * overhang (drops below)
    if original_half_width > 0.01:
        slope = ridge_height / original_half_width
    else:
        slope = 0.0

    # Get texture variations for this building (deterministic)
    roof_index, _ = select_building_variations(building.seed)

    # Record face count before roof generation (for double-sided duplication)
    faces_before_roof = len(mesh.faces)
    uvs_before_roof = len(mesh.face_uvs)

    # Generate the OBB-based roof slopes
    roof_stats = _generate_obb_roof_v3(
        mesh,
        obb,
        ridge_direction,
        ridge_z,
        slope,
        config.overhang,
        roof_index
    )

    faces_after_roof = len(mesh.faces)
    roof_face_count = faces_after_roof - faces_before_roof

    # Duplicate roof faces with reversed winding for underside visibility
    if config.double_sided_roof and roof_face_count > 0:
        _duplicate_faces_reversed(mesh, faces_before_roof, faces_after_roof, uvs_before_roof)

    faces_after_double = len(mesh.faces)

    # NOTE: Gable walls removed in v0.2.4
    # Gable walls are now generated as pentagonal walls in walls.py
    # The include_gable_walls option is deprecated

    # Debug logging for gabled roof generation
    if DEBUG_GABLED_ROOFS:
        vertex_count = get_unique_vertex_count(outer_ring)
        ridge_source = building.roof_direction_source.value if building.roof_direction_source else "computed"
        roof_half_width_debug = original_half_width + config.overhang
        corner_z = ridge_z - slope * roof_half_width_debug if slope > 0 else eave_z
        logger.info(
            f"GABLED DEBUG [{building.osm_id}]: "
            f"verts={vertex_count}, "
            f"OBB={obb['length']:.1f}x{obb['width']:.1f}m, "
            f"ridge_dir={ridge_direction:.1f}° (src={ridge_source}), "
            f"pitch={derived_pitch_deg:.1f}° (derived), "
            f"gable_h={GABLE_HEIGHT_FIXED:.1f}m (fixed), "
            f"wall_top_z={eave_z:.2f}m, "
            f"ridge_z={ridge_z:.2f}m, "
            f"ridge_h={ridge_height:.2f}m, "
            f"u_wall={original_half_width:.2f}m, "
            f"slope={slope:.4f}, "
            f"overhang={config.overhang:.2f}m, "
            f"corner_z={corner_z:.2f}m (drop={eave_z - corner_z:.2f}m), "
            f"roof_faces={roof_face_count}->{faces_after_double - faces_before_roof} (double-sided)"
        )

    return mesh


def _get_ridge_direction(building: BuildingRecord, ring: List[Point2D]) -> float:
    """
    Get ridge direction for the building.

    For a realistic gabled roof:
    - The RIDGE should run along the LONG axis of the footprint
    - The SPAN (width perpendicular to ridge) should be the SHORT dimension
    - This ensures roof height = (short_side/2) * tan(pitch), which looks natural

    Returns:
        Direction in degrees (0 = East, CCW positive)
    """
    if building.roof_direction_deg is not None:
        return building.roof_direction_deg

    # Compute from longest edge (side of building)
    # For a rectangle, the longest edge IS the long axis
    # The RIDGE should be PARALLEL to the longest edge (not perpendicular!)
    # This way the span uses the short dimension
    ridge_direction = compute_longest_edge_axis(ring)

    building.roof_direction_deg = ridge_direction
    building.roof_direction_source = RoofDirectionSource.LONGEST_AXIS

    return ridge_direction


def _get_roof_pitch(building: BuildingRecord) -> float:
    """
    Get roof pitch, potentially randomized around the base pitch.

    Uses building seed for deterministic randomization.
    """
    # Use seed for deterministic variation
    rng = random.Random(building.seed)

    # Vary pitch around default by ±10 degrees
    base_pitch = building.roof_pitch_deg
    variation = rng.uniform(-10, 10)
    pitch = base_pitch + variation

    # Clamp to valid range
    pitch = max(ROOF_PITCH_MIN, min(ROOF_PITCH_MAX, pitch))

    return pitch


def _generate_obb_roof_v3(
    mesh: MeshData,
    obb: dict,
    ridge_direction_deg: float,
    ridge_z: float,
    slope: float,
    overhang: float,
    roof_index: int = 0
) -> dict:
    """
    Generate gabled roof slopes with proper slope-based Z calculation and UV mapping.

    Geometry v3 design:
    - Roof corners use expanded footprint (with overhang)
    - Z at each corner is calculated by slope from ridge
    - At original footprint boundary: z = wall_top_z (eave contact)
    - At overhang edge: z < wall_top_z (natural drop)

    Only generates the two slope faces (quads). Gable end triangles are
    part of the walls, not the roof, to ensure coplanarity.

    UV mapping:
    - V: ridge (v_max) to eave (v_min), full slice
    - U: along ridge direction, preserves aspect ratio

    Geometry layout (looking down from above, ridge_direction points right):

        c3 -------- r0 -------- c0      <- Z at corners = ridge_z - slope * roof_half_width
        |           |           |
        |   LEFT    |   RIGHT   |
        |   SLOPE   |   SLOPE   |
        |           |           |
        c2 -------- r1 -------- c1      <- Ridge at Z = ridge_z

    Args:
        mesh: MeshData to add to
        obb: Oriented bounding box data
        ridge_direction_deg: Ridge direction in degrees
        ridge_z: Ridge elevation
        slope: Roof slope (ridge_height / original_half_width)
        overhang: Overhang distance beyond original footprint
        roof_index: Roof texture pattern index [0..5]

    Returns:
        Dictionary with roof generation stats
    """
    rad = math.radians(ridge_direction_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    # Original footprint dimensions (without overhang)
    original_half_length = obb['length'] / 2.0
    original_half_width = obb['width'] / 2.0

    # Roof footprint dimensions (with overhang)
    roof_half_length = original_half_length + overhang
    roof_half_width = original_half_width + overhang

    center_x = obb['center_x']
    center_y = obb['center_y']

    def obb_to_world(along: float, across: float) -> Tuple[float, float]:
        """Convert OBB-relative coordinates to world coordinates."""
        x = center_x + along * cos_r - across * sin_r
        y = center_y + along * sin_r + across * cos_r
        return (x, y)

    # Calculate corner Z using slope
    # Z = ridge_z - slope * |across|
    # At roof_half_width (overhang edge), this gives the lowered eave
    corner_z = ridge_z - slope * roof_half_width

    # Corner positions (expanded footprint for roof)
    c0 = obb_to_world(-roof_half_length, -roof_half_width)  # back-right
    c1 = obb_to_world(roof_half_length, -roof_half_width)   # front-right
    c2 = obb_to_world(roof_half_length, roof_half_width)    # front-left
    c3 = obb_to_world(-roof_half_length, roof_half_width)   # back-left

    # Ridge endpoints (expanded length, but at center across)
    r0 = obb_to_world(-roof_half_length, 0)  # Back ridge point
    r1 = obb_to_world(roof_half_length, 0)   # Front ridge point

    # Add vertices for roof corners (at calculated corner_z, lower than wall_top)
    v_c0 = mesh.add_vertex(c0[0], c0[1], corner_z)
    v_c1 = mesh.add_vertex(c1[0], c1[1], corner_z)
    v_c2 = mesh.add_vertex(c2[0], c2[1], corner_z)
    v_c3 = mesh.add_vertex(c3[0], c3[1], corner_z)

    # Add vertices for ridge (at ridge_z)
    v_r0 = mesh.add_vertex(r0[0], r0[1], ridge_z)
    v_r1 = mesh.add_vertex(r1[0], r1[1], ridge_z)

    # Compute UV coordinates for roof slopes
    # Roof length = full ridge length (2 * roof_half_length)
    # Roof width = distance from ridge to eave (roof_half_width)
    roof_length_m = 2.0 * roof_half_length
    roof_width_m = roof_half_width

    uvs = compute_roof_slope_uvs(roof_length_m, roof_width_m, roof_index)
    # uvs: [0]=eave-front, [1]=eave-back, [2]=ridge-back, [3]=ridge-front

    # Right slope: c1 (front eave), c0 (back eave), r0 (back ridge), r1 (front ridge)
    uv_r_c1 = mesh.add_uv(uvs[0][0], uvs[0][1])  # eave-front
    uv_r_c0 = mesh.add_uv(uvs[1][0], uvs[1][1])  # eave-back
    uv_r_r0 = mesh.add_uv(uvs[2][0], uvs[2][1])  # ridge-back
    uv_r_r1 = mesh.add_uv(uvs[3][0], uvs[3][1])  # ridge-front

    # Left slope: c3 (back eave), r0 (back ridge), r1 (front ridge), c2 (front eave)
    uv_l_c3 = mesh.add_uv(uvs[1][0], uvs[1][1])  # eave-back
    uv_l_r0 = mesh.add_uv(uvs[2][0], uvs[2][1])  # ridge-back
    uv_l_r1 = mesh.add_uv(uvs[3][0], uvs[3][1])  # ridge-front
    uv_l_c2 = mesh.add_uv(uvs[0][0], uvs[0][1])  # eave-front

    # Create roof slope faces with UVs (CCW winding for upward-facing normals)
    # Right slope (c0, c1 side): normal points up and to the right
    mesh.add_quad_with_uvs(v_c1, v_c0, v_r0, v_r1, uv_r_c1, uv_r_c0, uv_r_r0, uv_r_r1)

    # Left slope (c2, c3 side): normal points up and to the left
    mesh.add_quad_with_uvs(v_c3, v_r0, v_r1, v_c2, uv_l_c3, uv_l_r0, uv_l_r1, uv_l_c2)

    # NOTE: Gable end triangles removed in v0.2.4
    # The roof is now "open" at the gable ends - the pentagonal walls
    # in walls.py close the building visually

    return {
        'corner_z': corner_z,
        'roof_half_length': roof_half_length,
        'roof_half_width': roof_half_width,
    }


def _generate_gable_walls_v3(
    mesh: MeshData,
    ring: List[Point2D],
    obb: dict,
    ridge_direction_deg: float,
    eave_z: float,
    ridge_z: float
) -> None:
    """
    Generate triangular gable walls coplanar with rectangular walls.

    Geometry v3 design:
    - Gable walls use ORIGINAL footprint vertices (no overhang)
    - Triangle base is at eave_z (same as top of rectangular walls)
    - Triangle apex is at ridge_z on the ridge line
    - This ensures gable wall is coplanar with rectangular wall below

    The gable wall and rectangular wall form a single flat plane:
    - Rectangular wall: floor_z to eave_z
    - Triangular gable: eave_z to ridge_z
    - Both share the same footprint edge, same plane

    Args:
        mesh: MeshData to add to
        ring: Footprint vertices (ORIGINAL, not expanded)
        obb: OBB data with center and dimensions
        ridge_direction_deg: Ridge direction in degrees
        eave_z: Eave elevation (top of rectangular walls = wall_top_z)
        ridge_z: Ridge elevation
    """
    rad = math.radians(ridge_direction_deg)
    ridge_dx = math.cos(rad)
    ridge_dy = math.sin(rad)

    center_x = obb['center_x']
    center_y = obb['center_y']

    n = len(ring)
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1

    for i in range(n):
        j = (i + 1) % n

        p0 = ring[i]
        p1 = ring[j]

        # Edge vector
        edge_dx = p1.x - p0.x
        edge_dy = p1.y - p0.y
        edge_len = math.sqrt(edge_dx * edge_dx + edge_dy * edge_dy)

        if edge_len < 0.1:
            continue

        # Normalize edge direction
        edge_dx_n = edge_dx / edge_len
        edge_dy_n = edge_dy / edge_len

        # Check if edge is approximately PARALLEL to ridge direction
        # Parallel edges are gable ends (short sides of building)
        dot = abs(edge_dx_n * ridge_dx + edge_dy_n * ridge_dy)

        # Edge is gable end if parallel to ridge (dot > 0.7 means within ~45°)
        if dot > 0.7:
            # Midpoint of the edge
            mid_x = (p0.x + p1.x) / 2
            mid_y = (p0.y + p1.y) / 2

            # Project midpoint onto the ridge line
            to_mid_x = mid_x - center_x
            to_mid_y = mid_y - center_y
            along_dist = to_mid_x * ridge_dx + to_mid_y * ridge_dy

            # Ridge point on the ridge line
            ridge_x = center_x + along_dist * ridge_dx
            ridge_y = center_y + along_dist * ridge_dy

            # Create triangular gable wall
            # Vertices at ORIGINAL footprint corners (eave_z) and ridge point (ridge_z)
            # This makes the triangle coplanar with the rectangular wall below
            v0 = mesh.add_vertex(p0.x, p0.y, eave_z)
            v1 = mesh.add_vertex(p1.x, p1.y, eave_z)
            vr = mesh.add_vertex(ridge_x, ridge_y, ridge_z)

            # Determine outward normal direction
            perp_x = -ridge_dy  # perpendicular to ridge
            perp_y = ridge_dx
            perp_dist = to_mid_x * perp_x + to_mid_y * perp_y

            # Winding for outward-facing normal
            if perp_dist > 0:
                mesh.add_triangle(v0, v1, vr)
            else:
                mesh.add_triangle(v1, v0, vr)


def _duplicate_faces_reversed(
    mesh: MeshData,
    start_idx: int,
    end_idx: int,
    uv_start_idx: int = 0
) -> None:
    """
    Duplicate faces with reversed winding for double-sided visibility.

    This creates underside visibility for roof faces without adding vertices.
    Only face indices are duplicated with reversed order.
    Also duplicates face_uvs if present.

    DESIGN NOTE: We use this instead of soffit/fascia geometry because:
    - Same visual result (roof visible from below)
    - Fewer vertices (reuse existing roof vertices)
    - Simpler geometry (no edge seams at wall-roof junction)
    - Better performance for flight sim viewing distances

    Args:
        mesh: MeshData to modify
        start_idx: Index of first face to duplicate (inclusive)
        end_idx: Index of last face to duplicate (exclusive)
        uv_start_idx: Index of first face_uv to duplicate (if UVs present)
    """
    # Get faces to duplicate
    faces_to_dup = mesh.faces[start_idx:end_idx]

    # Get face_uvs to duplicate (if present)
    has_uvs = len(mesh.face_uvs) >= end_idx
    if has_uvs:
        face_uvs_to_dup = mesh.face_uvs[uv_start_idx:uv_start_idx + (end_idx - start_idx)]
    else:
        face_uvs_to_dup = []

    # Add reversed versions (same vertices, reversed winding = flipped normal)
    for i, face in enumerate(faces_to_dup):
        reversed_face = face[::-1]  # Reverse vertex order
        mesh.faces.append(reversed_face)

        # Also reverse UV indices if present
        if has_uvs and i < len(face_uvs_to_dup):
            reversed_uvs = face_uvs_to_dup[i][::-1]
            mesh.face_uvs.append(reversed_uvs)


# =============================================================================
# DEPRECATED: _generate_roof_underside removed in v0.2.3
# Replaced by double-sided faces via _duplicate_faces_reversed()
# =============================================================================


def generate_gabled_roof_lod0(building: BuildingRecord) -> MeshData:
    """
    Generate LOD0 gabled roof with overhang and double-sided faces.

    Geometry v3:
    - Roof has 0.5m overhang beyond walls
    - Double-sided faces for underside visibility
    - Gable walls coplanar with rectangular walls
    """
    config = GabledRoofConfig(
        overhang=ROOF_OVERHANG_LOD0,
        include_gable_walls=True,
        double_sided_roof=True  # Visible from below
    )
    return generate_gabled_roof(building, config)


def generate_gabled_roof_lod1(building: BuildingRecord) -> MeshData:
    """
    Generate LOD1 gabled roof without overhang.

    Geometry v3:
    - No overhang (roof edge at wall boundary)
    - Still double-sided for consistency
    - Gable walls coplanar with rectangular walls
    """
    config = GabledRoofConfig(
        overhang=0.0,
        include_gable_walls=True,
        double_sided_roof=True  # Keep double-sided for consistency
    )
    return generate_gabled_roof(building, config)
