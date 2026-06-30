Rozumím, Lubo. Aby se ti to nerozkouskovalo do více oken a mohl jsi to zkopírovat naráz, dal jsem celý text do jednoho souvislého bloku. Zároveň jsem zkontroloval a odstranil všechny neviditelné znaky (nedělitelné mezery), jak jsi požadoval.

```text
# Changes compared to Original/ — exact insertion points

---

## processing/mesh_grouper.py

**After line 21** (after the last import) — insert two new functions:
```python
def _nearest_edge_outward(fc_x: float, fc_y: float, ring):
    min_dist_sq = float('inf')
    best_nx = 0.0; best_ny = 0.0
    n = len(ring)
    for i in range(n):
        j = (i + 1) % n
        ax, ay = ring[i].x, ring[i].y
        bx, by = ring[j].x, ring[j].y
        ex = bx - ax; ey = by - ay
        len_sq = ex * ex + ey * ey
        if len_sq < 1e-12:
            continue
        t = max(0.0, min(1.0, ((fc_x - ax) * ex + (fc_y - ay) * ey) / len_sq))
        px = ax + t * ex; py = ay + t * ey
        dist_sq = (fc_x - px) ** 2 + (fc_y - py) ** 2
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            length = len_sq ** 0.5
            best_nx = ey / length; best_ny = -ex / length
    return min_dist_sq ** 0.5, best_nx, best_ny


def _fix_normals_outward(mesh_data: MeshData, outer_ring, holes=None) -> None:
    verts = mesh_data.vertices
    has_uvs = len(mesh_data.face_uvs) == len(mesh_data.faces)
    holes = holes or []
    for i, face in enumerate(mesh_data.faces):
        if len(face) < 3:
            continue
        n = len(face)
        fc_x = sum(verts[idx - 1][0] for idx in face) / n
        fc_y = sum(verts[idx - 1][1] for idx in face) / n
        v0 = verts[face[0]-1]; v1 = verts[face[1]-1]; v2 = verts[face[2]-1]
        ex = v1[0]-v0[0]; ey = v1[1]-v0[1]; ez = v1[2]-v0[2]
        fx = v2[0]-v0[0]; fy = v2[1]-v0[1]; fz = v2[2]-v0[2]
        nx = ey*fz-ez*fy; ny = ez*fx-ex*fz; nz = ex*fy-ey*fx
        if nx*nx + ny*ny + nz*nz < 1e-10:
            continue
        if nx*nx + ny*ny < 1e-6:
            should_flip = nz < 0
        else:
            out_d, out_nx, out_ny = _nearest_edge_outward(fc_x, fc_y, outer_ring)
            exp_nx, exp_ny = out_nx, out_ny
            best_d = out_d
            for hole in holes:
                h_d, h_nx, h_ny = _nearest_edge_outward(fc_x, fc_y, hole)
                if h_d < best_d:
                    best_d = h_d
                    exp_nx, exp_ny = h_nx, h_ny
            should_flip = (nx * exp_nx + ny * exp_ny) < 0
        if should_flip:
            mesh_data.faces[i] = list(reversed(face))
            if has_uvs:
                mesh_data.face_uvs[i] = list(reversed(mesh_data.face_uvs[i]))
```

**Line 48** — change the `__init__` signature:
```diff
-    def __init__(self, num_flat_roof_groups: int = 6, flat_roof_merge: bool = False):
+    def __init__(self, num_flat_roof_groups: int = 6, flat_roof_merge: bool = False, is_lod0: bool = False):
```

**After line 57** (after the `__init__` docstring) — add `self.is_lod0`:
```diff
+        self.is_lod0 = is_lod0
```

**After line 61** (after `self.houses = MeshData()`) — add `self.gabled_roofs`:
```diff
+        self.gabled_roofs = MeshData()
```

**After line 92** (at the beginning of `add_building()`, before if) — add normal fix call:
```diff
+        outer_ring = building.footprint.outer_ring
+        holes = building.footprint.holes
+        _fix_normals_outward(result.walls, outer_ring, holes)
+        _fix_normals_outward(result.roof, outer_ring, holes)
```

**Line 104** — change roof merge in `_add_pitched_building()`:
```diff
-        self.houses.merge(result.roof)
+        if self.is_lod0 and result.actual_roof_type == RoofType.GABLED:
+            self.gabled_roofs.merge(result.roof)
+        else:
+            self.houses.merge(result.roof)
```

**After line 169** (in `get_all_groups()`, after the base dict) — add key:
```diff
+        if self.is_lod0 and not self.gabled_roofs.is_empty():
+            groups['gabled_roofs_lod0'] = self.gabled_roofs
```

### Fix for inverted normals on hipped roofs

**After `self.gabled_roofs = MeshData()`** in `__init__()` — add:
```diff
+        self.hipped_roofs = MeshData()
```

**In `add_building()`** — change the `_fix_normals_outward` call for the roof:
```diff
-        _fix_normals_outward(result.walls, outer_ring, holes)
-        _fix_normals_outward(result.roof, outer_ring, holes)
+        _fix_normals_outward(result.walls, outer_ring, holes)
+        if result.actual_roof_type != RoofType.HIPPED:
+            _fix_normals_outward(result.roof, outer_ring, holes)
```
*Reason: hipped roofs have `double_sided_roof=True` (intentionally top and bottom faces).
`_fix_normals_outward` would flip the bottom faces upwards and destroy the double-sided effect.*

**In `_add_pitched_building()`** — add branch for hipped:
```diff
         if self.is_lod0 and result.actual_roof_type == RoofType.GABLED:
             self.gabled_roofs.merge(result.roof)
