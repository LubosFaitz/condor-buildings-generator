"""
OBJ mesh exporter for Condor Buildings Generator.

Exports MeshData to Wavefront OBJ format with Condor conventions:
- Y forward, Z up axis orientation
- Optional per-building groups
- LOD0 and LOD1 variants
"""

import os
from typing import List, Optional, Dict
from dataclasses import dataclass
import logging

from ..models.mesh import MeshData
from ..config import (
    CONDOR_MTL_KA, CONDOR_MTL_KD, CONDOR_MTL_KS,
    CONDOR_MTL_NS, CONDOR_MTL_D, CONDOR_MTL_ILLUM,
    CONDOR_TEXTURE_PREFIX,
)

logger = logging.getLogger(__name__)


@dataclass
class ExportStats:
    """Statistics from OBJ export."""
    total_vertices: int = 0
    total_uvs: int = 0
    total_faces: int = 0
    total_buildings: int = 0
    file_size_bytes: int = 0


def export_obj(
    meshes: List[MeshData],
    filepath: str,
    use_groups: bool = False,
    comment: Optional[str] = None
) -> ExportStats:
    """
    Export multiple meshes to a single OBJ file.

    Args:
        meshes: List of MeshData objects to export
        filepath: Output file path (.obj)
        use_groups: If True, create 'g' groups per building OSM ID
        comment: Optional comment to include in file header

    Returns:
        ExportStats with export statistics
    """
    stats = ExportStats()

    # Merge all meshes into one
    merged = MeshData()

    # Track group information for later
    group_info: List[Dict] = []  # [{name, face_start, face_count}]

    for mesh in meshes:
        if not mesh.vertices or not mesh.faces:
            continue

        face_start = len(merged.faces) + 1  # 1-indexed for counting
        vertex_offset = len(merged.vertices)
        uv_offset = len(merged.uvs)

        # Add vertices
        for v in mesh.vertices:
            merged.add_vertex(v[0], v[1], v[2])

        # Add UVs (if present)
        for uv in mesh.uvs:
            merged.uvs.append(uv)

        # Add faces with offset
        for face in mesh.faces:
            new_face = [idx + vertex_offset for idx in face]
            merged.faces.append(new_face)

        # Add face UVs with offset (if present)
        for face_uv in mesh.face_uvs:
            new_face_uv = [idx + uv_offset for idx in face_uv]
            merged.face_uvs.append(new_face_uv)

        if use_groups and mesh.osm_id:
            group_info.append({
                'name': f"building_{mesh.osm_id}",
                'face_start': face_start,
                'face_count': len(mesh.faces)
            })

        stats.total_buildings += 1

    stats.total_vertices = len(merged.vertices)
    stats.total_uvs = len(merged.uvs)
    stats.total_faces = len(merged.faces)

    # Write OBJ file
    os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)

    # Determine if we have UV data
    has_uvs = len(merged.uvs) > 0 and len(merged.face_uvs) == len(merged.faces)

    with open(filepath, 'w', encoding='utf-8') as f:
        # Header comment
        f.write("# Condor Buildings Generator OBJ Export\n")
        f.write(f"# Vertices: {stats.total_vertices}\n")
        if has_uvs:
            f.write(f"# UVs: {stats.total_uvs}\n")
        f.write(f"# Faces: {stats.total_faces}\n")
        f.write(f"# Buildings: {stats.total_buildings}\n")

        if comment:
            f.write(f"# {comment}\n")

        f.write("\n")

        # Write vertices
        # Condor uses Y forward, Z up (same as OBJ default)
        for x, y, z in merged.vertices:
            f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

        f.write("\n")

        # Write UV coordinates (if present)
        if has_uvs:
            for u, v in merged.uvs:
                f.write(f"vt {u:.6f} {v:.6f}\n")
            f.write("\n")

        # Write faces (with optional groups)
        if use_groups and group_info:
            face_idx = 0
            group_idx = 0

            for i, face in enumerate(merged.faces):
                # Check if we need to start a new group
                if group_idx < len(group_info):
                    g = group_info[group_idx]
                    if i + 1 == g['face_start']:
                        f.write(f"\ng {g['name']}\n")
                        group_idx += 1

                # Write face with UV indices if available
                if has_uvs:
                    face_uv = merged.face_uvs[i]
                    face_str = " ".join(f"{v}/{uv}" for v, uv in zip(face, face_uv))
                else:
                    face_str = " ".join(str(idx) for idx in face)
                f.write(f"f {face_str}\n")
        else:
            # No groups, just write all faces
            for i, face in enumerate(merged.faces):
                if has_uvs:
                    face_uv = merged.face_uvs[i]
                    face_str = " ".join(f"{v}/{uv}" for v, uv in zip(face, face_uv))
                else:
                    face_str = " ".join(str(idx) for idx in face)
                f.write(f"f {face_str}\n")

    # Get file size
    stats.file_size_bytes = os.path.getsize(filepath)

    if has_uvs:
        logger.info(
            f"Exported OBJ: {stats.total_vertices} vertices, "
            f"{stats.total_uvs} UVs, {stats.total_faces} faces, "
            f"{stats.total_buildings} buildings"
        )
    else:
        logger.info(
            f"Exported OBJ: {stats.total_vertices} vertices, "
            f"{stats.total_faces} faces, {stats.total_buildings} buildings"
        )

    return stats


