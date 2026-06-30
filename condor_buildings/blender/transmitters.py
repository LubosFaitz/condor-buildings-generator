"""
Transmitters (communication masts/towers) - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Chimney feature (Import / Merge / file-mode Batch) but for OSM
communication transmitters. Everything lives in THIS one file plus a few tiny,
clearly-marked, try/except-guarded hooks in:
  - __init__.py            (register/unregister this module)
  - blender/panels.py      (draw the Transmitter row inside "Other objects")
  - blender/batch_processing.py (add transmitters to the file-mode OBJ on Batch)

Delete this file (or comment out those guarded hooks) and the plugin falls back
to exactly the previous behaviour - nothing else depends on it.

OSM detection (the whole core):
  man_made=mast OR man_made=tower, together with tower:type=communication.
  height=* gives the real height.

Models (assets/3Dobjects):
  height <= 100 m  -> transmitter_small.obj  (material condor_transmitter_small)
  height >  100 m  -> transmitter_big.obj    (material condor_transmitter_big)
  Both are SCALED in Z to the OSM height (only if a height tag is present), foot
  kept on the terrain. Merge joins all big into one 'transmitter_big' object and
  all small into one 'transmitter_small' object (separate - different textures).
"""

import os
import logging

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

HEIGHT_THRESHOLD = 100.0  # > 100 m -> big model, else small
_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "3Dobjects")

# model key -> (obj file, dds texture, material name)
MODELS = {
    "big":   ("transmitter_big.obj",   "transmitter_big.dds",   "condor_transmitter_big"),
    "small": ("transmitter_small.obj", "transmitter_small.dds", "condor_transmitter_small"),
}


def _is_transmitter(tags):
    """OSM communication mast/tower.
    - man_made=communications_tower is a transmitter on its own (no tower:type needed).
    - man_made=mast / tower counts when tower:type=communication.
    """
    mm = tags.get("man_made")
    if mm not in ("mast", "tower", "communications_tower"):
        return False
    if mm == "communications_tower":
        return True
    if tags.get("tower:type") == "communication":
        return True
    return any(k == "communication" or k.startswith("communication:") for k in tags)


def _parse_transmitters(root, projector):
    """Return list of (x, y, height, has_height, model_key) from an OSM tree."""
    from .operators import _parse_height_str, _point_in_polygon

    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}

    out = []
    for n in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        if _is_transmitter(tags):
            has_h = "height" in tags
            h = _parse_height_str(tags.get("height", "0"))
            x, y = projector.project(float(n.get("lat")), float(n.get("lon")))
            model = "big" if (has_h and h > HEIGHT_THRESHOLD) else "small"
            out.append((x, y, h, has_h, model))

    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if not _is_transmitter(tags):
            continue
        coords = [node_coords[nd.get("ref")] for nd in w.findall("nd")
                  if nd.get("ref") in node_coords]
        if not coords:
            continue
        poly = [projector.project(la, lo) for la, lo in coords]
        if any(_point_in_polygon(px, py, poly) for px, py, *_ in out):
            continue  # a node transmitter already sits inside this way
        la = sum(c[0] for c in coords) / len(coords)
        lo = sum(c[1] for c in coords) / len(coords)
        has_h = "height" in tags
        h = _parse_height_str(tags.get("height", "0"))
        x, y = projector.project(la, lo)
        model = "big" if (has_h and h > HEIGHT_THRESHOLD) else "small"
        out.append((x, y, h, has_h, model))
    return out


# ----------------------------------------------------------------------------
# OBJ template loader (verts/uvs/faces) for the file-mode build.
# ----------------------------------------------------------------------------
def _load_obj(path):
    verts, uvs, faces = [], [], []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); uvs.append((float(p[1]), float(p[2])))
            elif line.startswith("f "):
                vi, ti = [], []
                ok = True
                for tok in line.split()[1:]:
                    bits = tok.split("/")
                    vi.append(int(bits[0]) - 1)
                    if len(bits) > 1 and bits[1]:
                        ti.append(int(bits[1]) - 1)
                    else:
                        ok = False
                faces.append((vi, ti if ok and len(ti) == len(vi) else None))
    minz = min(v[2] for v in verts) if verts else 0.0
    nativ = (max(v[2] for v in verts) - minz) if verts else 1.0
    return verts, uvs, faces, minz, nativ


