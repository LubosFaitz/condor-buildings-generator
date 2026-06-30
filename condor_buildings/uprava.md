# Úprava souborů ve složce condor_buildings

Toto je potřeba vložit správně do stejných souborů ve složce **condor_buildings** a souborech v podsložkách.

---

## 1. config.py

Na konci slovníku `TEXTURE_MAP` (řádek ~296) přidat řádek:

```python
'wind_turbine': 'WindTurbine.dds',
```

Výsledek:

```python
    'pylones': 'Pylons.dds',   # powerline towers + cables (optional, Wiek's assets)
    'wind_turbine': 'WindTurbine.dds',
}
```

---

## 2. main.py

### 2.1 Řádek ~81 — přidat statistiku

```python
wind_turbines: int = 0
```

### 2.2 Řádky ~215–242 — přidat novou funkci

```python
def _generate_wind_turbines_group(osm_path, projector, terrain):
    ...
```

Parsuje OSM, volá `generate_wind_turbines_mesh()`, sestaví slovník `wind_turbine`, `wind_turbine_1`, `wind_turbine_2`... pro LOD0 i LOD1.

### 2.3 Řádky ~643–651 — přidat volání funkce v hlavní pipeline

```python
wt0, wt1, wt_count = _generate_wind_turbines_group(...)
if wt0 is not None:
    lod0_groups.update(wt0)
    lod1_groups.update(wt1)
    stats.wind_turbines = wt_count
    ...
```

### 2.4 Řádek ~1079 — přidat výpis statistiky na konci reportu

```python
print(f"  Wind turbines: {report.stats.wind_turbines}")
```

---

## 3. powerlines.py

### 3.1 Za řádek 62 (`"Pylon_Small": "pylon_small.obj",`) přidat:

```python
    "Wind_Turbine": "turbine.obj",
```

### 3.2 Za řádek 409 (`lines_with_geometry: int = 0`) přidat:

```python
    turbines: int = 0
```

### 3.3 Za řádek 509 (konec `generate_powerlines_mesh()`, před `def _main()`) přidat novou funkci:

```python
def generate_wind_turbines_mesh(turbines, terrain: TerrainMesh, templates: Optional[Dict[str, PylonTemplate]] = None) -> Tuple[List[MeshData], int]:
    if templates is None:
        templates = load_pylon_templates()
    tmpl = templates.get("Wind_Turbine")
    if tmpl is None:
        logger.warning("Sablona pro vetrnou elektrarnu nebyla nalezena.")
        return [], 0
    meshes = []
    for t in turbines:
        if not t.in_patch:
            continue
        foot_z = _foot_z(terrain, t.x, t.y)
        mesh = MeshData(osm_id=f"wind_turbine_{len(meshes)}", origin=(t.x, t.y, foot_z))
        _place_pylon(mesh, tmpl, 0.0, 0.0, 0.0, 0.0)
        meshes.append(mesh)
    logger.info("Vygenerovano %d vetrnych elektraren.", len(meshes))
    return meshes, len(meshes)
```

---

## 4. powerline_parser.py

### 4.1 Za řádek 57 (před `@dataclass` nad `class PowerLine`) přidat:

```python
@dataclass
class WindTurbine:
    node_id: str
    x: float
    y: float
    in_patch: bool
```

### 4.2 Do třídy `PowerlineParseResult` (řádek ~144) přidat pole `turbines` (za `lines: List[PowerLine]`):

```python
    turbines: List[WindTurbine]
```

### 4.3 Za řádek 238 (kde se iteruje `for node_elem in root.findall("node"):` a čtou souřadnice uzlů) přidat blok parsování turbín:

```python
    turbines = []
    for node_elem in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in node_elem.findall("tag")}
        if tags.get("power") == "generator" and tags.get("generator:source") == "wind":
            nid = node_elem.get("id")
            if nid in node_coords:
                lat, lon = node_coords[nid]
                x, y = projector.project(lat, lon)
                in_patch = -patch_half <= x <= patch_half and -patch_half <= y <= patch_half
                turbines.append(WindTurbine(node_id=nid, x=x, y=y, in_patch=in_patch))
```

### 4.4 Do slovníku `stats` (před řádek 309 `return PowerlineParseResult`) přidat:

```python
        "turbines_total": len(turbines),
        "turbines_inside": sum(1 for t in turbines if t.in_patch),
```

### 4.5 Řádek 309 změnit

Z:

