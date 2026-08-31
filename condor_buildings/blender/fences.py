"""
Fences - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Tree row feature: everything lives in THIS one file plus three tiny,
clearly-marked, try/except-guarded hooks in:
  - __init__.py            (register/unregister this module)
  - blender/panels.py      (draw the Fence block inside "Other objects")
  - blender/properties.py  (the post-spacing slider in the add-on preferences)

Delete this file (or comment out those hooks) and the plugin falls back to exactly the
previous behaviour - nothing else depends on it. It does NOT touch buildings, pylons,
bridges, the exporter, config.py or osm_downloader.py: those are only wrapped/patched
from here, the way tree_rows.py does it.

The main Overpass query stays UNTOUCHED. barrier=fence is one of the most common tags in
OSM, so adding it to the buildings query would make every download noticeably bigger for
everybody. Instead the fences are fetched only when they are really being built: each
patch is checked (a cheap text scan of its map_<patch>.osm) and only a patch that has
none yet gets ONE tiny extra query of its own, whose result is merged into that file. A
patch that genuinely has none is marked in the file, so it is not asked about again.

What it does (the whole core):
  Every OSM way tagged barrier=fence becomes a fence line. A POST stands in every vertex
  of the line (so a corner always gets one) and the stretch between two vertices is
  divided EVENLY by roughly the spacing from the preferences - per segment, not by
  stepping along the whole line, so a corner is never doubled and no short stub is left.

  One post is an octagonal prism 0.16 m across, standing 1.30 m above the terrain and
  sunk 1.00 m into it (2.30 m of prism in total), so no hole opens up under it on a
  slope. It is capped on top only - the bottom is underground.

  TWO wires run at 0.60 m and 1.20 m above the terrain. Each has an equilateral
  triangular cross-section 1 cm across and runs THROUGH the posts without stopping at
  them: it is a straight line from post to post (no sag) and breaks at a corner together
  with the fence, using the averaged (mitred) direction of the two segments so the parts
  meet without a gap.

  ONE fence line from OSM = ONE object 'fence'. It uses the SAME material as the
  powerlines and the aerialways - 'pylones' on assets/pylons/Pylons.dds: the post model
  brings its own UVs and the wire samples the dark grey block at the top of that texture.
"""

import os
import math
import logging

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

OBJECT_NAME = "fence"
MAT_NAME = "pylones"              # the SAME material powerlines and aerialways use
TEX_FILE = "Pylons.dds"           # the powerline / aerialway texture, shared with them

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "pylons")
POST_MODEL = "fence_post.obj"     # the post model (assets/pylons), UVs already on Pylons.dds

PATCH_HALF = 2880.0
PATCH_SIZE = 5760.0

# --- the post ---------------------------------------------------------------
# The post is NOT generated any more - it is the fence_post.obj model, an octagonal
# wooden post 0.16 m across whose origin sits at ground level: 1.00 m of it is below the
# terrain (so no hole shows under it on a slope) and 1.30 m above.
POST_HEIGHT = 1.30            # how far the model stands ABOVE the terrain (m)

# --- the wires --------------------------------------------------------------
# The wires hang off the POST, not off the terrain: the top one sits WIRE_TOP_DROP under
# the head of the post, the lower one WIRE_GAP under that. With a 1.30 m post that comes
# out at 1.20 m and 0.60 m above the ground.
WIRE_TOP_DROP = 0.10          # top wire below the head of the post (m)
WIRE_GAP = 0.60              # the lower wire below the top one (m)
WIRE_HEIGHTS = (POST_HEIGHT - WIRE_TOP_DROP - WIRE_GAP, POST_HEIGHT - WIRE_TOP_DROP)
WIRE_SIDE = 0.01              # side of the equilateral triangular cross-section (1 cm)
WIRE_HALF = WIRE_SIDE / 2.0                        # half of the horizontal bottom edge
WIRE_IN = WIRE_SIDE / (2.0 * math.sqrt(3.0))       # centroid -> bottom edge
WIRE_OUT = WIRE_SIDE / math.sqrt(3.0)              # centroid -> top apex

# --- wire UVs ---------------------------------------------------------------
# The post model brings its own UVs. The wire samples the DARK GREY block at the top of
# Pylons.dds (measured in the file: pixels x 171-332, y 0-169 of 512 x 1024, a solid
# 65,69,74 grey) - a small square well inside it, so no edge of the block is caught.
# Like the powerline cables, the square is NOT a single point: zero-area UV triangles
# sample the mip-averaged texture and render pink.
UV_WIRE_U0, UV_WIRE_U1 = 0.47, 0.49
UV_WIRE_V0, UV_WIRE_V1 = 0.90, 0.92

# --- post spacing slider (add-on preferences) -------------------------------
SPACING_DEFAULT = 4.0         # target distance between two posts (m)
SPACING_MIN = 2.0
SPACING_MAX = 8.0

MAX_POSTS = 60000             # safety stop for one patch (a whole town of fences)


# ---------------------------------------------------------------------------
# OSM parsing
# ---------------------------------------------------------------------------
def _parse_fences(root, projector):
    """Return [(way_id, points)] for every barrier=fence WAY in the OSM tree, points =
    [(x, y)] in patch coordinates (the whole way, also outside the patch, so a fence
    reaching over the border is still divided correctly).

    Only ways are read: a fence mapped on a node is a single point and has nothing to
    build, and a way tagged area=yes is the OUTLINE of an area, not a run of fence."""
    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}

    out = []
    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if tags.get("barrier") != "fence":
            continue
        if (tags.get("area") or "").strip().lower() == "yes":
            continue
        pts = []
        for nd in w.findall("nd"):
            nid = nd.get("ref")
            if nid not in node_coords:
                continue
            lat, lon = node_coords[nid]
            pts.append(projector.project(lat, lon))
        if len(pts) < 2:
            continue
        # the OSM way id travels with the points: it is what tells the neighbouring patch
        # WHICH post of WHICH fence has already been built at the border
        out.append((w.get("id") or "", pts))
    return out


# ---------------------------------------------------------------------------
# Getting the fences into the patch OSM - ON DEMAND ONLY
# ---------------------------------------------------------------------------
# barrier=fence is an extremely common tag, so it is deliberately NOT part of the main
# buildings query: somebody who does not use this feature never downloads a single byte
# extra. Only when the fences are actually being built does each patch get checked - and
# only if its map_<patch>.osm holds no fence yet, ONE tiny extra Overpass query is sent
# and its result merged into that file. A patch which genuinely has no fence gets an
# invisible marker (an XML comment) saying "already asked", so it is not asked again.
_LINE_MARKS = (b'"fence"',)
_FETCH_MARK = b"condor_fences_fetched_v1"
_FETCH_COMMENT = "  <!-- condor_fences_fetched_v1 -->"


