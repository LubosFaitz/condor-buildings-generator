"""
Solar farms - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Bridges / Transmitter feature: the whole thing lives in THIS one file plus
two tiny, try/except-guarded hooks in:
  - __init__.py       (register/unregister this module)
  - blender/panels.py (draw the Solar row inside "Other objects")
and one line in blender/operators.py::_extra_obj_type so a baked solar is de-duplicated
like chimneys / bridges. Delete this file (+ those hooks) and the plugin is unchanged.

What it does:
  A ground solar farm in OSM is a way tagged power=plant/plant:source=solar or
  power=generator/generator:source=solar (roof panels excluded). For each farm the
  ESRI World Imagery tiles over it are downloaded, a clean panel MASK is built from the
  orthophoto (dark cells inside the OSM outline, de-speckled), the outline is auto-aligned
  to the imagery, and each mask row becomes ONE tilted, textured panel box on posts.

  Import (SINGLE patch only) creates each farm as a SEPARATE object 'Solar_<patch>_<n>'
  in the patch collection, so you can select one and press Flip to turn its tilt over.
  A flip SURVIVES a re-import (e.g. after one farm's mask was redrawn): the tilt is read
  off the objects before they are rebuilt, and is also cached in solar_flip.json so it
  survives closing Blender. Merge joins all 'Solar_<patch>_<n>' of a patch into one
  'solar_farm' object.

  Tiles + debug JPEGs go to  working/autogen/condor_esri_cache/<patch>/ .

  'Scan solar farms' lists which patches of the scenery have a farm at all (so you don't
  have to try them one by one). It runs solar_scan.py - the standalone helper next to this
  file - in its OWN process, and shows up only while working/autogen/solar_farms.txt is
  missing.
"""

import os
import re
import sys
import json
import math
import logging
import xml.etree.ElementTree as ET

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

OBJECT_NAME = "solar_farm"
MAT_NAME = "condor_solar"
TEX_FILE = "solar.dds"                            # in assets/3Dobjects, copied to Autogen/Textures
TEX_REL = os.path.join("Textures", TEX_FILE)      # under working/autogen

# --- panel field tuning ---------------------------------------------------
PANEL_TILT_DEG = 30.0
IMPORT_FLIP = True            # default tilt orientation for a fresh import (Flip button turns one over)
ROW_SPACING = 6.0
TABLE_DEPTH = 3.0
PANEL_THICKNESS = 0.1
SUPPORT_H = 1.1
POST_HALF = 0.05
POST_STEP = 10.0
POST_EMBED = 0.03           # raise the post top this far ABOVE the panel underside (m) -> tucked into the panel
POST_DEPTH = 3.0            # how far BELOW the terrain the post reaches (m) -> always anchored under ground

# --- texture (solar.dds) UV mapping ---------------------------------------
TEX_TILE_M = 4.0            # metres of panel per one texture width (repeats along the row)
CELL_VBOT = 0.0            # panel edge -> bottom of the texture (V=0)
CELL_VTOP = 0.965          # panel edge -> top of the panel image (494/512); strip is above this
UV_FRAME = (0.25, 0.98)    # light-grey (left half of the top strip) -> panel edges + underside
UV_POST = (0.72, 0.98)     # dark (right half of the top strip) -> posts

MASK_PLANE_H = 55.0         # 'Mask on terrain' plane floats this high (m) -> stays visible on a slope

MIN_RUN = 9.0               # drop panel boxes shorter than this (m) -> kills isolated speckle/road
MIN_COMPONENT = 400         # in the mask image, drop black blobs smaller than this many pixels (noise)
MIN_ELONG = 2.0             # drop small blobs that are not row-shaped (roundish noise), PCA-based
ELONG_MAX_AREA = 2000       # only test elongation on components smaller than this (px); big ones stay
POLY_MAX_SHIFT = 10.0       # auto-align search window (m): ESRI georef differs from OSM by a few m
MAX_PANELS = 200000

PATCH_HALF = 2880.0
PATCH_SIZE = 5760.0
SOLAR_PLANT = {"solar"}

ESRI_ZOOM = 18
ESRI_MAX_TILES = 80
ESRI_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}")


def _log(msg):
    print(f"[solar] {msg}")


# ---------------------------------------------------------------------------
# FLIP CACHE - remembers each farm's tilt so it survives a re-import
# ---------------------------------------------------------------------------
# Lives next to the ESRI images: condor_esri_cache/<patch>/solar_flip.json,
# {"1": true, "2": false} = farm index -> flipped. The object in the scene ALWAYS
# wins over this file; the cache only fills in when the object is gone (Blender
# was closed). Delete the cache folder and the tilt resets to IMPORT_FLIP.
FLIP_CACHE_NAME = "solar_flip.json"


def _flip_cache_read(cache_dir):
    """{farm_idx: bool} from the cache, {} when there is none / it is unreadable."""
    try:
        with open(os.path.join(cache_dir, FLIP_CACHE_NAME), "r") as f:
            return {int(k): bool(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def _flip_cache_write(cache_dir, idx, flip):
    """Remember one farm's tilt; the other farms in the file stay untouched."""
    data = _flip_cache_read(cache_dir)
    data[int(idx)] = bool(flip)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, FLIP_CACHE_NAME), "w") as f:
            json.dump({str(k): v for k, v in sorted(data.items())}, f)
    except OSError as e:
        _log(f"flip cache write failed: {e}")


# ---------------------------------------------------------------------------
# Terrain elevation
# ---------------------------------------------------------------------------
def _terrain_z_closure(terrain):
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


# ---------------------------------------------------------------------------
# OSM solar parsing
# ---------------------------------------------------------------------------
def _is_solar_tags(tags):
    if tags.get("location") == "roof" or tags.get("building"):
        return False                     # rooftop panels excluded - only ground farms
    return (
        (tags.get("power") == "plant" and tags.get("plant:source") in SOLAR_PLANT)
        or (tags.get("power") == "generator" and tags.get("generator:source") in SOLAR_PLANT)
    )


def _assemble_rings(segments):
    """Chain open way segments (lists of (x, y)) into CLOSED rings by matching shared
    endpoints. A single already-closed way comes back as one ring. Used for multipolygon
    relations whose outer boundary can be split across several member ways."""
    segs = [list(s) for s in segments if len(s) >= 2]
    used = [False] * len(segs)
    rings = []

    def same(a, b):
        return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        ring = list(segs[i])
        extended = True
        while extended and not same(ring[0], ring[-1]):
            extended = False
            for j in range(len(segs)):
                if used[j]:
                    continue
                s = segs[j]
                if same(ring[-1], s[0]):
                    ring.extend(s[1:]); used[j] = extended = True
                elif same(ring[-1], s[-1]):
                    ring.extend(reversed(s[:-1])); used[j] = extended = True
                elif same(ring[0], s[-1]):
                    ring[:0] = s[:-1]; used[j] = extended = True
                elif same(ring[0], s[0]):
                    ring[:0] = list(reversed(s[1:])); used[j] = extended = True
        rings.append(ring)
    return rings


def _parse_solar(root, projector):
    """Ground solar farms as patch-coord polygons, from BOTH simple ways AND multipolygon
    relations (e.g. FVE Stribro is a relation, not a way)."""
    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}

    # every way's projected points, indexed by id (relation members reference these)
    way_pts = {}
    for w in root.findall("way"):
        pts = []
        for nd in w.findall("nd"):
            nid = nd.get("ref")
            if nid in node_coords:
                lat, lon = node_coords[nid]
                pts.append(projector.project(lat, lon))
        if pts:
            way_pts[w.get("id")] = pts

    polys = []
    # 1) simple ways tagged as a solar farm
    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if not _is_solar_tags(tags):
            continue
        pts = way_pts.get(w.get("id"))
        if pts and len(pts) >= 3:
            polys.append(pts)

    # 2) multipolygon relations -> stitch the outer member ways into ring(s)
    for rel in root.findall("relation"):
        tags = {t.get("k"): t.get("v") for t in rel.findall("tag")}
        if not _is_solar_tags(tags):
            continue
        outers = [way_pts[m.get("ref")]
                  for m in rel.findall("member")
                  if m.get("type") == "way" and m.get("role") in ("outer", "")
                  and m.get("ref") in way_pts]
        for ring in _assemble_rings(outers):
            if len(ring) >= 3:
                polys.append(ring)
    return polys


