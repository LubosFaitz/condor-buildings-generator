"""
Condor Buildings Generator - Terrain smoothing (tessellation simulation)

Condor tessellates the terrain when it loads a landscape, so the surface the
glider actually flies over is slightly smoother than the raw h<patch>.obj mesh.
This module reproduces that with Blender's Smooth (Laplacian) modifier, run as
plain NumPy. Only the resulting HEIGHTS are stored - one value per line in
``Heightmaps/smooth/h<patch>.txt``, a few hundred kB - and every autogen
generator then sits its objects on the original geometry with those heights.
The original terrain (and ``modified``) is only ever read, never written to.

``laplacian_smooth()`` below is the transcription of the modifier's formula; the
rest of the module only feeds it the settings from the add-on preferences and
takes care of the OBJ file and of the twin object in the Blender scene.

Everything here is optional and fail-safe: with the feature switched off, or on
any error, the callers get the exact same terrain path the plugin used before.
"""

import logging
import os

try:
    import numpy as np
except ImportError:  # numpy ships with Blender; plain-CPython CLI may lack it
    np = None

logger = logging.getLogger(__name__)


# =============================================================================
# Blender's Smooth (Laplacian) modifier as plain NumPy - the transcription
# of the modifier's formula. Settings come from the add-on preferences.
# =============================================================================

MIN_AREA = 1e-5


def cot_weight(p, q, r):
    u, v = q - p, r - p
    cc = np.linalg.norm(np.cross(u, v), axis=1)
    out = np.zeros(len(p))
    m = cc > 1e-7
    out[m] = np.einsum('ij,ij->i', u[m], v[m]) / cc[m]
    return out * 0.5


