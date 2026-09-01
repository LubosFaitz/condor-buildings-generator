# -*- coding: utf-8 -*-
"""
OBJ <-> C3D conversion for Condor.

Pure library module (no bpy): reads/writes files on disk only, so it can be
used by the plugin's Export C3D button as well as standalone.
"""

import os
import sys
import struct
from array import array


# ============================================================================
#  FIXED SETTINGS  (used to be checkboxes in the dialog - now hardcoded)
# ============================================================================
REVERSE_WINDING = True    # "Reverse triangles (winding)"  - hardcoded on
FLIP_V          = True    # "Flip V (textures vertically)" - hardcoded on
SWAP_UV         = False
DOUBLE_SIDED    = False
SPEC            = 0.0
SHINY           = 0.0
ENV             = 0.0
TEX_PREFIX      = "Textures\\"   # path (prefix) to the textures


# ============================================================================
#  OBJ -> C3D  (conversion core)
# ============================================================================
def _resolve(idx, count):
    if idx is None:
        return None
    return idx - 1 if idx > 0 else count + idx


def parse_obj(path):
    positions, texcoords, normals = [], [], []
    groups, group_index = [], {}
    mtllibs = []
    lod = 0                                # LOD from the obj header (# LOD0 / # LOD1), passed on to the c3d
    cur_obj = cur_mat = None

    def group_for():
        key = (cur_obj, cur_mat)
        gi = group_index.get(key)
        if gi is None:
            gi = len(groups)
            group_index[key] = gi
            groups.append({"name": cur_obj, "material": cur_mat, "faces": []})
        return groups[gi]

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line[0] == "#":
                if "LOD1" in line:
                    lod = 1
                elif "LOD0" in line:
                    lod = 0
                continue
            tok = line.split()
            c = tok[0]
            if c == "v":
                x, y, z = float(tok[1]), float(tok[2]), float(tok[3])
                positions.append((y, x, z))     # C3D has X and Y swapped, Z unchanged
            elif c == "vt":
                texcoords.append((float(tok[1]), float(tok[2]) if len(tok) > 2 else 0.0))
            elif c == "vn":
                normals.append((float(tok[2]), float(tok[1]), float(tok[3])))   # same swap for normals
            elif c == "mtllib":
                mtllibs.extend(tok[1:])
            elif c in ("o", "g"):
                cur_obj = " ".join(tok[1:]) if len(tok) > 1 else cur_obj
            elif c == "usemtl":
                cur_mat = " ".join(tok[1:]) if len(tok) > 1 else None
            elif c == "f":
                face = []
                for part in tok[1:]:
                    p = part.split("/")
                    vi = int(p[0]) if p[0] else None
                    ti = int(p[1]) if len(p) > 1 and p[1] else None
                    ni = int(p[2]) if len(p) > 2 and p[2] else None
                    face.append((_resolve(vi, len(positions)),
                                 _resolve(ti, len(texcoords)),
                                 _resolve(ni, len(normals))))
                group_for()["faces"].append(face)
    return positions, texcoords, normals, groups, mtllibs, lod


def load_mtl(obj_path, mtllibs):
    mp = {}
    base_dir = os.path.dirname(os.path.abspath(obj_path))
    for lib in mtllibs:
        path = None
        for cand in (os.path.join(base_dir, lib), lib):
            if os.path.isfile(cand):
                path = cand
                break
        if not path:
            continue
        cur = None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    t = raw.split()
                    if not t:
                        continue
                    k = t[0].lower()
                    if k == "newmtl":
                        cur = " ".join(t[1:])
                    elif k == "map_kd" and cur is not None and len(t) > 1:
                        mp[cur] = t[-1]
                    elif k == "map_ka" and cur is not None and len(t) > 1 and cur not in mp:
                        mp[cur] = t[-1]
        except Exception:
            pass
    return mp


def face_normal(positions, face):
    nx = ny = nz = 0.0
    n = len(face)
    for i in range(n):
        ax, ay, az = positions[face[i][0]]
        bx, by, bz = positions[face[(i + 1) % n][0]]
        nx += (ay - by) * (az + bz)
        ny += (az - bz) * (ax + bx)
        nz += (ax - bx) * (ay + by)
    L = (nx * nx + ny * ny + nz * nz) ** 0.5
    return (0.0, 0.0, 1.0) if L < 1e-12 else (nx / L, ny / L, nz / L)


