"""
Bridges - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Transmitter feature: everything lives in THIS one file plus two tiny,
clearly-marked, try/except-guarded hooks in:
  - __init__.py       (register/unregister this module)
  - blender/panels.py (draw the Bridges row inside "Other objects")

Delete this file (or comment out those two hooks) and the plugin falls back to
exactly the previous behaviour - nothing else depends on it. It does NOT touch
buildings, pylons, transmitters, the exporter, config.py or osm_downloader.py:
the extra OSM tags it needs (rivers) are added at runtime by wrapping the query.

What it does (the whole core):
  A "bridge" in OSM is a way tagged bridge=* on a road (highway=*) or a railway.
  Only ROAD / MOTORWAY / RAILWAY bridges are used (no footways/cycleways), and a
  bridge is only built when it actually crosses:
    * a valley - the terrain under the deck drops more than VALLEY_MIN_CLEARANCE
      below the straight abutment-to-abutment deck line; or
    * a river  - the bridge centreline crosses a waterway (river/stream/canal).
  Each qualifying bridge gets a procedural deck + side railings + pillars down to
  the terrain, merged into a single 'bridges' object (grey material for now; a
  real texture can be added later).
"""

import os
import math
import logging

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

OBJECT_NAME = "bridges"
MAT_NAME = "condor_bridge"
TEX_FILE = "Bridge.dds"           # file-mode texture (copied into autogen/Textures on Batch)

PATCH_HALF = 2880.0
PATCH_SIZE = 5760.0

# --- geometry tuning -------------------------------------------------------
DECK_THICKNESS = 0.8          # deck slab thickness (m)
RAIL_HEIGHT = 1.2             # railing height above the deck top (m)
ARCH_CLEARANCE = 5.0          # mid-span is bowed up to this height above the terrain (m)
                              # so a bridge over flat water/ground does not sink into it
VALLEY_MIN_CLEARANCE = 3.0    # terrain must drop at least this far below the deck (m)
# ...and the drop must be at least this WIDE, so a narrow rail/road cutting is not
# mistaken for a valley - only a genuinely wide gap (valley / river) qualifies.
VALLEY_MIN_WIDTH = 25.0
PILLAR_MIN_BRIDGE = 20.0      # bridges shorter than this get NO pillar
PILLAR_TARGET_SPACING = 30.0  # else ~1 pillar per this many metres, spaced evenly
PILLAR_HALF = 0.9            # square pillar half-size (m)
PILLAR_MIN_DROP = 1.5        # minimum deck-to-terrain drop to place a pillar (m)
PILLAR_EMBED = 2.0          # pillar reaches this far BELOW the terrain (anchored, not floating)
MIN_BRIDGE_LENGTH = 20.0     # bridges shorter than this are skipped (small culverts)
# Parallel bridges of the SAME class within this lateral distance and running
# nearly parallel are merged into ONE wider deck (multi-track rail bridges are
# mapped in OSM as one way per track, which would otherwise stack decks).
PARALLEL_MERGE_LATERAL = 8.0   # only truly ADJACENT same-type tracks (a multi-track bridge)
PARALLEL_MERGE_COS = 0.97    # ~14 deg direction tolerance
SEG_LEN = 10.0              # deck split into pieces of at most this length (m), one texture tile each
ALIGN_LATERAL = 45.0        # parallel decks within this distance get the SAME length (aligned ends)
# Snapping a deck end across the water to the bank, using the WATER from the patch
# orthophoto's ALPHA channel (the heightmap can't tell water reliably; the photo can).
EXTEND_MAX = 200.0          # how far to look for the bank (m)
EXTEND_STEP = 4.0
BANK_MARGIN = 8.0           # how far the deck sits onto each bank (m)
# Water detection from t<patch>.dds alpha. Both may need flipping after the first
# test run (orientation / alpha polarity depend on the landscape's textures).
WATER_ALPHA_THRESHOLD = 0.5
WATER_IS_LOW_ALPHA = True   # water = alpha BELOW threshold (verified on t036024.dds)
ORTHO_V_FLIP = True         # DDS byte rows are top-first -> flip V (verified)

# --- OSM classification ----------------------------------------------------
ROAD_HIGHWAY = {
    "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "service", "road",
    "primary_link", "secondary_link", "tertiary_link",
}
MOTORWAY_HIGHWAY = {"motorway", "trunk", "motorway_link", "trunk_link"}
# Only real railways - NOT trams / light rail / subway (those run on the street /
# road bridge, they are not a separate railway bridge).
RAIL_RAILWAY = {"rail", "narrow_gauge"}
# Only real rivers count (no streams/canals).
RIVER_WATERWAY = {"river"}
BRIDGE_WIDTH = {"motorway": 28.0, "road": 7.0, "rail": 5.0}   # motorway = whole dual carriageway, ONE 28 m deck
SIDEWALK_WIDTH = 2.0        # footpath added to EACH side of a ROAD bridge (m); motorway has none

# --- texture bands (Bridge.dds, 512 wide x 1536 tall = 3 stacked strips) -----
# Blender V=0 is the BOTTOM of the image, so from the bottom up:
#   bottom strip = rails (koleje, left) + railing (zabradli w/ dark handrail, right)
#   middle strip = road  (silnice 11 m: footpath | lanes | footpath)
#   top strip    = motorway (dalnice: thin edge | lanes | thin divider | lanes | edge)
# Each deck maps its WIDTH across U and tiles its strip ALONG the length in V.
V_RAIL_BAND = (0.0, 1.0 / 3.0)
V_ROAD_BAND = (1.0 / 3.0, 2.0 / 3.0)
V_MOTORWAY_BAND = (2.0 / 3.0, 1.0)
U_TRACK = (0.0, 0.78)      # koleje occupy the left 78 % of the bottom strip
U_FENCE = (0.805, 1.0)     # zabradli (railing), set from Blender: base U=0.805 -> top(handrail) U=1.0
SIDEWALK_UV = (0.02, 0.5)  # a plain grey footpath pixel (road strip) for deck sides / soffit
                           # / pillars - "concrete" of the structure
ONE_TRACK_U = 0.134        # U width of ONE track in the koleje strip (set from Blender by the
                           # user: one 5 m track = U 0..0.134). A deck N*5 m wide maps U 0..N*0.134
                           # -> exactly N tracks (5m=1, 10m=2, ...). Change this to match the texture.


# ---------------------------------------------------------------------------
# OSM parsing
# ---------------------------------------------------------------------------
def _classify(tags):
    """Map a way's tags to a bridge class, or None if not a road/rail bridge."""
    bridge = tags.get("bridge")
    if not bridge or bridge == "no":
        return None
    hw = tags.get("highway")
    if hw in MOTORWAY_HIGHWAY:
        return "motorway"
    if hw in ROAD_HIGHWAY:
        return "road"
    if tags.get("railway") in RAIL_RAILWAY:
        return "rail"                        # service/yard flagged in _parse, gated on water
    return None


def _parse(root, projector):
    """Return (bridges, waterways, g_moto, g_other). bridges = list of dicts
    {class,width,points:[(x,y,in_patch)]}; waterways = list of [(x,y),...].
    g_moto / g_other = GROUND (non-bridge, non-tunnel) ways used to detect a
    grade-separated crossing: g_moto = dalnice (motorway) lines, g_other = silnice +
    koleje (road + rail) lines. Each is a list of [(x,y),...]."""
    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}

    def project_way(w):
        pts = []
        for nd in w.findall("nd"):
            nid = nd.get("ref")
            if nid not in node_coords:
                continue
            lat, lon = node_coords[nid]
            x, y = projector.project(lat, lon)
            in_patch = -PATCH_HALF <= x <= PATCH_HALF and -PATCH_HALF <= y <= PATCH_HALF
            pts.append((x, y, in_patch))
        return pts

    bridges, waterways, g_moto, g_other = [], [], [], []
    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if tags.get("waterway") in RIVER_WATERWAY:
            pts = project_way(w)
            if len(pts) >= 2:
                waterways.append([(x, y) for (x, y, _ip) in pts])
            continue
        cls = _classify(tags)
        if cls is None:
            # Not a road/rail bridge. Is it a GROUND (non-bridge, non-tunnel) road /
            # motorway / rail? Those are the "obstacle below" for a grade-separated
            # crossing (a nadjezd). A bridge/tunnel way is NOT ground and is skipped.
            br = tags.get("bridge")
            tn = tags.get("tunnel")
            if (br and br != "no") or (tn and tn != "no"):
                continue
            hw = tags.get("highway")
            rw = tags.get("railway")
            if hw in MOTORWAY_HIGHWAY:
                pts = project_way(w)
                if len(pts) >= 2:
                    g_moto.append([(x, y) for (x, y, _ip) in pts])
            elif hw in ROAD_HIGHWAY or rw in RAIL_RAILWAY:
                pts = project_way(w)
                if len(pts) >= 2:
                    g_other.append([(x, y) for (x, y, _ip) in pts])
            continue
        pts = project_way(w)
        if len(pts) >= 2:
            width = BRIDGE_WIDTH.get(cls, 7.0)
            if cls == "road":                    # footpath on each side (motorway has none)
                width += 2.0 * SIDEWALK_WIDTH
            bridges.append({"class": cls, "width": width, "points": pts,
                            "service": bool(tags.get("service") or tags.get("usage") == "industrial")})
    return bridges, waterways, g_moto, g_other


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
        b = terrain.bbox
        cx = min(max(x, b.min_x + 1.0), b.max_x - 1.0)
        cy = min(max(y, b.min_y + 1.0), b.max_y - 1.0)
        if cx != x or cy != y:
            bb2 = BBox(cx - 0.5, cy - 0.5, cx + 0.5, cy + 0.5)
            for ti in terrain.get_triangles_in_bbox(bb2):
                tri = terrain.triangles[ti]
                if tri.contains_point_2d(Point2D(cx, cy)):
                    z = tri.z_at_xy(cx, cy)
                    if z is not None:
                        return z
        return terrain.z_min

    return z_at


