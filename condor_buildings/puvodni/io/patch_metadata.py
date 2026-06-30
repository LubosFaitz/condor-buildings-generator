"""
Patch metadata loader for Condor Buildings Generator.

Parses h*.txt files that contain projection parameters and
geographic bounds for Condor landscape patches.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PatchMetadata:
    """
    Metadata for a Condor landscape patch.

    Loaded from h*.txt files in the Heightmaps directory.

    Attributes:
        patch_id: Patch identifier (e.g., "036019")
        zone_number: UTM zone number
        translate_x: X translation offset for local coordinates
        translate_y: Y translation offset for local coordinates
        lat_min: Minimum latitude (degrees)
        lat_max: Maximum latitude (degrees)
        lon_min: Minimum longitude (degrees)
        lon_max: Maximum longitude (degrees)
    """
    patch_id: str
    zone_number: int
    translate_x: float
    translate_y: float
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    @property
    def lat_center(self) -> float:
        """Center latitude."""
        return (self.lat_min + self.lat_max) / 2

    @property
    def lon_center(self) -> float:
        """Center longitude."""
        return (self.lon_min + self.lon_max) / 2

    @property
    def lat_span(self) -> float:
        """Latitude span in degrees."""
        return self.lat_max - self.lat_min

    @property
    def lon_span(self) -> float:
        """Longitude span in degrees."""
        return self.lon_max - self.lon_min


def load_patch_metadata(filepath: str) -> PatchMetadata:
    """
    Load patch metadata from h*.txt file.

    File format (one key: value per line):
        ZoneNumber: 33
        TranslateX: -434880
        TranslateY: -5135040
        LatMin: 46.339599609375
        LatMax: 46.3919868469238
        LonMin: 14.1155548095703
        LonMax: 14.1912298202515

    Args:
        filepath: Path to h*.txt file

    Returns:
        PatchMetadata instance

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is invalid
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Patch metadata file not found: {filepath}")

    # Extract patch ID from filename (e.g., "h036019.txt" -> "036019")
    patch_id = path.stem
    if patch_id.startswith('h'):
        patch_id = patch_id[1:]

    # Parse file
    values = {}
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or ':' not in line:
                continue

            key, value = line.split(':', 1)
            key = key.strip().lower()
            value = value.strip()

            # Map various key formats to normalized names
            key_map = {
                'zonenumber': 'zone_number',
                'zone_number': 'zone_number',
                'translatex': 'translate_x',
                'translate_x': 'translate_x',
                'translatey': 'translate_y',
                'translate_y': 'translate_y',
                'latmin': 'lat_min',
                'lat_min': 'lat_min',
                'latmax': 'lat_max',
                'lat_max': 'lat_max',
                'lonmin': 'lon_min',
                'lon_min': 'lon_min',
                'lonmax': 'lon_max',
                'lon_max': 'lon_max',
            }

            if key in key_map:
                values[key_map[key]] = value

    # Validate required fields
    required = ['zone_number', 'translate_x', 'translate_y',
                'lat_min', 'lat_max', 'lon_min', 'lon_max']

    missing = [k for k in required if k not in values]
    if missing:
        raise ValueError(
            f"Missing required fields in {filepath}: {missing}"
        )

    # Convert to typed values
    try:
        return PatchMetadata(
            patch_id=patch_id,
            zone_number=int(values['zone_number']),
            translate_x=float(values['translate_x']),
            translate_y=float(values['translate_y']),
            lat_min=float(values['lat_min']),
            lat_max=float(values['lat_max']),
            lon_min=float(values['lon_min']),
            lon_max=float(values['lon_max']),
        )
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid value format in {filepath}: {e}")


def extract_patch_id_from_path(filepath: str) -> str:
    """
    Extract patch ID from various file path formats.

    Handles:
        h036019.txt -> 036019
        h036019.obj -> 036019
        o036019_LOD0.obj -> 036019

    Args:
        filepath: Path to any patch-related file

    Returns:
        Patch ID string
    """
    path = Path(filepath)
    name = path.stem

    # Remove prefixes
    if name.startswith('h') or name.startswith('t') or name.startswith('o'):
        name = name[1:]

    # Remove LOD suffix
    for suffix in ['_LOD0', '_LOD1', '_lod0', '_lod1']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break

    return name
