"""
Condor Buildings Generator - local OSM source (.osm.pbf)

With the PBF checkbox in the panel ticked, every piece of OSM data the plugin
needs (map_<patch>.osm and the airport search) is cut out of a local .osm.pbf
extract - e.g. a Geofabrik country file - instead of being downloaded from the
Overpass servers, which are often overloaded. No server is contacted at all
while the checkbox is on (osm_downloader asks pbf_enabled() first).

The PBF has no spatial index, so the whole country file is read only ONCE, by
the "Split into Patches" button: one pass writes a small <patch>.osm.pbf for
every patch of the landscape into a folder next to the PBF
(<pbf folder>/<pbf name>/<landscape>/, manifest.json written last). Generate then
reads just the patch's small file (airports: the patch files around the search
area), which takes seconds. Without a finished split Generate reports
"PBF is not split yet" and no server is asked either. Reading uses pyosmium,
which is installed automatically (once) into Blender's user modules folder when
PBF is ticked.

The split keeps EVERY tagged object, cut only by place, so a new feature of the
plugin finds its objects without splitting again. The objects written into
map_<patch>.osm are the ones the Overpass query asks for: the query text from
osm_downloader.build_overpass_query (including the lines the feature modules wrap
into it at runtime) is parsed and its tag filters are applied to the patch file,
with the same "(._;>;)" recursion - every selected way gets its nodes, every
selected relation its member ways and their nodes. (An older split made with the
filters already applied still works the same way.)

The split core (split_pbf) does not need bpy, so it can be run and timed outside
Blender.

Removable: delete this file and the PBF checkbox simply does nothing (servers).
"""

import os
import re
import sys
import math
import json
import time
import shutil
import struct
import importlib
import subprocess
import logging
from array import array
from xml.sax.saxutils import quoteattr

try:
    import bpy
except ImportError:      # outside Blender (standalone split / timing test)
    bpy = None

logger = logging.getLogger(__name__)

# Package root ("condor_buildings"), the key of the add-on preferences.
ADDON_PACKAGE = __package__.split('.')[0]

# Location.x / .y of a node whose coordinates are unknown (libosmium undefined).
_UNDEF = 2147483647

_MEMBER_TYPES = {'n': "node", 'w': "way", 'r': "relation"}

_installing = False


# ---------------------------------------------------------------------------
# Settings (Add-on Preferences: use_pbf + pbf_path)
# ---------------------------------------------------------------------------

def _prefs():
    try:
        import bpy
        return bpy.context.preferences.addons[ADDON_PACKAGE].preferences
    except Exception:
        return None


def pbf_enabled():
    """True when the PBF checkbox is ticked. False outside Blender."""
    prefs = _prefs()
    return bool(prefs is not None and getattr(prefs, "use_pbf", False))


PATH_SEP = "|"      # several chosen PBF files are stored in pbf_path joined by this


def pbf_paths():
    """Every chosen .osm.pbf file as an absolute path ([] when none is set). Several
    files (e.g. the countries / regions of one scenery) are one split together."""
    prefs = _prefs()
    value = getattr(prefs, "pbf_path", "") if prefs is not None else ""
    out = []
    for path in value.split(PATH_SEP):
        path = path.strip()
        if not path:
            continue
        try:
            import bpy
            path = bpy.path.abspath(path)
        except Exception:
            pass
        out.append(os.path.normpath(path))
    return out


def pbf_path():
    """The first chosen .osm.pbf file as an absolute path ('' when none is set)."""
    paths = pbf_paths()
    return paths[0] if paths else ""


# ---------------------------------------------------------------------------
# pyosmium: availability + one-time automatic install
# ---------------------------------------------------------------------------

def _modules_dir():
    """Blender's per-user 'scripts/modules' folder - on sys.path, no admin rights."""
    import bpy
    return bpy.utils.user_resource('SCRIPTS', path="modules", create=True)


def _ensure_modules_on_path():
    try:
        target = _modules_dir()
    except Exception:
        return None
    if target and target not in sys.path:
        sys.path.append(target)
    return target


def osmium_ok():
    """True when pyosmium 4+ (FileProcessor + filters) can be imported."""
    _ensure_modules_on_path()
    try:
        osmium = importlib.import_module("osmium")
        importlib.import_module("osmium.filter")
        return hasattr(osmium, "FileProcessor")
    except Exception:
        return False


def _tail(result, lines=3):
    text = (result.stderr or result.stdout or "").strip().splitlines()
    return " | ".join(text[-lines:]) or f"exit code {result.returncode}"