def export_obj_grouped(
    pitched_meshes: List[MeshData],
    flat_meshes: List[MeshData],
    filepath: str,
    comment: Optional[str] = None
) -> ExportStats:
    """
    Export meshes to OBJ with roof-type grouping.

    Creates two groups in the OBJ file:
    - 'pitched': All gabled and hipped roof buildings combined into one mesh
    - 'flat': Flat roof buildings (kept separate for future texture support)

    This grouping ensures a single draw call for pitched roofs in Condor,
    while keeping flat roofs separate for different texture assignments.

    Args:
        pitched_meshes: List of MeshData for gabled/hipped buildings
        flat_meshes: List of MeshData for flat roof buildings
        filepath: Output file path (.obj)
        comment: Optional comment to include in file header

    Returns:
        ExportStats with export statistics
    """
    stats = ExportStats()

    # Build merged mesh with group tracking
    merged = MeshData()
    group_info: List[Dict] = []

    # First, merge all pitched meshes into ONE combined mesh (single draw call)
    if pitched_meshes:
        pitched_face_start = len(merged.faces) + 1
        pitched_face_count = 0

        for mesh in pitched_meshes:
            if not mesh.vertices or not mesh.faces:
                continue

            vertex_offset = len(merged.vertices)
            uv_offset = len(merged.uvs)

            # Add vertices
            for v in mesh.vertices:
                merged.add_vertex(v[0], v[1], v[2])

            # Add UVs
            for uv in mesh.uvs:
                merged.uvs.append(uv)

            # Add faces with offset
            for face in mesh.faces:
                new_face = [idx + vertex_offset for idx in face]
                merged.faces.append(new_face)

            # Add face UVs with offset
            for face_uv in mesh.face_uvs:
                new_face_uv = [idx + uv_offset for idx in face_uv]
                merged.face_uvs.append(new_face_uv)

            pitched_face_count += len(mesh.faces)
            stats.total_buildings += 1

        if pitched_face_count > 0:
            group_info.append({
                'name': 'pitched',
                'face_start': pitched_face_start,
                'face_count': pitched_face_count
            })

    # Then add flat meshes (kept as separate group)
    if flat_meshes:
        flat_face_start = len(merged.faces) + 1
        flat_face_count = 0

        for mesh in flat_meshes:
            if not mesh.vertices or not mesh.faces:
                continue

            vertex_offset = len(merged.vertices)
            uv_offset = len(merged.uvs)

            # Add vertices
            for v in mesh.vertices:
                merged.add_vertex(v[0], v[1], v[2])

            # Add UVs
            for uv in mesh.uvs:
                merged.uvs.append(uv)

            # Add faces with offset
            for face in mesh.faces:
                new_face = [idx + vertex_offset for idx in face]
                merged.faces.append(new_face)

            # Add face UVs with offset
            for face_uv in mesh.face_uvs:
                new_face_uv = [idx + uv_offset for idx in face_uv]
                merged.face_uvs.append(new_face_uv)

            flat_face_count += len(mesh.faces)
            stats.total_buildings += 1

        if flat_face_count > 0:
            group_info.append({
                'name': 'flat',
                'face_start': flat_face_start,
                'face_count': flat_face_count
            })

    stats.total_vertices = len(merged.vertices)
    stats.total_uvs = len(merged.uvs)
    stats.total_faces = len(merged.faces)

    # Write OBJ file
    os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)

    has_uvs = len(merged.uvs) > 0 and len(merged.face_uvs) == len(merged.faces)

    with open(filepath, 'w', encoding='utf-8') as f:
        # Header
        f.write("# Condor Buildings Generator OBJ Export\n")
        f.write(f"# Vertices: {stats.total_vertices}\n")
        if has_uvs:
            f.write(f"# UVs: {stats.total_uvs}\n")
        f.write(f"# Faces: {stats.total_faces}\n")
        f.write(f"# Buildings: {stats.total_buildings}\n")
        f.write(f"# Groups: pitched ({len(pitched_meshes)} buildings), flat ({len(flat_meshes)} buildings)\n")

        if comment:
            f.write(f"# {comment}\n")

        f.write("\n")

        # Write vertices
        for x, y, z in merged.vertices:
            f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

        f.write("\n")

        # Write UVs
        if has_uvs:
            for u, v in merged.uvs:
                f.write(f"vt {u:.6f} {v:.6f}\n")
            f.write("\n")

        # Write faces with groups
        group_idx = 0
        for i, face in enumerate(merged.faces):
            # Check if we need to start a new group
            if group_idx < len(group_info):
                g = group_info[group_idx]
                if i + 1 == g['face_start']:
                    f.write(f"\ng {g['name']}\n")
                    group_idx += 1

            # Write face
            if has_uvs:
                face_uv = merged.face_uvs[i]
                face_str = " ".join(f"{v}/{uv}" for v, uv in zip(face, face_uv))
            else:
                face_str = " ".join(str(idx) for idx in face)
            f.write(f"f {face_str}\n")

    stats.file_size_bytes = os.path.getsize(filepath)

    logger.info(
        f"Exported OBJ (grouped): {stats.total_vertices} vertices, "
        f"{stats.total_faces} faces, {len(pitched_meshes)} pitched + {len(flat_meshes)} flat buildings"
    )

    return stats