```python
return PowerlineParseResult(lines=lines, stats=stats, warnings=warnings)
```

Na:

```python
return PowerlineParseResult(lines=lines, turbines=turbines, stats=stats, warnings=warnings)
```

---

## 5. mesh_converter.py

### 5.1 Za řádek 73 (`collection.objects.link(obj)`) přidat před `return obj`:

```python
    if mesh_data.origin is not None:
        obj.location = mesh_data.origin
```

### 5.2 Za řádek 255 (`if not texture_filename:` / `return`) přidat záložní hledání:

```python
    if not texture_filename:
        import re
        base = re.sub(r'_\d+$', '', group_name)
        texture_filename = tmap.get(base)
```

---

## 6. osm_downloader.py

### 6.1 Za řádek 77 (`parts.append(f'  way["power"="minor_line"]({bbox});')`) přidat:

```python
        parts.append(f'  node["power"="generator"]["generator:source"="wind"]({bbox});')
```

---

## 7. properties.py

### 7.1 Na začátek souboru (za `import bpy`) přidat:

```python
import math
```

### 7.2 Za řádek 21 (za importy `from bpy.props`) přidat globální proměnné a funkce:

```python
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
```

### 7.3 Za property `generate_powerlines` (řádek 211) přidat:

```python
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
```

---

## 8. panels.py

### 8.1 Za řádek 57 (tlačítko single patch sekce) přidat:

```python
            row_terr = box.row()
            row_terr.operator("condor.export_terrain", text="Export Terrain", icon='EXPORT')
```

### 8.2 Za řádek 102 (`box.label(text="Pylons + cables -> 'pylones'...", icon='INFO')`) přidat:

```python
        turbines_in_scene = any(
            obj.name == 'wind_turbine' or obj.name.startswith('wind_turbine_')
            for obj in bpy.data.objects
        )
        if turbines_in_scene:
            box.prop(props, "wind_turbine_rotation", slider=True)
            box.operator("condor.merge_wind_turbines", icon='AUTOMERGE_ON')
```

---

## 9. operators.py

### 9.1 Za řádek 213 — přidat blok importu terénu (uvnitř existující funkce `execute`, za místem kde se zpracovává single patch):

```python
        # --- SINGLE PATCH TERRAIN IMPORT ---
        if props.single_patch_mode and props.patch_id:
            patch_id = str(props.patch_id)
            
            if bpy.data.objects.get(f"TR3{patch_id}"):
                pass
            else:
                heightmaps_dir = paths['heightmaps']
                modified_path = os.path.join(heightmaps_dir, "modified", f"h{patch_id}.obj")
                default_path = os.path.join(heightmaps_dir, f"h{patch_id}.obj")
                terrain_obj_path = modified_path if os.path.exists(modified_path) else default_path
                
                if not os.path.exists(terrain_obj_path):
                    self.report({'ERROR'}, f"Terrain obj not found: {terrain_obj_path}")
                    return {'CANCELLED'}
                
                context.scene.cursor.location = (0.0, 0.0, 0.0)
                
                try:
                    context.scene.tool_settings.transform_pivot_point = 'MEDIAN_POINT'
                    context.scene.transform_orientation_slots[0].type = 'GLOBAL'
                except Exception as e:
                    pass
                
                collection_name = "Patch_Terrain"
                terrain_col = bpy.data.collections.get(collection_name)
                if not terrain_col:
                    terrain_col = bpy.data.collections.new(collection_name)
                    context.scene.collection.children.link(terrain_col)
                
                prev_active_col = context.view_layer.active_layer_collection
                
                def find_layer_collection(layer_collection, name):
                    if layer_collection.name == name:
                        return layer_collection
                    for child in layer_collection.children:
                        res = find_layer_collection(child, name)
                        if res:
                            return res
                    return None
                
                layer_col = find_layer_collection(context.view_layer.layer_collection, collection_name)
                if layer_col:
                    context.view_layer.active_layer_collection = layer_col
                    
                if hasattr(bpy.ops.wm, 'obj_import'):
                    bpy.ops.wm.obj_import(filepath=terrain_obj_path, forward_axis='Y', up_axis='Z')
                else:
                    bpy.ops.import_scene.obj(filepath=terrain_obj_path, axis_forward='Y', axis_up='Z')
                    
                imported_objs = context.selected_objects
                if imported_objs:
                    terrain_obj = imported_objs[0]
                    terrain_obj.name = f"TR3{patch_id}"
                    
                    mat_name = terrain_obj.name
                    mat = bpy.data.materials.get(mat_name)
                    if not mat:
                        mat = bpy.data.materials.new(name=mat_name)
                    mat.use_nodes = True
                    mat.node_tree.nodes.clear()
                    
                    bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                    bsdf.location = (0, 0)
                    output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                    output.location = (300, 0)
                    mat.node_tree.links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
                    
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
                        min_x = min([v.co.x for v in verts])
                        max_x = max([v.co.x for v in verts])
                        min_y = min([v.co.y for v in verts])
                        max_y = max([v.co.y for v in verts])
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
```

