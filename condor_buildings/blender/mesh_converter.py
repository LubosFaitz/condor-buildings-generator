"""
Condor Buildings Generator - Mesh Converter

Converts MeshData instances from the pipeline to Blender mesh objects.
Handles vertex conversion, face indexing, and UV coordinate mapping.
"""

import os

import bpy
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
    use_osm_id: bool = True
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

    # Add UV layer if UVs exist
    if mesh_data.uvs and mesh_data.face_uvs:
        _add_uv_layer(mesh, mesh_data)

    # Update mesh to compute normals, etc.
    mesh.update()

    # Link object to collection
    if collection is None:
        collection = bpy.context.collection
    collection.objects.link(obj)

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
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Get the default Principled BSDF (already created by use_nodes=True)
    principled = nodes.get("Principled BSDF")

    if texture_path:
        if not os.path.isfile(texture_path):
            print(f"[Condor] WARNING: texture file not found: {texture_path}")
        else:
            print(f"[Condor] Loading texture: {texture_path}")
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

            # Connect Color → Base Color
            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
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


def _assign_material(
    obj: bpy.types.Object,
    group_name: str,
    texture_dir: Optional[str] = None
) -> None:
    """
    Assign a material to a Blender object based on its group name.

    Reuses existing materials if already created (avoids duplicates
    when importing multiple patches).

    Args:
        obj: Blender object to assign material to
        group_name: Mesh group name (e.g., 'houses', 'flat_roof_1')
        texture_dir: Directory where .dds textures are located
    """
    texture_filename = TEXTURE_MAP.get(group_name)
    if not texture_filename:
        return

    mat_name = f"condor_{group_name}"

    # Reuse existing material if already created
    if mat_name in bpy.data.materials:
        mat = bpy.data.materials[mat_name]
    else:
        # Build texture path with diagnostic logging
        texture_path = None
        if texture_dir:
            texture_path = _find_texture_file(texture_dir, texture_filename)
            if not texture_path:
                print(f"[Condor] Texture NOT FOUND: '{texture_filename}' in '{texture_dir}'")
                if os.path.isdir(texture_dir):
                    contents = os.listdir(texture_dir)
                    print(f"[Condor]   Directory contains {len(contents)} files: {contents[:20]}")
                else:
                    print(f"[Condor]   Directory does NOT exist: '{texture_dir}'")
                    # Check parent directory
                    parent = os.path.dirname(texture_dir)
                    if os.path.isdir(parent):
                        print(f"[Condor]   Parent '{parent}' contains: {os.listdir(parent)[:20]}")
        else:
            print(f"[Condor] No texture_dir provided for '{group_name}'")

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


def import_grouped_meshes_to_blender(
    grouped_meshes: Dict,  # Dict[str, MeshData]
    collection_name: str = "Condor Buildings",
    texture_dir: Optional[str] = None
) -> List[bpy.types.Object]:
    """
    Import grouped meshes to Blender as separate named objects.

    Each group (houses, apartment_walls, etc.) becomes a separate Blender
    object with the group name. This matches the OBJ export format with
    multiple 'o' objects.

    Each object gets a Principled BSDF material with Image Texture
    pointing to the corresponding .dds file (if found in texture_dir).

    Args:
        grouped_meshes: Dictionary mapping group name to MeshData
        collection_name: Name for the collection to hold buildings
        texture_dir: Directory containing .dds textures (e.g., Working/Autogen/Textures/)

    Returns:
        List of created Blender objects
    """
    if not grouped_meshes:
        return []

    # Create collection for buildings
    collection = create_buildings_collection(collection_name)

    # Import each group as a named object
    objects = []
    for group_name, mesh_data in grouped_meshes.items():
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
        _assign_material(obj, group_name, texture_dir)

        objects.append(obj)

    return objects
