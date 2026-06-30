"""
Condor Buildings Generator - Microsoft Building Footprints integration

Downloads Microsoft Global ML Building Footprints for a patch and merges
them into the existing OSM file, adding only buildings not already present.
"""

import os
import csv
import gzip
import json
import math
import shutil
import urllib.request
import xml.etree.ElementTree as ET

DATASET_LINKS_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
QK_ZOOM = 9
DUP_THRESHOLD_M = 12.0
_UA = {"User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------------------
# QuadKey helpers
# ---------------------------------------------------------------------------

def _lonlat_to_quadkey(lat, lon, zoom=QK_ZOOM):
    s = math.sin(lat * math.pi / 180.0)
    x = (lon + 180.0) / 360.0
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    tx = int(x * (2 ** zoom))
    ty = int(y * (2 ** zoom))
    qk = ""
    for i in range(zoom, 0, -1):
        d = 0
        mask = 1 << (i - 1)
        if tx & mask:
            d += 1
        if ty & mask:
            d += 2
        qk += str(d)
    return qk


def _quadkeys_for_bbox(lat_min, lat_max, lon_min, lon_max, zoom=QK_ZOOM):
    corners = [
        (lat_min, lon_min),
        (lat_min, lon_max),
        (lat_max, lon_min),
        (lat_max, lon_max),
        ((lat_min + lat_max) / 2, (lon_min + lon_max) / 2),
    ]
    return {_lonlat_to_quadkey(lat, lon, zoom) for lat, lon in corners}


# ---------------------------------------------------------------------------
# HTTP helper (with file cache)
# ---------------------------------------------------------------------------

def _fetch_to_file(url, dest, timeout=300):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _load_qk_index(cache_dir):
    """Download dataset-links.csv once and return QuadKey -> [url] mapping."""
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, "dataset-links.csv")
    _fetch_to_file(DATASET_LINKS_URL, local)
    qk_to_urls = {}
    with open(local, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 3:
                continue
            quadkey, url = row[1], row[2]
            qk_to_urls.setdefault(quadkey, []).append(url)
    return qk_to_urls


# ---------------------------------------------------------------------------
# OSM parsing helpers
# ---------------------------------------------------------------------------

def _parse_nodes(root):
    return {
        n.get('id'): (float(n.get('lat')), float(n.get('lon')))
        for n in root.findall('node')
    }


def _load_building_centroids(path):
    """Return list of (lat, lon) centroids for all building ways in an OSM file."""
    tree = ET.parse(path)
    root = tree.getroot()
    nodes = _parse_nodes(root)
    centroids = []
    for w in root.findall('way'):
        if not any(t.get('k') == 'building' for t in w.findall('tag')):
            continue
        pts = [nodes[nd.get('ref')] for nd in w.findall('nd') if nd.get('ref') in nodes]
        if len(pts) >= 3:
            centroids.append((
                sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts),
            ))
    return centroids