def _seg_intersect(p1, p2, p3, p4):
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = ccw(p3, p4, p1), ccw(p3, p4, p2)
    d3, d4 = ccw(p1, p2, p3), ccw(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _crosses_waterway(xy, waterways, wbounds):
    """Does the bridge centreline cross any water course? A cheap bounding-box
    pre-filter skips water courses nowhere near the bridge, so a big river network
    can't make this slow."""
    br = _bbox(xy)
    for wline, wb in zip(waterways, wbounds):
        if not _bbox_overlap(br, wb):
            continue
        for i in range(len(xy) - 1):
            a, b = xy[i], xy[i + 1]
            for j in range(len(wline) - 1):
                if _seg_intersect(a, b, wline[j], wline[j + 1]):
                    return True
    return False


def _crosses_road(xy, cls, g_moto, gm_bounds, g_other, go_bounds):
    """Grade-separated crossing (nadjezd) that qualifies a bridge - but ONLY when a
    DALNICE (motorway) is on one side of the crossing:
      * ANY bridge that crosses a ground MOTORWAY,          and
      * a MOTORWAY bridge that crosses a ground road / rail.
    So road×road, road×rail and rail×rail crossings (yards, stations, factories) are
    NOT counted. Reuses the same line-vs-line test as the waterway check."""
    if _crosses_waterway(xy, g_moto, gm_bounds):        # crosses a ground dalnice
        return True
    if cls == "motorway" and _crosses_waterway(xy, g_other, go_bounds):
        return True
    return False


def _alpha_crosses(xy, is_water):
    """True if any point along the centreline is over water (orthophoto alpha)."""
    for i in range(len(xy) - 1):
        ax, ay = xy[i]
        bx, by = xy[i + 1]
        seg = math.hypot(bx - ax, by - ay)
        n = max(1, int(seg / 5.0))
        for k in range(n + 1):
            t = k / n
            if is_water(ax + (bx - ax) * t, ay + (by - ay) * t):
                return True
    return False


def _deck_line(xy, z_at):
    n = len(xy)
    cum = [0.0] * n
    for i in range(1, n):
        cum[i] = cum[i - 1] + math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1])
    z0, z1 = z_at(*xy[0]), z_at(*xy[-1])
    total = cum[-1] if cum[-1] > 1e-6 else 1.0
    z_deck = [z0 + (z1 - z0) * (cum[i] / total) for i in range(n)]
    return cum, z_deck


def _valley(xy, cum, z_deck, z_at, step=5.0):
    """True only for a REAL valley: the terrain dips at least VALLEY_MIN_CLEARANCE
    below the LOWER of the two abutments (the bank level) over a stretch at least
    VALLEY_MIN_WIDTH long. Checking against the lower bank - not just the straight
    deck line - is what rejects a SLOPED bridge on flat/sloping ground (its chord
    floats above the terrain and used to look like a fake valley). A river reads as
    a valley too (wide low area). Sampling along the length handles 2-node bridges."""
    total = cum[-1]
    if total < 1e-6:
        return False
    banks = min(z_deck[0], z_deck[-1])   # lower abutment = bank level
    low_len = 0.0
    s = step
    while s < total:
        x, y, zt = _sample(xy, cum, z_deck, s)
        tz = z_at(x, y)
        # a valley floor sits below BOTH the deck line and the lower bank
        if (zt - tz) >= VALLEY_MIN_CLEARANCE and (banks - tz) >= VALLEY_MIN_CLEARANCE:
            low_len += step
        s += step
    return low_len >= VALLEY_MIN_WIDTH


def _perp(xy, i):
    n = len(xy)
    dx = dy = 0.0
    if i > 0:
        ax, ay = xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]
        d = math.hypot(ax, ay)
        if d > 1e-9:
            dx += ax / d; dy += ay / d
    if i < n - 1:
        bx, by = xy[i + 1][0] - xy[i][0], xy[i + 1][1] - xy[i][1]
        d = math.hypot(bx, by)
        if d > 1e-9:
            dx += bx / d; dy += by / d
    d = math.hypot(dx, dy)
    if d < 1e-9:
        return (0.0, 1.0)
    return (-dy / d, dx / d)


def _sample(xy, cum, z_deck, s):
    for i in range(len(cum) - 1):
        if s <= cum[i + 1]:
            seg = cum[i + 1] - cum[i]
            t = 0.0 if seg < 1e-9 else (s - cum[i]) / seg
            return (xy[i][0] + (xy[i + 1][0] - xy[i][0]) * t,
                    xy[i][1] + (xy[i + 1][1] - xy[i][1]) * t,
                    z_deck[i] + (z_deck[i + 1] - z_deck[i]) * t)
    return xy[-1][0], xy[-1][1], z_deck[-1]


def _load_water_sampler(path):
    """Read the patch orthophoto (t<patch>.dds, DXT3) DIRECTLY from disk - Blender's
    DDS loader is unreliable - and return is_water(x, y) from its ALPHA channel
    (water = low alpha). Decodes only the alpha of each 4x4 DXT3 block on demand.
    Returns None if the file is missing or is not a DXT3 DDS."""
    if not path or not os.path.exists(path):
        return None
    try:
        import struct
        d = open(path, "rb").read()
        if d[:4] != b"DDS " or d[84:88] != b"DXT3":
            return None
        h = struct.unpack("<I", d[12:16])[0]
        w = struct.unpack("<I", d[16:20])[0]
        bw, bh = w // 4, h // 4
        base = 128
        if len(d) < base + bw * bh * 16:
            return None
    except Exception as e:
        logger.warning("bridges: cannot read orthophoto %s: %s", path, e)
        return None

    thr = WATER_ALPHA_THRESHOLD * 255.0

    def block_alpha(bx, by):
        off = base + (by * bw + bx) * 16     # DXT3: 8 alpha bytes then 8 colour bytes
        tot = 0
        for k in range(8):
            b = d[off + k]
            tot += (b & 0x0F) + ((b >> 4) & 0x0F)
        return (tot * 17) // 16              # mean 4-bit alpha -> 0..255

    def is_water(x, y):
        if not (-PATCH_HALF <= x <= PATCH_HALF and -PATCH_HALF <= y <= PATCH_HALF):
            return False                     # outside the patch = treat as land
        u = (x + PATCH_HALF) / PATCH_SIZE
        v = (y + PATCH_HALF) / PATCH_SIZE
        if ORTHO_V_FLIP:
            v = 1.0 - v
        bx = min(max(int(u * bw), 0), bw - 1)
        by = min(max(int(v * bh), 0), bh - 1)
        a = block_alpha(bx, by)
        return (a < thr) if WATER_IS_LOW_ALPHA else (a > thr)

    return is_water