def install_osmium():
    """Install pyosmium (PyPI package 'osmium') into Blender's user modules folder.
    Returns (ok, message)."""
    try:
        target = _ensure_modules_on_path()
        if not target:
            return False, "cannot find Blender's user modules folder"
        os.makedirs(target, exist_ok=True)
        py = sys.executable
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def run(args, timeout):
            return subprocess.run([py] + args, capture_output=True, text=True,
                                  timeout=timeout, creationflags=flags)

        print("[PBF] pyosmium not found - installing it now (once, needs internet) ...")
        if run(["-m", "pip", "--version"], 60).returncode != 0:
            print("[PBF] pip is missing - running ensurepip")
            if run(["-m", "ensurepip"], 300).returncode != 0:
                result = run(["-m", "ensurepip", "--user"], 300)
                if result.returncode != 0:
                    return False, "pip is not available: " + _tail(result)
        # --no-deps: the reader needs nothing else, and Blender's own packages must
        # not get shadowed by other versions in the modules folder.
        result = run(["-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                      "--no-deps", "--upgrade", "--target", target, "osmium"], 600)
        if result.returncode != 0:
            return False, "pip install osmium failed: " + _tail(result)
        importlib.invalidate_caches()
        if not osmium_ok():
            return False, ("pyosmium was installed into " + target + " but cannot be "
                           "imported - restart Blender and tick PBF again")
        print(f"[PBF] pyosmium installed into {target}")
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "installing pyosmium timed out"
    except Exception as e:
        return False, f"installing pyosmium failed: {e}"


def _save_prefs():
    try:
        import bpy
        bpy.ops.wm.save_userpref()
    except Exception as e:
        print(f"[PBF] could not save the preferences: {e}")
    return None


def save_preferences_soon():
    """Store the preferences (checkbox + file) right away, so they survive a restart
    even with Blender's 'Auto-Save Preferences' switched off. Run from a timer - a
    property update callback is not a safe place to call an operator."""
    try:
        import bpy
        if not bpy.app.timers.is_registered(_save_prefs):
            bpy.app.timers.register(_save_prefs, first_interval=0.5)
    except Exception:
        _save_prefs()


def _report_error(context, message):
    print(f"[PBF] ERROR: {message}")
    try:
        def draw(menu, _ctx):
            for line in message.split("\n"):
                menu.layout.label(text=line)
        context.window_manager.popup_menu(draw, title="PBF", icon='ERROR')
    except Exception:
        pass


def on_settings_changed(prefs, context, path_only=False):
    """Update callback of the PBF checkbox / file: on the first tick install
    pyosmium; if that fails, untick (= servers). Then store the preferences."""
    global _installing
    if not path_only and prefs.use_pbf and not _installing and not osmium_ok():
        _installing = True
        try:
            ok, message = install_osmium()
        finally:
            _installing = False
        if not ok:
            _report_error(context, message +
                          "\nPBF switched off - OSM data comes from the servers.")
            prefs.use_pbf = False      # runs this callback again, which stores it
            return
    save_preferences_soon()


def _check_ready():
    """None when the PBF can be read, else the error text."""
    paths = pbf_paths()
    if not paths:
        return "PBF is ticked but no .pbf file is chosen"
    for path in paths:
        if not os.path.isfile(path):
            return f"PBF file not found: {path}"
    if not osmium_ok():
        return ("pyosmium is not available - untick and tick PBF again to install it "
                "(or untick PBF to use the servers)")
    return None


# ---------------------------------------------------------------------------
# Overpass tag filters -> Python
# ---------------------------------------------------------------------------

_STMT_RE = re.compile(r'^\s*(node|way|relation)((?:\[[^\]]*\])+)\(', re.M)
_COND_RE = re.compile(r'\["([^"]+)"(?:(=|~)"([^"]*)")?\]')


def _parse_query(query):
    """Overpass QL statements -> {'node'|'way'|'relation': [conditions, ...]}, one
    list of (key, op, value) per statement; op None = the key must exist,
    '=' = exact value, '~' = regular expression (searched, like Overpass)."""
    result = {'node': [], 'way': [], 'relation': []}
    for m in _STMT_RE.finditer(query):
        conds = []
        for key, op, value in _COND_RE.findall(m.group(2)):
            if op == '~':
                conds.append((key, '~', re.compile(value)))
            elif op == '=':
                conds.append((key, '=', value))
            else:
                conds.append((key, None, None))
        if not conds or len(conds) != m.group(2).count('['):
            print(f"[PBF] WARNING: filter not understood, skipped: {m.group(0).strip()}")
            continue
        result[m.group(1)].append(conds)
    return result


def _filter_sets():
    """'main' = the patch query (with every runtime wrapper), 'air' = airport query."""
    from . import osm_downloader as _osm
    return {
        'main': _parse_query(_osm.build_overpass_query(0.0, 0.1, 0.0, 0.1)),
        'air': _parse_query(_osm.build_aeroway_query(0.0, 0.1, 0.0, 0.1)),
    }


def _match(tags, statements):
    for conds in statements:
        for key, op, value in conds:
            v = tags.get(key)
            if v is None or (op == '=' and v != value) or \
               (op == '~' and not value.search(v)):
                break
        else:
            return True
    return False


# ---------------------------------------------------------------------------
# Geometry (coordinates as libosmium fixed-point integers, degrees * 1e7)
# ---------------------------------------------------------------------------

def _seg_hits_box(x1, y1, x2, y2, box):
    """Liang-Barsky: does the segment touch the box (xmin, ymin, xmax, ymax)?"""
    xmin, ymin, xmax, ymax = box
    if max(x1, x2) < xmin or min(x1, x2) > xmax or \
       max(y1, y2) < ymin or min(y1, y2) > ymax:
        return False
    dx = x2 - x1
    dy = y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - xmin), (dx, xmax - x1), (-dy, y1 - ymin), (dy, ymax - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return False
                if t > t0:
                    t0 = t
            else:
                if t < t0:
                    return False
                if t < t1:
                    t1 = t
    return True


def _way_hits(xs, ys, bb, box):
    """Overpass 'way in bbox': a node inside the box, or a segment crossing it."""
    xmin, ymin, xmax, ymax = box
    if bb[2] < xmin or bb[0] > xmax or bb[3] < ymin or bb[1] > ymax:
        return False
    for x, y in zip(xs, ys):
        if xmin <= x <= xmax and ymin <= y <= ymax:
            return True
    for i in range(1, len(xs)):
        if _seg_hits_box(xs[i - 1], ys[i - 1], xs[i], ys[i], box):
            return True
    return False


def _box_fixed(lat_min, lat_max, lon_min, lon_max):
    """(xmin, ymin, xmax, ymax) in fixed-point degrees * 1e7."""
    return (int(round(lon_min * 1e7)), int(round(lat_min * 1e7)),
            int(round(lon_max * 1e7)), int(round(lat_max * 1e7)))


# ---------------------------------------------------------------------------
# Split into Patches: ONE pass over the PBF -> one small .osm.pbf per patch
# ---------------------------------------------------------------------------

NOT_SPLIT = "PBF is not split yet - press Split into Patches"

_MANIFEST = "manifest.json"
_HTXT_RE = re.compile(r'^[hH](\d{6})\.txt$')
_PATCH_PBF_RE = re.compile(r'^\d{6}\.osm\.pbf$')

_CELL = 500000              # patch lookup grid: 0.05 deg cells (fixed-point)
_NEAR_CELL = 2000000        # coarse "near the scenery" cells: 0.2 deg
_NEAR_M = 20000.0           # a way starting farther than this from every patch is skipped
_PROGRESS_SEC = 5.0

# Records of the temporary per-patch data (all little-endian).
ALL_TAGS = True             # the split keeps EVERY tagged object (see split_pbf)

_NODE_REC = struct.Struct('<BqiiI')     # 1, id, x, y, tag bytes
_WAY_REC = struct.Struct('<BqII')       # 2 tagged / 3 skeleton, id, node count, tag bytes
_REL_REC = struct.Struct('<BqII')       # 4, id, member bytes, tag bytes


def _pbf_stem(pbf):
    """'czech-republic-260930.osm.pbf' -> 'czech-republic-260930'."""
    name = os.path.basename(pbf)
    for ext in (".osm.pbf", ".pbf"):
        if name.lower().endswith(ext):
            return name[:-len(ext)]
    return os.path.splitext(name)[0]


def split_dir(pbf, landscape):
    """Folder of the split: '<landscape> PBF OSM patch' next to the (first) PBF file.
    pbf is one path or the list of all chosen files."""
    first = pbf if isinstance(pbf, str) else pbf[0]
    return os.path.join(os.path.dirname(first), f"{landscape} PBF OSM patch")


def _pbf_stamps(pbfs):
    """[[file name, size, mtime], ...] of the chosen PBF files, sorted - stored in the
    manifest, so a changed or different set of files is noticed."""
    out = []
    for p in pbfs:
        st = os.stat(p)
        out.append([os.path.basename(p), st.st_size, int(st.st_mtime)])
    return sorted(out)


def _landscape_patches(heightmaps_dir):
    """{patch id: (lat_min, lat_max, lon_min, lon_max)} of every h<patch>.txt."""
    from ..io.patch_metadata import load_patch_metadata
    result = {}
    for name in sorted(os.listdir(heightmaps_dir)):
        m = _HTXT_RE.match(name)
        if not m or m.group(1) in result:
            continue
        try:
            meta = load_patch_metadata(os.path.join(heightmaps_dir, name))
        except Exception as e:
            print(f"[PBF] WARNING: {name} skipped ({e})")
            continue
        result[m.group(1)] = (meta.lat_min, meta.lat_max, meta.lon_min, meta.lon_max)
    return result


def _index_statements(statements):
    """Statements grouped by the key of their first condition."""
    index = {}
    for conds in statements:
        index.setdefault(conds[0][0], []).append(conds)
    return index


def _match_keyed(tags, index):
    """_match, but only the statements whose first key the object has are tried."""
    for k in tags:
        group = index.get(k)
        if group and _match(tags, group):
            return True
    return False


def _is_long(tags):
    """Long linear objects (power lines, aerialways, water courses): a way can cross
    the scenery far away from its first node, so all its nodes are always checked."""
    return tags.get('power') in ('line', 'minor_line') or 'aerialway' in tags or \
        'waterway' in tags


def _tags_to_bytes(tags):
    if not tags:
        return b""
    return "\0".join(f"{k}\0{v}" for k, v in tags.items()).encode("utf-8")


def _tags_from_bytes(data):
    if not data:
        return {}
    parts = bytes(data).decode("utf-8").split("\0")
    return dict(zip(parts[0::2], parts[1::2]))


def _way_rec(kind, wid, refs, xs, ys, tags):
    tb = _tags_to_bytes(tags)
    return b"".join((_WAY_REC.pack(kind, wid, len(refs), len(tb)),
                     array('q', refs).tobytes(), array('i', xs).tobytes(),
                     array('i', ys).tobytes(), tb))


def _decode_records(data):
    """Temporary patch data -> (nodes {id: (x, y, tags)}, ways {id: (refs, tags)},
    rels {id: (members, tags)}, coords {node id: (x, y)}). A tagged (selected) way
    wins over the same way added untagged as a relation member."""
    nodes, ways, rels, coords = {}, {}, {}, {}
    pos, end = 0, len(data)
    while pos < end:
        kind = data[pos]
        if kind == 1:
            _k, nid, x, y, tl = _NODE_REC.unpack_from(data, pos)
            pos += _NODE_REC.size
            nodes[nid] = (x, y, _tags_from_bytes(data[pos:pos + tl]))
            pos += tl
        elif kind in (2, 3):
            _k, wid, n, tl = _WAY_REC.unpack_from(data, pos)
            pos += _WAY_REC.size
            refs = array('q')
            refs.frombytes(data[pos:pos + 8 * n])
            pos += 8 * n
            xs = array('i')
            xs.frombytes(data[pos:pos + 4 * n])
            pos += 4 * n
            ys = array('i')
            ys.frombytes(data[pos:pos + 4 * n])
            pos += 4 * n
            tags = _tags_from_bytes(data[pos:pos + tl])
            pos += tl
            for ref, x, y in zip(refs, xs, ys):
                coords[ref] = (x, y)
            old = ways.get(wid)
            if old is None:
                ways[wid] = (refs.tolist(), tags)
            else:
                # The same way twice: tagged + relation member, or from two PBF files
                # that overlap at their borders (cut short in one of them). The longer
                # node list is kept, and the tags of the tagged copy.
                keep = refs.tolist() if len(refs) > len(old[0]) else old[0]
                ways[wid] = (keep, tags if kind == 2 else old[1])
        elif kind == 4:
            _k, rid, ml, tl = _REL_REC.unpack_from(data, pos)
            pos += _REL_REC.size
            members = [tuple(m) for m in json.loads(bytes(data[pos:pos + ml]).decode("utf-8"))]
            pos += ml
            rels[rid] = (members, _tags_from_bytes(data[pos:pos + tl]))
            pos += tl
        else:
            raise ValueError(f"damaged temporary data (record type {kind})")
    return nodes, ways, rels, coords


def _write_patch_pbf(path, data, pool):
    """Temporary patch data -> <patch>.osm.pbf (nodes, ways, relations, sorted).
    Selected objects keep their tags, the recursed ones are written without."""
    import osmium
    from osmium.osm import mutable
    nodes, ways, rels, coords = _decode_records(data)
    with osmium.SimpleWriter(path, overwrite=True, thread_pool=pool) as w:
        for nid in sorted(coords.keys() | nodes.keys()):
            nd = nodes.get(nid)
            if nd is not None:
                w.add_node(mutable.Node(id=nid, location=(nd[0] / 1e7, nd[1] / 1e7),
                                        tags=nd[2]))
            else:
                x, y = coords[nid]
                w.add_node(mutable.Node(id=nid, location=(x / 1e7, y / 1e7), tags={}))
        for wid in sorted(ways):
            refs, tags = ways[wid]
            w.add_way(mutable.Way(id=wid, nodes=refs, tags=tags))
        for rid in sorted(rels):
            members, tags = rels[rid]
            w.add_relation(mutable.Relation(id=rid, members=members, tags=tags))


class _PatchGrid:
    """Coarse lookup grid over all patch boxes, so a way is only tested against the
    few patches around it, not against thousands."""

    def __init__(self, patches):
        self.ids = sorted(patches)
        self.boxes = []
        self.cells = {}
        self.near = set()
        mlat = _NEAR_M / 111320.0
        for i, pid in enumerate(self.ids):
            lat_min, lat_max, lon_min, lon_max = patches[pid]
            box = _box_fixed(lat_min, lat_max, lon_min, lon_max)
            self.boxes.append(box)
            for cx in range(box[0] // _CELL, box[2] // _CELL + 1):
                for cy in range(box[1] // _CELL, box[3] // _CELL + 1):
                    self.cells.setdefault((cx, cy), []).append(i)
            clat = math.radians((lat_min + lat_max) / 2.0)
            mlon = _NEAR_M / (111320.0 * max(0.1, math.cos(clat)))
            near = _box_fixed(lat_min - mlat, lat_max + mlat, lon_min - mlon, lon_max + mlon)
            for cx in range(near[0] // _NEAR_CELL, near[2] // _NEAR_CELL + 1):
                for cy in range(near[1] // _NEAR_CELL, near[3] // _NEAR_CELL + 1):
                    self.near.add((cx, cy))

    def is_near(self, x, y):
        """Is the point within ~20 km (or a bit more) of some patch?"""
        return (x // _NEAR_CELL, y // _NEAR_CELL) in self.near

    def at_point(self, x, y):
        out = []
        for i in self.cells.get((x // _CELL, y // _CELL), ()):
            b = self.boxes[i]
            if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                out.append(i)
        return out

    def candidates(self, xs, ys, bb, cand):
        """Add the patches whose cells the way's bbox (a long way: its segments) touches."""
        cells = self.cells
        cx0, cy0, cx1, cy1 = bb[0] // _CELL, bb[1] // _CELL, bb[2] // _CELL, bb[3] // _CELL
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) <= 4:
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    group = cells.get((cx, cy))
                    if group:
                        cand.update(group)
            return cand
        px, py = xs[0], ys[0]
        for x, y in zip(xs, ys):
            for cx in range(min(px, x) // _CELL, max(px, x) // _CELL + 1):
                for cy in range(min(py, y) // _CELL, max(py, y) // _CELL + 1):
                    group = cells.get((cx, cy))
                    if group:
                        cand.update(group)
            px, py = x, y
        return cand

    def hits_way(self, xs, ys, bb):
        boxes = self.boxes
        return [i for i in self.candidates(xs, ys, bb, set())
                if _way_hits(xs, ys, bb, boxes[i])]


class _Progress:
    def __init__(self, start):
        self.start = start
        self.last = start

    def due(self):
        now = time.time()
        if now - self.last >= _PROGRESS_SEC:
            self.last = now
            return True
        return False

    def say(self, text):
        print(f"[PBF] split: {text} - {time.time() - self.start:.0f}s", flush=True)


def _prepare_out_dir(out_dir):
    """Make the folder ready for a new split: the old manifest goes FIRST (so an
    interrupted split is never taken as complete), then the old patch files.
    Returns the temporary folder."""
    os.makedirs(out_dir, exist_ok=True)
    manifest = os.path.join(out_dir, _MANIFEST)
    if os.path.exists(manifest):
        os.remove(manifest)
    for name in os.listdir(out_dir):
        if _PATCH_PBF_RE.match(name):
            os.remove(os.path.join(out_dir, name))
    tmp = os.path.join(out_dir, "_split_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    return tmp


def _fsets_to_json(fsets):
    """_filter_sets() -> plain lists (regular expressions as their text), so the
    filters can be handed over to the worker processes in a JSON file."""
    return {name: {o: [[[k, op, v.pattern if op == '~' else v] for k, op, v in conds]
                       for conds in stmts]
                   for o, stmts in sets.items()}
            for name, sets in fsets.items()}


def _fsets_from_json(data):
    return {name: {o: [[(k, op, re.compile(v) if op == '~' else v) for k, op, v in conds]
                       for conds in stmts]
                   for o, stmts in sets.items()}
            for name, sets in data.items()}


_WORKER = "pbf_split_worker.py"
_GB = 1024.0 ** 3
_KEEP_FREE = 8 * _GB        # RAM always left free for the other programs (Edge, ...)
_KEEP_FREE_LOW = 4 * _GB    # left free instead on a computer with 16 GB of RAM or less
_RAM_BASE = 0.25 * _GB      # estimated RAM of one worker: base + 3x the size of its
_RAM_PER_BYTE = 3.0         # PBF file (location cache of all nodes of the file)
_WRITER_RAM = 0.5 * _GB     # estimated RAM of one process writing patch files
_BIG_FILE = 300 * 1024 * 1024   # a PBF this big is read in parts at the same time


def _free_ram(total=False):
    """Free (total=True: all) physical memory in bytes, None when unknown."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MemoryStatus()
            ms.dwLength = ctypes.sizeof(ms)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return int(ms.ullTotalPhys if total else ms.ullAvailPhys)
            return None
        pages = "SC_PHYS_PAGES" if total else "SC_AVPHYS_PAGES"
        return os.sysconf(pages) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return None


def _python_exe():
    """Blender's own Python. sys.executable is it in current builds; older ones point at
    blender.exe, hence the fallback next to sys.prefix."""
    exe = sys.executable
    if exe and os.path.basename(exe).lower().startswith("python"):
        return exe
    name = "python.exe" if sys.platform == "win32" else "python"
    cand = os.path.join(sys.prefix, "bin", name)
    return cand if os.path.exists(cand) else exe


def _low_priority():
    """Popen arguments: no window, lower priority - the computer stays usable."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) |
                getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"preexec_fn": lambda: os.nice(5)}


def _proc_ram(proc):
    """RAM (working set) of a running worker in bytes, 0 when unknown."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        c = _Counters()
        c.cb = ctypes.sizeof(c)
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                               wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(int(proc._handle), ctypes.byref(c), c.cb):
            return int(c.WorkingSetSize)
    except Exception:
        pass
    return 0


_JOB = None     # Windows job object of the workers (closed together with Blender)


def _end_with_blender(proc):
    """Windows: put a worker into a job that Windows closes when Blender ends (also
    when Blender is closed or killed during the split) - the job ends the workers too,
    so none of them runs on and keeps the temporary files open."""
    global _JOB
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if _JOB is None:
            class _Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basic),
                            ("IoInfo", ctypes.c_uint64 * 6),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000     # KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            _JOB = job
        k32.AssignProcessToJobObject(_JOB, int(proc._handle))
    except Exception:
        pass


def _worker_env():
    """Environment of a worker: pyosmium and this add-on must be importable."""
    env = dict(os.environ)
    extra = []
    try:
        import osmium
        extra.append(os.path.dirname(os.path.dirname(os.path.abspath(osmium.__file__))))
    except Exception:
        pass
    modules = _ensure_modules_on_path() if bpy is not None else None
    if modules:
        extra.append(modules)
    env["PYTHONPATH"] = os.pathsep.join(p for p in extra + [env.get("PYTHONPATH", "")] if p)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _short_name(unit):
    """Console name of a unit: the PBF name without its date, e.g. 'czech-republic'."""
    name = re.sub(r"-\d{6}$", "", _pbf_stem(unit["file"]))
    return f"{name} relations" if unit["kind"] == "rels" else name


def _unit_name(unit):
    stem = _pbf_stem(unit["file"])
    if unit["kind"] == "rels":
        return f"{stem} relations"
    return f"{stem} part {unit['part'] + 1}/{unit['parts']}"


def _log_tail(path, lines=5):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip().splitlines()
        return " | ".join(text[-lines:])
    except Exception:
        return ""


def split_pbf(pbf, heightmaps_dir, out_dir, fsets, landscape=""):
    """Write <out_dir>/<patch>.osm.pbf for every h<patch>.txt in heightmaps_dir and
    manifest.json last. fsets = _filter_sets(). Needs no bpy. Raises on failure.
    Returns (number of patches, seconds).

    The work is cut into units (pbf_split_worker.py) that run at the same time, each
    in its own process with a lower priority: every PBF file is read in several
    parts (part k takes the nodes and ways with id % n == k) plus one unit for its
    relations (they come LAST in a PBF, so their member ways are let through by id
    in a separate read). How many run at once depends on the free RAM: at most half
    of it, and always at least 8 GB left free; at most (cores - 2), at least one.
    The patch files are then written by several processes as well."""
    # One file or several (e.g. the countries / regions of the scenery): all of them
    # go into the same patch files; an object found in two overlapping files is
    # written once (_decode_records keeps one copy per id).
    pbfs = [pbf] if isinstance(pbf, str) else list(pbf)
    start = time.time()
    print(f"[PBF] Split started: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    patches = _landscape_patches(heightmaps_dir)
    if not patches:
        raise RuntimeError(f"no h<patch>.txt files found in {heightmaps_dir}")
    ids = sorted(patches)
    # Every tagged object goes into the patch files (only by its place), so a new
    # feature of the plugin finds its objects without splitting again; what is
    # needed is picked when map_<patch>.osm is written (ensure_patch_map).
    rel_keys = ALL_TAGS or any(fsets[s]['relation'] for s in ('main', 'air'))
    progress = _Progress(start)
    print(f"[PBF] split: {len(pbfs)} PBF file(s) -> {out_dir} ({len(patches)} patches)",
          flush=True)
    tmp = _prepare_out_dir(out_dir)

    # How many workers: free RAM (half of it, 8 GB always left free) and CPU cores.
    sizes = {f: os.path.getsize(f) for f in pbfs}

    def ram(f):
        return _RAM_BASE + _RAM_PER_BYTE * sizes[f]

    max_workers = max(1, (os.cpu_count() or 1) - 2)
    free = _free_ram()
    total = _free_ram(total=True)
    keep = _KEEP_FREE if total is None or total > 16 * _GB else _KEEP_FREE_LOW
    budget = None if free is None else max(0.0, free - keep)
    workers = max_workers
    if budget is not None:
        workers = max(1, min(max_workers, int(budget // ram(min(pbfs, key=ram)))))
    if free is None:
        print(f"[PBF] split: {workers} workers (free RAM unknown)", flush=True)
    else:
        print(f"[PBF] split: {workers} workers ({free / _GB:.1f} GB free, "
              f"using max {budget / _GB:.1f} GB)", flush=True)

    # Units: a big file (300 MB and more) is read in as many parts as fit into the
    # RAM at the same time (each part holds all node locations of the file), so it
    # does not run alone in one process at the end; a small file is read once.
    units = []
    for f in pbfs:
        n = 1
        if budget is not None and sizes[f] >= _BIG_FILE:
            n = max(1, min(max_workers, int(budget // ram(f))))
        for k in range(n):
            units.append({"kind": "ways", "file": f, "part": k, "parts": n})
        if rel_keys:
            units.append({"kind": "rels", "file": f})
    job = {
        "tmp": tmp,
        "out_dir": out_dir,
        "patches": {pid: list(patches[pid]) for pid in ids},
        "patch_ids": ids,
        "fsets": _fsets_to_json(fsets),
        "all_tags": ALL_TAGS,
        "units": units,
    }
    job_path = os.path.join(tmp, "job.json")
    with open(job_path, "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), _WORKER)
    env = _worker_env()

    def launch(args, log_name):
        log = open(os.path.join(tmp, log_name), "wb")
        proc = subprocess.Popen([_python_exe(), script] + args, env=env,
                                stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **_low_priority())
        _end_with_blender(proc)
        return proc, log

    # Biggest work first, so the long units do not end up last.
    def weight(i):
        u = units[i]
        if u["kind"] == "rels":
            return 0.3 * sizes[u["file"]]
        return sizes[u["file"]] * (1.0 / u["parts"] + 0.3)

    pending = sorted(range(len(units)), key=weight, reverse=True)
    running = {}        # unit index -> (process, log file, RAM estimate)
    started = {}        # unit index -> start time
    peak = 0
    written = 0
    try:
        done = 0
        while pending or running:
            while pending and len(running) < workers:
                # The first waiting unit that fits into the free RAM (a big one that
                # does not fit yet must not hold up the smaller ones behind it).
                # Measured now: the free RAM, minus what the workers that are still
                # starting (reading the node locations, the first ~90 s) will take.
                free_now = _free_ram()
                if free_now is None:
                    used = sum(r[2] for r in running.values())
                    fits = [i for i in pending if not running or budget is None
                            or used + ram(units[i]["file"]) <= budget]
                else:
                    now = time.time()
                    room = free_now - keep - sum(max(0.0, r[2] - _proc_ram(r[0]))
                                                 for j, r in running.items()
                                                 if now - started[j] < 90)
                    fits = [i for i in pending
                            if not running or ram(units[i]["file"]) <= room]
                if not fits:
                    break
                i = fits[0]
                pending.remove(i)
                proc, log = launch(["unit", job_path, str(i)], f"u{i}.log")
                running[i] = (proc, log, ram(units[i]["file"]))
                started[i] = time.time()
                print(f"[PBF] start: {_short_name(units[i])}", flush=True)
            time.sleep(0.5)
            for i in list(running):
                proc, log, _est = running[i]
                rc = proc.poll()
                if rc is None:
                    continue
                log.close()
                del running[i]
                result = os.path.join(tmp, f"u{i}.json")
                if rc != 0 or not os.path.exists(result):
                    raise RuntimeError(f"{_unit_name(units[i])} failed (code {rc}): "
                                       f"{_log_tail(os.path.join(tmp, f'u{i}.log'))}")
                with open(result, encoding="utf-8") as fh:
                    st = json.load(fh)["stats"]
                done += 1
                peak = max(peak, st.get("peak_ram", 0))
                sec = st.get("seconds", 0)
                print(f"[PBF] done {done}/{len(units)}: {_short_name(units[i])} "
                      f"({int(sec // 60)} min {int(sec % 60)} s, "
                      f"{st.get('peak_ram', 0) / _GB:.1f} GB)", flush=True)
            if running and progress.due():
                names = ", ".join(_short_name(units[i]) for i in sorted(running))
                progress.say(f"{done}/{len(units)} done, running: {names}")

        # One small .osm.pbf per patch, written by several processes.
        print("[PBF] split: writing the patch files ...", flush=True)
        writers = max_workers
        if budget is not None:
            writers = max(1, min(max_workers, int(budget // _WRITER_RAM)))
        for w in range(writers):
            proc, log = launch(["write", job_path, str(w), str(writers)], f"w{w}.log")
            running[w] = (proc, log, 0)
        while running:
            time.sleep(0.5)
            for w in list(running):
                proc, log, _est = running[w]
                rc = proc.poll()
                if rc is None:
                    continue
                log.close()
                del running[w]
                result = os.path.join(tmp, f"w{w}.json")
                if rc != 0 or not os.path.exists(result):
                    raise RuntimeError(f"writing the patch files failed (code {rc}): "
                                       f"{_log_tail(os.path.join(tmp, f'w{w}.log'))}")
                with open(result, encoding="utf-8") as fh:
                    st = json.load(fh)
                written += st.get("written", 0)
                peak = max(peak, st.get("peak_ram", 0))
            if running and progress.due():
                progress.say(f"writing the patch files, {written} written so far")
    finally:
        for proc, log, _est in running.values():
            try:
                proc.kill()
                proc.wait(10)
            except Exception:
                pass
            log.close()
        shutil.rmtree(tmp, ignore_errors=True)

    size = sum(os.path.getsize(os.path.join(out_dir, f"{pid}.osm.pbf"))
               for pid in ids
               if os.path.exists(os.path.join(out_dir, f"{pid}.osm.pbf")))
    sec = time.time() - start
    manifest = {
        "pbf_files": _pbf_stamps(pbfs),
        "landscape": landscape,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(sec, 1),
        "files_with_data": written,
        "total_bytes": size,
        "patches": ids,
        "bounds": {pid: list(patches[pid]) for pid in ids},
        "all_tags": ALL_TAGS,
    }
    # Written LAST: without it the split counts as not done.
    path = os.path.join(out_dir, _MANIFEST)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    os.replace(path + ".tmp", path)
    print(f"[PBF] split: {written} patch files with data, {size / 1048576:.1f} MB, "
          f"max RAM of one worker {peak / _GB:.1f} GB", flush=True)
    print(f"[PBF] split done: {len(ids)} patches in {sec:.1f}s -> {out_dir}", flush=True)
    print(f"[PBF] Split finished: {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"(took {int(sec // 60)} min {int(sec % 60)} s)", flush=True)
    return len(ids), sec


# ---------------------------------------------------------------------------
# Generate: map_<patch>.osm from the split
# ---------------------------------------------------------------------------

def _scene_landscape():
    try:
        name = bpy.context.scene.condor_buildings.landscape_name
        return "" if not name or name == 'NONE' else name
    except Exception:
        return ""


def _load_split(autogen_dir=None):
    """(folder, manifest) of the finished split of the chosen PBF for this landscape,
    or (None, error text)."""
    pbfs = pbf_paths()
    if not pbfs:
        return None, "PBF is ticked but no .pbf file is chosen"
    names = []
    if autogen_dir:
        # .../Landscapes/<landscape>/Working/Autogen
        names.append(os.path.basename(os.path.dirname(os.path.dirname(
            os.path.normpath(os.path.abspath(autogen_dir))))))
    name = _scene_landscape()
    if name and name not in names:
        names.append(name)
    try:
        stamps = _pbf_stamps(pbfs)
    except OSError as e:
        return None, f"PBF file not found: {e.filename}"
    for name in names:
        folder = split_dir(pbfs, name)
        try:
            with open(os.path.join(folder, _MANIFEST), encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception:
            continue
        if manifest.get("pbf_files") != stamps:
            return None, ("the PBF files changed since they were split - "
                          "press Split into Patches again")
        return folder, manifest
    where = split_dir(pbfs, names[0]) if names else os.path.dirname(pbfs[0])
    return None, f"{NOT_SPLIT} ({where})"


def _read_patch_files(folder, patch_ids):
    """Union of the patch files: nodes {id: (x, y, tags|None)}, ways {id: (refs,
    tags|None)}, rels {id: (members, tags)}. Tagged = selected, untagged = recursed."""
    import osmium
    nodes, ways, rels = {}, {}, {}
    for pid in patch_ids:
        path = os.path.join(folder, f"{pid}.osm.pbf")
        if not os.path.isfile(path):
            continue        # patch without data
        for obj in osmium.FileProcessor(path):
            otype = obj.type_str()
            tags = {t.k: t.v for t in obj.tags} or None
            if otype == 'n':
                if tags or obj.id not in nodes:
                    loc = obj.location
                    nodes[obj.id] = (loc.x, loc.y, tags)
            elif otype == 'w':
                if tags or obj.id not in ways:
                    ways[obj.id] = ([n.ref for n in obj.nodes], tags)
            elif otype == 'r':
                rels[obj.id] = ([(m.type, m.ref, m.role) for m in obj.members], tags or {})
    return nodes, ways, rels


def _select_box(box, stmts, nodes, ways, rels):
    """Cut a box out of patch data with the given statements - the same rules as
    the split: node in box, way with a node in box or a segment crossing it,
    relation with a member in it; plus the recursed ways/nodes."""
    sel_nodes = {nid for nid, (x, y, tags) in nodes.items()
                 if tags and box[0] <= x <= box[2] and box[1] <= y <= box[3]
                 and _match(tags, stmts['node'])}

    def geometry(refs):
        xs = [nodes[r][0] for r in refs if r in nodes]
        ys = [nodes[r][1] for r in refs if r in nodes]
        return xs, ys, ((min(xs), min(ys), max(xs), max(ys)) if xs else None)

    sel_ways = set()
    for wid, (refs, tags) in ways.items():
        if tags and _match(tags, stmts['way']):
            xs, ys, bb = geometry(refs)
            if bb and _way_hits(xs, ys, bb, box):
                sel_ways.add(wid)
    sel_rels = set()
    for rid, (members, tags) in rels.items():
        if not _match(tags, stmts['relation']):
            continue
        for mt, ref, _r in members:
            if mt == 'w' and ref in ways:
                xs, ys, bb = geometry(ways[ref][0])
                if bb and _way_hits(xs, ys, bb, box):
                    sel_rels.add(rid)
                    break
            elif mt == 'n' and ref in sel_nodes:
                sel_rels.add(rid)
                break
    skel = {ref for rid in sel_rels for mt, ref, _r in rels[rid][0]
            if mt == 'w' and ref in ways and ref not in sel_ways}

    out_nodes, out_ways = {}, {}
    for wid in sel_ways | skel:
        refs, tags = ways[wid]
        out_ways[wid] = (refs, tags if wid in sel_ways else None)
        for ref in refs:
            if ref in nodes:
                out_nodes[ref] = (nodes[ref][0], nodes[ref][1], None)
    for nid in sel_nodes:
        out_nodes[nid] = nodes[nid]
    return out_nodes, out_ways, {rid: rels[rid] for rid in sel_rels}


def _patches_in_box(manifest, bounds):
    lat_min, lat_max, lon_min, lon_max = bounds
    return [pid for pid, b in manifest.get("bounds", {}).items()
            if not (b[1] < lat_min or b[0] > lat_max or b[3] < lon_min or b[2] > lon_max)]


def _tag_lines(tags):
    return [f'    <tag k={quoteattr(k)} v={quoteattr(v)}/>' for k, v in tags.items()]


def _osm_xml(bounds, nodes, ways, rels):
    """OSM XML 0.6 - selected objects with their tags ('out body'), the recursed
    ways/nodes without tags ('out skel'). nodes {id: (x, y, tags|None)}, ways
    {id: (refs, tags|None)}, rels {id: (members, tags)}."""
    lat_min, lat_max, lon_min, lon_max = bounds
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="Condor Buildings - local PBF">',
        f'  <bounds minlat="{lat_min:.7f}" minlon="{lon_min:.7f}" '
        f'maxlat="{lat_max:.7f}" maxlon="{lon_max:.7f}"/>',
    ]
    for nid in sorted(nodes):
        x, y, tags = nodes[nid]
        head = f'  <node id="{nid}" lat="{y / 1e7:.7f}" lon="{x / 1e7:.7f}"'
        if tags:
            lines.append(head + '>')
            lines.extend(_tag_lines(tags))
            lines.append('  </node>')
        else:
            lines.append(head + '/>')
    for wid in sorted(ways):
        refs, tags = ways[wid]
        lines.append(f'  <way id="{wid}">')
        lines.extend(f'    <nd ref="{ref}"/>' for ref in refs)
        if tags:
            lines.extend(_tag_lines(tags))
        lines.append('  </way>')
    for rid in sorted(rels):
        members, tags = rels[rid]
        lines.append(f'  <relation id="{rid}">')
        for mt, ref, role in members:
            lines.append(f'    <member type="{_MEMBER_TYPES.get(mt, mt)}" ref="{ref}" '
                         f'role={quoteattr(role)}/>')
        lines.extend(_tag_lines(tags))
        lines.append('  </relation>')
    lines.append('</osm>')
    return "\n".join(lines) + "\n"


def _write_map(path, text):
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".pbf_tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Entry points used by osm_downloader
# ---------------------------------------------------------------------------

def ensure_patch_map(patch_metadata, output_dir, filename_prefix="map"):
    """PBF counterpart of the Overpass patch download: map_<patch>.osm from the
    patch's small file of the split. Returns a DownloadResult."""
    from .osm_downloader import DownloadResult, mark_side_data_fetched
    pid = patch_metadata.patch_id
    out_path = os.path.join(output_dir, f"{filename_prefix}_{pid}.osm")
    start = time.time()
    error = _check_ready()
    if error is None:
        folder, manifest = _load_split(output_dir)
        if folder is None:
            error = manifest
        elif pid not in manifest.get("patches", []):
            error = f"patch {pid} is not in the split of this landscape - {NOT_SPLIT}"
        else:
            try:
                m = patch_metadata
                bounds = (m.lat_min, m.lat_max, m.lon_min, m.lon_max)
                data = _read_patch_files(folder, [pid])
                # The patch file holds everything; only what the plugin asks for
                # (the Overpass query, with every feature's lines) goes into the map.
                sel = _select_box(_box_fixed(*bounds), _filter_sets()['main'], *data)
                _write_map(out_path, _osm_xml(bounds, *sel))
                # Tree rows and fences are in the file already - same as the server path.
                mark_side_data_fetched(out_path)
                print(f"[PBF] patch {pid} taken from the split in "
                      f"{time.time() - start:.1f}s")
            except Exception as e:
                error = f"PBF: could not write {out_path}: {e}"
    if error is None and not os.path.exists(out_path):
        error = "PBF: no map file was written"
    if error:
        print(f"[PBF] ERROR patch {pid}: {error}")
        return DownloadResult(success=False, error=error)
    return DownloadResult(
        success=True,
        filepath=out_path,
        download_time_ms=int((time.time() - start) * 1000),
        file_size_bytes=os.path.getsize(out_path),
    )


def extract_bbox(lat_min, lat_max, lon_min, lon_max, output_path):
    """PBF counterpart of download_osm_data for an arbitrary box (one file), cut out
    of the patch files of the split that touch the box."""
    from .osm_downloader import DownloadResult, mark_side_data_fetched
    start = time.time()
    error = _check_ready()
    if error is None:
        folder, manifest = _load_split(os.path.dirname(output_path))
        bounds = (lat_min, lat_max, lon_min, lon_max)
        pids = _patches_in_box(manifest, bounds) if folder else []
        if folder is None:
            error = manifest
        elif not pids:
            error = "PBF: the box lies outside the patches of the split"
        else:
            try:
                data = _read_patch_files(folder, pids)
                sel = _select_box(_box_fixed(*bounds), _filter_sets()['main'], *data)
                _write_map(output_path, _osm_xml(bounds, *sel))
                mark_side_data_fetched(output_path)
            except Exception as e:
                error = f"PBF: could not write {output_path}: {e}"
    if error:
        print(f"[PBF] ERROR: {error}")
        return DownloadResult(success=False, error=error)
    return DownloadResult(
        success=True,
        filepath=output_path,
        download_time_ms=int((time.time() - start) * 1000),
        file_size_bytes=os.path.getsize(output_path),
    )


def airport_content(patch_metadata, autogen_dir, bbox):
    """OSM XML (bytes) of the aerodromes/runways in the 3x3 search area ``bbox``
    (lat_min, lat_max, lon_min, lon_max) - the PBF counterpart of the Overpass
    airport query, fed to the same parser. None on failure (reported)."""
    from .osm_downloader import remember_failed
    pid = patch_metadata.patch_id
    error = _check_ready()
    if error is None:
        folder, manifest = _load_split(autogen_dir)
        if folder is None:
            error = manifest
        else:
            try:
                # The patch files around the 3x3 area, then the area itself.
                data = _read_patch_files(folder, _patches_in_box(manifest, bbox))
                sel = _select_box(_box_fixed(*bbox), _filter_sets()['air'], *data)
                return _osm_xml(bbox, *sel).encode('utf-8')
            except Exception as e:
                error = f"PBF read failed ({folder}): {e}"
    print(f"[PBF] ERROR airport search for patch {pid}: {error}")
    remember_failed(pid, "airport search (PBF)")
    return None


# ---------------------------------------------------------------------------
# "Split into Patches" button
# ---------------------------------------------------------------------------

def _show_console():
    """Open Blender's System Console (Windows) unless it is already open - the
    split prints its progress there. console_toggle alone would close an open one."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
            return
        bpy.ops.wm.console_toggle()
    except Exception as e:
        print(f"[PBF] could not open the console: {e}")


if bpy is not None:
    class CONDOR_OT_pbf_choose_files(bpy.types.Operator):
        """Choose one or more .osm.pbf files (Ctrl+A / Ctrl+click to choose several,
        e.g. every country / region of the scenery)"""
        bl_idname = "condor.pbf_choose_files"
        bl_label = "Choose PBF files"
        bl_options = {'REGISTER'}

        filter_glob: bpy.props.StringProperty(default="*.pbf", options={'HIDDEN'})
        files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
        directory: bpy.props.StringProperty(subtype='DIR_PATH')

        def invoke(self, context, event):
            first = pbf_path()
            if first:
                self.directory = os.path.dirname(first) + os.sep
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}

        def execute(self, context):
            paths = [os.path.join(self.directory, f.name) for f in self.files
                     if f.name and f.name.lower().endswith(".pbf")]
            if not paths:
                self.report({'ERROR'}, "No .pbf file chosen")
                return {'CANCELLED'}
            prefs = _prefs()
            if prefs is None:
                return {'CANCELLED'}
            prefs.pbf_path = PATH_SEP.join(sorted(paths))     # stored by its update callback
            self.report({'INFO'}, f"{len(paths)} PBF file(s) chosen")
            return {'FINISHED'}

    class CONDOR_OT_pbf_split_patches(bpy.types.Operator):
        """Split the PBF file once into small files, one per patch of this landscape
        (folder next to the PBF). Progress in the System Console"""
        bl_idname = "condor.pbf_split_patches"
        bl_label = "Split into Patches"
        bl_options = {'REGISTER'}

        def execute(self, context):
            error = _check_ready()
            props = getattr(context.scene, "condor_buildings", None)
            if error is None and (props is None or not props.condor_path or
                                  props.landscape_name == 'NONE'):
                error = "choose the Condor folder and the landscape first"
            paths = None
            if error is None:
                from .operators import resolve_condor_paths
                paths = resolve_condor_paths(props)
                if not paths:
                    error = f"invalid Condor folder structure for landscape {props.landscape_name}"
            if error is None:
                pbfs = pbf_paths()
                _show_console()
                print("[PBF] Split into Patches - Blender does not respond until it is "
                      "done, the progress is printed here", flush=True)
                try:
                    count, sec = split_pbf(pbfs, paths['heightmaps'],
                                           split_dir(pbfs, props.landscape_name),
                                           _filter_sets(), props.landscape_name)
                except MemoryError:
                    error = "not enough memory to split the PBF - the split is not complete"
                except Exception as e:
                    error = f"split failed, it is not complete: {e}"
            if error:
                _report_error(context, error)
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            self.report({'INFO'}, f"PBF split into {count} patches in {sec:.1f}s")
            return {'FINISHED'}


# ---------------------------------------------------------------------------
# "Detect countries" button: which countries the scenery covers, i.e. which
# PBF files to download. Done exactly like the OSM_Landcover add-on (Detect
# countries in the Scenery): <Landscape>.trn header -> 4 corners -> lat/lon box
# -> one Overpass query for the country borders. Always asks the server - a PBF
# only holds its own country.
# ---------------------------------------------------------------------------

def _read_response(resp):
    import gzip
    raw_bytes = resp.read()
    if resp.info().get("Content-Encoding") == "gzip":
        raw_bytes = gzip.decompress(raw_bytes)
    return raw_bytes.decode("utf-8")


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def fetch_overpass(query):
    """Exact copy of fetch_overpass from the OSM_Landcover add-on."""
    import ssl
    import urllib.request
    import urllib.error
    data = query.encode("utf-8")
    request_headers = {
        "Content-Type": "text/plain",
        "User-Agent": "CondorOSMLandcover/1.0 (Blender addon for Condor scenery)",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last_err = None
    for url in OVERPASS_ENDPOINTS:
        print(f"[PBF] countries: asking {url} ...")
        req = urllib.request.Request(url, data=data, headers=request_headers)
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                raw = _read_response(resp)
            print(f"[PBF] countries: answered by {url}")
            return json.loads(raw)
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                try:
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                        raw = _read_response(resp)
                    print(f"[PBF] countries: answered by {url}")
                    return json.loads(raw)
                except Exception as inner_e:
                    last_err = inner_e
                    print(f"[Condor OSM Landcover] Server {url} failed ({inner_e}), trying fallback...")
            else:
                last_err = e
                print(f"[Condor OSM Landcover] Server {url} did not respond in time ({e}), trying fallback...")
        except Exception as e:
            last_err = e
            print(f"[Condor OSM Landcover] Server {url} failed ({e}), trying fallback...")

    if last_err:
        raise last_err
    raise RuntimeError("All Overpass API servers failed.")


def _read_trn_header(path):
    """The 36 byte header of a Condor <Landscape>.trn (same reader as OSM_Landcover):
    size, grid offsets, UTM origin, zone and hemisphere."""
    with open(path, "rb") as f:
        head = f.read(36)
    if len(head) < 36:
        raise ValueError("TRN header is shorter than 36 bytes")

    size_x, size_y = struct.unpack("<ii", head[0:8])
    # X grows west (negative), Y grows north (positive)
    offset_x = -abs(struct.unpack("<f", head[8:12])[0])
    offset_y = abs(struct.unpack("<f", head[12:16])[0])
    origin_e, origin_n = struct.unpack("<ff", head[20:28])
    zone = struct.unpack("<i", head[28:32])[0]
    hemisphere = chr(struct.unpack("<i", head[32:36])[0])

    return {
        "size_x": size_x,
        "size_y": size_y,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "origin_e": origin_e,
        "origin_n": origin_n,
        "zone": zone,
        "hemisphere": hemisphere,
    }


def _utm_to_latlon(easting, northing, zone, hemisphere="N"):
    """UTM easting/northing -> WGS84 lat/lon (same as OSM_Landcover utm_to_latlon)."""
    if hemisphere == "S":
        northing = northing - 10000000.0

    a = 6378137.0
    e2 = 0.00669437999014
    k0 = 0.9996

    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    m = northing / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 * e1 / 16) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
    )

    sin_phi1 = math.sin(phi1)
    cos_phi1 = math.cos(phi1)
    tan_phi1 = math.tan(phi1)

    n1 = a / math.sqrt(1 - e2 * sin_phi1 * sin_phi1)
    t1 = tan_phi1 ** 2
    c1 = e2 * cos_phi1 ** 2 / (1 - e2)
    r1 = a * (1 - e2) / math.pow(1 - e2 * sin_phi1 ** 2, 1.5)
    d = (easting - 500000.0) / (n1 * k0)

    lat = phi1 - (n1 * tan_phi1 / r1) * (
        d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * e2) * d ** 4 / 24
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * e2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos_phi1

    return math.degrees(lat), math.degrees(lon)


def _find_countries_in_bbox(south, west, north, east):
    """[(name, country_code), ...] of every country inside the box, interior ones
    included - one Overpass query for the admin_level=2 borders (as OSM_Landcover)."""
    query = (
        "[out:json][timeout:120];"
        'relation["boundary"="administrative"]["admin_level"="2"]'
        f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});"
        "out tags;"
    )
    data = fetch_overpass(query)

    found = {}
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        code = (tags.get("ISO3166-1") or tags.get("ISO3166-1:alpha2") or "").upper()
        name = tags.get("name:en") or tags.get("int_name") or tags.get("name")
        # Border relations between two countries ("Italy - Slovenia") have no country code
        if not name or not code:
            continue
        if code not in found:
            found[code] = (name, code)
    return sorted(found.values())


_COUNTRIES_FILE = "Scenery countries.html"
_GEOFABRIK_URL = "https://download.geofabrik.de/"


# ---------------------------------------------------------------------------
# Which Geofabrik files cover the scenery: Geofabrik publishes every region with
# its outline (index-v1.json). For each country the smallest regions that reach
# into the scenery are taken; where the smaller regions do not cover the whole
# part of the country inside the scenery, the whole country is taken instead.
# ---------------------------------------------------------------------------

_GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"
_GEOFABRIK_INDEX_FILE = "geofabrik_index.json"


def _load_geofabrik_index(autogen_dir):
    """index-v1.json, downloaded once and kept in Working/Autogen."""
    path = os.path.join(autogen_dir, _GEOFABRIK_INDEX_FILE)
    if not (os.path.isfile(path) and os.path.getsize(path) > 0):
        import urllib.request
        from .ssl_context import urlopen_ssl
        print(f"[PBF] countries: downloading the Geofabrik region list "
              f"({_GEOFABRIK_INDEX_URL}) - only once ...")
        req = urllib.request.Request(_GEOFABRIK_INDEX_URL,
                                     headers={"User-Agent": "condor-buildings"})
        with urlopen_ssl(req, timeout=120) as r:
            raw = r.read()
        os.makedirs(autogen_dir, exist_ok=True)
        with open(path + ".tmp", "wb") as fh:
            fh.write(raw)
        os.replace(path + ".tmp", path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class _Region:
    """One Geofabrik region: polygons as [(outer, [holes])] in (lon, lat) + bbox."""

    def __init__(self, feature):
        p = feature.get("properties", {})
        self.id = p.get("id")
        self.parent = p.get("parent")
        # some names carry an HTML line break ("Województwo opolskie<br />(Opole ...)")
        self.name = " ".join(str(p.get("name", self.id)).replace("<br />", " ").split())
        self.iso = p.get("iso3166-1:alpha2") or []
        self.url = (p.get("urls") or {}).get("pbf", "")
        g = feature.get("geometry") or {}
        coords = g.get("coordinates") or []
        if g.get("type") == "Polygon":
            coords = [coords]
        self.polys = [(poly[0], poly[1:]) for poly in coords if poly]
        xs = [pt[0] for outer, _h in self.polys for pt in outer]
        ys = [pt[1] for outer, _h in self.polys for pt in outer]
        self.bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else None

    def contains(self, x, y):
        b = self.bbox
        if b is None or x < b[0] or x > b[2] or y < b[1] or y > b[3]:
            return False
        for outer, holes in self.polys:
            if _in_ring(x, y, outer) and not any(_in_ring(x, y, h) for h in holes):
                return True
        return False


def _segments_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2 = orient(c, d, a), orient(c, d, b)
    d3, d4 = orient(a, b, c), orient(a, b, d)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def _region_hits(region, scenery, sbox):
    """Does the region reach into the scenery polygon (list of (lon, lat))?"""
    b = region.bbox
    if b is None or b[2] < sbox[0] or b[0] > sbox[2] or b[3] < sbox[1] or b[1] > sbox[3]:
        return False
    if any(region.contains(x, y) for x, y in scenery):
        return True
    for outer, _h in region.polys:
        for pt in outer:
            if sbox[0] <= pt[0] <= sbox[2] and sbox[1] <= pt[1] <= sbox[3] and \
               _in_ring(pt[0], pt[1], scenery):
                return True
    n = len(scenery)
    for outer, _h in region.polys:
        for i in range(1, len(outer)):
            a, c = outer[i - 1], outer[i]
            if max(a[0], c[0]) < sbox[0] or min(a[0], c[0]) > sbox[2] or \
               max(a[1], c[1]) < sbox[1] or min(a[1], c[1]) > sbox[3]:
                continue
            for k in range(n):
                if _segments_cross(a, c, scenery[k - 1], scenery[k]):
                    return True
    return False


def _geofabrik_regions(autogen_dir):
    """{id: _Region} of every Geofabrik region + {parent id: [child regions]}."""
    index = _load_geofabrik_index(autogen_dir)
    regions = {}
    for f in index.get("features", []):
        r = _Region(f)
        if r.id and r.bbox:
            regions[r.id] = r
    children = {}
    for r in regions.values():
        if r.parent in regions:
            children.setdefault(r.parent, []).append(r)
    return regions, children


def _country_regions(regions, code):
    """Country-level regions of one country code (not its smaller parts)."""
    cands = [r for r in regions.values()
             if code in r.iso and not (r.parent in regions and code in regions[r.parent].iso)]
    # a region of several countries (e.g. Germany + Austria + Switzerland) only
    # when the country has no file of its own
    own = [r for r in cands if len(r.iso) == 1]
    return own or cands


def _scenery_box(scenery):
    xs = [x for x, _y in scenery]
    ys = [y for _x, y in scenery]
    return (min(xs), min(ys), max(xs), max(ys))


def _geofabrik_countries(regions, scenery):
    """[(name, country code), ...] of every country whose Geofabrik outline reaches
    into the scenery - no Overpass needed."""
    sbox = _scenery_box(scenery)
    codes = sorted({c for r in regions.values() if len(r.iso) == 1 for c in r.iso})
    found = []
    for code in codes:
        for r in _country_regions(regions, code):
            if _region_hits(r, scenery, sbox):
                found.append((r.name, code))
                break
    return sorted(found)


def _geofabrik_files(countries, scenery, regions, children):
    """{country code: [(region name, pbf url), ...]} - the files to download."""
    xs = [x for x, _y in scenery]
    ys = [y for _x, y in scenery]
    sbox = (min(xs), min(ys), max(xs), max(ys))
    # sample points inside the scenery, to check the smaller regions leave no gap
    samples = []
    steps = 40
    for i in range(steps + 1):
        for j in range(steps + 1):
            x = sbox[0] + (sbox[2] - sbox[0]) * i / steps
            y = sbox[1] + (sbox[3] - sbox[1]) * j / steps
            if _in_ring(x, y, scenery):
                samples.append((x, y))

    def inside(region):
        """The whole region lies inside the scenery (every outline point)."""
        b = region.bbox
        if b[0] < sbox[0] or b[2] > sbox[2] or b[1] < sbox[1] or b[3] > sbox[3]:
            return False
        return all(_in_ring(pt[0], pt[1], scenery)
                   for outer, _h in region.polys for pt in outer)

    def pick(region):
        # a region lying wholly inside the scenery is one file, not split up
        if inside(region):
            return [region]
        all_kids = children.get(region.id, [])
        kids = [k for k in all_kids if _region_hits(k, scenery, sbox)]
        # no smaller parts, or every part is needed anyway -> the whole region
        if not kids or len(kids) == len(all_kids):
            return [region]
        # the smaller regions must cover every sample point of this region
        for x, y in samples:
            if region.contains(x, y) and not any(k.contains(x, y) for k in kids):
                return [region]
        out = []
        for k in kids:
            out.extend(pick(k))
        return out

    result = {}
    for _name, code in countries:
        cands = _country_regions(regions, code)
        files = []
        for r in cands:
            if _region_hits(r, scenery, sbox):
                for leaf in pick(r):
                    if leaf.url and (leaf.name, leaf.url) not in files:
                        files.append((leaf.name, leaf.url))
        result[code] = files
    return result


def _write_countries_html(path, landscape, countries, files=None):
    """Scenery countries.html: the sentence with a clickable Geofabrik link and the
    countries one under another in bold, each with the exact files to download.
    HTML so the links open with a click."""
    from html import escape
    files = files or {}
    rows = []
    for name, code in countries:
        rows.append(f"  <li><b>{escape(name)} ({escape(code)})</b>")
        if files.get(code):
            rows.append("    <ul class=\"files\">")
            for fname, url in files[code]:
                rows.append(f"      <li><a href=\"{escape(url)}\" target=\"_blank\">"
                            f"{escape(fname)}</a></li>")
            rows.append("    </ul>")
        rows.append("  </li>")
    items = "\n".join(rows)
    text = (
        "<!DOCTYPE html>\n"
        "<html>\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>Scenery countries - {escape(landscape)}</title>\n"
        "<style>body { font-family: sans-serif; font-size: 16px; margin: 30px; }"
        " li { font-size: 22px; margin: 6px 0; }"
        " ul.files li { font-size: 16px; margin: 3px 0; }</style>\n"
        "</head>\n<body>\n"
        f"<p>This scenery (<span style=\"color: red; text-decoration: underline; "
        f"font-size: 20px;\">{escape(landscape)}</span>) contains the following countries. "
        "Download the PBF files for these countries from this address: "
        f"<a href=\"{_GEOFABRIK_URL}\" target=\"_blank\">{_GEOFABRIK_URL}</a></p>\n"
        f"<ul>\n{items}\n</ul>\n"
        "</body>\n</html>\n"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


if bpy is not None:
    class CONDOR_OT_pbf_detect_countries(bpy.types.Operator):
        """Find which countries the scenery lies in (from <Landscape>.trn), so you know
        which PBF files to download. Asks the Overpass server. Result in the System Console"""
        bl_idname = "condor.pbf_detect_countries"
        bl_label = "Detect countries scenery"
        bl_options = {'REGISTER'}

        def execute(self, context):
            props = getattr(context.scene, "condor_buildings", None)
            if props is None or not props.condor_path or props.landscape_name in ('NONE', ''):
                self.report({'ERROR'}, "Choose the Condor folder and the landscape first")
                return {'CANCELLED'}
            trn_path = os.path.join(bpy.path.abspath(props.condor_path), "Landscapes",
                                    props.landscape_name, f"{props.landscape_name}.trn")
            if not os.path.isfile(trn_path):
                self.report({'ERROR'}, f"File not found: {trn_path}")
                return {'CANCELLED'}
            try:
                h = _read_trn_header(trn_path)
            except Exception as e:
                self.report({'ERROR'}, f"Error reading TRN file: {e}")
                return {'CANCELLED'}

            width_m = h["size_x"] * abs(h["offset_x"])
            height_m = h["size_y"] * abs(h["offset_y"])
            # Condor X grows west and Y grows north, so the origin is the south-east
            # corner (patch 000000 bottom right, the last patch top left)
            e_max = h["origin_e"]
            e_min = h["origin_e"] - width_m
            n_min = h["origin_n"]
            n_max = h["origin_n"] + height_m

            lats, lons = [], []
            for e, n in ((e_min, n_max), (e_max, n_max), (e_max, n_min), (e_min, n_min)):
                lat, lon = _utm_to_latlon(e, n, h["zone"], h["hemisphere"])
                lats.append(lat)
                lons.append(lon)
            south, north = min(lats), max(lats)
            west, east = min(lons), max(lons)
            print(f"[PBF] countries: {props.landscape_name}  zone {h['zone']}{h['hemisphere']}  "
                  f"~{width_m / 1000.0:.1f} x {height_m / 1000.0:.1f} km  "
                  f"lat {south:.4f}..{north:.4f}  lon {west:.4f}..{east:.4f}")

            autogen_dir = os.path.join(bpy.path.abspath(props.condor_path), "Landscapes",
                                       props.landscape_name, "Working", "Autogen")
            # Outline of the scenery in (lon, lat): the 4 UTM edges, 20 points each
            scenery = []
            edges = ((e_min, n_max, e_max, n_max), (e_max, n_max, e_max, n_min),
                     (e_max, n_min, e_min, n_min), (e_min, n_min, e_min, n_max))
            for e1, n1, e2, n2 in edges:
                for i in range(20):
                    t = i / 20.0
                    lat, lon = _utm_to_latlon(e1 + (e2 - e1) * t, n1 + (n2 - n1) * t,
                                              h["zone"], h["hemisphere"])
                    scenery.append((lon, lat))

            # Countries from the Geofabrik region outlines - no Overpass (its country
            # query is too heavy for a big scenery and ends in 504)
            try:
                regions, children = _geofabrik_regions(autogen_dir)
                countries = _geofabrik_countries(regions, scenery)
            except Exception as e:
                self.report({'ERROR'}, f"Country lookup failed: {e}")
                return {'CANCELLED'}

            if countries:
                print("[PBF] countries inside the scenery:")
                for name, code in countries:
                    print(f"[PBF]   {name} ({code})")
                msg = "Countries: " + ", ".join(f"{name} ({code})" for name, code in countries)
                html_path = os.path.join(autogen_dir, _COUNTRIES_FILE)
                files = None
                try:
                    files = _geofabrik_files(countries, scenery, regions, children)
                    print("[PBF] Geofabrik files to download:")
                    for name, code in countries:
                        print(f"[PBF]   {name} ({code}):")
                        for fname, url in files.get(code, []):
                            print(f"[PBF]     {fname}  {url}")
                except Exception as e:
                    print(f"[PBF] Geofabrik file list not available: {e}")
                try:
                    _write_countries_html(html_path, props.landscape_name, countries, files)
                    print(f"[PBF] written -> {html_path}")
                except Exception as e:
                    print(f"[PBF] could not write {html_path}: {e}")
            else:
                msg = "Countries inside the scenery: none found"
                print(f"[PBF] {msg}")
            self.report({'INFO'}, msg)
            return {'FINISHED'}


def register():
    if bpy is not None:
        bpy.utils.register_class(CONDOR_OT_pbf_choose_files)
        bpy.utils.register_class(CONDOR_OT_pbf_split_patches)
        bpy.utils.register_class(CONDOR_OT_pbf_detect_countries)


def unregister():
    if bpy is not None:
        for cls in (CONDOR_OT_pbf_detect_countries, CONDOR_OT_pbf_split_patches,
                    CONDOR_OT_pbf_choose_files):
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
