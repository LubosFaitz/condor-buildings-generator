"""
Condor Buildings Generator - OSM Downloader

Downloads OpenStreetMap building data from Overpass API based on
geographic bounding box coordinates from patch metadata.

Inspired by BLOSM (Blender-OSM) approach for on-the-fly OSM data retrieval.
"""

import os
import math
import json
import urllib.request
import urllib.parse
import urllib.error
import time
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Overpass API endpoints (multiple servers for redundancy)
OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Timeout for download (seconds)
DOWNLOAD_TIMEOUT = 120

# Maximum bbox area to prevent accidentally downloading too much data
MAX_BBOX_AREA_DEG2 = 0.25  # ~25km x 25km at mid-latitudes


@dataclass
class DownloadResult:
    """Result of an OSM download operation."""
    success: bool
    filepath: Optional[str] = None
    error: Optional[str] = None
    download_time_ms: int = 0
    file_size_bytes: int = 0


def build_overpass_query(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    include_relations: bool = True,
    include_power: bool = True,
) -> str:
    """
    Build an Overpass QL query for building (and optionally powerline) data.

    Args:
        lat_min, lat_max, lon_min, lon_max: Bounding box coordinates
        include_relations: Whether to include multipolygon relations
        include_power: Also fetch power=line / minor_line ways. These are tiny and
            harmless to the building parser (which ignores them), so they're
            included by default — that way a single cached map_*.osm serves both
            the buildings and the optional powerlines feature. The ``>;`` recursion
            pulls in each line's node coordinates, which is all the powerline
            generator needs (it stamps a pylon at every node).

    Returns:
        Overpass QL query string
    """
    # Bounding box format for Overpass: (south,west,north,east)
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"

    parts = [f'  way["building"]({bbox});']
    if include_relations:
        parts.append(f'  relation["building"]["type"="multipolygon"]({bbox});')
    if include_power:
        parts.append(f'  way["power"="line"]({bbox});')
        parts.append(f'  way["power"="minor_line"]({bbox});')
        parts.append(f'  node["power"="generator"]["generator:source"="wind"]({bbox});')

    # Aerialways (cable cars / chair lifts). Tiny and ignored by the building
    # parser; the ``>;`` recursion below pulls each way's nodes (incl. the
    # aerialway=pylon supports) so the aerialway generator can place pylons.
    parts.append(f'  way["aerialway"]({bbox});')

    # Aerodromes + runways (aeroway=aerodrome / runway) for the warning-ball
    # "near airport" rule. Tiny, and the building parser ignores non-building
    # features, so this never affects the buildings. The ``>;`` recursion below
    # pulls in the area's nodes so the runway gets endpoints / the aerodrome a
    # centroid.
    parts.append(f'  node["aeroway"="aerodrome"]({bbox});')
    parts.append(f'  way["aeroway"="aerodrome"]({bbox});')
    parts.append(f'  way["aeroway"="runway"]({bbox});')

    # Bridges (road + railway + bridge structures) for the optional bridge feature.
    # The building parser ignores non-building ways, so this is harmless. The ``>;``
    # recursion below pulls each bridge way's node coordinates.
    parts.append(f'  way["bridge"]["highway"]({bbox});')
    parts.append(f'  way["bridge"]["railway"]({bbox});')
    parts.append(f'  way["man_made"="bridge"]({bbox});')

    body = "\n".join(parts)
    query = f"""
[out:xml][timeout:90];
(
{body}
);
out body;
>;
out skel qt;
"""

    return query.strip()