def laplacian_smooth(V, polys, repeat, use_x, use_y, use_z,
                     lam, lam_border, preserve_volume, normalized):
    V = np.asarray(V, dtype=np.float64)
    n = len(V)

    groups = {}
    for p in polys:
        groups.setdefault(len(p), []).append(p)
    groups = {k: np.array(v, dtype=np.int64) for k, v in groups.items()}

    ne_fa = np.zeros(n, dtype=np.int64)
    for k, P in groups.items():
        np.add.at(ne_fa, P.ravel(), 1)

    ep = []
    for k, P in groups.items():
        for j in range(k):
            ep.append(np.stack([P[:, j], P[:, (j + 1) % k]], axis=1))
    edges = np.unique(np.sort(np.vstack(ep), axis=1), axis=0)

    ne_ed = np.zeros(n, dtype=np.int64)
    np.add.at(ne_ed, edges[:, 0], 1)
    np.add.at(ne_ed, edges[:, 1], 1)

    zerola = np.zeros(n, dtype=bool)
    elen = np.linalg.norm(V[edges[:, 0]] - V[edges[:, 1]], axis=1)
    short = elen < MIN_AREA
    zerola[edges[short, 0]] = True
    zerola[edges[short, 1]] = True
    eweights = np.where(short, elen, 1.0 / np.where(elen == 0, 1.0, elen))

    vweights = np.zeros(n)
    ring_areas = np.zeros(n)
    corners = []
    for k, P in groups.items():
        for j in range(k):
            curr = P[:, j]
            nxt = P[:, (j + 1) % k]
            prv = P[:, (j - 1) % k]
            a, b, c = V[curr], V[nxt], V[prv]
            areaf = np.linalg.norm(np.cross(b - a, c - a), axis=1) * 0.5
            zerola[curr[areaf < MIN_AREA]] = True

            np.add.at(ring_areas, curr, areaf)
            np.add.at(ring_areas, nxt, areaf)
            np.add.at(ring_areas, prv, areaf)

            w1 = cot_weight(a, b, c)
            w2 = cot_weight(b, c, a)
            w3 = cot_weight(c, a, b)

            np.add.at(vweights, curr, w2 + w3)
            np.add.at(vweights, nxt, w1 + w3)
            np.add.at(vweights, prv, w1 + w2)
            corners.append((curr, nxt, prv, w1, w2, w3))

    is_ring = (ne_ed == ne_fa)

    if normalized:
        w = vweights
        vw = np.where(w == 0.0, 0.0, -abs(lam) / np.where(w == 0.0, 1.0, w))
        d_ring = np.full(n, 1.0 + abs(lam))
    else:
        w = vweights * ring_areas
        vw = np.where(w == 0.0, 0.0,
                      -abs(lam) / (4.0 * np.where(w == 0.0, 1.0, w)))
        d_ring = 1.0 + abs(lam) / (4.0 * np.where(ring_areas == 0.0, 1.0, ring_areas))

    vlen = np.zeros(n)
    bnd = ~is_ring
    be = bnd[edges[:, 0]] & bnd[edges[:, 1]]
    np.add.at(vlen, edges[be, 0], eweights[be])
    np.add.at(vlen, edges[be, 1], eweights[be])
    vl = np.where(vlen == 0.0, 0.0,
                  -abs(lam_border) * 2.0 / np.where(vlen == 0.0, 1.0, vlen))

    diag = np.where(is_ring, d_ring, 1.0 + abs(lam_border) * 2.0)
    diag[zerola] = 1.0

    rI, cJ, val = [], [], []
    ok = is_ring & (~zerola)
    for curr, nxt, prv, w1, w2, w3 in corners:
        m = ok[curr]
        rI += [curr[m], curr[m]]
        cJ += [nxt[m], prv[m]]
        val += [w3[m] * vw[curr[m]], w2[m] * vw[curr[m]]]
        m = ok[nxt]
        rI += [nxt[m], nxt[m]]
        cJ += [curr[m], prv[m]]
        val += [w3[m] * vw[nxt[m]], w1[m] * vw[nxt[m]]]
        m = ok[prv]
        rI += [prv[m], prv[m]]
        cJ += [curr[m], nxt[m]]
        val += [w2[m] * vw[prv[m]], w1[m] * vw[prv[m]]]

    bm = be & (~zerola[edges[:, 0]]) & (~zerola[edges[:, 1]])
    rI += [edges[bm, 0], edges[bm, 1]]
    cJ += [edges[bm, 1], edges[bm, 0]]
    val += [eweights[bm] * vl[edges[bm, 0]], eweights[bm] * vl[edges[bm, 1]]]

    rI = np.concatenate(rI)
    cJ = np.concatenate(cJ)
    val = np.concatenate(val)

    centroid = V.mean(axis=0)

    def volume(P):
        tot = 0.0
        for k, G in groups.items():
            for j in range(1, k - 1):
                a = P[G[:, 0]] - centroid
                b = P[G[:, j]] - centroid
                c = P[G[:, j + 1]] - centroid
                tot += np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6.0
        return abs(tot)

    axes = [i for i, use in enumerate((use_x, use_y, use_z)) if use]

    P = V.copy()
    sgn = 1.0 if lam >= 0 else -1.0
    upd = ~zerola

    for _ in range(repeat):
        vini = volume(P) if preserve_volume else 0.0
        sol = {}
        for ax in axes:
            rhs = P[:, ax].copy()
            x = rhs.copy()
            for _ in range(20000):
                s = np.bincount(rI, weights=val * x[cJ], minlength=n)
                xn = (rhs - s) / diag
                if np.max(np.abs(xn - x)) < 1e-10:
                    x = xn
                    break
                x = xn
            sol[ax] = x
        for ax in axes:
            P[upd, ax] += sgn * (sol[ax][upd] - P[upd, ax])
        if preserve_volume:
            vend = volume(P)
            if vend != 0.0:
                beta = (vini / vend) ** (1.0 / 3.0)
                for ax in axes:
                    P[:, ax] = (P[:, ax] - centroid[ax]) * beta + centroid[ax]

    return P


SMOOTH_COLLECTION = "Patch_Terrain_Smooth"