def _osm_scan(osm_path):
    """(has_fence, already_asked) for a patch OSM - a cheap CHUNKED TEXT scan, NOT an XML
    parse. This runs for every patch of the range, so it must stay fast: a patch with no
    fence must never cost more than reading its file through once."""
    has_line = marked = False
    try:
        tail = b""
        with open(osm_path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                buf = tail + chunk
                if not has_line and any(m in buf for m in _LINE_MARKS):
                    has_line = True
                if not marked and _FETCH_MARK in buf:
                    marked = True
                if has_line and marked:
                    break
                tail = buf[-64:]          # a marker split across two chunks
    except OSError as e:
        logger.warning("fences: cannot read %s: %s", osm_path, e)
    return has_line, marked


def _fence_query(lat_min, lat_max, lon_min, lon_max):
    """Overpass query for the fences alone (tiny): the ways plus the nodes they are made
    of through the recursion down."""
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    return ("[out:xml][timeout:60];\n(\n"
            f'  way["barrier"="fence"]({bbox});\n'
            ");\n(._;>;);\nout body;")


def _merge_osm_elements(osm_path, content):
    """Add the fetched node/way elements to an existing map_<patch>.osm: only those whose
    id is not in the file yet, the rest of the file stays exactly as it was. The 'already
    asked' marker goes right behind the <osm ...> header. Everything is written to a
    TEMPORARY file first and only then moved over the original, so a failure can never
    damage the OSM that is already there. Returns how many elements were added, or None
    when nothing could be done."""
    import re
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(content)
    except Exception as e:
        logger.warning("fences: the Overpass answer could not be parsed: %s", e)
        return None
    try:
        with open(osm_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        logger.warning("fences: cannot read %s: %s", osm_path, e)
        return None

    close = text.rfind("</osm>")
    if close < 0:
        logger.warning("fences: %s is not a complete OSM file - left untouched",
                       os.path.basename(osm_path))
        return None
    have = set(re.findall(r'<(?:node|way)\s+id="(\d+)"', text))
    add = []
    for el in list(root.findall("node")) + list(root.findall("way")):
        eid = el.get("id")
        if not eid or eid in have:
            continue
        add.append("  " + ET.tostring(el, encoding="unicode").strip())

    if _FETCH_MARK.decode() not in text:
        head = text.find("<osm")
        head_end = text.find(">", head) if head >= 0 else -1
        if head_end > 0:
            text = text[:head_end + 1] + "\n" + _FETCH_COMMENT + text[head_end + 1:]
            close = text.rfind("</osm>")
    if add:
        text = text[:close] + "\n".join(add) + "\n" + text[close:]

    tmp = osm_path + ".fence_tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, osm_path)
    except OSError as e:
        logger.warning("fences: %s could not be updated: %s", os.path.basename(osm_path), e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return len(add)


def _fetch_fences_into_osm(paths, patch_id, osm_path):
    """Ask Overpass for JUST the fences of this patch and merge them into its
    map_<patch>.osm. The patch bounding box and the HTTP call are taken from the plugin's
    own osm_downloader, so the server list, the timeouts and the retries stay exactly the
    same as everywhere else. Never raises: without a connection it only logs a warning
    and the import carries on."""
    try:
        from ..io.patch_metadata import load_patch_metadata
        from . import osm_downloader as _osm
        txt_path = next((p for p in (
            os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
            os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
        if not txt_path:
            return False
        meta = load_patch_metadata(txt_path)
        content = _osm._overpass_fetch(_fence_query(
            meta.lat_min, meta.lat_max, meta.lon_min, meta.lon_max))
        if content is None:
            # a failed fetch writes NO marker, so it is tried again next time
            logger.warning("fences: the Overpass query for patch %s failed - the fences "
                           "are skipped for now", patch_id)
            return False
        n = _merge_osm_elements(osm_path, content)
        if n is None:
            return False
        print(f"[fences] patch {patch_id}: {n} fence element(s) added to "
              f"map_{patch_id}.osm")
        return True
    except Exception as e:
        logger.warning("fences: fetching the fences for %s failed: %s", patch_id, e)
        return False


def _ensure_fence_data(paths, patch_id, osm_path):
    """Make sure the patch OSM holds its fences. Returns (has_fences, fetched).
    Cheap for a patch that has none: one text scan, and the Overpass query only the FIRST
    time (afterwards the marker in the file says it has been asked already)."""
    has_fences, marked = _osm_scan(osm_path)
    if marked:
        return has_fences, False
    if not _fetch_fences_into_osm(paths, patch_id, osm_path):
        return False, False
    has_fences, _m = _osm_scan(osm_path)
    return has_fences, True


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _make_terrain_z(terrain):
    """Closure returning terrain elevation at (x, y), clamped to the patch edge."""
    from ..models.geometry import Point2D, BBox

    def z_at(x, y):
        bb = BBox(x - 0.5, y - 0.5, x + 0.5, y + 0.5)
        for ti in terrain.get_triangles_in_bbox(bb):
            tri = terrain.triangles[ti]
            if tri.contains_point_2d(Point2D(x, y)):
                z = tri.z_at_xy(x, y)
                if z is not None:
                    return z
        return terrain.z_min

    return z_at


def _dedupe(pts):
    """Drop points that repeat the previous one - two identical neighbours would give a
    segment of zero length and a post built twice in the same spot."""
    out = []
    for p in pts:
        if out and abs(p[0] - out[-1][0]) < 1e-6 and abs(p[1] - out[-1][1]) < 1e-6:
            continue
        out.append(p)
    return out


def _posts_on_segment(p0, p1, target=SPACING_DEFAULT):
    """The post positions between p0 and p1, p0 included, p1 NOT.

    The gaps are `target` metres, and NO gap is ever longer than that: the number of gaps
    is rounded UP. What is left over is shared equally between the TWO END gaps, so the
    stretch stays symmetrical and no short stub is left at either end.
      10 m -> 3 + 4 + 3
      14 m -> 3 + 4 + 4 + 3"""
    d = math.dist(p0, p1)
    if d < 1e-9:
        return [p0]
    n = max(1, int(math.ceil(d / target - 1e-9)))     # gaps, none longer than target
    if n == 1:
        return [p0]
    end = (d - (n - 2) * target) / 2.0                # the two outer gaps
    out = [p0]
    s = end
    for _ in range(n - 1):
        t = s / d
        out.append((p0[0] + (p1[0] - p0[0]) * t,
                    p0[1] + (p1[1] - p0[1]) * t))
        s += target
    return out


def _fence_posts(way, target=SPACING_DEFAULT):
    """Every post of one fence line: a post in EVERY vertex of the way (so a corner
    always gets one) and the stretches between the vertices divided per SEGMENT, not by
    stepping along the whole line - that is what keeps a corner from being doubled."""
    pts = []
    for a, b in zip(way[:-1], way[1:]):
        pts += _posts_on_segment(a, b, target)
    pts.append(way[-1])
    return pts


def _tangents(pts):
    """The direction of the fence in every post. On a straight stretch it is simply the
    direction of the segment; in a corner it is the AVERAGE of the two segments (a mitre),
    so the wire coming in and the wire going out share exactly the same cross-section and
    no gap opens up in the corner."""
    dirs = []
    for i in range(len(pts) - 1):
        dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        L = math.hypot(dx, dy) or 1.0
        dirs.append((dx / L, dy / L))
    out = []
    for i in range(len(pts)):
        a = dirs[i - 1] if i > 0 else None
        b = dirs[i] if i < len(dirs) else None
        if a is None:
            out.append(b)
        elif b is None:
            out.append(a)
        else:
            tx, ty = a[0] + b[0], a[1] + b[1]
            L = math.hypot(tx, ty)
            # L ~ 0 means the fence doubles straight back on itself - then there is no
            # sensible mitre and the outgoing direction is used
            out.append((tx / L, ty / L) if L > 1e-9 else b)
    return out


_POST_TEMPLATE = None


def _post_template():
    """The fence_post.obj model, read once: (verts, uvs, normals, faces) with ONE UV and
    ONE NORMAL per vertex, which is what the mesh builder needs. The model's OWN normals
    are kept, so the post stays SMOOTH-shaded (the 's 1' in the file) both in Blender and
    in the exported OBJ - exactly like the pylons. Its origin is at ground level. None when
    the file is missing - then no post is built and a warning is logged."""
    global _POST_TEMPLATE
    if _POST_TEMPLATE is not None:
        return _POST_TEMPLATE if _POST_TEMPLATE is not False else None

    path = os.path.join(_ASSETS, POST_MODEL)
    if not os.path.exists(path):
        logger.warning("fences: model %s not found in assets/pylons", POST_MODEL)
        _POST_TEMPLATE = False
        return None
    verts, uvs, norms, faces = [], [], [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("v "):
                    p = line.split()
                    verts.append((float(p[1]), float(p[2]), float(p[3])))
                elif line.startswith("vt "):
                    p = line.split()
                    uvs.append((float(p[1]), float(p[2])))
                elif line.startswith("vn "):
                    p = line.split()
                    norms.append((float(p[1]), float(p[2]), float(p[3])))
                elif line.startswith("f "):
                    vi, ti, ni = [], [], []
                    for tok in line.split()[1:]:
                        bits = tok.split("/")
                        vi.append(int(bits[0]) - 1)
                        ti.append(int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1)
                        ni.append(int(bits[2]) - 1 if len(bits) > 2 and bits[2] else -1)
                    faces.append((vi, ti, ni))
    except Exception as e:
        logger.warning("fences: model %s could not be read: %s", POST_MODEL, e)
        _POST_TEMPLATE = False
        return None
    if not verts or not faces:
        _POST_TEMPLATE = False
        return None

    # one vertex per (v, vt, vn) triple - the same flattening the tree models get, only
    # with the normal carried along as well
    out_v, out_uv, out_n, out_f, seen = [], [], [], [], {}
    for (vi, ti, ni) in faces:
        face = []
        for k, v in enumerate(vi):
            t, n = ti[k], ni[k]
            key = (v, t, n)
            if key not in seen:
                seen[key] = len(out_v)
                out_v.append(verts[v])
                out_uv.append(uvs[t] if 0 <= t < len(uvs) else (0.0, 0.0))
                out_n.append(norms[n] if 0 <= n < len(norms) else None)
            face.append(seen[key])
        out_f.append(tuple(face))
    _POST_TEMPLATE = (out_v, out_uv, out_n, out_f)
    return _POST_TEMPLATE


def _add_post(part, x, y, z_ground):
    """One post: the fence_post.obj model dropped at (x, y) with its origin on the
    terrain. It brings its own UVs and its own normals, so it keeps the smooth shading it
    was modelled with."""
    tmpl = _post_template()
    if tmpl is None:
        return
    mv, muv, mn, mf = tmpl
    verts, faces, uvs, norms = part
    base = len(verts)
    for (vx, vy, vz) in mv:
        verts.append((x + vx, y + vy, z_ground + vz))
    uvs.extend(muv)
    norms.extend(mn)
    for face in mf:
        faces.append(tuple(i + base for i in face))


def _wire_section(p, t, z):
    """One cross-section of a wire at point p, in the plane perpendicular to the direction
    t: the three corners of the triangle plus the OUTWARD normal at each of them. The
    triangle always stands the same way up: one edge horizontal at the bottom, the apex on
    top. The normals point away from the wire's axis, which is what makes the wire come out
    smooth (round-looking) instead of faceted."""
    nx, ny = -t[1], t[0]                       # horizontal, perpendicular to the fence
    pts = ((p[0] - nx * WIRE_HALF, p[1] - ny * WIRE_HALF, z - WIRE_IN),
           (p[0] + nx * WIRE_HALF, p[1] + ny * WIRE_HALF, z - WIRE_IN),
           (p[0], p[1], z + WIRE_OUT))
    nrm = []
    for c in pts:
        dx, dy, dz = c[0] - p[0], c[1] - p[1], c[2] - z
        L = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        nrm.append((dx / L, dy / L, dz / L))
    return pts, tuple(nrm)


def _add_wire_segment(part, sa, sb):
    """One straight piece of wire between two cross-sections: three quads, each with its
    own four vertices so the UV stays clean. The wire runs THROUGH the posts, it does not
    stop at them."""
    verts, faces, uvs, norms = part
    (pa, na), (pb, nb) = sa, sb
    du = (UV_WIRE_U1 - UV_WIRE_U0) / 3.0
    for k in range(3):
        k2 = (k + 1) % 3
        u0 = UV_WIRE_U0 + k * du
        u1 = u0 + du
        b = len(verts)
        verts.append(pa[k]);  uvs.append((u0, UV_WIRE_V0)); norms.append(na[k])
        verts.append(pa[k2]); uvs.append((u1, UV_WIRE_V0)); norms.append(na[k2])
        verts.append(pb[k2]); uvs.append((u1, UV_WIRE_V1)); norms.append(nb[k2])
        verts.append(pb[k]);  uvs.append((u0, UV_WIRE_V1)); norms.append(nb[k])
        faces.append((b, b + 1, b + 2, b + 3))


def _straight_run(pts):
    """The same polyline with every point that only continues STRAIGHT ON dropped.

    A point is kept when the wire really bends there - in plan OR in height. So five posts
    whose middle three are at one height and in one line give ONE piece of wire through
    those three, with a joint only at the two outer ones, where it starts to climb or
    fall. On flat straight ground the whole run comes out as a single piece."""
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay, az = (pts[i][0] - out[-1][0], pts[i][1] - out[-1][1], pts[i][2] - out[-1][2])
        bx, by, bz = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1],
                      pts[i + 1][2] - pts[i][2])
        la = math.sqrt(ax * ax + ay * ay + az * az)
        lb = math.sqrt(bx * bx + by * by + bz * bz)
        if la < 1e-9 or lb < 1e-9:
            continue
        # same direction? compare the two unit vectors
        if (abs(ax / la - bx / lb) < 1e-6 and abs(ay / la - by / lb) < 1e-6
                and abs(az / la - bz / lb) < 1e-6):
            continue                       # goes straight on - no joint needed here
        out.append(pts[i])
    out.append(pts[-1])
    return out


def _add_wire_run(part, pts):
    """One wire along a whole run of posts - straight lines, no sag, and a joint ONLY
    where it actually bends (see _straight_run)."""
    keep = _straight_run(pts)
    if len(keep) < 2:
        return
    tang = _tangents(keep)
    secs = [_wire_section(p, tang[i], p[2]) for i, p in enumerate(keep)]
    for i in range(len(secs) - 1):
        _add_wire_segment(part, secs[i], secs[i + 1])


# ---------------------------------------------------------------------------
# Posts on the patch border - Working/Autogen/fences_border.txt
# ---------------------------------------------------------------------------
# A fence does not stop at the edge of a patch. When it runs out of the square being
# built, ONE more post is put up just beyond the edge, so the wire has something to end
# in - and its height is taken from the NEIGHBOUR's terrain, not from this patch's, or it
# would stand at the wrong level.
#
# That post is written down, because the neighbouring square would otherwise build it a
# second time in the same spot. Each row says which square the post belongs to, in THAT
# square's own coordinates, so the neighbour reads it and uses it as it is:
#
#   <for>  <from>  <osm way>  <post no.>  <x>  <y>  <z>
#   036020 036019  123456789  17          -2860.412  2874.905  318.62
#
# A square can spill over all four of its sides at once, so every such post gets a row.
# Rebuilding a square first throws away the rows it wrote before, so nothing piles up.
BORDER_FILE = "fences_border.txt"


def _patch_xy(patch_id):
    """(x, y) numbers of a patch name, or None when it is not a 6-digit id."""
    if not patch_id or len(patch_id) != 6 or not patch_id.isdigit():
        return None
    return int(patch_id[:3]), int(patch_id[3:])


def _neighbour_for(patch_id, x, y):
    """Which patch a point outside this one falls into, and its coordinates THERE.

    The patches are laid out so that a rising x number goes towards -X in the scene and a
    rising y number towards +Y (the same layout the buildings import uses), hence the
    signs below. Returns (patch_id, x, y) or None when the point is inside after all."""
    pxy = _patch_xy(patch_id)
    if pxy is None:
        return None
    px, py = pxy
    nx, ny = x, y
    # the same half-open border as `inside` in _build_fences: exactly ON the line already
    # counts as the next square
    if x >= PATCH_HALF:
        px -= 1; nx = x - PATCH_SIZE
    elif x < -PATCH_HALF:
        px += 1; nx = x + PATCH_SIZE
    if y >= PATCH_HALF:
        py += 1; ny = y - PATCH_SIZE
    elif y < -PATCH_HALF:
        py -= 1; ny = y + PATCH_SIZE
    if (px, py) == (int(patch_id[:3]), int(patch_id[3:])):
        return None
    if not (0 <= px <= 999 and 0 <= py <= 999):
        return None
    return f"{px:03d}{py:03d}", nx, ny


_NB_TERRAIN = {}


def _neighbour_z(paths, patch_id, x, y):
    """Terrain height at (x, y) in the neighbouring patch `patch_id`, read from ITS OWN
    height map. None when that patch has no terrain here (the edge of the landscape)."""
    if patch_id not in _NB_TERRAIN:
        terrain = None
        try:
            from .terrain_smooth import load_terrain_smoothed, resolve_smooth_or_source
            if os.path.exists(resolve_smooth_or_source(paths['heightmaps'], patch_id)):
                terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
        except Exception as e:
            logger.warning("fences: terrain of the neighbour %s could not be read: %s",
                           patch_id, e)
        _NB_TERRAIN[patch_id] = terrain
    terrain = _NB_TERRAIN[patch_id]
    if terrain is None:
        return None
    return _make_terrain_z(terrain)(x, y)


def _border_path(paths):
    return os.path.join(paths['autogen'], BORDER_FILE)


def _read_border(paths):
    """The whole border list as {(for_patch, way_id, post_no): (x, y, z, built_by)}."""
    out = {}
    path = _border_path(paths)
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                p = line.split()
                if len(p) != 7 or line.lstrip().startswith("#"):
                    continue
                try:
                    out[(p[0], p[2], int(p[3]))] = (float(p[4]), float(p[5]),
                                                    float(p[6]), p[1])
                except ValueError:
                    continue
    except OSError as e:
        logger.warning("fences: %s could not be read: %s", BORDER_FILE, e)
    return out


def _write_border(paths, built_by, rows):
    """Replace everything patch `built_by` wrote earlier with `rows`, a list of
    (for_patch, way_id, post_no, x, y, z). Written through a temporary file, so a failure
    cannot damage what is already there."""
    path = _border_path(paths)
    keep = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    p = line.split()
                    if len(p) == 7 and p[1] == built_by:
                        continue              # this patch's own old rows go away
                    if line.strip():
                        keep.append(line.rstrip("\n"))
        except OSError as e:
            logger.warning("fences: %s could not be read: %s", BORDER_FILE, e)
            return
    for (for_patch, way_id, no, x, y, z) in rows:
        keep.append("%s %s %s %d %.3f %.3f %.3f" % (for_patch, built_by, way_id, no, x, y, z))
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(keep) + ("\n" if keep else ""))
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("fences: %s could not be written: %s", BORDER_FILE, e)
        try:
            os.remove(tmp)
        except OSError:
            pass


def _build_fences(ways, z_at, spacing, paths=None, patch_id=None):
    """Build the geometry of all fences of one patch.
    Returns (parts, n_fences, n_posts), parts = [[verts, faces, uvs, normals], ...] - ONE
    entry per fence line from OSM, i.e. one fence = one object.

    Where a fence leaves the patch, ONE post is still built just beyond the edge (its
    height read from the NEIGHBOUR's terrain) so the wire has something to end in, and
    that post is written into fences_border.txt. A post the neighbour already put up this
    way is NOT built again - it is only read back from that file and the wire is strung to
    it, so nothing stands in the same spot twice."""
    parts = []
    n_fences = n_posts = 0
    spacing = max(SPACING_MIN, min(SPACING_MAX, float(spacing)))
    known = _read_border(paths) if (paths and patch_id) else {}
    new_rows = []

    for (way_id, way) in ways:
        line = _dedupe(way)
        if len(line) < 2:
            continue
        posts = _fence_posts(line, spacing)
        if len(posts) < 2:
            continue
        # Half-open on purpose: a post landing EXACTLY on the border line belongs to the
        # next square only, never to both, or the two of them would each build it.
        inside = [(-PATCH_HALF <= p[0] < PATCH_HALF and -PATCH_HALF <= p[1] < PATCH_HALF)
                  for p in posts]
        if not any(inside):
            continue
        # A fence around a field comes back to where it started, so the first and the last
        # post are the same spot: the prism is built once, but the last point is still
        # needed as the end of the wire that closes the loop.
        closed = (len(posts) > 2
                  and abs(posts[0][0] - posts[-1][0]) < 1e-6
                  and abs(posts[0][1] - posts[-1][1]) < 1e-6)
        # --- who builds what ------------------------------------------------------------
        # Two squares meet at every border, and both of them read the same fence, so it has
        # to be settled without argument which of them puts up what. Both work it out from
        # the same line, so they always agree:
        #
        #   home(i)   the square post i falls into
        #   a POST    is built by the square holding the PREVIOUS post (the first post by
        #             its own square). So the square whose fence runs out puts up ONE more
        #             post beyond its edge - at the NEIGHBOUR's terrain height - and the
        #             neighbour, applying the same rule, leaves that one alone.
        #   a WIRE    between post i and i+1 is built by the square holding post i. The
        #             span crossing the border therefore exists exactly once.
        #
        # A fence can leave the square through all four sides at once and come back again;
        # this is decided per post, so all of that is covered.
        home = []
        for i, p in enumerate(posts):
            if inside[i]:
                home.append(patch_id)
            else:
                nb = _neighbour_for(patch_id, p[0], p[1]) if patch_id else None
                home.append(nb[0] if nb else None)

        zg = [None] * len(posts)
        build = [False] * len(posts)
        for i, p in enumerate(posts):
            # a height is needed for every post the wire can reach: ours, and the one
            # immediately beyond the edge at either end of a run
            near = inside[i] or (i > 0 and inside[i - 1]) or (i + 1 < len(posts)
                                                              and inside[i + 1])
            if not near:
                continue
            owner = home[i - 1] if i > 0 else home[i]
            if inside[i]:
                zg[i] = z_at(p[0], p[1])
                build[i] = (owner == patch_id)
                continue
            nb = _neighbour_for(patch_id, p[0], p[1]) if patch_id else None
            if nb is None:
                continue
            nb_id, nx, ny = nb
            z = _neighbour_z(paths, nb_id, nx, ny) if paths else None
            if z is None:
                continue                        # no terrain over there to stand on
            zg[i] = z
            if owner == patch_id:
                build[i] = True
                new_rows.append((nb_id, way_id, i, nx, ny, z))

        # one fence line = one object, so this line gets a part of its own
        # (verts, faces, uvs, normals - the normals keep the smooth shading)
        part = [[], [], [], []]
        for i, p in enumerate(posts):
            if not build[i]:
                continue
            if closed and i == len(posts) - 1:
                continue                   # the closing post is the first one again
            _add_post(part, p[0], p[1], zg[i])
            n_posts += 1
            if n_posts >= MAX_POSTS:
                break

        # The wires are strung over a whole RUN of posts at once, not from post to post, so
        # a straight stretch comes out as one piece (see _straight_run). A run is broken
        # wherever the next span belongs to the neighbour.
        runs, cur = [], []
        for i in range(len(posts)):
            mine = (zg[i] is not None and i + 1 < len(posts) and zg[i + 1] is not None
                    and home[i] == patch_id)
            if mine:
                if not cur:
                    cur = [i]
                cur.append(i + 1)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for run in runs:
            if len(run) < 2:
                continue
            for wh in WIRE_HEIGHTS:
                _add_wire_run(part, [(posts[i][0], posts[i][1], zg[i] + wh)
                                     for i in run])
        if part[0]:
            parts.append(part)
            n_fences += 1
        if n_posts >= MAX_POSTS:
            logger.warning("fences: stopped at %d posts (MAX_POSTS)", MAX_POSTS)
            break

    # Hand the posts we put up beyond our edges over to the neighbours. Our own older rows
    # are thrown away first, so rebuilding a square does not pile them up - and an empty
    # list is written just as well, which is what clears them when a fence disappears.
    if paths and patch_id:
        _write_border(paths, patch_id, new_rows)
    return parts, n_fences, n_posts


def _shade_smooth(mesh, norms):
    """Smooth-shade the whole mesh and give it the normals the geometry was built with -
    the post's own ones from fence_post.obj, the wire's radiating out from its axis. So
    both come out round instead of faceted, the same way the pylons do.

    Custom split normals are set when the Blender version takes them; on any failure the
    plain smooth shading stays, which already rounds both shapes off."""
    for poly in mesh.polygons:
        poly.use_smooth = True
    if not norms or any(n is None for n in norms):
        return
    try:
        mesh.normals_split_custom_set_from_vertices([tuple(n) for n in norms])
    except Exception:
        try:                                   # Blender 3.6-4.0 also wants this flag
            mesh.use_auto_smooth = True
            mesh.normals_split_custom_set_from_vertices([tuple(n) for n in norms])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Blender material
# ---------------------------------------------------------------------------
def _get_material():
    """The 'pylones' material - THE SAME ONE the powerlines and the aerialways use, so the
    fence rides on Pylons.dds. When it is already in the file (the powerlines built it, or
    the user edited it) it is taken as it is; otherwise it is created here."""
    mat = bpy.data.materials.get(MAT_NAME)
    if mat is not None:
        return mat                        # powerlines / the user's material -> use as-is
    mat = bpy.data.materials.new(MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    img_path = os.path.join(_ASSETS, TEX_FILE)
    if not os.path.exists(img_path):
        logger.warning("fences: texture %s not found in assets/pylons", TEX_FILE)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (0.45, 0.45, 0.45, 1.0)
        return mat
    try:
        img = bpy.data.images.load(img_path, check_existing=True)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        if bsdf is not None:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    except Exception as e:
        logger.warning("fences: texture %s could not be loaded: %s", TEX_FILE, e)
    return mat


def _copy_fence_texture(paths):
    """Copy Pylons.dds next to the patch OBJs (autogen/Textures) so Condor finds it, in
    case the powerlines are off and nobody else copied it."""
    src = os.path.join(_ASSETS, TEX_FILE)
    if not os.path.exists(src):
        logger.warning("fences: texture %s missing - nothing copied to Textures", TEX_FILE)
        return
    try:
        import shutil
        tdir = os.path.join(paths['autogen'], "Textures")
        os.makedirs(tdir, exist_ok=True)
        dst = os.path.join(tdir, TEX_FILE)
        if not os.path.exists(dst):
            shutil.copyfile(src, dst)
    except Exception as e:
        print(f"[fences] texture copy failed: {e}")


# ---------------------------------------------------------------------------
# Operator: Import Fences
# ---------------------------------------------------------------------------
class CONDOR_OT_import_fences(Operator):
    bl_idname = "condor.import_fences"
    bl_label = "Import Fences"
    bl_description = ("Generate fences from OSM (barrier=fence) as 'fence' objects - an "
                      "octagonal post every few metres plus two wires")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        # in Batch (file mode) the fences are written into the OBJ by the exporter,
        # so the manual Blender import is disabled (like the tree rows Import)
        return (bool(props.condor_path) and props.landscape_name != 'NONE'
                and not getattr(context.scene, "condor_fence_batch", False))

    def execute(self, context):
        import xml.etree.ElementTree as ET
        from .operators import resolve_condor_paths, ensure_patch_osm
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        spacing = _spacing()

        if props.single_patch_mode and props.patch_id:
            patch_ids = [str(props.patch_id)]
        else:
            patch_ids = [f"{x:03d}{y:03d}"
                         for x in range(props.patch_x_min, props.patch_x_max + 1)
                         for y in range(props.patch_y_min, props.patch_y_max + 1)]

        total_fences = total_posts = total_existing = total_fetched = 0
        missing_osm = []
        for patch_id in patch_ids:
            osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
            if not ensure_patch_osm(paths, patch_id, True):
                missing_osm.append(patch_id)
                continue
            # THE ORDER MATTERS. A patch with no fence must cost almost nothing - the whole
            # range is walked through, so nothing expensive (XML parse, and above all
            # loading the terrain) may happen before it is clear there is anything to build.
            has_fences, fetched = _ensure_fence_data(paths, patch_id, osm_path)
            if fetched:
                total_fetched += 1
            if not has_fences:
                continue
            txt_path = next((p for p in (
                os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
            if not txt_path:
                continue
            # the projector is needed for the parsing itself, the terrain only later
            try:
                meta = load_patch_metadata(txt_path)
                projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x,
                                                        meta.translate_y)
                root = ET.parse(osm_path).getroot()
            except Exception as e:
                logger.warning("fences: setup failed for %s: %s", patch_id, e)
                continue

            ways = _parse_fences(root, projector)
            if not ways:
                continue

            # only NOW, when it is certain something will be built, the terrain is loaded
            from .terrain_smooth import load_terrain_smoothed, resolve_smooth_or_source
            terrain_file = resolve_smooth_or_source(paths['heightmaps'], patch_id)
            if not os.path.exists(terrain_file):
                continue
            try:
                terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
            except Exception as e:
                logger.warning("fences: terrain load failed for %s: %s", patch_id, e)
                continue
            z_at = _make_terrain_z(terrain)

            # Which LODs to build into: the fence is the same in both, only the target
            # collection differs.
            lod_variants = []
            if props.output_lod in ('LOD0', 'BOTH'):
                lod_variants.append("")
            if props.output_lod in ('LOD1', 'BOTH'):
                lod_variants.append("_LOD1")
            if not lod_variants:
                lod_variants.append("")

            for vi, suffix in enumerate(lod_variants):
                # already present for THIS patch + LOD? -> skip so fences aren't duplicated
                _pcol = bpy.data.collections.get(
                    f"Condor_{props.landscape_name}_{patch_id}{suffix}")
                if _pcol is not None and any(_is_fence_name(o.name)
                                             for o in _pcol.all_objects):
                    total_existing += 1
                    continue

                parts, nfc, npo = _build_fences(ways, z_at, spacing, paths, patch_id)
                if vi == 0:
                    total_fences += nfc
                    total_posts += npo
                if not parts:
                    continue

                # one fence line = one object; Blender keeps them apart with its own
                # .001 / .002 suffix, and _is_fence_name looks past that
                for (verts, faces, uvs, norms) in parts:
                    obj_name = OBJECT_NAME
                    mesh = bpy.data.meshes.new(obj_name)
                    mesh.from_pydata([tuple(v) for v in verts], [],
                                     [list(f) for f in faces])
                    mesh.update()
                    uvl = mesh.uv_layers.new(name="UVMap")
                    for loop in mesh.loops:
                        uvl.data[loop.index].uv = uvs[loop.vertex_index]
                    _shade_smooth(mesh, norms)
                    ob = bpy.data.objects.new(obj_name, mesh)
                    ob["patch_id"] = patch_id
                    ob["lod"] = suffix
                    ob.data.materials.append(_get_material())

                    # patch offset (same layout the buildings import uses)
                    px, py = int(patch_id[:3]), int(patch_id[3:])
                    if not props.single_patch_mode:
                        ob.location = (-(px - props.patch_x_min) * PATCH_SIZE,
                                       (py - props.patch_y_min) * PATCH_SIZE, 0.0)

                    col_name = f"Condor_{props.landscape_name}_{patch_id}{suffix}"
                    col = bpy.data.collections.get(col_name)
                    if col is None:
                        col = bpy.data.collections.new(col_name)
                        try:
                            context.scene.collection.children.link(col)
                        except Exception:
                            pass
                    col.objects.link(ob)

        # copy fence.dds into Working/Autogen/Textures so Condor (and a re-import of the
        # OBJ) finds it - same as the Batch/file-mode path does
        if total_posts > 0:
            _copy_fence_texture(paths)

        if total_posts == 0 and total_existing == 0:
            # Say WHY nothing appeared - silently doing nothing looks like a broken button.
            msg = ("Fences: nothing built - no barrier=fence is mapped in OSM for the "
                   f"selected patch(es) ({len(patch_ids)} checked")
            msg += (f", {total_fetched} additionally queried on Overpass)"
                    if total_fetched else ")")
            if missing_osm:
                msg += f" | OSM missing (skipped): {', '.join(missing_osm)}"
            self.report({'WARNING'}, msg)
            return {'FINISHED'}

        msg = (f"Fences: {total_posts} posts in {total_fences} fence(s), "
               f"post spacing {spacing:.1f} m")
        if total_fetched:
            msg += f", fences downloaded for {total_fetched} patch(es)"
        if total_existing:
            msg += f", already imported {total_existing} (skipped)"
        if missing_osm:
            msg += f" | OSM missing (skipped): {', '.join(missing_osm)}"
        self.report({'INFO'} if not missing_osm else {'WARNING'}, msg)
        return {'FINISHED'}


class CONDOR_OT_merge_fences(Operator):
    bl_idname = "condor.merge_fences"
    bl_label = "Merge Fences"
    bl_description = ("Join the fence objects of each patch into one 'fence' object with "
                      "the single 'pylones' material. Your own edits are kept")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(_fence_objects())

    def execute(self, context):
        groups = {}                     # (collection, lod) -> [objects]
        for ob in _fence_objects():
            col = ob.users_collection[0].name if ob.users_collection else ""
            groups.setdefault((col, ob.get("lod", "")), []).append(ob)
        if not groups:
            self.report({'WARNING'}, "Fences: nothing to merge.")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mat = _get_material()
        merged = 0
        for (col_name, _lod), objs in groups.items():
            objs.sort(key=lambda o: o.name)
            # everything into ONE material slot before joining, so the result has one
            for ob in objs:
                ob.data.materials.clear()
                ob.data.materials.append(mat)
            target = objs[0]
            if len(objs) > 1:
                try:
                    with context.temp_override(active_object=target,
                                               selected_editable_objects=objs):
                        bpy.ops.object.join()
                except Exception as e:
                    logger.warning("fences: merge failed in %s: %s", col_name, e)
                    continue
            target.name = OBJECT_NAME
            target.data.name = OBJECT_NAME
            merged += 1

        self.report({'INFO'}, f"Fences: merged into {merged} object(s), "
                              f"material '{MAT_NAME}'.")
        return {'FINISHED'}


def _is_fence_name(name):
    """True for 'fence' (also with Blender's own .001 / .002 suffix, which it puts on the
    second and every further fence of a patch)."""
    return (name or "").lower().split('.')[0] == OBJECT_NAME


def _fence_objects():
    """The fence meshes in the scene."""
    return [o for o in bpy.data.objects
            if o.type == 'MESH' and _is_fence_name(o.name)]


# ---------------------------------------------------------------------------
# File mode (Batch): write the fences straight into each patch OBJ (LOD0 + LOD1)
# ---------------------------------------------------------------------------
# One-slot cache so a patch's setup (OSM parse + terrain) is done ONCE and reused for
# BOTH LOD0 and LOD1 (two separate exporter calls). Keyed by patch + the OSM file's
# mtime, so an edited OSM is re-read. A patch with NO fence is cached too (_NOTHING), so
# the second LOD does not go looking again.
_NOTHING = object()
_SETUP_CACHE = {"patch": None, "key": None, "setup": None}


def _patch_setup(paths, patch_id):
    """Return (ways, z_at) for a patch, or None. Cached per patch (one slot) so LOD0 and
    LOD1 share the same single parse / terrain load. Same order as the Import: nothing
    expensive - above all NOT the terrain - happens before it is clear the patch really
    has fences."""
    import xml.etree.ElementTree as ET
    from ..projection.transverse_mercator import TransverseMercatorProjector
    from ..io.patch_metadata import load_patch_metadata

    osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
    if not os.path.exists(osm_path):
        return None
    try:
        key = os.path.getmtime(osm_path)
    except OSError:
        key = None
    c = _SETUP_CACHE
    if c["patch"] == patch_id and c["key"] == key and c["setup"] is not None:
        return None if c["setup"] is _NOTHING else c["setup"]

    def _remember(setup):
        # the OSM may have just been extended by the fences -> re-read its mtime
        try:
            k = os.path.getmtime(osm_path)
        except OSError:
            k = key
        c["patch"], c["key"], c["setup"] = patch_id, k, setup
        return None if setup is _NOTHING else setup

    has_fences, _fetched = _ensure_fence_data(paths, patch_id, osm_path)
    if not has_fences:
        return _remember(_NOTHING)

    txt_path = next((p for p in (
        os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
        os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
    if not txt_path:
        return _remember(_NOTHING)
    try:
        meta = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x,
                                                meta.translate_y)
        root = ET.parse(osm_path).getroot()
    except Exception as e:
        logger.warning("fences: file-mode setup failed for %s: %s", patch_id, e)
        return _remember(_NOTHING)

    ways = _parse_fences(root, projector)
    if not ways:
        return _remember(_NOTHING)

    # only NOW, when it is certain something will be built, the terrain is loaded
    from .terrain_smooth import load_terrain_smoothed, resolve_smooth_or_source
    terrain_file = resolve_smooth_or_source(paths['heightmaps'], patch_id)
    if not os.path.exists(terrain_file):
        return _remember(_NOTHING)
    try:
        terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
    except Exception as e:
        logger.warning("fences: terrain load failed for %s: %s", patch_id, e)
        return _remember(_NOTHING)
    return _remember((ways, _make_terrain_z(terrain)))


def _generate_patch_geometry(paths, patch_id, spacing):
    """Build [[verts, faces, uvs], ...] of the fences for ONE patch in file mode, reusing
    the cached per-patch setup, or None if there is nothing."""
    setup = _patch_setup(paths, patch_id)
    if setup is None:
        return None
    ways, z_at = setup
    parts, nfc, npo = _build_fences(ways, z_at, spacing, paths, patch_id)
    if not parts:
        return None
    print(f"[fences] patch {patch_id}: {npo} post(s) in {nfc} fence(s)")
    return parts


def _append_fence_material(obj_path, texture_prefix):
    """Append the 'pylones' material to the patch .mtl (same block shape the exporter
    writes), pointing at Pylons.dds. When the powerlines already wrote it, nothing is
    added - the fence just uses that same block."""
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


def _append_fence_objects(obj_path, parts):
    """Append the fence objects to a file-mode patch OBJ, one 'o fence' block per fence
    line, keeping the SMOOTH per-vertex normals the geometry was built with. The file is
    read and written ONCE, however many fences there are. True when something was
    written."""
    from ..config import CONDOR_AXIS_SWAP
    from ..io.obj_exporter import _condor_xform

    try:
        lines = open(obj_path, "r", encoding="utf-8").read().split("\n")
    except OSError as e:
        logger.warning("fences: cannot read %s: %s", os.path.basename(obj_path), e)
        return False
    base_v = sum(1 for l in lines if l.startswith("v "))
    base_vt = sum(1 for l in lines if l.startswith("vt "))
    base_vn = sum(1 for l in lines if l.startswith("vn "))

    out = []
    for (verts, faces, uvs, norms) in parts:
        if not verts:
            continue
        wv = [_condor_xform(v, CONDOR_AXIS_SWAP) for v in verts]
        v_lines = ["v %.6f %.6f %.6f" % (w[0], w[1], w[2]) for w in wv]
        vt_lines = ["vt %.6f %.6f" % (u, vv) for (u, vv) in uvs]
        # ONE normal per vertex, the one the geometry was built with - so the OBJ carries
        # the same SMOOTH shading as Blender does (the post's own normals, the wire's
        # radiating out of its axis). They go through the same axis swap as the points.
        vn_lines = []
        for n in norms:
            if n is None:
                vn_lines.append("vn 0.000000 0.000000 1.000000")
                continue
            dx, dy, dz = _condor_xform(n, CONDOR_AXIS_SWAP)
            L = (dx * dx + dy * dy + dz * dz) ** 0.5 or 1.0
            vn_lines.append("vn %.6f %.6f %.6f" % (dx / L, dy / L, dz / L))
        tris = []
        for face in faces:
            fi = list(face)
            for k in range(1, len(fi) - 1):
                tris.append((fi[0], fi[k], fi[k + 1]))
        f_lines = ["f " + " ".join(
            f"{vi + base_v + 1}/{vi + base_vt + 1}/{vi + base_vn + 1}" for vi in tri)
            for tri in tris]
        out += (["", f"o {OBJECT_NAME}", f"usemtl {MAT_NAME}"]
                + v_lines + vt_lines + vn_lines + f_lines)
        base_v += len(v_lines)
        base_vt += len(vt_lines)
        base_vn += len(vn_lines)

    if not out:
        return False
    open(obj_path, "w", encoding="utf-8").write("\n".join(lines + out))
    return True


def _write_fences_into_obj(obj_path, paths, patch_id, spacing):
    """Append the fence objects to a file-mode patch OBJ and their material to the .mtl.
    Buildings / exporter untouched."""
    from ..config import CONDOR_TEXTURE_PREFIX
    parts = _generate_patch_geometry(paths, patch_id, spacing)
    if not parts:
        return
    if _append_fence_objects(obj_path, parts):
        _append_fence_material(obj_path, CONDOR_TEXTURE_PREFIX)
        _copy_fence_texture(paths)


def _append_fences_after_export(obj_filepath):
    """Called from the wrapped exporter: on Batch, write the fences into a file-mode patch
    OBJ. Both o<patch>.obj (LOD0) and o<patch>_LOD1.obj get the same fences."""
    import re
    if not getattr(bpy.context.scene, "condor_fence_batch", False):
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
    _write_fences_into_obj(obj_filepath, paths, patch_id, _spacing())


def _patch_obj_exporter():
    """Wrap obj_exporter.export_condor_obj_mtl so AFTER it writes a patch OBJ the fences
    are appended (file mode). The exporter source is NOT edited; removing this module
    restores the original behaviour."""
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_fence_export_patched", False):
        return
    _orig = _oe.export_condor_obj_mtl

    def _patched(groups, obj_filepath, texture_map, *a, **k):
        stats = _orig(groups, obj_filepath, texture_map, *a, **k)
        try:
            _append_fences_after_export(obj_filepath)
        except Exception as e:
            print(f"[fences] file-mode append failed: {e}")
        return stats

    _oe._fence_orig_export = _orig
    _oe.export_condor_obj_mtl = _patched
    _oe._fence_export_patched = True


def _unpatch_obj_exporter():
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_fence_export_patched", False):
        _oe.export_condor_obj_mtl = _oe._fence_orig_export
        _oe._fence_export_patched = False


def _patch_extra_obj_type():
    """Wrap operators._extra_obj_type so a separately imported 'fence' object counts as an
    EXTRA object (like the bridges / tree rows), not as a building. Without it a patch that
    has only the fences imported would look like its buildings are already there and the
    buildings generation would skip it. The operators source is NOT edited; removing this
    module restores the original function."""
    from . import operators as _op
    if getattr(_op, "_fence_type_patched", False):
        return
    _orig = _op._extra_obj_type

    def _patched(name):
        if (name or "").lower().startswith(OBJECT_NAME):
            return "fence"
        return _orig(name)

    _op._fence_orig_type = _orig
    _op._extra_obj_type = _patched
    _op._fence_type_patched = True


def _unpatch_extra_obj_type():
    from . import operators as _op
    if getattr(_op, "_fence_type_patched", False):
        _op._extra_obj_type = _op._fence_orig_type
        _op._fence_type_patched = False


# ---------------------------------------------------------------------------
# Post spacing (add-on preferences)
# ---------------------------------------------------------------------------
def _prefs():
    """The add-on preferences, or None when they cannot be read."""
    try:
        return bpy.context.preferences.addons[__package__.split('.')[0]].preferences
    except Exception:
        return None


def _spacing():
    """The post spacing from the ADD-ON PREFERENCES, so what the user sets holds for every
    .blend they open. Falls back to the default if it cannot be read."""
    p = _prefs()
    if p is None:
        return SPACING_DEFAULT
    try:
        return float(p.fence_post_spacing)
    except Exception:
        return SPACING_DEFAULT


# ---------------------------------------------------------------------------
# Panel row (called from panels.py inside the "Other objects" box)
# ---------------------------------------------------------------------------
def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Fence", icon='MOD_ARRAY')
    row.prop(context.scene, "condor_fence_batch", text="Batch")
    row = box.row(align=True)
    row.operator("condor.import_fences", text="Import", icon='IMPORT')
    row.operator("condor.merge_fences", text="Merge", icon='AUTOMERGE_ON')
    try:
        # the value lives in the add-on preferences, so it is remembered for every
        # .blend - the panel only shows it
        prefs = context.preferences.addons[__package__.split('.')[0]].preferences
        box.prop(prefs, "fence_post_spacing", text="Posts", slider=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Registration (operators + scene property)
# ---------------------------------------------------------------------------
# The main Overpass query is deliberately NOT touched: barrier=fence is far too common a
# tag for that. The fences are fetched only when they are actually being built, per patch
# (see _ensure_fence_data), so somebody who does not use this feature never downloads a
# single byte extra.
_classes = [CONDOR_OT_import_fences, CONDOR_OT_merge_fences]


def register():
    from bpy.props import BoolProperty
    bpy.types.Scene.condor_fence_batch = BoolProperty(
        name="Batch",
        description=("File mode (Import to Blender off): after each patch OBJ is written, "
                     "also generate the fences into it. Off by default"),
        default=False,
    )
    # The post spacing is NOT a scene property: it lives in the add-on preferences
    # (see blender/properties.py), so what the user sets is remembered for every .blend.
    for c in _classes:
        bpy.utils.register_class(c)
    # 'fence' is an extra object, not a building (see _patch_extra_obj_type)
    try:
        _patch_extra_obj_type()
    except Exception:
        pass
    # object 'fence' shares the powerline material 'pylones' (exactly like 'aerialway'),
    # so it goes onto Pylons.dds. TEXTURE_MAP already has 'pylones' from config.py.
    try:
        from .. import config
        config.MATERIAL_ALIAS.setdefault(OBJECT_NAME, MAT_NAME)
    except Exception:
        pass
    # append the fences into each patch OBJ after export (file mode / Batch)
    try:
        _patch_obj_exporter()
    except Exception:
        pass


def unregister():
    try:
        _unpatch_extra_obj_type()
    except Exception:
        pass
    try:
        _unpatch_obj_exporter()
    except Exception:
        pass
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
    try:
        del bpy.types.Scene.condor_fence_batch
    except Exception:
        pass
    try:
        from .. import config
        # only the fence alias is removed - 'pylones' itself belongs to config.py
        config.MATERIAL_ALIAS.pop(OBJECT_NAME, None)
    except Exception:
        pass
