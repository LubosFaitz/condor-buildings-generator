"""
Condor Buildings Generator - Blender UI Panels

Defines the sidebar panel for the Condor Buildings addon.
Located in View3D > Sidebar > Condor tab.

Updated for Condor workflow with:
- Condor path and landscape selection
- Patch range (X/Y min/max) for batch processing
- OSM download options
"""

import bpy
from bpy.types import Panel


class CONDOR_PT_main_panel(Panel):
    """Main panel for Condor Buildings Generator"""

    bl_label = "Condor Buildings"
    bl_idname = "CONDOR_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Condor"

    def draw(self, context):
        layout = self.layout
        props = context.scene.condor_buildings

        # --- Condor Settings ---
        box = layout.box()
        box.label(text="Condor Settings", icon='FILE_FOLDER')

        col = box.column(align=True)
        col.prop(props, "condor_path", text="")

        # Landscape dropdown
        row = box.row(align=True)
        row.prop(props, "landscape_name", text="")

        # Show validation feedback
        if not props.condor_path:
            box.label(text="Select Condor directory", icon='ERROR')
        elif props.landscape_name == 'NONE':
            box.label(text="Select a landscape", icon='ERROR')

        # --- Patch Selection ---
        box = layout.box()
        box.label(text="Patch Selection", icon='GRID')

        # Toggle between single patch and range
        row = box.row(align=True)
        row.prop(props, "single_patch_mode", text="Single Patch", toggle=True)

        if props.single_patch_mode:
            # Single patch ID input
            split = box.split(factor=0.30)
            split.label(text="Patch ID:")
            sub = split.split(factor=0.65)
            sub.prop(props, "patch_id", text="")
            sub.prop(props, "patch_tref", text="tr3f")
            row_btns = box.row(align=True)
            row_btns.operator("condor.import_patch", text="Import Patch", icon='IMPORT')
            row_btns.operator("condor.export_terrain", text="Export Terrain", icon='EXPORT')
        else:
            # Patch range inputs
            col = box.column(align=True)

            row = col.row(align=True)
            row.label(text="X:")
            row.prop(props, "patch_x_min", text="Min")
            row.prop(props, "patch_x_max", text="Max")

            row = col.row(align=True)
            row.label(text="Y:")
            row.prop(props, "patch_y_min", text="Min")
            row.prop(props, "patch_y_max", text="Max")

            # Show patch count
            x_count = max(0, props.patch_x_max - props.patch_x_min + 1)
            y_count = max(0, props.patch_y_max - props.patch_y_min + 1)
            total = x_count * y_count

            if total > 0:
                box.label(text=f"Patches: {total} ({x_count}×{y_count})", icon='INFO')
                box.prop(props, "import_patch_terrain")
                box.operator("condor.import_patch", text="Import Patch", icon='IMPORT')

        # --- OSM Data Source ---
        box = layout.box()
        box.label(text="OSM Data", icon='WORLD')
        box.prop(props, "osm_source", text="")

        if props.osm_source == 'DOWNLOAD':
            box.label(text="Will download from Overpass API", icon='URL')
        box.prop(props, "use_msprint")

        # --- Output Options ---
        box = layout.box()
        box.label(text="Output", icon='EXPORT')

        col = box.column(align=True)
        col.prop(props, "output_lod", text="")
        col.prop(props, "save_to_autogen")
        col.prop(props, "import_to_blender")

        # --- Powerlines (optional extra object) ---
        box = layout.box()
        box.label(text="Powerlines", icon='OUTLINER_DATA_GREASEPENCIL')
        row = box.row(align=True)
        row.prop(props, "generate_powerlines")
        # Randomized wind turbine applies only to file mode (Import to Blender off),
        # so it's greyed out when importing to Blender.
        sub = row.row(align=True)
        sub.enabled = not props.import_to_blender
        sub.prop(props, "randomize_wind_turbines", text="Randomized wind turbine")
        if props.generate_powerlines:
            box.label(text="Pylons + cables + aerialways -> 'pylones' (Pylons.dds)", icon='INFO')
            # Warning balls checkbox hidden on purpose: 'warning_balls' is default
            # ON (see properties.py), so balls are always added with powerlines.
            # box.prop(props, "warning_balls")
        turbines_in_scene = any(
            obj.name == 'wind_turbine' or obj.name.startswith('wind_turbine_')
            for obj in bpy.data.objects
        )
        if turbines_in_scene:
            box.prop(props, "wind_turbine_rotation", slider=True)
            box.operator("condor.merge_wind_turbines", icon='AUTOMERGE_ON')

        # --- Other objects (collapsible, right under Powerlines) ---
        # Container for extra objects (chimneys now, more may be added). Drawn
        # inline here so it sits under the Powerlines box; sub-panels would always
        # render after the whole main panel.
        box = layout.box()
        header = box.row(align=True)
        header.prop(
            props, "show_other_objects", text="Other objects", emboss=False,
            icon='TRIA_DOWN' if props.show_other_objects else 'TRIA_RIGHT',
        )
        if props.show_other_objects:
            # Chimney
            sub = box.box()
            row_title = sub.row(align=True)
            row_title.label(text="Chimney", icon='MESH_CYLINDER')
            row_title.prop(props, "chimney_batch", text="Batch")
            row = sub.row(align=True)
            row.operator("condor.import_chimneys", text="Import", icon='IMPORT')
            row.operator("condor.merge_chimneys", text="Merge", icon='AUTOMERGE_ON')

            # --- TRANSMITTER add-on (removable: delete blender/transmitters.py) ---
            try:
                from . import transmitters
                transmitters.draw_panel(box, context)
            except Exception:
                pass

        # --- Import Button ---
        layout.separator()

        # add MTL (file mode) + Batch processing
        # DOČASNĚ SKRYTO - oba checkboxy zakomentované. 'add_mtl' je ale defaultně
        # ZAPNUTÝ (viz properties.py), takže MTL se ve file-módu tvoří pořád, jen
        # checkbox není vidět. Odkomentovat pro zobrazení:
        # row_opts = layout.row(align=True)
        # row_opts.prop(props, "add_mtl")
        # row_opts.prop(props, "batch_processing")

        row = layout.row(align=True)
        row.scale_y = 1.8

        # Check if we can import
        can_import = (
            props.condor_path and
            props.landscape_name != 'NONE' and
            (props.single_patch_mode and props.patch_id or
             not props.single_patch_mode and props.patch_x_max >= props.patch_x_min)
        )

        # Import button (large)
        sub = row.row(align=True)
        sub.enabled = can_import and not props.is_processing

        if props.is_processing:
            sub.operator("condor.import_buildings", text=f"Processing {props.current_patch}...", icon='TIME')
        else:
            sub.operator("condor.import_buildings", text="Generate Buildings", icon='IMPORT')

        # Clear button (smaller)
        row.operator("condor.clear_buildings", text="", icon='TRASH')

        # --- Export Condor button (Condor-ready OBJ+MTL) ---
        row_exp = layout.row(align=True)
        row_exp.scale_y = 1.5
        sub_exp = row_exp.row(align=True)
        sub_exp.enabled = can_import and not props.is_processing
        sub_exp.operator("condor.export_condor", text="Export Condor OBJ+MTL", icon='EXPORT')
        layout.label(text="Condor-ready: triangulated, axis-corrected, .mtl included", icon='CHECKMARK')

        # --- Statistics (after import) ---
        if props.last_import_buildings > 0:
            box = layout.box()
            box.label(text="Last Import", icon='INFO')
            col = box.column(align=True)
            col.label(text=f"Buildings: {props.last_import_buildings}")
            if props.last_patches_processed > 1:
                col.label(text=f"Patches: {props.last_patches_processed}")
            col.label(text=f"Time: {props.last_import_time_ms}ms")


