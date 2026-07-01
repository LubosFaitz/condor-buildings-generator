"""
Condor Buildings Generator - Blender Operators

Defines operators for importing buildings and other actions.

Updated for Condor workflow:
- Auto-detects patch files from Condor folder structure
- Downloads OSM data from Overpass API
- Supports batch processing of multiple patches
- Saves to Working/Autogen folder
"""

import bpy
from bpy.types import Operator
import os


# =============================================================================
# Shared helpers (used by the Condor export operator)
# =============================================================================

def _merge_gabled_for_obj_export(groups):
    gabled = groups.get('gabled_roofs_lod0')
    if gabled is None or gabled.is_empty():
        return groups
    try:
        from ..models.mesh import MeshData
    except ImportError:
        return groups
    merged_houses = MeshData()
    if groups.get('houses') and not groups['houses'].is_empty():
        merged_houses.merge(groups['houses'])
    merged_houses.merge(gabled)
    result = {k: v for k, v in groups.items() if k not in ('gabled_roofs_lod0', 'houses')}
    result['houses'] = merged_houses
    return result


def _detect_obj_forward_axis(obj_path):
    """Pick the OBJ import forward axis based on the file header.

    Files written by export_condor_obj_mtl bake the Condor axis swap
    (Forward X) and carry a "# Axis swap: True" header line. Files written
    by export_mesh_groups keep raw pipeline coordinates (Forward Y) and have
    no such line. Returns 'X' for swapped files, otherwise 'Y'.
    """
    try:
        with open(obj_path, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                if line.startswith('#') and 'Axis swap: True' in line:
                    return 'X'
                if not line.startswith('#'):
                    break
    except OSError:
        pass
    return 'Y'


_REQUIRED_TEXTURES = [
    "Roof1.dds", "Roof2.dds", "Roof3.dds", "Roof4.dds", "Roof5.dds", "Roof6.dds",
    "Houses_Atlas.dds", "Highrise_Atlas.dds", "Industrial_Atlas.dds",
]


def _ensure_autogen_textures(paths):
    """Copy missing textures from the addon Textures folder to Working/Autogen/Textures."""
    import shutil
    addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest_dir = os.path.join(paths['autogen'], "Textures")
    os.makedirs(dest_dir, exist_ok=True)

    addon_tex_dir = os.path.join(addon_root, "Textures")
    for tex in _REQUIRED_TEXTURES:
        dest = os.path.join(dest_dir, tex)
        if not os.path.exists(dest):
            src = os.path.join(addon_tex_dir, tex)
            if os.path.exists(src):
                shutil.copy2(src, dest)


def _copy_asset_texture_if_missing(src_path, dest_dir, filename):
    """Copy a single asset texture to dest_dir only if not already present."""
    import shutil
    dest = os.path.join(dest_dir, filename)
    if not os.path.exists(dest) and os.path.exists(src_path):
        shutil.copy2(src_path, dest)


def _copy_asset_textures_for_result(result, paths):
    """Copy pylons/wind-turbine textures to Autogen/Textures for groups that were generated."""
    addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pylons_dir = os.path.join(addon_root, "assets", "pylons")
    dest_dir = os.path.join(paths['autogen'], "Textures")

    all_groups = set()
    for g in (result.grouped_lod0 or {}, result.grouped_lod1 or {}):
        all_groups.update(g.keys())

    if 'pylones' in all_groups:
        _copy_asset_texture_if_missing(os.path.join(pylons_dir, "Pylons.dds"), dest_dir, "Pylons.dds")

    if any(k == 'wind_turbine' or k.startswith('wind_turbine_') for k in all_groups):
        _copy_asset_texture_if_missing(os.path.join(pylons_dir, "WindTurbine.dds"), dest_dir, "WindTurbine.dds")


def resolve_condor_paths(props):
    """Resolve Condor folder paths from properties. Returns dict or None."""
    condor_path = bpy.path.abspath(props.condor_path)
    landscape = props.landscape_name

    paths = {
        'landscape': os.path.join(condor_path, "Landscapes", landscape),
        'working': os.path.join(condor_path, "Landscapes", landscape, "Working"),
        'heightmaps': os.path.join(condor_path, "Landscapes", landscape, "Working", "Heightmaps"),
        'autogen': os.path.join(condor_path, "Landscapes", landscape, "Working", "Autogen"),
    }

    if not os.path.isdir(paths['working']):
        return None

    if not os.path.exists(paths['autogen']):
        os.makedirs(paths['autogen'], exist_ok=True)

    return paths


def resolve_patch_list(props):
    """Build the list of patch IDs from properties."""
    if props.single_patch_mode:
        return [props.patch_id]

    patches = []
    for y in range(props.patch_y_min, props.patch_y_max + 1):
        for x in range(props.patch_x_min, props.patch_x_max + 1):
            patches.append(f"{x:03d}{y:03d}")
    return patches


def resolve_patch_files(patch_id, paths):
    """Find h*.txt and h*.obj for a patch. Returns (txt, obj) or (None, None)."""
    heightmaps_dir = paths['heightmaps']
    txt_path = os.path.join(heightmaps_dir, f"h{patch_id}.txt")
    obj_path = os.path.join(heightmaps_dir, f"h{patch_id}.obj")
    if os.path.exists(txt_path) and os.path.exists(obj_path):
        return txt_path, obj_path
    return None, None


def _attach_patch_log(output_dir, patch_id):
    """
    Attach a detailed (DEBUG) file log ``o<patch>.log`` next to the exported
    OBJ/MTL/JSON, in the SAME format as the command-line runner (see
    main.setup_logging). Used in file mode (Import to Blender off) so the readme's
    "detailed processing log" is actually produced.

    Returns the handler (or None on failure). Wrapped so generation never breaks
    on a logging problem.
    """
    import logging
    try:
        os.makedirs(output_dir, exist_ok=True)
        handler = logging.FileHandler(
            os.path.join(output_dir, f"o{patch_id}.log"), mode='w', encoding='utf-8'
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        root = logging.getLogger()
        # Remember the root level so we can restore it; raise it to DEBUG so the
        # records actually reach the file handler (each handler still filters by
        # its own level, so the Blender console is not spammed).
        handler._old_root_level = root.level
        if root.level == logging.NOTSET or root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        return handler
    except Exception:
        return None


def _detach_patch_log(handler):
    """Remove + close the patch log handler and restore the root logger level."""
    if handler is None:
        return
    import logging
    try:
        root = logging.getLogger()
        root.removeHandler(handler)
        handler.close()
        old = getattr(handler, '_old_root_level', None)
        if old is not None:
            root.setLevel(old)
    except Exception:
        pass


class CONDOR_OT_import_buildings(Operator):
    """Import buildings from OSM data for Condor 3 flight simulator"""

    bl_idname = "condor.import_buildings"
    bl_label = "Generate Condor Buildings"
    bl_description = "Generate and import 3D buildings from OSM data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Check if operator can run."""
        props = context.scene.condor_buildings

        # Check Condor path and landscape
        if not props.condor_path or props.landscape_name == 'NONE':
            return False

        # Check patch selection
        if props.single_patch_mode:
            return bool(props.patch_id)
        else:
            return props.patch_x_max >= props.patch_x_min

    def get_condor_paths(self, context):
        """
        Get all relevant paths from Condor folder structure.

        Returns:
            dict with paths or None if invalid
        """
        props = context.scene.condor_buildings
        condor_path = bpy.path.abspath(props.condor_path)
        landscape = props.landscape_name

        paths = {
            'landscape': os.path.join(condor_path, "Landscapes", landscape),
            'working': os.path.join(condor_path, "Landscapes", landscape, "Working"),
            'heightmaps': os.path.join(condor_path, "Landscapes", landscape, "Working", "Heightmaps"),
            'autogen': os.path.join(condor_path, "Landscapes", landscape, "Working", "Autogen"),
        }

        # Validate paths exist
        if not os.path.isdir(paths['working']):
            return None

        # Create Autogen folder if it doesn't exist
        if not os.path.exists(paths['autogen']):
            os.makedirs(paths['autogen'], exist_ok=True)

        return paths

    def get_patch_list(self, context, paths):
        """
        Get list of patch IDs to process.

        Returns:
            List of patch ID strings (e.g., ["036019", "036020"])
        """
        props = context.scene.condor_buildings

        if props.single_patch_mode:
            return [props.patch_id]

        # Generate patch IDs from range
        patches = []
        for y in range(props.patch_y_min, props.patch_y_max + 1):
            for x in range(props.patch_x_min, props.patch_x_max + 1):
                # Format as 6-digit ID: XXXYY (with leading zeros)
                patch_id = f"{x:03d}{y:03d}"
                patches.append(patch_id)

        return patches

    def find_patch_files(self, patch_id, paths):
        """
        Find h*.txt and h*.obj files for a patch.

        Args:
            patch_id: Patch ID string (e.g., "036019")
            paths: Dict with Condor paths

        Returns:
            Tuple of (txt_path, obj_path) or (None, None) if not found
        """
        heightmaps_dir = paths['heightmaps']

        txt_path = os.path.join(heightmaps_dir, f"h{patch_id}.txt")
        obj_path = os.path.join(heightmaps_dir, f"h{patch_id}.obj")

        if os.path.exists(txt_path) and os.path.exists(obj_path):
            return txt_path, obj_path

        return None, None

    def execute(self, context):
        """Execute the import operation."""
        import time

        # Import pipeline modules
        try:
            from ..main import run_pipeline
            from ..config import PipelineConfig, RoofSelectionMode, build_texture_map
            from ..io.patch_metadata import load_patch_metadata
            from ..generators import configure_generator
            from ..generators.powerlines import pylon_assets_dir
            from .mesh_converter import import_meshes_to_blender, import_grouped_meshes_to_blender, cleanup_buildings_collection
            from .osm_downloader import download_osm_for_patch
        except ImportError as e:
            self.report({'ERROR'}, f"Failed to import pipeline modules: {e}")
            return {'CANCELLED'}

        props = context.scene.condor_buildings

        # Get Condor paths
        paths = self.get_condor_paths(context)
        if not paths:
            self.report({'ERROR'}, f"Invalid Condor folder structure for landscape: {props.landscape_name}")
            return {'CANCELLED'}

        # Copy missing textures to Working/Autogen/Textures
        _ensure_autogen_textures(paths)

        # Get list of patches to process
        patch_ids = self.get_patch_list(context, paths)

        if not patch_ids:
            self.report({'ERROR'}, "No patches to process")
            return {'CANCELLED'}

        # Map roof selection mode enum
        roof_mode = RoofSelectionMode.GEOMETRY
        if props.roof_selection_mode == 'OSM_TAGS_ONLY':
            roof_mode = RoofSelectionMode.OSM_TAGS_ONLY

        # Configure generator with UI parameters
        configure_generator(
            gable_height=props.gable_height,
            hipped_height=props.gable_height,  # Same as gable for consistency
            roof_overhang_lod0=props.roof_overhang,
            floor_z_epsilon=props.floor_z_epsilon,
            gabled_max_floors=props.gabled_max_floors,
            hipped_max_floors=props.gabled_max_floors,  # Same as gabled
            gabled_min_rectangularity=props.gabled_min_rectangularity,
            polyskel_max_vertices=props.polyskel_max_vertices,
            house_max_area=props.house_max_area,
            house_max_side=props.house_max_side,
            house_min_side=props.house_min_side,
            house_max_aspect=props.house_max_aspect,
            flat_roof_merge=props.flat_roof_merge,
            flat_roof_terrain_photo=props.flat_roof_terrain_photo if props.flat_roof_merge else False,
        )

        # Track terrain objects imported during this run (for cleanup on failure)
        imported_terrain_names = []

        # --- SINGLE PATCH TERRAIN IMPORT ---
        if props.single_patch_mode and props.patch_id and props.import_to_blender and not props.batch_processing:
            patch_id = str(props.patch_id)
            terrain_obj_name = f"TR3f{patch_id}" if props.patch_tref else f"TR3{patch_id}"
            if not bpy.data.objects.get(terrain_obj_name):
                heightmaps_dir = paths['heightmaps']
                if props.patch_tref:
                    terrain_obj_path = os.path.join(heightmaps_dir, "22.5m", f"h{patch_id}.obj")
                    if not os.path.exists(terrain_obj_path):
                        self.report({'ERROR'}, f"File h{patch_id}.obj not found in Working/Heightmaps/22.5m/")
                        return {'CANCELLED'}
                else:
                    modified_path = os.path.join(heightmaps_dir, "modified", f"h{patch_id}.obj")
                    default_path = os.path.join(heightmaps_dir, f"h{patch_id}.obj")
                    terrain_obj_path = modified_path if os.path.exists(modified_path) else default_path

                if os.path.exists(terrain_obj_path):
                    collection_name_terrain = "Patch_Terrain"
                    terrain_col = bpy.data.collections.get(collection_name_terrain)
                    if not terrain_col:
                        terrain_col = bpy.data.collections.new(collection_name_terrain)
                        context.scene.collection.children.link(terrain_col)

                    def find_layer_collection(layer_collection, name):
                        if layer_collection.name == name:
                            return layer_collection
                        for child in layer_collection.children:
                            res = find_layer_collection(child, name)
                            if res:
                                return res
                        return None

                    layer_col = find_layer_collection(context.view_layer.layer_collection, collection_name_terrain)
                    prev_active_col = context.view_layer.active_layer_collection
                    if layer_col:
                        context.view_layer.active_layer_collection = layer_col

                    if hasattr(bpy.ops.wm, 'obj_import'):
                        bpy.ops.wm.obj_import(filepath=terrain_obj_path, forward_axis='Y', up_axis='Z')
                    else:
                        bpy.ops.import_scene.obj(filepath=terrain_obj_path, axis_forward='Y', axis_up='Z')

                    imported_objs = context.selected_objects
                    if imported_objs:
                        terrain_obj = imported_objs[0]
                        terrain_obj.name = terrain_obj_name
                        imported_terrain_names.append(terrain_obj_name)

                        mat_name = terrain_obj.name
                        mat = bpy.data.materials.get(mat_name)
                        if not mat:
                            mat = bpy.data.materials.new(name=mat_name)
                        mat.use_nodes = True
                        mat.node_tree.nodes.clear()

                        bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                        bsdf.location = (0, 0)
                        output_node = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                        output_node.location = (300, 0)
                        mat.node_tree.links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])

                        tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
                        tex_node.location = (-300, 0)
                        landscape_texture_dir = os.path.join(paths['landscape'], "Textures")
                        tex_path = os.path.join(landscape_texture_dir, f"t{patch_id}.dds")
                        if os.path.exists(tex_path):
                            img = bpy.data.images.load(tex_path)
                            tex_node.image = img
                        mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])

                        if terrain_obj.data.materials:
                            terrain_obj.data.materials[0] = mat
                        else:
                            terrain_obj.data.materials.append(mat)

                        mesh = terrain_obj.data
                        if not mesh.uv_layers:
                            mesh.uv_layers.new(name="UVMap")
                        uv_layer = mesh.uv_layers.active.data
                        verts = mesh.vertices
                        if len(verts) > 0:
                            min_x = min(v.co.x for v in verts)
                            max_x = max(v.co.x for v in verts)
                            min_y = min(v.co.y for v in verts)
                            max_y = max(v.co.y for v in verts)
                            dx = max_x - min_x if max_x != min_x else 1.0
                            dy = max_y - min_y if max_y != min_y else 1.0
                            for poly in mesh.polygons:
                                for loop_idx in poly.loop_indices:
                                    v_idx = mesh.loops[loop_idx].vertex_index
                                    v_co = verts[v_idx].co
                                    u = (v_co.x - min_x) / dx
                                    v = (v_co.y - min_y) / dy
                                    uv_layer[loop_idx].uv = (u, v)

                    context.view_layer.active_layer_collection = prev_active_col
        # --- END SINGLE PATCH TERRAIN IMPORT ---

        # Process patches
        start_time = time.time()
        total_buildings = 0
        total_objects = []
        patches_processed = 0
        errors = []
        total_ms_added = 0

        props.is_processing = True

        # Run log accumulator: collect per-patch blocks, then write a summary on
        # top + all blocks at the end (append-only, English).
        run_blocks = []          # list of (header, ms, object_names, airports)
        run_total_ms = 0
        run_n_lod0 = 0
        run_n_lod1 = 0

        try:
            for patch_id in patch_ids:
                props.current_patch = patch_id
                patch_start = time.time()

                # Force UI update
                bpy.context.view_layer.update()

                # --- TERRAIN IMPORT PER PATCH (range mode + checkbox) ---
                # Batch processing never imports terrain (keeps it light).
                if not props.single_patch_mode and props.import_patch_terrain and props.import_to_blender and not props.batch_processing:
                    terrain_obj_name = f"TR3{patch_id}"
                    if not bpy.data.objects.get(terrain_obj_name):
                        heightmaps_dir = paths['heightmaps']
                        modified_path = os.path.join(heightmaps_dir, "modified", f"h{patch_id}.obj")
                        default_path = os.path.join(heightmaps_dir, f"h{patch_id}.obj")
                        terrain_obj_path = modified_path if os.path.exists(modified_path) else default_path

                        if os.path.exists(terrain_obj_path):
                            terrain_col = bpy.data.collections.get("Patch_Terrain")
                            if not terrain_col:
                                terrain_col = bpy.data.collections.new("Patch_Terrain")
                                context.scene.collection.children.link(terrain_col)

                            def _find_layer_col(lc, name):
                                if lc.name == name:
                                    return lc
                                for child in lc.children:
                                    res = _find_layer_col(child, name)
                                    if res:
                                        return res
                                return None

                            layer_col = _find_layer_col(context.view_layer.layer_collection, "Patch_Terrain")
                            prev_active_col = context.view_layer.active_layer_collection
                            if layer_col:
                                context.view_layer.active_layer_collection = layer_col

                            if hasattr(bpy.ops.wm, 'obj_import'):
                                bpy.ops.wm.obj_import(filepath=terrain_obj_path, forward_axis='Y', up_axis='Z')
                            else:
                                bpy.ops.import_scene.obj(filepath=terrain_obj_path, axis_forward='Y', axis_up='Z')

                            imported_objs = context.selected_objects
                            if imported_objs:
                                terrain_obj = imported_objs[0]
                                terrain_obj.name = terrain_obj_name
                                imported_terrain_names.append(terrain_obj_name)

                                mat = bpy.data.materials.get(terrain_obj_name)
                                if not mat:
                                    mat = bpy.data.materials.new(name=terrain_obj_name)
                                mat.use_nodes = True
                                mat.node_tree.nodes.clear()
                                bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                                bsdf.location = (0, 0)
                                output_node = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                                output_node.location = (300, 0)
                                mat.node_tree.links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])
                                tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
                                tex_node.location = (-300, 0)
                                landscape_texture_dir = os.path.join(paths['landscape'], "Textures")
                                tex_path = os.path.join(landscape_texture_dir, f"t{patch_id}.dds")
                                if os.path.exists(tex_path):
                                    img = bpy.data.images.load(tex_path)
                                    tex_node.image = img
                                mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                                if terrain_obj.data.materials:
                                    terrain_obj.data.materials[0] = mat
                                else:
                                    terrain_obj.data.materials.append(mat)

                                mesh = terrain_obj.data
                                if not mesh.uv_layers:
                                    mesh.uv_layers.new(name="UVMap")
                                uv_layer = mesh.uv_layers.active.data
                                verts = mesh.vertices
                                if len(verts) > 0:
                                    min_x = min(v.co.x for v in verts)
                                    max_x = max(v.co.x for v in verts)
                                    min_y = min(v.co.y for v in verts)
                                    max_y = max(v.co.y for v in verts)
                                    dx = max_x - min_x if max_x != min_x else 1.0
                                    dy = max_y - min_y if max_y != min_y else 1.0
                                    for poly in mesh.polygons:
                                        for loop_idx in poly.loop_indices:
                                            v_idx = mesh.loops[loop_idx].vertex_index
                                            v_co = verts[v_idx].co
                                            u = (v_co.x - min_x) / dx
                                            v = (v_co.y - min_y) / dy
                                            uv_layer[loop_idx].uv = (u, v)

                            context.view_layer.active_layer_collection = prev_active_col
                # --- END TERRAIN IMPORT PER PATCH ---

                # Find patch files
                txt_path, obj_path = self.find_patch_files(patch_id, paths)

                if not txt_path:
                    errors.append(f"Patch {patch_id}: heightmap files not found")
                    continue

                # Load patch metadata
                try:
                    metadata = load_patch_metadata(txt_path)
                except Exception as e:
                    errors.append(f"Patch {patch_id}: failed to load metadata: {e}")
                    continue

                # Get OSM data
                osm_path = None

                if props.osm_source == 'DOWNLOAD':
                    # Download from Overpass API
                    download_result = download_osm_for_patch(
                        metadata,
                        output_dir=paths['autogen'],
                        filename_prefix="map"
                    )

                    if not download_result.success:
                        errors.append(f"Patch {patch_id}: OSM download failed: {download_result.error}")
                        continue

                    osm_path = download_result.filepath

                    # Wider 3x3 search for airports -> shared airport/airports.json,
                    # so warning balls appear near a runway even when it sits in a
                    # neighbouring patch. Guarded: never breaks generation.
                    try:
                        from .osm_downloader import download_airports_for_patch
                        download_airports_for_patch(metadata, paths['autogen'])
                    except Exception as e:
                        print(f"[Condor] airport search failed: {e}")

                else:
                    # Look for local OSM file in various locations
                    possible_paths = [
                        os.path.join(paths['autogen'], f"map_{patch_id}.osm"),
                        os.path.join(paths['working'], f"map_{patch_id}.osm"),
                        os.path.join(paths['heightmaps'], f"map_{patch_id}.osm"),
                    ]

                    for p in possible_paths:
                        if os.path.exists(p):
                            osm_path = p
                            break

                    if not osm_path:
                        errors.append(f"Patch {patch_id}: no local OSM file found")
                        continue

                # Build pipeline configuration
                config = PipelineConfig(
                    patch_id=patch_id,
                    patch_dir=paths['heightmaps'],  # For terrain mesh
                    zone_number=metadata.zone_number,
                    translate_x=metadata.translate_x,
                    translate_y=metadata.translate_y,
                    global_seed=props.global_seed,
                    export_groups=True,
                    output_dir=paths['autogen'] if props.save_to_autogen else "",
                    verbose=False,
                    roof_selection_mode=roof_mode,
                    random_hipped=props.random_hipped,
                    debug_osm_id=props.debug_osm_id if props.debug_osm_id else None,
                    # House-scale constraints
                    house_max_footprint_area=props.house_max_area,
                    house_max_side_length=props.house_max_side,
                    house_min_side_length=props.house_min_side,
                    house_max_aspect_ratio=props.house_max_aspect,
                    # Roof geometry parameters
                    gable_height=props.gable_height,
                    roof_overhang_lod0=props.roof_overhang,
                    floor_z_epsilon=props.floor_z_epsilon,
                    gabled_max_floors=props.gabled_max_floors,
                    # Advanced geometry constraints
                    gabled_min_rectangularity=props.gabled_min_rectangularity,
                    polyskel_max_vertices=props.polyskel_max_vertices,
                    # Flat roof merge + optional terrain photo
                    flat_roof_merge=props.flat_roof_merge,
                    flat_roof_terrain_photo=props.flat_roof_terrain_photo if props.flat_roof_merge else False,
                    flat_roof_industrial_only=props.flat_roof_industrial_only if props.flat_roof_terrain_photo else False,
                    # Optional powerlines (pylons + cables -> 'pylones' object)
                    generate_powerlines=props.generate_powerlines,
                    # Aviation warning balls on the top conductor (into 'pylones')
                    generate_warning_balls=props.warning_balls,
                )

                # Override OSM path in config
                config.osm_path = osm_path

                # MSprint: restore original if checkbox is off
                if not props.use_msprint and osm_path:
                    backup = osm_path + ".ori"
                    if os.path.exists(backup):
                        import shutil as _shutil
                        _shutil.copy2(backup, osm_path)
                        print(f"[MSprint] Patch {patch_id}: restored from backup {backup}")

                # MSprint: download and merge if checkbox is on
                if props.use_msprint and osm_path:
                    try:
                        from .msprint import download_ms_buildings, merge_ms_into_osm
                        msprint_dir = os.path.join(paths['autogen'], "MSprint")
                        cache_dir = os.path.join(msprint_dir, "_cache")
                        ms_osm = download_ms_buildings(metadata, msprint_dir, patch_id, cache_dir)
                        if ms_osm and os.path.exists(ms_osm):
                            added, skipped = merge_ms_into_osm(osm_path, ms_osm)
                            total_ms_added += added
                            print(f"[MSprint] Patch {patch_id}: added {added}, skipped {skipped}")
                        else:
                            self.report({'WARNING'}, f"Patch {patch_id}: MSprint download failed")
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        self.report({'WARNING'}, f"Patch {patch_id}: MSprint error: {e}")

                # File mode (Import to Blender off) also writes a detailed
                # o<patch>.log next to the OBJ/MTL/JSON (same format as the CLI
                # runner). Attach it BEFORE the pipeline so it captures the whole
                # run; wrapped so it can never break generation.
                file_mode = (not props.import_to_blender and props.save_to_autogen)
                patch_log = _attach_patch_log(paths['autogen'], patch_id) if file_mode else None

                # Run pipeline. Always "memory": file mode now also exports via a
                # brief Blender import (so pitched roofs are processed correctly),
                # done in batch_processing.export_filemode_via_blender below.
                output_mode = "memory"

                try:
                    result = run_pipeline(config, output_mode=output_mode)
                except Exception as e:
                    errors.append(f"Patch {patch_id}: pipeline failed: {e}")
                    _detach_patch_log(patch_log)
                    continue

                if not result.success:
                    error_msg = "; ".join(result.report.errors) if result.report.errors else "Unknown error"
                    errors.append(f"Patch {patch_id}: {error_msg}")
                    _detach_patch_log(patch_log)
                    continue

                patches_processed += 1

                # File mode (Import to Blender off): export with CORRECT roofs by
                # briefly importing the groups into Blender (which doubles gabled
                # roofs + recalcs hipped normals, exactly like the normal import),
                # writing the OBJ (+ MTL if 'add mtl batch') and the JSON report,
                # adding the chimney if 'Batch' is on, then deleting the temp
                # objects. Logic in batch_processing.export_filemode_via_blender.
                patch_exported = None  # file-mode: suffix -> real exported object names
                if file_mode:
                    try:
                        # Copy the asset textures (Pylons.dds / WindTurbine.dds) used
                        # by the generated objects into Working/Autogen/Textures, just
                        # like the Import-to-Blender path does.
                        _copy_asset_textures_for_result(result, paths)
                        from .batch_processing import export_filemode_via_blender
                        patch_exported = export_filemode_via_blender(context, props, patch_id, paths, result)
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        errors.append(f"Patch {patch_id}: file-mode export failed: {e}")
                    finally:
                        _detach_patch_log(patch_log)

                # Import to Blender if requested
                if props.import_to_blender:
                    collection_name = f"Condor_{props.landscape_name}_{patch_id}"
                    cleanup_buildings_collection(collection_name)

                    # Texture directories: building atlases live in
                    # Working/Autogen/Textures; the per-patch orthophoto
                    # t<patch>.dds lives in the landscape Textures folder.
                    texture_dir = os.path.join(paths['autogen'], "Textures")
                    landscape_texture_dir = os.path.join(paths['landscape'], "Textures")
                    print(f"[Condor] Texture directory: {texture_dir}")
                    print(f"[Condor] Texture dir exists: {os.path.isdir(texture_dir)}")

                    # Per-patch texture map (points merged flat_roof at t<patch>.dds)
                    tex_map = build_texture_map(patch_id, props.flat_roof_terrain_photo)
                    extra_dirs = [landscape_texture_dir]
                    # Pylons.dds ships inside the addon; add its folder so the
                    # 'pylones' preview is textured even before the user copies it
                    # into the landscape Textures folder.
                    if props.generate_powerlines:
                        extra_dirs.append(pylon_assets_dir())

                    # Copy asset textures only for groups that were actually generated
                    _copy_asset_textures_for_result(result, paths)

                    # Use new grouped meshes (v0.6.3+)
                    if props.output_lod in ('LOD0', 'BOTH') and result.grouped_lod0:
                        try:
                            objects = import_grouped_meshes_to_blender(
                                result.grouped_lod0,
                                collection_name=collection_name,
                                texture_dir=texture_dir,
                                texture_map=tex_map,
                                extra_texture_dirs=extra_dirs,
                            )
                            total_objects.extend(objects)
                            total_buildings += len(objects)
                        except Exception as e:
                            errors.append(f"Patch {patch_id}: Blender import failed: {e}")

                    if props.output_lod == 'LOD1' and result.grouped_lod1:
                        try:
                            objects = import_grouped_meshes_to_blender(
                                result.grouped_lod1,
                                collection_name=collection_name + "_LOD1",
                                texture_dir=texture_dir,
                                texture_map=tex_map,
                                extra_texture_dirs=extra_dirs,
                            )
                            total_objects.extend(objects)
                            total_buildings += len(objects)
                        except Exception as e:
                            errors.append(f"Patch {patch_id}: LOD1 import failed: {e}")

                    # Also import LOD1 if BOTH
                    if props.output_lod == 'BOTH' and result.grouped_lod1:
                        collection_name_lod1 = f"Condor_{props.landscape_name}_{patch_id}_LOD1"
                        cleanup_buildings_collection(collection_name_lod1)

                        try:
                            import_grouped_meshes_to_blender(
                                result.grouped_lod1,
                                collection_name=collection_name_lod1,
                                texture_dir=texture_dir,
                                texture_map=tex_map,
                                extra_texture_dirs=extra_dirs,
                            )
                        except Exception as e:
                            errors.append(f"Patch {patch_id}: LOD1 import failed: {e}")

                # Batch processing: with Batch processing + Import to Blender on,
                # run the whole chain for THIS freshly imported patch (rotate
                # turbines -> merge -> export OBJ+MTL -> delete), then continue to
                # the next patch. Logic lives in the standalone batch_processing.py.
                print(f"[BATCH] check patch {patch_id}: batch_processing={props.batch_processing}, import_to_blender={props.import_to_blender}")
                if props.batch_processing and props.import_to_blender:
                    try:
                        from .batch_processing import process_patch
                        errors.extend(process_patch(context, props, patch_id, paths))
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        errors.append(f"Patch {patch_id}: batch processing failed: {e}")

                # Run log (generate_log.txt in Working/Autogen): one block per LOD
                # (<patch> and <patch>_LOD1), each with the generation time and the
                # FINAL objects one per line. gabled_roofs/hipped_roofs are merged
                # into 'houses' on import, so the log lists 'houses' (not the raw
                # roof groups). Append-only (never reset).
                try:
                    from .batch_processing import append_run_log, airports_in_patch
                    from ..projection.transverse_mercator import TransverseMercatorProjector
                    from ..config import PATCH_HALF
                    # Raw roof groups that get joined into 'houses' on import.
                    ROOF_MERGE = {'gabled_roofs_lod0': 'houses', 'hipped_roofs': 'houses'}
                    # If this patch has aerialways (merged into 'pylones'), mark it.
                    has_aerial = getattr(result.report.stats, 'aerialways', 0) > 0

                    def _final_names(grouped):
                        names = []
                        for k in (grouped or {}):
                            nm = ROOF_MERGE.get(k, k)
                            if nm not in names:
                                names.append(nm)
                        names.sort()
                        if props.chimney_batch and 'chimney' not in names:
                            names.append('chimney')
                        if has_aerial:
                            names = [n + " (aerialway)" if n == 'pylones' else n for n in names]
                        return names

                    try:
                        projector = TransverseMercatorProjector(
                            metadata.zone_number, metadata.translate_x, metadata.translate_y)
                        airports = airports_in_patch(paths['autogen'], projector, PATCH_HALF)
                    except Exception:
                        airports = []
                    def _mark(names):
                        names = sorted(names)
                        if has_aerial:
                            names = [n + " (aerialway)" if n == 'pylones' else n for n in names]
                        return names

                    patch_ms = int((time.time() - patch_start) * 1000)
                    run_total_ms += patch_ms
                    if patch_exported is not None:
                        # File mode: the REAL objects written to the OBJ - chimney /
                        # transmitter appear only if actually generated.
                        lod0_names = _mark(patch_exported.get("", []))
                        lod1_names = _mark(patch_exported.get("_LOD1", []))
                        has_lod1 = "_LOD1" in patch_exported
                    else:
                        lod0_names = _final_names(result.grouped_lod0)
                        lod1_names = _final_names(result.grouped_lod1)
                        has_lod1 = bool(result.grouped_lod1)
                    run_blocks.append((patch_id, patch_ms, lod0_names, airports))
                    run_n_lod0 += 1
                    if has_lod1:
                        run_blocks.append((f"{patch_id}_LOD1", patch_ms, lod1_names, airports))
                        run_n_lod1 += 1
                except Exception as e:
                    print(f"[Condor] run log collect failed: {e}")

            # Write the run log: summary on top, then each collected block.
            try:
                from .batch_processing import write_run_summary, append_run_log
                if run_blocks:
                    write_run_summary(paths['autogen'], run_n_lod0,
                                      run_n_lod0, run_n_lod1, run_total_ms)
                    for (hdr, ms, objs, airs) in run_blocks:
                        append_run_log(paths['autogen'], hdr, ms, objs, airs)
            except Exception as e:
                print(f"[Condor] run log write failed: {e}")

        finally:
            props.is_processing = False
            props.current_patch = ""

        # Update statistics
        elapsed_ms = int((time.time() - start_time) * 1000)
        props.last_import_buildings = total_buildings
        props.last_import_time_ms = elapsed_ms
        props.last_patches_processed = patches_processed

        # Report results
        if errors:
            for error in errors[:5]:  # Show first 5 errors
                self.report({'WARNING'}, error)
            if len(errors) > 5:
                self.report({'WARNING'}, f"... and {len(errors) - 5} more errors")

        if patches_processed > 0:
            # Check texture status for diagnostics
            tex_ok = sum(1 for m in bpy.data.materials if m.name.startswith("condor_") and m.node_tree and any(n.type == 'TEX_IMAGE' and n.image for n in m.node_tree.nodes))
            tex_total = sum(1 for m in bpy.data.materials if m.name.startswith("condor_"))
            tex_msg = f" | Textures: {tex_ok}/{tex_total}" if tex_total > 0 else ""
            ms_msg = f" | MSprint added: {total_ms_added}" if total_ms_added > 0 else ""
            print(f"[Condor] Generation complete: {total_buildings} objects, {patches_processed} patch(es), {elapsed_ms}ms{ms_msg}")

            self.report(
                {'INFO'},
                f"Generated {total_buildings} objects from {patches_processed} patches in {elapsed_ms}ms{tex_msg}"
            )

            # --- POSITION PATCHES (only when terrain checkbox is on) ---
            # Skipped in batch mode (patches are exported and deleted per patch).
            if not props.single_patch_mode and props.import_patch_terrain and not props.batch_processing:
                min_x = props.patch_x_min
                min_y = props.patch_y_min
                for x in range(props.patch_x_min, props.patch_x_max + 1):
                    for y in range(props.patch_y_min, props.patch_y_max + 1):
                        patch_id = f"{x:03d}{y:03d}"
                        offset_x = -(x - min_x) * 5760.0
                        offset_y = (y - min_y) * 5760.0
                        if offset_x == 0.0 and offset_y == 0.0:
                            continue
                        col_name = f"Condor_{props.landscape_name}_{patch_id}"
                        col = bpy.data.collections.get(col_name)
                        if col:
                            for obj in col.objects:
                                obj.location.x += offset_x
                                obj.location.y += offset_y
                        terrain_obj = bpy.data.objects.get(f"TR3{patch_id}")
                        if terrain_obj:
                            terrain_obj.location.x += offset_x
                            terrain_obj.location.y += offset_y
            # --- END POSITION PATCHES ---

            # --- SET VIEWPORT ---
            import math as _math
            import mathutils as _mu
            layout_ws = bpy.data.workspaces.get("Layout")
            if layout_ws and context.window.workspace != layout_ws:
                context.window.workspace = layout_ws
            target_ws = layout_ws or context.window.workspace
            for screen in target_ws.screens:
                for area in screen.areas:
                    if area.type == 'VIEW_3D':
                        for space in area.spaces:
                            if space.type == 'VIEW_3D':
                                space.shading.type = 'MATERIAL'
                                space.lens = 50.0
                                space.clip_start = 0.009999999776482582
                                space.clip_end = 100000.0
                                r3d = space.region_3d
                                r3d.view_distance = 9051.04
                                r3d.view_rotation = _mu.Euler((0.0, -0.0, 0.0), 'XYZ').to_quaternion()
                                r3d.view_perspective = 'ORTHO'
                                r3d.view_location = (0.0, 0.0, 0.0)
                                first_patch = patch_ids[0]
                                terrain_lock = bpy.data.objects.get(
                                    f"TR3f{first_patch}" if props.single_patch_mode and props.patch_tref
                                    else f"TR3{first_patch}"
                                )
                                if terrain_lock:
                                    space.lock_object = None
                        break
            # --- END SET VIEWPORT ---

            return {'FINISHED'}
        else:
            # Clean up terrain imported during this failed run (scene + outliner)
            for t_name in imported_terrain_names:
                t_obj = bpy.data.objects.get(t_name)
                if t_obj:
                    bpy.data.objects.remove(t_obj, do_unlink=True)
            terrain_col = bpy.data.collections.get("Patch_Terrain")
            if terrain_col and len(terrain_col.objects) == 0:
                bpy.data.collections.remove(terrain_col)

            self.report({'ERROR'}, "No patches were processed successfully")
            return {'CANCELLED'}