-        else:
+        elif result.actual_roof_type == RoofType.HIPPED:
+            self.hipped_roofs.merge(result.roof)
+        else:
             self.houses.merge(result.roof)
```

**In `get_all_groups()`** — add `hipped_roofs` key (after the gabled_roofs block):
```diff
+        if not self.hipped_roofs.is_empty():
+            groups['hipped_roofs'] = self.hipped_roofs
```

---

## blender/mesh_converter.py

**Lines 80–83** — replace `_recalc_normals_outside` call with a conditional one:
```diff
-    _recalc_normals_outside(mesh)
+    # _recalc_normals_outside(mesh)  -- overwrote inner ring normals
+    if recalc_normals:
+        _recalc_normals_outside(mesh)
```
*Note: `recalc_normals` is a new parameter (see below) — for regular objects it is `False`,
for hipped roofs `import_grouped_meshes_to_blender` sets it to `True`.*

**Signature of `meshdata_to_blender()`** — add parameter:
```diff
 def meshdata_to_blender(
     mesh_data,
     name: str = "building",
     collection: Optional[bpy.types.Collection] = None,
     use_osm_id: bool = True,
+    recalc_normals: bool = False
 ) -> bpy.types.Object:
```

**After line 453** (after the `cleanup_buildings_collection` function) — insert two new functions:
```python
def _duplicate_and_flip_mesh(src_obj, name, collection):
    mesh_copy = src_obj.data.copy()
    mesh_copy.flip_normals()
    flip_obj = bpy.data.objects.new(name, mesh_copy)
    collection.objects.link(flip_obj)
    return flip_obj


def _join_objects_into(target_obj, source_objs):
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in [target_obj] + list(source_objs):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = target_obj
    bpy.ops.object.join()
```

**After line 492** (before the main `for` loop in `import_grouped_meshes_to_blender()`) — add:
```diff
+    gabled_roofs_data = grouped_meshes.get('gabled_roofs_lod0')
+    hipped_roofs_data = grouped_meshes.get('hipped_roofs')
+    houses_obj = None
```

**After line 495** (first line inside the `for group_name` loop) — add skip for both types:
```diff
-        if group_name == 'gabled_roofs_lod0':
+        if group_name in ('gabled_roofs_lod0', 'hipped_roofs'):
             continue
```

**After line 510** (after `objects.append(obj)`) — capture houses_obj:
```diff
+        if group_name == 'houses':
+            houses_obj = obj
```

**After the end of the `for` loop** — add duplication of gabled and merging of hipped:
```python
    if gabled_roofs_data and not gabled_roofs_data.is_empty():
        roofs_obj = meshdata_to_blender(
            gabled_roofs_data, name='gabled_roofs_lod0', collection=collection, use_osm_id=False
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

    if hipped_roofs_data and not hipped_roofs_data.is_empty():
        hipped_obj = meshdata_to_blender(
            hipped_roofs_data, name='hipped_roofs', collection=collection,
            use_osm_id=False, recalc_normals=True
        )
        _assign_material(hipped_obj, 'houses', texture_dirs=search_dirs, texture_map=texture_map)
        if houses_obj is not None:
            _join_objects_into(houses_obj, [hipped_obj])
        else:
            hipped_obj.name = 'houses'
            objects.append(hipped_obj)
```

---

## generators/building_generator.py

**Line 771** — add `double_sided_roof=False` to `GabledRoofConfig`:
```diff
-                    include_gable_walls=False
+                    include_gable_walls=False,
+                    double_sided_roof=False
```

---

## main.py

**After line 186** (after the `_apply_terrain_orthophoto_uvs` function or before it) — insert a new function:
```python
def _merge_gabled_for_export(groups: Dict[str, MeshData]) -> Dict[str, MeshData]:
    gabled = groups.get('gabled_roofs_lod0')
    if gabled is None or gabled.is_empty():
        return groups
    merged_houses = MeshData()
    if groups.get('houses') and not groups['houses'].is_empty():
        merged_houses.merge(groups['houses'])
    merged_houses.merge(gabled)
    result = {k: v for k, v in groups.items() if k not in ('gabled_roofs_lod0', 'houses')}
    result['houses'] = merged_houses
    return result
```

**Line 460** — add `is_lod0=True` to `grouper_lod0`:
```diff
-    grouper_lod0 = MeshGrouper(num_flat_roof_groups=6, flat_roof_merge=config.flat_roof_merge)
+    grouper_lod0 = MeshGrouper(num_flat_roof_groups=6, flat_roof_merge=config.flat_roof_merge, is_lod0=True)
```

**Line 697** — wrap `lod0_groups` during OBJ export:
```diff
-                export_mesh_groups(lod0_groups, result_lod0_path, ...)
+                export_mesh_groups(_merge_gabled_for_export(lod0_groups), result_lod0_path, ...)
```

---

## blender/operators.py

**After line 21** (before `def resolve_condor_paths`) — insert a new function:
```python
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
```

**Line 657** — wrap `result.grouped_lod0` during LOD0 export:
```diff
-                    lods.append(("LOD0", result.grouped_lod0))
+                    lods.append(("LOD0", _merge_gabled_for_obj_export(result.grouped_lod0)))
```

```