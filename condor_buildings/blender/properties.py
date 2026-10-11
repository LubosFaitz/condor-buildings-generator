"""
Condor Buildings Generator - Blender Properties

Defines PropertyGroup classes that map to PipelineConfig fields,
allowing users to configure the pipeline through Blender's UI.

Updated for Condor workflow:
- Condor installation path + Landscape name
- Patch range (X/Y min/max) for batch processing
- OSM data download from Overpass API
"""

import bpy
import math
from bpy.props import (
    StringProperty,
    EnumProperty,
    FloatProperty,
    BoolProperty,
    IntProperty,
)
from bpy.types import AddonPreferences, PropertyGroup
import os

_turbine_slider_pos = 0.0
_turbine_active_name = ""


def _get_wind_turbine_rotation(self):
    global _turbine_slider_pos, _turbine_active_name
    active = bpy.context.active_object
    active_name = active.name if active else ""
    if active_name != _turbine_active_name:
        _turbine_active_name = active_name
        if active and (active.name == 'wind_turbine' or active.name.startswith('wind_turbine_')):
            _turbine_slider_pos = math.degrees(active.rotation_euler.z) % 360
        else:
            _turbine_slider_pos = 0.0
    return _turbine_slider_pos


def _set_wind_turbine_rotation(self, value):
    global _turbine_slider_pos
    delta = math.radians(value - _turbine_slider_pos)
    _turbine_slider_pos = value
    for obj in bpy.context.selected_objects:
        if obj.name == 'wind_turbine' or obj.name.startswith('wind_turbine_'):
            obj.rotation_euler.z += delta


def get_landscapes(self, context):
    """Callback to populate landscape dropdown from Condor directory."""
    items = [('NONE', "-- Select Landscape --", "Select a landscape")]

    props = context.scene.condor_buildings
    condor_path = bpy.path.abspath(props.condor_path)

    if not condor_path or not os.path.isdir(condor_path):
        return items

    landscapes_dir = os.path.join(condor_path, "Landscapes")
    if not os.path.isdir(landscapes_dir):
        return items

    # Scan for landscape folders
    try:
        for name in sorted(os.listdir(landscapes_dir)):
            landscape_path = os.path.join(landscapes_dir, name)
            # Check if it's a valid landscape (has Working folder)
            if os.path.isdir(landscape_path):
                working_path = os.path.join(landscape_path, "Working")
                if os.path.isdir(working_path):
                    items.append((name, name, f"Landscape: {name}"))
    except PermissionError:
        pass

    return items