def export_obj_lod0(
    meshes: List[MeshData],
    patch_id: str,
    output_dir: str,
    use_groups: bool = False,
    pitched_meshes: Optional[List[MeshData]] = None,
    flat_meshes: Optional[List[MeshData]] = None
) -> str:
    """
    Export LOD0 (detailed) mesh.

    If pitched_meshes and flat_meshes are provided, uses grouped export
    (pitched combined, flat separate). Otherwise uses legacy export.

    Args:
        meshes: List of MeshData objects (legacy, used if pitched/flat not provided)
        patch_id: Patch identifier (e.g., "036019")
        output_dir: Output directory path
        use_groups: If True, create per-building groups (legacy mode only)
        pitched_meshes: Optional list of gabled/hipped meshes for grouped export
        flat_meshes: Optional list of flat roof meshes for grouped export

    Returns:
        Path to exported file
    """
    # LOD0 keeps the bare o<patch>.obj name (no suffix); LOD1 gets _LOD1. Matches
    # the Condor export operator and the convention the current Landscape Editor
    # expects (Wiek/Uros, 2026-06-09).
    filename = f"o{patch_id}.obj"
    filepath = os.path.join(output_dir, filename)

    if pitched_meshes is not None and flat_meshes is not None:
        # New grouped export mode
        export_obj_grouped(
            pitched_meshes,
            flat_meshes,
            filepath,
            comment=f"LOD0 - Patch {patch_id}"
        )
    else:
        # Legacy export mode
        export_obj(
            meshes,
            filepath,
            use_groups=use_groups,
            comment=f"LOD0 - Patch {patch_id}"
        )

    return filepath


def export_obj_lod1(
    meshes: List[MeshData],
    patch_id: str,
    output_dir: str,
    use_groups: bool = False,
    pitched_meshes: Optional[List[MeshData]] = None,
    flat_meshes: Optional[List[MeshData]] = None
) -> str:
    """
    Export LOD1 (simplified) mesh.

    If pitched_meshes and flat_meshes are provided, uses grouped export
    (pitched combined, flat separate). Otherwise uses legacy export.

    Args:
        meshes: List of MeshData objects (legacy, used if pitched/flat not provided)
        patch_id: Patch identifier (e.g., "036019")
        output_dir: Output directory path
        use_groups: If True, create per-building groups (legacy mode only)
        pitched_meshes: Optional list of gabled/hipped meshes for grouped export
        flat_meshes: Optional list of flat roof meshes for grouped export

    Returns:
        Path to exported file
    """
    filename = f"o{patch_id}_LOD1.obj"
    filepath = os.path.join(output_dir, filename)

    if pitched_meshes is not None and flat_meshes is not None:
        # New grouped export mode
        export_obj_grouped(
            pitched_meshes,
            flat_meshes,
            filepath,
            comment=f"LOD1 - Patch {patch_id}"
        )
    else:
        # Legacy export mode
        export_obj(
            meshes,
            filepath,
            use_groups=use_groups,
            comment=f"LOD1 - Patch {patch_id}"
        )

    return filepath