def texture_path(material, cfg, mtl_map):
    tex = mtl_map.get(material) if material else None
    if tex:
        base = os.path.splitext(os.path.basename(tex.replace("\\", "/")))[0]
    else:
        base = material or cfg["default_tex"]
    return cfg["tex_prefix"] + base + cfg["tex_ext"]


def build_objects(positions, texcoords, normals, groups, cfg, mtl_map):
    all_vertices, all_indices, objects = [], [], []
    rev = cfg["reverse_winding"]
    flip = cfg["flip_v"]
    swap = cfg["swap_uv"]
    ds = cfg["double_sided"]

    for g in groups:
        if not g["faces"]:
            continue
        first_vertex = len(all_vertices)
        first_index = len(all_indices)
        dedup = {}

        def add_vertex(vt):
            key = tuple(round(x, 6) for x in vt)
            li = dedup.get(key)
            if li is None:
                li = len(dedup)
                dedup[key] = li
                all_vertices.append(vt)
            return li

        for face in g["faces"]:
            computed = None
            data = []
            for (vi, ti, ni) in face:
                px, py, pz = positions[vi]
                if ni is not None and ni < len(normals):
                    nx, ny, nz = normals[ni]
                else:
                    if computed is None:
                        computed = face_normal(positions, face)
                    nx, ny, nz = computed
                if ti is not None and ti < len(texcoords):
                    u, v = texcoords[ti]
                else:
                    u, v = 0.0, 0.0
                if swap:
                    u, v = v, u
                if flip:
                    v = -v
                data.append((px, py, pz, nx, ny, nz, u, v))

            def emit(verts, reverse):
                for k in range(1, len(verts) - 1):
                    tri = (verts[0], verts[k + 1], verts[k]) if reverse \
                        else (verts[0], verts[k], verts[k + 1])
                    for li in tri:
                        all_indices.append(first_vertex + li)

            front = [add_vertex(d) for d in data]
            emit(front, rev)
            if ds:
                back = [add_vertex((d[0], d[1], d[2], -d[3], -d[4], -d[5], d[6], d[7]))
                        for d in data]
                emit(back, not rev)

        objects.append({
            "name": g["name"] or "object",
            "tex": texture_path(g["material"], cfg, mtl_map),
            "fv": first_vertex, "vc": len(all_vertices) - first_vertex,
            "fi": first_index, "ic": len(all_indices) - first_index,
        })
    return objects, all_vertices, all_indices


def _shortstr(s):
    b = s.encode("cp1252", errors="replace")[:255]
    return bytes([len(b)]) + b


def write_c3d(path, objects, vertices, indices, cfg, lod=0, n_lod0=None, n_lod1=None):
    out = bytearray()
    n = len(objects)
    if n_lod0 is None:                       # single obj: the whole file is one LOD
        n_lod0 = 0 if lod == 1 else n
        n_lod1 = n if lod == 1 else 0
    out += b"C3D"
    if n_lod1:                               # merged LOD0+LOD1 = version 2
        out += struct.pack("<I", 2)
        out += struct.pack("<5i", 0, n_lod0, n_lod0, n_lod1, n)
    else:                                    # one LOD = version 1 (same as an original c3d)
        out += struct.pack("<I", 1)
        out += struct.pack("<3i", 0, n, n)
    color = struct.pack("<4f", *cfg["color"])
    tail = struct.pack("<3f", cfg["spec"], cfg["shiny"], cfg["env"])
    for o in objects:
        out += _shortstr(o["name"])
        out += struct.pack("<iiii", o["fv"], o["vc"], o["fi"], o["ic"])
        out += _shortstr(o["tex"])
        out += color
        out += tail

    out += struct.pack("<I", len(vertices))
    flat = array("f")
    for v in vertices:
        flat.extend(v)
    if sys.byteorder != "little":
        flat.byteswap()
    out += flat.tobytes()

    out += struct.pack("<I", len(indices))
    idx = array("I", indices)
    if sys.byteorder != "little":
        idx.byteswap()
    out += idx.tobytes()

    with open(path, "wb") as f:
        f.write(out)
    return len(out)


