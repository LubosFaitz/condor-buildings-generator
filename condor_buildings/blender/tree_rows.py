"""
Tree rows - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Bridges / Transmitter feature: everything lives in THIS one file plus two
tiny, clearly-marked, try/except-guarded hooks in:
  - __init__.py       (register/unregister this module)
  - blender/panels.py (draw the Tree row block inside "Other objects")

Delete this file (or comment out those two hooks) and the plugin falls back to exactly
the previous behaviour - nothing else depends on it. It does NOT touch buildings,
pylons, bridges, the exporter, config.py or osm_downloader.py.

The main Overpass query stays UNTOUCHED. The lines of trees are fetched only when they
are really being built: each patch is checked (a cheap text scan of its map_<patch>.osm)
and only a patch that has none yet gets ONE tiny extra query of its own, whose result is
merged into that file. A patch that genuinely has none is marked in the file, so it is
not asked about again.

What it does (the whole core):
  Several kinds of OSM line are used, and ALL of them are built exactly the same way:
    * natural=tree_row   - an alley along a road, a windbreak along a field,
    * barrier=hedge      - a hedgerow (in England around nearly every field),
    * barrier=hedge_bank - a bank with a hedge on top,
    * natural=hedge      - the older / non-standard spelling of the same hedgerow,
    * fence_type=hedge   - likewise non-standard, likewise present in the data,
    * a tree-lined ROAD or WATERWAY - trees that belong to the road/river itself and are
      pushed sideways off the centreline so none of them stands on the carriageway.
      Every spelling of it is read:
        tree_lined=yes/both/left/right          the old flat key,
        tree_lined:left|right|both=yes          the namespaced sub-keys the wiki asks for
                                                (=no is skipped, =separate is left to the
                                                 tree_row way that maps those trees),
        denotation=avenue                       an avenue - both sides,
        alley=left/right/both                   deprecated, still in old data.
    * natural=tree / natural=tree_group on a NODE - a single tree / a small clump.
  Trees are placed ALONG the line, one every Spacing metres (the slider in the panel),
  each one standing with its foot on the terrain. The sources are told apart only in the
  report, so you can see how many trees came from which.

  One tree = TWO vertical quads crossed into an X (8 vertices, 4 triangles), the
  texture stretched over the whole quad (UV 0..1) on both of them, and the whole tree
  turned by a random angle around its vertical axis. The randomness is SEEDED from the
  OSM way id + the tree's index along the row, so the same patch always comes out
  exactly the same.

  Everything of one patch ends up in ONE object 'tree_rows' with the material
  'condor_tree' (texture assets/3Dobjects/tree.dds). The texture may be missing - then
  a plain green material is used instead and nothing breaks.
"""

import os
import math
import random
import logging

import bpy
from bpy.types import Operator

logger = logging.getLogger(__name__)

OBJECT_NAME = "tree_rows"
MAT_NAME = "condor_tree"
TEX_FILE = "tree_rows.dds"        # file-mode texture (copied into autogen/Textures on Batch)

# --- categories ------------------------------------------------------------
# The trees are split by WHERE THEY CAME FROM, one object per category, so each can get
# its own model later. Until the models are drawn every category is the same crossed
# billboard, only in a different colour, so they can be told apart in the viewport.
#   1 tree_rows_1_alley     natural=tree_row           a row of trees / an alley
#   2 tree_rows_2_hedge     barrier=hedge & friends    a hedgerow (a bush, not a tree)
#   3 tree_rows_3_roadside  highway=* + tree_lined     the trees lining a road
#   4 tree_rows_4_solitary  natural=tree               a single mapped tree
# The number stays in the name so a model can be tied to it; the word after it is only
# there so the outliner says what the object actually is.
# Copernicus has no category of its own - those are lines too, so they go into 1.
KIND_CATEGORY = {"tree_row": 1, "hedge": 2, "tree_lined": 3, "tree": 4, "copernicus": 1}
CATEGORY_NAME = {1: "alley", 2: "hedge", 3: "roadside", 4: "solitary"}
# A model is used at its own size, only SHRUNK by up to this much at random, so trees
# standing next to each other are not all exactly the same height.
MODEL_SHRINK = 0.25
CATEGORY_COLOR = {
    1: (0.05, 0.30, 0.05, 1.0),   # dark green
    2: (0.55, 0.45, 0.05, 1.0),   # olive/yellow  - hedgerows
    3: (0.05, 0.25, 0.45, 1.0),   # blue          - roadside trees
    4: (0.45, 0.10, 0.30, 1.0),   # purple        - single trees
}
_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "3Dobjects")

PATCH_HALF = 2880.0
PATCH_SIZE = 5760.0

# --- geometry tuning -------------------------------------------------------
TREE_HEIGHT = 12.0            # default tree height (m) when the OSM way has no height tag
HEIGHT_VARIANCE = 0.20        # default height varies randomly by +/- this fraction (20 %)
# A height=* mapped in OSM is a measured value, so it is trusted: only a slight variation
# is kept so a row of tagged trees does not look like a fence of identical clones.
TAGGED_HEIGHT_VARIANCE = 0.05
WIDTH_RATIO = 0.70            # quad width = this * tree height (a crown is narrower than tall)
MIN_HEIGHT = 3.0              # never build a tree lower than this (m)
# A height=* outside this range is not a tree height (an unparsable tag falls back to
# 30 m in the shared parser) -> such a way uses the default height instead.
MAX_HEIGHT = 25.0
BASE_SINK = 0.15              # the foot is pushed this far INTO the terrain, so no gap
                              # shows under the tree on a slope
# The two quads cross at 90 deg, so the shape repeats every 90 deg - a random angle out of
# a quarter turn already gives every distinct orientation.
ROT_RANGE = math.pi / 2.0
SEED_PREFIX = "condor_tree_row"   # makes the random placement reproducible

# --- Spacing sliders (Scene properties) ------------------------------------
# Trees and hedges need a different spacing: a tree crown is over 10 m across, a hedge
# bush half that, and a hedge is supposed to be a CONTINUOUS strip, not a row of blobs.
SPACING_DEFAULT = 20.0        # distance between trees along the row (m)
SPACING_MIN = 10.0
SPACING_MAX = 30.0
SPACING_STEP = 50             # Blender FloatProperty step is in 1/100 units -> 0.5 m
HEDGE_SPACING_DEFAULT = 8.0   # distance between hedge bushes (m)
HEDGE_SPACING_MIN = 5.0
HEDGE_SPACING_MAX = 10.0
# The hedge model is stretched sideways so it spans the spacing and the bushes touch,
# making one continuous hedgerow. Slightly over 1 so they overlap instead of just meeting.
HEDGE_OVERLAP = 1.35

MIN_ROW_LENGTH = 1.0          # a way shorter than this gets a single tree at its start
MAX_TREES = 60000             # safety stop for one patch (a huge forest of rows)

# --- tree-lined roads (highway=* + tree_lined=*) ---------------------------
# These trees line a ROAD, so they must not end up standing on the carriageway: they are
# pushed sideways off the centreline, perpendicular to the segment they sit on. 6 m clears
# a normal two-lane road (2 x 3.5 m lane = 3.5 m to the edge) plus a verge. Change this
# one number to move all tree-lined trees closer to / further from the road.
TREE_LINED_OFFSET = 6.0
# ...and when the way says how wide it is (width=*), the trees stand half that plus this
# much bank, so the trees along a wide river are on the bank and not in the water.
LINED_BANK = 3.0
# ...and width=* is only a hint. What really decides is the WATER MASK - the alpha of the
# patch orthophoto t<patch>.dds, the same one the bridges read. A tree that lands on water
# is pushed further off the centreline in steps until it is on dry land; if it is still in
# the water after WATER_PUSH_MAX it is not built at all.
WATER_PUSH_STEP = 2.0
WATER_PUSH_MAX = 60.0
# tree_lined value -> which side(s), +1 = LEFT of the way's direction, -1 = RIGHT.
# 'no' (and anything else) is not listed, so it is simply not built.
TREE_LINED_SIDES = {"yes": (1.0, -1.0), "both": (1.0, -1.0),
                    "left": (1.0,), "right": (-1.0,)}