class CondorBuildingsProperties(PropertyGroup):
    """
    Properties for the Condor Buildings addon.

    These map to PipelineConfig fields and are displayed in the UI panel.
    """

    # --- Condor Path Settings (NEW) ---

    condor_path: StringProperty(
        name="Condor Directory",
        description="Path to Condor 3 installation (e.g., C:\\Condor3)",
        subtype='DIR_PATH',
        default="C:\\Condor3",
    )

    landscape_name: EnumProperty(
        name="Landscape",
        description="Select the landscape to process",
        items=get_landscapes,
    )

    # --- Patch Range (NEW) ---

    patch_x_min: IntProperty(
        name="X Min",
        description="Minimum X patch coordinate",
        default=0,
        min=0,
        max=999,
    )

    patch_x_max: IntProperty(
        name="X Max",
        description="Maximum X patch coordinate",
        default=0,
        min=0,
        max=999,
    )

    patch_y_min: IntProperty(
        name="Y Min",
        description="Minimum Y patch coordinate",
        default=0,
        min=0,
        max=999,
    )

    patch_y_max: IntProperty(
        name="Y Max",
        description="Maximum Y patch coordinate",
        default=0,
        min=0,
        max=999,
    )

    # --- Single Patch Mode (for backward compatibility) ---

    single_patch_mode: BoolProperty(
        name="Single Patch Mode",
        description="Process only a single patch instead of a range",
        default=False,
    )

    import_patch_terrain: BoolProperty(
        name="terrain",
        description="Import terrain for each patch",
        default=False,
    )

    patch_id: StringProperty(
        name="Patch ID",
        description="6-digit patch identifier (e.g., 036019) for single patch mode",
        default="",
        maxlen=6,
    )

    patch_tref: BoolProperty(
        name="tr3f",
        description="tr3f",
        default=False,
    )

    import_c3d: BoolProperty(
        name="Import C3D",
        description="Import the patch from the Condor .c3d file instead of the OBJ",
        default=False,
    )

    # --- OSM Data Source ---

    osm_source: EnumProperty(
        name="OSM Source",
        description="Where to get OpenStreetMap building data",
        items=[
            ('DOWNLOAD', "Download from Overpass", "Download OSM data from Overpass API (requires internet)"),
            ('LOCAL', "Local OSM File", "Use existing local map_*.osm file"),
        ],
        default='DOWNLOAD',
    )

    # --- Output Options ---

    output_lod: EnumProperty(
        name="LOD Level",
        description="Which LOD level(s) to import",
        items=[
            ('LOD0', "LOD0 (Detailed)", "Detailed mesh with 0.5m roof overhang"),
            ('LOD1', "LOD1 (Simple)", "Simplified mesh without overhang"),
            ('BOTH', "Both LODs", "Import both LOD0 and LOD1 as separate collections"),
        ],
        default='LOD0',
    )

    save_to_autogen: BoolProperty(
        name="Save to Autogen",
        description="Save OBJ files to Landscape's Working/Autogen folder",
        default=True,
    )

    import_to_blender: BoolProperty(
        name="Import to Blender",
        description="Import generated meshes into Blender viewport",
        default=True,
    )

    save_as_c3d: BoolProperty(
        name="Save as C3D",
        description="Save the generated files as Condor .c3d instead of .obj",
        default=False,
    )

    # --- Roof Selection ---

    roof_selection_mode: EnumProperty(
        name="Roof Selection",
        description="How to determine roof types for buildings",
        items=[
            ('GEOMETRY', "Geometry-based (Recommended)",
             "Use footprint geometry and building category to determine roof type"),
            ('OSM_TAGS_ONLY', "OSM Tags Only",
             "Only buildings explicitly tagged as houses get pitched roofs"),
        ],
        default='GEOMETRY',
    )

    random_hipped: BoolProperty(
        name="Random Hipped Roofs",
        description="Randomly assign hipped roofs to 50% of eligible buildings (for testing variety)",
        default=False,
    )

    flat_roof_merge: BoolProperty(
        name="Merge Flat Roofs",
        description=(
            "Merge all flat roofs into a single object (instead of 6 atlas groups). "
            "Reduces object count; required for the terrain photo option below"
        ),
        default=False,
    )

    flat_roof_terrain_photo: BoolProperty(
        name="Terrain photo on flat roofs",
        description=(
            "Texture the merged flat roofs with the patch orthophoto t<patch>.dds "
            "(from the landscape Textures folder), using patch-normalized UVs so roofs "
            "blend with the aerial photo from the air. Optional (off by default); "
            "enabling it also merges flat roofs"
        ),
        default=False,
    )

    flat_roof_industrial_only: BoolProperty(
        name="Only industrial",
        description=(
            "Apply terrain photo only to industrial flat roofs (merged into flat_roof). "
            "Other flat roofs keep their normal Roof1..6 textures. "
            "Only available when Terrain photo is enabled"
        ),
        default=False,
    )

    # --- Powerlines ---

    generate_powerlines: BoolProperty(
        name="Generate Powerlines",
        description=(
            "If present in the OSM data for the patch, also generate powerlines "
            "(power=line / minor_line), wind turbines and aerialways (cable cars / "
            "chair lifts): pylons + catenary cables + lift cabins/seats, merged into "
            "a single 'pylones' object sharing the buildings' OBJ/MTL/C3D (texture "
            "Pylons.dds). Off by default"
        ),
        default=False,
    )

    randomize_wind_turbines: BoolProperty(
        name="Randomized wind turbine",
        description=(
            "File mode (Import to Blender off): rotate EACH wind turbine tower by "
            "its own random Z angle instead of one shared angle for the whole patch. "
            "Off by default (all towers share one random angle)"
        ),
        default=False,
    )

    warning_balls: BoolProperty(
        name="Warning balls",
        description=(
            "Add aviation warning balls on the top conductor of power lines "
            "(merged into the 'pylones' object, shares Pylons.dds). Placed inside "
            "airport zones (runway centre + half its length + 458 m -> 60 cm) and "
            "over deep valleys (cable >45 m above terrain -> 120 cm), 40 m apart. "
            "Checkbox is hidden but kept ON, so balls are always added when "
            "Generate Powerlines is on"
        ),
        default=True,
    )

    add_mtl: BoolProperty(
        name="add mtl batch",
        description=(
            "File mode (Import to Blender off): also write an .mtl next to each "
            "exported o<patch>.obj, with the same materials and textures as the "
            "Export OBJ+MTL button (textures from the config texture map). "
            "Checkbox is hidden in the panel, but kept ON by default so the MTL "
            "is always written"
        ),
        default=True,
    )

    batch_processing: BoolProperty(
        name="Batch processing",
        description=(
            "Process a whole patch range one patch at a time and export each as "
            "Condor-ready OBJ+MTL automatically. Only active together with Import "
            "to Blender: for every patch it generates buildings (no terrain), "
            "rotates wind turbines by one random angle, merges them, exports "
            "(splitting large objects when Separate is on) and then deletes the "
            "patch from Blender before the next one - keeping memory low. Off by "
            "default; with it off Generate Buildings behaves as before"
        ),
        default=False,
    )

    show_other_objects: BoolProperty(
        name="Other objects",
        description="Expand the 'Other objects' section (chimneys, ...)",
        default=False,
    )

    chimney_batch: BoolProperty(
        name="Batch",
        description=(
            "File mode (Import to Blender off): after generating the OBJ, also "
            "generate the chimneys (like the Import button), merge them into one "
            "'chimney' object at origin 0,0,0 and add it into the OBJ. If 'add mtl "
            "batch' is on, the chimney material + Chimney.dds texture are written "
            "into the MTL too. Off by default"
        ),
        default=False,
    )

    wind_turbine_rotation: FloatProperty(
        name="Wind Turbine Rotation",
        description="Rotation of selected wind turbines around the Z axis (degrees)",
        default=0.0,
        min=0.0,
        max=360.0,
        precision=1,
        get=_get_wind_turbine_rotation,
        set=_set_wind_turbine_rotation,
    )

    use_msprint: BoolProperty(
        name="MSprint - add buildings",
        description=(
            "After downloading OSM, adds missing buildings from Microsoft Global Building Footprints. "
            "Gz files are cached (downloaded only once); result is merged into map_*.osm"
        ),
        default=False,
    )

    exclude_airports_solar: BoolProperty(
        name="Exclude airports, solar farms and water",
        description=(
            "Generate no objects inside an airfield (aeroway=aerodrome) or a ground solar "
            "farm - those places already have their own Condor scenery objects / panels - "
            "and drop a building whose footprint stands WHOLLY on water (from the alpha "
            "channel of the patch texture t<patch>.dds); one point on dry land keeps it. "
            "Power lines are kept, they may cross the area. Needs a freshly downloaded "
            "map_*.osm (older files do not contain the solar farm outlines)"
        ),
        default=False,
    )

    # --- House-Scale Constraints (Advanced) ---

    house_max_area: FloatProperty(
        name="Max House Area",
        description="Maximum footprint area (m²) for gabled/hipped roof eligibility",
        default=360.0,
        min=50.0,
        max=2000.0,
        soft_min=100.0,
        soft_max=500.0,
        unit='AREA',
    )

    house_max_side: FloatProperty(
        name="Max Side Length",
        description="Maximum side length (m) for gabled/hipped roof eligibility",
        default=30.0,
        min=10.0,
        max=100.0,
        soft_min=15.0,
        soft_max=40.0,
        unit='LENGTH',
    )

    house_min_side: FloatProperty(
        name="Min Side Length",
        description="Minimum side length (m) for gabled/hipped roof eligibility",
        default=3.2,
        min=1.0,
        max=10.0,
        soft_min=2.0,
        soft_max=5.0,
        unit='LENGTH',
    )

    house_max_aspect: FloatProperty(
        name="Max Aspect Ratio",
        description="Maximum length/width ratio for gabled/hipped roof eligibility",
        default=4.8,
        min=1.5,
        max=10.0,
        soft_min=2.0,
        soft_max=6.0,
    )

    # --- Roof Geometry Parameters ---

    gable_height: FloatProperty(
        name="Gable Height",
        description="Fixed height of gable/hipped roof peak above walls (meters)",
        default=3.0,
        min=1.0,
        max=10.0,
        soft_min=2.0,
        soft_max=5.0,
        unit='LENGTH',
    )

    roof_overhang: FloatProperty(
        name="Roof Overhang",
        description="Roof overhang distance beyond walls for LOD0 (meters)",
        default=0.5,
        min=0.0,
        max=2.0,
        soft_min=0.0,
        soft_max=1.0,
        unit='LENGTH',
    )

    floor_z_epsilon: FloatProperty(
        name="Floor Z Offset",
        description="Distance to sink buildings below terrain to avoid gaps (meters)",
        default=0.3,
        min=0.0,
        max=2.0,
        soft_min=0.1,
        soft_max=1.0,
        unit='LENGTH',
    )

    gabled_max_floors: IntProperty(
        name="Max Floors (Gabled)",
        description="Maximum number of floors for gabled/hipped roof eligibility",
        default=2,
        min=1,
        max=10,
    )

    # --- Advanced Geometry Constraints ---

    gabled_min_rectangularity: FloatProperty(
        name="Min Rectangularity",
        description="Minimum area/OBB ratio for gabled roof eligibility (0.0-1.0)",
        default=0.70,
        min=0.5,
        max=1.0,
        soft_min=0.6,
        soft_max=0.9,
    )

    polyskel_max_vertices: IntProperty(
        name="Max Vertices (Polyskel)",
        description="Maximum footprint vertices for polyskel hipped roofs (complex shapes)",
        default=12,
        min=5,
        max=20,
    )

    # --- Reproducibility ---

    global_seed: IntProperty(
        name="Random Seed",
        description="Seed for deterministic texture/style variation (same seed = same results)",
        default=42,
        min=0,
    )

    # --- Debug Options ---

    debug_osm_id: StringProperty(
        name="Debug OSM ID",
        description="Process only this specific building (leave empty for all buildings)",
        default="",
    )

    # --- Import State (internal) ---

    last_import_buildings: IntProperty(
        name="Last Import Count",
        description="Number of buildings from last import",
        default=0,
    )

    last_import_time_ms: IntProperty(
        name="Last Import Time",
        description="Processing time of last import in milliseconds",
        default=0,
    )

    last_patches_processed: IntProperty(
        name="Patches Processed",
        description="Number of patches processed in last import",
        default=0,
    )

    # --- Progress Tracking ---

    is_processing: BoolProperty(
        name="Processing",
        description="Whether import is currently running",
        default=False,
    )

    current_patch: StringProperty(
        name="Current Patch",
        description="Patch currently being processed",
        default="",
    )