### 9.2 Za řádek 755 — přidat operátor `CONDOR_OT_export_terrain` (před `_classes`):

```python
class CONDOR_OT_export_terrain(Operator):
    """Export selected terrain patch"""

    bl_idname = "condor.export_terrain"
    bl_label = "Export Terrain"
    bl_description = "Export terrain object to heightmaps and modified folders"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return props.single_patch_mode and props.patch_id and props.condor_path and props.landscape_name != 'NONE'

    def execute(self, context):
        import os
        import shutil
        props = context.scene.condor_buildings
        patch_id = str(props.patch_id)
        
        bpy.ops.object.select_all(action='DESELECT')
        
        obj_name = f"TR3{patch_id}"
        terrain_obj = bpy.data.objects.get(obj_name)
        if not terrain_obj:
            self.report({'ERROR'}, f"Object {obj_name} not found in scene.")
            return {'CANCELLED'}
            
        terrain_obj.select_set(True)
        context.view_layer.objects.active = terrain_obj
        
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}
            
        heightmaps_dir = paths['heightmaps']
        modified_dir = os.path.join(heightmaps_dir, "modified")
        os.makedirs(modified_dir, exist_ok=True)
        
        target_filename = f"h{patch_id}.obj"
        path_2 = os.path.join(modified_dir, target_filename)
        
        if bpy.app.version >= (4, 0, 0):
            bpy.ops.wm.obj_export(
                filepath=path_2,
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
                filepath=path_2,
                use_selection=True,
                axis_forward='Y',
                axis_up='Z',
                use_triangles=True,
                use_normals=True,
                use_uvs=True,
                use_materials=True,
                path_mode='COPY'
            )
            
        if os.path.exists(path_2):
            self.report({'INFO'}, f"Terrain exported to: modified/{target_filename}")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Export failed.")
            return {'CANCELLED'}
```

### 9.3 Za řádek 756 (hned za `CONDOR_OT_export_terrain`) — přidat operátor `CONDOR_OT_merge_wind_turbines`:

```python
class CONDOR_OT_merge_wind_turbines(Operator):
    """Merge all wind_turbine objects into one and apply transforms"""

    bl_idname = "condor.merge_wind_turbines"
    bl_label = "Merge wind_turbine"
    bl_description = "Merge all wind_turbine objects into one, apply All Transforms and assign condor_wind_turbine material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(
            obj.name == 'wind_turbine' or obj.name.startswith('wind_turbine_')
            for obj in bpy.data.objects
        )

    def execute(self, context):
        turbines = [
            obj for obj in bpy.data.objects
            if obj.name == 'wind_turbine' or obj.name.startswith('wind_turbine_')
        ]

        if not turbines:
            self.report({'WARNING'}, "No wind_turbine objects found")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.select_all(action='DESELECT')
        for obj in turbines:
            obj.select_set(True)
        context.view_layer.objects.active = turbines[0]

        bpy.ops.object.join()
        merged = context.active_object
        merged.name = "wind_turbine"
        merged.data.name = "wind_turbine"

        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        import re
        mat_name = "condor_wind_turbine"
        target_mat = bpy.data.materials.get(mat_name)
        merged.data.materials.clear()
        if target_mat:
            merged.data.materials.append(target_mat)
        for poly in merged.data.polygons:
            poly.material_index = 0

        for mat in list(bpy.data.materials):
            if re.match(r'^condor_wind_turbine_\d+$', mat.name):
                mat.use_fake_user = False
                bpy.data.materials.remove(mat)

        self.report({'INFO'}, f"Merged {len(turbines)} turbines into '{merged.name}'")
        return {'FINISHED'}
```

### 9.4 Do `CONDOR_OT_import_buildings.execute()` — přidat validaci meshe po importu

