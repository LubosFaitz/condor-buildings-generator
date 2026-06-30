"""
Condor Buildings Generator - Mesh Converter

Converts MeshData instances from the pipeline to Blender mesh objects.
Handles vertex conversion, face indexing, and UV coordinate mapping.
"""

import os

import bpy
import bmesh
from typing import List, Optional, Dict

# Import MeshData type for type hints (conditional to allow testing outside Blender)
try:
    from ..models.mesh import MeshData
    from ..config import TEXTURE_MAP
except ImportError:
    MeshData = None
    TEXTURE_MAP = {}


def meshdata_to_blender(
    mesh_data,  # MeshData
    name: str = "building",
    collection: Optional[bpy.types.Collection] = None,
    use_osm_id: bool = True,
    recalc_normals: bool = False
) -> bpy.types.Object:
    """
    Convert a MeshData instance to a Blender mesh object.

    Args:
        mesh_data: Pipeline MeshData with vertices, faces, and UVs
        name: Base name for the object
        collection: Target collection (defaults to active collection)
        use_osm_id: If True and osm_id is set, use it as name (default: True)

    Returns:
        Created Blender object

    Note:
        - MeshData uses 1-based indices (OBJ convention)
        - Blender uses 0-based indices
        - Conversion is handled automatically
    """
    # Use osm_id as name if available and use_osm_id is True
    if use_osm_id and mesh_data.osm_id:
        name = f"building_{mesh_data.osm_id}"

    # Create new mesh and object
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)

    # Convert vertices: MeshData stores as tuples (x, y, z)
    vertices = list(mesh_data.vertices)

    # Convert faces: MeshData uses 1-based indices, Blender uses 0-based
    faces = [[idx - 1 for idx in face] for face in mesh_data.faces]

    # Create mesh geometry
    # Note: from_pydata expects vertices, edges, faces
    mesh.from_pydata(vertices, [], faces)

    # Add UV layer if UVs exist. This MUST run before mesh.validate():
    # _add_uv_layer assigns UVs by polygon index, and validate() may delete
    # degenerate faces (renumbering the remaining polygons). Assigning UVs while
    # the indices still match the pipeline, then validating, keeps UVs aligned —
    # validate() drops a bad face together with its (now-correct) UV loops.
    # (v0.8.13 ran validate() first and mis-assigned UVs — Lubos's report.)
    if mesh_data.uvs and mesh_data.face_uvs:
        _add_uv_layer(mesh, mesh_data)

    # Safety net: strip any degenerate geometry (collapsed edges, duplicate-
    # vertex faces) that would freeze Blender's Edit Mode (Lubos's report,
    # v0.8.13). In the normal pipeline this is a no-op because
    # MeshData.optimize() already removes such faces before conversion; it
    # guards against any future generator that emits a degenerate face.
    mesh.validate(verbose=False)

    # Normals are already corrected per-building in mesh_grouper._fix_normals_outward
    # before merging, so recalculate_outside is skipped here — it would override
    # the inner-ring (courtyard) wall normals that intentionally face inward.
    # For hipped roofs (double_sided_roof=True), caller passes recalc_normals=True.
    if recalc_normals:
        _recalc_normals_outside(mesh)

    # Update mesh to compute normals, etc.
    mesh.update()

    # Link object to collection
    if collection is None:
        collection = bpy.context.collection
    collection.objects.link(obj)

    if mesh_data.origin is not None:
        obj.location = mesh_data.origin

    return obj


def _add_uv_layer(mesh: bpy.types.Mesh, mesh_data) -> None:
    """
    Add UV coordinates to a Blender mesh.

    Blender stores UVs per-loop (per corner of each face), not per-vertex.
    This function maps MeshData's face_uvs to Blender's loop-based UV system.

    Args:
        mesh: Blender mesh to add UVs to
        mesh_data: MeshData with uvs and face_uvs
    """
    # Create UV layer
    uv_layer = mesh.uv_layers.new(name="UVMap")

    # Blender's UV data is accessed per-loop
    # mesh.polygons[i].loop_indices gives the loop indices for face i
    for face_idx, polygon in enumerate(mesh.polygons):
        # Get UV indices for this face (1-based from MeshData)
        if face_idx < len(mesh_data.face_uvs):
            face_uv_indices = mesh_data.face_uvs[face_idx]

            # Map each loop to its UV
            for loop_local_idx, loop_idx in enumerate(polygon.loop_indices):
                if loop_local_idx < len(face_uv_indices):
                    # Convert 1-based UV index to 0-based
                    uv_idx = face_uv_indices[loop_local_idx] - 1

                    if 0 <= uv_idx < len(mesh_data.uvs):
                        uv = mesh_data.uvs[uv_idx]
                        uv_layer.data[loop_idx].uv = (uv[0], uv[1])