DEFAULT_REPEAT = 2
DEFAULT_LAMBDA = 4.0


# =============================================================================
# Feature state - read from the add-on preferences
# (Edit > Preferences > Add-ons > Condor Buildings Generator)
# Blender keeps those in userpref.blend, so they survive scene changes and
# restarts on their own. Safe to call from modules that may run without Blender.
# =============================================================================

def _preferences():
    import bpy
    addon = bpy.context.preferences.addons[__package__.split('.')[0]]
    return addon.preferences


def smooth_enabled():
    """True when the user ticked the terrain smoothing checkbox."""
    try:
        return bool(_preferences().terrain_smooth_enable)
    except Exception:
        return False


def smooth_params():
    """All Smooth (Laplacian) modifier settings from the add-on preferences.

    Same names and meaning as the modifier panel in Blender.
    """
    defaults = {
        'repeat': DEFAULT_REPEAT,
        'lam': DEFAULT_LAMBDA,
        'lam_border': 0.0,
        'use_x': False,
        'use_y': False,
        'use_z': True,
        'preserve_volume': True,
        'normalized': True,
    }
    try:
        prefs = _preferences()
        return {
            'repeat': int(prefs.terrain_smooth_repeat),
            'lam': float(prefs.terrain_smooth_lambda),
            'lam_border': float(prefs.terrain_smooth_border),
            'use_x': bool(prefs.terrain_smooth_axis_x),
            'use_y': bool(prefs.terrain_smooth_axis_y),
            'use_z': bool(prefs.terrain_smooth_axis_z),
            'preserve_volume': bool(prefs.terrain_smooth_preserve_volume),
            'normalized': bool(prefs.terrain_smooth_normalized),
        }
    except Exception:
        return defaults


# =============================================================================
# Smoothed heights (built on demand, cached on disk)
#
# Only the heights are stored, one value per line, in the vertex order of the
# source OBJ - the geometry (faces, UVs, X/Y) is the original's and is never
# copied. That is a text file of a few hundred kB instead of a multi-megabyte
# OBJ. The original terrain is only ever read, never written to.
# =============================================================================

def _cache_header(p, source_obj_path):
    """Stamp of the settings AND of the source file.

    The cached heights are reused only when both match, so swapping the source
    (modified <-> the original one) or re-saving it forces a recomputation.
    """
    try:
        st = os.stat(source_obj_path)
        src = "%s size=%d mtime=%d" % (os.path.basename(
            os.path.dirname(source_obj_path)) + "/" + os.path.basename(
            source_obj_path), st.st_size, int(st.st_mtime))
    except OSError:
        src = "?"
    return ("# condor_smooth repeat=%d lambda=%.4f border=%.4f axis=%d%d%d "
            "volume=%d normalized=%d src=%s\n" % (
                p['repeat'], p['lam'], p['lam_border'],
                p['use_x'], p['use_y'], p['use_z'],
                p['preserve_volume'], p['normalized'], src))


def _cache_is_valid(out_path, header):
    try:
        if not os.path.exists(out_path):
            return False
        with open(out_path, 'r') as f:
            return f.readline() == header
    except OSError:
        return False


def smooth_heights_path(heightmaps_dir, patch_id, tref=False):
    return os.path.join(smooth_dir(heightmaps_dir, tref), "h%s.txt" % patch_id)


def build_smooth_heights(source_obj_path, out_dir, patch_id, params):
    """Write (or reuse) the smoothed heights of a patch and return the file path.

    The smoothing is ``laplacian_smooth()`` above - the transcription of the
    Smooth (Laplacian) modifier - run with the settings from the add-on
    preferences. Axes that are switched off keep the original coordinates, so
    with the default (Z only) X and Y come out exactly as in the source.
    """
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "h%s.txt" % patch_id)
    header = _cache_header(params, source_obj_path)

    if _cache_is_valid(out_path, header):
        return out_path

    if np is None:
        raise RuntimeError("numpy not available")

    verts, polys = read_obj_geometry(source_obj_path)
    if not verts or not polys:
        raise RuntimeError("no geometry in %s" % source_obj_path)

    V = np.array(verts, dtype=np.float64)
    P = laplacian_smooth(
        V, polys,
        params['repeat'],
        params['use_x'], params['use_y'], params['use_z'],
        params['lam'], params['lam_border'],
        params['preserve_volume'], params['normalized'],
    )

    with open(out_path, 'w') as f:
        f.write(header)
        f.write("".join("%.2f\n" % z for z in P[:, 2]))

    logger.info("Smoothed heights written: %s (%d vertices)", out_path, len(P))
    return out_path


