"""
Condor Buildings Generator - worker process of "Split into Patches" (.osm.pbf)

Run by Blender's own Python OUTSIDE Blender (no bpy), started by pbf_source.py:

    python pbf_split_worker.py split <job.json>          the whole split (coordinator)
    python pbf_split_worker.py unit  <job.json> <i>      one unit of work
    python pbf_split_worker.py write <job.json> <w> <n>  writes every n-th patch file

The coordinator (pbf_source.split_pbf) cuts the work into units and runs several
of them at once, each in its own process (so all CPU cores are used):

  'ways' unit = (PBF file, part k of n): the tagged nodes and ways whose id % n == k.
                Untagged ones are dropped by pyosmium in C++ (every tagged object is
                kept, only by its place - see "all_tags" in the job).
  'rels' unit = the relations of one PBF file + the geometry of their member ways
                and member nodes (only those are let through, by id, in C++).

Every unit writes its records patch by patch into its own temporary file; the
'write' processes then join them into the <patch>.osm.pbf files, in the same order
the old one-process split used (file by file, relations last).
"""

import os
import sys
import json
import time
import importlib


def _package():
    """pbf_source of this add-on (the add-ons folder is put on sys.path)."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)                 # .../addons/condor_buildings
    addons = os.path.dirname(root)
    if addons not in sys.path:
        sys.path.insert(0, addons)
    return importlib.import_module(os.path.basename(root) + ".blender.pbf_source")


def peak_ram():
    """Peak memory of this process in bytes (0 when unknown)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                    wintypes.DWORD]
            c = _PMC()
            c.cb = ctypes.sizeof(c)
            if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return int(c.PeakWorkingSetSize)
            return 0
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except Exception:
        return 0


class _UnitOut:
    """Records of one unit, kept per patch in RAM and appended to <tmp>/u<i>.dat
    from time to time; the index (patch -> [offset, length] chunks) goes to
    <tmp>/u<i>.json, written LAST (= the unit is complete)."""

    SPILL = 64 * 1024 * 1024

    def __init__(self, tmp, i):
        self.dat = os.path.join(tmp, f"u{i}.dat")
        self.idx = os.path.join(tmp, f"u{i}.json")
        self.fh = open(self.dat, "wb")
        self.pos = 0
        self.bufs = {}
        self.buffered = 0
        self.chunks = {}

    def put(self, hits, rec):
        for i in hits:
            buf = self.bufs.get(i)
            if buf is None:
                self.bufs[i] = buf = bytearray()
            buf += rec
        self.buffered += len(rec) * len(hits)
        if self.buffered > self.SPILL:
            self.flush()

    def flush(self):
        for i in sorted(self.bufs):
            buf = self.bufs[i]
            self.fh.write(buf)
            self.chunks.setdefault(i, []).append([self.pos, len(buf)])
            self.pos += len(buf)
        self.bufs.clear()
        self.buffered = 0

    def finish(self, stats):
        self.flush()
        self.fh.close()
        stats["peak_ram"] = peak_ram()
        with open(self.idx + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"chunks": {str(k): v for k, v in self.chunks.items()},
                       "stats": stats}, fh)
        os.replace(self.idx + ".tmp", self.idx)


def _setup(p, job):
    patches = {pid: tuple(b) for pid, b in job["patches"].items()}
    grid = p._PatchGrid(patches)
    fsets = p._fsets_from_json(job["fsets"])
    stmts = {o: fsets['main'][o] + fsets['air'][o] for o in ('node', 'way', 'relation')}
    index = {o: p._index_statements(stmts[o]) for o in stmts}
    # all_tags: EVERY tagged object goes into the patch files (only by its place);
    # what the plugin needs is picked later, when map_<patch>.osm is written.
    return grid, index, bool(job.get("all_tags"))