def _recalc_normals_outside(mesh: bpy.types.Mesh) -> None:
    """
    Orient face normals so the object renders correctly on import, removing the
    need for a manual Edit Mode "Recalculate Outside" (Shift+N) — Lubos's report.

    Two cases:
      - Flat-roof sheet (every face horizontal, e.g. the merged 'flat_roof'
        object): bmesh's recalc_face_normals is ambiguous for open planar sheets
        and can point them down (invisible from above), so force +Z (up).
      - Everything else (closed building volumes: walls + pitched/hipped roofs):
        recalc_face_normals outward, exactly like Shift+N. This is a no-op when
        the generator already wound faces outward; it only flips genuine
        inversions, so it won't undo the gable-wall winding fix (v0.8.10).

    UV loops are preserved: flipping/recalculating a face reverses its loop order
    together with the UV corners, so UVs stay attached to their vertices.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if not bm.faces:
            return
        bm.normal_update()
        all_horizontal = all(abs(f.normal.z) > 0.999 for f in bm.faces)
        if all_horizontal:
            for f in bm.faces:
                if f.normal.z < 0.0:
                    f.normal_flip()
        else:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
    finally:
        bm.free()


def _material_has_image(mat: bpy.types.Material) -> bool:
    """
    True if the material already has an Image Texture node with a loaded image.

    Used to detect "white" materials that were created before their .dds was in
    the Textures folder, so they can be healed (textured) on a later run instead
    of being reused untextured.
    """
    if not mat.use_nodes or not mat.node_tree:
        return False
    return any(
        node.type == 'TEX_IMAGE' and node.image is not None
        for node in mat.node_tree.nodes
    )


def _attach_texture_node(mat: bpy.types.Material, texture_path: str) -> None:
    """
    Add an Image Texture node to a node-based material and wire it to Base Color.

    Settings match Wiek's reference: Linear interpolation, Flat projection,
    Repeat extension, sRGB color space, Straight alpha.
    """
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Get the default Principled BSDF (created by use_nodes=True)
    principled = nodes.get("Principled BSDF")

    # Create Image Texture node
    tex_node = nodes.new(type='ShaderNodeTexImage')
    tex_node.location = (-300, 300)

    # Load image
    image = bpy.data.images.load(texture_path)
    image.colorspace_settings.name = 'sRGB'
    image.alpha_mode = 'STRAIGHT'
    tex_node.image = image

    # Settings: Linear interpolation, Flat projection, Repeat
    tex_node.interpolation = 'Linear'
    tex_node.projection = 'FLAT'
    tex_node.extension = 'REPEAT'

    # Connect Color -> Base Color
    if principled is not None:
        links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])


def _create_material(
    name: str,
    texture_path: Optional[str] = None
) -> bpy.types.Material:
    """
    Create a Principled BSDF material with optional Image Texture.

    Settings match Wiek's reference: Principled BSDF defaults,
    Image Texture with Linear interpolation, Flat projection,
    Repeat extension, sRGB color space, Straight alpha.

    Args:
        name: Material name
        texture_path: Path to .dds texture file (None = no image)

    Returns:
        Created Blender material
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True

    if texture_path:
        if not os.path.isfile(texture_path):
            print(f"[Condor] WARNING: texture file not found: {texture_path}")
        else:
            print(f"[Condor] Loading texture: {texture_path}")
            _attach_texture_node(mat, texture_path)
    else:
        print(f"[Condor] Material '{name}': no texture path provided")

    return mat