NO_OFFSET = (0.0,)            # ordinary lines (tree row / hedge) sit ON the way itself

# The values that MEAN "there are trees here" for the namespaced sub-keys and for
# denotation / alley. 'separate' means the trees are ALSO mapped as their own
# natural=tree / natural=tree_row way - those are built from that way, so a side marked
# 'separate' is NOT built again here (it would double every tree).
_TREE_LINED_YES = ("yes", "both", "left", "right")
# tree_lined:<side>=* -> which physical side that sub-key is about
_TREE_LINED_SUBKEYS = {"tree_lined:left": (1.0,),
                       "tree_lined:right": (-1.0,),
                       "tree_lined:both": (1.0, -1.0)}


def _lined_offset(tags):
    """How far off the centreline the trees of a tree-lined way stand (m).

    A road is a few metres wide, a river can be fifty - with a fixed offset the trees of a
    wide river would end up IN the water. So when the way says how wide it is (width=*),
    half of that plus a bank is used; without it the road default stays."""
    from .operators import _parse_height_str        # same "12 m" / "12" parser

    for key in ("width", "est_width"):
        raw = tags.get(key)
        if not raw:
            continue
        try:
            w = _parse_height_str(raw)
        except Exception:
            continue
        if 0.5 <= w <= 500.0:
            return max(TREE_LINED_OFFSET, w / 2.0 + LINED_BANK)
    return TREE_LINED_OFFSET


def _tree_lined_sides(tags):
    """Every side of a road that carries trees, from ALL the ways this can be tagged:

      * tree_lined=yes/both/left/right      - the old, flat spelling
      * tree_lined:left / :right / :both    - the namespaced sub-keys the wiki now asks
                                              for; value yes/no/separate
      * denotation=avenue                   - the road is an avenue (trees on both sides)
      * alley=left/right/both               - deprecated, but still in the data

    Returns a tuple of side multipliers (+1 = left of the way's direction, -1 = right),
    each side only once, or () when the road carries none."""
    sides = set()

    # 1) flat tree_lined=*
    v = (tags.get("tree_lined") or "").strip().lower()
    for s in TREE_LINED_SIDES.get(v, ()):
        sides.add(s)

    # 2) namespaced tree_lined:left / :right / :both = yes|no|separate
    for key, key_sides in _TREE_LINED_SUBKEYS.items():
        val = (tags.get(key) or "").strip().lower()
        if val in ("yes", "both", "left", "right"):
            for s in key_sides:
                sides.add(s)
        # 'no' -> nothing, 'separate' -> the trees are their own way, built from there

    # 3) denotation=avenue - an avenue is lined on both sides
    if (tags.get("denotation") or "").strip().lower() == "avenue":
        sides.update((1.0, -1.0))

    # 4) alley=left/right/both (deprecated spelling, still present in old data)
    a = (tags.get("alley") or "").strip().lower()
    if a in TREE_LINED_SIDES:
        for s in TREE_LINED_SIDES[a]:
            sides.add(s)

    return tuple(sorted(sides, reverse=True))    # left (+1) first, then right (-1)

# --- single trees (natural=tree on a NODE) ---------------------------------
# A single mapped tree is built exactly like any other one. It is only skipped when a
# tree already stands this close - a lone tree is very often mapped ON a tree row that is
# drawn through the same spot, and two trees in one place look like an error.
SINGLE_TREE_MIN_GAP = 4.0
GRID_CELL = 4.0               # fixed cell of the "a tree already stands here" grid


# ---------------------------------------------------------------------------
# OSM parsing
# ---------------------------------------------------------------------------
def _line_kind(tags):
    """(kind, offsets) for a way, or (None, None) when it carries no line of trees.

    Every source is built EXACTLY the same way; kind only says where the line came from,
    so the report can tell the user how much came from which:
      * natural=tree_row   - an alley / a row of trees
      * barrier=hedge      - a hedgerow (in England around nearly every field)
      * natural=hedge      - the older / non-standard way of tagging the same hedgerow
      * barrier=hedge_bank - a bank with a hedge on top (Cornish hedge)
      * highway=* + tree_lined=yes/both/left/right - a tree-lined ROAD

    offsets = how far SIDEWAYS off the way the trees stand (metres, + = left of the way's
    direction). A tree row / hedge IS the line, so it gets 0.0; a tree-lined road has the
    trees beside the carriageway, on one or both sides."""
    if tags.get("natural") == "tree_row":
        return "tree_row", NO_OFFSET
    # ALL the hedge spellings are one and the same thing:
    #   barrier=hedge       - the correct, approved tag
    #   barrier=hedge_bank  - a bank with a hedge on top
    #   natural=hedge       - the older / non-standard spelling (a tagging mistake in the
    #                         wiki, but there are thousands of them in the data)
    #   fence_type=hedge    - likewise a mistake, likewise present in the data
    if (tags.get("barrier") in ("hedge", "hedge_bank")
            or tags.get("natural") == "hedge"
            or tags.get("fence_type") == "hedge"):
        return "hedge", NO_OFFSET
    # a tree-lined ROAD or WATERWAY - every spelling of it, see _tree_lined_sides
    if tags.get("highway") or tags.get("waterway"):
        sides = _tree_lined_sides(tags)
        if sides:                      # 'no' / 'separate' / missing -> not built here
            return "tree_lined", tuple(s * _lined_offset(tags) for s in sides)
    return None, None


def _parse_tree_rows(root, projector):
    """Return [(points, height, has_height, way_id, kind, offsets)] for every line of
    trees in the OSM tree (see _line_kind for the sources) AND for every single tree
    (a NODE tagged natural=tree), which comes back as kind 'tree' with its two points
    identical - a line of zero length, i.e. exactly one tree.
    points = [(x, y)] in patch coordinates (the whole way, also outside the patch, so a
    row reaching over the border is still sampled correctly)."""
    from .operators import _parse_height_str

    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}

    rows = []
    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        kind, offsets = _line_kind(tags)
        if kind is None:
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
        # height=* on a ROAD is the height of the road structure (a bridge, a wall), never
        # the height of its trees - so a tree-lined road always uses the default height.
        has_h = kind != "tree_lined" and "height" in tags
        h = _parse_height_str(tags.get("height", "0")) if has_h else 0.0
        if has_h and not (MIN_HEIGHT <= h <= MAX_HEIGHT):   # nonsense -> use the default
            has_h, h = False, 0.0
        rows.append((pts, h, has_h, w.get("id") or "", kind, offsets))

    # Single trees mapped as a node. They are put AFTER the lines on purpose: a single
    # tree is dropped when the lines already put one in the same spot (see
    # SINGLE_TREE_MIN_GAP), so the lines have to be built first.
    for n in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        nat = tags.get("natural")
        bar = tags.get("barrier")
        # natural=tree is the proper way to map a single tree, natural=tree_group a small
        # clump of them. tree_row / hedge belong on a way, but they DO turn up on nodes as
        # well - such a node is built as one tree of that category instead of being
        # silently thrown away.
        if nat in ("tree", "tree_group"):
            kind = "tree"
        elif nat == "tree_row":
            kind = "tree_row"
        elif (nat == "hedge" or bar in ("hedge", "hedge_bank")
                or tags.get("fence_type") == "hedge"):
            kind = "hedge"
        else:
            continue
        nid = n.get("id")
        if nid not in node_coords:
            continue
        lat, lon = node_coords[nid]
        p = projector.project(lat, lon)
        has_h = "height" in tags
        h = _parse_height_str(tags.get("height", "0")) if has_h else 0.0
        if has_h and not (MIN_HEIGHT <= h <= MAX_HEIGHT):
            has_h, h = False, 0.0
        rows.append(([p, p], h, has_h, nid or "", kind, NO_OFFSET))
    return rows


