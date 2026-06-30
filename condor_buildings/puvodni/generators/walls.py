"""
Wall mesh generator for Condor Buildings Generator.

Generates vertical wall faces from building footprint,
from floor_z up to wall_top_z (eave height).

For gabled roofs, gable end walls are pentagonal (extend to ridge_z).
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
import math

from ..models.geometry import Point2D, Polygon
from ..models.mesh import MeshData
from ..models.building import BuildingRecord, BuildingCategory
from .uv_mapping import (
    select_building_variations,
    compute_wall_quad_uvs,
    compute_gable_triangle_uvs,
    compute_pentagon_wall_uvs,
    compute_multi_floor_wall_uvs,
    compute_sidewall_continuous_uvs,
    select_highrise_region,
    compute_highrise_wall_uvs,
)
from ..config import DEFAULT_FLOOR_HEIGHT, HIGHRISE_FLOORS_PER_REGION, HIGHRISE_UPPER_FLOORS


@dataclass
class WallGeneratorConfig:
    """Configuration for wall generation."""
    include_bottom_cap: bool = False  # Usually false (underground)


def generate_walls(
    building: BuildingRecord,
    config: Optional[WallGeneratorConfig] = None
) -> MeshData:
    """
    Generate wall mesh for a building.

    Creates vertical quad faces for:
    - Outer ring walls (facing outward)
    - Hole walls (facing inward into holes)

    Args:
        building: Building record with footprint and heights
        config: Optional configuration

    Returns:
        MeshData with wall geometry
    """
    if config is None:
        config = WallGeneratorConfig()

    mesh = MeshData(osm_id=building.osm_id)

    floor_z = building.floor_z
    top_z = building.wall_top_z

    # Get texture variations for this building (deterministic)
    _, facade_index = select_building_variations(building.seed)

    # Industrial_Atlas has no roof section — facades span full V 0.0-1.0
    # so V values (normally 0.0-0.75) must be scaled up by 1/0.75
    _INDUSTRIAL_V_SCALE = 1.0 / 0.75

    # Generate outer walls
    _generate_ring_walls(
        mesh,
        building.footprint.outer_ring,
        floor_z,
        top_z,
        is_outer=True,
        facade_index=facade_index,
        building_floors=building.floors,
        v_scale=_INDUSTRIAL_V_SCALE,
    )

    # Generate hole walls (facing inward)
    for hole in building.footprint.holes:
        _generate_ring_walls(
            mesh,
            hole,
            floor_z,
            top_z,
            is_outer=False,
            facade_index=facade_index,
            building_floors=building.floors,
            v_scale=_INDUSTRIAL_V_SCALE,
        )

    return mesh


def generate_walls_for_gabled(
    building: BuildingRecord,
    ridge_direction_deg: float,
    ridge_z: float,
    obb_center: Tuple[float, float],
    config: Optional[WallGeneratorConfig] = None,
    separate_gable_for_single_floor: bool = True
) -> MeshData:
    """
    Generate walls for a gabled roof building.

    For gabled buildings:
    - Side walls (perpendicular to ridge): rectangular (floor_z → wall_top_z)
    - Gable end walls (parallel to ridge): pentagonal (floor_z → ridge_z)

    Phase 1 geometry requirement:
    - For 1-floor buildings: gable triangle is a SEPARATE face (for UV mapping)
    - For multi-floor buildings: pentagon is acceptable (single face)

    The separated gable wall has:
    - Rectangular face (floor_z → wall_top_z)
    - Triangular gable face (wall_top_z → ridge_z)
    Both faces are coplanar and share the edge at wall_top_z.

    Args:
        building: Building record with footprint and heights
        ridge_direction_deg: Ridge direction in degrees (0 = East, CCW)
        ridge_z: Ridge elevation (top of gable)
        obb_center: Center of the OBB (x, y) for projecting ridge point
        config: Optional configuration
        separate_gable_for_single_floor: If True, 1-floor buildings get
            separated rectangle + triangle instead of pentagon

    Returns:
        MeshData with wall geometry
    """
    if config is None:
        config = WallGeneratorConfig()

    mesh = MeshData(osm_id=building.osm_id)

    floor_z = building.floor_z
    wall_top_z = building.wall_top_z
    ring = building.footprint.outer_ring

    # Get texture variations for this building (deterministic)
    _, facade_index = select_building_variations(building.seed)

    # Ridge direction vector
    rad = math.radians(ridge_direction_deg)
    ridge_dx = math.cos(rad)
    ridge_dy = math.sin(rad)

    n = len(ring)
    if n < 3:
        return mesh

    # Handle closed ring
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

        if edge_len < 0.01:
            continue

        # Normalize edge
        edge_dx_n = edge_dx / edge_len
        edge_dy_n = edge_dy / edge_len

        # Check if edge is perpendicular to ridge (gable end) or parallel (side)
        # dot product: 1 = parallel, 0 = perpendicular
        dot = abs(edge_dx_n * ridge_dx + edge_dy_n * ridge_dy)

        if dot < 0.3:
            # GABLE END: Edge is perpendicular to ridge
            # These are the short walls where the triangular gable is visible
            # Phase 1: For 1-floor buildings, separate rectangle + triangle for UV mapping
            if separate_gable_for_single_floor and building.floors == 1:
                _generate_separated_gable_wall(
                    mesh, p0, p1, floor_z, wall_top_z, ridge_z,
                    ridge_dx, ridge_dy, obb_center, edge_len, facade_index
                )
            else:
                _generate_pentagonal_gable_wall(
                    mesh, p0, p1, floor_z, wall_top_z, ridge_z,
                    ridge_dx, ridge_dy, obb_center, edge_len, facade_index,
                    building.floors
                )
        else:
            # SIDE WALL: Edge is parallel to ridge - generate rectangular wall
            # These are the long walls under the roof slopes
            _generate_side_wall_with_uvs(
                mesh, p0, p1, floor_z, wall_top_z, edge_len, facade_index,
                building.floors
            )

    return mesh


def _generate_side_wall_with_uvs(
    mesh: MeshData,
    p0: Point2D,
    p1: Point2D,
    floor_z: float,
    wall_top_z: float,
    edge_len: float,
    facade_index: int,
    building_floors: int
) -> None:
    """
    Generate a rectangular side wall as a SINGLE quad with continuous UV mapping.

    NO per-floor splitting - uses UV interpolation for multi-story appearance.
    The GPU interpolates UVs linearly across the quad, so:
    - 1 floor (3m): Maps to ground section (doors + windows)
    - 2 floors (6m): Bottom half maps to ground, top half to upper section

    This reduces geometry while maintaining correct texture appearance.

    Args:
        mesh: MeshData to add to
        p0, p1: Edge endpoints
        floor_z: Floor elevation
        wall_top_z: Wall top elevation
        edge_len: Wall width in meters
        facade_index: Facade style index [0..11]
        building_floors: Number of floors (1 or 2 for gabled)
    """
    # Get continuous UVs for single quad
    uvs = compute_sidewall_continuous_uvs(edge_len, building_floors, facade_index)

    # Create 4 vertices for complete wall (NOT per floor)
    bl = mesh.add_vertex(p0.x, p0.y, floor_z)
    br = mesh.add_vertex(p1.x, p1.y, floor_z)
    tr = mesh.add_vertex(p1.x, p1.y, wall_top_z)
    tl = mesh.add_vertex(p0.x, p0.y, wall_top_z)

    # Add UV coordinates
    uv_bl = mesh.add_uv(uvs[0][0], uvs[0][1])
    uv_br = mesh.add_uv(uvs[1][0], uvs[1][1])
    uv_tr = mesh.add_uv(uvs[2][0], uvs[2][1])
    uv_tl = mesh.add_uv(uvs[3][0], uvs[3][1])

    # Add single quad with UVs
    mesh.add_quad_with_uvs(bl, br, tr, tl, uv_bl, uv_br, uv_tr, uv_tl)


def _generate_pentagonal_gable_wall(
    mesh: MeshData,
    p0: Point2D,
    p1: Point2D,
    floor_z: float,
    wall_top_z: float,
    ridge_z: float,
    ridge_dx: float,
    ridge_dy: float,
    obb_center: Tuple[float, float],
    edge_len: float,
    facade_index: int,
    building_floors: int
) -> None:
    """
    Generate a pentagonal gable wall as a single polygon face with UVs.

    The wall has 5 vertices in CCW order:
    - v0: p0 at floor_z (bottom left)
    - v1: p1 at floor_z (bottom right)
    - v2: p1 at wall_top_z (top right, eave corner)
    - v3: midpoint at ridge_z (peak/apex)
    - v4: p0 at wall_top_z (top left, eave corner)

    Generated as a single pentagon face (not triangulated).
    This is better for UV mapping and texture application.

    Args:
        mesh: MeshData to add to
        p0, p1: Edge endpoints
        floor_z: Floor elevation
        wall_top_z: Wall top elevation (eave height)
        ridge_z: Ridge elevation
        ridge_dx, ridge_dy: Ridge direction unit vector
        obb_center: OBB center for ridge projection
        edge_len: Wall width in meters
        facade_index: Facade style index [0..11]
        building_floors: Number of floors
    """
    # Calculate ridge point - directly above the midpoint of the edge
    # This ensures the gable wall is coplanar (flat face)
    mid_x = (p0.x + p1.x) / 2
    mid_y = (p0.y + p1.y) / 2

    # Ridge point is directly above the edge midpoint at ridge_z
    ridge_x = mid_x
    ridge_y = mid_y

    # Create vertices
    v0 = mesh.add_vertex(p0.x, p0.y, floor_z)      # bottom left
    v1 = mesh.add_vertex(p1.x, p1.y, floor_z)      # bottom right
    v2 = mesh.add_vertex(p1.x, p1.y, wall_top_z)   # top right (eave)
    v3 = mesh.add_vertex(ridge_x, ridge_y, ridge_z)  # ridge peak (apex)
    v4 = mesh.add_vertex(p0.x, p0.y, wall_top_z)   # top left (eave)

    # Compute UVs for pentagon
    uvs = compute_pentagon_wall_uvs(edge_len, facade_index, building_floors)

    # Add UV coordinates
    uv0 = mesh.add_uv(uvs[0][0], uvs[0][1])  # bottom left
    uv1 = mesh.add_uv(uvs[1][0], uvs[1][1])  # bottom right
    uv2 = mesh.add_uv(uvs[2][0], uvs[2][1])  # top right (eave)
    uv3 = mesh.add_uv(uvs[3][0], uvs[3][1])  # apex
    uv4 = mesh.add_uv(uvs[4][0], uvs[4][1])  # top left (eave)

    # Outward-facing winding for a CCW-normalized footprint.
    #
    # The vertex order [v0,v1,v2,v3,v4] follows the outer ring's p0→p1 direction,
    # which yields a horizontal normal of (edge_dy, -edge_dx) — i.e. the outward
    # side of a CCW ring (the very same convention the side walls use). The OSM
    # parser guarantees CCW outer rings (normalize_ring_orientation,
    # should_be_ccw=True), so this winding is always outward.
    #
    # BUGFIX: the previous OBB-based test projected (mid - obb_center) onto
    # perp=(-ridge_dy, ridge_dx) — the gable EDGE axis — but a gable wall's normal
    # runs along the RIDGE axis, not the edge axis. For centred/symmetric buildings
    # both gable ends projected to ~0 and were wound INWARD, so Condor back-face
    # culled them and they looked like missing walls (Andy's UK3 report).
    mesh.add_polygon_with_uvs([v0, v1, v2, v3, v4], [uv0, uv1, uv2, uv3, uv4])


def _generate_separated_gable_wall(
    mesh: MeshData,
    p0: Point2D,
    p1: Point2D,
    floor_z: float,
    wall_top_z: float,
    ridge_z: float,
    ridge_dx: float,
    ridge_dy: float,
    obb_center: Tuple[float, float],
    edge_len: float,
    facade_index: int
) -> None:
    """
    Generate a gable wall as TWO separate faces: rectangle + triangle, with UVs.

    Phase 1 geometry requirement for 1-floor buildings:
    The gable triangle must be a separate face to allow different UV mapping
    for the rectangular wall section and the triangular gable section.
    This enables skipping the middle facade slice in UV mapping.

    UV Mapping:
    - Rectangle (ground floor) -> 'ground' section (doors+windows)
    - Triangle (gable) -> 'gable' section (no windows)
    - Skips 'upper' section entirely for 1-floor buildings

    The wall has 5 vertices total (but generates 2 faces):
    - v0: p0 at floor_z (bottom left)
    - v1: p1 at floor_z (bottom right)
    - v2: p1 at wall_top_z (top right, eave corner)
    - v3: p0 at wall_top_z (top left, eave corner)
    - v4: midpoint at ridge_z (peak/apex)

    Face 1 (rectangle): v0, v1, v2, v3 - rectangular wall
    Face 2 (triangle): v3, v2, v4 - triangular gable

    Both faces are coplanar and share the edge at wall_top_z (v2-v3).

    Args:
        mesh: MeshData to add to
        p0, p1: Edge endpoints
        floor_z: Floor elevation
        wall_top_z: Wall top elevation (eave height)
        ridge_z: Ridge elevation
        ridge_dx, ridge_dy: Ridge direction unit vector
        obb_center: OBB center for determining winding direction
        edge_len: Wall width in meters
        facade_index: Facade style index [0..11]
    """
    # Calculate ridge point - directly above the midpoint of the edge
    # This ensures the gable wall is coplanar (flat face)
    mid_x = (p0.x + p1.x) / 2
    mid_y = (p0.y + p1.y) / 2

    # Create vertices
    v0 = mesh.add_vertex(p0.x, p0.y, floor_z)       # bottom left
    v1 = mesh.add_vertex(p1.x, p1.y, floor_z)       # bottom right
    v2 = mesh.add_vertex(p1.x, p1.y, wall_top_z)    # top right (eave)
    v3 = mesh.add_vertex(p0.x, p0.y, wall_top_z)    # top left (eave)
    v4 = mesh.add_vertex(mid_x, mid_y, ridge_z)     # ridge peak (apex)

    # Compute UVs for rectangle (ground floor section)
    rect_uvs = compute_wall_quad_uvs(edge_len, facade_index, 'ground')
    uv_rect_bl = mesh.add_uv(rect_uvs[0][0], rect_uvs[0][1])
    uv_rect_br = mesh.add_uv(rect_uvs[1][0], rect_uvs[1][1])
    uv_rect_tr = mesh.add_uv(rect_uvs[2][0], rect_uvs[2][1])
    uv_rect_tl = mesh.add_uv(rect_uvs[3][0], rect_uvs[3][1])

    # Compute UVs for gable triangle (gable section - no windows)
    tri_uvs = compute_gable_triangle_uvs(edge_len, facade_index)
    uv_tri_left = mesh.add_uv(tri_uvs[0][0], tri_uvs[0][1])
    uv_tri_right = mesh.add_uv(tri_uvs[1][0], tri_uvs[1][1])
    uv_tri_apex = mesh.add_uv(tri_uvs[2][0], tri_uvs[2][1])

    # Outward-facing winding for a CCW-normalized footprint (see the detailed note
    # in _generate_pentagonal_gable_wall). The p0→p1 vertex order gives an outward
    # normal of (edge_dy, -edge_dx), matching the side walls; the footprint is
    # guaranteed CCW by the OSM parser. The old OBB/perp test used the gable EDGE
    # axis instead of the ridge-aligned NORMAL axis and flipped gable ends inward on
    # centred buildings (back-face culled → "missing walls", Andy's UK3 report).
    # Rectangle: bottom-left → bottom-right → top-right → top-left
    mesh.add_quad_with_uvs(v0, v1, v2, v3, uv_rect_bl, uv_rect_br, uv_rect_tr, uv_rect_tl)
    # Triangle (gable): top-left → top-right → apex
    mesh.add_triangle_with_uvs(v3, v2, v4, uv_tri_left, uv_tri_right, uv_tri_apex)


def _generate_ring_walls(
    mesh: MeshData,
    ring: List[Point2D],
    floor_z: float,
    top_z: float,
    is_outer: bool,
    facade_index: int = 0,
    building_floors: int = 1,
    v_scale: float = 1.0,
) -> None:
    """
    Generate wall quads for a single ring with UV coordinates.

    For multi-floor buildings, generates stacked quads per floor.

    For outer ring: faces point outward (CCW vertices)
    For hole ring: faces point inward (CW vertices, reversed winding)

    Args:
        mesh: MeshData to add vertices and faces to
        ring: Ring vertices (2D)
        floor_z: Bottom elevation
        top_z: Top elevation
        is_outer: True for outer ring, False for holes
        facade_index: Facade style index [0..11] for UV mapping
        building_floors: Number of floors
        v_scale: V multiplier (1/0.75 for Industrial_Atlas without roof section)
    """
    n = len(ring)
    if n < 3:
        return

    # Handle closed ring (skip duplicate closing vertex)
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1

    for i in range(n):
        j = (i + 1) % n

        p0 = ring[i]
        p1 = ring[j]

        # Calculate edge length for UV mapping
        edge_dx = p1.x - p0.x
        edge_dy = p1.y - p0.y
        edge_len = math.sqrt(edge_dx * edge_dx + edge_dy * edge_dy)

        if edge_len < 0.01:
            continue

        # Get UVs for each floor segment
        floor_uvs = compute_multi_floor_wall_uvs(edge_len, building_floors, facade_index, v_scale=v_scale)

        # Calculate actual floor height to ensure walls reach top_z exactly
        # This handles cases where height_m is not exactly floors * 3.0m
        total_wall_height = top_z - floor_z
        actual_floor_height = total_wall_height / building_floors if building_floors > 0 else DEFAULT_FLOOR_HEIGHT

        # Generate one quad per floor
        for floor_idx in range(building_floors):
            z_bottom = floor_z + (floor_idx * actual_floor_height)
            z_top = z_bottom + actual_floor_height

            uvs = floor_uvs[floor_idx]

            if is_outer:
                # Outer ring: normals face outward
                bl = mesh.add_vertex(p0.x, p0.y, z_bottom)
                br = mesh.add_vertex(p1.x, p1.y, z_bottom)
                tr = mesh.add_vertex(p1.x, p1.y, z_top)
                tl = mesh.add_vertex(p0.x, p0.y, z_top)

                uv_bl = mesh.add_uv(uvs[0][0], uvs[0][1])
                uv_br = mesh.add_uv(uvs[1][0], uvs[1][1])
                uv_tr = mesh.add_uv(uvs[2][0], uvs[2][1])
                uv_tl = mesh.add_uv(uvs[3][0], uvs[3][1])

                mesh.add_quad_with_uvs(bl, br, tr, tl, uv_bl, uv_br, uv_tr, uv_tl)
            else:
                # Hole ring: reverse winding for inward-facing normals
                bl = mesh.add_vertex(p1.x, p1.y, z_bottom)
                br = mesh.add_vertex(p0.x, p0.y, z_bottom)
                tr = mesh.add_vertex(p0.x, p0.y, z_top)
                tl = mesh.add_vertex(p1.x, p1.y, z_top)

                # Reverse UV order for reversed winding
                uv_bl = mesh.add_uv(uvs[1][0], uvs[1][1])
                uv_br = mesh.add_uv(uvs[0][0], uvs[0][1])
                uv_tr = mesh.add_uv(uvs[3][0], uvs[3][1])
                uv_tl = mesh.add_uv(uvs[2][0], uvs[2][1])

                mesh.add_quad_with_uvs(bl, br, tr, tl, uv_bl, uv_br, uv_tr, uv_tl)


def generate_walls_lod1(
    building: BuildingRecord,
    config: Optional[WallGeneratorConfig] = None
) -> MeshData:
    """
    Generate simplified LOD1 walls (same as LOD0 for walls).

    In LOD1, walls are the same but roofs may be simplified.

    Args:
        building: Building record
        config: Optional configuration

    Returns:
        MeshData with wall geometry
    """
    # For now, LOD1 walls are identical to LOD0
    # Future: could reduce vertex count for very complex footprints
    return generate_walls(building, config)


def generate_walls_for_hipped(
    building: BuildingRecord,
    config: Optional[WallGeneratorConfig] = None
) -> MeshData:
    """
    Generate walls for a hipped roof building.

    For hipped buildings, ALL walls are rectangular (floor_z -> wall_top_z).
    Unlike gabled roofs, there are no pentagonal gable end walls because
    the hipped roof slopes on all four sides.

    Like gabled side walls, hipped walls use SINGLE continuous quads (no per-floor
    splitting) with continuous UV mapping for multi-story appearance.

    Args:
        building: Building record with footprint and heights
        config: Optional configuration

    Returns:
        MeshData with wall geometry
    """
    if config is None:
        config = WallGeneratorConfig()

    mesh = MeshData(osm_id=building.osm_id)

    floor_z = building.floor_z
    wall_top_z = building.wall_top_z
    ring = building.footprint.outer_ring

    # Get texture variations for this building (deterministic)
    _, facade_index = select_building_variations(building.seed)

    n = len(ring)
    if n < 3:
        return mesh

    # Handle closed ring
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1

    # Generate continuous walls (single quad per edge, no floor splits)
    for i in range(n):
        j = (i + 1) % n
        p0 = ring[i]
        p1 = ring[j]

        # Edge length for UV mapping
        edge_dx = p1.x - p0.x
        edge_dy = p1.y - p0.y
        edge_len = math.sqrt(edge_dx * edge_dx + edge_dy * edge_dy)

        if edge_len < 0.01:
            continue

        # Use continuous UV mapping (same as gabled side walls)
        _generate_side_wall_with_uvs(
            mesh, p0, p1, floor_z, wall_top_z, edge_len, facade_index,
            building.floors
        )

    # Generate hole walls (if any)
    for hole in building.footprint.holes:
        _generate_hipped_hole_walls(
            mesh, hole, floor_z, wall_top_z, facade_index, building.floors
        )

    return mesh


def _generate_hipped_hole_walls(
    mesh: MeshData,
    ring: List[Point2D],
    floor_z: float,
    wall_top_z: float,
    facade_index: int,
    building_floors: int
) -> None:
    """
    Generate continuous wall quads for a hole ring (inward-facing) for hipped buildings.

    Like outer walls, uses single continuous quads with UV interpolation.

    Args:
        mesh: MeshData to add to
        ring: Hole ring vertices
        floor_z: Floor elevation
        wall_top_z: Wall top elevation
        facade_index: Facade style index
        building_floors: Number of floors
    """
    n = len(ring)
    if n < 3:
        return

    # Handle closed ring
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1

    for i in range(n):
        j = (i + 1) % n
        p0 = ring[i]
        p1 = ring[j]

        edge_dx = p1.x - p0.x
        edge_dy = p1.y - p0.y
        edge_len = math.sqrt(edge_dx * edge_dx + edge_dy * edge_dy)

        if edge_len < 0.01:
            continue

        # Get continuous UVs
        uvs = compute_sidewall_continuous_uvs(edge_len, building_floors, facade_index)

        # Create vertices with REVERSED winding for inward-facing normals
        bl = mesh.add_vertex(p1.x, p1.y, floor_z)
        br = mesh.add_vertex(p0.x, p0.y, floor_z)
        tr = mesh.add_vertex(p0.x, p0.y, wall_top_z)
        tl = mesh.add_vertex(p1.x, p1.y, wall_top_z)

        # Reverse UV order for reversed winding
        uv_bl = mesh.add_uv(uvs[1][0], uvs[1][1])
        uv_br = mesh.add_uv(uvs[0][0], uvs[0][1])
        uv_tr = mesh.add_uv(uvs[3][0], uvs[3][1])
        uv_tl = mesh.add_uv(uvs[2][0], uvs[2][1])

        mesh.add_quad_with_uvs(bl, br, tr, tl, uv_bl, uv_br, uv_tr, uv_tl)


# =============================================================================
# HIGHRISE WALL GENERATION (Apartment / Commercial)
# =============================================================================

def generate_highrise_walls(
    building: BuildingRecord,
    config: Optional[WallGeneratorConfig] = None
) -> MeshData:
    """
    Generate wall mesh for a highrise (apartment/commercial) building.

    Uses the Highrise texture atlas (2048x12288, 12 regions of 2048x1024).
    Creates multi-floor wall quads:
    - First quad: up to 4 floors, mapped to full texture region
    - Subsequent quads: up to 3 floors each, mapped to upper 768px of region

    Args:
        building: Building record with footprint and heights
        config: Optional configuration

    Returns:
        MeshData with wall geometry
    """
    if config is None:
        config = WallGeneratorConfig()

    mesh = MeshData(osm_id=building.osm_id)

    floor_z = building.floor_z
    top_z = building.wall_top_z

    # Select texture region based on category
    region_index = select_highrise_region(building.seed, building.category)

    # Generate outer walls
    _generate_highrise_ring_walls(
        mesh,
        building.footprint.outer_ring,
        floor_z,
        top_z,
        is_outer=True,
        building_floors=building.floors,
        region_index=region_index,
    )

    # Generate hole walls (facing inward)
    for hole in building.footprint.holes:
        _generate_highrise_ring_walls(
            mesh,
            hole,
            floor_z,
            top_z,
            is_outer=False,
            building_floors=building.floors,
            region_index=region_index,
        )

    return mesh


def _generate_highrise_ring_walls(
    mesh: MeshData,
    ring: List[Point2D],
    floor_z: float,
    top_z: float,
    is_outer: bool,
    building_floors: int,
    region_index: int,
) -> None:
    """
    Generate highrise wall quads for a single ring.

    Creates multi-floor quads (up to 4 floors for the first quad,
    up to 3 floors for subsequent overflow quads).

    Args:
        mesh: MeshData to add vertices and faces to
        ring: Ring vertices (2D)
        floor_z: Bottom elevation
        top_z: Top elevation
        is_outer: True for outer ring, False for holes
        building_floors: Number of floors
        region_index: Highrise texture region index [0..11]
    """
    n = len(ring)
    if n < 3:
        return

    # Handle closed ring (skip duplicate closing vertex)
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1

    # Compute actual floor height to handle non-exact multiples
    total_wall_height = top_z - floor_z
    actual_floor_height = total_wall_height / building_floors if building_floors > 0 else DEFAULT_FLOOR_HEIGHT

    # Compute Z ranges for each quad
    quad_z_ranges = []
    remaining = building_floors
    z_current = floor_z

    # First quad: up to 4 floors
    first_floors = min(HIGHRISE_FLOORS_PER_REGION, remaining)
    z_next = z_current + first_floors * actual_floor_height
    quad_z_ranges.append((z_current, z_next))
    remaining -= first_floors
    z_current = z_next

    # Subsequent quads: up to 3 floors each
    while remaining > 0:
        overflow_floors = min(HIGHRISE_UPPER_FLOORS, remaining)
        z_next = z_current + overflow_floors * actual_floor_height
        quad_z_ranges.append((z_current, z_next))
        remaining -= overflow_floors
        z_current = z_next

    for i in range(n):
        j = (i + 1) % n

        p0 = ring[i]
        p1 = ring[j]

        # Calculate edge length for UV mapping
        edge_dx = p1.x - p0.x
        edge_dy = p1.y - p0.y
        edge_len = math.sqrt(edge_dx * edge_dx + edge_dy * edge_dy)

        if edge_len < 0.01:
            continue

        # Get UV quad sets for this edge
        uv_quads = compute_highrise_wall_uvs(edge_len, building_floors, region_index)

        # Generate one quad per multi-floor section
        for quad_idx, (z_bottom, z_top) in enumerate(quad_z_ranges):
            uvs = uv_quads[quad_idx]

            if is_outer:
                # Outer ring: normals face outward (CCW)
                bl = mesh.add_vertex(p0.x, p0.y, z_bottom)
                br = mesh.add_vertex(p1.x, p1.y, z_bottom)
                tr = mesh.add_vertex(p1.x, p1.y, z_top)
                tl = mesh.add_vertex(p0.x, p0.y, z_top)

                uv_bl = mesh.add_uv(uvs[0][0], uvs[0][1])
                uv_br = mesh.add_uv(uvs[1][0], uvs[1][1])
                uv_tr = mesh.add_uv(uvs[2][0], uvs[2][1])
                uv_tl = mesh.add_uv(uvs[3][0], uvs[3][1])

                mesh.add_quad_with_uvs(bl, br, tr, tl, uv_bl, uv_br, uv_tr, uv_tl)
            else:
                # Hole ring: reverse winding for inward-facing normals
                bl = mesh.add_vertex(p1.x, p1.y, z_bottom)
                br = mesh.add_vertex(p0.x, p0.y, z_bottom)
                tr = mesh.add_vertex(p0.x, p0.y, z_top)
                tl = mesh.add_vertex(p1.x, p1.y, z_top)

                # Reverse UV order for reversed winding
                uv_bl = mesh.add_uv(uvs[1][0], uvs[1][1])
                uv_br = mesh.add_uv(uvs[0][0], uvs[0][1])
                uv_tr = mesh.add_uv(uvs[3][0], uvs[3][1])
                uv_tl = mesh.add_uv(uvs[2][0], uvs[2][1])

                mesh.add_quad_with_uvs(bl, br, tr, tl, uv_bl, uv_br, uv_tr, uv_tl)
