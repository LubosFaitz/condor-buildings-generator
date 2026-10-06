"""
Water towers - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Transmitter feature (Import / file-mode Batch) but for OSM water towers.
Everything lives in THIS one file plus a few tiny, clearly-marked, try/except-guarded
hooks in:
  - __init__.py            (register/unregister this module)
  - blender/panels.py      (draw the Water tower row inside "Other objects")
  - blender/batch_processing.py (add water towers to the file-mode OBJ on Batch)

Delete this file (or comment out those guarded hooks) and the plugin falls back
to exactly the previous behaviour - nothing else depends on it.

OSM detection (the whole core):
  man_made=water_tower (node, or way -> its centroid).
  height=* gives the real height.

Model (assets/3Dobjects):
  watertower.obj (LOD0) and watertowerLOD1.obj (LOD1), the SAME material + texture as
  the transmitter: condor_transmitter / transmitter.dds. Foot at the local origin (the
  shaft goes 0.3 m below it on purpose), top at z=30 m. With a height tag the WHOLE model is scaled uniformly so its top above
  the foot equals the OSM height; without it the tower stays 30 m. The foot is kept on
  the terrain. The model is never rotated, so there is no Merge button: Import itself
  joins all water towers of a patch (per LOD) into ONE 'watertower' object.
"""

import os
import re
import logging

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "3Dobjects")

OBJ_FILE = "watertower.obj"
OBJ_FILE_LOD1 = "watertowerLOD1.obj"
GROUP_NAME = "watertower"         # object name in the OBJ (like 'pylones', 'transmitter')
# shared with the transmitter (blender/transmitters.py): same material + texture
MAT_NAME = "condor_transmitter"   # material name (object 'watertower' -> material via alias)
TEX_FILE = "transmitter.dds"


def _obj_file(lod1):
    """Model file for the given LOD (watertowerLOD1.obj for LOD1, else watertower.obj)."""
    return OBJ_FILE_LOD1 if lod1 else OBJ_FILE


def _get_watertower_material():
    """Return the single shared 'condor_transmitter' material (image transmitter.dds),
    created once with an image-texture node so every water tower looks identical."""
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


def _is_water_tower(tags):
    """OSM water tower: man_made=water_tower."""
    return tags.get("man_made") == "water_tower"


def _parse_water_towers(root, projector, patch_id=None, heightmaps=None):
    """Return list of (x, y, height, has_height) from an OSM tree."""
    from .operators import _parse_height_str, _point_in_polygon

    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}

    out = []
    for n in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        if _is_water_tower(tags):
            has_h = "height" in tags
            h = _parse_height_str(tags.get("height", "0"))
            x, y = projector.project(float(n.get("lat")), float(n.get("lon")))
            out.append((x, y, h, has_h))

    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if not _is_water_tower(tags):
            continue
        coords = [node_coords[nd.get("ref")] for nd in w.findall("nd")
                  if nd.get("ref") in node_coords]
        if not coords:
            continue
        poly = [projector.project(la, lo) for la, lo in coords]
        if any(_point_in_polygon(px, py, poly) for px, py, *_ in out):
            continue  # a node water tower already sits inside this way
        la = sum(c[0] for c in coords) / len(coords)
        lo = sum(c[1] for c in coords) / len(coords)
        has_h = "height" in tags
        h = _parse_height_str(tags.get("height", "0"))
        x, y = projector.project(la, lo)
        out.append((x, y, h, has_h))
    # Only what stands inside the patch (like the buildings) - one outside it is built
    # by the neighbouring patch it stands in.
    # Exactly ON the border: the left/bottom edge belongs to this patch, the right/top
    # edge to the neighbour there (same half-open border as the fences), so a tower on
    # the line is built ONCE. If that neighbour is not in the landscape (no heightmap),
    # this patch keeps it, so a tower on the landscape's outer edge is not lost.
    # Layout: +X neighbour = x number - 1, +Y neighbour = y number + 1 (see fences.py).
    from ..config import PATCH_HALF

    def _nb_missing(dx, dy):
        if not (patch_id and heightmaps):
            return True
        try:
            nid = f"{int(patch_id[:3]) + dx:03d}{int(patch_id[3:]) + dy:03d}"
        except ValueError:
            return True
        return not any(os.path.exists(os.path.join(heightmaps, f"{c}{nid}.txt"))
                       for c in ("h", "H"))

    def _owns(v, dx, dy):
        if -PATCH_HALF <= v < PATCH_HALF:
            return True
        return v == PATCH_HALF and _nb_missing(dx, dy)

    return [o for o in out if _owns(o[0], -1, 0) and _owns(o[1], 0, 1)]