class CONDOR_OT_clear_buildings(Operator):
    """Remove all imported Condor buildings from the scene"""

    bl_idname = "condor.clear_buildings"
    bl_label = "Clear Buildings"
    bl_description = "Remove all buildings from Condor collections"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Check if there are buildings to clear."""
        # Check for any collection starting with "Condor_"
        for collection in bpy.data.collections:
            if collection.name.startswith("Condor_"):
                return True
        return False

    def execute(self, context):
        """Execute the clear operation."""
        from .mesh_converter import cleanup_buildings_collection

        count = 0

        # Find and clear all Condor collections
        collections_to_remove = [
            c.name for c in bpy.data.collections
            if c.name.startswith("Condor_")
        ]

        for collection_name in collections_to_remove:
            count += cleanup_buildings_collection(collection_name)

            # Also remove the empty collection
            if collection_name in bpy.data.collections:
                collection = bpy.data.collections[collection_name]
                if len(collection.objects) == 0:
                    bpy.data.collections.remove(collection)

        # Reset stats
        props = context.scene.condor_buildings
        props.last_import_buildings = 0
        props.last_import_time_ms = 0
        props.last_patches_processed = 0

        self.report({'INFO'}, f"Removed {count} building objects")
        return {'FINISHED'}


class CONDOR_OT_export_condor(Operator):
    """Export Condor-ready OBJ + MTL (triangulated, axis-corrected, materials)"""

    bl_idname = "condor.export_condor"
    bl_label = "Export Condor OBJ+MTL"
    bl_description = (
        "Generate buildings and export Condor-ready OBJ + MTL to Working/Autogen. "
        "Files are triangulated, axis-corrected (Forward X / Up Z) and carry a .mtl "
        "with Condor material values - no manual tweaking in Blender required"
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        if not props.condor_path or props.landscape_name == 'NONE':
            return False
        if props.single_patch_mode:
            return bool(props.patch_id)
        return props.patch_x_max >= props.patch_x_min

    def execute(self, context):
        import time
        import shutil

        try:
            from ..config import build_texture_map, CONDOR_AXIS_SWAP, CONDOR_EXPORT_TRIANGULATE, CONDOR_EXPORT_NORMALS
            from ..io.obj_exporter import export_condor_obj_mtl
            from ..generators.powerlines import pylon_texture_path
            from .mesh_converter import blender_obj_to_meshdata
        except ImportError as e:
            self.report({'ERROR'}, f"Failed to import modules: {e}")
            return {'CANCELLED'}

        props = context.scene.condor_buildings

        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, f"Invalid Condor folder structure for landscape: {props.landscape_name}")
            return {'CANCELLED'}

        patch_ids = resolve_patch_list(props)
        if not patch_ids:
            self.report({'ERROR'}, "No patches to process")
            return {'CANCELLED'}

        start_time = time.time()
        files_written = []
        patches_exported = 0
        errors = []

        for patch_id in patch_ids:
            # LOD0: collection Condor_{landscape}_{patch}
            # LOD1: collection Condor_{landscape}_{patch}_LOD1
            lods = []
            if props.output_lod in ('LOD0', 'BOTH'):
                col_name = f"Condor_{props.landscape_name}_{patch_id}"
                col = bpy.data.collections.get(col_name)
                if col:
                    lods.append(("LOD0", col))
                else:
                    errors.append(f"Patch {patch_id}: kolekce '{col_name}' neexistuje — nejdřív spusť Generate Buildings")
            if props.output_lod in ('LOD1', 'BOTH'):
                col_name_lod1 = f"Condor_{props.landscape_name}_{patch_id}_LOD1"
                col1 = bpy.data.collections.get(col_name_lod1)
                if col1:
                    lods.append(("LOD1", col1))
                elif props.output_lod == 'LOD1':
                    errors.append(f"Patch {patch_id}: kolekce '{col_name_lod1}' neexistuje — nejdřív spusť Generate Buildings")

            if not lods:
                continue

            tex_map = build_texture_map(patch_id, props.flat_roof_terrain_photo)
            condor_tex_map = dict(tex_map)
            if props.flat_roof_terrain_photo and 'flat_roof' in condor_tex_map:
                condor_tex_map['flat_roof'] = "T_" + condor_tex_map['flat_roof']

            # Move objects to origin before export if patches are offset
            saved_locations = {}
            mesh_objs = [o for o in lods[0][1].objects if o.type == 'MESH']
            need_restore = not props.single_patch_mode and props.import_patch_terrain and mesh_objs
            if need_restore:
                bpy.ops.object.select_all(action='DESELECT')
                for obj in mesh_objs:
                    saved_locations[obj.name] = obj.location.copy()
                    obj.location = (0.0, 0.0, 0.0)
                    obj.select_set(True)
                context.view_layer.objects.active = mesh_objs[0]
                bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            for lod_name, col in lods:
                groups = {}
                for obj in col.objects:
                    if obj.type != 'MESH':
                        continue
                    md = blender_obj_to_meshdata(obj, osm_id=obj.name)
                    if md and not md.is_empty():
                        import re as _re
                        group_name = _re.sub(r'\.\d+$', '', obj.name)
                        groups[group_name] = md
                        if group_name not in condor_tex_map and obj.material_slots:
                            mat = obj.material_slots[0].material
                            if mat and mat.use_nodes and mat.node_tree:
                                for node in mat.node_tree.nodes:
                                    if node.type == 'TEX_IMAGE' and node.image:
                                        img = node.image
                                        tex_name = os.path.basename(bpy.path.abspath(img.filepath)) if img.filepath else img.name
                                        if tex_name:
                                            condor_tex_map[group_name] = tex_name
                                        break

                if not groups:
                    errors.append(f"Patch {patch_id} {lod_name}: kolekce neobsahuje žádné mesh objekty")
                    continue

                suffix = "" if lod_name == "LOD0" else f"_{lod_name}"
                fname = f"o{patch_id}{suffix}.obj"
                out_obj = os.path.join(paths['autogen'], fname)
                try:
                    export_condor_obj_mtl(
                        groups, out_obj, condor_tex_map,
                        comment=f"{lod_name} - Patch {patch_id} (Condor-ready)",
                        axis_swap=CONDOR_AXIS_SWAP,
                        triangulate=CONDOR_EXPORT_TRIANGULATE,
                        include_normals=CONDOR_EXPORT_NORMALS,
                    )
                    files_written.append(out_obj)
                except Exception as e:
                    errors.append(f"Patch {patch_id} {lod_name}: export failed: {e}")

            # Restore locations after export
            if need_restore:
                for obj in mesh_objs:
                    if obj.name in saved_locations:
                        obj.location = saved_locations[obj.name]

            if props.generate_powerlines:
                src_tex = pylon_texture_path()
                if src_tex:
                    tex_out_dir = os.path.join(paths['autogen'], "Textures")
                    os.makedirs(tex_out_dir, exist_ok=True)
                    dst_tex = os.path.join(tex_out_dir, os.path.basename(src_tex))
                    if not os.path.exists(dst_tex):
                        try:
                            shutil.copy2(src_tex, dst_tex)
                        except Exception as e:
                            errors.append(f"Patch {patch_id}: could not copy Pylons.dds: {e}")

            patches_exported += 1

        elapsed_ms = int((time.time() - start_time) * 1000)
        props.last_import_time_ms = elapsed_ms
        props.last_patches_processed = patches_exported

        if errors:
            for error in errors[:5]:
                self.report({'WARNING'}, error)
            if len(errors) > 5:
                self.report({'WARNING'}, f"... and {len(errors) - 5} more errors")

        if files_written:
            self.report(
                {'INFO'},
                f"Exported {len(files_written)} Condor OBJ+MTL file(s) from "
                f"{patches_exported} patch(es) in {elapsed_ms}ms -> Working/Autogen"
            )
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "No files were exported")
            return {'CANCELLED'}


class CONDOR_OT_export_terrain(Operator):
    """Export selected terrain patch"""

    bl_idname = "condor.export_terrain"
    bl_label = "Export Terrain"
    bl_description = "Export terrain object to heightmaps/modified folder"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return props.single_patch_mode and props.patch_id and props.condor_path and props.landscape_name != 'NONE'

    def execute(self, context):
        props = context.scene.condor_buildings
        patch_id = str(props.patch_id)

        obj_name = f"TR3f{patch_id}" if props.patch_tref else f"TR3{patch_id}"
        terrain_obj = bpy.data.objects.get(obj_name)
        if not terrain_obj:
            self.report({'ERROR'}, f"Object {obj_name} not found in scene.")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        terrain_obj.select_set(True)
        context.view_layer.objects.active = terrain_obj

        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        heightmaps_dir = paths['heightmaps']
        if props.patch_tref:
            modified_dir = os.path.join(heightmaps_dir, "22.5m", "modified")
        else:
            modified_dir = os.path.join(heightmaps_dir, "modified")
        os.makedirs(modified_dir, exist_ok=True)

        target_filename = f"h{patch_id}.obj"
        path_out = os.path.join(modified_dir, target_filename)

        if bpy.app.version >= (4, 0, 0):
            bpy.ops.wm.obj_export(
                filepath=path_out,
                export_selected_objects=True,
                forward_axis='Y',
                up_axis='Z',
                export_triangulated_mesh=True,
                export_normals=True,
                export_uv=True,
                export_materials=True,
                path_mode='COPY'
            )
        else:
            bpy.ops.export_scene.obj(
                filepath=path_out,
                use_selection=True,
                axis_forward='Y',
                axis_up='Z',
                use_triangles=True,
                use_normals=True,
                use_uvs=True,
                use_materials=True,
                path_mode='COPY'
            )

        if os.path.exists(path_out):
            self.report({'INFO'}, f"Terrain exported to: modified/{target_filename}")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Export failed.")
            return {'CANCELLED'}


class CONDOR_OT_merge_wind_turbines(Operator):
    """Merge all wind_turbine objects into one and apply transforms"""

    bl_idname = "condor.merge_wind_turbines"
    bl_label = "Merge wind_turbine"
    bl_description = "Merge all wind_turbine objects into one, apply transforms and assign condor_wind_turbine material"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _is_turbine(name):
        # Any object whose name CONTAINS 'wind_turbine' counts (the merge groups
        # by collection, so each patch/LOD collection is reduced to one turbine).
        # Robust to any naming: 'wind_turbine', 'wind_turbine_3', 'wind_turbine.002',
        # 'wind_turbine_NOVY', etc. - across multiple patches and both LODs.
        return 'wind_turbine' in name

    @classmethod
    def poll(cls, context):
        return any(cls._is_turbine(obj.name) for obj in bpy.data.objects)

    def execute(self, context):
        import re
        turbines = [
            obj for obj in bpy.data.objects
            if self._is_turbine(obj.name)
        ]

        if not turbines:
            self.report({'WARNING'}, "No wind_turbine objects found")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Group turbines by their Condor collection (= patch)
        by_collection = {}
        for obj in turbines:
            col_name = None
            for col in obj.users_collection:
                if col.name.startswith("Condor_"):
                    col_name = col.name
                    break
            by_collection.setdefault(col_name, []).append(obj)

        total = len(turbines)
        for col_name, objs in by_collection.items():
            bpy.ops.object.select_all(action='DESELECT')
            for obj in objs:
                obj.select_set(True)
            context.view_layer.objects.active = objs[0]

            bpy.ops.object.join()
            merged = context.active_object
            merged.name = "wind_turbine"
            merged.data.name = "wind_turbine"

            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            mat_name = "condor_wind_turbine"
            target_mat = bpy.data.materials.get(mat_name)
            merged.data.materials.clear()
            if target_mat:
                merged.data.materials.append(target_mat)
            for poly in merged.data.polygons:
                poly.material_index = 0

            if col_name and col_name in bpy.data.collections:
                target_col = bpy.data.collections[col_name]
                for c in list(merged.users_collection):
                    c.objects.unlink(merged)
                target_col.objects.link(merged)

        for mat in list(bpy.data.materials):
            if re.match(r'^condor_wind_turbine_\d+$', mat.name):
                mat.use_fake_user = False
                bpy.data.materials.remove(mat)

        self.report({'INFO'}, f"Merged {total} turbines into {len(by_collection)} patch(es)")
        return {'FINISHED'}


def _point_in_polygon(x, y, polygon):
    winding = 0
    n = len(polygon)
    xj, yj = polygon[n - 1]
    for i in range(n):
        xi, yi = polygon[i]
        if yj <= y:
            if yi > y:
                if (xi - xj) * (y - yj) - (x - xj) * (yi - yj) > 0:
                    winding += 1
        else:
            if yi <= y:
                if (xi - xj) * (y - yj) - (x - xj) * (yi - yj) < 0:
                    winding -= 1
        xj, yj = xi, yi
    return winding != 0


def _parse_height_str(height_str):
    try:
        return float(str(height_str).replace('m', '').strip())
    except (ValueError, TypeError):
        return 30.0


class CONDOR_OT_import_chimneys(Operator):
    """Import chimney objects from OSM data"""

    bl_idname = "condor.import_chimneys"
    bl_label = "Import Chimneys"
    bl_description = "Import chimney objects from existing OSM files"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        # With chimney 'Batch' on, merging runs automatically during generation,
        # so this manual button is disabled (non-functional).
        return props.condor_path and props.landscape_name != 'NONE' and not props.chimney_batch

    def execute(self, context):
        import xml.etree.ElementTree as ET
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "3Dobjects")
        chimney_tex_src = os.path.join(assets_dir, "Chimney.dds")
        chimney_tex_dest = os.path.join(paths['autogen'], "Textures", "Chimney.dds")
        chimney_tex_copied = os.path.exists(chimney_tex_dest)

        chimney_big_path = os.path.join(assets_dir, "chimney_big.obj")
        chimney_small_path = os.path.join(assets_dir, "chimney_small.obj")
        chimney_big_low_path = os.path.join(assets_dir, "chimney_big_low.obj")
        chimney_small_low_path = os.path.join(assets_dir, "chimney_small_low.obj")

        # Output LOD -> which variants to import. Each is (suffix, low models?):
        # LOD0 -> detailed models into the bare collection; LOD1 -> low models
        # into the _LOD1 collection; Both -> both.
        lod_variants = []
        if props.output_lod in ('LOD0', 'BOTH'):
            lod_variants.append(("", False))
        if props.output_lod in ('LOD1', 'BOTH'):
            lod_variants.append(("_LOD1", True))
        if not lod_variants:
            lod_variants.append(("", False))

        patch_ids = []
        if props.single_patch_mode and props.patch_id:
            patch_ids = [str(props.patch_id)]
        else:
            for x in range(props.patch_x_min, props.patch_x_max + 1):
                for y in range(props.patch_y_min, props.patch_y_max + 1):
                    patch_ids.append(f"{x:03d}{y:03d}")

        total = 0

        for patch_id in patch_ids:
            osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
            if not os.path.exists(osm_path):
                continue

            # Optional: pull this patch's chimneys out of an extra chimney.osm
            # source into map_<patch>.osm first (no-op if that file isn't there).
            from .batch_processing import inject_chimneys_from_source
            inject_chimneys_from_source(paths, patch_id)

            txt_files = [
                os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                os.path.join(paths['heightmaps'], f"H{patch_id}.txt"),
            ]
            txt_path = next((p for p in txt_files if os.path.exists(p)), None)
            if not txt_path:
                self.report({'WARNING'}, f"Patch {patch_id}: heightmap .txt not found, skipping")
                continue

            terrain_obj_file = os.path.join(paths['heightmaps'], f"h{patch_id}.obj")
            modified_file = os.path.join(paths['heightmaps'], "modified", f"h{patch_id}.obj")
            if os.path.exists(modified_file):
                terrain_obj_file = modified_file
            if not os.path.exists(terrain_obj_file):
                self.report({'WARNING'}, f"Patch {patch_id}: terrain h{patch_id}.obj not found, skipping")
                continue

            try:
                metadata = load_patch_metadata(txt_path)
                projector = TransverseMercatorProjector(
                    metadata.zone_number,
                    metadata.translate_x,
                    metadata.translate_y
                )
            except Exception:
                continue

            terrain_obj = bpy.data.objects.get(f"TR3{patch_id}")

            px = int(patch_id[:3])
            py = int(patch_id[3:])
            if not props.single_patch_mode:
                patch_offset_x = -(px - props.patch_x_min) * 5760.0
                patch_offset_y = (py - props.patch_y_min) * 5760.0
            else:
                patch_offset_x = 0.0
                patch_offset_y = 0.0

            terrain_orig_loc = None
            if props.import_patch_terrain and terrain_obj and (patch_offset_x != 0.0 or patch_offset_y != 0.0):
                terrain_orig_loc = terrain_obj.location.copy()
                terrain_obj.location = (0.0, 0.0, 0.0)
                bpy.context.view_layer.update()

            if not props.import_patch_terrain:
                from ..io.terrain_loader import load_terrain
                from ..models.geometry import Point2D
                try:
                    terrain_mesh = load_terrain(terrain_obj_file)
                except Exception:
                    terrain_mesh = None
            else:
                terrain_mesh = None

            try:
                tree = ET.parse(osm_path)
                root = tree.getroot()
            except Exception:
                continue

            node_coords = {}
            for node_elem in root.findall("node"):
                nid = node_elem.get("id")
                node_coords[nid] = (float(node_elem.get("lat")), float(node_elem.get("lon")))

            chimneys = []
            for node_elem in root.findall("node"):
                tags = {t.get("k"): t.get("v") for t in node_elem.findall("tag")}
                if tags.get("man_made") == "chimney":
                    lat = float(node_elem.get("lat"))
                    lon = float(node_elem.get("lon"))
                    has_height = "height" in tags
                    height = _parse_height_str(tags.get("height", "30"))
                    material = tags.get("material")
                    x, y = projector.project(lat, lon)
                    chimneys.append((x, y, height, has_height, material))

            for way_elem in root.findall("way"):
                tags = {t.get("k"): t.get("v") for t in way_elem.findall("tag")}
                if tags.get("man_made") == "chimney":
                    nds = way_elem.findall("nd")
                    if nds:
                        coords = [node_coords[nd.get("ref")] for nd in nds if nd.get("ref") in node_coords]
                        if coords:
                            poly_xy = [projector.project(lat, lon) for lat, lon in coords]
                            node_inside = any(_point_in_polygon(nx, ny, poly_xy) for nx, ny, *_ in chimneys)
                            if node_inside:
                                continue
                            lat = sum(c[0] for c in coords) / len(coords)
                            lon = sum(c[1] for c in coords) / len(coords)
                            has_height = "height" in tags
                            height = _parse_height_str(tags.get("height", "30"))
                            material = tags.get("material")
                            x, y = projector.project(lat, lon)
                            chimneys.append((x, y, height, has_height, material))

            if chimneys and not chimney_tex_copied:
                import shutil as _shutil
                if os.path.exists(chimney_tex_src):
                    os.makedirs(os.path.dirname(chimney_tex_dest), exist_ok=True)
                    _shutil.copy2(chimney_tex_src, chimney_tex_dest)
                    chimney_tex_copied = True

            patch_chimneys = []
            for lod_suffix, low in lod_variants:
                big_col = None
                small_col = None
                # Clear previous chimneys of THIS patch + LOD before re-importing.
                for obj in [o for o in bpy.data.objects
                            if o.get("patch_id") == patch_id and o.get("lod", "") == lod_suffix
                            and o.name.startswith("Chimney_")]:
                    bpy.data.objects.remove(obj, do_unlink=True)

                for idx, (cx, cy, height, has_height, material) in enumerate(chimneys):
                    # Model choice: brick -> small (brick texture); otherwise big
                    # when taller than 30 m, else small.
                    is_big = (material != "brick") and (height > 30)
                    if low:
                        obj_path = chimney_big_low_path if is_big else chimney_small_low_path
                    else:
                        obj_path = chimney_big_path if is_big else chimney_small_path
                    native_height = 100.0 if is_big else 30.0
                    if not os.path.exists(obj_path):
                        continue

                    if hasattr(bpy.ops.wm, 'obj_import'):
                        bpy.ops.wm.obj_import(filepath=obj_path, forward_axis='Y', up_axis='Z')
                    else:
                        bpy.ops.import_scene.obj(filepath=obj_path, axis_forward='Y', axis_up='Z')

                    imported = context.selected_objects
                    if not imported:
                        continue

                    ch_obj = imported[0]
                    ch_obj.name = f"Chimney_{patch_id}{lod_suffix}_{idx + 1:03d}"
                    ch_obj["patch_id"] = patch_id
                    ch_obj["lod"] = lod_suffix

                    # Scale model height to the real OSM height (only if a height
                    # tag is present); width stays, foot stays on the terrain.
                    if has_height and native_height > 0:
                        _s = height / native_height
                        ch_obj.scale = (_s, _s, _s)  # uniform - keep shape

                    foot_z = 0.0
                    got_foot = False
                    if props.import_patch_terrain and terrain_obj:
                        try:
                            depsgraph = context.evaluated_depsgraph_get()
                            terrain_eval = terrain_obj.evaluated_get(depsgraph)
                            hit, loc, _, _ = terrain_eval.ray_cast(
                                (cx, cy, 10000.0), (0.0, 0.0, -1.0))
                            if hit:
                                foot_z = loc.z
                                got_foot = True
                        except Exception:
                            # terrain HIDDEN -> no evaluated mesh; fall back to file
                            if terrain_mesh is None:
                                from ..io.terrain_loader import load_terrain
                                try:
                                    terrain_mesh = load_terrain(terrain_obj_file)
                                except Exception:
                                    terrain_mesh = None
                    if not got_foot and terrain_mesh:
                        from ..models.geometry import Point2D, BBox
                        pt = Point2D(cx, cy)
                        query_bbox = BBox(cx - 1, cy - 1, cx + 1, cy + 1)
                        for tri_idx in terrain_mesh.get_triangles_in_bbox(query_bbox):
                            tri = terrain_mesh.triangles[tri_idx]
                            if tri.contains_point_2d(pt):
                                z = tri.z_at_xy(cx, cy)
                                if z is not None:
                                    foot_z = z
                                    break

                    ch_obj.location = (cx, cy, foot_z)
                    patch_chimneys.append(ch_obj)

                    patch_col_name = f"Condor_{props.landscape_name}_{patch_id}{lod_suffix}"
                    patch_col = bpy.data.collections.get(patch_col_name)
                    if not patch_col:
                        patch_col = bpy.data.collections.new(patch_col_name)
                        context.scene.collection.children.link(patch_col)

                    if is_big:
                        if big_col is None:
                            big_col_name = f"chimney_big_{patch_id}{lod_suffix}"
                            big_col = bpy.data.collections.get(big_col_name) or bpy.data.collections.new(big_col_name)
                            if big_col.name not in [c.name for c in patch_col.children]:
                                patch_col.children.link(big_col)
                        for c in list(ch_obj.users_collection):
                            c.objects.unlink(ch_obj)
                        big_col.objects.link(ch_obj)
                    else:
                        if small_col is None:
                            small_col_name = f"chimney_small_{patch_id}{lod_suffix}"
                            small_col = bpy.data.collections.get(small_col_name) or bpy.data.collections.new(small_col_name)
                            if small_col.name not in [c.name for c in patch_col.children]:
                                patch_col.children.link(small_col)
                        for c in list(ch_obj.users_collection):
                            c.objects.unlink(ch_obj)
                        small_col.objects.link(ch_obj)

                    total += 1

            if terrain_orig_loc is not None and terrain_obj:
                terrain_obj.location = terrain_orig_loc
            if (patch_offset_x != 0.0 or patch_offset_y != 0.0):
                for ch in patch_chimneys:
                    ch.location.x += patch_offset_x
                    ch.location.y += patch_offset_y

        self.report({'INFO'}, f"Imported {total} chimneys")
        return {'FINISHED'}


class CONDOR_OT_merge_chimneys(Operator):
    """Merge chimney objects per patch"""

    bl_idname = "condor.merge_chimneys"
    bl_label = "Merge Chimneys"
    bl_description = "Merge all Chimney_* objects per patch into one object"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _is_chimney(name):
        # Any object whose name CONTAINS 'chimney' (case-insensitive) counts; the
        # merge groups by collection, so each patch/LOD collection is reduced to one
        # 'chimney'. Robust to any naming: 'Chimney_045034_001', 'chimney.002',
        # 'chimney_NOVY', etc. - across multiple patches and both LODs.
        return 'chimney' in name.lower()

    @staticmethod
    def _condor_collection(obj):
        # The Condor patch/LOD collection owning this object, either directly or as
        # the parent of its chimney_big_/chimney_small_ sub-collection.
        for col in obj.users_collection:
            if col.name.startswith("Condor_"):
                return col
        for col in obj.users_collection:
            for parent in bpy.data.collections:
                if parent.name.startswith("Condor_") and col.name in [c.name for c in parent.children]:
                    return parent
        return None

    @classmethod
    def poll(cls, context):
        return any(cls._is_chimney(obj.name) for obj in bpy.data.objects)

    def execute(self, context):
        import re as _re
        chimneys = [obj for obj in bpy.data.objects
                    if self._is_chimney(obj.name) and obj.name in context.view_layer.objects]
        if not chimneys:
            self.report({'WARNING'}, "No chimney objects found")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        props = context.scene.condor_buildings

        # Group chimneys by their owning Condor patch/LOD collection.
        by_col = {}
        for obj in chimneys:
            col = self._condor_collection(obj)
            by_col.setdefault(col.name if col else None, []).append(obj)

        for col_name, objs in by_col.items():
            bpy.ops.object.select_all(action='DESELECT')
            for obj in objs:
                obj.select_set(True)
            context.view_layer.objects.active = objs[0]
            bpy.ops.object.join()
            merged = context.active_object
            merged.name = "chimney"
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            # Patch id from the collection name (Condor_<landscape>_<patch>[_LOD1]).
            pid = None
            if col_name:
                m = _re.search(r'_(\d{6})(?:_LOD1)?$', col_name)
                if m:
                    pid = m.group(1)
            if pid and not props.single_patch_mode and props.import_patch_terrain:
                import mathutils
                terrain_obj = bpy.data.objects.get(f"TR3{pid}")
                if terrain_obj:
                    origin_pos = terrain_obj.location.copy()
                    merged.data.transform(mathutils.Matrix.Translation(-origin_pos))
                    merged.location = origin_pos

            mat_name = "condor_chimney"
            target_mat = bpy.data.materials.get(mat_name)
            merged.data.materials.clear()
            if target_mat:
                merged.data.materials.append(target_mat)

            target_col = bpy.data.collections.get(col_name) if col_name else None
            if target_col:
                for c in list(merged.users_collection):
                    c.objects.unlink(merged)
                target_col.objects.link(merged)

        for col_name in ("chimney_big", "chimney_small"):
            col = bpy.data.collections.get(col_name)
            if col and not col.objects:
                bpy.data.collections.remove(col)

        for mat in list(bpy.data.materials):
            if _re.match(r'^condor_chimney\.\d+$', mat.name):
                mat.use_fake_user = False
                bpy.data.materials.remove(mat)

        for col in list(bpy.data.collections):
            if (_re.match(r'^chimney_big_\d+(_LOD1)?$', col.name) or _re.match(r'^chimney_small_\d+(_LOD1)?$', col.name)):
                if len(col.objects) == 0:
                    for parent in list(bpy.data.collections):
                        if col.name in [c.name for c in parent.children]:
                            parent.children.unlink(col)
                    if col.name in [c.name for c in bpy.context.scene.collection.children]:
                        bpy.context.scene.collection.children.unlink(col)
                    bpy.data.collections.remove(col)

        self.report({'INFO'}, f"Merged chimneys for {len(by_col)} collection(s)")
        return {'FINISHED'}


def _collapse_collections_if_many(context):
    """When more than 2 'Condor_' collections are loaded, collapse the collection
    contents in every Outliner so the import isn't shown with everything expanded.
    Safe no-op if it can't run."""
    n_cols = sum(1 for c in bpy.data.collections if c.name.startswith("Condor_"))
    if n_cols <= 2:
        return

    # The Outliner tree is only rebuilt AFTER the import operator returns, so the
    # collapse must run deferred (via a timer) - otherwise it acts on a tree that
    # doesn't list the new collections yet and nothing happens.
    def _do_collapse():
        try:
            for window in bpy.context.window_manager.windows:
                screen = window.screen
                if screen is None:
                    continue
                for area in screen.areas:
                    if area.type != 'OUTLINER':
                        continue
                    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                    if region is None:
                        continue
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.outliner.show_one_level(open=False)
        except Exception as e:
            print(f"[Condor] collapse outliner failed: {e}")
        return None  # run once, don't repeat

    try:
        bpy.app.timers.register(_do_collapse, first_interval=0.2)
    except Exception as e:
        print(f"[Condor] collapse timer failed: {e}")


