"""
Silos - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Water tower feature (Import / file-mode Batch) but for OSM grain / industrial
silos, plus a Merge button like the wind turbines (each silo is its own object with its
origin at the foot, so it can be turned by hand before merging).
Everything lives in THIS one file plus a few tiny, clearly-marked, try/except-guarded
hooks in:
  - __init__.py            (register/unregister this module)
  - blender/panels.py      (draw the Silo row inside "Other objects")
  - blender/batch_processing.py (add silos to the file-mode OBJ on Batch)
  - io/osm_parser.py       (a silo outline is not generated as a building)
  - main.py                (a building under a silo is not generated)

Delete this file (or comment out those guarded hooks) and the plugin falls back
to exactly the previous behaviour - nothing else depends on it.

OSM detection (the whole core):
  man_made=silo or building=silo, NOT silo=bunker / silo=bag (flat ground silos).
  Outline (way / multipolygon) -> the model is turned along the LONGER side of the
  outline's smallest rectangle and stretched to its length and width.
  Point (node) -> the model at its own size, facing north (turned by hand).
  height=* gives the real height, without it the model stays 26.8 m.

Model (assets/3Dobjects):
  silos.obj - ONE model for LOD0 and LOD1, the SAME material + texture as the
  transmitter: condor_transmitter / transmitter.dds. Long side along Y, foot at z=0.
  The model's OWN (smooth) normals are kept on import, on Merge and in the file-mode
  OBJ - only turned / stretched with the model, never recomputed.
"""

import os
import re
import math
import logging

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "3Dobjects")

OBJ_FILE = "silos.obj"            # one model for LOD0 and LOD1
GROUP_NAME = "silo"               # merged object name (like 'watertower', 'transmitter')
FRESH_PREFIX = "Silo_"            # freshly imported, not merged yet: Silo_<patch>[_LOD1]_NNN
# shared with the transmitter (blender/transmitters.py): same material + texture
MAT_NAME = "condor_transmitter"   # material name (object 'silo' -> material via alias)
TEX_FILE = "transmitter.dds"


def _get_silo_material():
    """Return the single shared 'condor_transmitter' material (image transmitter.dds),
    created once with an image-texture node so every silo looks identical."""
    mat = bpy.data.materials.get(MAT_NAME)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
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


def _is_silo(tags):
    """OSM grain / industrial silo: man_made=silo or building=silo, but not the flat
    ground ones (silo=bunker / silo=bag)."""
    return ((tags.get("man_made") == "silo" or tags.get("building") == "silo")
            and tags.get("silo") not in ("bunker", "bag"))


# ----------------------------------------------------------------------------
# Outline -> smallest rectangle (centre, width, length, turn angle).
# ----------------------------------------------------------------------------
def _convex_hull(pts):
    """Convex hull (counter-clockwise) of 2D points - monotone chain."""
    p = sorted(set(pts))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(p):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def _min_rect(pts):
    """Smallest rectangle around the outline: (cx, cy, width, length, angle).
    angle turns the model's long side (Y) onto the rectangle's LONGER side."""
    hull = _convex_hull(pts)
    if len(hull) < 3:
        return None
    best = None
    n = len(hull)
    for i in range(n):
        ex = hull[(i + 1) % n][0] - hull[i][0]
        ey = hull[(i + 1) % n][1] - hull[i][1]
        L = math.hypot(ex, ey)
        if L < 1e-9:
            continue
        ux, uy = ex / L, ey / L
        vx, vy = -uy, ux
        us = [px * ux + py * uy for px, py in hull]
        vs = [px * vx + py * vy for px, py in hull]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            best = (area, ux, uy, vx, vy, min(us), max(us), min(vs), max(vs))
    if best is None:
        return None
    _, ux, uy, vx, vy, u0, u1, v0, v1 = best
    um, vm = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    cx, cy = um * ux + vm * vx, um * uy + vm * vy
    du, dv = u1 - u0, v1 - v0
    if du >= dv:
        length, width, dx, dy = du, dv, ux, uy
    else:
        length, width, dx, dy = dv, du, vx, vy
    if width < 0.1 or length < 0.1:
        return None
    # the model's +Y turned by `angle` points along (dx, dy)
    return cx, cy, width, length, math.atan2(-dx, dy)