class CONDOR_PT_roof_options_panel(Panel):
    """Roof options sub-panel"""

    bl_label = "Roof Options"
    bl_idname = "CONDOR_PT_roof_options_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Condor"
    bl_parent_id = "CONDOR_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.condor_buildings

        layout.prop(props, "roof_selection_mode", text="")
        layout.prop(props, "random_hipped")
        layout.prop(props, "flat_roof_merge")
        if props.flat_roof_merge:
            sub = layout.box()
            sub.prop(props, "flat_roof_terrain_photo")
            if props.flat_roof_terrain_photo:
                sub.label(text="Flat roofs use patch orthophoto t<patch>.dds", icon='IMAGE_DATA')
                row = sub.row()
                row.enabled = props.flat_roof_terrain_photo
                row.prop(props, "flat_roof_industrial_only")

        # Help text
        if props.roof_selection_mode == 'GEOMETRY':
            layout.label(text="Uses geometry + category", icon='INFO')
        else:
            layout.label(text="Only tagged houses", icon='INFO')

        # Roof geometry parameters
        box = layout.box()
        box.label(text="Roof Geometry", icon='MOD_SOLIDIFY')
        col = box.column(align=True)
        col.prop(props, "gable_height")
        col.prop(props, "roof_overhang")
        col.prop(props, "gabled_max_floors")


class CONDOR_PT_advanced_panel(Panel):
    """Advanced settings sub-panel"""

    bl_label = "Advanced"
    bl_idname = "CONDOR_PT_advanced_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Condor"
    bl_parent_id = "CONDOR_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.condor_buildings

        # House-scale constraints
        box = layout.box()
        box.label(text="House-Scale Constraints", icon='HOME')

        col = box.column(align=True)
        col.prop(props, "house_max_area")
        col.prop(props, "house_max_side")
        col.prop(props, "house_min_side")
        col.prop(props, "house_max_aspect")

        # Geometry constraints
        box = layout.box()
        box.label(text="Geometry Constraints", icon='MESH_DATA')

        col = box.column(align=True)
        col.prop(props, "gabled_min_rectangularity")
        col.prop(props, "polyskel_max_vertices")

        # Terrain integration
        box = layout.box()
        box.label(text="Terrain Integration", icon='WORLD')
        box.prop(props, "floor_z_epsilon")

        # Reproducibility
        box = layout.box()
        box.label(text="Reproducibility", icon='FILE_REFRESH')
        box.prop(props, "global_seed")


class CONDOR_PT_debug_panel(Panel):
    """Debug options sub-panel"""

    bl_label = "Debug"
    bl_idname = "CONDOR_PT_debug_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Condor"
    bl_parent_id = "CONDOR_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.condor_buildings

        layout.prop(props, "debug_osm_id")

        if props.debug_osm_id:
            layout.label(text="Single building mode", icon='INFO')


# Registration
_classes = [
    CONDOR_PT_main_panel,
    CONDOR_PT_roof_options_panel,
    CONDOR_PT_advanced_panel,
    CONDOR_PT_debug_panel,
]


def register():
    """Register panel classes."""
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister panel classes."""
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