Hned před volání `total_objects.extend(objects)` (u LOD0, LOD1 i BOTH) přidat pomocnou funkci a její volání:

```python
def _validate_objects(objs):
    fixed = 0
    for obj in objs:
        if obj.type == 'MESH' and obj.data.validate():
            obj.data.update()
            fixed += 1
    if fixed:
        print(f"[Condor] mesh.validate() fixed {fixed} object(s)")
```

Po každém volání `import_grouped_meshes_to_blender()` přidat:

```python
_validate_objects(objects)
```

Funkce odstraní degenerované hrany (začátek = konec) a poškozené plochy s duplicitními body. Pokud jsou opravy provedeny, vypíše počet opravených objektů do konzole.

---

### 9.5 Do seznamu `_classes` — přidat oba operátory:

```python
    CONDOR_OT_export_terrain,
    CONDOR_OT_merge_wind_turbines,
```

---

## 10. blender/msprint.py — nový soubor (Microsoft Building Footprints)

Nový modul obsahující veškerou logiku pro stažení a sloučení budov z Microsoft Global ML Building Footprints.

### Veřejné funkce:

#### `download_ms_buildings(metadata, output_dir, patch_id, cache_dir)`
- Pokud `output_dir/map_{patch_id}.osm` již existuje, vrátí ho přímo bez stahování.
- Vypočítá QuadKeys (zoom 9) pro 4 rohy + střed bbox patche.
- Stáhne `dataset-links.csv` z Microsoftu do cache (jednou, pak použije lokální kopii).
- Pro každý QuadKey stáhne odpovídající `.csv.gz` tile soubory (cachováno).
- Vyfiltruje budovy jejichž centroid leží uvnitř bbox patche.
- Uloží výsledek jako `map_{patch_id}.osm` ve formátu OSM XML.
- **Po vytvoření osm souboru smaže všechny `.gz` soubory z cache** (šetří místo na disku), `dataset-links.csv` zůstane.

#### `merge_ms_into_osm(orig_path, ms_path)`
- Načte OSM budovy jako **obrysy** (ne jen centroidy) z `orig_path` (nebo ze zálohy `.ori` pokud existuje).
- Sestaví prostorovou mřížku OSM budov podle jejich bbox (~200 m buňky).
- Pro každou MS budovu testuje **skutečný překryv obrysů** pomocí ray-casting (point-in-polygon): pokud střed nebo jakýkoliv roh MS budovy leží uvnitř OSM budovy (nebo naopak), považuje ji za duplikát a nepřidá ji. Tím se eliminují případy kdy velká OSM budova má střed daleko, ale MS budova fyzicky leží uvnitř ní.
- Nové budovy přidá na konec XML souboru před `</osm>`.
- Zálohu čistého Overpass souboru uloží jako `orig_path + ".ori"` (pouze při prvním sloučení — existující `.ori` se nepřepisuje).
- Díky `.ori` záloze **opakované spuštění nikdy nepřidá MS budovy dvakrát**.

---

## 11. properties.py — checkbox MSprint

### 11.1 Za property `generate_powerlines` přidat:

```python
use_msprint: BoolProperty(
    name="MSprint - add buildings",
    description=(
        "After downloading OSM, adds missing buildings from Microsoft Global Building Footprints. "
        "Gz files are cached (downloaded only once); result is merged into map_*.osm"
    ),
    default=False,
)
```

---

## 12. panels.py — zobrazení checkboxu

### 12.1 V sekci OSM Data — přidat `use_msprint` checkbox pod dropdown:

```python
if props.osm_source == 'DOWNLOAD':
    box.label(text="Will download from Overpass API", icon='URL')
box.prop(props, "use_msprint")
```

Checkbox se zobrazuje pro obě možnosti zdroje OSM dat (Download i Local).

---

## 13. operators.py — MSprint logika v CONDOR_OT_import_buildings

### 13.1 Za nastavení `osm_path` — přidat blok obnovy originálu (checkbox vypnutý):

```python
if not props.use_msprint and osm_path:
    backup = osm_path + ".ori"
    if os.path.exists(backup):
        import shutil as _shutil
        _shutil.copy2(backup, osm_path)
        print(f"[MSprint] Patch {patch_id}: restored from backup {backup}")
```

Pokud checkbox není zaškrtnutý a existuje záloha `.ori`, obnoví čistý Overpass soubor před generováním.

### 13.2 Hned za blok obnovy — přidat MSprint blok (checkbox zapnutý):