def _fetch_solar_osm(meta):
    import urllib.request
    import urllib.parse
    bbox = f"{meta.lat_min},{meta.lon_min},{meta.lat_max},{meta.lon_max}"
    q = ("[out:xml][timeout:60];\n(\n"
         f'way["power"="plant"]["plant:source"="solar"]({bbox});\n'
         f'relation["power"="plant"]["plant:source"="solar"]({bbox});\n'
         f'way["power"="generator"]["generator:source"="solar"]({bbox});\n'
         f'relation["power"="generator"]["generator:source"="solar"]({bbox});\n'
         ");\nout body;\n>;\nout skel qt;")
    data = urllib.parse.urlencode({"data": q}).encode("utf-8")
    for server in ("https://overpass-api.de/api/interpreter",
                   "https://overpass.kumi.systems/api/interpreter"):
        try:
            req = urllib.request.Request(server, data=data,
                                         headers={"User-Agent": "condor-solar"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return ET.fromstring(r.read())
        except Exception as e:
            _log(f"Overpass failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _point_in_poly(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# --- patch metres <-> GPS via a LOCAL LINEARISATION of the real projector -------
_INV = None   # (lat_c, lon_c, x_c, y_c, J, Ji) - set per patch by _setup_inv


def _setup_inv(projector, meta):
    global _INV
    lat_c = (meta.lat_min + meta.lat_max) / 2.0
    lon_c = (meta.lon_min + meta.lon_max) / 2.0
    x_c, y_c = projector.project(lat_c, lon_c)
    d = 0.001
    x1, y1 = projector.project(lat_c, lon_c + d)     # d(x,y)/dlon
    x2, y2 = projector.project(lat_c + d, lon_c)     # d(x,y)/dlat
    j00, j01 = (x1 - x_c) / d, (x2 - x_c) / d
    j10, j11 = (y1 - y_c) / d, (y2 - y_c) / d
    det = j00 * j11 - j01 * j10
    ji = ((j11 / det, -j01 / det), (-j10 / det, j00 / det))
    _INV = (lat_c, lon_c, x_c, y_c, (j00, j01, j10, j11), ji)


def _patch_to_latlon(x, y, meta):
    if _INV is None:
        fx = (x + PATCH_HALF) / PATCH_SIZE; fy = (y + PATCH_HALF) / PATCH_SIZE
        return (meta.lat_min + fy * (meta.lat_max - meta.lat_min),
                meta.lon_min + fx * (meta.lon_max - meta.lon_min))
    lat_c, lon_c, x_c, y_c, J, Ji = _INV
    dx, dy = x - x_c, y - y_c
    dlon = Ji[0][0] * dx + Ji[0][1] * dy
    dlat = Ji[1][0] * dx + Ji[1][1] * dy
    return lat_c + dlat, lon_c + dlon


def _latlon_to_patch(lat, lon, meta):
    if _INV is None:
        fx = (lon - meta.lon_min) / (meta.lon_max - meta.lon_min)
        fy = (lat - meta.lat_min) / (meta.lat_max - meta.lat_min)
        return fx * PATCH_SIZE - PATCH_HALF, fy * PATCH_SIZE - PATCH_HALF
    lat_c, lon_c, x_c, y_c, J, Ji = _INV
    dlat, dlon = lat - lat_c, lon - lon_c
    j00, j01, j10, j11 = J
    return x_c + j00 * dlon + j01 * dlat, y_c + j10 * dlon + j11 * dlat


def _deg_to_tile_f(lat, lon, z):
    n = 2 ** z
    tx = (lon + 180.0) / 360.0 * n
    ty = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return tx, ty


# ---------------------------------------------------------------------------
# ESRI tiles + panel mask
# ---------------------------------------------------------------------------
def _load_sat_jpeg(path, W, H):
    """Load the greyscale mosaic back from a saved esri_sat.jpg (so the tiles aren't needed after
    the first import). Returns L (W*H luminance 0..255) or None if size doesn't match."""
    try:
        img = bpy.data.images.load(path, check_existing=False)
    except Exception:
        return None
    w, h = img.size
    if w != W or h != H:
        bpy.data.images.remove(img)
        return None
    buf = [0.0] * (w * h * 4)
    img.pixels.foreach_get(buf)
    bpy.data.images.remove(img)
    L = [[0.0] * W for _ in range(H)]
    for y in range(H):
        br = (H - 1 - y) * W * 4          # jpg row 0 = bottom; L row 0 = north (top)
        Lrow = L[y]
        for x in range(W):
            Lrow[x] = buf[br + x * 4] * 255.0   # already greyscale
    return L


def _delete_tiles(cache_dir):
    """Remove the raw ESRI tile files ({z}_{x}_{y}.jpg) from cache_dir once the stitched
    esri_sat/mask/overlay/poly.jpg exist - they're no longer needed. Returns how many were removed."""
    pat = re.compile(r'^\d+_\d+_\d+\.jpg$')
    n = 0
    try:
        for fn in os.listdir(cache_dir):
            if pat.match(fn):
                try:
                    os.remove(os.path.join(cache_dir, fn)); n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def _esri_luma(poly, meta, cache_dir, sat_path=None):
    """Return (L, W, H, z, x0, y0) - a greyscale mosaic over the farm - or None. If sat_path
    (a saved esri_sat.jpg) exists and matches, it's used and NO tiles are downloaded/needed;
    otherwise the ESRI World Imagery tiles are downloaded into cache_dir and stitched."""
    import urllib.request
    os.makedirs(cache_dir, exist_ok=True)
    z = ESRI_ZOOM
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    lat_a, lon_a = _patch_to_latlon(min(xs), min(ys), meta)
    lat_b, lon_b = _patch_to_latlon(max(xs), max(ys), meta)
    txa, tya = _deg_to_tile_f(lat_a, lon_a, z)
    txb, tyb = _deg_to_tile_f(lat_b, lon_b, z)
    x0, x1 = int(min(txa, txb)), int(max(txa, txb))
    y0, y1 = int(min(tya, tyb)), int(max(tya, tyb))
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    W, H = cols * 256, rows * 256

    # reuse the saved satellite mosaic if we have it -> tiles not needed
    if sat_path and os.path.exists(sat_path):
        L = _load_sat_jpeg(sat_path, W, H)
        if L is not None:
            _log(f"ESRI: using saved {os.path.basename(sat_path)} (tiles not needed)")
            return L, W, H, z, x0, y0

    _log(f"ESRI z{z} tiles x[{x0}..{x1}] y[{y0}..{y1}]  ({cols * rows} tiles)")
    if cols * rows > ESRI_MAX_TILES:
        _log(f"too many tiles ({cols * rows} > {ESRI_MAX_TILES}) - aborting ESRI")
        return None

    L = [[0.0] * W for _ in range(H)]
    got = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            fn = os.path.join(cache_dir, f"{z}_{tx}_{ty}.jpg")
            if not os.path.exists(fn) or os.path.getsize(fn) == 0:
                try:
                    req = urllib.request.Request(ESRI_URL.format(z=z, x=tx, y=ty),
                                                 headers={"User-Agent": "condor-solar"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        open(fn, "wb").write(r.read())
                except Exception as e:
                    _log(f"tile {tx},{ty} failed: {e}")
                    continue
            try:
                img = bpy.data.images.load(fn, check_existing=False)
                w, h = img.size
                buf = [0.0] * (w * h * 4)
                img.pixels.foreach_get(buf)          # RGBA floats, row 0 = BOTTOM
                cox, roy = (tx - x0) * 256, (ty - y0) * 256
                for rb in range(h):
                    rt = h - 1 - rb                   # flip: Blender rows are bottom-first
                    base = rb * w * 4
                    Lrow = L[roy + rt]
                    for col in range(w):
                        i = base + col * 4
                        Lrow[cox + col] = (0.299 * buf[i] + 0.587 * buf[i + 1]
                                           + 0.114 * buf[i + 2]) * 255.0
                bpy.data.images.remove(img)
                got += 1
            except Exception as e:
                _log(f"tile load {tx},{ty}: {e}")
    if got == 0:
        return None
    _log(f"ESRI luma {W}x{H} from {got} tiles")
    return L, W, H, z, x0, y0


def _estimate_poly_shift(luma, poly, meta, thr, max_shift=POLY_MAX_SHIFT):
    """Return (dx, dy) in patch metres that best seats the OSM polygon on the dark panel
    mass (ESRI georef differs from OSM by a few metres)."""
    L, W, H, z, x0, y0 = luma
    n = 2 ** z
    if thr is None:
        return (0.0, 0.0)
    px_step = max(1, int((W * H / 20000.0) ** 0.5))
    darks = []
    for py in range(0, H, px_step):
        gy = y0 + py / 256.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / n))))
        Lrow = L[py]
        for px in range(0, W, px_step):
            if Lrow[px] < thr:
                lon = (x0 + px / 256.0) / n * 360.0 - 180.0
                darks.append(_latlon_to_patch(lat, lon, meta))
    if len(darks) < 50:
        return (0.0, 0.0)
    minx = min(p[0] for p in poly) - max_shift - 2.0
    miny = min(p[1] for p in poly) - max_shift - 2.0
    maxx = max(p[0] for p in poly) + max_shift + 2.0
    maxy = max(p[1] for p in poly) + max_shift + 2.0
    gw, gh = int(maxx - minx) + 1, int(maxy - miny) + 1
    inside = [bytearray(gw) for _ in range(gh)]
    for iy in range(gh):
        wy = miny + iy + 0.5
        row = inside[iy]
        for ix in range(gw):
            if _point_in_poly(minx + ix + 0.5, wy, poly):
                row[ix] = 1

    def is_in(wx, wy):
        ix, iy = int(wx - minx), int(wy - miny)
        return 0 <= ix < gw and 0 <= iy < gh and inside[iy][ix] == 1

    best, best_n = (0.0, 0.0), -1
    r = int(max_shift)
    for dyi in range(-r, r + 1):
        for dxi in range(-r, r + 1):
            c = 0
            for (wx, wy) in darks:
                if is_in(wx - dxi, wy - dyi):
                    c += 1
            if c > best_n:
                best_n, best = c, (float(dxi), float(dyi))
    return best


def _detect_row_angle(luma, poly, meta):
    """Row direction in PATCH coords from the ESRI image (structure tensor inside the
    farm polygon)."""
    L, W, H, z, x0, y0 = luma
    n = 2 ** z
    step = max(1, int(max(W, H) / 1000))

    def pix_to_patch(px, py):
        gx = x0 + px / 256.0
        gy = y0 + py / 256.0
        lon = gx / n * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / n))))
        return _latlon_to_patch(lat, lon, meta)

    Jxx = Jyy = Jxy = 0.0
    used = 0
    for py in range(step, H - step, step):
        for px in range(step, W - step, step):
            wx, wy = pix_to_patch(px, py)
            if not _point_in_poly(wx, wy, poly):
                continue
            gx = L[py][px + step] - L[py][px - step]
            gy = L[py + step][px] - L[py - step][px]
            Jxx += gx * gx
            Jyy += gy * gy
            Jxy += gx * gy
            used += 1
    if used < 50:
        _log(f"ESRI: too few pixels in polygon ({used})")
        return None
    grad = 0.5 * math.atan2(2 * Jxy, Jxx - Jyy)
    row_patch = -(grad + math.pi / 2.0)          # pixel +y is south -> flip to patch north
    if math.cos(row_patch) < 0:
        row_patch += math.pi
    _log(f"ESRI row angle: {math.degrees(row_patch):.1f} deg  (from {used} px)")
    return row_patch