def _find_texture_file(texture_dir: str, texture_filename: str) -> Optional[str]:
    """
    Find a texture file in directory with case-insensitive fallback.

    Args:
        texture_dir: Directory to search in
        texture_filename: Expected filename (e.g., 'Houses_Atlas.dds')

    Returns:
        Full path to found file, or None
    """
    # Direct match first
    candidate = os.path.join(texture_dir, texture_filename)
    if os.path.isfile(candidate):
        return candidate

    # Case-insensitive fallback (handles OneDrive, network drives, etc.)
    if os.path.isdir(texture_dir):
        target_lower = texture_filename.lower()
        for entry in os.listdir(texture_dir):
            if entry.lower() == target_lower:
                match = os.path.join(texture_dir, entry)
                print(f"[Condor] Case-insensitive match: '{entry}' for '{texture_filename}'")
                return match

    return None


def _find_texture_in_dirs(
    search_dirs: List[str],
    texture_filename: str,
) -> Optional[str]:
    """Return the first match for texture_filename across search_dirs, or None."""
    for d in search_dirs:
        path = _find_texture_file(d, texture_filename)
        if path:
            return path
    return None


def _assign_material(
    obj: bpy.types.Object,
    group_name: str,
    texture_dirs: Optional[List[str]] = None,
    texture_map: Optional[Dict[str, str]] = None,
) -> None:
    """
    Assign a material to a Blender object based on its group name.

    Reuses existing materials if already created (avoids duplicates
    when importing multiple patches).

    Args:
        obj: Blender object to assign material to
        group_name: Mesh group name (e.g., 'houses', 'flat_roof_1', 'flat_roof')
        texture_dirs: Ordered list of directories to search for the .dds texture
            (e.g., [Working/Autogen/Textures, Landscapes/<name>/Textures]). The
            second is where the per-patch orthophoto t<patch>.dds lives.
        texture_map: Optional per-run group->filename map (overrides TEXTURE_MAP).
            Used to point the merged 'flat_roof' object at the patch orthophoto.
    """
    tmap = texture_map if texture_map is not None else TEXTURE_MAP
    texture_filename = tmap.get(group_name)
    if not texture_filename:
        import re
        base = re.sub(r'_\d+$', '', group_name)
        texture_filename = tmap.get(base)
    if not texture_filename:
        return

    # The merged flat_roof uses a per-patch orthophoto (t<patch>.dds), so key its
    # material on the texture name to avoid reusing one patch's photo on another.
    if group_name == 'flat_roof':
        mat_name = f"condor_{os.path.splitext(texture_filename)[0]}"
    else:
        mat_name = f"condor_{group_name}"

    search_dirs = [d for d in (texture_dirs or []) if d]

    # Reuse existing material if already created (avoids duplicates across patches).
    if mat_name in bpy.data.materials:
        mat = bpy.data.materials[mat_name]
        # Self-heal: a material first created before its .dds was in the Textures
        # folder exists but is "white" (no Image Texture node). Reusing it as-is
        # leaves the object untextured even after the user later copies the texture
        # in (and a plain "Clear Buildings" doesn't help — the material lingers as
        # orphan data and gets reused). So if it has no image and the texture is now
        # present, attach it instead of reusing the empty material — no manual
        # delete needed. (Reported by Lubos Faitz, 2026-06-09.)
        if not _material_has_image(mat):
            texture_path = _find_texture_in_dirs(search_dirs, texture_filename)
            if texture_path:
                print(f"[Condor] Healing material '{mat_name}' with now-available texture: {texture_path}")
                _attach_texture_node(mat, texture_path)
    else:
        # Search each candidate directory in order
        texture_path = _find_texture_in_dirs(search_dirs, texture_filename)

        if not texture_path:
            print(f"[Condor] Texture NOT FOUND: '{texture_filename}' in {search_dirs}")
            for d in search_dirs:
                if os.path.isdir(d):
                    contents = os.listdir(d)
                    print(f"[Condor]   '{d}' contains {len(contents)} files: {contents[:20]}")
                else:
                    print(f"[Condor]   Directory does NOT exist: '{d}'")

        mat = _create_material(mat_name, texture_path)

    # Assign material to object
    obj.data.materials.append(mat)


