"""
Condor Buildings Generator - OSM Downloader

Downloads OpenStreetMap building data from Overpass API based on
geographic bounding box coordinates from patch metadata.

Inspired by BLOSM (Blender-OSM) approach for on-the-fly OSM data retrieval.
"""

import os
import re
import math
import json
import importlib
import urllib.request
import urllib.parse
import urllib.error
import time
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Tuple
from dataclasses import dataclass

from .ssl_context import urlopen_ssl

logger = logging.getLogger(__name__)

# Overpass API endpoints (multiple servers for redundancy), strongest machine
# first: VK Maps runs on 56 cores / 384 GB, kumi.systems on 20 / 256 and
# overpass-api.de on 8 / 128, so a big patch query has the best chance up top.
OVERPASS_SERVERS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# How many attempts a patch download gets in total. With four servers this means
# every server is tried TWICE before the patch is given up on - an overloaded
# Overpass answers 502/504 within seconds, so the attempts themselves are cheap;
# it is the growing wait between them that gives the servers time to recover.
DOWNLOAD_ATTEMPTS = 6

# Growing wait between attempts (seconds); the last value is the cap. Kept short
# on purpose: a range of a hundred patches must not turn into a multi-day run, so
# the worst case stays around a minute per patch instead of five. A rate limit
# (HTTP 429) waits longer than an ordinary error - there the server explicitly
# asks to be left alone for a while.
RETRY_WAITS = [3, 8, 15, 25]
RATE_LIMIT_FACTOR = 2
RATE_LIMIT_MAX_WAIT = 60

# Index of the server that answered last. The next query starts there instead of
# always hammering server 0 first - so once a working mirror is found, it keeps
# being used and a rate-limiting server is not hit first every single time.
_last_good_server = 0


def _retry_wait(attempt: int, rate_limited: bool = False) -> float:
    """Seconds to wait before the next attempt (``attempt`` is 0-based)."""
    wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
    if rate_limited:
        wait = min(wait * RATE_LIMIT_FACTOR, RATE_LIMIT_MAX_WAIT)
    return wait


def _remember_server(index: int) -> None:
    """Remember the server that just answered, so the next query starts there."""
    global _last_good_server
    _last_good_server = index % len(OVERPASS_SERVERS)


# Everything this run could NOT download, as (patch id, what) pairs - the side
# queries (tree rows, fences, bridges, airports) used to fail silently, so a
# patch could end up without its trees and nothing said so. The operator clears
# this at the start of a run and prints it in the final summary.
_failed_downloads = []


def remember_failed(patch_id, what):
    """Record a download that did not succeed, for the end-of-run summary."""
    entry = (str(patch_id) if patch_id else "?", what)
    if entry not in _failed_downloads:
        _failed_downloads.append(entry)


def clear_failed():
    """Forget the failures of the previous run."""
    del _failed_downloads[:]


def failed_downloads():
    """The recorded failures as a list of 'patch: what' strings."""
    return [f"{pid}: {what}" for pid, what in _failed_downloads]


# Timeout for download (seconds)
DOWNLOAD_TIMEOUT = 120

# Shorter timeout for the (small, optional) airport/aeroway query so a slow or
# overloaded Overpass server does not stall generation for the full 120 s.
AIRPORT_TIMEOUT = 60

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