def validate_bbox(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float
) -> Tuple[bool, str]:
    """
    Validate bounding box coordinates.

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check coordinate ranges
    if not (-90 <= lat_min <= 90 and -90 <= lat_max <= 90):
        return False, "Latitude must be between -90 and 90"

    if not (-180 <= lon_min <= 180 and -180 <= lon_max <= 180):
        return False, "Longitude must be between -180 and 180"

    # Check ordering
    if lat_min >= lat_max:
        return False, "lat_min must be less than lat_max"

    if lon_min >= lon_max:
        return False, "lon_min must be less than lon_max"

    # Check area (prevent downloading huge regions)
    area = (lat_max - lat_min) * (lon_max - lon_min)
    if area > MAX_BBOX_AREA_DEG2:
        return False, f"Bounding box too large ({area:.4f} deg²). Maximum: {MAX_BBOX_AREA_DEG2} deg²"

    return True, ""


def download_osm_data(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    output_path: str,
    server_index: int = 0,
    retry_count: int = 2
) -> DownloadResult:
    """
    Download OSM building data for a bounding box.

    Args:
        lat_min, lat_max, lon_min, lon_max: Bounding box coordinates
        output_path: Where to save the .osm file
        server_index: Which Overpass server to use (0-based)
        retry_count: Number of retries on failure

    Returns:
        DownloadResult with success status and file info
    """
    # Validate bbox
    is_valid, error = validate_bbox(lat_min, lat_max, lon_min, lon_max)
    if not is_valid:
        return DownloadResult(success=False, error=error)

    # Build query
    query = build_overpass_query(lat_min, lat_max, lon_min, lon_max)

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # Try each server with retries
    last_error = ""
    start_time = time.time()

    for attempt in range(retry_count + 1):
        # Rotate through servers on retry
        current_server = OVERPASS_SERVERS[(server_index + attempt) % len(OVERPASS_SERVERS)]

        try:
            logger.info(f"Downloading OSM data from {current_server} (attempt {attempt + 1})")

            # Prepare request
            data = urllib.parse.urlencode({'data': query}).encode('utf-8')
            request = urllib.request.Request(
                current_server,
                data=data,
                headers={
                    'User-Agent': 'CondorBuildings/0.4 (Blender addon)',
                    'Content-Type': 'application/x-www-form-urlencoded',
                }
            )

            # Download
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
                content = response.read()

                # Save to file
                with open(output_path, 'wb') as f:
                    f.write(content)

                elapsed_ms = int((time.time() - start_time) * 1000)
                file_size = len(content)

                logger.info(f"Downloaded {file_size} bytes in {elapsed_ms}ms")

                return DownloadResult(
                    success=True,
                    filepath=output_path,
                    download_time_ms=elapsed_ms,
                    file_size_bytes=file_size
                )

        except urllib.error.HTTPError as e:
            last_error = f"HTTP error {e.code}: {e.reason}"
            logger.warning(f"Download failed: {last_error}")

            # Rate limit - wait before retry
            if e.code == 429:
                time.sleep(5)
            else:
                time.sleep(1)

        except urllib.error.URLError as e:
            last_error = f"URL error: {e.reason}"
            logger.warning(f"Download failed: {last_error}")
            time.sleep(1)

        except TimeoutError:
            last_error = "Download timed out"
            logger.warning(f"Download failed: {last_error}")
            time.sleep(1)

        except Exception as e:
            last_error = str(e)
            logger.warning(f"Download failed: {last_error}")
            time.sleep(1)

    return DownloadResult(success=False, error=last_error)


def download_osm_for_patch(
    patch_metadata,
    output_dir: str,
    filename_prefix: str = "map"
) -> DownloadResult:
    """
    Download OSM data for a patch using its metadata.

    Args:
        patch_metadata: PatchMetadata with lat/lon bounds
        output_dir: Directory to save the .osm file
        filename_prefix: Prefix for output filename (e.g., "map" -> "map_036019.osm")

    Returns:
        DownloadResult with success status and file info
    """
    # Generate output filename
    filename = f"{filename_prefix}_{patch_metadata.patch_id}.osm"
    output_path = os.path.join(output_dir, filename)

    # Check if file already exists and is recent
    if os.path.exists(output_path):
        # Check file size (if it's too small, it might be corrupt)
        size = os.path.getsize(output_path)
        if size > 100:  # Minimum valid OSM file size
            logger.info(f"OSM file already exists: {output_path}")
            return DownloadResult(
                success=True,
                filepath=output_path,
                file_size_bytes=size
            )
        else:
            logger.warning(f"Existing OSM file too small ({size} bytes), re-downloading")
            os.remove(output_path)

    return download_osm_data(
        lat_min=patch_metadata.lat_min,
        lat_max=patch_metadata.lat_max,
        lon_min=patch_metadata.lon_min,
        lon_max=patch_metadata.lon_max,
        output_path=output_path
    )


def merge_bbox(patches: list) -> Tuple[float, float, float, float]:
    """
    Merge bounding boxes from multiple patches.

    Args:
        patches: List of PatchMetadata objects

    Returns:
        Tuple of (lat_min, lat_max, lon_min, lon_max) for merged bbox
    """
    if not patches:
        raise ValueError("No patches to merge")

    lat_min = min(p.lat_min for p in patches)
    lat_max = max(p.lat_max for p in patches)
    lon_min = min(p.lon_min for p in patches)
    lon_max = max(p.lon_max for p in patches)

    return lat_min, lat_max, lon_min, lon_max


def download_osm_for_patch_range(
    patches: list,
    output_dir: str,
    filename: str = "buildings.osm"
) -> DownloadResult:
    """
    Download OSM data for a range of patches (merged bbox).

    Args:
        patches: List of PatchMetadata objects
        output_dir: Directory to save the .osm file
        filename: Output filename

    Returns:
        DownloadResult with success status and file info
    """
    if not patches:
        return DownloadResult(success=False, error="No patches provided")

    # Merge bounding boxes
    try:
        lat_min, lat_max, lon_min, lon_max = merge_bbox(patches)
    except ValueError as e:
        return DownloadResult(success=False, error=str(e))

    # Validate merged bbox isn't too large
    is_valid, error = validate_bbox(lat_min, lat_max, lon_min, lon_max)
    if not is_valid:
        return DownloadResult(success=False, error=f"Merged bounding box: {error}")

    output_path = os.path.join(output_dir, filename)

    return download_osm_data(
        lat_min=lat_min,
        lat_max=lat_max,
        lon_min=lon_min,
        lon_max=lon_max,
        output_path=output_path
    )


# ---------------------------------------------------------------------------
# Airports (wider 3x3 search -> shared airports.json)
# ---------------------------------------------------------------------------
#
# Warning balls near an airport must appear even when the runway sits in a
# NEIGHBOURING patch. The per-patch map_*.osm only covers one patch, so we run a
# SEPARATE Overpass query over a 3x3 patch area (patch + one patch each side) just
# for aeroway features, and cache them in a single ``airport/airports.json`` keyed
# by airport name. The buildings/powerlines download is untouched.

# Default runway length for an aerodrome that has NO mapped runway in the search
# area (the airport bbox is the whole site, not a runway, so it can't be used).
NO_RUNWAY_DEFAULT_LENGTH = 1500.0


def _approx_m(lat1, lon1, lat2, lon2):
    """Rough metric distance between two lat/lon points (equirectangular)."""
    la = math.radians((lat1 + lat2) / 2.0)
    dy = (lat1 - lat2) * 111320.0
    dx = (lon1 - lon2) * 111320.0 * math.cos(la)
    return math.hypot(dx, dy)


def _point_in_poly(lat, lon, poly):
    """Ray-cast point-in-polygon on (lon, lat) coordinates."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if ((xi > lon) != (xj > lon)) and \
           (lat < (yj - yi) * (lon - xi) / ((xj - xi) or 1e-12) + yi):
            inside = not inside
        j = i
    return inside