def _mask_row_angle(B, luma, meta):
    """Row direction in PATCH coords taken from the BLACK/WHITE mask itself (structure tensor on
    the binary mask), so the build follows exactly how the mask is oriented. None if too empty."""
    L, W, H, z, x0, y0 = luma
    step = max(1, int(max(W, H) / 1000))
    Jxx = Jyy = Jxy = 0.0
    used = 0
    for py in range(step, H - step, step):
        for px in range(step, W - step, step):
            gx = B[py][px + step] - B[py][px - step]
            gy = B[py + step][px] - B[py - step][px]
            if gx == 0 and gy == 0:
                continue
            Jxx += gx * gx; Jyy += gy * gy; Jxy += gx * gy
            used += 1
    if used < 50:
        return None
    grad = 0.5 * math.atan2(2 * Jxy, Jxx - Jyy)
    row_patch = -(grad + math.pi / 2.0)          # pixel +y is south -> flip to patch north
    if math.cos(row_patch) < 0:
        row_patch += math.pi
    _log(f"mask row angle: {math.degrees(row_patch):.1f} deg  (from {used} px)")
    return row_patch


def _panel_threshold(luma, poly, meta):
    """Otsu threshold on the image luminance inside the farm polygon (panels are darker)."""
    L, W, H, z, x0, y0 = luma
    n = 2 ** z
    step = max(1, int(max(W, H) / 500))
    hist = [0] * 256
    for py in range(0, H, step):
        for px in range(0, W, step):
            gx = x0 + px / 256.0
            gy = y0 + py / 256.0
            lon = gx / n * 360.0 - 180.0
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / n))))
            wx, wy = _latlon_to_patch(lat, lon, meta)
            if not _point_in_poly(wx, wy, poly):
                continue
            hist[max(0, min(255, int(L[py][px])))] += 1
    total = sum(hist)
    if total < 50:
        return None
    sum_all = sum(i * hist[i] for i in range(256))
    sumB = wB = 0
    best_var = -1.0
    thr = 128
    for i in range(256):
        wB += hist[i]
        if wB == 0:
            continue
        wF = total - wB
        if wF == 0:
            break
        sumB += i * hist[i]
        mB = sumB / wB
        mF = (sum_all - sumB) / wF
        var = wB * wF * (mB - mF) ** 2
        if var > best_var:
            best_var = var
            thr = i
    _log(f"panel/grass threshold (Otsu): {thr}")
    return thr


def _elongation(comp):
    """PCA elongation of a pixel component (rotation-invariant): sqrt(l1/l2). Row -> large,
    roundish noise blob -> ~1."""
    nN = len(comp)
    mx = sum(c[0] for c in comp) / nN
    my = sum(c[1] for c in comp) / nN
    sxx = sum((c[0] - mx) ** 2 for c in comp) / nN
    syy = sum((c[1] - my) ** 2 for c in comp) / nN
    sxy = sum((c[0] - mx) * (c[1] - my) for c in comp) / nN
    tr = sxx + syy
    disc = math.sqrt(max(0.0, tr * tr / 4.0 - (sxx * syy - sxy * sxy)))
    l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
    if l2 <= 1e-6:
        return 1e6
    return math.sqrt(l1 / l2)


def _clean_panel_bitmap(luma, thr, poly, meta):
    """Binary panel bitmap: 1 = dark AND inside the OSM outline, then drop small / roundish
    blobs (road / dotted noise) so only long panel rows stay = 1."""
    L, W, H, z, x0, y0 = luma
    n = 2 ** z
    B = [bytearray(W) for _ in range(H)]
    for y in range(H):
        gy = y0 + y / 256.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / n))))
        Lrow = L[y]; Brow = B[y]
        for x in range(W):
            if thr is not None and Lrow[x] < thr:
                lon = (x0 + x / 256.0) / n * 360.0 - 180.0
                wx, wy = _latlon_to_patch(lat, lon, meta)
                if _point_in_poly(wx, wy, poly):
                    Brow[x] = 1
    seen = [bytearray(W) for _ in range(H)]
    for sy in range(H):
        Brow = B[sy]
        for sx in range(W):
            if Brow[sx] == 1 and not seen[sy][sx]:
                stack = [(sx, sy)]; seen[sy][sx] = 1; comp = [(sx, sy)]
                while stack:
                    cx, cy = stack.pop()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H and B[ny][nx] == 1 and not seen[ny][nx]:
                            seen[ny][nx] = 1
                            stack.append((nx, ny)); comp.append((nx, ny))
                if len(comp) < MIN_COMPONENT or (
                        len(comp) < ELONG_MAX_AREA and _elongation(comp) < MIN_ELONG):
                    for (cx, cy) in comp:
                        B[cy][cx] = 0
    return B


def _mask_rows_local(B, luma, meta):
    """Each connected component of the mask = one panel row, described by ITS OWN orientation
    (so a global row angle can't drift the panels): returns (cx, cy, dx, dy, hl) - centre in patch
    metres, unit direction ALONG the stripe (its principal axis), and half its length."""
    L, W, H, z, x0, y0 = luma
    n = 2 ** z
    latH = [math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y0 + py / 256.0) / n))))
            for py in range(H)]
    lonW = [(x0 + px / 256.0) / n * 360.0 - 180.0 for px in range(W)]
    seen = [bytearray(W) for _ in range(H)]
    rows = []
    for sy in range(H):
        Brow = B[sy]
        for sx in range(W):
            if Brow[sx] == 1 and not seen[sy][sx]:
                stack = [(sx, sy)]; seen[sy][sx] = 1; comp = [(sx, sy)]
                while stack:
                    cx, cy = stack.pop()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H and B[ny][nx] == 1 and not seen[ny][nx]:
                            seen[ny][nx] = 1
                            stack.append((nx, ny)); comp.append((nx, ny))
                if len(comp) < MIN_COMPONENT:
                    continue
                pts = [_latlon_to_patch(latH[py], lonW[px], meta) for (px, py) in comp]
                N = len(pts)
                mx = sum(p[0] for p in pts) / N
                my = sum(p[1] for p in pts) / N
                sxx = sum((p[0] - mx) ** 2 for p in pts) / N
                syy = sum((p[1] - my) ** 2 for p in pts) / N
                sxy = sum((p[0] - mx) * (p[1] - my) for p in pts) / N
                ang = 0.5 * math.atan2(2 * sxy, sxx - syy)   # principal (long) axis of the stripe
                dx, dy = math.cos(ang), math.sin(ang)
                tmin = 1e18; tmax = -1e18
                for (x, y) in pts:
                    t = (x - mx) * dx + (y - my) * dy
                    if t < tmin: tmin = t
                    if t > tmax: tmax = t
                if tmax - tmin < MIN_RUN:
                    continue
                mc = (tmin + tmax) / 2.0                     # centre along the stripe
                rows.append((mx + dx * mc, my + dy * mc, dx, dy, (tmax - tmin) / 2.0))
    return rows