def build_transmitter_meshdata(props, paths, patch_id):
    """File-mode: return {'transmitter_big': MeshData, 'transmitter_small': MeshData}
    in Condor-swapped coords (like build_chimney_meshdata), or {} if none."""
    import xml.etree.ElementTree as ET
    from ..projection.transverse_mercator import TransverseMercatorProjector
    from ..io.patch_metadata import load_patch_metadata
    from ..io.terrain_loader import load_terrain
    from ..models.geometry import Point2D, BBox
    from ..models.mesh import MeshData
    from ..io.obj_exporter import _condor_xform

    osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
    if not os.path.exists(osm_path):
        return {}
    txt_path = None
    for cand in (os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                 os.path.join(paths['heightmaps'], f"H{patch_id}.txt")):
        if os.path.exists(cand):
            txt_path = cand; break
    if not txt_path:
        return {}
    terrain_file = os.path.join(paths['heightmaps'], "modified", f"h{patch_id}.obj")
    if not os.path.exists(terrain_file):
        terrain_file = os.path.join(paths['heightmaps'], f"h{patch_id}.obj")
    if not os.path.exists(terrain_file):
        return {}
    try:
        meta = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
        terrain = load_terrain(terrain_file)
        root = ET.parse(osm_path).getroot()
    except Exception as e:
        logger.warning("transmitter: setup failed for %s: %s", patch_id, e)
        return {}

    items = _parse_transmitters(root, projector)
    if not items:
        return {}

    def _terrain_z(cx, cy):
        bb = BBox(cx - 1, cy - 1, cx + 1, cy + 1)
        for ti in terrain.get_triangles_in_bbox(bb):
            tri = terrain.triangles[ti]
            if tri.contains_point_2d(Point2D(cx, cy)):
                z = tri.z_at_xy(cx, cy)
                if z is not None:
                    return z
        return terrain.z_min

    tmpl = {}
    for key, (objfile, _dds, _mat) in MODELS.items():
        p = os.path.join(_ASSETS, objfile)
        tmpl[key] = _load_obj(p) if os.path.exists(p) else None

    out = {}
    for (cx, cy, h, has_h, model) in items:
        t = tmpl.get(model)
        if t is None:
            continue
        verts, uvs, faces, minz, nativ = t
        s = (h / nativ) if (has_h and nativ > 0) else 1.0
        foot = _terrain_z(cx, cy)
        md = out.get(model)
        if md is None:
            md = MeshData(osm_id=model)
            out[model] = md
        vbase = len(md.vertices)
        ubase = len(md.uvs)
        for (vx, vy, vz) in verts:
            # RAW pipeline coords (NO axis swap - the exporter swaps ALL groups
            # together). UNIFORM scale (x, y, z all by s) so the tower keeps its
            # shape (Z-only would deform it). The model origin (0,0,0 at the foot)
            # lands on the terrain at (cx, cy, foot).
            md.vertices.append((cx + s * vx, cy + s * vy, foot + s * vz))
        for (u, v) in uvs:
            md.uvs.append((u, v))
        for (vi, ti) in faces:
            md.faces.append([vbase + i + 1 for i in vi])
            if ti is not None:
                md.face_uvs.append([ubase + i + 1 for i in ti])
    # rename keys to the object/material group names
    return {("transmitter_" + k): v for k, v in out.items()}


def add_filemode_groups(groups, props, paths, patch_id):
    """Hook for batch_processing file mode: add transmitter groups when Batch on."""
    if not getattr(bpy.context.scene, "condor_transmitter_batch", False):
        return
    for name, md in build_transmitter_meshdata(props, paths, patch_id).items():
        if md and not md.is_empty():
            groups[name] = md
    # copy textures
    dest = os.path.join(paths['autogen'], "Textures")
    import shutil
    for key, (_obj, dds, _mat) in MODELS.items():
        src = os.path.join(_ASSETS, dds); dst = os.path.join(dest, dds)
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(dest, exist_ok=True)
            try: shutil.copy2(src, dst)
            except Exception: pass