def build_aeroway_query(lat_min, lat_max, lon_min, lon_max):
    """Overpass query for aerodromes + runways only (tiny)."""
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    return (
        "[out:xml][timeout:60];\n(\n"
        f'  way["aeroway"="aerodrome"]({bbox});\n'
        f'  node["aeroway"="aerodrome"]({bbox});\n'
        f'  relation["aeroway"="aerodrome"]({bbox});\n'
        f'  way["aeroway"="runway"]({bbox});\n'
        ");\nout body;\n>;\nout skel qt;"
    )


def _overpass_fetch(query, retry_count=2):
    """POST a query to Overpass and return the raw bytes, or None on failure."""
    data = urllib.parse.urlencode({'data': query}).encode('utf-8')
    for attempt in range(retry_count + 1):
        server = OVERPASS_SERVERS[attempt % len(OVERPASS_SERVERS)]
        try:
            req = urllib.request.Request(
                server, data=data,
                headers={'User-Agent': 'CondorBuildings/0.4 (Blender addon)',
                         'Content-Type': 'application/x-www-form-urlencoded'})
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                return resp.read()
        except Exception as e:
            logger.warning("Airport search: Overpass fetch failed (%s): %s", server, e)
            time.sleep(1)
    return None


def _runway_length(tags, ends):
    """Runway length in metres: the ``length`` tag if present, else geometry."""
    try:
        return float(str(tags.get('length', '')).lower().replace('m', '').strip())
    except (ValueError, AttributeError):
        pass
    (la1, lo1), (la2, lo2) = ends
    return _approx_m(la1, lo1, la2, lo2)