def _tower_scale(h, has_h, top):
    """Uniform scale so the model's top above its foot (top = max z, foot at z=0)
    equals the OSM height. No (usable) height tag -> 1.0 (the native 30 m tower)."""
    if has_h and h > 0 and top > 0:
        return h / top
    return 1.0


# ----------------------------------------------------------------------------
# OBJ template loader (verts/uvs/normals/faces) for the file-mode build.
# ----------------------------------------------------------------------------
def _load_obj(path):
    # Reads v / vt / vn / f. Normals (vn) are kept so the file-mode writer reuses the
    # model's OWN (smooth) normals - no recompute.
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
    # height above the foot (z=0); NOT max-min, the shaft goes below the foot on purpose
    top = max(v[2] for v in verts) if verts else 0.0
    return verts, uvs, norms, faces, top


def _watertower_setup(props, paths, patch_id, lod1=False):
    """Shared file-mode setup: OSM water towers + a terrain-height function + the asset
    template (verts, uvs, NORMALS, faces, top) of the given LOD's model.
    Returns (items, terrain_z_fn, tmpl) or None."""
    import xml.etree.ElementTree as ET
    from ..projection.transverse_mercator import TransverseMercatorProjector
    from ..io.patch_metadata import load_patch_metadata
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
    from .terrain_smooth import load_terrain_smoothed, resolve_smooth_or_source
    terrain_file = resolve_smooth_or_source(paths['heightmaps'], patch_id)
    if not os.path.exists(terrain_file):
        return None
    try:
        meta = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
        terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
        root = ET.parse(osm_path).getroot()
    except Exception as e:
        logger.warning("watertower: setup failed for %s: %s", patch_id, e)
        return None

    items = _parse_water_towers(root, projector, patch_id, paths['heightmaps'])
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

    p = os.path.join(_ASSETS, _obj_file(lod1))
    tmpl = _load_obj(p) if os.path.exists(p) else None
    if tmpl is None:
        return None
    return items, _terrain_z, tmpl


def _copy_watertower_texture(paths):
    """Copy transmitter.dds into the patch Textures folder (file mode)."""
    import shutil
    dest = os.path.join(paths['autogen'], "Textures")
    src = os.path.join(_ASSETS, TEX_FILE); dst = os.path.join(dest, TEX_FILE)
    if os.path.exists(src) and not os.path.exists(dst):
        os.makedirs(dest, exist_ok=True)
        try: shutil.copy2(src, dst)
        except Exception: pass


def add_filemode_groups(groups, props, paths, patch_id):
    """File mode: the water tower is written AFTER the OBJ is exported (via the wrapped
    exporter, see _patch_obj_exporter), so nothing is added to `groups` here - we only
    make sure the texture is copied next to the patch."""
    if not getattr(bpy.context.scene, "condor_watertower_batch", False):
        return
    _copy_watertower_texture(paths)


def _shift_obj_face(line, dv, dvt, dvn):
    """Add offsets to every index of an OBJ 'f' line - used to renumber the pylones
    block after inserting the water tower before it. Non-face lines pass through."""
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


def _append_watertower_material(obj_path, texture_prefix):
    """Append the 'condor_transmitter' material to the patch .mtl (same block shape the
    exporter writes), pointing at transmitter.dds - skipped when the transmitter already
    put it there."""
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


def _write_watertower_into_obj(obj_path, props, paths, patch_id, lod1=False):
    """Insert the merged 'watertower' object into o<patch>.obj (material into the .mtl)
    KEEPING the model's own (smooth) normals - NO recompute. Placed right before
    'pylones' so pylones stays the last object. Buildings and the exporter are untouched.
    o<patch>_LOD1.obj gets the LOD1 model (watertowerLOD1.obj)."""
    from ..config import CONDOR_AXIS_SWAP, CONDOR_TEXTURE_PREFIX
    from ..io.obj_exporter import _condor_xform
    setup = _watertower_setup(props, paths, patch_id, lod1)
    if setup is None:
        return
    items, _terrain_z, tmpl = setup
    verts, uvs, norms, faces, top = tmpl

    v_lines, vt_lines, vn_lines, raw_faces = [], [], [], []
    for (cx, cy, h, has_h) in items:
        s = _tower_scale(h, has_h, top)
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
    _append_watertower_material(obj_path, CONDOR_TEXTURE_PREFIX)


def _append_watertower_after_export(obj_filepath):
    """Called from the wrapped exporter: if this is a file-mode patch OBJ and the
    water tower Batch is on, write the water towers into it (with their own normals)."""
    if not getattr(bpy.context.scene, "condor_watertower_batch", False):
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
    _write_watertower_into_obj(obj_filepath, props, paths, patch_id, bool(m.group(2)))


