"""
Condor Buildings Generator - OSM Downloader

Downloads OpenStreetMap building data from Overpass API based on
geographic bounding box coordinates from patch metadata.

Inspired by BLOSM (Blender-OSM) approach for on-the-fly OSM data retrieval.
"""

import os
import urllib.request
import urllib.parse
import urllib.error
import time
import logging
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
