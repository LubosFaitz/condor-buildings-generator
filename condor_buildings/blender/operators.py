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
            from ..config import PipelineConfig, RoofSelectionMode
            from ..io.patch_metadata import load_patch_metadata
            from ..generators import configure_generator
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
        )

        # Process patches
        start_time = time.time()
        total_buildings = 0
        total_objects = []
        patches_processed = 0
        errors = []

        props.is_processing = True

        try:
            for patch_id in patch_ids:
                props.current_patch = patch_id

                # Force UI update
                bpy.context.view_layer.update()

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
                    # Flat roof merge
                    flat_roof_merge=props.flat_roof_merge,
                )

                # Override OSM path in config
                config.osm_path = osm_path

                # Run pipeline
                output_mode = "file" if props.save_to_autogen and not props.import_to_blender else "memory"

                try:
                    result = run_pipeline(config, output_mode=output_mode)
                except Exception as e:
                    errors.append(f"Patch {patch_id}: pipeline failed: {e}")
                    continue

                if not result.success:
                    error_msg = "; ".join(result.report.errors) if result.report.errors else "Unknown error"
                    errors.append(f"Patch {patch_id}: {error_msg}")
                    continue

                patches_processed += 1

                # Import to Blender if requested
                if props.import_to_blender:
                    collection_name = f"Condor_{props.landscape_name}_{patch_id}"
                    cleanup_buildings_collection(collection_name)

                    # Texture directory: Working/Autogen/Textures/
                    texture_dir = os.path.join(paths['autogen'], "Textures")
                    print(f"[Condor] Texture directory: {texture_dir}")
                    print(f"[Condor] Texture dir exists: {os.path.isdir(texture_dir)}")

                    # Use new grouped meshes (v0.6.3+)
                    if props.output_lod in ('LOD0', 'BOTH') and result.grouped_lod0:
                        try:
                            objects = import_grouped_meshes_to_blender(
                                result.grouped_lod0,
                                collection_name=collection_name,
                                texture_dir=texture_dir
                            )
                            total_objects.extend(objects)
                            total_buildings += len(objects)
                        except Exception as e:
                            errors.append(f"Patch {patch_id}: Blender import failed: {e}")

                    if props.output_lod == 'LOD1' and result.grouped_lod1:
                        try:
                            objects = import_grouped_meshes_to_blender(
                                result.grouped_lod1,
                                collection_name=collection_name,
                                texture_dir=texture_dir
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
                                texture_dir=texture_dir
                            )
                        except Exception as e:
                            errors.append(f"Patch {patch_id}: LOD1 import failed: {e}")

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

            self.report(
                {'INFO'},
                f"Generated {total_buildings} objects from {patches_processed} patches in {elapsed_ms}ms{tex_msg}"
            )
            return {'FINISHED'}
        else:
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


# Registration
_classes = [
    CONDOR_OT_import_buildings,
    CONDOR_OT_clear_buildings,
]


def register():
    """Register operator classes."""
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister operator classes."""
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