def _span_water(xy, is_water):
    """Trim/extend the bridge to exactly the WATER it crosses while FOLLOWING the
    road's real (possibly curved) path: the mapped points across the water are kept
    (so a bent bridge stays bent) and only the two ENDS are run straight onto the dry
    banks (BANK_MARGIN metres onto each). Reads the water from the orthophoto alpha,
    so both ends land on dry ground; long dry approaches are trimmed. If the path
    crosses no water (a dry valley bridge), the original way is kept unchanged."""
    if len(xy) < 2:
        return xy
    cum = [0.0] * len(xy)
    for i in range(1, len(xy)):
        cum[i] = cum[i - 1] + math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1])
    total = cum[-1]
    if total < 1e-6:
        return xy

    def at(s):
        """Point at arc-length s along the real path. s<0 / s>total extrapolate along
        the first / last segment, so the deck can reach a bank past a mapped end."""
        if s <= 0.0:
            (x0, y0), (x1, y1) = xy[0], xy[1]
            d = math.hypot(x1 - x0, y1 - y0) or 1.0
            return (x0 + (x1 - x0) / d * s, y0 + (y1 - y0) / d * s)
        if s >= total:
            (x0, y0), (x1, y1) = xy[-2], xy[-1]
            d = math.hypot(x1 - x0, y1 - y0) or 1.0
            return (x1 + (x1 - x0) / d * (s - total), y1 + (y1 - y0) / d * (s - total))
        for i in range(len(cum) - 1):
            if s <= cum[i + 1]:
                seg = cum[i + 1] - cum[i]
                t = 0.0 if seg < 1e-9 else (s - cum[i]) / seg
                return (xy[i][0] + (xy[i + 1][0] - xy[i][0]) * t,
                        xy[i][1] + (xy[i + 1][1] - xy[i][1]) * t)
        return xy[-1]

    # first / last water hit ALONG THE REAL PATH
    first = None
    s = 0.0
    while s <= total:
        if is_water(*at(s)):
            first = s
            break
        s += EXTEND_STEP
    if first is None:
        return xy                            # crosses no water -> leave as mapped
    last = first
    s = total
    while s >= 0.0:
        if is_water(*at(s)):
            last = s
            break
        s -= EXTEND_STEP

    # walk off the first/last water onto the dry banks (up to EXTEND_MAX), then a bit
    a = first
    while a > first - EXTEND_MAX and is_water(*at(a)):
        a -= EXTEND_STEP
    b = last
    while b < last + EXTEND_MAX and is_water(*at(b)):
        b += EXTEND_STEP
    a -= BANK_MARGIN
    b += BANK_MARGIN

    # rebuild the deck as the path from a to b, KEEPING the mapped curve between banks
    pts = [at(a)]
    for i in range(len(xy)):
        if a < cum[i] < b:
            pts.append((xy[i][0], xy[i][1]))
    pts.append(at(b))
    return pts


def _line_point_dist(p, a, d):
    """Perpendicular distance from point p to the line through a with unit dir d,
    and the signed distance t of p along d from a."""
    vx, vy = p[0] - a[0], p[1] - a[1]
    t = vx * d[0] + vy * d[1]
    perp = math.hypot(vx - d[0] * t, vy - d[1] * t)
    return perp, t