def read_obj_geometry(obj_path):
    """(vertices, polygons) of an OBJ - vertices as [x, y, z], faces 0-based."""
    verts, polys = [], []
    with open(obj_path, 'r') as f:
        for line in f:
            if line.startswith('v '):
                verts.append([float(x) for x in line.split()[1:4]])
            elif line.startswith('f '):
                polys.append([int(t.split('/')[0]) - 1 for t in line.split()[1:]])
    return verts, polys


def read_smooth_heights(heights_path):
    """Heights from a smoothed-heights file, in vertex order."""
    heights = []
    with open(heights_path, 'r') as f:
        for line in f:
            if line and not line.startswith('#'):
                heights.append(float(line))
    return heights


# =============================================================================
# Central terrain path resolver - used by every generator
# =============================================================================

def source_terrain_path(heightmaps_dir, patch_id, tref=False):
    """The terrain file the plugin uses when smoothing is off (unchanged rules)."""
    if tref:
        return os.path.join(heightmaps_dir, "22.5m", "h%s.obj" % patch_id)
    modified_path = os.path.join(heightmaps_dir, "modified", "h%s.obj" % patch_id)
    default_path = os.path.join(heightmaps_dir, "h%s.obj" % patch_id)
    return modified_path if os.path.exists(modified_path) else default_path


def smooth_dir(heightmaps_dir, tref=False):
    if tref:
        return os.path.join(heightmaps_dir, "22.5m", "smooth")
    return os.path.join(heightmaps_dir, "smooth")


def resolve_smooth_or_source(heightmaps_dir, patch_id, tref=False,
                             source_path=None):
    """The terrain FILE the plugin reads - unchanged rules, never the smoothed one.

    Smoothing does not produce a terrain file of its own (only heights), so this
    always points at the original OBJ. Callers that want the smoothed surface use
    ``load_terrain_smoothed()``.
    """
    if source_path is not None:
        return source_path
    return source_terrain_path(heightmaps_dir, patch_id, tref)


def load_terrain_smoothed(heightmaps_dir, patch_id, tref=False, source_path=None):
    """The terrain mesh the autogen should sit on.

    Smoothing off -> exactly the terrain the plugin used before. Smoothing on ->
    the same geometry with the smoothed heights applied. Anything going wrong
    falls back to the original terrain, so generation can never fail because of
    smoothing.
    """
    from ..io.terrain_loader import load_terrain

    src = resolve_smooth_or_source(heightmaps_dir, patch_id, tref, source_path)
    if not smooth_enabled():
        return load_terrain(src)

    try:
        heights_path = build_smooth_heights(
            src, smooth_dir(heightmaps_dir, tref), patch_id, smooth_params())
        heights = read_smooth_heights(heights_path)
        verts, polys = read_obj_geometry(src)
        if len(heights) != len(verts):
            raise RuntimeError("heights (%d) do not match the terrain (%d)"
                               % (len(heights), len(verts)))
    except Exception as e:
        logger.warning("Terrain smoothing failed for patch %s (%s) - "
                       "using the original terrain", patch_id, e)
        return load_terrain(src)

    return _terrain_mesh_with_heights(verts, polys, heights)