def export_mesh_groups(
    groups: Dict[str, MeshData],
    filepath: str,
    comment: Optional[str] = None
) -> ExportStats:
    """
    Export mesh groups as separate objects in ONE OBJ file.

    Uses 'o' (object) instead of 'g' (group) so Blender imports them
    as separate objects. This is the main export function for the
    texture-based mesh grouping system.

    Each group becomes a separate object in the OBJ file:
    - houses: pitched roof buildings (walls + roofs)
    - apartment_walls, commercial_walls, industrial_walls: flat roof walls
    - flat_roof_1..6: flat roofs distributed across texture groups

    Args:
        groups: Dictionary mapping group name to MeshData
        filepath: Output file path (.obj)
        comment: Optional comment to include in file header

    Returns:
        ExportStats with export statistics
    """
    stats = ExportStats()

    # Filter out empty groups and sort for consistent output order
    non_empty_groups = [
        (name, mesh) for name, mesh in sorted(groups.items())
        if not mesh.is_empty()
    ]

    if not non_empty_groups:
        logger.warning("No non-empty mesh groups to export")
        return stats

    os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        # Header
        f.write("# Condor Buildings Generator OBJ Export\n")
        f.write(f"# Objects: {len(non_empty_groups)}\n")
        if comment:
            f.write(f"# {comment}\n")
        f.write("\n")

        # Track offsets for vertex/UV indices (OBJ uses global indices)
        vertex_offset = 0
        uv_offset = 0

        for group_name, mesh in non_empty_groups:
            # Write object declaration (NOT group!)
            # This makes Blender import each as a separate object
            f.write(f"o {group_name}\n")

            # Write vertices for this object
            for x, y, z in mesh.vertices:
                f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

            # Write UVs for this object
            has_uvs = len(mesh.uvs) > 0 and len(mesh.face_uvs) == len(mesh.faces)
            if has_uvs:
                for u, v in mesh.uvs:
                    f.write(f"vt {u:.6f} {v:.6f}\n")

            # Write faces with global offset indices
            for i, face in enumerate(mesh.faces):
                if has_uvs and i < len(mesh.face_uvs):
                    face_uv = mesh.face_uvs[i]
                    face_str = " ".join(
                        f"{v + vertex_offset}/{uv + uv_offset}"
                        for v, uv in zip(face, face_uv)
                    )
                else:
                    face_str = " ".join(str(v + vertex_offset) for v in face)
                f.write(f"f {face_str}\n")

            f.write("\n")

            # Update stats
            stats.total_vertices += len(mesh.vertices)
            stats.total_uvs += len(mesh.uvs)
            stats.total_faces += len(mesh.faces)
            stats.total_buildings += 1  # Each group counts as 1 object

            # Update offsets for next object
            vertex_offset += len(mesh.vertices)
            uv_offset += len(mesh.uvs)

    stats.file_size_bytes = os.path.getsize(filepath)

    logger.info(
        f"Exported OBJ (multi-object): {stats.total_vertices} vertices, "
        f"{stats.total_faces} faces, {len(non_empty_groups)} objects"
    )

    return stats


