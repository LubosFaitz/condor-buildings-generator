"""
Input/Output modules for Condor Buildings Generator.
"""

from .patch_metadata import PatchMetadata, load_patch_metadata
from .terrain_loader import load_terrain, TerrainLoadError
from .way_stitcher import (
    WaySegment,
    StitchingError,
    stitch_ways,
    stitch_ways_to_points,
    normalize_ring_orientation,
    validate_ring,
)
from .osm_parser import (
    OSMNode,
    OSMWay,
    OSMRelation,
    ParseResult,
    parse_osm_file,
)
from .obj_exporter import (
    ExportStats,
    export_obj,
    export_obj_lod0,
    export_obj_lod1,
    validate_obj_file,
)

__all__ = [
    # Patch metadata
    'PatchMetadata',
    'load_patch_metadata',
    # Terrain
    'load_terrain',
    'TerrainLoadError',
    # Way stitching
    'WaySegment',
    'StitchingError',
    'stitch_ways',
    'stitch_ways_to_points',
    'normalize_ring_orientation',
    'validate_ring',
    # OSM parsing
    'OSMNode',
    'OSMWay',
    'OSMRelation',
    'ParseResult',
    'parse_osm_file',
    # OBJ export
    'ExportStats',
    'export_obj',
    'export_obj_lod0',
    'export_obj_lod1',
    'validate_obj_file',
]