def convert_obj_to_c3d(obj_path, c3d_path, cfg):
    positions, texcoords, normals, groups, mtllibs, lod = parse_obj(obj_path)
    mtl_map = load_mtl(obj_path, mtllibs)
    objects, vertices, indices = build_objects(positions, texcoords, normals,
                                               groups, cfg, mtl_map)
    if not objects:
        raise ValueError("no faces (f) in the OBJ")
    size = write_c3d(c3d_path, objects, vertices, indices, cfg, lod)
    return len(objects), len(vertices), len(indices), size


def _build_from_obj(obj_path, cfg):
    positions, texcoords, normals, groups, mtllibs, lod = parse_obj(obj_path)
    mtl_map = load_mtl(obj_path, mtllibs)
    objects, vertices, indices = build_objects(positions, texcoords, normals,
                                               groups, cfg, mtl_map)
    return objects, vertices, indices


def convert_pair_to_c3d(obj_lod0, obj_lod1, c3d_path, cfg):
    """Merge a LOD0 obj + LOD1 obj into a single c3d (version 2): LOD0 objects
    first, then LOD1 objects. The c3d is named after the LOD0 obj."""
    o0, v0, i0 = _build_from_obj(obj_lod0, cfg)
    o1, v1, i1 = _build_from_obj(obj_lod1, cfg)
    if not o0 or not o1:
        raise ValueError("no faces (f) in one of the obj files")
    nv0, ni0 = len(v0), len(i0)
    for o in o1:                              # shift the LOD1 offsets past LOD0
        o["fv"] += nv0
        o["fi"] += ni0
    objects = o0 + o1
    vertices = v0 + v1
    indices = i0 + [ix + nv0 for ix in i1]    # LOD1 indices point at the shifted vertices
    size = write_c3d(c3d_path, objects, vertices, indices, cfg,
                     n_lod0=len(o0), n_lod1=len(o1))
    return len(objects), len(vertices), len(indices), size


# ============================================================================
#  C3D -> OBJ  (reverse conversion - undoes the same transformations)
# ============================================================================
def parse_c3d(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:3] != b"C3D":
        raise ValueError("not a C3D file")
    off = 3
    version = struct.unpack_from("<I", data, off)[0]; off += 4
    if version >= 2:
        h = struct.unpack_from("<5i", data, off); off += 20   # [0][LOD0][LOD0][LOD1][nobj]
        nobj = h[4]
        n_lod0 = h[2]                         # number of LOD0 objects (the rest are LOD1)
        lod = 1 if (h[2] == 0 and h[3] > 0) else 0
    else:
        off += 8                              # <ii> 0, nobj (version 1)
        nobj = struct.unpack_from("<I", data, off)[0]; off += 4
        lod = 0
        n_lod0 = nobj                         # version 1 = everything is LOD0
    objects = []
    for _ in range(nobj):
        ln = data[off]; off += 1
        name = data[off:off + ln].decode("cp1252", "replace"); off += ln
        fv, vc, fi, ic = struct.unpack_from("<iiii", data, off); off += 16
        tln = data[off]; off += 1
        tex = data[off:off + tln].decode("cp1252", "replace"); off += tln
        off += 28                             # color 4f + spec/shiny/env 3f
        objects.append({"name": name, "fv": fv, "vc": vc, "fi": fi, "ic": ic, "tex": tex})
    vcount = struct.unpack_from("<I", data, off)[0]; off += 4
    verts = array("f"); verts.frombytes(data[off:off + vcount * 32]); off += vcount * 32
    if sys.byteorder != "little":
        verts.byteswap()
    icount = struct.unpack_from("<I", data, off)[0]; off += 4
    idx = array("I"); idx.frombytes(data[off:off + icount * 4])
    if sys.byteorder != "little":
        idx.byteswap()
    return objects, verts, vcount, idx, lod, n_lod0


def _tex_base(tex):
    return os.path.splitext(os.path.basename(tex.replace("\\", "/")))[0]