def _bbox_of(latlons):
    las = [p[0] for p in latlons]
    los = [p[1] for p in latlons]
    return (min(las), max(las), min(los), max(los))


def _airport_name(clat, clon, aerodromes, tags):
    """
    Name for a runway: the aerodrome whose bounding box contains the runway centre
    (the smallest one if several), else the nearest aerodrome (<=5 km), else the
    runway ``ref``, else 'airport'. bbox works for both way and relation aerodromes.
    """
    inside = [(nm, bb) for (nm, bb, _cen) in aerodromes
              if bb[0] <= clat <= bb[1] and bb[2] <= clon <= bb[3]]
    if inside:
        inside.sort(key=lambda a: (a[1][1] - a[1][0]) * (a[1][3] - a[1][2]))
        return inside[0][0]
    best, bestd = None, 9e9
    for nm, _bb, cen in aerodromes:
        d = _approx_m(clat, clon, cen[0], cen[1])
        if d < bestd:
            best, bestd = nm, d
    if best and bestd <= 5000.0:
        return best
    return tags.get('ref') or "airport"


def _parse_airports(content, search_bbox=None):
    """
    Parse an aeroway Overpass result into ``{name: {length, center[lat,lon]}}`` —
    one entry per airport (its LONGEST runway), skipping disused/junk runways.

    ``search_bbox`` (lat_min, lat_max, lon_min, lon_max): a no-runway aerodrome is
    only emitted when its CENTRE is inside this box. A big airport whose polygon
    merely clips the box (its runway/centre far outside) is therefore NOT written
    as a fallback here — the patch that actually contains its runway writes it.
    """
    try:
        root = ET.fromstring(content)
    except Exception:
        return {}
    nodes = {n.get('id'): (float(n.get('lat')), float(n.get('lon')))
             for n in root.findall('node')}
    way_pts = {w.get('id'): [nodes[nd.get('ref')] for nd in w.findall('nd')
                             if nd.get('ref') in nodes]
               for w in root.findall('way')}

    # Aerodromes (name, bbox, centroid) from ways, relations and points.
    aerodromes = []
    for w in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in w.findall('tag')}
        if tags.get('aeroway') == 'aerodrome' and tags.get('name'):
            pts = way_pts.get(w.get('id')) or []
            if pts:
                cen = (sum(p[0] for p in pts) / len(pts),
                       sum(p[1] for p in pts) / len(pts))
                aerodromes.append((tags['name'], _bbox_of(pts), cen))
    for rel in root.findall('relation'):
        tags = {t.get('k'): t.get('v') for t in rel.findall('tag')}
        if tags.get('aeroway') == 'aerodrome' and tags.get('name'):
            pts = []
            for mem in rel.findall('member'):
                if mem.get('type') == 'way':
                    pts.extend(way_pts.get(mem.get('ref')) or [])
            if pts:
                cen = (sum(p[0] for p in pts) / len(pts),
                       sum(p[1] for p in pts) / len(pts))
                aerodromes.append((tags['name'], _bbox_of(pts), cen))
    for n in root.findall('node'):
        tags = {t.get('k'): t.get('v') for t in n.findall('tag')}
        if tags.get('aeroway') == 'aerodrome' and tags.get('name'):
            la, lo = nodes[n.get('id')]
            aerodromes.append((tags['name'], (la, la, lo, lo), (la, lo)))

    result = {}
    disused_runways = {}  # name -> disused runway, used only if no active runway
    for w in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in w.findall('tag')}
        if tags.get('aeroway') != 'runway':
            continue
        # Disused/abandoned runways are kept SEPARATELY (used only as a fallback for
        # an aerodrome with no active runway, e.g. a former airfield) - so a former
        # airport's zone still sits on its real runway, not the polygon centroid.
        is_disused = tags.get('disused') == 'yes' or any(
            ('disused' in k or 'abandoned' in k) for k in tags)
        nds = [nd.get('ref') for nd in w.findall('nd')]
        ends = [nodes[r] for r in (nds[0], nds[-1]) if nds and r in nodes]
        if len(ends) != 2:
            continue
        length = _runway_length(tags, ends)
        if length < 50.0:            # junk / taxiway / mis-tag
            continue
        clat = (ends[0][0] + ends[1][0]) / 2.0
        clon = (ends[0][1] + ends[1][1]) / 2.0
        name = _airport_name(clat, clon, aerodromes, tags)
        entry = {"length": round(length, 1),
                 "center": [round(clat, 7), round(clon, 7)],
                 "source": "runway_disused" if is_disused else "runway"}
        target = disused_runways if is_disused else result
        cur = target.get(name)
        if cur is None or length > cur["length"]:     # keep the longest runway
            target[name] = entry

    # Aerodromes with NO mapped runway (runway not in this search area): emit a
    # zone from the airport centre with a DEFAULT length (the airport bbox is the
    # whole site, not a runway, so it must not be used as the length). A later
    # patch that does see the runway overwrites this (see download_airports_for_patch).
    for name, _bb, cen in aerodromes:
        if name in result:
            continue
        # No active runway: prefer a DISUSED runway (its real centre + length) over
        # the aerodrome centroid; only when there's no runway at all use the default.
        if name in disused_runways:
            result[name] = disused_runways[name]
            continue
        if search_bbox is not None and not (
                search_bbox[0] <= cen[0] <= search_bbox[1] and
                search_bbox[2] <= cen[1] <= search_bbox[3]):
            continue   # aerodrome centre outside the search area -> skip fallback
        result[name] = {"length": NO_RUNWAY_DEFAULT_LENGTH,
                        "center": [round(cen[0], 7), round(cen[1], 7)],
                        "source": "aerodrome"}
    return result