def create_buildings_collection(
    name: str = "Condor Buildings",
    parent: Optional[bpy.types.Collection] = None
) -> bpy.types.Collection:
    """
    Create or get a collection for imported buildings.

    Args:
        name: Collection name
        parent: Parent collection (defaults to scene collection)

    Returns:
        The collection (created or existing)
    """
    # Check if collection already exists
    if name in bpy.data.collections:
        return bpy.data.collections[name]

    # Create new collection
    collection = bpy.data.collections.new(name)

    # Link to parent
    if parent is None:
        parent = bpy.context.scene.collection
    parent.children.link(collection)

    return collection


def blender_obj_to_meshdata(obj: bpy.types.Object, osm_id: str = None):
    """
    Convert a Blender mesh object back to MeshData.

    Used by the export operator to read objects from the scene collection
    instead of re-running the pipeline.
    """
    try:
        from ..models.mesh import MeshData
    except ImportError:
        return None

    mesh = obj.data
    md = MeshData(osm_id=osm_id or obj.name)

    for v in mesh.vertices:
        md.vertices.append((v.co.x, v.co.y, v.co.z))

    uv_layer = mesh.uv_layers.active
    uv_map = {}
    if uv_layer:
        for loop in mesh.loops:
            uv = uv_layer.data[loop.index].uv
            key = (round(uv.x, 6), round(uv.y, 6))
            if key not in uv_map:
                uv_map[key] = len(md.uvs) + 1
                md.uvs.append((uv.x, uv.y))

    for poly in mesh.polygons:
        face = [li + 1 for li in poly.vertices]
        md.faces.append(face)
        if uv_layer:
            face_uv = []
            for loop_idx in poly.loop_indices:
                uv = uv_layer.data[loop_idx].uv
                key = (round(uv.x, 6), round(uv.y, 6))
                face_uv.append(uv_map[key])
            md.face_uvs.append(face_uv)

    return md


def import_meshes_to_blender(
    meshes: List,  # List[MeshData]
    collection_name: str = "Condor Buildings",
    join_meshes: bool = False
) -> List[bpy.types.Object]:
    """
    Import multiple MeshData instances to Blender.

    Args:
        meshes: List of MeshData from pipeline
        collection_name: Name for the collection to hold buildings
        join_meshes: If True, join all meshes into single object (faster for large datasets)

    Returns:
        List of created Blender objects
    """
    if not meshes:
        return []

    # Create collection for buildings
    collection = create_buildings_collection(collection_name)

    # Import each mesh
    objects = []
    for mesh_data in meshes:
        if mesh_data.vertices:  # Skip empty meshes
            obj = meshdata_to_blender(mesh_data, collection=collection)
            objects.append(obj)

    # Optionally join all objects for performance
    if join_meshes and len(objects) > 1:
        # Select all objects
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)

        # Set active object
        bpy.context.view_layer.objects.active = objects[0]

        # Join
        bpy.ops.object.join()

        # Return single joined object
        return [bpy.context.active_object]

    return objects


def cleanup_buildings_collection(name: str = "Condor Buildings") -> int:
    """
    Remove all objects from a buildings collection.

    Args:
        name: Collection name to clean up

    Returns:
        Number of objects removed
    """
    if name not in bpy.data.collections:
        return 0

    collection = bpy.data.collections[name]
    count = len(collection.objects)

    # Remove all objects
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    return count


def _duplicate_and_flip_mesh(
    src_obj: bpy.types.Object,
    name: str,
    collection: bpy.types.Collection
) -> bpy.types.Object:
    mesh_copy = src_obj.data.copy()
    mesh_copy.flip_normals()
    flip_obj = bpy.data.objects.new(name, mesh_copy)
    collection.objects.link(flip_obj)
    return flip_obj


def _join_objects_into(
    target_obj: bpy.types.Object,
    source_objs: List[bpy.types.Object]
) -> None:
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in [target_obj] + list(source_objs):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = target_obj
    bpy.ops.object.join()