class CONDOR_OT_import_patch(bpy.types.Operator):
    bl_idname = "condor.import_patch"
    bl_label = "Import Patch"
    bl_description = "Import generated OBJ file for the selected patch(es) from Working/Autogen"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        if not props.condor_path or props.landscape_name == 'NONE':
            return False
        if props.single_patch_mode:
            return bool(props.patch_id)
        return props.patch_x_max >= props.patch_x_min

    def execute(self, context):
        import re as _re
        import math
        import mathutils

        props = context.scene.condor_buildings
        patch_id = str(props.patch_id)

        # --- SWITCH TO LAYOUT WORKSPACE ---
        layout_ws = bpy.data.workspaces.get("Layout")
        if layout_ws and context.window.workspace != layout_ws:
            context.window.workspace = layout_ws
        # --- END SWITCH WORKSPACE ---

        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        if not props.single_patch_mode:
            return self._import_range(context, props, paths, _re, mathutils)

        # --- TERRAIN IMPORT ---
        terrain_col = bpy.data.collections.get("Patch_Terrain")
        terrain_obj_name = f"TR3f{patch_id}" if props.patch_tref else f"TR3{patch_id}"
        terrain_in_col = (
            terrain_col is not None and
            any(o.name == terrain_obj_name for o in terrain_col.objects)
        )
        if not terrain_in_col:
            heightmaps_dir = paths['heightmaps']
            if props.patch_tref:
                terrain_obj_path = os.path.join(heightmaps_dir, "22.5m", f"h{patch_id}.obj")
                if not os.path.exists(terrain_obj_path):
                    self.report({'ERROR'}, f"File h{patch_id}.obj not found in Working/Heightmaps/22.5m/")
                    return {'CANCELLED'}
            else:
                modified_path = os.path.join(heightmaps_dir, "modified", f"h{patch_id}.obj")
                default_path = os.path.join(heightmaps_dir, f"h{patch_id}.obj")
                terrain_obj_path = modified_path if os.path.exists(modified_path) else default_path

            if os.path.exists(terrain_obj_path):
                if not terrain_col:
                    terrain_col = bpy.data.collections.new("Patch_Terrain")
                    context.scene.collection.children.link(terrain_col)

                def find_layer_collection(layer_collection, name):
                    if layer_collection.name == name:
                        return layer_collection
                    for child in layer_collection.children:
                        res = find_layer_collection(child, name)
                        if res:
                            return res
                    return None

                layer_col = find_layer_collection(context.view_layer.layer_collection, "Patch_Terrain")
                prev_active_col = context.view_layer.active_layer_collection
                if layer_col:
                    context.view_layer.active_layer_collection = layer_col

                if hasattr(bpy.ops.wm, 'obj_import'):
                    bpy.ops.wm.obj_import(filepath=terrain_obj_path, forward_axis='Y', up_axis='Z')
                else:
                    bpy.ops.import_scene.obj(filepath=terrain_obj_path, axis_forward='Y', axis_up='Z')

                imported_objs = context.selected_objects
                if imported_objs:
                    terrain_obj = imported_objs[0]
                    terrain_obj.name = terrain_obj_name

                    mat = bpy.data.materials.get(terrain_obj_name)
                    if not mat:
                        mat = bpy.data.materials.new(name=terrain_obj_name)
                    mat.use_nodes = True
                    mat.node_tree.nodes.clear()
                    bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                    bsdf.location = (0, 0)
                    output_node = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                    output_node.location = (300, 0)
                    mat.node_tree.links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])
                    tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
                    tex_node.location = (-300, 0)
                    landscape_texture_dir = os.path.join(paths['landscape'], "Textures")
                    tex_path = os.path.join(landscape_texture_dir, f"t{patch_id}.dds")
                    if os.path.exists(tex_path):
                        img = bpy.data.images.load(tex_path)
                        tex_node.image = img
                    mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                    if terrain_obj.data.materials:
                        terrain_obj.data.materials[0] = mat
                    else:
                        terrain_obj.data.materials.append(mat)

                    mesh = terrain_obj.data
                    if not mesh.uv_layers:
                        mesh.uv_layers.new(name="UVMap")
                    uv_layer = mesh.uv_layers.active.data
                    verts = mesh.vertices
                    if len(verts) > 0:
                        min_x = min(v.co.x for v in verts)
                        max_x = max(v.co.x for v in verts)
                        min_y = min(v.co.y for v in verts)
                        max_y = max(v.co.y for v in verts)
                        dx = max_x - min_x if max_x != min_x else 1.0
                        dy = max_y - min_y if max_y != min_y else 1.0
                        for poly in mesh.polygons:
                            for loop_idx in poly.loop_indices:
                                v_idx = mesh.loops[loop_idx].vertex_index
                                v_co = verts[v_idx].co
                                u = (v_co.x - min_x) / dx
                                v = (v_co.y - min_y) / dy
                                uv_layer[loop_idx].uv = (u, v)

                context.view_layer.active_layer_collection = prev_active_col
            else:
                self.report({'WARNING'}, f"Terrain file not found for patch {patch_id}, skipping terrain import.")
        # --- END TERRAIN IMPORT ---

        if props.patch_tref:
            obj_path_lod0 = os.path.join(paths['autogen'], f"o{patch_id}.obj")
            obj_path_lod1 = os.path.join(paths['autogen'], f"o{patch_id}_LOD1.obj")
            if not os.path.exists(obj_path_lod0) and not os.path.exists(obj_path_lod1):
                print(f"[CONDOR tr3f] no OBJ in autogen, setting viewport")
                layout_ws = bpy.data.workspaces.get("Layout")
                print(f"[CONDOR tr3f] layout_ws={layout_ws}, current_ws={context.window.workspace}")
                if layout_ws and context.window.workspace != layout_ws:
                    context.window.workspace = layout_ws
                target_ws = layout_ws or context.window.workspace
                print(f"[CONDOR tr3f] target_ws={target_ws}, screens={list(target_ws.screens)}")
                for screen in target_ws.screens:
                    print(f"[CONDOR tr3f] screen={screen.name}, areas={[a.type for a in screen.areas]}")
                    for area in screen.areas:
                        if area.type == 'VIEW_3D':
                            print(f"[CONDOR tr3f] found VIEW_3D area")
                            for space in area.spaces:
                                if space.type == 'VIEW_3D':
                                    print(f"[CONDOR tr3f] found VIEW_3D space, setting shading=MATERIAL lens=50 clip_start clip_end")
                                    space.shading.type = 'MATERIAL'
                                    space.lens = 50.0
                                    space.clip_start = 0.009999999776482582
                                    space.clip_end = 100000.0
                                    r3d = space.region_3d
                                    print(f"[CONDOR tr3f] r3d={r3d}")
                                    if r3d:
                                        r3d.view_distance = 9051.04
                                        r3d.view_rotation = mathutils.Euler((0.0, -0.0, 0.0), 'XYZ').to_quaternion()
                                        r3d.view_perspective = 'ORTHO'
                                        r3d.view_location = (0.0, 0.0, 0.0)
                                    terrain_lock = bpy.data.objects.get(terrain_obj_name)
                                    print(f"[CONDOR tr3f] terrain_lock={terrain_lock} (looking for '{terrain_obj_name}')")
                                    if terrain_lock:
                                        space.lock_object = None
                            break
                self.report({'WARNING'}, f"File o{patch_id}.obj not found in Working/Autogen/")
                return {'FINISHED'}

        from ..config import TEXTURE_MAP

        def find_layer_col(layer_collection, name):
            if layer_collection.name == name:
                return layer_collection
            for child in layer_collection.children:
                res = find_layer_col(child, name)
                if res:
                    return res
            return None

        files_to_import = []
        obj_path_lod0 = os.path.join(paths['autogen'], f"o{patch_id}.obj")
        obj_path_lod1 = os.path.join(paths['autogen'], f"o{patch_id}_LOD1.obj")
        if os.path.exists(obj_path_lod0):
            files_to_import.append((obj_path_lod0, f"Condor_{props.landscape_name}_{patch_id}"))
        if os.path.exists(obj_path_lod1):
            files_to_import.append((obj_path_lod1, f"Condor_{props.landscape_name}_{patch_id}_LOD1"))

        if not files_to_import:
            print(f"[CONDOR normal] no OBJ in autogen, setting viewport before early return")
            layout_ws = bpy.data.workspaces.get("Layout")
            print(f"[CONDOR normal] layout_ws={layout_ws}, current_ws={context.window.workspace}")
            if layout_ws and context.window.workspace != layout_ws:
                context.window.workspace = layout_ws
            target_ws = layout_ws or context.window.workspace
            print(f"[CONDOR normal] target_ws={target_ws}, screens={list(target_ws.screens)}")
            for screen in target_ws.screens:
                print(f"[CONDOR normal] screen={screen.name}, areas={[a.type for a in screen.areas]}")
                for area in screen.areas:
                    if area.type == 'VIEW_3D':
                        print(f"[CONDOR normal] found VIEW_3D area")
                        for space in area.spaces:
                            if space.type == 'VIEW_3D':
                                print(f"[CONDOR normal] found VIEW_3D space, setting shading=MATERIAL lens=50 clip_start clip_end")
                                space.shading.type = 'MATERIAL'
                                space.lens = 50.0
                                space.clip_start = 0.009999999776482582
                                space.clip_end = 100000.0
                                r3d = space.region_3d
                                print(f"[CONDOR normal] r3d={r3d}")
                                if r3d:
                                    r3d.view_distance = 9051.04
                                    r3d.view_rotation = mathutils.Euler((0.0, -0.0, 0.0), 'XYZ').to_quaternion()
                                    r3d.view_perspective = 'ORTHO'
                                    r3d.view_location = (0.0, 0.0, 0.0)
                                terrain_lock = bpy.data.objects.get(terrain_obj_name)
                                print(f"[CONDOR normal] terrain_lock={terrain_lock} (looking for '{terrain_obj_name}')")
                                if terrain_lock:
                                    space.lock_object = None
                        break
            self.report({'WARNING'}, f"File not found: o{patch_id}.obj or o{patch_id}_LOD1.obj")
            return {'FINISHED'}

        for obj_path, collection_name in files_to_import:
            patch_col = bpy.data.collections.get(collection_name)
            if not patch_col:
                patch_col = bpy.data.collections.new(collection_name)
                context.scene.collection.children.link(patch_col)

            layer_col = find_layer_col(context.view_layer.layer_collection, collection_name)
            prev_active_col = context.view_layer.active_layer_collection
            if layer_col:
                context.view_layer.active_layer_collection = layer_col

            existing_objects = set(bpy.data.objects)

            import_axis = _detect_obj_forward_axis(obj_path)
            if bpy.app.version >= (4, 0, 0):
                bpy.ops.wm.obj_import(
                    filepath=obj_path,
                    forward_axis=import_axis,
                    up_axis='Z',
                    import_vertex_groups=False,
                )
            else:
                bpy.ops.import_scene.obj(
                    filepath=obj_path,
                    axis_forward=import_axis,
                    axis_up='Z',
                )

            context.view_layer.active_layer_collection = prev_active_col

            new_objects = [o for o in bpy.data.objects if o not in existing_objects]
            for obj in new_objects:
                base_obj_name = _re.sub(r'\.\d+$', '', obj.name)
                for slot in obj.material_slots:
                    mat = slot.material
                    if not mat:
                        continue
                    base_mat_name = _re.sub(r'\.\d+$', '', mat.name)
                    if base_obj_name in TEXTURE_MAP:
                        target_name = f"condor_{base_obj_name}"
                    else:
                        target_name = base_mat_name
                    if target_name == mat.name:
                        continue
                    existing_mat = bpy.data.materials.get(target_name)
                    if existing_mat:
                        slot.material = existing_mat
                        mat.use_fake_user = False
                        bpy.data.materials.remove(mat)
                    else:
                        mat.name = target_name

        tex_dir = os.path.join(paths['autogen'], "Textures")
        if os.path.exists(tex_dir):
            bpy.ops.file.find_missing_files(directory=tex_dir)

        # --- SET VIEWPORT ---
        print(f"[CONDOR normal] setting viewport")
        layout_ws = bpy.data.workspaces.get("Layout")
        print(f"[CONDOR normal] layout_ws={layout_ws}, current_ws={context.window.workspace}")
        if layout_ws and context.window.workspace != layout_ws:
            context.window.workspace = layout_ws
        target_ws = layout_ws or context.window.workspace
        print(f"[CONDOR normal] target_ws={target_ws}, screens={list(target_ws.screens)}")
        for screen in target_ws.screens:
            print(f"[CONDOR normal] screen={screen.name}, areas={[a.type for a in screen.areas]}")
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    print(f"[CONDOR normal] found VIEW_3D area")
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            print(f"[CONDOR normal] found VIEW_3D space, setting shading=MATERIAL lens=50 clip_start clip_end")
                            space.shading.type = 'MATERIAL'
                            space.lens = 50.0
                            space.clip_start = 0.009999999776482582
                            space.clip_end = 100000.0
                            r3d = space.region_3d
                            print(f"[CONDOR normal] r3d={r3d}")
                            if r3d:
                                r3d.view_distance = 9051.04
                                r3d.view_rotation = mathutils.Euler((0.0, -0.0, 0.0), 'XYZ').to_quaternion()
                                r3d.view_perspective = 'ORTHO'
                                r3d.view_location = (0.0, 0.0, 0.0)
                            terrain_lock = bpy.data.objects.get(terrain_obj_name)
                            print(f"[CONDOR normal] terrain_lock={terrain_lock} (looking for '{terrain_obj_name}')")
                            if terrain_lock:
                                space.lock_object = None
                    break
        # --- END SET VIEWPORT ---

        # Collapse the Outliner collections when many patches are loaded.
        _collapse_collections_if_many(context)

        self.report({'INFO'}, f"Imported o{patch_id}.obj")
        return {'FINISHED'}

    def _import_range(self, context, props, paths, _re, mathutils):
        """Import OBJ + terrain for all patches in the configured range."""
        from ..config import TEXTURE_MAP

        patch_ids = resolve_patch_list(props)
        if not patch_ids:
            self.report({'ERROR'}, "No patches to process")
            return {'CANCELLED'}

        errors = []
        imported_patches = []

        def _find_lc(lc, name):
            if lc.name == name:
                return lc
            for child in lc.children:
                res = _find_lc(child, name)
                if res:
                    return res
            return None

        for patch_id in patch_ids:

            # --- TERRAIN IMPORT (jen když je checkbox zapnutý) ---
            if props.import_patch_terrain:
                terrain_obj_name = f"TR3{patch_id}"
                terrain_col = bpy.data.collections.get("Patch_Terrain")
                terrain_in_col = (
                    terrain_col is not None and
                    any(o.name == terrain_obj_name for o in terrain_col.objects)
                )
                if not terrain_in_col:
                    heightmaps_dir = paths['heightmaps']
                    modified_path = os.path.join(heightmaps_dir, "modified", f"h{patch_id}.obj")
                    default_path = os.path.join(heightmaps_dir, f"h{patch_id}.obj")
                    terrain_obj_path = modified_path if os.path.exists(modified_path) else default_path

                    if os.path.exists(terrain_obj_path):
                        terrain_col = bpy.data.collections.get("Patch_Terrain")
                        if not terrain_col:
                            terrain_col = bpy.data.collections.new("Patch_Terrain")
                            context.scene.collection.children.link(terrain_col)

                        layer_col = _find_lc(context.view_layer.layer_collection, "Patch_Terrain")
                        prev_active_col = context.view_layer.active_layer_collection
                        if layer_col:
                            context.view_layer.active_layer_collection = layer_col

                        if hasattr(bpy.ops.wm, 'obj_import'):
                            bpy.ops.wm.obj_import(filepath=terrain_obj_path, forward_axis='Y', up_axis='Z')
                        else:
                            bpy.ops.import_scene.obj(filepath=terrain_obj_path, axis_forward='Y', axis_up='Z')

                        imported_objs = context.selected_objects
                        if imported_objs:
                            terrain_obj = imported_objs[0]
                            terrain_obj.name = terrain_obj_name

                            mat = bpy.data.materials.get(terrain_obj_name)
                            if not mat:
                                mat = bpy.data.materials.new(name=terrain_obj_name)
                            mat.use_nodes = True
                            mat.node_tree.nodes.clear()
                            bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                            bsdf.location = (0, 0)
                            out_node = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                            out_node.location = (300, 0)
                            mat.node_tree.links.new(bsdf.outputs['BSDF'], out_node.inputs['Surface'])
                            tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
                            tex_node.location = (-300, 0)
                            landscape_tex_dir = os.path.join(paths['landscape'], "Textures")
                            tex_path = os.path.join(landscape_tex_dir, f"t{patch_id}.dds")
                            if os.path.exists(tex_path):
                                img = bpy.data.images.load(tex_path)
                                tex_node.image = img
                            mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                            if terrain_obj.data.materials:
                                terrain_obj.data.materials[0] = mat
                            else:
                                terrain_obj.data.materials.append(mat)

                            mesh = terrain_obj.data
                            if not mesh.uv_layers:
                                mesh.uv_layers.new(name="UVMap")
                            uv_layer = mesh.uv_layers.active.data
                            verts = mesh.vertices
                            if len(verts) > 0:
                                min_x = min(v.co.x for v in verts)
                                max_x = max(v.co.x for v in verts)
                                min_y = min(v.co.y for v in verts)
                                max_y = max(v.co.y for v in verts)
                                dx = max_x - min_x if max_x != min_x else 1.0
                                dy = max_y - min_y if max_y != min_y else 1.0
                                for poly in mesh.polygons:
                                    for loop_idx in poly.loop_indices:
                                        v_idx = mesh.loops[loop_idx].vertex_index
                                        v_co = verts[v_idx].co
                                        u = (v_co.x - min_x) / dx
                                        v = (v_co.y - min_y) / dy
                                        uv_layer[loop_idx].uv = (u, v)

                        context.view_layer.active_layer_collection = prev_active_col
            # --- END TERRAIN IMPORT ---

            # --- OBJ IMPORT ---
            files_to_import = []
            obj_path_lod0 = os.path.join(paths['autogen'], f"o{patch_id}.obj")
            obj_path_lod1 = os.path.join(paths['autogen'], f"o{patch_id}_LOD1.obj")
            if os.path.exists(obj_path_lod0):
                files_to_import.append((obj_path_lod0, f"Condor_{props.landscape_name}_{patch_id}"))
            if os.path.exists(obj_path_lod1):
                files_to_import.append((obj_path_lod1, f"Condor_{props.landscape_name}_{patch_id}_LOD1"))

            if not files_to_import:
                errors.append(f"Patch {patch_id}: o{patch_id}.obj not found in Autogen")
                continue

            for obj_path, collection_name in files_to_import:
                patch_col = bpy.data.collections.get(collection_name)
                if not patch_col:
                    patch_col = bpy.data.collections.new(collection_name)
                    context.scene.collection.children.link(patch_col)

                layer_col = _find_lc(context.view_layer.layer_collection, collection_name)
                prev_active_col = context.view_layer.active_layer_collection
                if layer_col:
                    context.view_layer.active_layer_collection = layer_col

                existing_objects = set(bpy.data.objects)

                import_axis = _detect_obj_forward_axis(obj_path)
                if bpy.app.version >= (4, 0, 0):
                    bpy.ops.wm.obj_import(filepath=obj_path, forward_axis=import_axis, up_axis='Z', import_vertex_groups=False)
                else:
                    bpy.ops.import_scene.obj(filepath=obj_path, axis_forward=import_axis, axis_up='Z')

                context.view_layer.active_layer_collection = prev_active_col

                new_objects = [o for o in bpy.data.objects if o not in existing_objects]
                for obj in new_objects:
                    base_obj_name = _re.sub(r'\.\d+$', '', obj.name)
                    for slot in obj.material_slots:
                        mat = slot.material
                        if not mat:
                            continue
                        base_mat_name = _re.sub(r'\.\d+$', '', mat.name)
                        if base_obj_name in TEXTURE_MAP:
                            target_name = f"condor_{base_obj_name}"
                        else:
                            target_name = base_mat_name
                        if target_name == mat.name:
                            continue
                        existing_mat = bpy.data.materials.get(target_name)
                        if existing_mat:
                            slot.material = existing_mat
                            mat.use_fake_user = False
                            bpy.data.materials.remove(mat)
                        else:
                            mat.name = target_name

            imported_patches.append(patch_id)
            # --- END OBJ IMPORT ---

        # Najít chybějící textury
        tex_dir = os.path.join(paths['autogen'], "Textures")
        if os.path.exists(tex_dir):
            bpy.ops.file.find_missing_files(directory=tex_dir)

        # --- POZICOVÁNÍ PATCHŮ (jen pokud je terrain checkbox zapnutý) ---
        if props.import_patch_terrain:
            min_x = props.patch_x_min
            min_y = props.patch_y_min
            for x in range(props.patch_x_min, props.patch_x_max + 1):
                for y in range(props.patch_y_min, props.patch_y_max + 1):
                    pid = f"{x:03d}{y:03d}"
                    offset_x = -(x - min_x) * 5760.0
                    offset_y = (y - min_y) * 5760.0
                    if offset_x == 0.0 and offset_y == 0.0:
                        continue
                    # Shift BOTH the LOD0 and the LOD1 collection by the same offset.
                    for col_name in (f"Condor_{props.landscape_name}_{pid}",
                                     f"Condor_{props.landscape_name}_{pid}_LOD1"):
                        col = bpy.data.collections.get(col_name)
                        if col:
                            for obj in col.objects:
                                obj.location.x += offset_x
                                obj.location.y += offset_y
                    terrain_obj = bpy.data.objects.get(f"TR3{pid}")
                    if terrain_obj:
                        terrain_obj.location.x += offset_x
                        terrain_obj.location.y += offset_y
        # --- END POZICOVÁNÍ PATCHŮ ---

        # --- VIEWPORT ---
        if props.import_patch_terrain:
            lock_obj = bpy.data.objects.get(f"TR3{patch_ids[0]}")
        else:
            lock_obj = None
            if imported_patches:
                first_col = bpy.data.collections.get(f"Condor_{props.landscape_name}_{imported_patches[0]}")
                if first_col:
                    for _o in first_col.objects:
                        if _o.type == 'MESH':
                            lock_obj = _o
                            break
        target_ws = bpy.data.workspaces.get("Layout") or context.window.workspace
        for screen in target_ws.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.shading.type = 'MATERIAL'
                            space.lens = 50.0
                            space.clip_start = 0.009999999776482582
                            space.clip_end = 100000.0
                            r3d = space.region_3d
                            r3d.view_distance = 9051.04
                            r3d.view_rotation = mathutils.Euler((0.0, -0.0, 0.0), 'XYZ').to_quaternion()
                            r3d.view_perspective = 'ORTHO'
                            r3d.view_location = (0.0, 0.0, 0.0)
                            if lock_obj:
                                space.lock_object = None
                    break
        # --- END VIEWPORT ---

        # Collapse the Outliner collections when many patches were loaded.
        _collapse_collections_if_many(context)

        if errors:
            for e in errors[:5]:
                self.report({'WARNING'}, e)

        if imported_patches:
            self.report({'INFO'}, f"Imported {len(imported_patches)} patch(es)")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "No patches imported - check OBJ files exist in Working/Autogen")
            return {'CANCELLED'}


# Registration
_classes = [
    CONDOR_OT_import_buildings,
    CONDOR_OT_clear_buildings,
    CONDOR_OT_export_condor,
    CONDOR_OT_export_terrain,
    CONDOR_OT_merge_wind_turbines,
    CONDOR_OT_import_chimneys,
    CONDOR_OT_merge_chimneys,
    CONDOR_OT_import_patch,
]


def register():
    """Register operator classes."""
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister operator classes."""
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