def run_unit(job, i):
    """One unit of work (see the module docstring)."""
    p = _package()
    import osmium
    import osmium.filter

    start = time.time()
    unit = job["units"][i]
    pbf = unit["file"]
    grid, index, all_tags = _setup(p, job)
    way_keys = set(index['way'])
    node_index = index['node']
    way_index = index['way']
    out = _UnitOut(job["tmp"], i)
    stats = {}

    if unit["kind"] == "ways":
        k, n = unit["part"], unit["parts"]
        fp = osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY)
        fp.with_locations()
        if all_tags:
            # untagged nodes / ways never reach Python (the locations are still kept)
            fp.with_filter(osmium.filter.EmptyTagFilter())
        else:
            if node_index:
                node_filter = osmium.filter.KeyFilter(*sorted(node_index))
                node_filter.enable_for(osmium.osm.NODE)
                fp.with_filter(node_filter)
            if way_keys:
                # ways without any wanted key never reach Python
                way_filter = osmium.filter.KeyFilter(*sorted(way_keys))
                way_filter.enable_for(osmium.osm.WAY)
                fp.with_filter(way_filter)
        n_ways = n_kept = n_nodes = n_far = 0
        for obj in fp:
            if obj.id % n != k:
                continue
            if obj.type_str() == 'n':
                tags = {t.k: t.v for t in obj.tags}
                if not all_tags and not p._match_keyed(tags, node_index):
                    continue
                loc = obj.location
                x, y = loc.x, loc.y
                hits = grid.at_point(x, y)
                if not hits:
                    continue
                tb = p._tags_to_bytes(tags)
                out.put(hits, p._NODE_REC.pack(1, obj.id, x, y, len(tb)) + tb)
                n_nodes += 1
                continue

            n_ways += 1
            wid = obj.id
            tags = None
            if all_tags:
                tags = {t.k: t.v for t in obj.tags} or None
            else:
                for tg in obj.tags:
                    if tg.k in way_keys:
                        tags = {t.k: t.v for t in obj.tags}
                        break
            if tags is None or not (all_tags or p._match_keyed(tags, way_index)):
                continue
            nodes = obj.nodes
            if not len(nodes):
                continue
            # Far from the scenery? Decided by the FIRST node only, without walking
            # the way. Long lines are always walked (they may cross a patch anyway).
            if not p._is_long(tags):
                loc = nodes[0].location
                if loc.x != p._UNDEF and not grid.is_near(loc.x, loc.y):
                    n_far += 1
                    continue
            refs, xs, ys = [], [], []
            for nd in nodes:
                loc = nd.location
                x, y = loc.x, loc.y
                if x == p._UNDEF or y == p._UNDEF:
                    continue
                refs.append(nd.ref)
                xs.append(x)
                ys.append(y)
            if not refs:
                continue
            bb = (min(xs), min(ys), max(xs), max(ys))
            hits = grid.hits_way(xs, ys, bb)
            if hits:
                out.put(hits, p._way_rec(2, wid, refs, xs, ys, tags))
                n_kept += 1
        stats.update(ways=n_ways, kept=n_kept, nodes=n_nodes, far=n_far)

    else:
        # 1) Relations: libosmium skips everything else in C++.
        rels = {}
        member_ways = set()
        member_nodes = set()
        fp = osmium.FileProcessor(pbf, osmium.osm.RELATION)
        if all_tags:
            fp.with_filter(osmium.filter.EmptyTagFilter())
        else:
            fp.with_filter(osmium.filter.KeyFilter(*sorted(index['relation'])))
        for rel in fp:
            tags = {t.k: t.v for t in rel.tags}
            if not all_tags and not p._match_keyed(tags, index['relation']):
                continue
            members = [(m.type, m.ref, m.role) for m in rel.members]
            rels[rel.id] = (tags, members)
            for mt, ref, _role in members:
                if mt == 'w':
                    member_ways.add(ref)
                elif mt == 'n':
                    member_nodes.add(ref)

        # 2) Geometry of the member ways and the tagged member nodes - only they
        #    are let through (by id), everything else stays in C++.
        member_geo = {}     # relation member ways near the scenery: id -> (refs, xs, ys, bb)
        member_pts = {}     # tagged relation member nodes in a patch: id -> (x, y)
        if rels:
            fp = osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY)
            fp.with_locations()
            if all_tags:
                # tagged member nodes only; member WAYS may be untagged (skeleton)
                node_filter = osmium.filter.EmptyTagFilter()
                node_filter.enable_for(osmium.osm.NODE)
                fp.with_filter(node_filter)
            elif node_index:
                node_filter = osmium.filter.KeyFilter(*sorted(node_index))
                node_filter.enable_for(osmium.osm.NODE)
                fp.with_filter(node_filter)
            node_ids = osmium.filter.IdFilter(member_nodes if (all_tags or node_index) else [])
            node_ids.enable_for(osmium.osm.NODE)
            fp.with_filter(node_ids)
            way_ids = osmium.filter.IdFilter(member_ways)
            way_ids.enable_for(osmium.osm.WAY)
            fp.with_filter(way_ids)
            for obj in fp:
                if obj.type_str() == 'n':
                    tags = {t.k: t.v for t in obj.tags}
                    if not all_tags and not p._match_keyed(tags, node_index):
                        continue
                    loc = obj.location
                    x, y = loc.x, loc.y
                    if grid.at_point(x, y):
                        member_pts[obj.id] = (x, y)
                    continue
                wid = obj.id
                tags = None
                if all_tags:
                    tags = {t.k: t.v for t in obj.tags} or None
                else:
                    for tg in obj.tags:
                        if tg.k in way_keys:
                            tags = {t.k: t.v for t in obj.tags}
                            break
                matched = tags is not None and (all_tags or p._match_keyed(tags, way_index))
                nodes = obj.nodes
                if not len(nodes):
                    continue
                if not (matched and p._is_long(tags)):
                    loc = nodes[0].location
                    if loc.x != p._UNDEF and not grid.is_near(loc.x, loc.y):
                        continue
                refs, xs, ys = [], [], []
                for nd in nodes:
                    loc = nd.location
                    x, y = loc.x, loc.y
                    if x == p._UNDEF or y == p._UNDEF:
                        continue
                    refs.append(nd.ref)
                    xs.append(x)
                    ys.append(y)
                if not refs:
                    continue
                member_geo[wid] = (refs, xs, ys, (min(xs), min(ys), max(xs), max(ys)))

        # 3) A relation is in a patch when one of its member ways (or tagged member
        #    nodes) is - the Overpass rule. It brings all its member ways along.
        n_rels = 0
        for rid, (tags, members) in rels.items():
            geos = [member_geo[ref] for mt, ref, _r in members
                    if mt == 'w' and ref in member_geo]
            pts = [member_pts[ref] for mt, ref, _r in members
                   if mt == 'n' and ref in member_pts]
            if not geos and not pts:
                continue
            cand = set()
            for g in geos:
                grid.candidates(g[1], g[2], g[3], cand)
            for x, y in pts:
                cand.update(grid.cells.get((x // p._CELL, y // p._CELL), ()))
            hits = []
            for c in cand:
                b = grid.boxes[c]
                if any(p._way_hits(g[1], g[2], g[3], b) for g in geos) or \
                   any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for x, y in pts):
                    hits.append(c)
            if not hits:
                continue
            mb = json.dumps(members, ensure_ascii=False).encode("utf-8")
            tb = p._tags_to_bytes(tags)
            head = [p._REL_REC.pack(4, rid, len(mb), len(tb)), mb, tb]
            if all_tags and tags.get('type') != 'multipolygon':
                # a route / boundary can cross the whole country: each patch gets only
                # its member ways that run through it (an outline - multipolygon -
                # still gets all of them, as before)
                for c in hits:
                    b = grid.boxes[c]
                    parts = list(head)
                    seen = set()
                    for mt, ref, _r in members:
                        if mt == 'w' and ref in member_geo and ref not in seen:
                            seen.add(ref)
                            g = member_geo[ref]
                            if p._way_hits(g[1], g[2], g[3], b):
                                parts.append(p._way_rec(3, ref, g[0], g[1], g[2], None))
                    out.put([c], b"".join(parts))
                n_rels += 1
                continue
            parts = list(head)
            seen = set()
            for mt, ref, _r in members:
                if mt == 'w' and ref in member_geo and ref not in seen:
                    seen.add(ref)
                    g = member_geo[ref]
                    parts.append(p._way_rec(3, ref, g[0], g[1], g[2], None))
            out.put(hits, b"".join(parts))
            n_rels += 1
        stats.update(relations=len(rels), rels_kept=n_rels)

    stats["seconds"] = round(time.time() - start, 1)
    out.finish(stats)


def run_write(job, w, n):
    """Write every n-th patch file that has data (starting with the w-th)."""
    p = _package()
    import osmium.io

    start = time.time()
    tmp = job["tmp"]
    units = []
    for i in range(len(job["units"])):
        with open(os.path.join(tmp, f"u{i}.json"), encoding="utf-8") as fh:
            units.append(json.load(fh)["chunks"])
    with_data = sorted({int(c) for chunks in units for c in chunks})
    mine = with_data[w::n]
    handles = {}
    pool = osmium.io.ThreadPool()
    written = 0
    try:
        for c in mine:
            data = bytearray()
            key = str(c)
            for i, chunks in enumerate(units):
                for off, length in chunks.get(key, ()):
                    fh = handles.get(i)
                    if fh is None:
                        fh = handles[i] = open(os.path.join(tmp, f"u{i}.dat"), "rb")
                    fh.seek(off)
                    data += fh.read(length)
            if not data:
                continue
            pid = job["patch_ids"][c]
            p._write_patch_pbf(os.path.join(job["out_dir"], f"{pid}.osm.pbf"), data, pool)
            written += 1
    finally:
        for fh in handles.values():
            fh.close()
    stats = {"written": written, "seconds": round(time.time() - start, 1),
             "peak_ram": peak_ram()}
    path = os.path.join(tmp, f"w{w}.json")
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(stats, fh)
    os.replace(path + ".tmp", path)


def run_split(job_path):
    """The whole split (started by the Split into Patches button)."""
    p = _package()
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    try:
        p.split_pbf(job["pbfs"], job["heightmaps"], job["out_dir"],
                    p._fsets_from_json(job["fsets"]), job.get("landscape", ""))
    except MemoryError:
        print("[PBF] ERROR: not enough memory to split the PBF - the split is not complete",
              flush=True)
        return 1
    except Exception as e:
        print(f"[PBF] ERROR: split failed, it is not complete: {e}", flush=True)
        return 1
    return 0


def main(argv):
    mode = argv[1]
    if mode == "split":
        return run_split(argv[2])
    with open(argv[2], encoding="utf-8") as fh:
        job = json.load(fh)
    if mode == "unit":
        run_unit(job, int(argv[3]))
    elif mode == "write":
        run_write(job, int(argv[3]), int(argv[4]))
    else:
        raise SystemExit(f"unknown mode {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
