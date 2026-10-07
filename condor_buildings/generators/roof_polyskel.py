"""
Polyskel-based hipped roof generator for Condor Buildings Generator.

Generates hipped roofs for polygonal footprints with more than 4 vertices
using the bpypolyskel straight skeleton library. This extends the roof
coverage beyond the analytical hipped algorithm (which only handles
quadrilaterals).

Requirements:
    - mathutils (included in Blender's Python runtime)
    - bpypolyskel (embedded in condor_buildings/bpypolyskel/)

When running standalone (outside Blender), this module gracefully
degrades: POLYSKEL_AVAILABLE = False, and the building generator
falls back to flat roofs as before.

Reference: https://github.com/prochitecture/bpypolyskel
License: GPL v3
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
import math
import logging

from ..models.geometry import Point2D
from ..models.mesh import MeshData
from ..models.building import BuildingRecord
from ..processing.footprint import get_unique_vertex_count
from ..config import (
    ROOF_OVERHANG_LOD0,
    HIPPED_HEIGHT_FIXED,
    DEBUG_POLYSKEL_ROOFS,
)
from .uv_mapping import select_building_variations, get_roof_v_range

logger = logging.getLogger(__name__)

# Conditional import of mathutils and bpypolyskel
# Available in Blender, not in standalone mode
try:
    import mathutils
    from ..bpypolyskel.bpypolyskel import polygonize
    POLYSKEL_AVAILABLE = True
except ImportError:
    POLYSKEL_AVAILABLE = False


@dataclass
class PolyskelRoofConfig:
    """Configuration for polyskel-based hipped roof generation."""
    overhang: float = 0.0  # Roof overhang in meters
    double_sided_roof: bool = True  # Duplicate faces for underside visibility


def generate_polyskel_roof(
    building: BuildingRecord,
    config: Optional[PolyskelRoofConfig] = None
) -> MeshData:
    """
    Generate hipped roof mesh for a building with >4 vertices using bpypolyskel.

    The algorithm:
    1. Extract footprint vertices as 2D coordinates
    2. If overhang > 0, expand footprint outward and compute eave_z
    3. Convert to mathutils.Vector list and call polygonize()
    4. Build MeshData from the resulting faces with correct Z values
    5. Apply UV mapping and double-sided face duplication

    Args:
        building: Building record with footprint and heights
        config: Optional configuration

    Returns:
        MeshData with roof geometry

    Raises:
        RuntimeError: If POLYSKEL_AVAILABLE is False (should be checked before calling)
    """
    if not POLYSKEL_AVAILABLE:
        raise RuntimeError("bpypolyskel is not available (requires Blender/mathutils)")

    if config is None:
        config = PolyskelRoofConfig()

    mesh = MeshData(osm_id=building.osm_id)

    footprint = building.footprint
    outer_ring = footprint.outer_ring

    # Extract unique 2D vertices (remove closing vertex if present)
    original_verts = _get_polygon_vertices(outer_ring)
    if original_verts is None or len(original_verts) < 3:
        logger.warning(f"Building {building.osm_id}: Not enough vertices for polyskel roof")
        return mesh

    n_verts = len(original_verts)

    # Fixed roof height (Phase 1 requirement)
    roof_height = HIPPED_HEIGHT_FIXED
    building.ridge_height_m = roof_height
    wall_top_z = building.wall_top_z

    # Handle overhang
    if config.overhang > 0:
        # Compute tan_pitch from original footprint skeleton
        tan_pitch = _compute_tan_pitch(original_verts, wall_top_z, roof_height)
        if tan_pitch is None:
            logger.warning(
                f"Building {building.osm_id}: Failed to compute pitch from skeleton, "
                f"generating without overhang"
            )
            eave_z = wall_top_z
            verts_for_roof = original_verts
        else:
            eave_z = wall_top_z - tan_pitch * config.overhang
            verts_for_roof = _expand_footprint(original_verts, config.overhang)
    else:
        eave_z = wall_top_z
        verts_for_roof = original_verts
        tan_pitch = None

    n_roof_verts = len(verts_for_roof)

    # Convert to mathutils.Vector list for polygonize()
    # polygonize() expects 3D vectors with z = zBase
    vectors = [mathutils.Vector((x, y, wall_top_z)) for x, y in verts_for_roof]

    # Call polygonize
    try:
        faces = polygonize(
            vectors,
            0,           # firstVertIndex
            n_roof_verts,  # numVerts
            holesInfo=None,
            height=roof_height,
        )
    except Exception as e:
        logger.warning(
            f"Building {building.osm_id}: polygonize() failed: {e}"
        )
        return mesh

    if not faces:
        logger.warning(f"Building {building.osm_id}: polygonize() returned no faces")
        return mesh

    # Get texture variations
    roof_index, _ = select_building_variations(building.seed)

    # Record face count before adding roof geometry
    faces_before = len(mesh.faces)

    # Compute ridge_z (maximum roof height)
    ridge_z = wall_top_z + roof_height

    # Build mesh from polyskel faces
    _build_mesh_from_faces(
        mesh, faces, vectors, n_roof_verts, eave_z, roof_index, ridge_z
    )

    faces_after = len(mesh.faces)
    roof_face_count = faces_after - faces_before

    # Duplicate faces for double-sided visibility
    if config.double_sided_roof and roof_face_count > 0:
        _duplicate_faces_reversed(mesh, faces_before, faces_after)

    # Debug logging
    if DEBUG_POLYSKEL_ROOFS:
        vertex_count = get_unique_vertex_count(outer_ring)
        eave_drop = wall_top_z - eave_z
        logger.info(
            f"POLYSKEL DEBUG [{building.osm_id}]: "
            f"verts={vertex_count}, "
            f"overhang={config.overhang:.2f}m, "
            f"wall_top_z={wall_top_z:.2f}m, "
            f"eave_z={eave_z:.2f}m (drop={eave_drop:.2f}m), "
            f"ridge_z={wall_top_z + roof_height:.2f}m, "
            f"skeleton_faces={len(faces)}, "
            f"mesh_faces={roof_face_count} (x2 for double-sided)"
        )

    return mesh


def _get_polygon_vertices(ring: List[Point2D]) -> Optional[List[Tuple[float, float]]]:
    """
    Extract unique vertices from the footprint ring.

    Args:
        ring: Footprint outer ring

    Returns:
        List of (x, y) tuples, or None if degenerate
    """
    verts = [(p.x, p.y) for p in ring]

    # Remove closing vertex if present
    if len(verts) > 1 and verts[0] == verts[-1]:
        verts = verts[:-1]

    if len(verts) < 3:
        return None

    return verts


def _compute_tan_pitch(
    verts: List[Tuple[float, float]],
    z_base: float,
    height: float
) -> Optional[float]:
    """
    Compute the roof pitch tangent by running polygonize on the original footprint.

    This is needed to calculate eave_z when overhang is applied.
    The pitch is: tan_pitch = height / maxSkelHeight (from the skeleton).

    polygonize() computes this internally as tan_alpha = height / maxSkelHeight,
    and we can extract it from the resulting vertex Z values.

    Args:
        verts: Original footprint vertices [(x, y), ...]
        z_base: Z coordinate at wall top
        height: Desired roof height

    Returns:
        tan_pitch value, or None if computation fails
    """
    if not POLYSKEL_AVAILABLE:
        return None

    n = len(verts)
    vectors = [mathutils.Vector((x, y, z_base)) for x, y in verts]

    try:
        polygonize(vectors, 0, n, height=height)
    except Exception:
        return None

    # After polygonize, vectors[n:] are skeleton nodes with computed Z
    # The maximum Z gives us the ridge height
    if len(vectors) <= n:
        return None

    max_z = max(v.z for v in vectors[n:])
    actual_height = max_z - z_base

    if actual_height < 0.01:
        return None

    # Find the minimum distance from any edge to the center
    # to compute tan_pitch = actual_height / min_dist_to_center
    # But since polygonize already computed the Z correctly with the
    # desired height, we can derive tan_pitch differently:
    # The skeleton maxSkelHeight determines the slope.
    # tan_alpha = height / maxSkelHeight
    # The eave overhang drops by: tan_alpha * overhang
    # Since actual_height == height (polygonize enforces this), we just
    # need to find what maxSkelHeight was.
    # From: node.z = arc.height * tan_alpha + zBase
    # max_z = maxSkelHeight * tan_alpha + zBase
    # max_z = maxSkelHeight * (height / maxSkelHeight) + zBase
    # max_z = height + zBase ← always true!
    #
    # So we can't extract maxSkelHeight from Z alone.
    # Instead, look at intermediate skeleton nodes to find the slope.
    # The minimum Z above zBase gives us the shallowest slope face.
    # tan_pitch for eave calculation = height / maxSkelHeight
    # We need maxSkelHeight from the skeleton, but it's not directly available.
    #
    # Alternative approach: estimate from the polygon's inradius.
    # inradius ≈ area / (perimeter / 2) for convex polygons
    # maxSkelHeight ≈ inradius for convex, or the maximum inscribed circle radius
    # tan_pitch = height / inradius

    # Compute area and perimeter for inradius estimate
    area = 0.0
    perimeter = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += verts[i][0] * verts[j][1] - verts[j][0] * verts[i][1]
        dx = verts[j][0] - verts[i][0]
        dy = verts[j][1] - verts[i][1]
        perimeter += math.sqrt(dx * dx + dy * dy)

    area = abs(area) / 2.0
    if perimeter < 0.01:
        return None

    inradius = area / (perimeter / 2.0)
    if inradius < 0.01:
        return None

    tan_pitch = height / inradius
    return tan_pitch


def _expand_footprint(
    verts: List[Tuple[float, float]],
    overhang: float
) -> List[Tuple[float, float]]:
    """
    Expand the footprint outward by the overhang distance.

    Uses the bisector direction at each vertex to expand uniformly.
    Works for any polygon (convex or concave).

    Based on the same algorithm used in roof_hipped.py.

    Args:
        verts: List of (x, y) vertices
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
        dot = prev_nx * next_nx + prev_ny * next_ny
        half_angle_cos = math.sqrt((1 + dot) / 2)
        if half_angle_cos < 0.1:
            half_angle_cos = 0.1  # Prevent extreme expansion

        expansion = overhang / half_angle_cos

        # Expand vertex
        new_x = vx + bisect_x * expansion
        new_y = vy + bisect_y * expansion
        expanded.append((new_x, new_y))

    return expanded