# ----------------------------------------------------------------------------
# Operators: Import + Merge (mirror the chimney ones, adapted).
# ----------------------------------------------------------------------------
class CONDOR_OT_import_transmitters(Operator):
    bl_idname = "condor.import_transmitters"
    bl_label = "Import Transmitters"
    bl_description = "Import communication transmitters (man_made=mast/tower, tower:type=communication)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return (props.condor_path and props.landscape_name != 'NONE'
                and not getattr(context.scene, "condor_transmitter_batch", False))

    def execute(self, context):
        import xml.etree.ElementTree as ET
        import shutil as _shutil
        from .operators import resolve_condor_paths
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        tex_dest = os.path.join(paths['autogen'], "Textures")

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
            txt_path = next((p for p in (
                os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
            if not txt_path:
                continue
            terrain_file = os.path.join(paths['heightmaps'], "modified", f"h{patch_id}.obj")
            if not os.path.exists(terrain_file):
                terrain_file = os.path.join(paths['heightmaps'], f"h{patch_id}.obj")
            if not os.path.exists(terrain_file):
                continue
            try:
                meta = load_patch_metadata(txt_path)
                projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
                root = ET.parse(osm_path).getroot()
            except Exception:
                continue

            items = _parse_transmitters(root, projector)
            if not items:
                continue

            # Clear previous transmitters of THIS patch before re-importing, so a
            # repeated import doesn't pile up .001 / .002 duplicates.
            for o in [x for x in bpy.data.objects
                      if x.get("patch_id") == patch_id and x.name.startswith("Transmitter_")]:
                bpy.data.objects.remove(o, do_unlink=True)

            # copy textures once we know there's something
            for key, (_obj, dds, _mat) in MODELS.items():
                s = os.path.join(_ASSETS, dds); d = os.path.join(tex_dest, dds)
                if os.path.exists(s) and not os.path.exists(d):
                    os.makedirs(tex_dest, exist_ok=True)
                    try: _shutil.copy2(s, d)
                    except Exception: pass

            terrain_obj = bpy.data.objects.get(f"TR3{patch_id}")
            px, py = int(patch_id[:3]), int(patch_id[3:])
            if not props.single_patch_mode:
                off_x = -(px - props.patch_x_min) * 5760.0
                off_y = (py - props.patch_y_min) * 5760.0
            else:
                off_x = off_y = 0.0

            terrain_orig = None
            if props.import_patch_terrain and terrain_obj and (off_x or off_y):
                terrain_orig = terrain_obj.location.copy()
                terrain_obj.location = (0.0, 0.0, 0.0)
                context.view_layer.update()

            terrain_mesh = None
            if not (props.import_patch_terrain and terrain_obj):
                from ..io.terrain_loader import load_terrain
                try: terrain_mesh = load_terrain(terrain_file)
                except Exception: terrain_mesh = None

            placed = []
            for idx, (cx, cy, h, has_h, model) in enumerate(items):
                objfile = MODELS[model][0]
                p = os.path.join(_ASSETS, objfile)
                if not os.path.exists(p):
                    continue
                if hasattr(bpy.ops.wm, 'obj_import'):
                    bpy.ops.wm.obj_import(filepath=p, forward_axis='Y', up_axis='Z')
                else:
                    bpy.ops.import_scene.obj(filepath=p, axis_forward='Y', axis_up='Z')
                imported = context.selected_objects
                if not imported:
                    continue
                ob = imported[0]
                ob.name = f"Transmitter_{model}_{patch_id}_{idx + 1:03d}"
                ob["patch_id"] = patch_id
                ob["transmitter_model"] = model

                nativ = (max(v.co.z for v in ob.data.vertices) - min(v.co.z for v in ob.data.vertices)) or 1.0
                s = (h / nativ) if (has_h and nativ > 0) else 1.0
                ob.scale = (s, s, s)  # uniform - keep shape, don't deform

                foot_z = 0.0
                got_foot = False
                if props.import_patch_terrain and terrain_obj:
                    try:
                        dg = context.evaluated_depsgraph_get()
                        hit, loc, _, _ = terrain_obj.evaluated_get(dg).ray_cast((cx, cy, 10000.0), (0, 0, -1))
                        if hit: foot_z = loc.z; got_foot = True
                    except Exception:
                        # terrain HIDDEN -> no evaluated mesh; fall back to file
                        if terrain_mesh is None:
                            from ..io.terrain_loader import load_terrain
                            try: terrain_mesh = load_terrain(terrain_file)
                            except Exception: terrain_mesh = None
                if not got_foot and terrain_mesh:
                    from ..models.geometry import Point2D, BBox
                    bb = BBox(cx - 1, cy - 1, cx + 1, cy + 1)
                    for ti in terrain_mesh.get_triangles_in_bbox(bb):
                        tri = terrain_mesh.triangles[ti]
                        if tri.contains_point_2d(Point2D(cx, cy)):
                            z = tri.z_at_xy(cx, cy)
                            if z is not None: foot_z = z; break
                # origin (the model's foot at local 0,0,0) sits on the terrain
                ob.location = (cx, cy, foot_z)
                placed.append(ob)

                col_name = f"Condor_{props.landscape_name}_{patch_id}"
                col = bpy.data.collections.get(col_name) or bpy.data.collections.new(col_name)
                if col.name not in [c.name for c in context.scene.collection.children] and not col.users_scene:
                    try: context.scene.collection.children.link(col)
                    except Exception: pass
                sub_name = f"transmitter_{model}_{patch_id}"
                sub = bpy.data.collections.get(sub_name) or bpy.data.collections.new(sub_name)
                if sub.name not in [c.name for c in col.children]:
                    col.children.link(sub)
                for c in list(ob.users_collection):
                    c.objects.unlink(ob)
                sub.objects.link(ob)
                total += 1

            if terrain_orig is not None and terrain_obj:
                terrain_obj.location = terrain_orig
            if off_x or off_y:
                for ob in placed:
                    ob.location.x += off_x; ob.location.y += off_y

        self.report({'INFO'}, f"Imported {total} transmitters")
        return {'FINISHED'}


class CONDOR_OT_merge_transmitters(Operator):
    bl_idname = "condor.merge_transmitters"
    bl_label = "Merge Transmitters"
    bl_description = "Merge Transmitter_* objects per patch into transmitter_big / transmitter_small"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _is_t(name):
        return name.lower().startswith("transmitter_")

    @staticmethod
    def _condor_collection(obj):
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
        return any(cls._is_t(o.name) for o in bpy.data.objects)

    def execute(self, context):
        import re as _re
        objs = [o for o in bpy.data.objects if self._is_t(o.name) and o.name in context.view_layer.objects]
        if not objs:
            self.report({'WARNING'}, "No transmitter objects found")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # group by (Condor collection, model) -> separate big/small objects
        groups = {}
        for o in objs:
            col = self._condor_collection(o)
            model = o.get("transmitter_model") or ("big" if "_big_" in o.name else "small")
            groups.setdefault((col.name if col else None, model), []).append(o)

        for (col_name, model), members in groups.items():
            bpy.ops.object.select_all(action='DESELECT')
            for o in members:
                o.select_set(True)
            context.view_layer.objects.active = members[0]
            bpy.ops.object.join()
            merged = context.active_object
            merged.name = f"transmitter_{model}"
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            mat_name = MODELS[model][2]
            mat = bpy.data.materials.get(mat_name)
            merged.data.materials.clear()
            if mat:
                merged.data.materials.append(mat)

            target = bpy.data.collections.get(col_name) if col_name else None
            if target:
                for c in list(merged.users_collection):
                    c.objects.unlink(merged)
                target.objects.link(merged)

        # clean empty transmitter_* sub-collections + duplicate materials
        for col in list(bpy.data.collections):
            if _re.match(r'^transmitter_(big|small)_\d{6}$', col.name) and not col.objects:
                for parent in list(bpy.data.collections):
                    if col.name in [c.name for c in parent.children]:
                        parent.children.unlink(col)
                bpy.data.collections.remove(col)
        for mat in list(bpy.data.materials):
            if _re.match(r'^condor_transmitter_(big|small)\.\d+$', mat.name):
                mat.use_fake_user = False
                bpy.data.materials.remove(mat)

        self.report({'INFO'}, f"Merged transmitters ({len(groups)} group(s))")
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Panel row (called from panels.py inside the "Other objects" box).
# ----------------------------------------------------------------------------
def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Transmitter", icon='MOD_SCREW')
    row.prop(context.scene, "condor_transmitter_batch", text="Batch")
    row = box.row(align=True)
    row.operator("condor.import_transmitters", text="Import", icon='IMPORT')
    row.operator("condor.merge_transmitters", text="Merge", icon='AUTOMERGE_ON')


# ----------------------------------------------------------------------------
# Registration (operators + scene property + TEXTURE_MAP entries).
# ----------------------------------------------------------------------------
_classes = [CONDOR_OT_import_transmitters, CONDOR_OT_merge_transmitters]


def _patch_overpass_query():
    """Wrap osm_downloader.build_overpass_query so the OSM download ALSO fetches
    communication masts/towers - kept here so the whole feature lives in one file.
    Removing this module restores the original query."""
    from . import osm_downloader as _osm
    if getattr(_osm, "_transmitter_patched", False):
        return
    _orig = _osm.build_overpass_query

    def _patched(lat_min, lat_max, lon_min, lon_max, *a, **k):
        q = _orig(lat_min, lat_max, lon_min, lon_max, *a, **k)
        bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
        extra = (
            f'  node["man_made"="communications_tower"]({bbox});\n'
            f'  way["man_made"="communications_tower"]({bbox});\n'
            f'  node["man_made"="mast"]({bbox});\n'
            f'  way["man_made"="mast"]({bbox});\n'
            f'  node["man_made"="tower"]({bbox});\n'
            f'  way["man_made"="tower"]({bbox});'
        )
        return q.replace("\n);", "\n" + extra + "\n);", 1)

    _osm._transmitter_orig_query = _orig
    _osm.build_overpass_query = _patched
    _osm._transmitter_patched = True


def _unpatch_overpass_query():
    from . import osm_downloader as _osm
    if getattr(_osm, "_transmitter_patched", False):
        _osm.build_overpass_query = _osm._transmitter_orig_query
        _osm._transmitter_patched = False


def register():
    from bpy.props import BoolProperty
    bpy.types.Scene.condor_transmitter_batch = BoolProperty(
        name="Batch",
        description=("File mode (Import to Blender off): after generating the OBJ, "
                     "also generate transmitters and add them as transmitter_big / "
                     "transmitter_small objects. Off by default"),
        default=False,
    )
    for c in _classes:
        bpy.utils.register_class(c)
    # add textures to the export map so file-mode MTL gets them
    try:
        from .. import config
        config.TEXTURE_MAP.setdefault("transmitter_big", "transmitter_big.dds")
        config.TEXTURE_MAP.setdefault("transmitter_small", "transmitter_small.dds")
    except Exception:
        pass
    # make the OSM download fetch communication masts/towers (kept in this module)
    try:
        _patch_overpass_query()
    except Exception:
        pass


def unregister():
    try:
        _unpatch_overpass_query()
    except Exception:
        pass
    for c in reversed(_classes):
        try: bpy.utils.unregister_class(c)
        except Exception: pass
    try:
        del bpy.types.Scene.condor_transmitter_batch
    except Exception:
        pass
    try:
        from .. import config
        config.TEXTURE_MAP.pop("transmitter_big", None)
        config.TEXTURE_MAP.pop("transmitter_small", None)
    except Exception:
        pass