def _tree_rows_changed(context):
    """Changing a tree-row spacing rebuilds what is already in the scene. Guarded, so
    deleting blender/tree_rows.py changes nothing here."""
    try:
        from . import tree_rows
        tree_rows._spacing_update(None, context)
    except Exception:
        pass


def _pbf_changed(prefs, context, path_only=False):
    """PBF checkbox / file changed: install pyosmium on the first tick and store the
    preferences right away. Guarded, so deleting blender/pbf_source.py changes
    nothing here."""
    try:
        from . import pbf_source
        pbf_source.on_settings_changed(prefs, context, path_only)
    except Exception as e:
        print(f"[PBF] {e}")


class CondorBuildingsPreferences(AddonPreferences):
    """Add-on preferences (Edit > Preferences > Add-ons > Condor Buildings).

    Terrain smoothing lives here instead of the sidebar panel: Blender stores
    add-on preferences in userpref.blend, so the settings are remembered across
    scenes and Blender restarts without the addon keeping its own file.
    """

    # Package root ("condor_buildings"), which is what Blender registers.
    bl_idname = __package__.split('.')[0]

    terrain_smooth_enable: BoolProperty(
        name="Enable Laplacian Smooth Modifier for Terrain (Tessellation Simulation)",
        description=("Place all autogen (buildings, powerlines, aerialways, bridges, "
                     "turbines, solar, substations, chimneys, transmitters) on a "
                     "Laplacian-smoothed terrain, which approximates the surface "
                     "Condor produces by tessellation. Off = original terrain"),
        default=False,
    )

    terrain_smooth_repeat: IntProperty(
        name="Repeat",
        description="Smooth modifier Repeat (number of smoothing iterations)",
        default=2,
        min=1,
        max=50,
    )

    terrain_smooth_lambda: FloatProperty(
        name="Lambda Factor",
        description="Smooth modifier Lambda Factor (smoothing strength)",
        default=4.0,
        min=0.0,
        max=100.0,
    )

    terrain_smooth_border: FloatProperty(
        name="Lambda Border",
        description=("Smooth modifier Lambda Border. 0 keeps the patch edges in "
                     "place, so no gap appears towards the neighbouring patch"),
        default=0.0,
        min=0.0,
        max=100.0,
    )

    terrain_smooth_axis_x: BoolProperty(
        name="X",
        description="Smooth modifier Axis X (leave off - terrain must not shift sideways)",
        default=False,
    )

    terrain_smooth_axis_y: BoolProperty(
        name="Y",
        description="Smooth modifier Axis Y (leave off - terrain must not shift sideways)",
        default=False,
    )

    terrain_smooth_axis_z: BoolProperty(
        name="Z",
        description="Smooth modifier Axis Z (heights - this is the one that matters)",
        default=True,
    )

    terrain_smooth_preserve_volume: BoolProperty(
        name="Preserve Volume",
        description="Smooth modifier Preserve Volume",
        default=True,
    )

    terrain_smooth_normalized: BoolProperty(
        name="Normalized",
        description="Smooth modifier Normalized",
        default=True,
    )

    # --- TREE ROWS add-on (removable: delete blender/tree_rows.py; these two settings
    # then simply go unused) ---------------------------------------------------------
    # They live here, not in the scene, so the spacing a user settles on is remembered
    # for every .blend they open afterwards.
    tree_row_spacing: FloatProperty(
        name="Tree spacing",
        description="Distance between the trees along a tree row / an alley (m)",
        default=20.0, min=10.0, max=30.0, step=50, precision=1,
        subtype='DISTANCE', unit='LENGTH',
        update=lambda self, ctx: _tree_rows_changed(ctx),
    )

    tree_row_copernicus: BoolProperty(
        name="Copernicus",
        description=("Also build trees from the Copernicus Small Woody Features layer - "
                     "the European map of woody vegetation outside forests (hedgerows, "
                     "tree lines). EUROPE ONLY, downloaded from the internet (one image "
                     "per patch, then cached). Off by default"),
        default=False,
        update=lambda self, ctx: _tree_rows_changed(ctx),
    )

    tree_row_hedge_spacing: FloatProperty(
        name="Hedge spacing",
        description="Distance between the bushes of a hedgerow (m). The bushes are "
                    "widened to span it, so the hedge stays a continuous strip",
        default=8.0, min=5.0, max=10.0, step=50, precision=1,
        subtype='DISTANCE', unit='LENGTH',
        update=lambda self, ctx: _tree_rows_changed(ctx),
    )

    # --- FENCE add-on (removable: delete blender/fences.py; this setting then simply
    # goes unused) --------------------------------------------------------------------
    fence_post_spacing: FloatProperty(
        name="Post spacing",
        description="Target distance between two fence posts (m). A post always stands "
                    "in every corner of the fence; between the corners the stretch is "
                    "divided evenly by roughly this much",
        default=4.0, min=2.0, max=8.0, step=50, precision=1,
        subtype='DISTANCE', unit='LENGTH',
    )

    # --- PBF add-on (removable: delete blender/pbf_source.py; these two settings
    # then simply go unused) ----------------------------------------------------------
    # Drawn in the sidebar panel above Generate Buildings. Kept here so the choice and
    # the file are remembered after a Blender restart.
    use_pbf: BoolProperty(
        name="OSM_pbf",
        description=("Take all OSM data from the local .osm.pbf file below instead of "
                     "the Overpass servers - no server is contacted while this is on. "
                     "The first tick installs the pyosmium reader (needs internet once)"),
        default=False,
        update=lambda self, ctx: _pbf_changed(self, ctx),
    )

    pbf_path: StringProperty(
        name="PBF files",
        description=("Local OpenStreetMap extracts (.osm.pbf), e.g. Geofabrik country / "
                     "region files - several are stored joined by '|'"),
        default="",
        update=lambda self, ctx: _pbf_changed(self, ctx, path_only=True),
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "terrain_smooth_enable")

        # Same fields, same order as the Smooth (Laplacian) modifier panel.
        col = layout.column(align=True)
        col.enabled = self.terrain_smooth_enable
        col.prop(self, "terrain_smooth_repeat")
        col.prop(self, "terrain_smooth_lambda")
        col.prop(self, "terrain_smooth_border")

        row = col.row(align=True)
        row.label(text="Axis")
        row.prop(self, "terrain_smooth_axis_x", toggle=True)
        row.prop(self, "terrain_smooth_axis_y", toggle=True)
        row.prop(self, "terrain_smooth_axis_z", toggle=True)

        col.prop(self, "terrain_smooth_preserve_volume")
        col.prop(self, "terrain_smooth_normalized")

        # --- TREE ROWS add-on (removable) ---
        box = layout.box()
        box.label(text="Tree rows", icon='OUTLINER_OB_MESH')
        box.prop(self, "tree_row_spacing")
        box.prop(self, "tree_row_hedge_spacing")
        box.prop(self, "tree_row_copernicus")

        # --- FENCE add-on (removable) ---
        box = layout.box()
        box.label(text="Fences", icon='MOD_ARRAY')
        box.prop(self, "fence_post_spacing")