def _parse_silos(root, projector, patch_id=None, heightmaps=None):
    """Return list of (cx, cy, width, length, angle, height, has_height) from an OSM tree.
    width/length are None for a silo mapped only as a point (model at its own size)."""
    from .operators import _parse_height_str, _point_in_polygon

    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}
    way_refs = {w.get("id"): [nd.get("ref") for nd in w.findall("nd")]
                for w in root.findall("way")}

    def _ring(refs):
        return [projector.project(*node_coords[r]) for r in refs if r in node_coords]

    def _h(tags):
        return _parse_height_str(tags.get("height", "0")), "height" in tags

    out = []
    outlines = []
    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if not _is_silo(tags):
            continue
        poly = _ring(way_refs.get(w.get("id"), []))
        rect = _min_rect(poly) if len(poly) >= 3 else None
        if rect is None:
            continue
        h, has_h = _h(tags)
        out.append(rect + (h, has_h))
        outlines.append(poly)

    for r in root.findall("relation"):
        tags = {t.get("k"): t.get("v") for t in r.findall("tag")}
        if tags.get("type") != "multipolygon" or not _is_silo(tags):
            continue
        poly = []
        for m in r.findall("member"):
            if m.get("type") == "way" and m.get("role", "outer") in ("outer", ""):
                poly.extend(_ring(way_refs.get(m.get("ref"), [])))
        rect = _min_rect(poly) if len(poly) >= 3 else None
        if rect is None:
            continue
        h, has_h = _h(tags)
        out.append(rect + (h, has_h))
        outlines.append(poly)

    for n in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        if not _is_silo(tags):
            continue
        x, y = projector.project(float(n.get("lat")), float(n.get("lon")))
        if any(_point_in_polygon(x, y, poly) for poly in outlines):
            continue  # the same silo is also drawn as an outline -> the outline wins
        h, has_h = _h(tags)
        out.append((x, y, None, None, 0.0, h, has_h))

    # Only what stands inside the patch (like the buildings) - one outside it is built
    # by the neighbouring patch it stands in. The silo's CENTRE decides.
    # Exactly ON the border: the left/bottom edge belongs to this patch, the right/top
    # edge to the neighbour there (same half-open border as the water towers), so a silo
    # on the line is built ONCE. If that neighbour is not in the landscape (no
    # heightmap), this patch keeps it, so a silo on the landscape's outer edge is not lost.
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


# ----------------------------------------------------------------------------
# Placement of one silo (shared by Blender Import and the file-mode OBJ).
# ----------------------------------------------------------------------------
def _model_box(verts):
    """(centre x, centre y, width X, length Y, top) of the model; foot at z=0."""
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    top = max(v[2] for v in verts) if verts else 0.0
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
            max(xs) - min(xs), max(ys) - min(ys), top)


def _silo_scale(item, box):
    """Stretch (sx, sy, sz): outline -> its width / length, point -> own size;
    height=* -> that height, without it the model's own height."""
    _cx, _cy, w, l, _a, h, has_h = item
    _mcx, _mcy, mw, ml, top = box
    sx = (w / mw) if (w and mw > 0) else 1.0
    sy = (l / ml) if (l and ml > 0) else 1.0
    sz = (h / top) if (has_h and h > 0 and top > 0) else 1.0
    return sx, sy, sz


def _foot_points(item, box, scale):
    """Centre + the 4 corners of the placed silo - the foot goes to the LOWEST terrain
    under them (like the buildings), so no corner floats on a slope."""
    cx, cy, _w, _l, a = item[:5]
    hw, hl = box[2] * scale[0] / 2.0, box[3] * scale[1] / 2.0
    ca, sa = math.cos(a), math.sin(a)
    pts = [(cx, cy)]
    for px, py in ((-hw, -hl), (hw, -hl), (hw, hl), (-hw, hl)):
        pts.append((cx + ca * px - sa * py, cy + sa * px + ca * py))
    return pts


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
    return verts, uvs, norms, faces