Před smyčkou patchů přidat čítač `total_ms_added = 0`. Uvnitř smyčky:

```python
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
```

### 13.3 Na konci generování — výpis do konzole

Před finální `self.report` přidat:

```python
ms_msg = f" | MSprint added: {total_ms_added}" if total_ms_added > 0 else ""
print(f"[Condor] Generation complete: {total_buildings} objects, {patches_processed} patch(es), {elapsed_ms}ms{ms_msg}")
```

Výstup v konzoli např.: `[Condor] Generation complete: 312 objects, 1 patch(es), 4821ms | MSprint added: 127`

### Chování celého workflow:

| Checkbox | `.ori` existuje | Akce |
|----------|----------------|------|
| Vypnutý  | Ne             | Generuje z aktuálního osm (normálně) |
| Vypnutý  | Ano            | Obnoví osm z `.ori`, pak generuje |
| Zapnutý  | Ne             | Stáhne MS osm (nebo použije existující), sloučí, záloha → `.ori` |
| Zapnutý  | Ano            | MS osm použije přímo, sloučí vždy z `.ori` (bez duplikátů) |

### Cesty (vše odvozeno z pluginu):
- MS osm soubor: `Working/Autogen/MSprint/map_{patch_id}.osm`
- Cache gz/csv: `Working/Autogen/MSprint/_cache/`
- Záloha Overpass osm: `Working/Autogen/map_{patch_id}.osm.ori`

---

---

## 14. panels.py — panel Other objects

Za třídu `CONDOR_PT_advanced_panel` (před `CONDOR_PT_debug_panel`) přidat novou třídu:

```python
class CONDOR_PT_other_objects_panel(Panel):
    bl_label = "Other objects"
    bl_idname = "CONDOR_PT_other_objects_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Condor"
    bl_parent_id = "CONDOR_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Chimney", icon='MESH_CYLINDER')
        row = box.row(align=True)
        row.operator("condor.import_chimneys", text="Import", icon='IMPORT')
        row.operator("condor.merge_chimneys", text="Merge", icon='AUTOMERGE_ON')
```

Do `_classes` přidat mezi `CONDOR_PT_advanced_panel` a `CONDOR_PT_debug_panel`:

```python
    CONDOR_PT_other_objects_panel,
```

---

## 15. operators.py — import a sloučení komínů

### 15.1 Pomocné funkce (před třídami operátorů)

```python
def _parse_height_str(height_str):
    try:
        return float(str(height_str).replace('m', '').strip())
    except (ValueError, TypeError):
        return 30.0

def _parse_osm_chimneys(osm_path):
    import xml.etree.ElementTree as ET
    # Vrátí seznam (lat, lon, height_m) pro man_made=chimney nody i waye
    ...

def _import_obj_as_mesh(filepath, context):
    # Importuje OBJ, zkopíruje mesh data, dočasný objekt smaže
    # Vrátí kopii mesh dat nebo None
    ...
```

### 15.2 Operátor `CONDOR_OT_import_chimneys`

```python
class CONDOR_OT_import_chimneys(Operator):
    bl_idname = "condor.import_chimneys"
    bl_label = "Import Chimneys"
```

- Načte modely `chimney_big.obj` a `chimney_small.obj` z `assets/3Dobjects/`.
- Pro každý patch přečte existující OSM soubor a najde `man_made=chimney`.
- Převede souřadnice přes `TransverseMercatorProjector`.
- Z-souřadnici zjistí ray_castem **přímo na terénní objekt** (`terrain_obj.ray_cast()`) — budovy se ignorují.
- Výška ≥ 31 m → `chimney_big`, jinak → `chimney_small`.
- Objekty pojmenuje `Chimney_{patch_id}_{pořadí}`, uloží `patch_id` jako custom property.
- Roztřídí do kolekcí `chimney_big` / `chimney_small` (vytvoří se jen pokud jsou komíny nalezeny).

### 15.3 Operátor `CONDOR_OT_merge_chimneys`

```python
class CONDOR_OT_merge_chimneys(Operator):
    bl_idname = "condor.merge_chimneys"
    bl_label = "Merge Chimneys"
```

- Najde všechny `Chimney_*` objekty, seskupí podle `patch_id`.
- Pro každý patch: join, apply all transforms, materiál `condor_chimney`.
- Přesune výsledek do kolekce `Condor_{landscape}_{patch_id}`.
- Odstraní prázdné kolekce `chimney_big` a `chimney_small`.