def _terrain_mesh_with_heights(verts, polys, heights):
    """TerrainMesh from the original geometry with the smoothed heights."""
    from ..models.geometry import Point3D
    from ..models.terrain import TerrainMesh
    from ..config import TERRAIN_GRID_STEP

    points = [Point3D(v[0], v[1], heights[i]) for i, v in enumerate(verts)]
    quads = []
    for p in polys:
        if len(p) == 4:
            quads.append(tuple(p))
        elif len(p) == 3:
            quads.append((p[0], p[1], p[2], p[2]))
    return TerrainMesh.from_quads(points, quads, TERRAIN_GRID_STEP)


# =============================================================================
# Smoothed terrain in the Blender scene (Patch_Terrain_Smooth collection)
# =============================================================================

def terrain_object_name(patch_id, tref=False):
    return ("TR3f%s" if tref else "TR3%s") % patch_id


def smooth_object_name(orig_obj_name):
    return "%s_smooth" % orig_obj_name


def _find_layer_collection(layer_collection, name):
    if layer_collection.name == name:
        return layer_collection
    for child in layer_collection.children:
        res = _find_layer_collection(child, name)
        if res:
            return res
    return None


def _import_smooth_terrain(context, source_obj_path, patch_id, orig_obj_name, paths):
    """Smoothed twin of the terrain already in the scene.

    It is a copy of the original terrain object with the smoothed heights written
    into its vertices - nothing is imported and no polygon is touched, so the mesh
    keeps exactly the same quads, UVs and material as the original terrain.
    """
    import bpy

    smooth_name = smooth_object_name(orig_obj_name)
    existing = bpy.data.objects.get(smooth_name)
    if existing is not None:
        return existing

    orig_obj = bpy.data.objects.get(orig_obj_name)
    if orig_obj is None or orig_obj.type != 'MESH':
        return None

    tref = orig_obj_name.startswith("TR3f")
    heights_path = build_smooth_heights(
        source_obj_path, smooth_dir(paths['heightmaps'], tref),
        patch_id, smooth_params(),
    )
    heights = read_smooth_heights(heights_path)
    if len(heights) != len(orig_obj.data.vertices):
        logger.warning("Smoothed heights for patch %s (%d) do not match the terrain "
                       "in the scene (%d) - skipping the smoothed twin",
                       patch_id, len(heights), len(orig_obj.data.vertices))
        return None

    smooth_col = bpy.data.collections.get(SMOOTH_COLLECTION)
    if not smooth_col:
        smooth_col = bpy.data.collections.new(SMOOTH_COLLECTION)
        context.scene.collection.children.link(smooth_col)

    # Copy of the original mesh (same quads, UVs and material), heights replaced.
    mesh = orig_obj.data.copy()
    for i, v in enumerate(mesh.vertices):
        v.co.z = heights[i]
    mesh.update()

    smooth_obj = bpy.data.objects.new(smooth_name, mesh)
    smooth_obj.matrix_world = orig_obj.matrix_world.copy()
    smooth_col.objects.link(smooth_obj)
    return smooth_obj


def import_smooth_terrain(context, source_obj_path, patch_id, orig_obj_name, paths):
    """Import the smoothed terrain of this patch into Patch_Terrain_Smooth.

    Never raises - returns None when the smoothed terrain can't be produced.
    """
    try:
        return _import_smooth_terrain(context, source_obj_path, patch_id,
                                      orig_obj_name, paths)
    except Exception as e:
        logger.warning("Smoothed terrain import failed for patch %s: %s", patch_id, e)
        return None


def scene_terrain_object(patch_id, tref=False):
    """The terrain object autogen should raycast against.

    With smoothing on, the smoothed copy takes precedence (when it is in the
    scene); otherwise the original object, exactly as before.
    """
    try:
        import bpy
    except ImportError:
        return None
    orig_name = terrain_object_name(patch_id, tref)
    if smooth_enabled():
        smooth_obj = bpy.data.objects.get(smooth_object_name(orig_name))
        if smooth_obj is not None:
            return smooth_obj
    return bpy.data.objects.get(orig_name)