def _compute_face_uvs(
    face_vertices: List[Tuple[float, float, float]],
    eave_z: float,
    ridge_z: float,
    roof_index: int
) -> List[Tuple[float, float]]:
    """
    Compute UV coordinates using orthographic planar projection for U
    and global building Z height for V.

    Algorithm:
    1. Compute face normal from first 3 vertices (cross product)
    2. Define local 2D coordinate system on the face plane:
       - V-axis: "up" direction (projected Z onto face plane)
       - U-axis: perpendicular to V-axis within face plane
    3. Project all vertices onto face plane (for U in meters)
    4. Both U and V scaled by the same global_height reference:
       - U = projected_meters / global_height
       - V = (world_z - eave_z) / global_height mapped to atlas range

    Key properties:
    - Shape preservation: planar projection preserves face shape for U
    - Consistent tile size: both U and V use the same physical scale
      (global roof height), so tiles are identical across all faces
    - Correct aspect ratio: 1 meter in U = 1 meter in V

    Args:
        face_vertices: List of (x, y, z) tuples for face vertices (CCW order)
        eave_z: Z coordinate at eave level (global for building)
        ridge_z: Z coordinate at ridge level (global for building)
        roof_index: Roof texture pattern index

    Returns:
        List of (u, v) tuples, one per vertex
    """
    n_verts = len(face_vertices)
    if n_verts < 3:
        v_min, v_max = get_roof_v_range(roof_index)
        return [(0.0, (v_min + v_max) / 2.0)] * n_verts

    # Global height range for the building's roof
    global_height = ridge_z - eave_z
    if global_height < 0.01:
        global_height = 1.0

    # Step 1: Compute face normal from first 3 vertices
    v0 = face_vertices[0]
    v1 = face_vertices[1]
    v2 = face_vertices[2]

    edge1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
    edge2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])

    # Cross product for normal (CCW winding gives outward normal)
    nx = edge1[1] * edge2[2] - edge1[2] * edge2[1]
    ny = edge1[2] * edge2[0] - edge1[0] * edge2[2]
    nz = edge1[0] * edge2[1] - edge1[1] * edge2[0]

    n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n_len < 0.0001:
        # Degenerate face, use fallback
        return _compute_fallback_uvs(face_vertices, eave_z, ridge_z - eave_z if ridge_z > eave_z else 1.0,
                                      *get_roof_v_range(roof_index))

    nx, ny, nz = nx / n_len, ny / n_len, nz / n_len

    # Step 2: Define local coordinate system on the face plane
    # V-axis: project world Z onto face plane (the "up" direction on the face)
    dot_z_n = nz  # dot([0,0,1], [nx,ny,nz]) = nz

    if abs(dot_z_n) > 0.99:
        # Face is nearly horizontal (flat roof face), use Y as up
        vax, vay, vaz = 0.0, 1.0, 0.0
    else:
        # Project Z onto face plane: Z - (Z.n)*n
        vax = -dot_z_n * nx
        vay = -dot_z_n * ny
        vaz = 1.0 - dot_z_n * nz

        v_len = math.sqrt(vax * vax + vay * vay + vaz * vaz)
        if v_len < 0.0001:
            vax, vay, vaz = 0.0, 1.0, 0.0
        else:
            vax, vay, vaz = vax / v_len, vay / v_len, vaz / v_len

    # U-axis: perpendicular to both normal and V-axis (cross product)
    uax = ny * vaz - nz * vay
    uay = nz * vax - nx * vaz
    uaz = nx * vay - ny * vax

    u_len = math.sqrt(uax * uax + uay * uay + uaz * uaz)
    if u_len < 0.0001:
        uax, uay, uaz = 1.0, 0.0, 0.0
    else:
        uax, uay, uaz = uax / u_len, uay / u_len, uaz / u_len

    # Step 3: Project all vertices onto face plane (for U coordinate in meters)
    origin = face_vertices[0]
    u_coords = []

    for vx, vy, vz in face_vertices:
        dx = vx - origin[0]
        dy = vy - origin[1]
        dz = vz - origin[2]

        u_2d = dx * uax + dy * uay + dz * uaz
        u_coords.append(u_2d)

    u_min_2d = min(u_coords)

    # Step 4: Compute UV coordinates
    # Both U and V use the same scale (global_height as reference) so that
    # 1 meter in U = 1 meter in V, giving consistent tile size across all faces
    v_atlas_min, v_atlas_max = get_roof_v_range(roof_index)
    v_atlas_span = v_atlas_max - v_atlas_min

    uvs = []
    for i in range(n_verts):
        vx, vy, vz = face_vertices[i]

        # U: planar projection in meters, scaled by global_height
        u_final = (u_coords[i] - u_min_2d) / global_height

        # V: world Z relative to building's global eave_z / ridge_z
        v_fraction = (vz - eave_z) / global_height
        v_fraction = max(0.0, min(1.0, v_fraction))
        v_final = v_atlas_min + v_fraction * v_atlas_span

        uvs.append((u_final, v_final))

    return uvs