def _silo_setup(props, paths, patch_id):
    """Shared file-mode setup: OSM silos + a terrain-height function + the model
    template (verts, uvs, NORMALS, faces). Returns (items, terrain_z_fn, tmpl) or None."""
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
        logger.warning("silo: setup failed for %s: %s", patch_id, e)
        return None

    items = _parse_silos(root, projector, patch_id, paths['heightmaps'])
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
        return None

    p = os.path.join(_ASSETS, OBJ_FILE)
    tmpl = _load_obj(p) if os.path.exists(p) else None
    if tmpl is None or not tmpl[0]:
        return None
    return items, _terrain_z, tmpl


def _copy_silo_texture(paths):
    """Copy transmitter.dds into the patch Textures folder (file mode)."""
    import shutil
    dest = os.path.join(paths['autogen'], "Textures")
    src = os.path.join(_ASSETS, TEX_FILE); dst = os.path.join(dest, TEX_FILE)
    if os.path.exists(src) and not os.path.exists(dst):
        os.makedirs(dest, exist_ok=True)
        try: shutil.copy2(src, dst)
        except Exception: pass


def add_filemode_groups(groups, props, paths, patch_id):
    """File mode: the silos are written AFTER the OBJ is exported (via the wrapped
    exporter, see _patch_obj_exporter), so nothing is added to `groups` here - we only
    make sure the texture is copied next to the patch."""
    if not getattr(bpy.context.scene, "condor_silo_batch", False):
        return
    _copy_silo_texture(paths)


def _shift_obj_face(line, dv, dvt, dvn):
    """Add offsets to every index of an OBJ 'f' line - used to renumber the pylones
    block after inserting the silos before it. Non-face lines pass through."""
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


def _append_silo_material(obj_path, texture_prefix):
    """Append the 'condor_transmitter' material to the patch .mtl (same block shape the
    exporter writes), pointing at transmitter.dds - skipped when the transmitter / water
    tower already put it there."""
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


def _write_silos_into_obj(obj_path, props, paths, patch_id):
    """Insert the merged 'silo' object into o<patch>.obj (material into the .mtl)
    KEEPING the model's own (smooth) normals - turned / stretched with the model, NO
    recompute. Placed right before 'pylones' so pylones stays the last object.
    Buildings and the exporter are untouched. LOD0 and LOD1 get the same model."""
    from ..config import CONDOR_AXIS_SWAP, CONDOR_TEXTURE_PREFIX
    from ..io.obj_exporter import _condor_xform
    setup = _silo_setup(props, paths, patch_id)
    if setup is None:
        return
    items, _terrain_z, tmpl = setup
    verts, uvs, norms, faces = tmpl
    box = _model_box(verts)
    mcx, mcy = box[0], box[1]

    v_lines, vt_lines, vn_lines, raw_faces = [], [], [], []
    for item in items:
        cx, cy, _w, _l, a = item[:5]
        sx, sy, sz = _silo_scale(item, box)
        zs = [z for z in (_terrain_z(px, py) for px, py in _foot_points(item, box, (sx, sy, sz)))
              if z is not None]
        foot = min(zs) if zs else 0.0
        ca, sa = math.cos(a), math.sin(a)
        vbase, vtbase, vnbase = len(v_lines), len(vt_lines), len(vn_lines)
        for (vx, vy, vz) in verts:
            lx, ly = sx * (vx - mcx), sy * (vy - mcy)
            wx, wy, wz = _condor_xform((cx + ca * lx - sa * ly, cy + sa * lx + ca * ly,
                                        foot + sz * vz), CONDOR_AXIS_SWAP)
            v_lines.append("v %.6f %.6f %.6f" % (wx, wy, wz))
        for (u, v) in uvs:
            vt_lines.append("vt %.6f %.6f" % (u, v))
        for (nx, ny, nz) in norms:
            # stretched model -> normal scaled by the INVERSE stretch, then turned
            lx, ly, lz = nx / sx, ny / sy, nz / sz
            dx, dy, dz = _condor_xform((ca * lx - sa * ly, sa * lx + ca * ly, lz), CONDOR_AXIS_SWAP)
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
    _append_silo_material(obj_path, CONDOR_TEXTURE_PREFIX)