def _load_mask_jpeg(path, W, H):
    """Load a hand-editable mask JPEG (black = panel, white = nothing) into a bitmap that
    matches the luma indexing, or None if it can't be used. Lets the user repaint a bad mask
    (open esri_sat.jpg, draw the panels black, save as esri_mask.jpg) and rebuild from it."""
    try:
        img = bpy.data.images.load(path, check_existing=False)
    except Exception:
        return None
    w, h = img.size
    if w != W or h != H:
        bpy.data.images.remove(img)
        return None
    buf = [0.0] * (w * h * 4)
    img.pixels.foreach_get(buf)
    bpy.data.images.remove(img)
    B = [bytearray(W) for _ in range(H)]
    for y in range(H):
        br = (H - 1 - y) * W * 4          # jpg row 0 = bottom; bitmap row 0 = north (top)
        Brow = B[y]
        for x in range(W):
            if buf[br + x * 4] < 0.5:     # dark pixel = panel
                Brow[x] = 1
    return B


def _save_debug_jpegs(luma, B, poly, meta, out_dir, tag="", write_mask=True):
    """Write esri_sat/overlay/poly<tag>.jpg into out_dir (north-up). esri_mask<tag>.jpg is
    written too UNLESS write_mask is False - then a hand-edited mask is left untouched."""
    L, W, H, z, x0, y0 = luma
    n = 2 ** z
    sat = [0.0] * (W * H * 4)
    msk = [0.0] * (W * H * 4)
    ovr = [0.0] * (W * H * 4)
    ply = [0.0] * (W * H * 4)
    for y in range(H):
        yy = H - 1 - y
        Lrow = L[y]; Brow = B[y]
        for x in range(W):
            v = Lrow[x] / 255.0
            i = (yy * W + x) * 4
            sat[i] = sat[i + 1] = sat[i + 2] = v; sat[i + 3] = 1.0
            ply[i] = ply[i + 1] = ply[i + 2] = v; ply[i + 3] = 1.0
            panel = Brow[x] == 1
            m = 0.0 if panel else 1.0
            msk[i] = msk[i + 1] = msk[i + 2] = m; msk[i + 3] = 1.0
            if panel:
                ovr[i] = 1.0; ovr[i + 1] = v * 0.2; ovr[i + 2] = v * 0.2
            else:
                ovr[i] = ovr[i + 1] = ovr[i + 2] = v
            ovr[i + 3] = 1.0

    def patch_to_pix(px, py):
        lat, lon = _patch_to_latlon(px, py, meta)
        gx = (lon + 180.0) / 360.0 * n
        gy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
        return int((gx - x0) * 256), int((gy - y0) * 256)

    def dot(cx, ry):
        for ddx in range(-1, 2):
            for ddy in range(-1, 2):
                xx, yt = cx + ddx, ry + ddy
                if 0 <= xx < W and 0 <= yt < H:
                    i = ((H - 1 - yt) * W + xx) * 4
                    ply[i] = 0.0; ply[i + 1] = 1.0; ply[i + 2] = 0.0

    m = len(poly)
    for k in range(m):
        ax, ay = patch_to_pix(*poly[k])
        bx, by = patch_to_pix(*poly[(k + 1) % m])
        steps = max(abs(bx - ax), abs(by - ay), 1)
        for st in range(steps + 1):
            dot(ax + (bx - ax) * st // steps, ay + (by - ay) * st // steps)

    outs = [(f"esri_sat{tag}.jpg", sat), (f"esri_overlay{tag}.jpg", ovr),
            (f"esri_poly{tag}.jpg", ply)]
    if write_mask:
        outs.append((f"esri_mask{tag}.jpg", msk))
    for name, buf in outs:
        img = bpy.data.images.new(name, W, H)
        img.pixels.foreach_set(buf)
        img.filepath_raw = os.path.join(out_dir, name)
        img.file_format = 'JPEG'
        img.save()
        bpy.data.images.remove(img)


# ---------------------------------------------------------------------------
# Build one tilted, textured box per mask row
# ---------------------------------------------------------------------------
def _build_rows(rows, z_at, flip, posts=True):
    """Return (verts, faces, face_uvs). One tilted, textured panel box per row, each built along
    ITS OWN direction so panels sit exactly on their mask stripe (no global-angle drift). Each row
    is (cx, cy, dx, dy, hl): centre, unit direction along the stripe, half length. posts=False (LOD1)
    builds the panels WITHOUT the support posts."""
    verts, faces, face_uvs = [], [], []
    tilt = math.radians(PANEL_TILT_DEG)
    depth = TABLE_DEPTH * math.cos(tilt)     # fixed panel depth (same for every row)
    rise = TABLE_DEPTH * math.sin(tilt)
    oyt = math.sin(tilt) * PANEL_THICKNESS   # panel-thickness offset, along the perpendicular
    oz = math.cos(tilt) * PANEL_THICKNESS
    thick = -1.0 if flip else 1.0            # underside offset side mirrors when flipped
    hd = depth / 2.0

    n = 0
    for (cx, cy, dx, dy, hl) in rows:
        if 2.0 * hl < MIN_RUN:
            continue
        px, py = -dy, dx                     # unit perpendicular (across the panel)
        ax, ay = cx - dx * hl, cy - dy * hl  # end A (u = -hl)
        bx, by = cx + dx * hl, cy + dy * hl  # end B (u = +hl)
        tzA = z_at(ax, ay); tzB = z_at(bx, by)
        # v0 side = -perp, v1 side = +perp; the flip decides which side is the HIGH edge
        if flip:
            zA0, zB0 = tzA + SUPPORT_H + rise, tzB + SUPPORT_H + rise
            zA1, zB1 = tzA + SUPPORT_H, tzB + SUPPORT_H
        else:
            zA0, zB0 = tzA + SUPPORT_H, tzB + SUPPORT_H
            zA1, zB1 = tzA + SUPPORT_H + rise, tzB + SUPPORT_H + rise
        # top corners (world XY, z): (A,v0),(B,v0),(B,v1),(A,v1)
        top = [(ax - px * hd, ay - py * hd, zA0),
               (bx - px * hd, by - py * hd, zB0),
               (bx + px * hd, by + py * hd, zB1),
               (ax + px * hd, ay + py * hd, zA1)]
        b = len(verts)
        for (wx, wy, wz) in top:
            verts.append((wx, wy, wz))
        for (wx, wy, wz) in top:             # underside: offset along perp + down by thickness
            verts.append((wx + thick * oyt * px, wy + thick * oyt * py, wz - oz))
        uR = (2.0 * hl) / TEX_TILE_M
        faces.append((b, b + 1, b + 2, b + 3))       # top = the solar cells (tiled along the row)
        face_uvs.append([(0.0, CELL_VBOT), (uR, CELL_VBOT), (uR, CELL_VTOP), (0.0, CELL_VTOP)])
        faces.append((b + 4, b + 7, b + 6, b + 5))   # underside = light-grey strip
        face_uvs.append([UV_FRAME] * 4)
        for k in range(4):                            # 4 edges = light-grey strip
            kn = (k + 1) % 4
            faces.append((b + k, b + kn, b + 4 + kn, b + 4 + k))
            face_uvs.append([UV_FRAME] * 4)

        # posts along the row, under the HIGH edge (v0 side if flipped, else v1 side). LOD1 skips them.
        edge = (-1.0 if flip else 1.0) * (hd - POST_HALF)
        pcorners = ((-POST_HALF, -POST_HALF), (POST_HALF, -POST_HALF),
                    (POST_HALF, POST_HALF), (-POST_HALF, POST_HALF))
        s = -hl + POST_STEP / 2.0
        while posts and s < hl:
            f = (s + hl) / (2.0 * hl) if hl > 0 else 0.0
            wx = cx + dx * s + px * edge
            wy = cy + dy * s + py * edge
            tz = tzA + (tzB - tzA) * f
            z_bot = z_at(wx, wy) - POST_DEPTH
            pb = len(verts)
            for (a, b2) in pcorners:
                verts.append((wx + dx * a + px * b2, wy + dy * a + py * b2, z_bot))
            for (a, b2) in pcorners:
                q = edge + b2                                 # corner's position across the panel
                frac = (hd - q) / (2.0 * hd) if flip else (q + hd) / (2.0 * hd)
                st = tz + SUPPORT_H + rise * frac - oz + POST_EMBED   # sloped like the panel, tucked in
                verts.append((wx + dx * a + px * b2, wy + dy * a + py * b2, st))
            for k in range(4):
                kn = (k + 1) % 4
                faces.append((pb + k, pb + kn, pb + 4 + kn, pb + 4 + k))
                face_uvs.append([UV_POST] * 4)
            s += POST_STEP

        n += 1
        if n >= MAX_PANELS:
            break
    return verts, faces, face_uvs


# ---------------------------------------------------------------------------
# Blender material / object
# ---------------------------------------------------------------------------
def _copy_solar_texture(paths):
    """Copy solar.dds from assets/3Dobjects next to the patch OBJs (Working/Autogen/Textures) so
    Condor finds it - exactly like the bridges do. If it's already there, it's left alone."""
    try:
        import shutil
        tdir = os.path.join(paths['autogen'], "Textures")
        os.makedirs(tdir, exist_ok=True)
        dst = os.path.join(tdir, TEX_FILE)
        src = os.path.join(os.path.dirname(__file__), "..", "assets", "3Dobjects", TEX_FILE)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copyfile(src, dst)
            _log(f"texture copied -> {dst}")
    except Exception as e:
        _log(f"texture copy failed: {e}")


def _material(tex_path=None):
    mat = bpy.data.materials.get(MAT_NAME)
    if mat is not None:
        return mat                       # the user's material -> use it as-is
    mat = bpy.data.materials.new(MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if tex_path and os.path.exists(tex_path):
        img = bpy.data.images.load(tex_path, check_existing=True)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.extension = 'REPEAT'
        if bsdf:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    elif bsdf:
        bsdf.inputs["Base Color"].default_value = (0.02, 0.05, 0.18, 1.0)
    return mat


def _make_object(name, verts, faces, face_uvs, tex_path):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], [list(f) for f in faces])
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for poly, quad in zip(mesh.polygons, face_uvs):
        for li, uvc in zip(poly.loop_indices, quad):
            uv.data[li].uv = uvc
    mesh.materials.append(_material(tex_path))
    return bpy.data.objects.new(name, mesh)