# ----------------------------------------------------------------------------
# Blender mode helpers.
# ----------------------------------------------------------------------------
def _ensure_custom_normals(me):
    """Keep the model's smooth normals as CUSTOM normals, so Export OBJ writes them
    (mesh_converter reads normals only when mesh.has_custom_normals). The OBJ import
    already brings them from the file's vn; this is only a fallback in case it did not."""
    try:
        if hasattr(me, "use_auto_smooth"):        # Blender < 4.1: custom normals need it
            me.use_auto_smooth = True
        if getattr(me, "has_custom_normals", False):
            return
        if hasattr(me, "calc_normals_split"):     # Blender < 4.1
            me.auto_smooth_angle = 3.14159
            me.calc_normals_split()
            nors = [tuple(l.normal) for l in me.loops]
        else:                                     # Blender 4.1+
            nors = [tuple(n.vector) for n in me.corner_normals]
        me.normals_split_custom_set(nors)
    except Exception:
        pass


def _join_objects(context, members):
    """Join `members` into one object and return it (a single member is returned as is)."""
    if len(members) == 1:
        return members[0]
    bpy.ops.object.select_all(action='DESELECT')
    for o in members:
        o.select_set(True)
    context.view_layer.objects.active = members[0]
    bpy.ops.object.join()
    return context.active_object