def _load_building_rings(path):
    """Return list of vertex rings [(lat, lon), ...] for all building ways."""
    tree = ET.parse(path)
    root = tree.getroot()
    nodes = _parse_nodes(root)
    out = []
    for w in root.findall('way'):
        if not any(t.get('k') == 'building' for t in w.findall('tag')):
            continue
        pts = [nodes[nd.get('ref')] for nd in w.findall('nd') if nd.get('ref') in nodes]
        if len(pts) >= 3:
            ring = pts[:-1] if pts[0] == pts[-1] else pts
            out.append(ring)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def download_ms_buildings(metadata, output_dir, patch_id, cache_dir):
    """
    Download Microsoft Global Building Footprints for the patch bbox.

    Args:
        metadata: PatchMetadata with lat_min/lat_max/lon_min/lon_max
        output_dir: Where to save map_{patch_id}.osm (e.g. Autogen/MSprint)
        patch_id:   6-digit patch ID string
        cache_dir:  Where to cache gz files (e.g. Autogen/MSprint/_cache)

    Returns:
        Path to the generated OSM file (may contain 0 buildings for empty areas).
        None on fatal error.
    """
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    out_path = os.path.join(output_dir, f"map_{patch_id}.osm")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
        print(f"[MSprint] Patch {patch_id}: MS osm already exists, using directly -> {out_path}")
        return out_path

    lat_min, lat_max = metadata.lat_min, metadata.lat_max
    lon_min, lon_max = metadata.lon_min, metadata.lon_max

    qks = _quadkeys_for_bbox(lat_min, lat_max, lon_min, lon_max)
    print(f"[MSprint] Patch {patch_id}: QuadKeys={sorted(qks)}")

    try:
        qk_to_urls = _load_qk_index(cache_dir)
    except Exception as e:
        print(f"[MSprint] Failed to download dataset-links.csv: {e}")
        return None

    node_id = -1
    way_id = -1
    nodes_out = []
    ways_out = []
    count = 0

    for qk in sorted(qks):
        urls = qk_to_urls.get(qk)
        if not urls:
            print(f"[MSprint] QuadKey {qk} not found (empty area?).")
            continue
        for i, url in enumerate(urls):
            dest = os.path.join(cache_dir, f"{qk}_{i}.csv.gz")
            try:
                _fetch_to_file(url, dest)
            except Exception as e:
                print(f"[MSprint] Download failed {url}: {e}")
                continue

            with gzip.open(dest, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    g = obj.get("geometry")
                    if not g or g.get("type") != "Polygon":
                        continue
                    rings = g.get("coordinates")
                    if not rings:
                        continue
                    outer = rings[0]
                    if len(outer) < 3:
                        continue
                    # centroid check (coordinates are [lon, lat])
                    cx = sum(p[0] for p in outer) / len(outer)
                    cy = sum(p[1] for p in outer) / len(outer)
                    if not (lon_min <= cx <= lon_max and lat_min <= cy <= lat_max):
                        continue
                    pts = outer[:-1] if len(outer) > 1 and outer[0] == outer[-1] else outer
                    if len(pts) < 3:
                        continue
                    nids = []
                    for lon_pt, lat_pt in pts:
                        nodes_out.append(
                            f'  <node id="{node_id}" lat="{lat_pt:.7f}" lon="{lon_pt:.7f}" version="1"/>'
                        )
                        nids.append(node_id)
                        node_id -= 1
                    nids.append(nids[0])
                    w_lines = [f'  <way id="{way_id}" version="1">']
                    for n in nids:
                        w_lines.append(f'    <nd ref="{n}"/>')
                    w_lines.append('    <tag k="building" v="yes"/>')
                    w_lines.append('    <tag k="source" v="microsoft"/>')
                    w_lines.append('  </way>')
                    ways_out.append("\n".join(w_lines))
                    way_id -= 1
                    count += 1

    with open(out_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<osm version="0.6" generator="condor_ms_buildings">\n')
        if nodes_out:
            f.write("\n".join(nodes_out))
            f.write("\n")
        if ways_out:
            f.write("\n".join(ways_out))
            f.write("\n")
        f.write("</osm>\n")

    print(f"[MSprint] {count} MS buildings saved -> {out_path}")

    # Delete gz tile files from cache (saves space), dataset-links.csv is kept
    for fname in os.listdir(cache_dir):
        if fname.endswith(".gz"):
            try:
                os.remove(os.path.join(cache_dir, fname))
            except Exception:
                pass
    print("[MSprint] gz files deleted from cache (dataset-links.csv kept)")

    return out_path


def merge_ms_into_osm(orig_path, ms_path, dup_threshold_m=DUP_THRESHOLD_M):
    """
    Merge MS buildings into orig_path OSM file.

    Keeps a .ori backup of the clean Overpass data so repeated runs never
    double-add buildings — merging always starts from the original Overpass file.

    Args:
        orig_path:       Path to Autogen/map_{patch_id}.osm (will be overwritten)
        ms_path:         Path to MSprint/map_{patch_id}.osm
        dup_threshold_m: Distance in metres below which an MS building is considered
                         a duplicate of an existing OSM building

    Returns:
        (added, skipped) tuple with building counts.
    """
    # Use .ori backup as base so re-running never double-adds MS buildings
    backup = orig_path + ".ori"
    base_path = backup if os.path.exists(backup) else orig_path

    osm_centroids = _load_building_centroids(base_path)  # kept for reference, unused by new logic
    print(f"[MSprint] OSM buildings (base): {len(osm_centroids)}")

    # Load OSM buildings as outlines (not just centroids) - for overlap test
    osm_rings = _load_building_rings(base_path)

    def _bbox(ring):
        la = [p[0] for p in ring]; lo = [p[1] for p in ring]
        return (min(la), max(la), min(lo), max(lo))

    # Spatial grid of OSM buildings by bbox (~200 m cells)
    CELL = 0.002
    grid = {}
    osm_bb = []
    for ring in osm_rings:
        bb = _bbox(ring)
        osm_bb.append((bb, ring))
        idx = len(osm_bb) - 1
        a0 = int(bb[0] / CELL); a1 = int(bb[1] / CELL)
        b0 = int(bb[2] / CELL); b1 = int(bb[3] / CELL)
        for a in range(a0, a1 + 1):
            for b in range(b0, b1 + 1):
                grid.setdefault((a, b), []).append(idx)

    def _pip(pt, ring):
        y, x = pt; n = len(ring); inside = False; j = n - 1
        for i in range(n):
            yi, xi = ring[i]; yj, xj = ring[j]
            if ((xi > x) != (xj > x)) and (y < (yj - yi) * (x - xi) / (xj - xi) + yi):
                inside = not inside
            j = i
        return inside

    def _bb_overlap(b1, b2):
        return not (b1[1] < b2[0] or b1[0] > b2[1] or b1[3] < b2[2] or b1[2] > b2[3])

    def overlaps_osm(ms_ring):
        bb = _bbox(ms_ring)
        a0 = int(bb[0] / CELL); a1 = int(bb[1] / CELL)
        b0 = int(bb[2] / CELL); b1 = int(bb[3] / CELL)
        cand = set()
        for a in range(a0, a1 + 1):
            for b in range(b0, b1 + 1):
                cand.update(grid.get((a, b), []))
        mcy = sum(p[0] for p in ms_ring) / len(ms_ring)
        mcx = sum(p[1] for p in ms_ring) / len(ms_ring)
        for idx in cand:
            obb, oring = osm_bb[idx]
            if not _bb_overlap(bb, obb):
                continue
            if _pip((mcy, mcx), oring):
                return True
            if any(_pip(p, oring) for p in ms_ring):
                return True
            ocy = sum(p[0] for p in oring) / len(oring)
            ocx = sum(p[1] for p in oring) / len(oring)
            if _pip((ocy, ocx), ms_ring):
                return True
            if any(_pip(p, ms_ring) for p in oring):
                return True
        return False

    ms_buildings = _load_building_rings(ms_path)
    print(f"[MSprint] MS budov celkem: {len(ms_buildings)}")

    to_add = []
    for ring in ms_buildings:
        if not overlaps_osm(ring):
            to_add.append(ring)

    skipped = len(ms_buildings) - len(to_add)
    print(f"[MSprint] Adding: {len(to_add)}, skipped (duplicates): {skipped}")

    if not to_add:
        return 0, skipped

    with open(base_path, 'r', encoding='utf-8') as f:
        orig_text = f.read()

    if "</osm>" not in orig_text:
        print("[MSprint] ERROR: file has no </osm> — skipping merge.")
        return 0, 0

    node_id = -1
    way_id = -1
    parts = []
    for ring in to_add:
        nids = []
        for lat, lon in ring:
            parts.append(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>')
            nids.append(node_id)
            node_id -= 1
        nids.append(nids[0])
        w_lines = [f'  <way id="{way_id}" version="1">']
        for n in nids:
            w_lines.append(f'    <nd ref="{n}"/>')
        w_lines.append('    <tag k="building" v="yes"/>')
        w_lines.append('    <tag k="source" v="microsoft"/>')
        w_lines.append('  </way>')
        parts.append("\n".join(w_lines))
        way_id -= 1

    merged_text = orig_text.replace("</osm>", "\n".join(parts) + "\n</osm>")

    # Save clean backup (only once — don't overwrite existing .ori)
    if not os.path.exists(backup):
        shutil.copy2(orig_path, backup)
        print(f"[MSprint] Backup saved: {backup}")

    with open(orig_path, 'w', encoding='utf-8') as f:
        f.write(merged_text)

    total = merged_text.count('k="building"')
    print(f"[MSprint] Merged. Total buildings in file: {total}")
    return len(to_add), skipped