def _segs_cross(p1, p2, p3, p4):
    """True if the straight segment p1-p2 intersects the segment p3-p4."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
    return (ccw(p1, p3, p4) != ccw(p2, p3, p4) and
            ccw(p1, p2, p3) != ccw(p1, p2, p4))


def _merge_parallel(bridges):
    """Arrange bridges that run parallel and close together so they sit SIDE BY
    SIDE instead of on top of each other:
      * several ways of the SAME type (e.g. a double-track rail bridge mapped as
        one way per track) collapse into ONE deck;
      * different types (a road AND a railway at the same crossing) are shifted
        apart perpendicular so the road deck sits next to the rail deck.
    Lone / non-parallel bridges pass through unchanged."""
    n = len(bridges)
    info = []
    for b in bridges:
        xy = [(x, y) for (x, y, _ip) in b["points"]]
        sx, sy = xy[0]
        ex, ey = xy[-1]
        L = math.hypot(ex - sx, ey - sy)
        if L < 1e-6:
            info.append(None)
        else:
            info.append({"d": ((ex - sx) / L, (ey - sy) / L),
                         "mid": ((sx + ex) / 2.0, (sy + ey) / 2.0), "len": L})

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Cluster only SAME-TYPE parallel close tracks (a multi-track railway). A road
    # and a railway are never merged, so the road bridge keeps its own deck.
    for i in range(n):
        if info[i] is None:
            continue
        for j in range(i + 1, n):
            if info[j] is None or bridges[i]["class"] != bridges[j]["class"]:
                continue
            di, dj = info[i]["d"], info[j]["d"]
            if abs(di[0] * dj[0] + di[1] * dj[1]) < PARALLEL_MERGE_COS:
                continue
            perp, t = _line_point_dist(info[j]["mid"], info[i]["mid"], di)
            # rail: fixed small gap (multi-track railway). road / motorway: merge ONLY
            # when the two decks would actually OVERLAP (gap < their combined half-widths),
            # so overlapping carriageways become one and decks with a real gap stay two.
            if bridges[i]["class"] == "rail":
                lat = PARALLEL_MERGE_LATERAL
            else:
                lat = (bridges[i]["width"] + bridges[j]["width"]) / 2.0
            if perp > lat:
                continue
            if abs(t) > 0.5 * min(info[i]["len"], info[j]["len"]):  # STRONG overlap only
                continue
            parent[find(i)] = find(j)

    clusters = {}
    for i in range(n):
        key = i if info[i] is None else find(i)
        clusters.setdefault(key, []).append(i)

    out = []
    for members in clusters.values():
        if len(members) == 1:
            out.append(bridges[members[0]])
            continue
        spine = max(members, key=lambda k: info[k]["len"] if info[k] else 0.0)
        sd = info[spine]["d"]
        smid = info[spine]["mid"]
        nx, ny = -sd[1], sd[0]                       # unit normal (across)

        def off(k):
            mx, my = info[k]["mid"]
            return (mx - smid[0]) * nx + (my - smid[1]) * ny

        # Group the cluster by type. A multi-track railway (many parallel rail
        # ways) becomes ONE deck WIDE enough to cover ALL its tracks, kept at the
        # tracks' real position. Different types (road + rail) stay as separate
        # decks, each at its own position, so they end up side by side.
        by_class = {}
        for k in members:
            if info[k] is None:
                out.append(bridges[k])
                continue
            by_class.setdefault(bridges[k]["class"], []).append(k)

        for c, ks in by_class.items():
            offs = [off(k) for k in ks]
            lo, hi = min(offs), max(offs)
            center = (lo + hi) / 2.0
            longest = max(ks, key=lambda k: info[k]["len"])
            shift = center - off(longest)      # centre the wide deck on the tracks
            nb = dict(bridges[longest])
            if c == "motorway":
                nb["width"] = 28.0                                # whole dual carriageway = ONE 28 m deck
            else:
                nb["width"] = (hi - lo) + bridges[longest]["width"]   # cover all tracks / carriageways
            nb["points"] = [(x + nx * shift, y + ny * shift, ip)
                            for (x, y, ip) in bridges[longest]["points"]]
            out.append(nb)
    return out


def _align_parallel(specs):
    """Give parallel, side-by-side decks the SAME length: group decks that run
    parallel and close, then extend each to the group's common start/end, so two
    bridges next to each other line up instead of ending at different lengths."""
    n = len(specs)
    info = []
    for sp in specs:
        p0, p1 = sp["xy"][0], sp["xy"][-1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        info.append(None if L < 1e-6 else
                    {"d": (dx / L, dy / L),
                     "mid": ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0), "len": L})
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        if info[i] is None:
            continue
        for j in range(i + 1, n):
            if info[j] is None:
                continue
            di, dj = info[i]["d"], info[j]["d"]
            if abs(di[0] * dj[0] + di[1] * dj[1]) < 0.97:
                continue
            perp, t = _line_point_dist(info[j]["mid"], info[i]["mid"], di)
            if perp > ALIGN_LATERAL:
                continue
            if abs(t) > 0.6 * max(info[i]["len"], info[j]["len"]):
                continue
            parent[find(i)] = find(j)

    groups = {}
    for i in range(n):
        groups.setdefault(i if info[i] is None else find(i), []).append(i)

    for members in groups.values():
        if len(members) < 2:
            continue
        spine = max(members, key=lambda k: info[k]["len"] if info[k] else 0.0)
        ux, uy = info[spine]["d"]
        ox, oy = info[spine]["mid"]
        rng = {}
        for k in members:
            if info[k] is None:
                continue
            p0, p1 = specs[k]["xy"][0], specs[k]["xy"][-1]
            rng[k] = ((p0[0] - ox) * ux + (p0[1] - oy) * uy,
                      (p1[0] - ox) * ux + (p1[1] - oy) * uy)
        if not rng:
            continue
        A = min(min(v) for v in rng.values())
        Bb = max(max(v) for v in rng.values())
        for k, (a0, a1) in rng.items():
            p0, p1 = specs[k]["xy"][0], specs[k]["xy"][-1]
            if a0 > a1:                        # p0 = start (smaller projection)
                p0, p1, a0, a1 = p1, p0, a1, a0
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            Ld = math.hypot(dx, dy) or 1.0
            mux, muy = dx / Ld, dy / Ld
            specs[k]["xy"] = [(p0[0] + mux * (A - a0), p0[1] + muy * (A - a0)),
                              (p1[0] + mux * (Bb - a1), p1[1] + muy * (Bb - a1))]


def _merge_crossing(specs):
    """After _span_water each carriageway of a road is stretched bank-to-bank on its
    OWN slightly different angle, so the two decks of one road end up crossing into an
    'X'. Fuse SAME-TYPE road / motorway / rail decks whose final spans actually CROSS or
    lie ON TOP of each other into ONE deck centred on both. Decks that run side by side
    WITH A GAP (they neither cross nor overlap) are left as two next to each other."""
    n = len(specs)
    info = []
    for sp in specs:
        p0, p1 = sp["xy"][0], sp["xy"][-1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        info.append(None if L < 1e-6 else
                    {"d": (dx / L, dy / L),
                     "mid": ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0), "len": L})

    def deckish(i):
        return specs[i].get("class", "road") in ("road", "motorway", "rail")

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        if info[i] is None or not deckish(i):
            continue
        for j in range(i + 1, n):
            if info[j] is None or not deckish(j):
                continue
            if specs[i]["class"] != specs[j]["class"]:   # never fuse road OVER motorway etc.
                continue
            a0, a1 = specs[i]["xy"][0], specs[i]["xy"][-1]
            b0, b1 = specs[j]["xy"][0], specs[j]["xy"][-1]
            perp, t = _line_point_dist(info[j]["mid"], info[i]["mid"], info[i]["d"])
            overlap = (perp < (specs[i]["width"] + specs[j]["width"]) / 2.0 and
                       abs(t) < 0.5 * (info[i]["len"] + info[j]["len"]))
            if _segs_cross(a0, a1, b0, b1) or overlap:
                parent[find(i)] = find(j)

    clusters = {}
    for i in range(n):
        clusters.setdefault(i if info[i] is None else find(i), []).append(i)

    out = []
    for members in clusters.values():
        if len(members) == 1:
            out.append(specs[members[0]])
            continue
        spine = max(members, key=lambda k: info[k]["len"] if info[k] else 0.0)
        ux, uy = info[spine]["d"]
        ox, oy = info[spine]["mid"]
        nx, ny = -uy, ux                             # unit normal (across the deck)
        ts, ls = [], []
        for k in members:
            for p in (specs[k]["xy"][0], specs[k]["xy"][-1]):
                ts.append((p[0] - ox) * ux + (p[1] - oy) * uy)
                ls.append((p[0] - ox) * nx + (p[1] - oy) * ny)
        A, B = min(ts), max(ts)                       # union length along the spine
        loff, hoff = min(ls), max(ls)                 # lateral spread across it
        coff = (loff + hoff) / 2.0
        widest = max(specs[k]["width"] for k in members)
        nb = dict(specs[spine])
        if nb.get("class") == "motorway":
            nb["width"] = 28.0
        else:
            nb["width"] = (hoff - loff) + widest      # cover both carriageways
        cx, cy = ox + nx * coff, oy + ny * coff
        nb["xy"] = [(cx + ux * A, cy + uy * A), (cx + ux * B, cy + uy * B)]
        out.append(nb)
    return out


def _pt_poly_dist(p, poly):
    """Shortest distance from point p to a polyline."""
    best = 1e18
    for i in range(len(poly) - 1):
        ax, ay = poly[i]
        bx, by = poly[i + 1]
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 < 1e-9:
            d = math.hypot(p[0] - ax, p[1] - ay)
        else:
            t = max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / L2))
            d = math.hypot(p[0] - (ax + vx * t), p[1] - (ay + vy * t))
        best = min(best, d)
    return best


def _overlap_frac(A, B, thr):
    """Fraction (0..1) of polyline A that runs within `thr` metres of polyline B - i.e. how
    much of deck A lies ON TOP of deck B. Samples A every ~5 m."""
    cum = [0.0]
    for i in range(len(A) - 1):
        cum.append(cum[-1] + math.hypot(A[i + 1][0] - A[i][0], A[i + 1][1] - A[i][1]))
    total = cum[-1]
    if total < 1e-6:
        return 0.0
    n = max(2, int(total / 5.0))
    inside = 0
    for k in range(n + 1):
        s = total * k / n
        for i in range(len(cum) - 1):        # point at arc-length s along A
            if s <= cum[i + 1]:
                seg = cum[i + 1] - cum[i]
                t = 0.0 if seg < 1e-9 else (s - cum[i]) / seg
                px = A[i][0] + (A[i + 1][0] - A[i][0]) * t
                py = A[i][1] + (A[i + 1][1] - A[i][1]) * t
                break
        else:
            px, py = A[-1]
        if _pt_poly_dist((px, py), B) <= thr:
            inside += 1
    return inside / (n + 1)


def _drop_crossing_decks(specs):
    """Where two decks that run ROUGHLY PARALLEL sit ON TOP of each other (two motorway
    carriageways / a road stacked on a motorway / ramps that fan out into a 'V' at a
    junction), keep only the LONGER one and drop the other, so the decks don't stack. Two
    conditions must BOTH hold, which is what keeps a real grade separation intact:
      * the decks are ~parallel (|dir_i . dir_j| >= 0.6) - a bridge that CROSSES another at
        an angle is NOT parallel, so both are kept;
      * a good chunk of the shorter deck (>= 25 %) runs within the decks' combined half-width
        of the longer one - decks that sit side by side WITH A GAP overlap ~0 and are kept."""
    def length(sp):
        xy = sp["xy"]
        return sum(math.hypot(xy[i + 1][0] - xy[i][0], xy[i + 1][1] - xy[i][1])
                   for i in range(len(xy) - 1))

    def unit_dir(sp):
        p0, p1 = sp["xy"][0], sp["xy"][-1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        d = math.hypot(dx, dy) or 1.0
        return (dx / d, dy / d)

    lens = [length(sp) for sp in specs]
    dirs = [unit_dir(sp) for sp in specs]
    drop = set()
    for i in range(len(specs)):
        if i in drop:
            continue
        for j in range(i + 1, len(specs)):
            if j in drop:
                continue
            di, dj = dirs[i], dirs[j]
            if abs(di[0] * dj[0] + di[1] * dj[1]) < 0.6:   # not parallel -> a real crossing
                continue
            # keep the longer, test how much of the SHORTER lies on top of the longer
            lo, hi = (j, i) if lens[j] <= lens[i] else (i, j)
            thr = (specs[i]["width"] + specs[j]["width"]) / 2.0
            if _overlap_frac(specs[lo]["xy"], specs[hi]["xy"], thr) >= 0.25:
                drop.add(lo)
                if lo == i:
                    break
    return [sp for k, sp in enumerate(specs) if k not in drop]


def _cap_rail_width(specs):
    """Cap the width of rail decks. One track = 5 m on the texture and the texture
    holds 6 tracks, so a single deck is at most 30 m (width always a multiple of 5).
    A wider track bundle (e.g. a station throat that got merged into one 108 m slab)
    is replaced by at most TWO 30 m decks side by side (max 60 m) - never more.
    Non-rail decks pass through unchanged."""
    out = []
    for sp in specs:
        if sp.get("class") != "rail":
            out.append(sp)
            continue
        wsnap = max(5.0, round(sp["width"] / 5.0) * 5.0)   # snap to whole tracks
        if wsnap <= 30.0:
            nb = dict(sp)
            nb["width"] = wsnap
            out.append(nb)
            continue
        # too wide -> two decks side by side, each <= 30 m and a multiple of 5
        wd = min(30.0, max(5.0, round((wsnap / 2.0) / 5.0) * 5.0))
        xy = sp["xy"]
        p0, p1 = xy[0], xy[-1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L                            # unit normal across the deck
        for s in (-0.5, 0.5):                               # two decks, centred +/- wd/2
            off = s * wd
            nb = dict(sp)
            nb["width"] = wd
            nb["xy"] = [(x + nx * off, y + ny * off) for (x, y) in xy]
            out.append(nb)
    return out


def _build_bridges(bridges, waterways, z_at, is_water=None, railings=True,
                   g_moto=None, g_other=None):
    """Build merged (verts, faces). verts = [(x,y,z)], faces = [(i,j,k,l)/(i,j,k)]
    with 0-based indices. Returns (verts, faces, n_bridges, n_pillars).
    railings=False drops the zabradli (used for the lighter LOD1).
    g_moto / g_other = ground motorway / (road+rail) lines for grade-separated
    crossing detection (a nadjezd over a dalnice)."""
    verts, faces, uvs = [], [], []
    wbounds = [_bbox(w) for w in waterways]
    g_moto = g_moto or []
    g_other = g_other or []
    gm_bounds = [_bbox(w) for w in g_moto]
    go_bounds = [_bbox(w) for w in g_other]
    bridges = _merge_parallel(bridges)

    def av(p, uv):
        verts.append(p)
        uvs.append(uv)
        return len(verts) - 1

    n_bridges = n_pillars = n_skipped = 0
    specs = []
    for b in bridges:
        pts = b["points"]
        if not any(ip for (_x, _y, ip) in pts):
            continue
        xy = [(x, y) for (x, y, _ip) in pts]
        if len(xy) < 2:
            continue
        cum, z_deck = _deck_line(xy, z_at)
        if cum[-1] < MIN_BRIDGE_LENGTH:   # skip tiny culverts over creeks
            n_skipped += 1
            continue
        # A valley (terrain drops wide & deep - a river is a wide low area too) OR
        # a crossing of a mapped river. A narrow rail/road cutting does NOT qualify.
        over_water = is_water is not None and _alpha_crosses(xy, is_water)
        if b.get("service"):
            # a siding/yard track counts ONLY if it truly crosses the river (water
            # in the orthophoto alpha): keeps a station's river bridge, drops the
            # dry plant vlecky
            qualifies = over_water
            water_q, cross_q = over_water, False
        else:
            water_q = over_water or _crosses_waterway(xy, waterways, wbounds)
            cross_q = _crosses_road(xy, b["class"], g_moto, gm_bounds,
                                    g_other, go_bounds)
            qualifies = _valley(xy, cum, z_deck, z_at) or water_q or cross_q
        if not qualifies:
            n_skipped += 1
            continue

        # reach the bank across the water using the orthophoto alpha (reliable)
        if is_water is not None:
            xy = _span_water(xy, is_water)
        # remember HOW it qualified -> drives the pillars later (crossing = none, water = 2)
        specs.append({"xy": xy, "width": b["width"], "class": b["class"],
                      "water": water_q, "crossing": cross_q})

    # fuse the two carriageways of one road (their bank-to-bank spans cross into an 'X')
    # into a single deck; genuinely separate decks with a gap stay side by side
    specs = _merge_crossing(specs)

    # length-align parallel decks (two bridges side by side get the same span)
    _align_parallel(specs)

    # rail decks: max 6 tracks (30 m) per deck; a wider bundle -> at most two 30 m decks
    specs = _cap_rail_width(specs)

    # two same-type decks that CROSS (diverging ramps 'V') -> keep only the longer one
    specs = _drop_crossing_decks(specs)

    # Pass 2: build the deck geometry for each (aligned) span.
    for spec in specs:
        xy = spec["xy"]
        cum, z_deck = _deck_line(xy, z_at)
        total = cum[-1]
        # split the centreline into equal pieces of at most SEG_LEN (one texture tile
        # each). Resample FIRST: after _span_water the raw deck is only its two END
        # nodes, so the arch below needs these mid-span points to actually lift.
        nseg = max(1, int(math.ceil(total / SEG_LEN)))
        rp = [_sample(xy, cum, z_deck, total * k / nseg) for k in range(nseg + 1)]
        # Bow the deck into a smooth arch: ends stay on the banks, the mid-span rises,
        # tapering as sin() to zero at both ends, so a bridge over flat water/ground does
        # not lie on it. OVER WATER the heightmap is the riverBED but the deck sits at the
        # water SURFACE (~the chord), so there we lift a full ARCH_CLEARANCE above the
        # chord; over a DRY valley we lift only enough to clear the ground.
        bowed = False                        # True = deck lifted into an arch in the middle
        if total > 1e-6 and nseg >= 2:
            mx, my, chord_mid = rp[nseg // 2]
            if is_water is not None and is_water(mx, my):
                amp = ARCH_CLEARANCE
            else:
                amp = (z_at(mx, my) + ARCH_CLEARANCE) - chord_mid
            if amp > 0.0:
                rp = [(x, y, z + amp * math.sin(math.pi * k / nseg))
                      for k, (x, y, z) in enumerate(rp)]
                bowed = True
        rxy = [(p[0], p[1]) for p in rp]
        # arched arc-length profile so the pillars sit under the RAISED deck
        rcum = [total * k / nseg for k in range(nseg + 1)]
        rz = [p[2] for p in rp]

        # pick the texture strip (V band) + how much of it to map ACROSS the width,
        # and snap the deck width to a whole number of tracks / carriageways
        cls = spec.get("class", "road")
        w = spec["width"]
        if cls == "motorway":
            vb0, vb1 = V_MOTORWAY_BAND
            uL, uR = 0.0, 1.0                        # one motorway cross-section, 15 m
        elif cls == "rail":
            vb0, vb1 = V_RAIL_BAND
            n_tr = max(1, int(round(w / 5.0)))       # 5 m per track: 5m=1, 10m=2, 15m=3...
            w = n_tr * 5.0                            # snap width to whole tracks
            uL, uR = 0.0, ONE_TRACK_U * n_tr         # map U 0..N*0.134 -> exactly N tracks
        else:
            vb0, vb1 = V_ROAD_BAND
            n_cw = max(1, int(round(w / 11.0)))      # 11 m per carriageway: 25.6m -> 22m (2x)
            w = n_cw * 11.0                           # snap width to whole carriageways
            uL, uR = 0.0, float(n_cw)                 # REPEAT tiles N carriageways side by side
        fu0, fu1 = U_FENCE                            # railing (zabradli) sits in the bottom strip
        fv0, fv1 = V_RAIL_BAND

        half = w / 2.0
        # consistent cross direction (perp) per resampled point (no bow-tie twist)
        cols = []
        prev_perp = None
        for i, (x, y, zt) in enumerate(rp):
            px, py = _perp(rxy, i)
            if prev_perp is not None and (px * prev_perp[0] + py * prev_perp[1]) < 0.0:
                px, py = -px, -py
            prev_perp = (px, py)
            cols.append((x, y, zt, px, py))

        def mkrow(idx, vt, fvt):
            # one cross-section row at resample point idx; vt = V inside the strip. Each
            # 10 m segment gets its OWN bottom(vb0)/top(vb1) row, so it maps the strip
            # bottom->top ONCE per segment: exactly ONE texture tile per segment, and the
            # markings are NOT flipped between segments.
            #   L/R  = deck TOP corners (road/rail/motorway strip)
            #   Lo/Ro, Lb/Rb = skirt corners (sides + soffit + caps) -> footpath grey
            #   Lrb/Rrb, Lr/Rr = railing base/top -> zabradli strip (handrail at the top)
            x, y, zt, px, py = cols[idx]
            lx, ly = x + px * half, y + py * half
            rx, ry = x - px * half, y - py * half
            row = {
                "L": av((lx, ly, zt), (uL, vt)),
                "R": av((rx, ry, zt), (uR, vt)),
                "Lo": av((lx, ly, zt), SIDEWALK_UV),
                "Ro": av((rx, ry, zt), SIDEWALK_UV),
                "Lb": av((lx, ly, zt - DECK_THICKNESS), SIDEWALK_UV),
                "Rb": av((rx, ry, zt - DECK_THICKNESS), SIDEWALK_UV),
            }
            if railings:
                row.update({
                    "Lrb": av((lx, ly, zt), (fu0, fvt)),
                    "Rrb": av((rx, ry, zt), (fu0, fvt)),
                    "Lr": av((lx, ly, zt + RAIL_HEIGHT), (fu1, fvt)),
                    "Rr": av((rx, ry, zt + RAIL_HEIGHT), (fu1, fvt)),
                    # railing BACK side gets its OWN vertices (same spot) so the flipped
                    # face is not merged away by Blender -> zabradli visible from both sides
                    "Lrb2": av((lx, ly, zt), (fu0, fvt)),
                    "Rrb2": av((rx, ry, zt), (fu0, fvt)),
                    "Lr2": av((lx, ly, zt + RAIL_HEIGHT), (fu1, fvt)),
                    "Rr2": av((rx, ry, zt + RAIL_HEIGHT), (fu1, fvt)),
                })
            return row

        first_a = last_b = None
        for seg in range(nseg):
            a = mkrow(seg, vb0, fv0)            # bottom edge of this 10 m texture tile
            b = mkrow(seg + 1, vb1, fv1)        # top edge of this 10 m texture tile
            if seg == 0:
                first_a = a
            last_b = b
            faces.append((a["L"], a["R"], b["R"], b["L"]))          # deck top (its strip)
            faces.append((a["Lb"], b["Lb"], b["Rb"], a["Rb"]))      # soffit (footpath grey)
            faces.append((a["Lo"], b["Lo"], b["Lb"], a["Lb"]))      # left side (footpath grey)
            faces.append((a["Ro"], a["Rb"], b["Rb"], b["Ro"]))      # right side (footpath grey)
            if railings:
                faces.append((a["Lrb"], a["Lr"], b["Lr"], b["Lrb"]))   # left railing (zabradli)
                faces.append((a["Rrb"], b["Rrb"], b["Rr"], a["Rr"]))   # right railing (zabradli)
                # railings are thin -> a duplicate with its OWN verts + reversed winding
                # (flipped normal), so the zabradli is visible from BOTH sides
                faces.append((b["Lrb2"], b["Lr2"], a["Lr2"], a["Lrb2"]))   # left railing (back)
                faces.append((a["Rr2"], b["Rr2"], b["Rrb2"], a["Rrb2"]))   # right railing (back)
        faces.append((first_a["Lo"], first_a["Lb"], first_a["Rb"], first_a["Ro"]))  # start cap
        faces.append((last_b["Lo"], last_b["Ro"], last_b["Rb"], last_b["Lb"]))      # end cap

        # Pillars depend on HOW the deck sits (only decks BOWED up in the middle get the
        # special treatment; decks that follow the terrain keep the normal spacing):
        #   * bowed up over a road/dalnice/rail crossing (nadjezd) -> NO pillars (a pier
        #     would land on the road/track below);
        #   * bowed up over a river -> exactly TWO pillars, at 1/3 and 2/3 (none in the middle);
        #   * otherwise -> ~1 pillar per PILLAR_TARGET_SPACING, spaced evenly.
        if total < PILLAR_MIN_BRIDGE:
            n_pil = 0
        elif bowed and spec.get("crossing"):
            n_pil = 0
        elif bowed and spec.get("water"):
            n_pil = 2
        else:
            n_pil = max(1, round(total / PILLAR_TARGET_SPACING))
        for pi in range(n_pil):
            s = total * (pi + 1) / (n_pil + 1)
            cx, cy, zt = _sample(rxy, rcum, rz, s)   # arched deck profile
            top = zt - DECK_THICKNESS
            bot = z_at(cx, cy) - PILLAR_EMBED   # go 2 m under the terrain (anchored)
            if (top - bot) >= PILLAR_MIN_DROP:
                # LOCAL bridge direction HERE (the bridge can curve) - take the
                # tangent from a bit behind to a bit ahead, so the pier lines up
                # with the deck at this point, not with the overall straight line
                fx, fy, _f = _sample(rxy, rcum, rz, min(s + 2.0, total))
                gx, gy, _g = _sample(rxy, rcum, rz, max(s - 2.0, 0.0))
                ldx, ldy = fx - gx, fy - gy
                ll = math.hypot(ldx, ldy) or 1.0
                aax, aay = ldx / ll, ldy / ll      # along the bridge (local)
                apx, apy = -aay, aax               # across the bridge (local)
                hl = PILLAR_HALF                   # half-length ALONG the bridge
                hw = max(PILLAR_HALF, half * 0.8)  # a pier spanning most of the width
                c = [(cx - hl * aax - hw * apx, cy - hl * aay - hw * apy),
                     (cx + hl * aax - hw * apx, cy + hl * aay - hw * apy),
                     (cx + hl * aax + hw * apx, cy + hl * aay + hw * apy),
                     (cx - hl * aax + hw * apx, cy - hl * aay + hw * apy)]
                # pillars sample the same plain grey footpath patch - concrete-ish
                bidx = [av((p[0], p[1], bot), SIDEWALK_UV) for p in c]
                tidx = [av((p[0], p[1], top), SIDEWALK_UV) for p in c]
                for k in range(4):
                    kn = (k + 1) % 4
                    faces.append((bidx[k], bidx[kn], tidx[kn], tidx[k]))
                n_pillars += 1

        n_bridges += 1

    return verts, faces, uvs, n_bridges, n_pillars, n_skipped


# ---------------------------------------------------------------------------
# Blender material / object
# ---------------------------------------------------------------------------
def _get_material():
    mat = bpy.data.materials.get(MAT_NAME)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    base = os.path.join(os.path.dirname(__file__), "..", "assets", "3Dobjects")
    # the current texture is Bridge.dds (512x1536, 3 strips); Bridge.png is a fallback
    img_path = next((os.path.join(base, n) for n in ("Bridge.png", "Bridge.dds")
                     if os.path.exists(os.path.join(base, n))), None)
    if img_path and bsdf is not None:
        try:
            img = bpy.data.images.load(img_path, check_existing=True)
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            tex.extension = 'REPEAT'
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        except Exception:
            pass
    elif bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.35, 0.35, 0.37, 1.0)
    return mat


def _cross_patch_samplers(patch_id, heightmaps_dir, texture_dirs, cur_z, cur_water):
    """Wrap the current patch's terrain-Z and water samplers so that points which fall
    OUTSIDE this patch (a bridge crossing the tile border) are looked up in the NEIGHBOUR
    patch's terrain + orthophoto instead of the clamped patch edge. Neighbour data is
    loaded lazily and cached. Coordinates are patch-local; the neighbour's local frame is
    just this one shifted by +/-PATCH_SIZE (adjacent patch origins are exactly one tile
    apart). Returns (z_at, is_water) with the same call signature as before."""
    from ..io.terrain_loader import load_terrain

    px0, py0 = int(patch_id[:3]), int(patch_id[3:])
    zcache = {patch_id: cur_z}
    wcache = {patch_id: cur_water}

    def resolve(x, y):
        px, py, nx, ny = px0, py0, x, y
        if x > PATCH_HALF:
            px -= 1; nx = x - PATCH_SIZE
        elif x < -PATCH_HALF:
            px += 1; nx = x + PATCH_SIZE
        if y > PATCH_HALF:
            py += 1; ny = y - PATCH_SIZE
        elif y < -PATCH_HALF:
            py -= 1; ny = y + PATCH_SIZE
        return f"{px:03d}{py:03d}", nx, ny

    def get_z(pid):
        if pid not in zcache:
            from .terrain_smooth import (load_terrain_smoothed,
                                         resolve_smooth_or_source)
            z = None
            cand = resolve_smooth_or_source(heightmaps_dir, pid)
            if os.path.exists(cand):
                try:
                    z = _make_terrain_z(load_terrain_smoothed(heightmaps_dir, pid))
                except Exception:
                    z = None
            zcache[pid] = z
        return zcache[pid]

    def get_w(pid):
        if pid not in wcache:
            w = None
            for d in texture_dirs:
                w = _load_water_sampler(os.path.join(d, f"t{pid}.dds"))
                if w is not None:
                    break
            wcache[pid] = w
        return wcache[pid]

    def z_at(x, y):
        if -PATCH_HALF <= x <= PATCH_HALF and -PATCH_HALF <= y <= PATCH_HALF:
            return cur_z(x, y)
        pid, nx, ny = resolve(x, y)
        z = get_z(pid)
        return z(nx, ny) if z is not None else cur_z(x, y)

    def is_water(x, y):
        if -PATCH_HALF <= x <= PATCH_HALF and -PATCH_HALF <= y <= PATCH_HALF:
            return cur_water(x, y)
        pid, nx, ny = resolve(x, y)
        w = get_w(pid)
        return w(nx, ny) if w is not None else cur_water(x, y)

    return z_at, (is_water if cur_water is not None else None)


# ---------------------------------------------------------------------------
# Operator: Import Bridges
# ---------------------------------------------------------------------------
class CONDOR_OT_import_bridges(Operator):
    bl_idname = "condor.import_bridges"
    bl_label = "Import Bridges"
    bl_description = ("Generate road/rail bridges over valleys and rivers as one "
                      "'bridges' object (deck + railings + pillars)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        # in Batch (file mode) the bridges are written into the OBJ by the exporter,
        # so the manual Blender import is disabled (like the transmitter Import)
        return (bool(props.condor_path) and props.landscape_name != 'NONE'
                and not getattr(context.scene, "condor_bridge_batch", False))

    def execute(self, context):
        import xml.etree.ElementTree as ET
        from .operators import resolve_condor_paths, ensure_patch_osm, _extra_obj_type
        from ..projection.transverse_mercator import TransverseMercatorProjector
        from ..io.patch_metadata import load_patch_metadata
        from ..io.terrain_loader import load_terrain

        props = context.scene.condor_buildings
        paths = resolve_condor_paths(props)
        if not paths:
            self.report({'ERROR'}, "Invalid Condor paths.")
            return {'CANCELLED'}

        if props.single_patch_mode and props.patch_id:
            patch_ids = [str(props.patch_id)]
        else:
            patch_ids = [f"{x:03d}{y:03d}"
                         for x in range(props.patch_x_min, props.patch_x_max + 1)
                         for y in range(props.patch_y_min, props.patch_y_max + 1)]

        total_bridges = total_pillars = total_skipped = 0
        total_found = total_waterways = total_existing = 0
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
            from .terrain_smooth import (load_terrain_smoothed,
                                         resolve_smooth_or_source)
            terrain_file = resolve_smooth_or_source(paths['heightmaps'], patch_id)
            if not os.path.exists(terrain_file):
                continue
            try:
                meta = load_patch_metadata(txt_path)
                projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
                terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
                root = ET.parse(osm_path).getroot()
            except Exception as e:
                logger.warning("bridges: setup failed for %s: %s", patch_id, e)
                continue

            bridges, waterways, g_moto, g_other = _parse(root, projector)
            total_found += len(bridges)
            total_waterways += len(waterways)
            if not bridges:
                continue

            is_water = None
            for cand in (
                os.path.join(paths['landscape'], "Textures", f"t{patch_id}.dds"),
                os.path.join(paths['heightmaps'], f"t{patch_id}.dds"),
                os.path.join(paths['landscape'], "Working", "Textures", f"t{patch_id}.dds"),
                os.path.join(paths['landscape'], "Working", "HeightMaps", f"t{patch_id}.dds"),
            ):
                is_water = _load_water_sampler(cand)
                if is_water is not None:
                    print(f"[bridges] orthophoto LOADED (water spanning ON): {cand}")
                    break
            if is_water is None:
                print(f"[bridges] orthophoto NOT LOADED for patch {patch_id} "
                      f"(no water spanning) - tried landscape/Textures, heightmaps, Working")
            # terrain-Z + water that also reach into NEIGHBOUR patches for bridges that
            # cross the tile border (so the far half is seated on the right ground/water)
            texture_dirs = [
                os.path.join(paths['landscape'], "Textures"),
                paths['heightmaps'],
                os.path.join(paths['landscape'], "Working", "Textures"),
                os.path.join(paths['landscape'], "Working", "HeightMaps"),
            ]
            z_at, is_water = _cross_patch_samplers(
                patch_id, paths['heightmaps'], texture_dirs,
                _make_terrain_z(terrain), is_water)
            # Which LODs to build into (same pattern as chimneys):
            #   LOD0 -> "" collection, WITH railings; LOD1 -> "_LOD1", WITHOUT railings.
            lod_variants = []
            if props.output_lod in ('LOD0', 'BOTH'):
                lod_variants.append(("", True))
            if props.output_lod in ('LOD1', 'BOTH'):
                lod_variants.append(("_LOD1", False))
            if not lod_variants:
                lod_variants.append(("", True))

            for vi, (suffix, railings) in enumerate(lod_variants):
                # already present for THIS patch + LOD (separately imported OR baked in the
                # patch OBJ)? -> skip so bridges aren't duplicated
                _pcol = bpy.data.collections.get(
                    f"Condor_{props.landscape_name}_{patch_id}{suffix}")
                if _pcol is not None and any(_extra_obj_type(o.name) == "bridges"
                                             for o in _pcol.all_objects):
                    total_existing += 1
                    continue
                verts, faces, uvs, nb, npil, nsk = _build_bridges(
                    bridges, waterways, z_at, is_water, railings, g_moto, g_other)
                if vi == 0:
                    total_skipped += nsk
                    total_bridges += nb
                    total_pillars += npil
                if not verts or nb == 0:
                    continue

                obj_name = "bridges"   # stejny nazev v obou LOD (LOD urcuje kolekce); Blender da LOD1 pripadne .001
                mesh = bpy.data.meshes.new(obj_name)
                mesh.from_pydata([tuple(v) for v in verts], [], [list(f) for f in faces])
                mesh.update()
                # UV layer so the deck can be textured (U across 0..1, V tiled per segment)
                if uvs:
                    uvl = mesh.uv_layers.new(name="UVMap")
                    for loop in mesh.loops:
                        uvl.data[loop.index].uv = uvs[loop.vertex_index]
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
                    # brand-new collection -> link it under the scene once
                    col = bpy.data.collections.new(col_name)
                    try:
                        context.scene.collection.children.link(col)
                    except Exception:
                        pass
                col.objects.link(ob)

        # copy Bridge.dds into Working/Autogen/Textures so Condor (and a re-import of
        # the OBJ) finds it - same as the Batch/file-mode path already does
        if total_bridges > 0:
            _copy_bridge_texture(paths)

        msg = (f"Bridges: built {total_bridges} (pillars {total_pillars}), "
               f"skipped (not over valley/river) {total_skipped}, "
               f"bridges in OSM {total_found}, rivers {total_waterways}")
        if total_existing:
            msg += f", already imported {total_existing} (skipped)"
        if missing_osm:
            msg += f" | OSM missing (skipped): {', '.join(missing_osm)}"
        self.report({'INFO'} if (total_bridges > 0 or total_existing) and not missing_osm
                    else {'WARNING'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# File mode (Batch): write bridges straight into each patch OBJ (LOD0 + LOD1),
# mirroring how transmitters do it. Nothing outside this file is edited.
# ---------------------------------------------------------------------------
# One-slot cache so a patch's expensive setup (OSM parse + terrain + water + neighbours)
# is done ONCE and reused for BOTH LOD0 and LOD1 (which come as two separate exporter
# calls). Keyed by patch + the OSM file's mtime, so an edited OSM is re-read.
_SETUP_CACHE = {"patch": None, "key": None, "setup": None}


def _patch_setup(paths, patch_id):
    """Return (bridges, waterways, g_moto, g_other, z_at, is_water) for a patch, or None.
    Cached per patch (one slot) so LOD0 and LOD1 share the same single parse/terrain/water
    load."""
    import xml.etree.ElementTree as ET
    from ..projection.transverse_mercator import TransverseMercatorProjector
    from ..io.patch_metadata import load_patch_metadata
    from ..io.terrain_loader import load_terrain

    osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
    if not os.path.exists(osm_path):
        return None
    try:
        key = os.path.getmtime(osm_path)
    except OSError:
        key = None
    c = _SETUP_CACHE
    if c["patch"] == patch_id and c["key"] == key and c["setup"] is not None:
        return c["setup"]

    txt_path = next((p for p in (
        os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
        os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
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
        logger.warning("bridges: file-mode setup failed for %s: %s", patch_id, e)
        return None

    bridges, waterways, g_moto, g_other = _parse(root, projector)
    if not bridges:
        return None
    is_water = None
    for cand in (
        os.path.join(paths['landscape'], "Textures", f"t{patch_id}.dds"),
        os.path.join(paths['heightmaps'], f"t{patch_id}.dds"),
        os.path.join(paths['landscape'], "Working", "Textures", f"t{patch_id}.dds"),
        os.path.join(paths['landscape'], "Working", "HeightMaps", f"t{patch_id}.dds"),
    ):
        is_water = _load_water_sampler(cand)
        if is_water is not None:
            break
    texture_dirs = [
        os.path.join(paths['landscape'], "Textures"),
        paths['heightmaps'],
        os.path.join(paths['landscape'], "Working", "Textures"),
        os.path.join(paths['landscape'], "Working", "HeightMaps"),
    ]
    z_at, is_water = _cross_patch_samplers(
        patch_id, paths['heightmaps'], texture_dirs, _make_terrain_z(terrain), is_water)
    setup = (bridges, waterways, g_moto, g_other, z_at, is_water)
    c["patch"], c["key"], c["setup"] = patch_id, key, setup
    return setup


def _generate_patch_geometry(paths, patch_id, railings=True):
    """Build (verts, faces, uvs) of the bridges for ONE patch/LOD in file mode, reusing
    the cached per-patch setup, or None if there is nothing."""
    setup = _patch_setup(paths, patch_id)
    if setup is None:
        return None
    bridges, waterways, g_moto, g_other, z_at, is_water = setup
    verts, faces, uvs, nb, npil, nsk = _build_bridges(
        bridges, waterways, z_at, is_water, railings, g_moto, g_other)
    if not verts or nb == 0:
        return None
    return verts, faces, uvs


def _append_bridge_material(obj_path):
    """Append the 'condor_bridge' material (Bridge.dds) to the patch .mtl (same block
    shape the exporter writes)."""
    from ..config import (CONDOR_MTL_KA, CONDOR_MTL_KD, CONDOR_MTL_KS, CONDOR_MTL_NS,
                          CONDOR_MTL_D, CONDOR_MTL_ILLUM, CONDOR_TEXTURE_PREFIX)
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
        mf.write(f"map_Kd {CONDOR_TEXTURE_PREFIX}{TEX_FILE}\n")


def _copy_bridge_texture(paths):
    """Copy Bridge.dds next to the patch OBJs (autogen/Textures) so Condor finds it."""
    try:
        import shutil
        tdir = os.path.join(paths['autogen'], "Textures")
        os.makedirs(tdir, exist_ok=True)
        dst = os.path.join(tdir, TEX_FILE)
        src = os.path.join(os.path.dirname(__file__), "..", "assets", "3Dobjects", TEX_FILE)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copyfile(src, dst)
    except Exception as e:
        print(f"[bridges] texture copy failed: {e}")


def _write_bridge_into_obj(obj_path, paths, patch_id, railings):
    """Append the 'bridges' object to a file-mode patch OBJ (and its material to the .mtl),
    with freshly computed per-triangle normals. Buildings/exporter stay untouched."""
    from ..config import CONDOR_AXIS_SWAP
    from ..io.obj_exporter import _condor_xform

    geo = _generate_patch_geometry(paths, patch_id, railings)
    if geo is None:
        return
    verts, faces, uvs = geo
    wv = [_condor_xform(v, CONDOR_AXIS_SWAP) for v in verts]
    v_lines = ["v %.6f %.6f %.6f" % (w[0], w[1], w[2]) for w in wv]
    vt_lines = ["vt %.6f %.6f" % (u, vv) for (u, vv) in uvs]
    vn_lines, tris = [], []
    for face in faces:
        fi = list(face)
        for k in range(1, len(fi) - 1):
            a, b, c = fi[0], fi[k], fi[k + 1]
            pa, pb, pc = wv[a], wv[b], wv[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            L = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            ni = len(vn_lines)
            vn_lines.append("vn %.6f %.6f %.6f" % (nx / L, ny / L, nz / L))
            tris.append((a, b, c, ni))
    if not v_lines:
        return

    lines = open(obj_path, "r", encoding="utf-8").read().split("\n")
    base_v = sum(1 for l in lines if l.startswith("v "))
    base_vt = sum(1 for l in lines if l.startswith("vt "))
    base_vn = sum(1 for l in lines if l.startswith("vn "))
    f_lines = ["f " + " ".join(
        f"{vi + base_v + 1}/{vi + base_vt + 1}/{ni + base_vn + 1}" for vi in (a, b, c))
        for (a, b, c, ni) in tris]

    block = ["", f"o {OBJECT_NAME}", f"usemtl {MAT_NAME}"] + v_lines + vt_lines + vn_lines + f_lines
    open(obj_path, "w", encoding="utf-8").write("\n".join(lines + block))
    _append_bridge_material(obj_path)
    _copy_bridge_texture(paths)


def _append_bridge_after_export(obj_filepath):
    """Called from the wrapped exporter: on Batch, write the bridges into a file-mode
    patch OBJ. o<patch>.obj -> full (LOD0); o<patch>_LOD1.obj -> no railings (LOD1)."""
    import re
    if not getattr(bpy.context.scene, "condor_bridge_batch", False):
        return
    m = re.match(r'^o(\d{6})(_LOD1)?\.obj$', os.path.basename(obj_filepath))
    if not m:
        return
    patch_id = m.group(1)
    is_lod1 = m.group(2) is not None
    props = bpy.context.scene.condor_buildings
    from .operators import resolve_condor_paths
    paths = resolve_condor_paths(props)
    if not paths:
        return
    _write_bridge_into_obj(obj_filepath, paths, patch_id, railings=(not is_lod1))


def _patch_obj_exporter():
    """Wrap obj_exporter.export_condor_obj_mtl so AFTER it writes a patch OBJ the bridges
    are appended (file mode). The exporter source is NOT edited; removing this module
    restores the original behaviour."""
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_bridge_export_patched", False):
        return
    _orig = _oe.export_condor_obj_mtl

    def _patched(groups, obj_filepath, texture_map, *a, **k):
        stats = _orig(groups, obj_filepath, texture_map, *a, **k)
        try:
            _append_bridge_after_export(obj_filepath)
        except Exception as e:
            print(f"[bridges] file-mode append failed: {e}")
        return stats

    _oe._bridge_orig_export = _orig
    _oe.export_condor_obj_mtl = _patched
    _oe._bridge_export_patched = True


def _unpatch_obj_exporter():
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_bridge_export_patched", False):
        _oe.export_condor_obj_mtl = _oe._bridge_orig_export
        _oe._bridge_export_patched = False


# ---------------------------------------------------------------------------
# Panel row (called from panels.py inside the "Other objects" box)
# ---------------------------------------------------------------------------
def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Bridges", icon='MOD_LATTICE')
    row.prop(context.scene, "condor_bridge_batch", text="Batch")
    row = box.row(align=True)
    row.operator("condor.import_bridges", text="Import", icon='IMPORT')


# ---------------------------------------------------------------------------
# Registration (operator + runtime OSM-query wrap for rivers)
# ---------------------------------------------------------------------------
_classes = [CONDOR_OT_import_bridges]


def _patch_overpass_query():
    """Wrap osm_downloader.build_overpass_query so the OSM download ALSO fetches:
      * water courses (rivers) - a bridge over a river,
      * GROUND (non-bridge) motorways / roads / rails - the "obstacle below" needed to
        detect a grade-separated crossing (a nadjezd over a dalnice). service / farm
        tracks are deliberately left out (yards would bloat the download for nothing).
    The bridge ways themselves are already fetched by the base query. Kept here so the
    whole feature lives in one file; removing this module restores the original query."""
    from . import osm_downloader as _osm
    if getattr(_osm, "_bridges_patched", False):
        return
    _orig = _osm.build_overpass_query

    def _patched(lat_min, lat_max, lon_min, lon_max, *a, **k):
        q = _orig(lat_min, lat_max, lon_min, lon_max, *a, **k)
        bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
        extra = (
            f'  way["waterway"="river"]({bbox});\n'
            f'  way["highway"~"^(motorway|trunk|motorway_link|trunk_link|primary|'
            f'primary_link|secondary|secondary_link|tertiary|tertiary_link|'
            f'unclassified|residential|road)$"]({bbox});\n'
            f'  way["railway"="rail"]({bbox});'
        )
        return q.replace("\n);", "\n" + extra + "\n);", 1)

    _osm._bridges_orig_query = _orig
    _osm.build_overpass_query = _patched
    _osm._bridges_patched = True


def _unpatch_overpass_query():
    from . import osm_downloader as _osm
    if getattr(_osm, "_bridges_patched", False):
        _osm.build_overpass_query = _osm._bridges_orig_query
        _osm._bridges_patched = False


def register():
    from bpy.props import BoolProperty
    bpy.types.Scene.condor_bridge_batch = BoolProperty(
        name="Batch",
        description=("File mode (Import to Blender off): after each patch OBJ is written, "
                     "also generate the bridges into it - LOD0 with railings, LOD1 without. "
                     "Off by default"),
        default=False,
    )
    for c in _classes:
        bpy.utils.register_class(c)
    try:
        _patch_overpass_query()
    except Exception:
        pass
    # add the bridge texture/material to the export maps so file-mode MTL gets it
    try:
        from .. import config
        config.TEXTURE_MAP.setdefault(MAT_NAME, TEX_FILE)
        config.MATERIAL_ALIAS.setdefault(OBJECT_NAME, MAT_NAME)
    except Exception:
        pass
    # append the bridges into each patch OBJ after export (file mode / Batch)
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
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
    try:
        del bpy.types.Scene.condor_bridge_batch
    except Exception:
        pass