# ---------------------------------------------------------------------------
# Getting the tree rows into the patch OSM - ON DEMAND ONLY
# ---------------------------------------------------------------------------
# The main buildings query is NOT touched: when this feature is not used, nothing extra
# is ever downloaded. Only when the trees are actually being built does each patch get
# checked - and only if its map_<patch>.osm holds no such line yet, ONE tiny extra
# Overpass query is sent and its result merged into that file.
#
# So a patch which genuinely has no line of trees is not asked about over and over, the
# file gets an invisible marker (an XML comment) saying "already asked".
#
# The marker is VERSIONED, because each version asked for MORE than the one before:
#   v1 = natural=tree_row only
#   v2 = + barrier=hedge
#   v3 = + natural=hedge, barrier=hedge_bank, tree-lined roads
#   v4 = + natural=tree (single trees mapped as a node)
#   v6 = + the namespaced tree_lined:left / :right / :both sub-keys, denotation=avenue,
#          alley=*, fence_type=hedge, natural=tree_group, tree-lined WATERWAYS, and the
#          hedge_bank / fence_type nodes that were parsed but never downloaded
# A file marked by an older version therefore knows nothing about the sources added
# later, so an older marker does NOT count and such a patch is asked once more (this time
# for everything). The old comment is rewritten to the current one during the merge, so
# there is never more than one marker in the file.
_LINE_MARKS = (b'"tree_row"', b'"hedge"', b'"hedge_bank"', b'"tree_lined"',
               b'"tree_lined:', b'"tree"', b'"tree_group"', b'"avenue"', b'"alley"')
_FETCH_MARK = b"condor_tree_rows_fetched_v6"
_FETCH_COMMENT = "  <!-- condor_tree_rows_fetched_v6 -->"
_FETCH_COMMENTS_OLD = ("<!-- condor_tree_rows_fetched_v5 -->",
                       "<!-- condor_tree_rows_fetched_v4 -->",
                       "<!-- condor_tree_rows_fetched_v3 -->",
                       "<!-- condor_tree_rows_fetched_v2 -->",
                       "<!-- condor_tree_rows_fetched -->")


def _osm_scan(osm_path):
    """(has_line, already_asked) for a patch OSM - a cheap CHUNKED TEXT scan, NOT an XML
    parse. This runs for every patch of the range, so it must stay fast: a patch with no
    tree row and no hedge must never cost more than reading its file through once."""
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
        logger.warning("tree_rows: cannot read %s: %s", osm_path, e)
    return has_line, marked