def validate_obj_file(filepath: str) -> List[str]:
    """
    Validate an OBJ file for common issues.

    Args:
        filepath: Path to OBJ file

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not os.path.exists(filepath):
        errors.append(f"File does not exist: {filepath}")
        return errors

    vertex_count = 0
    face_count = 0
    max_vertex_ref = 0

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()

                if not line or line.startswith('#'):
                    continue

                parts = line.split()
                if not parts:
                    continue

                if parts[0] == 'v':
                    vertex_count += 1
                    if len(parts) < 4:
                        errors.append(
                            f"Line {line_num}: Vertex has < 3 coordinates"
                        )

                elif parts[0] == 'f':
                    face_count += 1
                    if len(parts) < 4:
                        errors.append(
                            f"Line {line_num}: Face has < 3 vertices"
                        )

                    # Check vertex references
                    for part in parts[1:]:
                        # Handle v/vt/vn format
                        idx_str = part.split('/')[0]
                        try:
                            idx = int(idx_str)
                            if idx > 0:
                                max_vertex_ref = max(max_vertex_ref, idx)
                            elif idx < 0:
                                # Negative indices are relative to end
                                abs_idx = vertex_count + idx + 1
                                if abs_idx < 1:
                                    errors.append(
                                        f"Line {line_num}: Invalid negative index {idx}"
                                    )
                        except ValueError:
                            errors.append(
                                f"Line {line_num}: Invalid vertex index '{idx_str}'"
                            )

    except Exception as e:
        errors.append(f"Failed to read file: {e}")
        return errors

    # Check if vertex references are valid
    if max_vertex_ref > vertex_count:
        errors.append(
            f"Face references vertex {max_vertex_ref} but only {vertex_count} vertices exist"
        )

    if vertex_count == 0:
        errors.append("File contains no vertices")

    if face_count == 0:
        errors.append("File contains no faces")

    return errors


# =============================================================================
# CONDOR-READY EXPORT (OBJ + MTL)
# =============================================================================

def _condor_xform(v, axis_swap: bool):
    """Apply the Condor axis transform: (x,y,z) -> (y,-x,z) when axis_swap."""
    x, y, z = v
    if axis_swap:
        return (y, -x, z)
    return (x, y, z)


def _tri_fan(n: int):
    """Yield (a, b, c) position triples that fan-triangulate an n-gon (n>=3)."""
    for i in range(1, n - 1):
        yield (0, i, i + 1)


def _face_normal(p0, p1, p2):
    """Unit normal of triangle (p0,p1,p2). Falls back to +Z if degenerate."""
    ux, uy, uz = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    vx, vy, vz = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (nx / length, ny / length, nz / length)


def export_condor_obj_mtl(
    groups: Dict[str, MeshData],
    obj_filepath: str,
    texture_map: Dict[str, str],
    comment: Optional[str] = None,
    axis_swap: bool = True,
    triangulate: bool = True,
    include_normals: bool = True,
    material_prefix: str = "",
    texture_prefix: Optional[str] = None,
) -> ExportStats:
    """
    Export grouped meshes to a Condor-ready OBJ + matching MTL.

    Unlike export_mesh_groups(), this produces files the Condor Landscape Editor
    accepts without manual tweaking in Blender:
    - Axis transform baked in (matches Blender "Forward: X, Up: Z" export)
    - Faces triangulated (Condor requires triangle mesh)
    - mtllib / usemtl references wired up
    - A .mtl written next to the .obj with Condor material values
      (Kd 1 1 1, Ks 0 0 0, Ns 0, d 1, map_Kd <texture>.dds)

    Args:
        groups: Dict mapping object/group name to MeshData
        obj_filepath: Output .obj path; .mtl is written alongside
        texture_map: Dict mapping group name to texture filename (config.TEXTURE_MAP)
        comment: Optional header comment
        axis_swap: Apply (x,y,z)->(y,-x,z) transform (default True)
        triangulate: Fan-triangulate n-gons (default True)
        include_normals: Write vn and f v/vt/vn (default True)
        material_prefix: Prefix for material names (default "")
        texture_prefix: Prefix prepended to texture filenames in map_Kd
            (default config.CONDOR_TEXTURE_PREFIX, empty — the Condor Landscape
            Editor adds the "Texture/" folder itself during c3d conversion)

    Returns:
        ExportStats with export statistics
    """
    stats = ExportStats()

    if texture_prefix is None:
        texture_prefix = CONDOR_TEXTURE_PREFIX

    non_empty = [
        (name, mesh) for name, mesh in sorted(groups.items())
        if not mesh.is_empty()
    ]

    if not non_empty:
        logger.warning("No non-empty mesh groups to export (Condor)")
        return stats

    out_dir = os.path.dirname(obj_filepath) or '.'
    os.makedirs(out_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(obj_filepath))[0]
    mtl_filename = base + ".mtl"
    mtl_filepath = os.path.join(out_dir, mtl_filename)

    # ---- Write MTL ----
    with open(mtl_filepath, 'w', encoding='utf-8') as mf:
        mf.write("# Condor Buildings Generator MTL\n")
        if comment:
            mf.write(f"# {comment}\n")
        for name, _mesh in non_empty:
            matname = f"{material_prefix}{name}"
            tex = texture_map.get(name)
            mf.write(f"\nnewmtl {matname}\n")
            mf.write("Ka {:.6f} {:.6f} {:.6f}\n".format(*CONDOR_MTL_KA))
            mf.write("Kd {:.6f} {:.6f} {:.6f}\n".format(*CONDOR_MTL_KD))
            mf.write("Ks {:.6f} {:.6f} {:.6f}\n".format(*CONDOR_MTL_KS))
            mf.write(f"Ns {CONDOR_MTL_NS:.6f}\n")
            mf.write(f"d {CONDOR_MTL_D:.6f}\n")
            mf.write(f"illum {CONDOR_MTL_ILLUM}\n")
            if tex:
                mf.write(f"map_Kd {texture_prefix}{tex}\n")

    # ---- Write OBJ ----
    vertex_offset = 0
    uv_offset = 0
    normal_offset = 0

    with open(obj_filepath, 'w', encoding='utf-8') as f:
        f.write("# Condor Buildings Generator OBJ (Condor-ready)\n")
        f.write(f"# Objects: {len(non_empty)}\n")
        f.write(f"# Axis swap: {axis_swap} | Triangulated: {triangulate} | Normals: {include_normals}\n")
        if comment:
            f.write(f"# {comment}\n")
        f.write(f"mtllib {mtl_filename}\n")

        for group_name, mesh in non_empty:
            f.write(f"\no {group_name}\n")
            f.write(f"usemtl {material_prefix}{group_name}\n")

            # Transformed vertices
            tverts = [_condor_xform(v, axis_swap) for v in mesh.vertices]
            for x, y, z in tverts:
                f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

            has_uvs = len(mesh.uvs) > 0 and len(mesh.face_uvs) == len(mesh.faces)
            if has_uvs:
                for u, v in mesh.uvs:
                    f.write(f"vt {u:.6f} {v:.6f}\n")

            # Build triangulated faces (with optional per-triangle normals)
            normals = []
            tri_records = []  # each: (vidx3, uvidx3_or_None, nidx_or_None) 1-indexed local
            for fi, face in enumerate(mesh.faces):
                n = len(face)
                if n < 3:
                    continue
                fuv = mesh.face_uvs[fi] if (has_uvs and fi < len(mesh.face_uvs)) else None
                tris = _tri_fan(n) if triangulate else [tuple(range(n))]
                for tri in tris:
                    if len(tri) != 3:
                        # Non-triangulated n-gon path keeps polygon as-is
                        vidx = tuple(face[p] for p in tri)
                        uvidx = tuple(fuv[p] for p in tri) if fuv else None
                        tri_records.append((vidx, uvidx, None))
                        continue
                    a, b, c = tri
                    vidx = (face[a], face[b], face[c])
                    uvidx = (fuv[a], fuv[b], fuv[c]) if fuv else None
                    nidx = None
                    if include_normals:
                        p0 = tverts[vidx[0] - 1]
                        p1 = tverts[vidx[1] - 1]
                        p2 = tverts[vidx[2] - 1]
                        normals.append(_face_normal(p0, p1, p2))
                        nidx = len(normals)  # 1-indexed within object
                    tri_records.append((vidx, uvidx, nidx))

            if include_normals:
                for nx, ny, nz in normals:
                    f.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")

            for vidx, uvidx, nidx in tri_records:
                parts = []
                for k in range(len(vidx)):
                    vref = vidx[k] + vertex_offset
                    if uvidx is not None:
                        tref = uvidx[k] + uv_offset
                        if nidx is not None:
                            parts.append(f"{vref}/{tref}/{nidx + normal_offset}")
                        else:
                            parts.append(f"{vref}/{tref}")
                    else:
                        if nidx is not None:
                            parts.append(f"{vref}//{nidx + normal_offset}")
                        else:
                            parts.append(f"{vref}")
                f.write("f " + " ".join(parts) + "\n")

            stats.total_vertices += len(mesh.vertices)
            stats.total_uvs += len(mesh.uvs)
            stats.total_faces += len(tri_records)
            stats.total_buildings += 1

            vertex_offset += len(mesh.vertices)
            uv_offset += len(mesh.uvs)
            normal_offset += len(normals)

    stats.file_size_bytes = os.path.getsize(obj_filepath)
    logger.info(
        f"Exported Condor OBJ+MTL: {stats.total_vertices} vertices, "
        f"{stats.total_faces} triangles, {len(non_empty)} objects -> "
        f"{os.path.basename(obj_filepath)} + {mtl_filename}"
    )
    return stats