def _load_terrain_z(paths, patch_id):
    """z_at closure for a patch (modified terrain preferred), or None."""
    from ..io.terrain_loader import load_terrain
    terr = os.path.join(paths['heightmaps'], "modified", f"h{patch_id}.obj")
    if not os.path.exists(terr):
        terr = os.path.join(paths['heightmaps'], f"h{patch_id}.obj")
    if not os.path.exists(terr):
        return None
    try:
        return _terrain_z_closure(load_terrain(terr))
    except Exception as e:
        logger.warning("solar: terrain load failed for %s: %s", patch_id, e)
        return None


# ---------------------------------------------------------------------------
# Operator: Import Solar (single patch)
# ---------------------------------------------------------------------------
class CONDOR_OT_import_solar(Operator):
    bl_idname = "condor.import_solar"
    bl_label = "Import Solar"
    bl_description = ("Generate ground solar farms from OSM + ESRI imagery as separate "
                      "'Solar_<patch>_n' objects (single patch only)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return bool(props.condor_path) and props.landscape_name != 'NONE'

    def execute(self, context):
        from .operators import resolve_condor_paths, ensure_patch_osm, _extra_obj_type
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata
        from ..io.terrain_loader import load_terrain

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}
        if not props.single_patch_mode or not props.patch_id:
            self.report({'WARNING'}, "Solar import works in SINGLE patch mode only.")
            return {'CANCELLED'}
        patch_id = str(props.patch_id)

        # LOD variants (same switch as buildings): LOD0 -> "" WITH posts, LOD1 -> "_LOD1" WITHOUT
        lod_variants = []
        if props.output_lod in ('LOD0', 'BOTH'):
            lod_variants.append(("", True))
        if props.output_lod in ('LOD1', 'BOTH'):
            lod_variants.append(("_LOD1", False))
        if not lod_variants:
            lod_variants.append(("", True))
        fresh_re = re.compile(rf"solar_{patch_id}_\d+")

        # per target LOD: a MERGED 'solar_farm'/baked solar is left alone (that LOD skipped); separate
        # 'Solar_<patch>_n' from an earlier import are removed so a re-import REBUILDS them.
        # Their tilt is read BEFORE they go, so a re-import (e.g. after a mask was redrawn)
        # keeps it. LOD0 wins if both LODs are there - the flip is per farm, not per LOD.
        active_lods = []
        scene_flips = {}
        idx_re = re.compile(rf"solar_{patch_id}_(\d+)")
        for suffix, posts in lod_variants:
            cn = f"Condor_{props.landscape_name}_{patch_id}{suffix}"
            pcol = bpy.data.collections.get(cn)
            if pcol is not None:
                solar_objs = [o for o in pcol.all_objects if _extra_obj_type(o.name) == "solar"]
                if any(not fresh_re.match(o.name.lower()) for o in solar_objs):
                    _log(f"LOD{suffix or '0'}: solar already present (merged/baked) - skipped")
                    continue
                for o in [o for o in solar_objs if fresh_re.match(o.name.lower())]:
                    m = idx_re.match(o.name.lower())
                    if m and "solar_flip" in o:
                        scene_flips.setdefault(int(m.group(1)), bool(o["solar_flip"]))
                    bpy.data.objects.remove(o, do_unlink=True)
            active_lods.append((suffix, posts))
        if not active_lods:
            self.report({'INFO'}, f"Solar already present in {patch_id} (merged/baked) - skipped.")
            return {'FINISHED'}

        if not ensure_patch_osm(paths, patch_id, True):
            self.report({'WARNING'}, f"OSM missing for {patch_id}.")
            return {'CANCELLED'}
        txt_path = next((p for p in (
            os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
            os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
        if not txt_path:
            self.report({'WARNING'}, f"h{patch_id}.txt missing.")
            return {'CANCELLED'}
        try:
            meta = load_patch_metadata(txt_path)
            projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
            root = ET.parse(os.path.join(paths['autogen'], f"map_{patch_id}.osm")).getroot()
        except Exception as e:
            self.report({'ERROR'}, f"Solar setup failed: {e}")
            return {'CANCELLED'}

        _setup_inv(projector, meta)
        z_at = _load_terrain_z(paths, patch_id)
        if z_at is None:
            self.report({'WARNING'}, f"Terrain missing for {patch_id}.")
            return {'CANCELLED'}

        cache_dir = os.path.join(paths['autogen'], "condor_esri_cache", patch_id)

        # solar polygons: from the patch OSM, else a cached solar OSM, else Overpass (cached)
        polys = _parse_solar(root, projector)
        cache_osm = os.path.join(cache_dir, f"solar_{patch_id}.osm")
        if not polys and os.path.exists(cache_osm):
            try:
                polys = _parse_solar(ET.parse(cache_osm).getroot(), projector)
            except Exception:
                pass
        if not polys:
            s_root = _fetch_solar_osm(meta)
            if s_root is not None:
                polys = _parse_solar(s_root, projector)
                if polys:
                    try:
                        os.makedirs(cache_dir, exist_ok=True)
                        ET.ElementTree(s_root).write(cache_osm)
                    except Exception:
                        pass
        if not polys:
            self.report({'WARNING'}, f"No ground solar farms found in {patch_id}.")
            return {'CANCELLED'}

        _copy_solar_texture(paths)          # put solar.dds into Autogen/Textures (skipped if there)
        tex_path = os.path.join(paths['autogen'], TEX_REL)
        cols = {}
        for suffix, _posts in active_lods:
            cn = f"Condor_{props.landscape_name}_{patch_id}{suffix}"
            col = bpy.data.collections.get(cn)
            if col is None:
                col = bpy.data.collections.new(cn)
                try:
                    context.scene.collection.children.link(col)
                except Exception:
                    pass
            cols[suffix] = col

        built = 0
        multi = len(polys) > 1
        flip_cache = _flip_cache_read(cache_dir)
        for idx, poly in enumerate(polys, 1):
            tag = f"_{idx}" if multi else ""
            # tilt: the scene wins (farm is still there), else the cache (Blender was
            # closed), else the default for a first import
            flip = scene_flips.get(idx, flip_cache.get(idx, IMPORT_FLIP))
            sat_path = os.path.join(cache_dir, f"esri_sat{tag}.jpg")
            luma = _esri_luma(poly, meta, cache_dir, sat_path)
            if luma is None:
                _log(f"farm {idx}: no ESRI imagery - skipped")
                continue
            _L, W, H, _z, _x0, _y0 = luma
            mask_path = os.path.join(cache_dir, f"esri_mask{tag}.jpg")

            # if a mask JPEG already exists (auto or HAND-EDITED), build from it and don't
            # overwrite it; otherwise compute the mask automatically and save it
            bitmap = _load_mask_jpeg(mask_path, W, H) if os.path.exists(mask_path) else None
            if bitmap is not None:
                _log(f"farm {idx}: using existing mask {os.path.basename(mask_path)}")
                apoly = list(poly)
                write_mask = False
            else:
                thr0 = _panel_threshold(luma, poly, meta)
                dx, dy = _estimate_poly_shift(luma, poly, meta, thr0)
                apoly = [(x + dx, y + dy) for (x, y) in poly] if (dx or dy) else list(poly)
                if dx or dy:
                    _log(f"farm {idx}: auto-aligned dx={dx:.1f} dy={dy:.1f} m")
                thr = _panel_threshold(luma, apoly, meta)
                if thr is None:
                    continue
                bitmap = _clean_panel_bitmap(luma, thr, apoly, meta)
                write_mask = True

            # each row carries its OWN orientation from the mask -> panels sit on their stripe
            rows = _mask_rows_local(bitmap, luma, meta)
            if not rows:
                _log(f"farm {idx}: empty mask - skipped")
                continue
            flat_rows = [v for r in rows for v in r]           # flat [cx,cy,dx,dy,hl, ...]
            for suffix, posts in active_lods:                  # LOD0 with posts, LOD1 without
                verts, faces, uvs = _build_rows(rows, z_at, flip, posts)
                if not verts:
                    continue
                obj = _make_object(f"Solar_{patch_id}_{idx}{suffix}", verts, faces, uvs, tex_path)
                obj["patch_id"] = patch_id
                obj["solar_patch"] = patch_id
                obj["solar_flip"] = int(flip)
                obj["solar_posts"] = int(posts)
                obj["solar_rows"] = flat_rows
                cols[suffix].objects.link(obj)
            try:
                _save_debug_jpegs(luma, bitmap, apoly, meta, cache_dir, tag=tag,
                                  write_mask=write_mask)
            except Exception as e:
                _log(f"debug JPEG save failed: {e}")
            built += 1

        if built == 0:
            self.report({'WARNING'}, f"Solar: nothing built for {patch_id}.")
            return {'CANCELLED'}
        removed = _delete_tiles(cache_dir)   # stitched jpgs exist now -> raw tiles no longer needed
        self.report({'INFO'}, f"Solar: built {built} farm(s) in {patch_id} "
                              f"({removed} tiles removed; esri_*.jpg kept in condor_esri_cache/{patch_id})")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Flip Solar (turn the selected farm's tilt + posts over)
# ---------------------------------------------------------------------------
class CONDOR_OT_flip_solar(Operator):
    bl_idname = "condor.flip_solar"
    bl_label = "Flip Solar"
    bl_description = "Flip the tilt (and posts) of the SELECTED solar farm object(s)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any("solar_rows" in o for o in context.selected_objects)

    def execute(self, context):
        from .operators import resolve_condor_paths
        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        targets = [o for o in context.selected_objects if "solar_rows" in o]
        if not targets:
            self.report({'WARNING'}, "Select a Solar_* object first.")
            return {'CANCELLED'}

        z_cache = {}
        done = 0
        for obj in targets:
            patch_id = obj.get("solar_patch")
            if not patch_id:
                continue
            if patch_id not in z_cache:
                z_cache[patch_id] = _load_terrain_z(paths, patch_id)
            z_at = z_cache[patch_id]
            if z_at is None:
                continue
            flat = list(obj["solar_rows"])
            rows = [tuple(flat[i:i + 5]) for i in range(0, len(flat), 5)]
            new_flip = not bool(obj.get("solar_flip", 0))
            verts, faces, uvs = _build_rows(rows, z_at, new_flip, bool(obj.get("solar_posts", 1)))
            if not verts:
                continue
            old_mesh = obj.data
            mesh = bpy.data.meshes.new(obj.name)
            mesh.from_pydata([tuple(v) for v in verts], [], [list(f) for f in faces])
            mesh.update()
            uv = mesh.uv_layers.new(name="UVMap")
            for poly, quad in zip(mesh.polygons, uvs):
                for li, uvc in zip(poly.loop_indices, quad):
                    uv.data[li].uv = uvc
            mesh.materials.append(_material())
            obj.data = mesh
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
            obj["solar_flip"] = int(new_flip)
            # remember it, so the tilt survives closing Blender (a re-import with the
            # object still in the scene reads it straight off the object)
            m = re.match(rf"solar_{patch_id}_(\d+)", obj.name.lower())
            if m:
                _flip_cache_write(os.path.join(paths['autogen'], "condor_esri_cache", patch_id),
                                  int(m.group(1)), new_flip)
            done += 1

        self.report({'INFO'}, f"Flipped {done} solar farm(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Merge Solar (all Solar_<patch>_n -> one 'solar_farm' per patch)
# ---------------------------------------------------------------------------
class CONDOR_OT_merge_solar(Operator):
    bl_idname = "condor.merge_solar"
    bl_label = "Merge Solar"
    bl_description = "Merge all Solar_<patch>_n objects per patch into one 'solar_farm'"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _is_solar(name):
        # only freshly-imported 'Solar_<patch>_<n>' (also the '_LOD1' variant) - 'solar_farm' is NOT
        return bool(re.match(r'solar_\d{6}_\d+', name.lower()))

    @staticmethod
    def _condor_collection(obj):
        for col in obj.users_collection:
            if col.name.startswith("Condor_"):
                return col
        return None

    @classmethod
    def poll(cls, context):
        return any(cls._is_solar(o.name) for o in bpy.data.objects)

    def execute(self, context):
        solars = [o for o in bpy.data.objects
                  if self._is_solar(o.name) and o.name in context.view_layer.objects]
        if not solars:
            self.report({'WARNING'}, "No solar objects found")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        merged_patches = {o.get("solar_patch") for o in solars if o.get("solar_patch")}

        by_col = {}
        for obj in solars:
            col = self._condor_collection(obj)
            by_col.setdefault(col.name if col else None, []).append(obj)

        target_mat = bpy.data.materials.get(MAT_NAME)
        for col_name, objs in by_col.items():
            bpy.ops.object.select_all(action='DESELECT')
            for obj in objs:
                obj.select_set(True)
            context.view_layer.objects.active = objs[0]
            bpy.ops.object.join()
            merged = context.active_object
            merged.name = "solar_farm"
            for k in ("solar_rows", "solar_angle", "solar_flip"):
                if k in merged:
                    del merged[k]
            if target_mat:
                merged.data.materials.clear()
                merged.data.materials.append(target_mat)
            target_col = bpy.data.collections.get(col_name) if col_name else None
            if target_col:
                for c in list(merged.users_collection):
                    c.objects.unlink(merged)
                target_col.objects.link(merged)

        # the mask planes of the merged patches have done their job -> clear them from the
        # outliner. Planes of OTHER patches (work in progress) are left alone.
        for pl in [o for o in bpy.data.objects
                   if "solar_mask_edit" in o and o.get("solar_patch") in merged_patches]:
            bpy.data.objects.remove(pl, do_unlink=True)

        self.report({'INFO'}, f"Merged solar for {len(by_col)} collection(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Mask alignment: lay the mask on the terrain, move/rotate it, save it back
# ---------------------------------------------------------------------------
def _tile_bounds(poly, meta):
    """(z, x0, y0, W, H) of the ESRI tile mosaic over a farm - same maths as _esri_luma."""
    z = ESRI_ZOOM
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    lat_a, lon_a = _patch_to_latlon(min(xs), min(ys), meta)
    lat_b, lon_b = _patch_to_latlon(max(xs), max(ys), meta)
    txa, tya = _deg_to_tile_f(lat_a, lon_a, z)
    txb, tyb = _deg_to_tile_f(lat_b, lon_b, z)
    x0, x1 = int(min(txa, txb)), int(max(txa, txb))
    y0, y1 = int(min(tya, tyb)), int(max(tya, tyb))
    return z, x0, y0, (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256


def _pix_to_patch(col, row, z, x0, y0, meta):
    n = 2 ** z
    lon = (x0 + col / 256.0) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y0 + row / 256.0) / n))))
    return _latlon_to_patch(lat, lon, meta)


def _patch_to_pix_f(wx, wy, z, x0, y0, meta):
    n = 2 ** z
    lat, lon = _patch_to_latlon(wx, wy, meta)
    gx = (lon + 180.0) / 360.0 * n
    gy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return (gx - x0) * 256.0, (gy - y0) * 256.0


def _mask_overlay_material(name, mask_path):
    """Material that shows the mask on a plane: RED where the mask is black (panel), the rest
    transparent, so the terrain texture shows through for alignment."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.blend_method = 'BLEND'
    nt = mat.node_tree
    for nd in list(nt.nodes):
        nt.nodes.remove(nd)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(mask_path, check_existing=False)
    tex.interpolation = 'Closest'
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    sub = nt.nodes.new("ShaderNodeMath"); sub.operation = 'SUBTRACT'
    sub.inputs[0].default_value = 1.0            # 1 - luminance -> black=1, white=0
    emis = nt.nodes.new("ShaderNodeEmission"); emis.inputs[0].default_value = (1.0, 0.0, 0.0, 1.0)
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(tex.outputs["Color"], bw.inputs[0])
    nt.links.new(bw.outputs[0], sub.inputs[1])
    nt.links.new(sub.outputs[0], mix.inputs[0])
    nt.links.new(transp.outputs[0], mix.inputs[1])
    nt.links.new(emis.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def _solar_polys_no_fetch(paths, patch_id, projector, cache_dir):
    """Solar polygons from the patch OSM or the cached solar_<patch>.osm (no Overpass call -
    Import already fetched/cached them)."""
    polys = []
    osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
    if os.path.exists(osm_path):
        try:
            polys = _parse_solar(ET.parse(osm_path).getroot(), projector)
        except Exception:
            pass
    if not polys:
        cache_osm = os.path.join(cache_dir, f"solar_{patch_id}.osm")
        if os.path.exists(cache_osm):
            try:
                polys = _parse_solar(ET.parse(cache_osm).getroot(), projector)
            except Exception:
                pass
    return polys


class CONDOR_OT_solar_mask_edit(Operator):
    bl_idname = "condor.solar_mask_edit"
    bl_label = "Mask on terrain"
    bl_description = ("Float each farm's esri_mask.jpg flat above the terrain (RED = panels) so you "
                      "can MOVE/ROTATE it onto the panels in the Condor texture, then Save mask")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return bool(props.condor_path) and props.landscape_name != 'NONE'

    def execute(self, context):
        from .operators import resolve_condor_paths
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths or not props.single_patch_mode or not props.patch_id:
            self.report({'WARNING'}, "Set Condor paths and SINGLE patch mode.")
            return {'CANCELLED'}
        patch_id = str(props.patch_id)
        txt_path = next((p for p in (
            os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
            os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
        if not txt_path:
            self.report({'WARNING'}, f"h{patch_id}.txt missing.")
            return {'CANCELLED'}
        meta = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
        _setup_inv(projector, meta)
        z_at = _load_terrain_z(paths, patch_id)
        cache_dir = os.path.join(paths['autogen'], "condor_esri_cache", patch_id)

        polys = _solar_polys_no_fetch(paths, patch_id, projector, cache_dir)
        if not polys:
            self.report({'WARNING'}, "No solar polygons - run Import first.")
            return {'CANCELLED'}

        made = 0
        multi = len(polys) > 1
        for idx, poly in enumerate(polys, 1):
            tag = f"_{idx}" if multi else ""
            mask_path = os.path.join(cache_dir, f"esri_mask{tag}.jpg")
            if not os.path.exists(mask_path):
                continue
            z, x0, y0, W, H = _tile_bounds(poly, meta)
            corners = [_pix_to_patch(0, 0, z, x0, y0, meta),   # NW
                       _pix_to_patch(W, 0, z, x0, y0, meta),   # NE
                       _pix_to_patch(W, H, z, x0, y0, meta),   # SE
                       _pix_to_patch(0, H, z, x0, y0, meta)]   # SW
            cx = sum(c[0] for c in corners) / 4.0
            cy = sum(c[1] for c in corners) / 4.0
            zc = (z_at(cx, cy) if z_at else 0.0) + MASK_PLANE_H
            verts = [(c[0], c[1], zc) for c in corners]
            name = f"SolarMask_{patch_id}{tag}"
            old = bpy.data.objects.get(name)
            if old is not None:
                bpy.data.objects.remove(old, do_unlink=True)
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
            mesh.update()
            uvl = mesh.uv_layers.new(name="UVMap")
            for li, uc in zip(mesh.polygons[0].loop_indices, [(0, 1), (1, 1), (1, 0), (0, 0)]):
                uvl.data[li].uv = uc
            mesh.materials.append(_mask_overlay_material(name, mask_path))
            obj = bpy.data.objects.new(name, mesh)
            # Save mask can only take a FLAT move/turn: tilting or scaling the plane would
            # skew the saved mask, and the imagery is top-down so neither is ever wanted.
            # Locked here so it cannot happen by a slip of the gizmo. Moving (incl. up/down
            # to see it) and turning around Z stay free.
            obj.lock_rotation[0] = obj.lock_rotation[1] = True
            obj.lock_scale[0] = obj.lock_scale[1] = True
            obj["solar_mask_edit"] = 1
            obj["solar_patch"] = patch_id
            obj["solar_maskpath"] = mask_path
            obj["solar_geo"] = [z, x0, y0, W, H]
            context.scene.collection.objects.link(obj)
            made += 1

        if made == 0:
            self.report({'WARNING'}, "No esri_mask.jpg found - run Import first.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{made} mask plane(s) on terrain - move/rotate them, then 'Save mask'")
        return {'FINISHED'}


class CONDOR_OT_solar_mask_save(Operator):
    bl_idname = "condor.solar_mask_save"
    bl_label = "Save mask"
    bl_description = "Bake the moved/rotated mask plane(s) back into esri_mask.jpg, then remove them"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any("solar_mask_edit" in o for o in bpy.data.objects)

    def execute(self, context):
        from .operators import resolve_condor_paths
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        planes = [o for o in context.selected_objects if "solar_mask_edit" in o] \
            or [o for o in bpy.data.objects if "solar_mask_edit" in o]
        saved = 0
        untouched = 0
        for pl in planes:
            patch_id = pl.get("solar_patch")
            mask_path = pl.get("solar_maskpath")
            geo = pl.get("solar_geo")
            if not (patch_id and mask_path and geo):
                continue

            # 2D affine A the user applied (plane local verts ARE patch coords, object was identity):
            #   world_patch = A(orig_patch).  We need orig source = A^-1(target patch).
            M = pl.matrix_world
            moved = (any(abs(M[r][k] - (1.0 if r == k else 0.0)) > 1e-6
                         for r in (0, 1) for k in (0, 1))
                     or abs(M[0][3]) > 1e-6 or abs(M[1][3]) > 1e-6)
            if not moved:
                # nobody moved this one -> the mask on disk is already right. Do NOT rewrite it:
                # JPEG is lossy, so re-saving would only blur the edges for nothing. Just clear
                # the plane away.
                bpy.data.objects.remove(pl, do_unlink=True)
                untouched += 1
                continue

            z, x0, y0, W, H = int(geo[0]), int(geo[1]), int(geo[2]), int(geo[3]), int(geo[4])
            txt_path = next((p for p in (
                os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
            if not txt_path:
                continue
            meta = load_patch_metadata(txt_path)
            projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
            _setup_inv(projector, meta)
            Bold = _load_mask_jpeg(mask_path, W, H)
            if Bold is None:
                continue

            a, b, tx = M[0][0], M[0][1], M[0][3]
            c, d, ty = M[1][0], M[1][1], M[1][3]
            det = a * d - b * c
            if abs(det) < 1e-9:
                continue
            ia, ib = d / det, -b / det
            ic, id_ = -c / det, a / det
            itx = -(ia * tx + ib * ty)
            ity = -(ic * tx + id_ * ty)

            def src_of(col, row):
                qx, qy = _pix_to_patch(col, row, z, x0, y0, meta)     # target patch
                sx = ia * qx + ib * qy + itx                          # source patch = A^-1(q)
                sy = ic * qx + id_ * qy + ity
                return _patch_to_pix_f(sx, sy, z, x0, y0, meta)

            # pixel->source-pixel is ~affine; fit it from 3 corners (fast, no per-pixel trig)
            s0 = src_of(0, 0); s1 = src_of(W, 0); s2 = src_of(0, H)
            dcu, dru = (s1[0] - s0[0]) / W, (s1[1] - s0[1]) / W
            dcv, drv = (s2[0] - s0[0]) / H, (s2[1] - s0[1]) / H
            Bnew = [bytearray(W) for _ in range(H)]
            for row in range(H):
                bc, br = s0[0] + dcv * row, s0[1] + drv * row
                Bn = Bnew[row]
                for col in range(W):
                    sc = int(bc + dcu * col); sr = int(br + dru * col)
                    if 0 <= sc < W and 0 <= sr < H and Bold[sr][sc] == 1:
                        Bn[col] = 1

            buf = [0.0] * (W * H * 4)
            for y in range(H):
                yy = H - 1 - y
                Bn = Bnew[y]
                for x in range(W):
                    i = (yy * W + x) * 4
                    val = 0.0 if Bn[x] == 1 else 1.0     # black = panel
                    buf[i] = buf[i + 1] = buf[i + 2] = val; buf[i + 3] = 1.0
            img = bpy.data.images.new("solar_mask_save", W, H)
            img.pixels.foreach_set(buf)
            img.filepath_raw = mask_path
            img.file_format = 'JPEG'
            img.save()
            bpy.data.images.remove(img)
            bpy.data.objects.remove(pl, do_unlink=True)
            saved += 1

        if saved == 0 and untouched == 0:
            self.report({'WARNING'}, "No mask plane to save.")
            return {'CANCELLED'}
        extra = f", {untouched} unchanged (not rewritten)" if untouched else ""
        if saved == 0:
            self.report({'INFO'}, f"No mask was moved{extra} - nothing to rebuild")
            return {'FINISHED'}
        self.report({'INFO'}, f"Saved {saved} aligned mask(s){extra} - now press Import to rebuild")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# SCAN: which patches of this scenery have a solar farm at all
# ---------------------------------------------------------------------------
# solar_scan.py is a STANDALONE command-line helper (stdlib only, no bpy). It is run in
# its OWN process, because it asks Overpass band by band and waits in between - minutes
# of work that would otherwise freeze Blender solid.
SCAN_SCRIPT = "solar_scan.py"
FARMS_FILE = "solar_farms.txt"


def _farms_txt_path(context):
    """Where solar_farms.txt would be, WITHOUT touching the disk - draw() must not
    create folders, so resolve_condor_paths() cannot be used here."""
    props = context.scene.condor_buildings
    if not props.condor_path or props.landscape_name in ('NONE', ''):
        return None
    return os.path.join(bpy.path.abspath(props.condor_path), "Landscapes",
                        props.landscape_name, "Working", "Autogen", FARMS_FILE)


def _python_exe():
    """Blender's own Python. sys.executable is it in current builds; older ones point at
    blender.exe, hence the fallback next to sys.prefix."""
    exe = sys.executable
    if exe and os.path.basename(exe).lower().startswith("python"):
        return exe
    name = "python.exe" if sys.platform == "win32" else "python"
    cand = os.path.join(sys.prefix, "bin", name)
    return cand if os.path.exists(cand) else exe


class CONDOR_OT_scan_solar_farms(Operator):
    """Modal, but the work happens in a SEPARATE process: the scan takes minutes and would
    freeze Blender solid if it ran here. So we start solar_scan.py, and this operator only
    watches it on a timer - Blender stays fully usable, Esc leaves it running, and once the
    scan finishes the panel is redrawn so the button disappears on its own."""
    bl_idname = "condor.scan_solar_farms"
    bl_label = "Scan solar farms"
    bl_description = ("List which patches of this scenery have a solar farm, so you don't have to "
                      "try them all. Runs OUTSIDE Blender in its own window and takes minutes; "
                      "writes Working/Autogen/solar_farms.txt")

    _timer = None
    _proc = None

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        return bool(props.condor_path) and props.landscape_name not in ('NONE', '')

    @staticmethod
    def _redraw(context):
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    def execute(self, context):
        import subprocess
        from .operators import resolve_condor_paths

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}
        if not os.path.isdir(paths['heightmaps']):
            self.report({'WARNING'}, f"No Heightmaps folder: {paths['heightmaps']}")
            return {'CANCELLED'}
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), SCAN_SCRIPT)
        if not os.path.exists(script):
            self.report({'ERROR'}, f"{SCAN_SCRIPT} not found next to solar.py")
            return {'CANCELLED'}

        cmd = [_python_exe(), script,
               bpy.path.abspath(props.condor_path), props.landscape_name]
        try:
            # its own console window -> the progress is visible and Blender stays usable
            self._proc = subprocess.Popen(
                cmd, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except Exception as e:
            self.report({'ERROR'}, f"Scan could not start: {e}")
            return {'CANCELLED'}
        _log(f"scan started: {' '.join(cmd)}")

        # watch it, so the button can go away by itself when it's done
        self._timer = context.window_manager.event_timer_add(2.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "Solar scan running in its own window (takes minutes) - the button "
                              "disappears by itself when it's done")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}          # never swallow input - Blender must stay usable
        if self._proc.poll() is None:
            return {'PASS_THROUGH'}          # still running
        rc = self._proc.returncode
        self._cleanup(context)
        self._redraw(context)                # the list is there now -> the button goes
        if rc == 0:
            self.report({'INFO'}, "Solar scan finished - see solar_farms.txt")
        else:
            self.report({'WARNING'}, f"Solar scan ended with code {rc} - see its window; "
                                     "press Scan again to pick up from cache")
        return {'FINISHED'}

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def cancel(self, context):
        # Blender took the modal away (file loaded, window closed): stop watching, but LEAVE
        # the scan running - it is its own process and its result is worth keeping.
        self._cleanup(context)


# ---------------------------------------------------------------------------
# Panel row (called from panels.py inside the "Other objects" box)
# ---------------------------------------------------------------------------
def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Solar", icon='LIGHT_SUN')
    row = box.row(align=True)
    row.operator("condor.import_solar", text="Import", icon='IMPORT')
    row.operator("condor.merge_solar", text="Merge", icon='AUTOMERGE_ON')
    box.operator("condor.flip_solar", text="Flip selected", icon='MOD_MIRROR')
    row = box.row(align=True)
    row.operator("condor.solar_mask_edit", text="Mask on terrain", icon='IMAGE_PLANE')
    row.operator("condor.solar_mask_save", text="Save mask", icon='FILE_TICK')
    # only offered while the list isn't there; delete the file and it comes back
    p = _farms_txt_path(context)
    if p and not os.path.exists(p):
        box.operator("condor.scan_solar_farms", text="Scan solar farms", icon='VIEWZOOM')


# ---------------------------------------------------------------------------
# OSM query wrap: also fetch solar farms when a patch OSM is downloaded
# ---------------------------------------------------------------------------
def _patch_overpass_query():
    from . import osm_downloader as _osm
    if getattr(_osm, "_solar_patched", False):
        return
    _orig = _osm.build_overpass_query

    def _patched(lat_min, lat_max, lon_min, lon_max, *a, **k):
        q = _orig(lat_min, lat_max, lon_min, lon_max, *a, **k)
        bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
        extra = (
            f'  way["power"="plant"]["plant:source"="solar"]({bbox});\n'
            f'  relation["power"="plant"]["plant:source"="solar"]({bbox});\n'
            f'  way["power"="generator"]["generator:source"="solar"]({bbox});\n'
            f'  relation["power"="generator"]["generator:source"="solar"]({bbox});'
        )
        return q.replace("\n);", "\n" + extra + "\n);", 1)

    _osm._solar_orig_query = _orig
    _osm.build_overpass_query = _patched
    _osm._solar_patched = True


def _unpatch_overpass_query():
    from . import osm_downloader as _osm
    if getattr(_osm, "_solar_patched", False):
        _osm.build_overpass_query = _osm._solar_orig_query
        _osm._solar_patched = False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
_classes = [CONDOR_OT_import_solar, CONDOR_OT_flip_solar, CONDOR_OT_merge_solar,
            CONDOR_OT_solar_mask_edit, CONDOR_OT_solar_mask_save,
            CONDOR_OT_scan_solar_farms]


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    try:
        _patch_overpass_query()
    except Exception:
        pass
    # so the exporter writes the right material/texture into the MTL (same as bridges)
    try:
        from .. import config
        config.TEXTURE_MAP.setdefault(MAT_NAME, TEX_FILE)
        config.MATERIAL_ALIAS.setdefault(OBJECT_NAME, MAT_NAME)
    except Exception:
        pass


def unregister():
    try:
        _unpatch_overpass_query()
    except Exception:
        pass
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