def _append_silos_after_export(obj_filepath):
    """Called from the wrapped exporter: if this is a file-mode patch OBJ and the
    silo Batch is on, write the silos into it (with their own normals)."""
    if not getattr(bpy.context.scene, "condor_silo_batch", False):
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
    _write_silos_into_obj(obj_filepath, props, paths, patch_id)


# ----------------------------------------------------------------------------
# Blender mode helpers - the model's own normals as CUSTOM normals.
# ----------------------------------------------------------------------------
def _loop_normals(me):
    """The mesh's current per-corner normals (its custom = the model's own normals)."""
    if hasattr(me, "corner_normals"):             # Blender 4.1+
        return [tuple(n.vector) for n in me.corner_normals]
    if hasattr(me, "use_auto_smooth"):            # Blender < 4.1: custom normals need it
        me.use_auto_smooth = True
    me.calc_normals_split()
    return [tuple(l.normal) for l in me.loops]


def _set_normals(me, nors):
    """Store `nors` (one per corner) as the mesh's custom normals, so Export OBJ writes
    them (mesh_converter reads normals only when mesh.has_custom_normals)."""
    try:
        if hasattr(me, "use_auto_smooth"):        # Blender < 4.1
            me.use_auto_smooth = True
        me.normals_split_custom_set(nors)
    except Exception:
        pass


def _transform_keep_normals(me, matrix):
    """Move/turn/stretch the mesh by `matrix` and carry its own normals along (inverse-
    transpose), instead of letting Blender recompute them - the shading stays the model's."""
    from mathutils import Vector
    nors = _loop_normals(me)
    nm = matrix.to_3x3().inverted_safe().transposed()
    new = []
    for n in nors:
        v = nm @ Vector(n)
        L = v.length or 1.0
        new.append((v.x / L, v.y / L, v.z / L))
    me.transform(matrix)
    _set_normals(me, new)