# --- Panel settings remembered in a JSON file ----------------------------------------
# The scene settings live only in the .blend; this file keeps the last used Condor
# path, landscape, patch numbers and checkboxes, so they are filled in again in every
# new Blender session. Stored next to the add-on folder (in the addons folder).
_SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "condor_buildings_settings.json")

_SETTINGS_FIELDS = (
    "condor_path", "landscape_name",
    "patch_x_min", "patch_x_max", "patch_y_min", "patch_y_max", "patch_id",
)

# Checkboxes of the 'Other objects' section, registered on the scene by their modules.
_SETTINGS_SCENE_FIELDS = (
    "condor_bridge_batch", "condor_fence_batch", "condor_watertower_batch",
    "condor_silo_batch", "condor_tree_row_batch", "condor_transmitter_batch",
)

# PBF checkbox + chosen files, kept in the add-on preferences (userpref.blend) as well.
_SETTINGS_PREF_FIELDS = ("use_pbf", "pbf_path")

_settings_ready = False
_settings_last = None


def _settings_collect(scene):
    """Current values of the remembered settings."""
    props = getattr(scene, "condor_buildings", None)
    if props is None:
        return None
    data = {}
    names = list(_SETTINGS_FIELDS)
    for p in CondorBuildingsProperties.bl_rna.properties:
        if p.type == 'BOOLEAN' and p.identifier != "is_processing":
            names.append(p.identifier)
    for name in names:
        try:
            data[name] = getattr(props, name)
        except Exception:
            pass
    for name in _SETTINGS_SCENE_FIELDS:
        if hasattr(scene, name):
            data[name] = bool(getattr(scene, name))
    prefs = _settings_prefs()
    if prefs is not None:
        for name in _SETTINGS_PREF_FIELDS:
            if hasattr(prefs, name):
                data[name] = getattr(prefs, name)
    return data