# ----------------------------------------------------------------------------
# Operator: Import (also joins the water towers per patch - no Merge button).
# ----------------------------------------------------------------------------
class CONDOR_OT_import_water_towers(Operator):
    bl_idname = "condor.import_water_towers"
    bl_label = "Import Water Towers"
    bl_description = ("Import water towers (man_made=water_tower), merged per patch "
                      "into one 'watertower' object")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return (props.condor_path and props.landscape_name != 'NONE'
                and not getattr(context.scene, "condor_watertower_batch", False))

    def execute(self, context):
        import xml.etree.ElementTree as ET
        import shutil as _shutil
        from mathutils import Matrix
        from .operators import resolve_condor_paths, ensure_patch_osm, _extra_obj_type
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        for lod1 in ((False, True) if props.output_lod in ('LOD1', 'BOTH') else (False,)):
            obj_asset = os.path.join(_ASSETS, _obj_file(lod1))
            if not os.path.exists(obj_asset):
                self.report({'ERROR'}, f"Model not found: {obj_asset}")
                return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        tex_dest = os.path.join(paths['autogen'], "Textures")

        patch_ids = []
        if props.single_patch_mode and props.patch_id:
            patch_ids = [str(props.patch_id)]
        else:
            for x in range(props.patch_x_min, props.patch_x_max + 1):
                for y in range(props.patch_y_min, props.patch_y_max + 1):
                    patch_ids.append(f"{x:03d}{y:03d}")

        # the shared material exists BEFORE the first OBJ import, so the importer's own
        # copies become condor_transmitter.NNN and are removed at the end
        mat = _get_watertower_material()

        total = 0
        skipped_existing = 0
        missing_osm = []
        for patch_id in patch_ids:
            osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
            if not ensure_patch_osm(paths, patch_id, True):
                missing_osm.append(patch_id)
                continue
            txt_path = next((p for p in (
                os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
            if not txt_path:
                continue
            from .terrain_smooth import resolve_smooth_or_source
            terrain_file = resolve_smooth_or_source(paths['heightmaps'], patch_id)
            if not os.path.exists(terrain_file):
                continue
            try:
                meta = load_patch_metadata(txt_path)
                projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
                root = ET.parse(osm_path).getroot()
            except Exception:
                continue

            items = _parse_water_towers(root, projector, patch_id, paths['heightmaps'])
            if not items:
                continue

            # Which LODs are requested, and which are NOT yet imported for this patch.
            # Already-imported LODs are left as they are (no re-import / duplicate); only
            # the missing LOD(s) get built. Different LOD = different object -> allowed.
            req_lods = []
            if props.output_lod in ('LOD0', 'BOTH'):
                req_lods.append("")
            if props.output_lod in ('LOD1', 'BOTH'):
                req_lods.append("_LOD1")
            if not req_lods:
                req_lods = [""]
            # a LOD counts as done if its patch collection already has a water tower
            # (separately imported OR baked into the patch OBJ)
            missing_lods = []
            for s in req_lods:
                _pcol = bpy.data.collections.get(f"Condor_{props.landscape_name}_{patch_id}{s}")
                has = _pcol is not None and any(_extra_obj_type(o.name) == "watertower"
                                                for o in _pcol.all_objects)
                if not has:
                    missing_lods.append(s)
            if not missing_lods:
                skipped_existing += 1
                continue

            # copy the texture once we know there's something
            s_tex = os.path.join(_ASSETS, TEX_FILE); d_tex = os.path.join(tex_dest, TEX_FILE)
            if os.path.exists(s_tex) and not os.path.exists(d_tex):
                os.makedirs(tex_dest, exist_ok=True)
                try: _shutil.copy2(s_tex, d_tex)
                except Exception: pass

            from .terrain_smooth import scene_terrain_object
            terrain_obj = scene_terrain_object(patch_id)
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
                from .terrain_smooth import load_terrain_smoothed
                try: terrain_mesh = load_terrain_smoothed(paths['heightmaps'], patch_id)
                except Exception: terrain_mesh = None

            # each missing LOD is built from ITS OWN model (watertowerLOD1.obj for LOD1)
            placed_by_lod = {s: [] for s in missing_lods}
            for suffix, (cx, cy, h, has_h) in [(s, it) for s in missing_lods for it in items]:
                obj_asset = os.path.join(_ASSETS, _obj_file(suffix == "_LOD1"))
                if hasattr(bpy.ops.wm, 'obj_import'):
                    bpy.ops.wm.obj_import(filepath=obj_asset, forward_axis='Y', up_axis='Z')
                else:
                    bpy.ops.import_scene.obj(filepath=obj_asset, axis_forward='Y', axis_up='Z')
                imported = context.selected_objects
                if not imported:
                    continue
                ob = imported[0]
                me = ob.data
                me.materials.clear()
                me.materials.append(mat)

                # whatever transform the importer put on the object goes into the mesh,
                # so the object itself stays at identity
                me.transform(ob.matrix_basis)
                ob.matrix_basis = Matrix.Identity(4)

                # height above the foot (z=0); NOT max-min, the shaft goes below on purpose
                top = max((v.co.z for v in me.vertices), default=0.0)
                s = _tower_scale(h, has_h, top)  # uniform - keep shape, don't deform

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
                            from .terrain_smooth import load_terrain_smoothed
                            try: terrain_mesh = load_terrain_smoothed(paths['heightmaps'], patch_id)
                            except Exception: terrain_mesh = None
                if not got_foot and terrain_mesh:
                    from ..models.geometry import Point2D, BBox
                    bb = BBox(cx - 1, cy - 1, cx + 1, cy + 1)
                    for ti in terrain_mesh.get_triangles_in_bbox(bb):
                        tri = terrain_mesh.triangles[ti]
                        if tri.contains_point_2d(Point2D(cx, cy)):
                            z = tri.z_at_xy(cx, cy)
                            if z is not None: foot_z = z; break

                # scale + place in PATCH coordinates, baked into the mesh: the model's
                # foot (local 0,0,0) sits on the terrain
                me.transform(Matrix.Translation((cx, cy, foot_z)) @ Matrix.Scale(s, 4))
                _ensure_custom_normals(me)

                # keep it in the scene's master collection until it is joined (always in
                # the view layer, so the join works whatever collection was active)
                for c in list(ob.users_collection):
                    c.objects.unlink(ob)
                if ob.name not in context.scene.collection.objects:
                    context.scene.collection.objects.link(ob)
                placed_by_lod[suffix].append(ob)
                if suffix == missing_lods[0]:
                    total += 1

            if terrain_orig is not None and terrain_obj:
                terrain_obj.location = terrain_orig

            # one set of objects per missing LOD
            sets = [(s, placed_by_lod[s]) for s in missing_lods if placed_by_lod[s]]

            for suffix, members in sets:
                merged = _join_objects(context, members)
                merged.name = GROUP_NAME
                merged["patch_id"] = patch_id
                merged["lod"] = suffix
                merged.data.materials.clear()
                merged.data.materials.append(mat)
                _ensure_custom_normals(merged.data)

                col_name = f"Condor_{props.landscape_name}_{patch_id}{suffix}"
                col = bpy.data.collections.get(col_name) or bpy.data.collections.new(col_name)
                if col not in context.scene.collection.children_recursive:
                    try: context.scene.collection.children.link(col)
                    except Exception: pass
                for c in list(merged.users_collection):
                    c.objects.unlink(merged)
                if merged.name in context.scene.collection.objects:
                    context.scene.collection.objects.unlink(merged)
                col.objects.link(merged)
                # mesh is in patch coordinates; the object carries the patch offset
                # (same layout as the buildings / bridges import)
                merged.location = (off_x, off_y, 0.0)

        # duplicate materials left by the OBJ import (condor_transmitter.001, ...)
        for m in list(bpy.data.materials):
            if re.match(r'^condor_transmitter\.\d+$', m.name) and m.users == 0:
                bpy.data.materials.remove(m)

        msg = f"Imported {total} water towers"
        if skipped_existing:
            msg += f", {skipped_existing} patch(es) already imported (skipped)"
        if missing_osm:
            msg += f" | OSM missing (skipped): {', '.join(missing_osm)}"
        self.report({'WARNING'} if missing_osm else {'INFO'}, msg)
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Panel row (called from panels.py inside the "Other objects" box).
# ----------------------------------------------------------------------------
def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Water tower", icon='MOD_FLUID')
    row.prop(context.scene, "condor_watertower_batch", text="Batch")
    row = box.row(align=True)
    row.operator("condor.import_water_towers", text="Import", icon='IMPORT')


# ----------------------------------------------------------------------------
# Registration (operator + scene property + TEXTURE_MAP entries).
# ----------------------------------------------------------------------------
_classes = [CONDOR_OT_import_water_towers]


def _patch_overpass_query():
    """Wrap osm_downloader.build_overpass_query so the OSM download ALSO fetches
    water towers - kept here so the whole feature lives in one file.
    Removing this module restores the original query."""
    from . import osm_downloader as _osm
    if getattr(_osm, "_watertower_patched", False):
        return
    _orig = _osm.build_overpass_query

    def _patched(lat_min, lat_max, lon_min, lon_max, *a, **k):
        q = _orig(lat_min, lat_max, lon_min, lon_max, *a, **k)
        bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
        extra = (
            f'  node["man_made"="water_tower"]({bbox});\n'
            f'  way["man_made"="water_tower"]({bbox});'
        )
        return q.replace("\n);", "\n" + extra + "\n);", 1)

    _osm._watertower_orig_query = _orig
    _osm.build_overpass_query = _patched
    _osm._watertower_patched = True


def _unpatch_overpass_query():
    from . import osm_downloader as _osm
    if getattr(_osm, "_watertower_patched", False):
        _osm.build_overpass_query = _osm._watertower_orig_query
        _osm._watertower_patched = False


def _patch_obj_exporter():
    """Wrap obj_exporter.export_condor_obj_mtl so that AFTER the exporter writes the
    patch OBJ, the water towers are written into it with their OWN normals (file mode).
    Kept here so the whole feature lives in one file; the exporter/buildings source is
    NOT edited, and removing this module restores the original behaviour."""
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_watertower_export_patched", False):
        return
    _orig = _oe.export_condor_obj_mtl

    def _patched(groups, obj_filepath, texture_map, *a, **k):
        stats = _orig(groups, obj_filepath, texture_map, *a, **k)
        try:
            _append_watertower_after_export(obj_filepath)
        except Exception as e:
            print(f"[watertower] file-mode append failed: {e}")
        return stats

    _oe._watertower_orig_export = _orig
    _oe.export_condor_obj_mtl = _patched
    _oe._watertower_export_patched = True


def _unpatch_obj_exporter():
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_watertower_export_patched", False):
        _oe.export_condor_obj_mtl = _oe._watertower_orig_export
        _oe._watertower_export_patched = False


def register():
    from bpy.props import BoolProperty
    bpy.types.Scene.condor_watertower_batch = BoolProperty(
        name="Batch",
        description=("File mode (Import to Blender off): after generating the OBJ, "
                     "also generate water towers and add them as one watertower "
                     "object. Off by default"),
        default=False,
    )
    for c in _classes:
        bpy.utils.register_class(c)
    # add the texture to the export map so file-mode MTL gets it
    try:
        from .. import config
        config.TEXTURE_MAP.setdefault(MAT_NAME, TEX_FILE)
        # object 'watertower' uses material 'condor_transmitter' (like aerialway->pylones)
        config.MATERIAL_ALIAS.setdefault(GROUP_NAME, MAT_NAME)
    except Exception:
        pass
    # make the OSM download fetch water towers (kept in this module)
    try:
        _patch_overpass_query()
    except Exception:
        pass
    # write the water towers into the OBJ after export, with their own normals (file mode)
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
        del bpy.types.Scene.condor_watertower_batch
    except Exception:
        pass
    try:
        from .. import config
        # TEXTURE_MAP[condor_transmitter] is shared with the transmitter - left alone
        config.MATERIAL_ALIAS.pop(GROUP_NAME, None)
    except Exception:
        pass