def write_obj(obj_path, objects, verts, vcount, idx, cfg, lod=0):
    flip = cfg["flip_v"]
    swap = cfg["swap_uv"]
    rev = cfg["reverse_winding"]
    mtl_name = os.path.splitext(os.path.basename(obj_path))[0] + ".mtl"

    base = os.path.splitext(os.path.basename(obj_path))[0].replace("_LOD1", "")
    patch = base[1:] if base[:1].lower() == "o" else base
    out = []
    ap = out.append
    ap("# Condor Buildings Generator OBJ (Condor-ready)")
    ap("# Objects: %d" % len(objects))
    ap("# Axis swap: True | Triangulated: True | Normals: True")
    ap("# LOD%d - Patch %s (Condor-ready)" % (lod, patch))
    ap("mtllib " + mtl_name)
    for i in range(vcount):
        b = i * 8                              # swap X<->Y back
        ap("v %.6f %.6f %.6f" % (verts[b + 1], verts[b], verts[b + 2]))
    for i in range(vcount):
        b = i * 8
        u, v = verts[b + 6], verts[b + 7]
        if flip:
            v = -v
        if swap:
            u, v = v, u
        ap("vt %.6f %.6f" % (u, v))
    for i in range(vcount):
        b = i * 8                              # normals get the same X<->Y swap back
        ap("vn %.6f %.6f %.6f" % (verts[b + 4], verts[b + 3], verts[b + 5]))

    mats = {}
    for o in objects:
        ap("o " + o["name"])
        mat = o["name"] or "material"
        mats[mat] = o["tex"]
        ap("usemtl " + mat)
        fi = o["fi"]
        for t in range(fi, fi + o["ic"], 3):
            a = idx[t] + 1
            bb = idx[t + 1] + 1
            c = idx[t + 2] + 1
            if rev:                            # inverse winding
                bb, c = c, bb
            ap("f %d/%d/%d %d/%d/%d %d/%d/%d" % (a, a, a, bb, bb, bb, c, c, c))

    with open(obj_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    mtl_path = os.path.join(os.path.dirname(obj_path), mtl_name)
    ml = ["# Condor Buildings Generator MTL"]
    for mat, tex in mats.items():
        ml.append("")
        ml.append("newmtl " + mat)
        ml.append("Ka 1.000000 1.000000 1.000000")
        ml.append("Kd 1.000000 1.000000 1.000000")
        ml.append("Ks 0.000000 0.000000 0.000000")
        ml.append("Ns 0.000000")
        ml.append("d 1.000000")
        ml.append("illum 1")
        ml.append("map_Kd " + _tex_base(tex) + ".dds")
    with open(mtl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ml) + "\n")
    return vcount, len(idx) // 3


def convert_c3d_to_obj(c3d_path, obj_path, cfg):
    objects, verts, vcount, idx, lod, n_lod0 = parse_c3d(c3d_path)
    if not objects:
        raise ValueError("no objects in the C3D")
    n_lod1 = len(objects) - n_lod0

    # Merged c3d (LOD0 + LOD1) -> split into two obj: <patch>.obj + <patch>_LOD1.obj
    if n_lod0 > 0 and n_lod1 > 0:
        base = os.path.splitext(obj_path)[0]
        if base.lower().endswith("_lod1"):
            path1 = obj_path
            path0 = base[:-5] + ".obj"
        else:
            path0 = obj_path
            path1 = base + "_LOD1.obj"
        nv0 = objects[n_lod0]["fv"]           # boundary = fv of the first LOD1 object
        ni0 = objects[n_lod0]["fi"]
        # LOD0 part (offsets are already 0-based)
        write_obj(path0, objects[:n_lod0], verts[:nv0 * 8], nv0, idx[:ni0], cfg, lod=0)
        # LOD1 part (shift offsets and indices back to 0)
        lod1_objs = [dict(o) for o in objects[n_lod0:]]
        for o in lod1_objs:
            o["fv"] -= nv0
            o["fi"] -= ni0
        lod1_idx = array("I", [ix - nv0 for ix in idx[ni0:]])
        write_obj(path1, lod1_objs, verts[nv0 * 8:], vcount - nv0, lod1_idx, cfg, lod=1)
        return len(objects), vcount, len(idx)

    nv, nt = write_obj(obj_path, objects, verts, vcount, idx, cfg, lod)
    return len(objects), nv, nt * 3


# ============================================================================
#  DEFAULTS
# ============================================================================
def default_cfg(tex_prefix=TEX_PREFIX):
    """Conversion settings used by the plugin's Export C3D button."""
    return dict(reverse_winding=REVERSE_WINDING, flip_v=FLIP_V,
                swap_uv=SWAP_UV, double_sided=DOUBLE_SIDED,
                spec=SPEC, shiny=SHINY, env=ENV,
                color=(1.0, 1.0, 1.0, 1.0),
                tex_prefix=tex_prefix, tex_ext=".dds", default_tex="default")