def mark_side_data_fetched(osm_path):
    """Write the 'already asked' markers of the optional features into a freshly
    downloaded map_<patch>.osm.

    The main query above now fetches the tree rows and the fences as well, so those
    two features must not go and ask a second time - that extra query is exactly what
    kept failing on an overloaded server and left a patch without its trees. The
    marker text is taken from the feature modules themselves, so it stays in sync
    with their versions; a deleted module is simply skipped. Never raises.
    """
    marks = []
    for mod_name in ("tree_rows", "fences"):
        try:
            mod = importlib.import_module("." + mod_name, __package__)
            comment = getattr(mod, "_FETCH_COMMENT", None)
            if comment:
                marks.append(comment.strip())
        except Exception:
            continue
    if not marks:
        return
    try:
        with open(osm_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        head_end = text.find(">", text.find("<osm"))
        if head_end < 0:
            return
        missing = [m for m in marks if m not in text]
        if not missing:
            return
        text = text[:head_end + 1] + "\n  " + "\n  ".join(missing) + text[head_end + 1:]
        tmp = osm_path + ".mark_tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, osm_path)
    except Exception as e:
        logger.warning("could not mark side data in %s: %s", osm_path, e)


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
        # Substation outline: OSM marks a switchyard with a closed way tagged
        # power=substation (w23232738 "Iver Substation", w28457258 "Rozvodna Reporyje").
        # Without this the polygon is simply not in the downloaded file.
        parts.append(f'  way["power"="substation"]({bbox});')
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
    # A big aerodrome is often a multipolygon RELATION, not a single way — without
    # this its outline is simply not in the file. The ``>;`` recursion below pulls
    # in the member ways and their nodes, so the outline can be stitched together.
    parts.append(f'  relation["aeroway"="aerodrome"]({bbox});')

    # Ground solar farms (power=plant / generator with a solar source), ways AND
    # multipolygon relations — same tags the solar tool downloads on its own
    # (see blender/solar.py). Tiny and ignored by the building parser; having them
    # in the shared map_*.osm lets the generator see the farm outlines too.
    parts.append(f'  way["power"="plant"]["plant:source"="solar"]({bbox});')
    parts.append(f'  relation["power"="plant"]["plant:source"="solar"]({bbox});')
    parts.append(f'  way["power"="generator"]["generator:source"="solar"]({bbox});')
    parts.append(f'  relation["power"="generator"]["generator:source"="solar"]({bbox});')

    # Bridges (road + railway + bridge structures) for the optional bridge feature.
    # The building parser ignores non-building ways, so this is harmless. The ``>;``
    # recursion below pulls each bridge way's node coordinates.
    parts.append(f'  way["bridge"]["highway"]({bbox});')
    parts.append(f'  way["bridge"]["railway"]({bbox});')
    parts.append(f'  way["man_made"="bridge"]({bbox});')

    # Tree rows / hedges / tree-lined roads and single trees, and fences. They used
    # to be fetched by their own extra queries AFTER this one, so a patch could end
    # up with its buildings but without its trees whenever that second query hit an
    # overloaded server (504). Asking for everything in ONE query means a patch is
    # either complete or not downloaded at all. Same tags as tree_rows._tree_row_query
    # and fences._fence_query; the building parser ignores them.
    parts.append(f'  way["natural"="tree_row"]({bbox});')
    parts.append(f'  way["barrier"="hedge"]({bbox});')
    parts.append(f'  way["barrier"="hedge_bank"]({bbox});')
    parts.append(f'  way["natural"="hedge"]({bbox});')
    parts.append(f'  way["fence_type"="hedge"]({bbox});')
    parts.append(f'  way["highway"]["tree_lined"]({bbox});')
    parts.append(f'  way["highway"]["tree_lined:left"]({bbox});')
    parts.append(f'  way["highway"]["tree_lined:right"]({bbox});')
    parts.append(f'  way["highway"]["tree_lined:both"]({bbox});')
    parts.append(f'  way["highway"]["denotation"="avenue"]({bbox});')
    parts.append(f'  way["highway"]["alley"]({bbox});')
    parts.append(f'  way["waterway"]["tree_lined"]({bbox});')
    parts.append(f'  way["waterway"]["tree_lined:left"]({bbox});')
    parts.append(f'  way["waterway"]["tree_lined:right"]({bbox});')
    parts.append(f'  way["waterway"]["tree_lined:both"]({bbox});')
    parts.append(f'  node["natural"="tree"]({bbox});')
    parts.append(f'  node["natural"="tree_group"]({bbox});')
    parts.append(f'  node["natural"="tree_row"]({bbox});')
    parts.append(f'  node["natural"="hedge"]({bbox});')
    parts.append(f'  node["barrier"="hedge"]({bbox});')
    parts.append(f'  node["barrier"="hedge_bank"]({bbox});')
    parts.append(f'  node["fence_type"="hedge"]({bbox});')
    parts.append(f'  way["barrier"="fence"]({bbox});')

    body = "\n".join(parts)
    query = f"""
[out:xml][timeout:180];
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
    server_index: int = -1,
    retry_count: int = DOWNLOAD_ATTEMPTS - 1
) -> DownloadResult:
    """
    Download OSM building data for a bounding box.

    Args:
        lat_min, lat_max, lon_min, lon_max: Bounding box coordinates
        output_path: Where to save the .osm file
        server_index: Which Overpass server to start with (0-based); -1 (default)
            starts with the server that answered last
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
    total_attempts = retry_count + 1
    # Start with the server that last answered, not always with server 0.
    start_index = _last_good_server if server_index < 0 else server_index

    for attempt in range(total_attempts):
        # Rotate through servers on retry
        current_index = (start_index + attempt) % len(OVERPASS_SERVERS)
        current_server = OVERPASS_SERVERS[current_index]
        rate_limited = False

        try:
            logger.info(f"Downloading OSM data from {current_server} "
                        f"(attempt {attempt + 1}/{total_attempts})")

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
            with urlopen_ssl(request, timeout=DOWNLOAD_TIMEOUT) as response:
                content = response.read()

                # Save to file
                with open(output_path, 'wb') as f:
                    f.write(content)

                # The query above already carries the tree rows and the fences, so
                # mark the file as done for them - no second query, no patch left
                # without its trees because that extra query timed out.
                mark_side_data_fetched(output_path)

                elapsed_ms = int((time.time() - start_time) * 1000)
                file_size = len(content)

                logger.info(f"Downloaded {file_size} bytes in {elapsed_ms}ms")
                _remember_server(current_index)

                return DownloadResult(
                    success=True,
                    filepath=output_path,
                    download_time_ms=elapsed_ms,
                    file_size_bytes=file_size
                )

        except urllib.error.HTTPError as e:
            last_error = f"HTTP error {e.code}: {e.reason}"
            rate_limited = (e.code == 429)   # rate limit - wait longer

        except urllib.error.URLError as e:
            last_error = f"URL error: {e.reason}"

        except TimeoutError:
            last_error = "Download timed out"

        except Exception as e:
            last_error = str(e)

        # Only reached when the attempt failed (a success returns above).
        logger.warning(f"Download failed (attempt {attempt + 1}/{total_attempts}, "
                       f"{current_server}): {last_error}")
        if attempt < total_attempts - 1:
            wait = _retry_wait(attempt, rate_limited)
            logger.info(f"Waiting {wait:.0f} s before the next Overpass attempt")
            time.sleep(wait)

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


def _overpass_fetch(query, retry_count=None, what="Overpass query", patch_id=None):
    """POST a query to Overpass and return the raw bytes, or None on failure.

    Used by every SIDE query (tree rows, fences, bridges, airports), so it gets
    the same patience as the patch download: as many attempts, the same growing
    wait, and it starts with the server that last answered. `what` names the data
    in the log, `patch_id` puts the failure into the end-of-run summary.
    """
    data = urllib.parse.urlencode({'data': query}).encode('utf-8')
    if retry_count is None:
        retry_count = DOWNLOAD_ATTEMPTS - 1
    total_attempts = retry_count + 1
    start_index = _last_good_server
    for attempt in range(total_attempts):
        index = (start_index + attempt) % len(OVERPASS_SERVERS)
        server = OVERPASS_SERVERS[index]
        try:
            req = urllib.request.Request(
                server, data=data,
                headers={'User-Agent': 'CondorBuildings/0.4 (Blender addon)',
                         'Content-Type': 'application/x-www-form-urlencoded'})
            with urlopen_ssl(req, timeout=AIRPORT_TIMEOUT) as resp:
                content = resp.read()
            _remember_server(index)
            return content
        except Exception as e:
            print(f"[download] {what}: failed "
                  f"(attempt {attempt + 1}/{total_attempts}, {server}): {e}")
            logger.warning("%s: Overpass fetch failed (attempt %d/%d, %s): %s",
                           what, attempt + 1, total_attempts, server, e)
            if attempt < total_attempts - 1:
                time.sleep(_retry_wait(attempt))
    remember_failed(patch_id, what)
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
        patch_id = getattr(patch_metadata, "patch_id", None)
        airport_dir = os.path.join(autogen_dir, "airport")
        path = os.path.join(airport_dir, "airports.json")

        # Load the existing shared file first so we can SKIP the (slow, often timing-out)
        # online Overpass query for a patch that was already searched before. The list of
        # searched patches is kept under the "__meta__" key (ignored by airports_in_patch,
        # which only reads entries that have a "center").
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        meta = existing.get("__meta__", {}) if isinstance(existing.get("__meta__"), dict) else {}
        searched = list(meta.get("searched_patches", []))
        # This patch sits in the CENTRE of its own 3x3 airport area. Work out those 9
        # patch ids; the Overpass query is skipped ONLY when ALL 9 are already searched.
        # If any is still missing (e.g. the 037 column / 025 row when 036024 is the
        # centre) we must query so those get covered too - being a mere neighbour of an
        # earlier search is NOT enough. patch_id is "XXXYYY" (px=first 3, py=last 3).
        neigh_ids = [patch_id] if patch_id else []
        if patch_id:
            try:
                px, py = int(patch_id[:3]), int(patch_id[3:])
                neigh_ids = [f"{px + dx:03d}{py + dy:03d}"
                             for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            except Exception:
                neigh_ids = [patch_id]
        if patch_id and all(nid in searched for nid in neigh_ids):
            logger.info("Airports: 3x3 around patch %s already searched -> skip Overpass", patch_id)
            return True

        # 3x3 search area = patch CENTRE +/- 8640 m (half a patch 2880 + one full
        # patch 5760) on each side -> exactly 17280 m = 3 x 5760 m.
        clat = (patch_metadata.lat_min + patch_metadata.lat_max) / 2.0
        clon = (patch_metadata.lon_min + patch_metadata.lon_max) / 2.0
        half = 2880.0 + 5760.0
        dlat = half / 111320.0
        dlon = half / (111320.0 * math.cos(math.radians(clat)))
        content = _overpass_fetch(build_aeroway_query(
            clat - dlat, clat + dlat, clon - dlon, clon + dlon,
        ), what="airport search", patch_id=patch_id)
        if content is None:
            # a failed/timed-out fetch is NOT recorded, so it is retried next time
            return False
        found = _parse_airports(content)

        os.makedirs(airport_dir, exist_ok=True)
        changed = False
        # Priority: active runway > disused runway > aerodrome centroid fallback.
        _prio = {"aerodrome": 0, "runway_disused": 1, "runway": 2}
        for name, data in found.items():
            data = dict(data)
            if patch_id:
                data["patch"] = patch_id       # patch this airport was found from
            old = existing.get(name)
            if old is None or _prio.get(data.get("source"), 0) > _prio.get(old.get("source"), 0):
                existing[name] = data      # new airport, or a better source
                changed = True
        # The 3x3 query centred on this patch covered it AND its 8 neighbours, so mark
        # all 9 as searched (even where no airport was found). That way the NEXT patch
        # only has to search the strip it adds: e.g. after 036024 (which searches the
        # 037 column) generating 037024 finds 036 + 037 already done and queries only
        # the new 038 column (038023-038025).
        if patch_id:
            for nid in neigh_ids:
                if nid not in searched:
                    searched.append(nid)
                    changed = True
            meta["searched_patches"] = sorted(searched)
            existing["__meta__"] = meta
        if changed:
            # __meta__ (searched patches) on top; patches grouped by X (first three
            # digits) - each X group on its own line side by side; airports below that
            ordered = {}
            if "__meta__" in existing:
                ordered["__meta__"] = existing["__meta__"]
            for k, v in existing.items():
                if k != "__meta__":
                    ordered[k] = v
            patches = ordered.get("__meta__", {}).get("searched_patches", [])
            rows = []
            cur_x = None
            for p in patches:
                if p[:3] != cur_x:
                    rows.append([])
                    cur_x = p[:3]
                rows[-1].append(p)
            if rows:
                patches_block = "[\n" + ",\n".join(
                    "      " + ", ".join(json.dumps(p, ensure_ascii=False) for p in row)
                    for row in rows) + "\n    ]"
            else:
                patches_block = "[]"
            text = json.dumps(ordered, ensure_ascii=False, indent=2)
            text = re.sub(
                r'("searched_patches": )\[[^\]]*\]',
                lambda m: m.group(1) + patches_block,
                text,
            )
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            n_air = sum(1 for k in existing if k != "__meta__")
            logger.info("Airports: %d in %s (patch %s searched)", n_air, path, patch_id)
        return True
    except Exception as e:
        logger.warning("Airport search failed: %s", e)
        return False