def _tree_row_query(lat_min, lat_max, lon_min, lon_max):
    """Overpass query for the lines of trees alone (tiny): every source, plus the nodes
    they are made of through the recursion down. tree_lined is asked for without filtering
    the value - keeping the query simple; tree_lined=no is thrown away when parsing."""
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    return ("[out:xml][timeout:60];\n(\n"
            # --- rows of trees (WAYS) ---
            f'  way["natural"="tree_row"]({bbox});\n'
            # --- hedgerows (WAYS), every spelling incl. the two wrong ones ---
            f'  way["barrier"="hedge"]({bbox});\n'
            f'  way["barrier"="hedge_bank"]({bbox});\n'
            f'  way["natural"="hedge"]({bbox});\n'
            f'  way["fence_type"="hedge"]({bbox});\n'
            # --- tree-lined ROADS and WATERWAYS (WAYS), every spelling ---
            # the value is not filtered here (keeps the query simple); tree_lined=no and
            # tree_lined:*=no / =separate are thrown away when parsing
            f'  way["highway"]["tree_lined"]({bbox});\n'
            f'  way["highway"]["tree_lined:left"]({bbox});\n'
            f'  way["highway"]["tree_lined:right"]({bbox});\n'
            f'  way["highway"]["tree_lined:both"]({bbox});\n'
            f'  way["highway"]["denotation"="avenue"]({bbox});\n'
            f'  way["highway"]["alley"]({bbox});\n'
            f'  way["waterway"]["tree_lined"]({bbox});\n'
            f'  way["waterway"]["tree_lined:left"]({bbox});\n'
            f'  way["waterway"]["tree_lined:right"]({bbox});\n'
            f'  way["waterway"]["tree_lined:both"]({bbox});\n'
            # --- single trees / small clumps (NODES) ---
            f'  node["natural"="tree"]({bbox});\n'
            f'  node["natural"="tree_group"]({bbox});\n'
            # tree_row / hedge belong on a way, but they do appear on nodes too
            f'  node["natural"="tree_row"]({bbox});\n'
            f'  node["natural"="hedge"]({bbox});\n'
            f'  node["barrier"="hedge"]({bbox});\n'
            f'  node["barrier"="hedge_bank"]({bbox});\n'
            f'  node["fence_type"="hedge"]({bbox});\n'
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
        logger.warning("tree_rows: the Overpass answer could not be parsed: %s", e)
        return None
    try:
        with open(osm_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        logger.warning("tree_rows: cannot read %s: %s", osm_path, e)
        return None

    close = text.rfind("</osm>")
    if close < 0:
        logger.warning("tree_rows: %s is not a complete OSM file - left untouched",
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
        old = next((o for o in _FETCH_COMMENTS_OLD if o in text), None)
        if old is not None:
            # marked by an older version (it knew fewer sources) -> upgrade the marker
            text = text.replace(old, _FETCH_COMMENT.strip(), 1)
            close = text.rfind("</osm>")
        else:
            head = text.find("<osm")
            head_end = text.find(">", head) if head >= 0 else -1
            if head_end > 0:
                text = text[:head_end + 1] + "\n" + _FETCH_COMMENT + text[head_end + 1:]
                close = text.rfind("</osm>")
    if add:
        text = text[:close] + "\n".join(add) + "\n" + text[close:]

    tmp = osm_path + ".tree_tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, osm_path)
    except OSError as e:
        logger.warning("tree_rows: %s could not be updated: %s", os.path.basename(osm_path), e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return len(add)


def _fetch_tree_rows_into_osm(paths, patch_id, osm_path):
    """Ask Overpass for JUST the tree rows of this patch and merge them into its
    map_<patch>.osm. The patch bounding box and the HTTP call are taken from the
    plugin's own osm_downloader, so the server list, the timeouts and the retries stay
    exactly the same as everywhere else. Never raises: without a connection it only logs
    a warning and the import carries on."""
    try:
        from ..io.patch_metadata import load_patch_metadata
        from . import osm_downloader as _osm
        txt_path = next((p for p in (
            os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
            os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
        if not txt_path:
            print(f"[tree_rows] patch {patch_id}: h{patch_id}.txt not found in Heightmaps "
                  f"- the tree rows cannot be downloaded")
            _osm.remember_failed(patch_id, "tree rows (no heightmap txt)")
            return False
        meta = load_patch_metadata(txt_path)
        content = _osm._overpass_fetch(_tree_row_query(
            meta.lat_min, meta.lat_max, meta.lon_min, meta.lon_max),
            what="tree rows", patch_id=patch_id)
        if content is None:
            # a failed fetch writes NO marker, so it is tried again next time
            print(f"[tree_rows] patch {patch_id}: the Overpass query failed - "
                  f"the tree rows are skipped for now")
            logger.warning("tree_rows: the Overpass query for patch %s failed - the tree "
                           "rows are skipped for now", patch_id)
            return False
        n = _merge_osm_elements(osm_path, content)
        if n is None:
            # downloaded fine, but writing it into the patch OSM did not work - used to
            # be silent, which is how a patch could end up without its trees unnoticed
            print(f"[tree_rows] patch {patch_id}: downloaded, but writing them into "
                  f"map_{patch_id}.osm failed - the tree rows are skipped for now")
            _osm.remember_failed(patch_id, "tree rows (writing into the patch OSM failed)")
            return False
        print(f"[tree_rows] patch {patch_id}: {n} tree-row element(s) added to "
              f"map_{patch_id}.osm")
        return True
    except Exception as e:
        logger.warning("tree_rows: fetching the tree rows for %s failed: %s", patch_id, e)
        return False


def _ensure_tree_row_data(paths, patch_id, osm_path):
    """Make sure the patch OSM holds its lines of trees (tree_row + hedge).
    Returns (has_rows, fetched).
    Cheap for a patch that has none: one text scan, and the Overpass query only the
    FIRST time (afterwards the marker in the file says it has been asked already)."""
    has_rows, marked = _osm_scan(osm_path)
    # ONLY the marker decides. A file that already holds some line (a tree_row fetched by
    # an earlier version) still knows nothing about the sources added later, so it must
    # be asked once more - that is exactly what the versioned marker is for.
    if marked:
        return has_rows, False
    if not _fetch_tree_rows_into_osm(paths, patch_id, osm_path):
        # The download did not work, but the file may already hold lines of trees from an
        # earlier run - build those instead of throwing them away. No marker is written,
        # so the missing sources are fetched again next time.
        if has_rows:
            print(f"[tree_rows] patch {patch_id}: download failed - building the tree rows "
                  f"already in map_{patch_id}.osm")
        return has_rows, False
    has_rows, _m = _osm_scan(osm_path)
    return has_rows, True


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


def _cumulative(pts):
    """Arc length at every point of a polyline."""
    cum = [0.0] * len(pts)
    for i in range(1, len(pts)):
        cum[i] = cum[i - 1] + math.hypot(pts[i][0] - pts[i - 1][0],
                                         pts[i][1] - pts[i - 1][1])
    return cum


def _sample(pts, cum, s):
    """(x, y, dx, dy) at arc length s along the polyline: the point plus the unit
    direction of the SEGMENT it lies on (not of the whole line), so a tree beside a
    bending road follows that bend."""
    n = len(cum)
    idx = n - 2
    t = 1.0
    for i in range(n - 1):
        if s <= cum[i + 1]:
            seg = cum[i + 1] - cum[i]
            t = 0.0 if seg < 1e-9 else (s - cum[i]) / seg
            idx = i
            break
    ax, ay = pts[idx]
    bx, by = pts[idx + 1]
    d = math.hypot(bx - ax, by - ay) or 1.0
    return (ax + (bx - ax) * t, ay + (by - ay) * t, (bx - ax) / d, (by - ay) / d)


def _add_tree(verts, faces, uvs, x, y, z_foot, width, height, angle):
    """One tree: two vertical quads crossed into an X (8 verts, 2 quads = 4 triangles).
    Each quad carries the WHOLE texture (UV 0..1 in both directions)."""
    hw = width / 2.0
    z_top = z_foot + height
    for k in (0, 1):                       # the second quad is turned by 90 deg
        a = angle + k * (math.pi / 2.0)
        dx, dy = math.cos(a) * hw, math.sin(a) * hw
        b = len(verts)
        verts.append((x - dx, y - dy, z_foot)); uvs.append((0.0, 0.0))
        verts.append((x + dx, y + dy, z_foot)); uvs.append((1.0, 0.0))
        verts.append((x + dx, y + dy, z_top)); uvs.append((1.0, 1.0))
        verts.append((x - dx, y - dy, z_top)); uvs.append((0.0, 1.0))
        faces.append((b, b + 1, b + 2, b + 3))


def _load_model(path):
    """Read a tree model (v / vt / f) and flatten it so every vertex carries exactly one
    UV - the mesh builder needs them 1:1. Returns (verts, uvs, faces) or None."""
    verts, uvs, faces = [], [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("v "):
                    p = line.split()
                    verts.append((float(p[1]), float(p[2]), float(p[3])))
                elif line.startswith("vt "):
                    p = line.split()
                    uvs.append((float(p[1]), float(p[2])))
                elif line.startswith("f "):
                    vi, ti = [], []
                    for tok in line.split()[1:]:
                        bits = tok.split("/")
                        vi.append(int(bits[0]) - 1)
                        ti.append(int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1)
                    faces.append((vi, ti))
    except Exception as e:
        logger.warning("tree_rows: model %s could not be read: %s",
                       os.path.basename(path), e)
        return None
    if not verts or not faces:
        return None
    native_h = max(v[2] for v in verts) - min(v[2] for v in verts)
    native_w = max(max(v[0] for v in verts) - min(v[0] for v in verts),
                   max(v[1] for v in verts) - min(v[1] for v in verts))
    # one vertex per (v, vt) pair
    out_v, out_uv, out_f, seen = [], [], [], {}
    for (vi, ti) in faces:
        face = []
        for k, v in enumerate(vi):
            t = ti[k]
            key = (v, t)
            if key not in seen:
                seen[key] = len(out_v)
                out_v.append(verts[v])
                out_uv.append(uvs[t] if 0 <= t < len(uvs) else (0.0, 0.0))
            face.append(seen[key])
        out_f.append(tuple(face))
    return out_v, out_uv, out_f, native_h, native_w


_MODEL_CACHE = {}


def _model_for(category):
    """The model of one category, loaded once. None -> the built-in billboard is used."""
    if category in _MODEL_CACHE:
        return _MODEL_CACHE[category]
    name = f"{OBJECT_NAME}_{category}_{CATEGORY_NAME.get(category, 'other')}.obj"
    path = os.path.join(_ASSETS, name)
    m = _load_model(path) if os.path.exists(path) else None
    if m is None:
        logger.warning("tree_rows: model %s not found - the built-in crossed billboard "
                       "is used for category %d", name, category)
    _MODEL_CACHE[category] = m
    return m


def _add_model(model, verts, faces, uvs, x, y, z_foot, scale, angle, widen=1.0):
    """Place one model at ITS OWN size times `scale`, turned by `angle` around the
    vertical axis, foot at z_foot. `widen` stretches it sideways only (used to make the
    hedge bushes span the spacing so the hedgerow is continuous)."""
    mv, muv, mf = model[0], model[1], model[2]
    base = len(verts)
    ca, sa = math.cos(angle), math.sin(angle)
    sw = scale * widen
    for (vx, vy, vz) in mv:
        verts.append((x + vx * sw * ca - vy * sw * sa,
                      y + vx * sw * sa + vy * sw * ca,
                      z_foot + vz * scale))
    uvs.extend(muv)
    for face in mf:
        faces.append(tuple(i + base for i in face))


def _water_sampler(paths, patch_id):
    """is_water(x, y) read from the patch orthophoto's alpha, or None when it cannot be
    had. The reader lives in bridges.py - this only borrows it, and works without it."""
    try:
        from .bridges import _load_water_sampler
    except Exception:
        return None
    for cand in (
        os.path.join(paths['landscape'], "Textures", f"t{patch_id}.dds"),
        os.path.join(paths['heightmaps'], f"t{patch_id}.dds"),
        os.path.join(paths['landscape'], "Working", "Textures", f"t{patch_id}.dds"),
        os.path.join(paths['landscape'], "Working", "HeightMaps", f"t{patch_id}.dds"),
    ):
        try:
            w = _load_water_sampler(cand)
        except Exception:
            w = None
        if w is not None:
            return w
    return None


def _off_water(x, y, dx, dy, off, is_water):
    """Keep a tree out of the water: when (x, y) is over water, step further away from the
    centreline until dry land is found. Returns (x, y) or None when it stays wet.
    dx, dy is the direction of the line, off says which side the tree is on."""
    if is_water is None or not is_water(x, y):
        return x, y
    if not off:                       # a tree ON the line (a row / a hedge) - just drop it
        return None
    s = 1.0 if off > 0 else -1.0
    d = WATER_PUSH_STEP
    while d <= WATER_PUSH_MAX:
        nx, ny = x - dy * s * d, y + dx * s * d
        if not is_water(nx, ny):
            return nx, ny
        d += WATER_PUSH_STEP
    return None


def _build_tree_rows(rows, z_at, spacing, hedge_spacing=HEDGE_SPACING_DEFAULT,
                     is_water=None):
    """Build the merged geometry of all lines of trees of one patch.
    Returns (verts, faces, uvs, n_trees, n_rows, per_kind), where per_kind is
    {kind: [trees, rows]} - purely for the report, a hedge is built exactly like a tree
    row. Trees are spread EVENLY over each line (the real step is the nearest divisor of
    the line length to `spacing`), so a line always starts and ends with a tree. Trees
    falling outside the patch are dropped - the neighbouring patch builds its own half."""
    parts = {}                    # category number -> [verts, faces, uvs]
    n_trees = n_rows = 0
    per_kind = {}
    spacing = max(SPACING_MIN, min(SPACING_MAX, float(spacing)))
    hedge_spacing = max(HEDGE_SPACING_MIN, min(HEDGE_SPACING_MAX, float(hedge_spacing)))
    # Where a tree already stands, in cells of SINGLE_TREE_MIN_GAP: only single trees are
    # checked against it (a node mapped on a line that is drawn through the same spot).
    grid = {}
    for (pts, height, has_h, way_id, kind, offsets) in rows:
        # a hedgerow is planted much closer together than an alley
        sp = hedge_spacing if kind == "hedge" else spacing
        cum = _cumulative(pts)
        total = cum[-1]
        if total < MIN_ROW_LENGTH:
            steps = 0                       # a dot of a way -> one tree at its start
            step = 0.0
        else:
            steps = max(1, int(round(total / sp)))
            step = total / steps
        used = False
        kc = per_kind.setdefault(kind, [0, 0])
        cat = KIND_CATEGORY.get(kind, 1)
        part = parts.setdefault(cat, [[], [], []])
        for oi, off in enumerate(offsets or NO_OFFSET):
            for i in range(steps + 1):
                x, y, dx, dy = _sample(pts, cum, i * step)
                if off:
                    # push the tree off the road, perpendicular to THIS segment
                    # (+off = left of the way's direction)
                    x -= dy * off
                    y += dx * off
                if not (-PATCH_HALF <= x <= PATCH_HALF and -PATCH_HALF <= y <= PATCH_HALF):
                    continue                # outside this patch - the neighbour builds it
                # never leave a tree standing in water (orthophoto alpha)
                dry = _off_water(x, y, dx, dy, off, is_water)
                if dry is None:
                    continue
                x, y = dry
                # How close another tree may stand: the WHOLE crown of this category's
                # model, so two solitary trees only touch at the edge instead of growing
                # through each other. Falls back to the fixed gap without a model.
                mdl = _model_for(cat)
                gap = max(SINGLE_TREE_MIN_GAP,
                          mdl[4] if (mdl and kind != "hedge") else 0.0)
                # the grid cell stays FIXED so the keys are always comparable; how many
                # rings of cells are searched follows from the gap
                cx, cy = int(x // GRID_CELL), int(y // GRID_CELL)
                r = int(gap // GRID_CELL) + 1
                # anything placed as a POINT is checked against what already stands there
                # (a lone tree is often mapped on top of a row drawn through the spot)
                if (kind in ("tree", "copernicus") or steps == 0) and any(
                        (px - x) ** 2 + (py - y) ** 2 < gap ** 2
                        for gx in range(cx - r, cx + r + 1)
                        for gy in range(cy - r, cy + r + 1)
                        for (px, py) in grid.get((gx, gy), ())):
                    continue                # a tree already stands here (mapped twice)
                grid.setdefault((cx, cy), []).append((x, y))
                # Seeded from the OSM way id + the tree's index: the same patch always
                # comes out exactly the same, however many times it is generated. The side
                # goes into the seed ONLY for a two-sided road, so that the two rows do not
                # come out as mirror images of each other (and single-sided lines keep
                # exactly the trees they had before).
                tag = f"{way_id}:{oi}" if len(offsets or NO_OFFSET) > 1 else way_id
                rnd = random.Random(f"{SEED_PREFIX}:{tag}:{i}")
                var = TAGGED_HEIGHT_VARIANCE if has_h else HEIGHT_VARIANCE
                base = height if has_h else TREE_HEIGHT
                h = max(MIN_HEIGHT, base * (1.0 + rnd.uniform(-var, var)))
                z_foot = z_at(x, y) - BASE_SINK
                ang = rnd.uniform(0.0, ROT_RANGE)
                model = _model_for(cat)
                if model is not None:
                    # The model is used at ITS OWN size, only shrunk a little at random so
                    # neighbouring trees are not all exactly the same height. A height=*
                    # mapped in OSM overrides that and scales the model to it.
                    scale = ((height / model[3]) if (has_h and model[3] > 0.01)
                             else 1.0 - rnd.uniform(0.0, MODEL_SHRINK))
                    # a hedge bush is widened to span the spacing, so the hedgerow is a
                    # continuous strip instead of a row of separate blobs
                    widen = 1.0
                    if kind == "hedge" and model[4] > 0.01:
                        widen = max(1.0, (sp * HEDGE_OVERLAP) / (model[4] * scale))
                    _add_model(model, part[0], part[1], part[2], x, y, z_foot, scale, ang,
                               widen)
                else:
                    _add_tree(part[0], part[1], part[2], x, y, z_foot,
                              h * WIDTH_RATIO, h, ang)
                n_trees += 1
                kc[0] += 1
                used = True
                if n_trees >= MAX_TREES:
                    break
            if n_trees >= MAX_TREES:
                break
        if used:
            n_rows += 1
            kc[1] += 1
        if n_trees >= MAX_TREES:
            logger.warning("tree_rows: stopped at %d trees (MAX_TREES)", MAX_TREES)
            break
    return parts, n_trees, n_rows, per_kind


# ---------------------------------------------------------------------------
# Blender material
# ---------------------------------------------------------------------------
def _get_material(category=1):
    """The material of one category. tree_rows.dds is hooked up with its alpha as a
    cut-out; while the texture does not exist yet each category gets its own plain
    colour (see CATEGORY_COLOR), so the four kinds can be told apart in the viewport."""
    name = f"{MAT_NAME}_{category}"
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat                        # the user's material -> use it as-is
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    return _hook_texture(mat, CATEGORY_COLOR.get(category, CATEGORY_COLOR[1]))


def _hook_texture(mat, fallback_color=None):
    """Put tree_rows.dds into `mat` with its alpha as a cut-out. When the texture is not
    there, the material is left as a plain colour instead - the import must not fail just
    because the texture has not been drawn yet."""
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    img_path = os.path.join(_ASSETS, TEX_FILE)
    if not os.path.exists(img_path):
        logger.warning("tree_rows: texture %s not found in assets/3Dobjects", TEX_FILE)
        if bsdf is not None and fallback_color is not None:
            bsdf.inputs["Base Color"].default_value = fallback_color
        return mat
    try:
        img = bpy.data.images.load(img_path, check_existing=True)
        try:
            img.alpha_mode = 'STRAIGHT'
        except Exception:
            pass
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.extension = 'CLIP'            # the crown fills the quad, nothing repeats
        if bsdf is not None:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            try:
                nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
            except Exception:
                pass                       # very old node set without an Alpha input
    except Exception as e:
        logger.warning("tree_rows: texture %s could not be loaded: %s", TEX_FILE, e)
        return mat
    # Alpha cut-out: Blender 3.6-4.1 use material.blend_method ('CLIP'), 4.2+ replaced it
    # with surface_render_method ('DITHERED'). Both are tried, every failure is ignored -
    # the material must never break the import on any supported version.
    for attr, values in (("blend_method", ('CLIP', 'BLEND')),
                         ("surface_render_method", ('DITHERED',))):
        for v in values:
            try:
                setattr(mat, attr, v)
                break
            except Exception:
                continue
    try:
        mat.show_transparent_back = False
    except Exception:
        pass
    return mat


def _copy_tree_texture(paths):
    """Copy tree.dds next to the patch OBJs (autogen/Textures) so Condor finds it. When
    the texture does not exist yet, the copy is simply skipped with a warning."""
    src = os.path.join(_ASSETS, TEX_FILE)
    if not os.path.exists(src):
        logger.warning("tree_rows: texture %s missing - nothing copied to Textures", TEX_FILE)
        return
    try:
        import shutil
        tdir = os.path.join(paths['autogen'], "Textures")
        os.makedirs(tdir, exist_ok=True)
        dst = os.path.join(tdir, TEX_FILE)
        if not os.path.exists(dst):
            shutil.copyfile(src, dst)
    except Exception as e:
        print(f"[tree_rows] texture copy failed: {e}")


def _kind_summary(per_kind):
    """' (tree rows 120 in 3, hedges 640 in 25)' for the report - how many trees came from
    which OSM source. Empty when there is nothing to say."""
    label = {"tree_row": "tree rows", "hedge": "hedges", "tree_lined": "tree-lined roads",
             "tree": "single trees", "copernicus": "Copernicus"}
    order = ("tree_row", "hedge", "tree_lined", "tree", "copernicus")
    parts = [f"{label.get(k, k)} {per_kind[k][0]} in {per_kind[k][1]}"
             for k in order if per_kind.get(k) and per_kind[k][0]]
    return f" ({', '.join(parts)})" if parts else ""


# ---------------------------------------------------------------------------
# Operator: Import Tree rows
# ---------------------------------------------------------------------------
class CONDOR_OT_import_tree_rows(Operator):
    bl_idname = "condor.import_tree_rows"
    bl_label = "Import Tree rows"
    bl_description = ("Generate rows of trees from OSM (natural=tree_row) as one "
                      "'tree_rows' object, spaced by the Spacing slider")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.condor_buildings
        # in Batch (file mode) the trees are written into the OBJ by the exporter,
        # so the manual Blender import is disabled (like the bridges Import)
        return (bool(props.condor_path) and props.landscape_name != 'NONE'
                and not getattr(context.scene, "condor_tree_row_batch", False))

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

        spacing, hedge_spacing = _spacings()

        if props.single_patch_mode and props.patch_id:
            patch_ids = [str(props.patch_id)]
        else:
            patch_ids = [f"{x:03d}{y:03d}"
                         for x in range(props.patch_x_min, props.patch_x_max + 1)
                         for y in range(props.patch_y_min, props.patch_y_max + 1)]

        total_trees = total_rows = total_existing = total_fetched = 0
        per_kind = {}            # {kind: [trees, rows]} - only to tell the user what came from where
        missing_osm = []
        for patch_id in patch_ids:
            osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
            if not ensure_patch_osm(paths, patch_id, True):
                missing_osm.append(patch_id)
                continue
            # THE ORDER MATTERS. A patch with no such line must cost almost nothing - the
            # whole range is walked through, so nothing expensive (XML parse, and above
            # all loading the terrain) may happen before it is clear there is anything to
            # build here at all.
            has_rows, fetched = _ensure_tree_row_data(paths, patch_id, osm_path)
            if fetched:
                total_fetched += 1
            # with the Copernicus layer on, a patch with nothing in OSM can still get trees
            if not has_rows and not _copernicus_on():
                continue
            txt_path = next((p for p in (
                os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
            if not txt_path:
                continue
            # the projector is needed for the parsing itself, the terrain only later
            try:
                meta = load_patch_metadata(txt_path)
                projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
                root = ET.parse(osm_path).getroot()
            except Exception as e:
                logger.warning("tree_rows: setup failed for %s: %s", patch_id, e)
                continue

            rows = _parse_tree_rows(root, projector)
            # --- COPERNICUS add-on (removable: comment out these 7 lines / delete
            # blender/tree_rows_copernicus.py) ---
            if _copernicus_on():
                try:
                    from . import tree_rows_copernicus as _cop
                    rows = rows + _cop.extra_rows(paths, patch_id, meta, projector, spacing)
                except Exception as e:
                    logger.warning("tree_rows: the Copernicus layer failed for %s: %s",
                                   patch_id, e)
            if not rows:
                continue

            # only NOW, when it is certain something will be built, the terrain is loaded
            from .terrain_smooth import (load_terrain_smoothed,
                                         resolve_smooth_or_source)
            terrain_file = resolve_smooth_or_source(paths['heightmaps'], patch_id)
            if not os.path.exists(terrain_file):
                continue
            try:
                terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
            except Exception as e:
                logger.warning("tree_rows: terrain load failed for %s: %s", patch_id, e)
                continue
            z_at = _make_terrain_z(terrain)

            # Which LODs to build into (same pattern as the bridges): the trees are the
            # same in both, only the target collection differs.
            lod_variants = []
            if props.output_lod in ('LOD0', 'BOTH'):
                lod_variants.append("")
            if props.output_lod in ('LOD1', 'BOTH'):
                lod_variants.append("_LOD1")
            if not lod_variants:
                lod_variants.append("")

            for vi, suffix in enumerate(lod_variants):
                # already present for THIS patch + LOD? -> skip so trees aren't duplicated
                _pcol = bpy.data.collections.get(
                    f"Condor_{props.landscape_name}_{patch_id}{suffix}")
                if _pcol is not None and any(o.name.lower().startswith(OBJECT_NAME)
                                             for o in _pcol.all_objects):
                    total_existing += 1
                    continue

                parts, ntr, nrw, kinds = _build_tree_rows(rows, z_at, spacing,
                                                          hedge_spacing,
                                                          _water_sampler(paths, patch_id))
                if vi == 0:
                    total_trees += ntr
                    total_rows += nrw
                    for k, (kt, kr) in kinds.items():
                        cur = per_kind.setdefault(k, [0, 0])
                        cur[0] += kt
                        cur[1] += kr
                if not parts:
                    continue

                # one object per category, so each can get its own model later
                for cat in sorted(parts):
                    verts, faces, uvs = parts[cat]
                    if not verts:
                        continue
                    obj_name = f"{OBJECT_NAME}_{cat}_{CATEGORY_NAME.get(cat, 'other')}"
                    mesh = bpy.data.meshes.new(obj_name)
                    mesh.from_pydata([tuple(v) for v in verts], [],
                                     [list(f) for f in faces])
                    mesh.update()
                    uvl = mesh.uv_layers.new(name="UVMap")
                    for loop in mesh.loops:
                        uvl.data[loop.index].uv = uvs[loop.vertex_index]
                    ob = bpy.data.objects.new(obj_name, mesh)
                    ob["patch_id"] = patch_id
                    ob["lod"] = suffix
                    ob.data.materials.append(_get_material(cat))

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

        # copy tree.dds into Working/Autogen/Textures so Condor (and a re-import of the
        # OBJ) finds it - same as the Batch/file-mode path does
        if total_trees > 0:
            _copy_tree_texture(paths)

        if total_trees == 0 and total_existing == 0:
            # Say WHY nothing appeared - silently doing nothing looks like a broken button.
            msg = ("Tree rows: nothing built - no tree row, hedge, tree-lined road or "
                   "single tree is mapped in OSM for the selected patch(es) "
                   f"({len(patch_ids)} checked")
            msg += (f", {total_fetched} additionally queried on Overpass)"
                    if total_fetched else ")")
            if missing_osm:
                msg += f" | OSM missing (skipped): {', '.join(missing_osm)}"
            self.report({'WARNING'}, msg)
            return {'FINISHED'}

        msg = (f"Tree rows: {total_trees} trees in {total_rows} line(s)"
               f"{_kind_summary(per_kind)}, spacing {spacing:.1f} m")
        if total_fetched:
            msg += f", tree rows downloaded for {total_fetched} patch(es)"
        if total_existing:
            msg += f", already imported {total_existing} (skipped)"
        if missing_osm:
            msg += f" | OSM missing (skipped): {', '.join(missing_osm)}"
        self.report({'INFO'} if not missing_osm else {'WARNING'}, msg)
        return {'FINISHED'}


class CONDOR_OT_merge_tree_rows(Operator):
    bl_idname = "condor.merge_tree_rows"
    bl_label = "Merge Tree rows"
    bl_description = ("Join the tree categories of each patch into one 'tree_rows' object "
                      "with the single 'condor_tree' material (the per-category colours "
                      "are dropped). Your own edits are kept; this cannot be undone by "
                      "the sliders afterwards")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(_category_objects())

    def execute(self, context):
        groups = {}                     # (collection, lod) -> [objects]
        for ob in _category_objects():
            col = ob.users_collection[0].name if ob.users_collection else ""
            groups.setdefault((col, ob.get("lod", "")), []).append(ob)
        if not groups:
            self.report({'WARNING'}, "Tree rows: nothing to merge.")
            return {'CANCELLED'}

        mat = _merged_material()
        merged = 0
        for (col_name, _lod), objs in groups.items():
            objs.sort(key=lambda o: o.name)
            target = objs[0]
            # everything into ONE material slot before joining, so the result has one
            for ob in objs:
                ob.data.materials.clear()
                ob.data.materials.append(mat)
            if len(objs) > 1:
                try:
                    with context.temp_override(active_object=target,
                                               selected_editable_objects=objs):
                        bpy.ops.object.join()
                except Exception as e:
                    logger.warning("tree_rows: merge failed in %s: %s", col_name, e)
                    continue
            target.name = OBJECT_NAME
            target.data.name = OBJECT_NAME
            merged += 1

        # the per-category colours are of no use now
        for cat in CATEGORY_COLOR:
            m = bpy.data.materials.get(f"{MAT_NAME}_{cat}")
            if m is not None and m.users == 0:
                try:
                    bpy.data.materials.remove(m)
                except Exception:
                    pass
        _MODEL_CACHE.clear()
        self.report({'INFO'}, f"Tree rows: merged into '{OBJECT_NAME}' "
                              f"({merged} patch/LOD), material '{MAT_NAME}'.")
        return {'FINISHED'}


def _category_objects():
    """The per-category objects (tree_rows_1_alley, ...) - NOT an already merged one."""
    pref = OBJECT_NAME + "_"
    return [o for o in bpy.data.objects
            if o.type == 'MESH' and o.name.lower().startswith(pref)]


def _merged_objects():
    """Objects that are already merged, i.e. named plain 'tree_rows'."""
    return [o for o in bpy.data.objects
            if o.type == 'MESH' and o.name.lower().split('.')[0] == OBJECT_NAME]


def _merged_material():
    """The single 'condor_tree' material used after merging - ALWAYS with the texture.

    It must not reuse one of the per-category materials: those may have been made while
    tree_rows.dds did not exist yet and are a plain colour. If the material is already
    there but carries no image, the texture is hooked up now."""
    mat = bpy.data.materials.get(MAT_NAME)
    if mat is None:
        mat = bpy.data.materials.new(MAT_NAME)
    if not mat.use_nodes:
        mat.use_nodes = True
    has_img = any(n.type == 'TEX_IMAGE' and n.image for n in mat.node_tree.nodes)
    if not has_img:
        _hook_texture(mat)
    return mat


# ---------------------------------------------------------------------------
# File mode (Batch): write the trees straight into each patch OBJ (LOD0 + LOD1),
# mirroring how the bridges do it. Nothing outside this file is edited.
# ---------------------------------------------------------------------------
# One-slot cache so a patch's setup (OSM parse + terrain) is done ONCE and reused for
# BOTH LOD0 and LOD1 (two separate exporter calls). Keyed by patch + the OSM file's
# mtime, so an edited OSM is re-read. A patch with NO such line is cached too (_NOTHING),
# so the second LOD does not go looking again.
_NOTHING = object()
_SETUP_CACHE = {"patch": None, "key": None, "setup": None}


def _patch_setup(paths, patch_id):
    """Return (rows, z_at) for a patch, or None. Cached per patch (one slot) so LOD0 and
    LOD1 share the same single parse / terrain load. Same order as the Import: nothing
    expensive - above all NOT the terrain - happens before it is clear the patch really
    has tree rows."""
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
        # the OSM may have just been extended by the tree rows -> re-read its mtime
        try:
            k = os.path.getmtime(osm_path)
        except OSError:
            k = key
        c["patch"], c["key"], c["setup"] = patch_id, k, setup
        return None if setup is _NOTHING else setup

    has_rows, _fetched = _ensure_tree_row_data(paths, patch_id, osm_path)
    if not has_rows:
        return _remember(_NOTHING)

    txt_path = next((p for p in (
        os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
        os.path.join(paths['heightmaps'], f"H{patch_id}.txt")) if os.path.exists(p)), None)
    if not txt_path:
        return _remember(_NOTHING)
    # the projector is needed for the parsing itself, the terrain only later
    try:
        meta = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(meta.zone_number, meta.translate_x, meta.translate_y)
        root = ET.parse(osm_path).getroot()
    except Exception as e:
        logger.warning("tree_rows: file-mode setup failed for %s: %s", patch_id, e)
        return _remember(_NOTHING)

    rows = _parse_tree_rows(root, projector)
    if not rows:
        return _remember(_NOTHING)

    # only NOW, when it is certain something will be built, the terrain is loaded
    from .terrain_smooth import load_terrain_smoothed, resolve_smooth_or_source
    terrain_file = resolve_smooth_or_source(paths['heightmaps'], patch_id)
    if not os.path.exists(terrain_file):
        return _remember(_NOTHING)
    try:
        terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
    except Exception as e:
        logger.warning("tree_rows: terrain load failed for %s: %s", patch_id, e)
        return _remember(_NOTHING)
    return _remember((rows, _make_terrain_z(terrain)))


def _generate_patch_geometry(paths, patch_id, spacing, hedge_spacing):
    """Build {category: (verts, faces, uvs)} of the tree rows for ONE patch in file mode,
    reusing the cached per-patch setup, or None if there is nothing."""
    setup = _patch_setup(paths, patch_id)
    if setup is None:
        return None
    rows, z_at = setup
    parts, ntr, nrw, kinds = _build_tree_rows(rows, z_at, spacing, hedge_spacing,
                                              _water_sampler(paths, patch_id))
    if not parts:
        return None
    print(f"[tree_rows] patch {patch_id}: {ntr} tree(s) in {nrw} line(s)"
          f"{_kind_summary(kinds)}")
    return parts


def _append_tree_material(obj_path):
    """Append the 'condor_tree' material (tree.dds) to the patch .mtl (same block shape
    the exporter writes)."""
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


def _write_tree_rows_into_obj(obj_path, paths, patch_id, spacing, hedge_spacing):
    """Append the tree-row objects to a file-mode patch OBJ (and their material to the
    .mtl), one 'o tree_rows_<category>' block each, with freshly computed per-triangle
    normals. Buildings / exporter untouched."""
    parts = _generate_patch_geometry(paths, patch_id, spacing, hedge_spacing)
    if not parts:
        return
    # File mode writes ONE object: the categories only exist so the models can be edited
    # per kind in Blender, and they all share the same material anyway - so in the OBJ
    # they are merged straight away, exactly like the Merge button does in the scene.
    verts, faces, uvs = [], [], []
    for cat in sorted(parts):
        cv, cf, cu = parts[cat]
        if not cv:
            continue
        base = len(verts)
        verts.extend(cv)
        uvs.extend(cu)
        faces.extend(tuple(i + base for i in f) for f in cf)
    if verts and _append_one_object(obj_path, OBJECT_NAME, verts, faces, uvs):
        _append_tree_material(obj_path)
        _copy_tree_texture(paths)


def _append_one_object(obj_path, obj_name, verts, faces, uvs):
    """Append ONE object block to the patch OBJ. True when something was written."""
    from ..config import CONDOR_AXIS_SWAP
    from ..io.obj_exporter import _condor_xform

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
        return False

    lines = open(obj_path, "r", encoding="utf-8").read().split("\n")
    base_v = sum(1 for l in lines if l.startswith("v "))
    base_vt = sum(1 for l in lines if l.startswith("vt "))
    base_vn = sum(1 for l in lines if l.startswith("vn "))
    f_lines = ["f " + " ".join(
        f"{vi + base_v + 1}/{vi + base_vt + 1}/{ni + base_vn + 1}" for vi in (a, b, c))
        for (a, b, c, ni) in tris]

    block = ["", f"o {obj_name}", f"usemtl {MAT_NAME}"] + v_lines + vt_lines + vn_lines + f_lines
    open(obj_path, "w", encoding="utf-8").write("\n".join(lines + block))
    return True


def _append_tree_rows_after_export(obj_filepath):
    """Called from the wrapped exporter: on Batch, write the tree rows into a file-mode
    patch OBJ. Both o<patch>.obj (LOD0) and o<patch>_LOD1.obj get the same trees."""
    import re
    if not getattr(bpy.context.scene, "condor_tree_row_batch", False):
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
    spacing, hedge_spacing = _spacings()
    _write_tree_rows_into_obj(obj_filepath, paths, patch_id, spacing, hedge_spacing)


def _patch_obj_exporter():
    """Wrap obj_exporter.export_condor_obj_mtl so AFTER it writes a patch OBJ the tree
    rows are appended (file mode). The exporter source is NOT edited; removing this
    module restores the original behaviour."""
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_tree_row_export_patched", False):
        return
    _orig = _oe.export_condor_obj_mtl

    def _patched(groups, obj_filepath, texture_map, *a, **k):
        stats = _orig(groups, obj_filepath, texture_map, *a, **k)
        try:
            _append_tree_rows_after_export(obj_filepath)
        except Exception as e:
            print(f"[tree_rows] file-mode append failed: {e}")
        return stats

    _oe._tree_row_orig_export = _orig
    _oe.export_condor_obj_mtl = _patched
    _oe._tree_row_export_patched = True


def _unpatch_obj_exporter():
    from ..io import obj_exporter as _oe
    if getattr(_oe, "_tree_row_export_patched", False):
        _oe.export_condor_obj_mtl = _oe._tree_row_orig_export
        _oe._tree_row_export_patched = False


# ---------------------------------------------------------------------------
# Panel row (called from panels.py inside the "Other objects" box)
# ---------------------------------------------------------------------------
def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Tree row", icon='OUTLINER_OB_MESH')
    row.prop(context.scene, "condor_tree_row_batch", text="Batch")
    row = box.row(align=True)
    row.operator("condor.import_tree_rows", text="Import", icon='IMPORT')
    row.operator("condor.merge_tree_rows", text="Merge", icon='AUTOMERGE_ON')
    # Once merged the categories are gone, so re-spacing would have to throw the merged
    # object (and the user's own edits in it) away - the sliders are greyed out instead.
    sub = box.column(align=True)
    sub.enabled = not _merged_objects()
    try:
        # the values live in the add-on preferences, so they are remembered for every
        # .blend - the panel only shows them
        prefs = context.preferences.addons[__package__.split('.')[0]].preferences
        sub.prop(prefs, "tree_row_spacing", text="Trees", slider=True)
        sub.prop(prefs, "tree_row_hedge_spacing", text="Hedges", slider=True)
    except Exception:
        pass
    # --- COPERNICUS add-on (removable: comment out these 5 lines) ---
    try:
        from . import tree_rows_copernicus            # noqa: F401 - only a presence check
        prefs = context.preferences.addons[__package__.split('.')[0]].preferences
        box.prop(prefs, "tree_row_copernicus", text="Copernicus")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Registration (operator + scene properties)
# ---------------------------------------------------------------------------
# The main Overpass query is deliberately NOT touched: the tree rows are fetched only
# when they are actually being built, per patch (see _ensure_tree_row_data). Somebody who
# does not use this feature therefore never downloads a single byte extra.
_classes = [CONDOR_OT_import_tree_rows, CONDOR_OT_merge_tree_rows]


def _patch_extra_obj_type():
    """Wrap operators._extra_obj_type so a separately imported 'tree_rows' object counts
    as an EXTRA object (like the bridges / chimneys), not as a building. Without it a
    patch that has only the trees imported would look like its buildings are already
    there and the buildings generation would skip it; the baked copy in the patch OBJ
    would also not be de-duplicated on re-import. The operators source is NOT edited;
    removing this module restores the original function."""
    from . import operators as _op
    if getattr(_op, "_tree_rows_type_patched", False):
        return
    _orig = _op._extra_obj_type

    def _patched(name):
        if (name or "").lower().startswith(OBJECT_NAME):
            return "tree_rows"
        return _orig(name)

    _op._tree_rows_orig_type = _orig
    _op._extra_obj_type = _patched
    _op._tree_rows_type_patched = True


def _unpatch_extra_obj_type():
    from . import operators as _op
    if getattr(_op, "_tree_rows_type_patched", False):
        _op._extra_obj_type = _op._tree_rows_orig_type
        _op._tree_rows_type_patched = False


# ---------------------------------------------------------------------------
# Spacing slider -> rebuild what is already in the scene
# ---------------------------------------------------------------------------
# Moving the slider re-spaces the trees that are ALREADY imported: the old 'tree_rows'
# objects are thrown away and built again with the new spacing. As long as nothing is
# imported the slider only sets a number and nothing happens.
#
# Dragging fires the update over and over, so the rebuild is not done in the callback
# itself (which also must not run bpy.ops): the callback only pushes a deadline and a
# timer does the work once the slider has been still for REBUILD_DELAY.
REBUILD_DELAY = 0.35
_rebuild_at = 0.0


def _prefs():
    """The add-on preferences, or None when they cannot be read."""
    try:
        return bpy.context.preferences.addons[__package__.split('.')[0]].preferences
    except Exception:
        return None


def _spacings():
    """(tree spacing, hedge spacing) from the ADD-ON PREFERENCES, so what the user sets
    holds for every .blend they open. Falls back to the defaults if they cannot be read."""
    p = _prefs()
    if p is None:
        return SPACING_DEFAULT, HEDGE_SPACING_DEFAULT
    try:
        return (float(p.tree_row_spacing), float(p.tree_row_hedge_spacing))
    except Exception:
        return SPACING_DEFAULT, HEDGE_SPACING_DEFAULT


def _copernicus_on():
    """True when the Copernicus layer is ticked in the add-on preferences."""
    p = _prefs()
    return bool(getattr(p, "tree_row_copernicus", False)) if p else False


def _scene_tree_objects():
    """What the sliders may rebuild: the per-category objects only. An already merged
    'tree_rows' is left alone - it may hold the user's own edits."""
    return _category_objects()


def _rebuild_trees():
    """Drop the imported tree objects and import them again with the current spacing."""
    for ob in _scene_tree_objects():
        mesh = ob.data
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            continue
        if mesh is not None and mesh.users == 0:
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:
                pass
    try:
        if CONDOR_OT_import_tree_rows.poll(bpy.context):
            bpy.ops.condor.import_tree_rows()
    except Exception as e:
        logger.warning("tree_rows: rebuild after the spacing change failed: %s", e)


def _rebuild_timer():
    import time
    left = _rebuild_at - time.time()
    if left > 0:
        return left                    # still being dragged - look again later
    _rebuild_trees()
    return None                        # done, unregister the timer


def _spacing_update(self, context):
    import time
    global _rebuild_at
    if not _scene_tree_objects():      # nothing imported yet -> the slider just sets a value
        return
    _rebuild_at = time.time() + REBUILD_DELAY
    try:
        if not bpy.app.timers.is_registered(_rebuild_timer):
            bpy.app.timers.register(_rebuild_timer, first_interval=REBUILD_DELAY)
    except Exception:
        pass


def register():
    from bpy.props import BoolProperty, FloatProperty
    bpy.types.Scene.condor_tree_row_batch = BoolProperty(
        name="Batch",
        description=("File mode (Import to Blender off): after each patch OBJ is written, "
                     "also generate the tree rows into it. Off by default"),
        default=False,
    )
    # The two spacings are NOT scene properties: they live in the add-on preferences
    # (see blender/properties.py), so what the user sets is remembered for every .blend.
    # The Copernicus tick is in the add-on preferences too (blender/properties.py).
    for c in _classes:
        bpy.utils.register_class(c)
    # 'tree_rows' is an extra object, not a building (see _patch_extra_obj_type)
    try:
        _patch_extra_obj_type()
    except Exception:
        pass
    # add the tree texture/material to the export maps so file-mode MTL gets it
    try:
        from .. import config
        config.TEXTURE_MAP.setdefault(MAT_NAME, TEX_FILE)
        config.MATERIAL_ALIAS.setdefault(OBJECT_NAME, MAT_NAME)
        for cat in CATEGORY_COLOR:         # every category maps to the one shared material
            config.MATERIAL_ALIAS.setdefault(
                f"{OBJECT_NAME}_{cat}_{CATEGORY_NAME.get(cat, 'other')}", MAT_NAME)
    except Exception:
        pass
    # append the trees into each patch OBJ after export (file mode / Batch)
    try:
        _patch_obj_exporter()
    except Exception:
        pass


def unregister():
    try:
        if bpy.app.timers.is_registered(_rebuild_timer):
            bpy.app.timers.unregister(_rebuild_timer)
    except Exception:
        pass
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
        del bpy.types.Scene.condor_tree_row_batch
    except Exception:
        pass
    try:
        from .. import config
        config.TEXTURE_MAP.pop(MAT_NAME, None)
        config.MATERIAL_ALIAS.pop(OBJECT_NAME, None)
        for cat in CATEGORY_COLOR:
            config.MATERIAL_ALIAS.pop(
                f"{OBJECT_NAME}_{cat}_{CATEGORY_NAME.get(cat, 'other')}", None)
    except Exception:
        pass