def download_airports_for_patch(patch_metadata, autogen_dir):
    """
    Search a 3x3 patch area (patch + one patch each side) for aerodromes/runways
    and merge them into ``<autogen_dir>/airport/airports.json`` (keyed by name,
    deduplicated). Safe to call every patch — never raises (returns False on any
    problem). The buildings/powerlines OSM download is not affected.
    """
    try:
        # 3x3 search area = patch CENTRE +/- 8640 m (half a patch 2880 + one full
        # patch 5760) on each side -> exactly 17280 m = 3 x 5760 m.
        clat = (patch_metadata.lat_min + patch_metadata.lat_max) / 2.0
        clon = (patch_metadata.lon_min + patch_metadata.lon_max) / 2.0
        half = 2880.0 + 5760.0
        dlat = half / 111320.0
        dlon = half / (111320.0 * math.cos(math.radians(clat)))
        content = _overpass_fetch(build_aeroway_query(
            clat - dlat, clat + dlat, clon - dlon, clon + dlon,
        ))
        if content is None:
            return False
        found = _parse_airports(content)
        if not found:
            return True  # searched fine, just nothing there

        airport_dir = os.path.join(autogen_dir, "airport")
        os.makedirs(airport_dir, exist_ok=True)
        path = os.path.join(airport_dir, "airports.json")
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        changed = False
        # Priority: active runway > disused runway > aerodrome centroid fallback.
        _prio = {"aerodrome": 0, "runway_disused": 1, "runway": 2}
        for name, data in found.items():
            old = existing.get(name)
            if old is None or _prio.get(data.get("source"), 0) > _prio.get(old.get("source"), 0):
                existing[name] = data      # new airport, or a better source
                changed = True
        if changed:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            logger.info("Airports: %d in %s", len(existing), path)
        return True
    except Exception as e:
        logger.warning("Airport search failed: %s", e)
        return False