### 15.4 Přidat do `_classes`:

```python
    CONDOR_OT_import_chimneys,
    CONDOR_OT_merge_chimneys,
```

---

## 16. utils/triangulation.py — oprava střech s dvorkem (inner ring)

**Soubor:** `utils/triangulation.py`

**Příčina selhání:** Budovy s dvorkem (inner ring v OSM multipolygon relation) dostávaly plochou střechu přes celý půdorys. Starý bridge-and-earclip algoritmus selhával na nekonvexních outer rinzích (L-tvar, U-tvar) → ear-clipping selhal → fallback → plochá střecha.

**Oprava:** Přepsána `triangulate_with_holes()` na tříkrokový fallback:

### 16.1 Nová funkce `_strip_closing_vertex()` — přidat před `_triangulate_blender()`

```python
def _strip_closing_vertex(ring):
    if len(ring) >= 2 and ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        return list(ring[:-1])
    return list(ring)
```

### 16.2 Nová funkce `_triangulate_blender()` — přidat za `_strip_closing_vertex()`

```python
def _triangulate_blender(outer, holes):
    from mathutils import Vector
    from mathutils.geometry import tessellate_polygon
    merged_points = list(outer)
    rings_as_vectors = [[Vector((p.x, p.y, 0.0)) for p in outer]]
    for hole in holes:
        rings_as_vectors.append([Vector((p.x, p.y, 0.0)) for p in hole])
        merged_points.extend(hole)
    tris = tessellate_polygon(rings_as_vectors)
    if not tris:
        raise TriangulationError("Blender tessellate_polygon returned no triangles")
    triangles = [(int(t[0]), int(t[1]), int(t[2])) for t in tris]
    return (merged_points, triangles)
```

### 16.3 Nová funkce `_triangulate_earcut()` — přidat za `_triangulate_blender()`

Záloha přes `mapbox_earcut` pro CLI/non-Blender prostředí. Vyhodí `ImportError` pokud knihovna není nainstalována.

### 16.4 Přejmenovat původní tělo `triangulate_with_holes()` na `_triangulate_with_holes_legacy()`

Původní bridge-and-earclip kód se stane třetí (poslední) zálohou.

### 16.5 Nová `triangulate_with_holes()` — orchestrátor tří strategií

```python
def triangulate_with_holes(outer, holes):
    if not holes:
        triangles = triangulate_polygon(outer)
        return (outer, triangles)
    outer_clean = _strip_closing_vertex(outer)
    holes_clean = [_strip_closing_vertex(h) for h in holes if len(h) >= 3]
    if len(outer_clean) < 3:
        raise TriangulationError("Outer ring has fewer than 3 distinct vertices")
    try:
        return _triangulate_blender(outer_clean, holes_clean)
    except (ImportError, Exception):
        pass
    try:
        return _triangulate_earcut(outer_clean, holes_clean)
    except (ImportError, Exception):
        pass
    return _triangulate_with_holes_legacy(outer_clean, holes_clean)
```

---

### 9.6 `CONDOR_OT_export_condor.execute()` — přepsat na čtení ze scény

Export nespouští pipeline ani neimportuje nic do Blenderu. Čte pouze objekty z kolekce `Condor_{landscape}_{patch}` (resp. `_LOD1`) v outlineru a exportuje je do OBJ+MTL.

Importy změnit z:
```python
from ..main import run_pipeline
from ..config import (PipelineConfig, RoofSelectionMode, ...)
...
```
Na:
```python
import shutil
from ..config import build_texture_map, CONDOR_AXIS_SWAP, CONDOR_EXPORT_TRIANGULATE, CONDOR_EXPORT_NORMALS
from ..io.obj_exporter import export_condor_obj_mtl
from ..generators.powerlines import pylon_texture_path
from .mesh_converter import blender_obj_to_meshdata
```

Logika exportu — pro každý patch:
1. Najdi kolekci `Condor_{landscape}_{patch}` v `bpy.data.collections`
2. Pro každý mesh objekt v kolekci zavolej `blender_obj_to_meshdata(obj, osm_id=obj.name)`
3. Sestav dict `groups = {obj.name: meshdata, ...}`
4. Zavolej `export_condor_obj_mtl(groups, out_obj, condor_tex_map, ...)`
5. Pokud kolekce neexistuje → chybová hláška „nejdřív spusť Generate Buildings"
6. Scéna se nijak nemění — žádný import, žádný cleanup