def _settings_prefs():
    """The add-on preferences, or None."""
    try:
        addon = bpy.context.preferences.addons.get(CondorBuildingsPreferences.bl_idname)
        return addon.preferences if addon else None
    except Exception:
        return None


def _settings_apply():
    """Fill the panel from the JSON file (Condor path first, so the landscape list
    is already known when the landscape is set)."""
    global _settings_ready, _settings_last
    import json
    scene = bpy.context.scene
    props = getattr(scene, "condor_buildings", None) if scene else None
    if props is None:
        return
    data = None
    if os.path.isfile(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[Condor Buildings] Settings file not read: {e}")
    if isinstance(data, dict):
        if "condor_path" in data:
            try:
                props.condor_path = data["condor_path"]
            except Exception:
                pass
        for name, value in data.items():
            if name == "condor_path":
                continue
            try:
                if name in _SETTINGS_SCENE_FIELDS:
                    if hasattr(scene, name):
                        setattr(scene, name, value)
                elif name in _SETTINGS_PREF_FIELDS:
                    # Only when it differs: setting it runs the PBF checkbox / file
                    # callback (pyosmium check + storing the preferences).
                    prefs = _settings_prefs()
                    if prefs is not None and getattr(prefs, name) != value:
                        setattr(prefs, name, value)
                elif hasattr(props, name):
                    setattr(props, name, value)
            except Exception:
                pass
    _settings_last = _settings_collect(scene)
    _settings_ready = True


def _settings_save_poll():
    """Writes the JSON whenever one of the remembered settings has changed."""
    global _settings_last
    if not _settings_ready:
        return 1.0
    try:
        scene = bpy.context.scene
        data = _settings_collect(scene) if scene else None
        if data is not None and data != _settings_last:
            import json
            with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            _settings_last = data
    except Exception as e:
        print(f"[Condor Buildings] Settings file not written: {e}")
    return 1.0


def _settings_first_load():
    try:
        _settings_apply()
    except Exception as e:
        print(f"[Condor Buildings] Settings not loaded: {e}")
    return None


@bpy.app.handlers.persistent
def _settings_load_post(*args):
    _settings_first_load()


# Registration
_classes = [
    CondorBuildingsProperties,
    CondorBuildingsPreferences,
]


def register():
    """Register property classes."""
    for cls in _classes:
        bpy.utils.register_class(cls)

    # Add properties to scene
    bpy.types.Scene.condor_buildings = bpy.props.PointerProperty(
        type=CondorBuildingsProperties
    )

    # Remembered panel settings (JSON next to the add-on folder)
    if _settings_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_settings_load_post)
    bpy.app.timers.register(_settings_first_load, first_interval=0.5)
    bpy.app.timers.register(_settings_save_poll, first_interval=1.0, persistent=True)


def unregister():
    """Unregister property classes."""
    # Remembered panel settings
    global _settings_ready
    _settings_ready = False
    if _settings_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_settings_load_post)
    for fn in (_settings_first_load, _settings_save_poll):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)

    # Remove from scene
    del bpy.types.Scene.condor_buildings

    # Unregister classes in reverse order
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