# ----------------------------------------------------------------------------
# Operator: Import (each silo its own object, origin at the foot -> can be turned).
# ----------------------------------------------------------------------------
class CONDOR_OT_import_silos(Operator):
    bl_idname = "condor.import_silos"
    bl_label = "Import Silos"
    bl_description = ("Import silos (man_made=silo / building=silo) as separate objects "
                      "that can be turned around their foot, then Merge them")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return (props.condor_path and props.landscape_name != 'NONE'
                and not getattr(context.scene, "condor_silo_batch", False))

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

        obj_asset = os.path.join(_ASSETS, OBJ_FILE)
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

        # the shared material exists BEFORE the OBJ import, so the importer's own copy
        # becomes condor_transmitter.NNN and is removed at the end
        mat = _get_silo_material()

        # the model is read ONCE; every silo gets a copy of its mesh
        tmpl_me = None
        tmpl_ob = None
        total = 0
        skipped_existing = 0
        missing_osm = []
        try:
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

                items = _parse_silos(root, projector, patch_id, paths['heightmaps'])
                if not items:
                    continue

                # Which LODs are requested, and which are NOT yet imported for this patch.
                # Already-imported LODs are left as they are (no re-import / duplicate).
                req_lods = []
                if props.output_lod in ('LOD0', 'BOTH'):
                    req_lods.append("")
                if props.output_lod in ('LOD1', 'BOTH'):
                    req_lods.append("_LOD1")
                if not req_lods:
                    req_lods = [""]
                # a LOD counts as done if its patch collection already has a silo
                # (fresh, merged OR baked into the patch OBJ)
                missing_lods = []
                for s in req_lods:
                    _pcol = bpy.data.collections.get(f"Condor_{props.landscape_name}_{patch_id}{s}")
                    has = _pcol is not None and any(_extra_obj_type(o.name) == "silo"
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

                if tmpl_me is None:
                    if hasattr(bpy.ops.wm, 'obj_import'):
                        bpy.ops.wm.obj_import(filepath=obj_asset, forward_axis='Y', up_axis='Z')
                    else:
                        bpy.ops.import_scene.obj(filepath=obj_asset, axis_forward='Y', axis_up='Z')
                    imported = context.selected_objects
                    if not imported:
                        self.report({'ERROR'}, f"Model could not be imported: {obj_asset}")
                        return {'CANCELLED'}
                    tmpl_ob = imported[0]
                    tmpl_me = tmpl_ob.data
                    # whatever transform the importer put on the object goes into the mesh
                    _transform_keep_normals(tmpl_me, tmpl_ob.matrix_basis.copy())
                    tmpl_ob.matrix_basis = Matrix.Identity(4)
                    tmpl_me.materials.clear()
                    tmpl_me.materials.append(mat)
                    _tv = [tuple(v.co) for v in tmpl_me.vertices]
                    box = _model_box(_tv)

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

                def _z_at(x, y):
                    nonlocal terrain_mesh
                    if props.import_patch_terrain and terrain_obj:
                        try:
                            dg = context.evaluated_depsgraph_get()
                            hit, loc, _, _ = terrain_obj.evaluated_get(dg).ray_cast((x, y, 10000.0), (0, 0, -1))
                            if hit:
                                return loc.z
                        except Exception:
                            # terrain HIDDEN -> no evaluated mesh; fall back to file
                            if terrain_mesh is None:
                                from .terrain_smooth import load_terrain_smoothed
                                try: terrain_mesh = load_terrain_smoothed(paths['heightmaps'], patch_id)
                                except Exception: terrain_mesh = None
                    if terrain_mesh:
                        from ..models.geometry import Point2D, BBox
                        bb = BBox(x - 1, y - 1, x + 1, y + 1)
                        for ti in terrain_mesh.get_triangles_in_bbox(bb):
                            tri = terrain_mesh.triangles[ti]
                            if tri.contains_point_2d(Point2D(x, y)):
                                z = tri.z_at_xy(x, y)
                                if z is not None:
                                    return z
                    return None

                # size + height baked into the mesh (around its centre, foot at z=0);
                # the turn and the place stay on the OBJECT, so it can be turned by hand
                placed = []
                for item in items:
                    cx, cy, _w, _l, a = item[:5]
                    sx, sy, sz = _silo_scale(item, box)
                    zs = [z for z in (_z_at(qx, qy) for qx, qy in _foot_points(item, box, (sx, sy, sz)))
                          if z is not None]
                    foot_z = min(zs) if zs else 0.0
                    placed.append((cx, cy, foot_z, a, (sx, sy, sz)))

                if terrain_orig is not None and terrain_obj:
                    terrain_obj.location = terrain_orig

                for suffix in missing_lods:
                    col_name = f"Condor_{props.landscape_name}_{patch_id}{suffix}"
                    col = bpy.data.collections.get(col_name) or bpy.data.collections.new(col_name)
                    if col not in context.scene.collection.children_recursive:
                        try: context.scene.collection.children.link(col)
                        except Exception: pass
                    for idx, (cx, cy, foot_z, a, (sx, sy, sz)) in enumerate(placed):
                        me = tmpl_me.copy()
                        me.name = GROUP_NAME
                        _transform_keep_normals(
                            me, Matrix.Diagonal((sx, sy, sz, 1.0))
                            @ Matrix.Translation((-box[0], -box[1], 0.0)))
                        ob = bpy.data.objects.new(f"{FRESH_PREFIX}{patch_id}{suffix}_{idx + 1:03d}", me)
                        ob.location = (off_x + cx, off_y + cy, foot_z)
                        ob.rotation_euler = (0.0, 0.0, a)
                        ob["patch_id"] = patch_id
                        ob["lod"] = suffix
                        ob["patch_off"] = (off_x, off_y)
                        col.objects.link(ob)
                        if suffix == missing_lods[0]:
                            total += 1
        finally:
            if tmpl_ob is not None:
                bpy.data.objects.remove(tmpl_ob, do_unlink=True)
            if tmpl_me is not None and tmpl_me.users == 0:
                bpy.data.meshes.remove(tmpl_me)
            # duplicate materials left by the OBJ import (condor_transmitter.001, ...)
            for m in list(bpy.data.materials):
                if re.match(r'^condor_transmitter\.\d+$', m.name) and m.users == 0:
                    bpy.data.materials.remove(m)

        msg = f"Imported {total} silos"
        if skipped_existing:
            msg += f", {skipped_existing} patch(es) already imported (skipped)"
        if missing_osm:
            msg += f" | OSM missing (skipped): {', '.join(missing_osm)}"
        self.report({'WARNING'} if missing_osm else {'INFO'}, msg)
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Operator: Merge (like the wind turbines - joins per patch / LOD collection).
# ----------------------------------------------------------------------------
class CONDOR_OT_merge_silos(Operator):
    bl_idname = "condor.merge_silos"
    bl_label = "Merge Silos"
    bl_description = ("Merge all Silo_* objects per patch into one 'silo' object, "
                      "keeping how they were turned and their own shading")
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _is_fresh(name):
        # Only freshly-imported silos ('Silo_<patch>_NNN') count - same as the chimneys.
        # A merged ('silo') or OBJ-baked ('silo.001') one is NOT matched, so the button
        # greys out after merging.
        return name.lower().startswith(FRESH_PREFIX.lower())

    @classmethod
    def poll(cls, context):
        return any(cls._is_fresh(obj.name) for obj in bpy.data.objects)

    def execute(self, context):
        from mathutils import Matrix
        silos = [obj for obj in bpy.data.objects
                 if self._is_fresh(obj.name) and obj.type == 'MESH'
                 and obj.name in context.view_layer.objects]
        if not silos:
            self.report({'WARNING'}, "No silo objects found")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mat = _get_silo_material()

        # Group silos by their owning Condor patch/LOD collection.
        by_col = {}
        for obj in silos:
            col_name = next((c.name for c in obj.users_collection
                             if c.name.startswith("Condor_")), None)
            by_col.setdefault(col_name, []).append(obj)

        for col_name, objs in by_col.items():
            off = tuple(objs[0].get("patch_off", (0.0, 0.0)))
            for obj in objs:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                # bake the turn + place into the mesh in PATCH coordinates, with the
                # model's own normals turned along (no recompute)
                o = tuple(obj.get("patch_off", off))
                m = Matrix.Translation((-o[0], -o[1], 0.0)) @ obj.matrix_basis
                _transform_keep_normals(obj.data, m)
                obj.matrix_basis = Matrix.Identity(4)

            if len(objs) > 1:
                bpy.ops.object.select_all(action='DESELECT')
                for obj in objs:
                    obj.select_set(True)
                context.view_layer.objects.active = objs[0]
                bpy.ops.object.join()
                merged = context.active_object
            else:
                merged = objs[0]
            merged.name = GROUP_NAME
            merged.data.name = GROUP_NAME
            merged.data.materials.clear()
            merged.data.materials.append(mat)
            for poly in merged.data.polygons:
                poly.material_index = 0
            for k in ("patch_off",):
                if k in merged:
                    del merged[k]
            # mesh is in patch coordinates; the object carries the patch offset
            # (same layout as the buildings / water towers)
            merged.location = (off[0], off[1], 0.0)

        self.report({'INFO'}, f"Merged {len(silos)} silos into {len(by_col)} collection(s)")
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Panel row (called from panels.py inside the "Other objects" box).
# ----------------------------------------------------------------------------
def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Silo", icon='MESH_CYLINDER')
    row.prop(context.scene, "condor_silo_batch", text="Batch")
    row = box.row(align=True)
    row.operator("condor.import_silos", text="Import", icon='IMPORT')
    row.operator("condor.merge_silos", text="Merge", icon='AUTOMERGE_ON')


# ----------------------------------------------------------------------------
# Registration (operators + scene property + TEXTURE_MAP entries).
# ----------------------------------------------------------------------------
_classes = [CONDOR_OT_import_silos, CONDOR_OT_merge_silos]


def _patch_extra_obj_type():
    """Wrap operators._extra_obj_type so a 'silo' object counts as an EXTRA object (like
    the water towers / chimneys), not as a building - a patch with only its silos imported
    must not look like its buildings are there, and the silo baked into the patch OBJ is
    dropped when it was imported separately. The operators source is NOT edited."""
    from . import operators as _op
    if getattr(_op, "_silo_type_patched", False):
        return
    _orig = _op._extra_obj_type

    def _patched(name):
        if (name or "").lower().startswith(GROUP_NAME):
            return "silo"
        return _orig(name)

    _op._silo_orig_type = _orig
    _op._extra_obj_type = _patched
    _op._silo_type_patched = True


def _unpatch_extra_obj_type():
    from . import operators as _op
    if getattr(_op, "_silo_type_patched", False):
        _op._extra_obj_type = _op._silo_orig_type
        _op._silo_type_patched = False


def _patch_overpass_query():
    """Wrap osm_downloader.build_overpass_query so the OSM download ALSO fetches
    man_made=silo (building=silo already comes with the buildings) - kept here so the
    whole feature lives in one file. Removing this module restores the original query."""
    from . import osm_downloader as _osm
    if getattr(_osm, "_silo_patched", False):
        return
    _orig = _osm.build_overpass_query

    def _patched(lat_min, lat_max, lon_min, lon_max, *a, **k):
        q = _orig(lat_min, lat_max, lon_min, lon_max, *a, **k)
        bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
        extra = (
            f'  node["man_made"="silo"]({bbox});\n'
            f'  way["man_made"="silo"]({bbox});\n'
            f'  relation["man_made"="silo"]({bbox});'
        )
        return q.replace("\n);", "\n" + extra + "\n);", 1)

    _osm._silo_orig_query = _orig
    _osm.build_overpass_query = _patched
    _osm._silo_patched = True


def _unpatch_overpass_query():
    from . import osm_downloader as _osm
    if getattr(_osm, "_silo_patched", False):
        _osm.build_overpass_query = _osm._silo_orig_query
        _osm._silo_patched = False


def _patch_obj_exporter():
    """Wrap obj_exporter.export_condor_obj_mtl so that AFTER the exporter writes the
    patch OBJ, the silos are written into it with their OWN normals (file mode).
    Kept here so the whole feature lives in one file; the exporter/buildings source is
    NOT edited, and removing this module restores the original behaviour."""
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_silo_export_patched", False):
        return
    _orig = _oe.export_condor_obj_mtl

    def _patched(groups, obj_filepath, texture_map, *a, **k):
        stats = _orig(groups, obj_filepath, texture_map, *a, **k)
        try:
            _append_silos_after_export(obj_filepath)
        except Exception as e:
            print(f"[silo] file-mode append failed: {e}")
        return stats

    _oe._silo_orig_export = _orig
    _oe.export_condor_obj_mtl = _patched
    _oe._silo_export_patched = True


def _unpatch_obj_exporter():
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_silo_export_patched", False):
        _oe.export_condor_obj_mtl = _oe._silo_orig_export
        _oe._silo_export_patched = False


def register():
    from bpy.props import BoolProperty
    bpy.types.Scene.condor_silo_batch = BoolProperty(
        name="Batch",
        description=("File mode (Import to Blender off): after generating the OBJ, "
                     "also generate silos and add them as one silo object. "
                     "Off by default"),
        default=False,
    )
    for c in _classes:
        bpy.utils.register_class(c)
    # 'silo' is an extra object, not a building (see _patch_extra_obj_type)
    try:
        _patch_extra_obj_type()
    except Exception:
        pass
    # add the texture to the export map so file-mode MTL gets it
    try:
        from .. import config
        config.TEXTURE_MAP.setdefault(MAT_NAME, TEX_FILE)
        # object 'silo' uses material 'condor_transmitter' (like watertower)
        config.MATERIAL_ALIAS.setdefault(GROUP_NAME, MAT_NAME)
    except Exception:
        pass
    # make the OSM download fetch silos (kept in this module)
    try:
        _patch_overpass_query()
    except Exception:
        pass
    # write the silos into the OBJ after export, with their own normals (file mode)
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
    try:
        _unpatch_extra_obj_type()
    except Exception:
        pass
    for c in reversed(_classes):
        try: bpy.utils.unregister_class(c)
        except Exception: pass
    try:
        del bpy.types.Scene.condor_silo_batch
    except Exception:
        pass
    try:
        from .. import config
        # TEXTURE_MAP[condor_transmitter] is shared with the transmitter - left alone
        config.MATERIAL_ALIAS.pop(GROUP_NAME, None)
    except Exception:
        pass
