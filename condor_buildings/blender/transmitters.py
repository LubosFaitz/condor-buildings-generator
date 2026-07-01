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
  height <= 100 m  -> transmitter_small.obj
  height >  100 m  -> transmitter_big.obj
  Both sizes share ONE material + texture: condor_transmitter / transmitter.dds.
  Both are SCALED to the OSM height (only if a height tag is present), foot kept
  on the terrain. Merge joins all big into one 'transmitter_big' object and all
  small into one 'transmitter_small' object (both use the shared material).
"""

import os
import logging

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

HEIGHT_THRESHOLD = 100.0  # > 100 m -> big model, else small
_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "3Dobjects")

# model key -> (obj file, dds texture, material name)
# Both sizes share ONE material + texture (condor_transmitter / transmitter.dds).
GROUP_NAME = "transmitter"        # object name in the OBJ (like 'pylones', 'chimney')
MAT_NAME = "condor_transmitter"   # material name (object 'transmitter' -> material via alias)
TEX_FILE = "transmitter.dds"
MODELS = {
    "big":   ("transmitter_big.obj",   TEX_FILE, MAT_NAME),
    "small": ("transmitter_small.obj", TEX_FILE, MAT_NAME),
}


def _get_transmitter_material():
    """Return the single shared 'condor_transmitter' material (image transmitter.dds),
    created once with an image-texture node so big and small look identical."""
    mat = bpy.data.materials.get(MAT_NAME)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    img_path = os.path.join(_ASSETS, TEX_FILE)
    if os.path.exists(img_path) and bsdf is not None:
        try:
            img = bpy.data.images.load(img_path, check_existing=True)
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        except Exception:
            pass
    return mat


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
    # Reads v / vt / vn / f. Normals (vn) are kept so the file-mode writer reuses the
    # model's OWN normals (authored flip / duplicate faces stay intact - no recompute).
    verts, uvs, norms, faces = [], [], [], []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vn "):
                p = line.split(); norms.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); uvs.append((float(p[1]), float(p[2])))
            elif line.startswith("f "):
                vi, ti, ni = [], [], []
                okt = okn = True
                for tok in line.split()[1:]:
                    bits = tok.split("/")
                    vi.append(int(bits[0]) - 1)
                    if len(bits) > 1 and bits[1]:
                        ti.append(int(bits[1]) - 1)
                    else:
                        okt = False
                    if len(bits) > 2 and bits[2]:
                        ni.append(int(bits[2]) - 1)
                    else:
                        okn = False
                faces.append((vi,
                              ti if okt and len(ti) == len(vi) else None,
                              ni if okn and len(ni) == len(vi) else None))
    minz = min(v[2] for v in verts) if verts else 0.0
    nativ = (max(v[2] for v in verts) - minz) if verts else 1.0
    return verts, uvs, norms, faces, minz, nativ


def _transmitter_setup(props, paths, patch_id):
    """Shared file-mode setup: OSM transmitters + a terrain-height function + the asset
    templates (verts, uvs, NORMALS, faces). Returns (items, terrain_z_fn, tmpl) or None."""
    import xml.etree.ElementTree as ET
    from ..projection.transverse_mercator import TransverseMercatorProjector
    from ..io.patch_metadata import load_patch_metadata
    from ..io.terrain_loader import load_terrain
    from ..models.geometry import Point2D, BBox

    osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
    if not os.path.exists(osm_path):
        return None
    txt_path = None
    for cand in (os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                 os.path.join(paths['heightmaps'], f"H{patch_id}.txt")):
        if os.path.exists(cand):
            txt_path = cand; break
    if not txt_path:
        return None
    terrain_file = os.path.join(paths['heightmaps'], "modified", f"h{patch_id}.obj")
    if not os.path.exists(terrain_file):
        terrain_file = os.path.join(paths['heightmaps'], f"h{patch_id}.obj")
    if not os.path.exists(terrain_file):
        return None
    try:
        meta = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
        terrain = load_terrain(terrain_file)
        root = ET.parse(osm_path).getroot()
    except Exception as e:
        logger.warning("transmitter: setup failed for %s: %s", patch_id, e)
        return None

    items = _parse_transmitters(root, projector)
    if not items:
        return None

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
    return items, _terrain_z, tmpl


def _copy_transmitter_texture(paths):
    """Copy transmitter.dds into the patch Textures folder (file mode)."""
    import shutil
    dest = os.path.join(paths['autogen'], "Textures")
    src = os.path.join(_ASSETS, TEX_FILE); dst = os.path.join(dest, TEX_FILE)
    if os.path.exists(src) and not os.path.exists(dst):
        os.makedirs(dest, exist_ok=True)
        try: shutil.copy2(src, dst)
        except Exception: pass


def add_filemode_groups(groups, props, paths, patch_id):
    """File mode: the transmitter is written AFTER the OBJ is exported (via the wrapped
    exporter, see _patch_obj_exporter), so nothing is added to `groups` here - we only
    make sure the texture is copied next to the patch."""
    if not getattr(bpy.context.scene, "condor_transmitter_batch", False):
        return
    _copy_transmitter_texture(paths)


def _shift_obj_face(line, dv, dvt, dvn):
    """Add offsets to every index of an OBJ 'f' line - used to renumber the pylones
    block after inserting the transmitter before it. Non-face lines pass through."""
    if not line.startswith("f "):
        return line
    toks = []
    for tok in line.split()[1:]:
        bits = tok.split("/")
        v = str(int(bits[0]) + dv) if bits[0] else ""
        if len(bits) == 1:
            toks.append(v)
        elif len(bits) == 2:
            t = str(int(bits[1]) + dvt) if bits[1] else ""
            toks.append(f"{v}/{t}")
        else:
            t = str(int(bits[1]) + dvt) if bits[1] else ""
            n = str(int(bits[2]) + dvn) if bits[2] else ""
            toks.append(f"{v}/{t}/{n}")
    return "f " + " ".join(toks)


def _append_transmitter_material(obj_path, texture_prefix):
    """Append the 'condor_transmitter' material to the patch .mtl (same block shape the
    exporter writes), pointing at transmitter.dds."""
    from ..config import (CONDOR_MTL_KA, CONDOR_MTL_KD, CONDOR_MTL_KS,
                          CONDOR_MTL_NS, CONDOR_MTL_D, CONDOR_MTL_ILLUM)
    mtl_path = os.path.splitext(obj_path)[0] + ".mtl"
    if not os.path.exists(mtl_path):
        return
    if f"newmtl {MAT_NAME}" in open(mtl_path, "r", encoding="utf-8").read():
        return
    with open(mtl_path, "a", encoding="utf-8") as mf:
        mf.write(f"\nnewmtl {MAT_NAME}\n")
        mf.write("Ka {:.6f} {:.6f} {:.6f}\n".format(*CONDOR_MTL_KA))
        mf.write("Kd {:.6f} {:.6f} {:.6f}\n".format(*CONDOR_MTL_KD))
        mf.write("Ks {:.6f} {:.6f} {:.6f}\n".format(*CONDOR_MTL_KS))
        mf.write(f"Ns {CONDOR_MTL_NS:.6f}\n")
        mf.write(f"d {CONDOR_MTL_D:.6f}\n")
        mf.write(f"illum {CONDOR_MTL_ILLUM}\n")
        mf.write(f"map_Kd {texture_prefix}{TEX_FILE}\n")


def _write_transmitter_into_obj(obj_path, props, paths, patch_id):
    """Insert the merged 'transmitter' object into o<patch>.obj (material into the .mtl)
    KEEPING the model's own normals (authored flip / duplicate faces stay intact - NO
    recompute). Placed right before 'pylones' so pylones stays the last object.
    Buildings and the exporter are untouched."""
    from ..config import CONDOR_AXIS_SWAP, CONDOR_TEXTURE_PREFIX
    from ..io.obj_exporter import _condor_xform
    setup = _transmitter_setup(props, paths, patch_id)
    if setup is None:
        return
    items, _terrain_z, tmpl = setup

    v_lines, vt_lines, vn_lines, raw_faces = [], [], [], []
    for (cx, cy, h, has_h, model) in items:
        t = tmpl.get(model)
        if t is None:
            continue
        verts, uvs, norms, faces, minz, nativ = t
        s = (h / nativ) if (has_h and nativ > 0) else 1.0
        foot = _terrain_z(cx, cy)
        vbase, vtbase, vnbase = len(v_lines), len(vt_lines), len(vn_lines)
        for (vx, vy, vz) in verts:
            wx, wy, wz = _condor_xform((cx + s * vx, cy + s * vy, foot + s * vz), CONDOR_AXIS_SWAP)
            v_lines.append("v %.6f %.6f %.6f" % (wx, wy, wz))
        for (u, v) in uvs:
            vt_lines.append("vt %.6f %.6f" % (u, v))
        for (nx, ny, nz) in norms:
            dx, dy, dz = _condor_xform((nx, ny, nz), CONDOR_AXIS_SWAP)
            L = (dx * dx + dy * dy + dz * dz) ** 0.5 or 1.0
            vn_lines.append("vn %.6f %.6f %.6f" % (dx / L, dy / L, dz / L))
        for (vi, ti, ni) in faces:
            if len(vi) < 3:
                continue
            for k in range(1, len(vi) - 1):     # fan-triangulate, keep original normals
                raw_faces.append([
                    (vbase + vi[p] + 1,
                     (vtbase + ti[p] + 1) if ti is not None else None,
                     (vnbase + ni[p] + 1) if ni is not None else None)
                    for p in (0, k, k + 1)])
    if not v_lines:
        return

    lines = open(obj_path, "r", encoding="utf-8").read().split("\n")
    pyidx = next((i for i, l in enumerate(lines) if l.strip() == "o pylones"), None)
    insert_at = pyidx if pyidx is not None else len(lines)
    base_v = sum(1 for l in lines[:insert_at] if l.startswith("v "))
    base_vt = sum(1 for l in lines[:insert_at] if l.startswith("vt "))
    base_vn = sum(1 for l in lines[:insert_at] if l.startswith("vn "))

    f_lines = []
    for tri in raw_faces:
        parts = []
        for (vg, tg, ng) in tri:
            vs = str(vg + base_v)
            if tg is not None and ng is not None:
                parts.append(f"{vs}/{tg + base_vt}/{ng + base_vn}")
            elif tg is not None:
                parts.append(f"{vs}/{tg + base_vt}")
            elif ng is not None:
                parts.append(f"{vs}//{ng + base_vn}")
            else:
                parts.append(vs)
        f_lines.append("f " + " ".join(parts))

    block = ["", f"o {GROUP_NAME}", f"usemtl {MAT_NAME}"] + v_lines + vt_lines + vn_lines + f_lines
    nv, nvt, nvn = len(v_lines), len(vt_lines), len(vn_lines)
    tail = lines[insert_at:]
    if pyidx is not None:
        tail = [_shift_obj_face(l, nv, nvt, nvn) for l in tail]
    open(obj_path, "w", encoding="utf-8").write("\n".join(lines[:insert_at] + block + tail))
    _append_transmitter_material(obj_path, CONDOR_TEXTURE_PREFIX)


def _append_transmitter_after_export(obj_filepath):
    """Called from the wrapped exporter: if this is a file-mode patch OBJ and the
    transmitter Batch is on, write the transmitter into it (with its own normals)."""
    import re
    if not getattr(bpy.context.scene, "condor_transmitter_batch", False):
        return
    m = re.match(r'^o(\d{6})(_LOD1)?\.obj$', os.path.basename(obj_filepath))
    if not m:
        return
    patch_id = m.group(1)
    props = bpy.context.scene.condor_buildings
    from .operators import resolve_condor_paths
    paths = resolve_condor_paths(props)
    if not paths:
        return
    _write_transmitter_into_obj(obj_filepath, props, paths, patch_id)


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
                # both sizes get the same shared material + texture
                mat = _get_transmitter_material()
                ob.data.materials.clear()
                ob.data.materials.append(mat)

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
    bl_description = "Merge Transmitter_* objects per patch into one 'transmitter' object"
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

        # group by Condor collection -> ONE merged 'transmitter' object per patch
        groups = {}
        for o in objs:
            col = self._condor_collection(o)
            groups.setdefault(col.name if col else None, []).append(o)

        for col_name, members in groups.items():
            bpy.ops.object.select_all(action='DESELECT')
            for o in members:
                o.select_set(True)
            context.view_layer.objects.active = members[0]
            bpy.ops.object.join()
            merged = context.active_object
            merged.name = "transmitter"
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            mat = bpy.data.materials.get(MAT_NAME)
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
            if _re.match(r'^condor_transmitter(_big|_small)?\.\d+$', mat.name):
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


def _patch_obj_exporter():
    """Wrap obj_exporter.export_condor_obj_mtl so that AFTER the exporter writes the
    patch OBJ, the transmitter is written into it with its OWN normals (file mode).
    Kept here so the whole feature lives in one file; the exporter/buildings source is
    NOT edited, and removing this module restores the original behaviour."""
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_transmitter_export_patched", False):
        return
    _orig = _oe.export_condor_obj_mtl

    def _patched(groups, obj_filepath, texture_map, *a, **k):
        stats = _orig(groups, obj_filepath, texture_map, *a, **k)
        try:
            _append_transmitter_after_export(obj_filepath)
        except Exception as e:
            print(f"[transmitter] file-mode append failed: {e}")
        return stats

    _oe._transmitter_orig_export = _orig
    _oe.export_condor_obj_mtl = _patched
    _oe._transmitter_export_patched = True


def _unpatch_obj_exporter():
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_transmitter_export_patched", False):
        _oe.export_condor_obj_mtl = _oe._transmitter_orig_export
        _oe._transmitter_export_patched = False


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
        config.TEXTURE_MAP.setdefault(MAT_NAME, TEX_FILE)
        # object 'transmitter' uses material 'condor_transmitter' (like aerialway->pylones)
        config.MATERIAL_ALIAS.setdefault(GROUP_NAME, MAT_NAME)
    except Exception:
        pass
    # make the OSM download fetch communication masts/towers (kept in this module)
    try:
        _patch_overpass_query()
    except Exception:
        pass
    # write the transmitter into the OBJ after export, with its own normals (file mode)
    try:
        _patch_obj_exporter()
    except Exception:
        pass


def unregister():
    try:
        _unpatch_overpass_query()
    except Exception:
        pass
    try:
        _unpatch_obj_exporter()
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
        config.TEXTURE_MAP.pop(MAT_NAME, None)
        config.MATERIAL_ALIAS.pop(GROUP_NAME, None)
    except Exception:
        pass