def import_grouped_meshes_to_blender(
    grouped_meshes: Dict,  # Dict[str, MeshData]
    collection_name: str = "Condor Buildings",
    texture_dir: Optional[str] = None,
    texture_map: Optional[Dict[str, str]] = None,
    extra_texture_dirs: Optional[List[str]] = None,
) -> List[bpy.types.Object]:
    """
    Import grouped meshes to Blender as separate named objects.

    Each group (houses, Highrise_walls, flat_roof, etc.) becomes a separate
    Blender object with the group name. This matches the OBJ export format with
    multiple 'o' objects.

    Each object gets a Principled BSDF material with Image Texture pointing to the
    corresponding .dds file (if found). Textures are searched in texture_dir first,
    then in extra_texture_dirs (e.g., the landscape Textures folder that holds the
    per-patch orthophoto t<patch>.dds used by the merged flat_roof object).

    Args:
        grouped_meshes: Dictionary mapping group name to MeshData
        collection_name: Name for the collection to hold buildings
        texture_dir: Primary texture directory (e.g., Working/Autogen/Textures/)
        texture_map: Optional per-run group->filename map (overrides TEXTURE_MAP),
            used to point 'flat_roof' at the patch orthophoto.
        extra_texture_dirs: Additional directories to search (e.g., the landscape
            Textures folder).

    Returns:
        List of created Blender objects
    """
    if not grouped_meshes:
        return []

    # Create collection for buildings
    collection = create_buildings_collection(collection_name)

    search_dirs = [texture_dir] + list(extra_texture_dirs or [])

    # Check for LOD0 gabled roofs that need doubling, and hipped roofs that need recalc
    gabled_roofs_data = grouped_meshes.get('gabled_roofs_lod0')
    hipped_roofs_data = grouped_meshes.get('hipped_roofs')
    houses_obj = None

    # Import each group as a named object
    objects = []
    for group_name, mesh_data in grouped_meshes.items():
        # Handled separately after 'houses' is created
        if group_name in ('gabled_roofs_lod0', 'hipped_roofs'):
            continue

        # Skip empty meshes
        if mesh_data.is_empty():
            continue

        # Create object with group name (don't use osm_id)
        obj = meshdata_to_blender(
            mesh_data,
            name=group_name,
            collection=collection,
            use_osm_id=False
        )

        # Assign material with texture
        _assign_material(obj, group_name, texture_dirs=search_dirs, texture_map=texture_map)

        if group_name == 'houses':
            houses_obj = obj

        objects.append(obj)

    # Double LOD0 gabled roofs: duplicate, flip normals on duplicate, join both into houses.
    if gabled_roofs_data and not gabled_roofs_data.is_empty():
        roofs_obj = meshdata_to_blender(
            gabled_roofs_data,
            name='gabled_roofs_lod0',
            collection=collection,
            use_osm_id=False
        )
        _assign_material(roofs_obj, 'houses', texture_dirs=search_dirs, texture_map=texture_map)

        roofs_flip_obj = _duplicate_and_flip_mesh(roofs_obj, 'gabled_roofs_lod0_flip', collection)
        _assign_material(roofs_flip_obj, 'houses', texture_dirs=search_dirs, texture_map=texture_map)

        if houses_obj is not None:
            _join_objects_into(houses_obj, [roofs_obj, roofs_flip_obj])
        else:
            _join_objects_into(roofs_obj, [roofs_flip_obj])
            roofs_obj.name = 'houses'
            houses_obj = roofs_obj
            objects.append(roofs_obj)

    # Hipped roofs: recalc normals outside (double_sided_roof=True, _fix_normals_outward skipped),
    # then join into houses.
    if hipped_roofs_data and not hipped_roofs_data.is_empty():
        hipped_obj = meshdata_to_blender(
            hipped_roofs_data,
            name='hipped_roofs',
            collection=collection,
            use_osm_id=False,
            recalc_normals=True
        )
        _assign_material(hipped_obj, 'houses', texture_dirs=search_dirs, texture_map=texture_map)

        if houses_obj is not None:
            _join_objects_into(houses_obj, [hipped_obj])
        else:
            hipped_obj.name = 'houses'
            objects.append(hipped_obj)

    return objects