def _compute_fallback_uvs(
    face_vertices: List[Tuple[float, float, float]],
    eave_z: float,
    height_range: float,
    v_min: float,
    v_max: float
) -> List[Tuple[float, float]]:
    """
    Fallback UV computation using simple distribution.

    V based on Z height, U evenly distributed.
    """
    n_verts = len(face_vertices)
    uvs = []

    for i, (vx, vy, vz) in enumerate(face_vertices):
        # V based on height
        t_height = (vz - eave_z) / height_range
        t_height = max(0.0, min(1.0, t_height))
        uv_v = v_min + t_height * (v_max - v_min)

        # U evenly distributed
        u = i / max(1, n_verts - 1)

        uvs.append((u, uv_v))

    return uvs


def _build_mesh_from_faces(
    mesh: MeshData,
    faces: List[List[int]],
    vectors: list,
    n_polygon_verts: int,
    eave_z: float,
    roof_index: int,
    ridge_z: float = None
) -> None:
    """
    Convert polyskel faces to MeshData geometry with per-face UV mapping.

    For each face returned by polygonize():
    - Polygon boundary vertices (index < n_polygon_verts) get z = eave_z
    - Skeleton vertices (index >= n_polygon_verts) keep their computed z

    UV mapping is computed per-face to ensure correct texture orientation:
    - V is based on relative Z height (eave to ridge)
    - U is based on horizontal position, preserving aspect ratio
    - Each face is mapped independently for correct texture alignment

    Args:
        mesh: MeshData to add geometry to
        faces: List of face index lists from polygonize()
        vectors: The vertex list (mutated by polygonize, includes skeleton nodes)
        n_polygon_verts: Number of original polygon vertices
        eave_z: Z coordinate for eave vertices (polygon boundary)
        roof_index: Roof texture pattern index for UV mapping
        ridge_z: Z coordinate at ridge level (max height). If None, computed from vectors.
    """
    # Compute ridge_z if not provided
    if ridge_z is None:
        ridge_z = max(v.z for v in vectors[n_polygon_verts:]) if len(vectors) > n_polygon_verts else eave_z + 3.0

    # Build a mapping from polyskel vertex index to MeshData vertex index
    # Note: UVs are computed per-face, so we only cache vertex indices, not UVs
    vertex_index_map = {}

    for face in faces:
        if len(face) < 3:
            continue

        # First pass: create MeshData vertices and collect face vertex positions
        face_v_indices = []
        face_vertices_3d = []  # (x, y, z) for UV computation

        for idx in face:
            v = vectors[idx]

            # Polygon boundary vertices get eave_z,
            # skeleton vertices keep their computed Z
            if idx < n_polygon_verts:
                z = eave_z
            else:
                z = v.z

            # Cache vertex index (but not UV - computed per face)
            if idx not in vertex_index_map:
                v_idx = mesh.add_vertex(v.x, v.y, z)
                vertex_index_map[idx] = v_idx
            else:
                v_idx = vertex_index_map[idx]

            face_v_indices.append(v_idx)
            face_vertices_3d.append((v.x, v.y, z))

        # Compute UVs for this face using the per-face algorithm
        face_uvs = _compute_face_uvs(face_vertices_3d, eave_z, ridge_z, roof_index)

        # Add UV coordinates to mesh and get indices
        face_uv_indices = []
        for u, uv_v in face_uvs:
            uv_idx = mesh.add_uv(u, uv_v)
            face_uv_indices.append(uv_idx)

        # Add face to mesh
        # polygonize() returns faces in CCW order (outward-facing for roof)
        if len(face_v_indices) == 3:
            mesh.add_triangle_with_uvs(
                face_v_indices[0], face_v_indices[1], face_v_indices[2],
                face_uv_indices[0], face_uv_indices[1], face_uv_indices[2]
            )
        elif len(face_v_indices) == 4:
            mesh.add_quad_with_uvs(
                face_v_indices[0], face_v_indices[1],
                face_v_indices[2], face_v_indices[3],
                face_uv_indices[0], face_uv_indices[1],
                face_uv_indices[2], face_uv_indices[3]
            )
        else:
            # N-gon: add as polygon face
            mesh.add_polygon_with_uvs(face_v_indices, face_uv_indices)


def _duplicate_faces_reversed(
    mesh: MeshData,
    start_idx: int,
    end_idx: int
) -> None:
    """
    Duplicate faces with reversed winding for double-sided visibility.

    Same approach as roof_hipped.py.

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

def generate_polyskel_roof_lod0(building: BuildingRecord) -> MeshData:
    """
    Generate LOD0 polyskel roof with overhang and double-sided faces.

    Args:
        building: Building record

    Returns:
        MeshData with roof geometry
    """
    config = PolyskelRoofConfig(
        overhang=ROOF_OVERHANG_LOD0,
        double_sided_roof=True
    )
    return generate_polyskel_roof(building, config)


def generate_polyskel_roof_lod1(building: BuildingRecord) -> MeshData:
    """
    Generate LOD1 polyskel roof without overhang.

    Args:
        building: Building record

    Returns:
        MeshData with roof geometry
    """
    config = PolyskelRoofConfig(
        overhang=0.0,
        double_sided_roof=True
    )
    return generate_polyskel_roof(building, config)
