"""
OSM XML parser for Condor Buildings Generator.

Parses OpenStreetMap XML data to extract building footprints,
handling both simple ways and multipolygon relations with holes.

Uses way_stitcher for proper handling of unordered multipolygon members.
"""

import xml.etree.ElementTree as ET
import hashlib
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field
import logging

from ..models.geometry import Point2D, Polygon
from ..models.building import BuildingRecord, BuildingCategory, RoofType, RoofDirectionSource
from ..projection import IProjector
from ..utils.polygon_utils import remove_collinear_points, polygon_signed_area
from .way_stitcher import WaySegment, stitch_ways, normalize_ring_orientation

logger = logging.getLogger(__name__)


@dataclass
class OSMNode:
    """An OSM node with geographic coordinates."""
    id: str
    lat: float
    lon: float


@dataclass
class OSMWay:
    """An OSM way with node references and tags."""
    id: str
    node_ids: List[str]
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class OSMRelation:
    """An OSM relation with members and tags."""
    id: str
    members: List[Tuple[str, str, str]]  # (type, ref, role)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class ParseResult:
    """Result of parsing an OSM file."""
    buildings: List[BuildingRecord]
    stats: Dict[str, int]
    warnings: List[str]


def parse_osm_file(
    filepath: str,
    projector: IProjector,
    global_seed: int = 42
) -> ParseResult:
    """
    Parse an OSM XML file and extract building footprints.

    Args:
        filepath: Path to .osm XML file
        projector: Coordinate projector (lat/lon -> local X/Y)
        global_seed: Global seed for deterministic randomness

    Returns:
        ParseResult with buildings, stats, and warnings
    """
    logger.info(f"Parsing OSM file: {filepath}")

    # Parse XML
    tree = ET.parse(filepath)
    root = tree.getroot()

    # First pass: collect all nodes
    nodes: Dict[str, OSMNode] = {}
    for node_elem in root.findall('node'):
        node_id = node_elem.get('id')
        lat = float(node_elem.get('lat'))
        lon = float(node_elem.get('lon'))
        nodes[node_id] = OSMNode(node_id, lat, lon)

    logger.debug(f"Parsed {len(nodes)} nodes")

    # Second pass: collect all ways
    ways: Dict[str, OSMWay] = {}
    for way_elem in root.findall('way'):
        way_id = way_elem.get('id')
        node_ids = [nd.get('ref') for nd in way_elem.findall('nd')]
        tags = {tag.get('k'): tag.get('v') for tag in way_elem.findall('tag')}
        ways[way_id] = OSMWay(way_id, node_ids, tags)

    logger.debug(f"Parsed {len(ways)} ways")

    # Third pass: collect all relations
    relations: Dict[str, OSMRelation] = {}
    for rel_elem in root.findall('relation'):
        rel_id = rel_elem.get('id')
        members = [
            (m.get('type'), m.get('ref'), m.get('role', ''))
            for m in rel_elem.findall('member')
        ]
        tags = {tag.get('k'): tag.get('v') for tag in rel_elem.findall('tag')}
        relations[rel_id] = OSMRelation(rel_id, members, tags)

    logger.debug(f"Parsed {len(relations)} relations")

    # Track which ways are part of building relations
    ways_in_relations: Set[str] = set()

    # Process multipolygon relations first
    buildings: List[BuildingRecord] = []
    warnings: List[str] = []
    stats = {
        'total_nodes': len(nodes),
        'total_ways': len(ways),
        'total_relations': len(relations),
        'building_relations': 0,
        'building_ways': 0,
        'buildings_parsed': 0,
        'parse_errors': 0,
    }

    # Fourth pass: process building relations
    for rel_id, relation in relations.items():
        if relation.tags.get('type') != 'multipolygon':
            continue

        # Check if it's a building
        if 'building' not in relation.tags:
            continue

        stats['building_relations'] += 1

        try:
            building = _parse_multipolygon_building(
                relation, ways, nodes, projector, global_seed
            )
            if building:
                buildings.append(building)
                stats['buildings_parsed'] += 1

                # Mark member ways as used
                for member_type, ref, role in relation.members:
                    if member_type == 'way':
                        ways_in_relations.add(ref)

        except Exception as e:
            stats['parse_errors'] += 1
            warnings.append(f"Failed to parse relation {rel_id}: {e}")
            logger.warning(f"Failed to parse relation {rel_id}: {e}")

    # Fifth pass: process standalone building ways
    for way_id, way in ways.items():
        # Skip if already processed as part of a relation
        if way_id in ways_in_relations:
            continue

        # Check if it's a building
        if 'building' not in way.tags:
            continue

        stats['building_ways'] += 1

        try:
            building = _parse_way_building(
                way, nodes, projector, global_seed
            )
            if building:
                buildings.append(building)
                stats['buildings_parsed'] += 1

        except Exception as e:
            stats['parse_errors'] += 1
            warnings.append(f"Failed to parse way {way_id}: {e}")
            logger.warning(f"Failed to parse way {way_id}: {e}")

    logger.info(
        f"Parsed {stats['buildings_parsed']} buildings "
        f"({stats['building_relations']} from relations, "
        f"{stats['building_ways']} from ways)"
    )

    return ParseResult(buildings, stats, warnings)


def _parse_multipolygon_building(
    relation: OSMRelation,
    ways: Dict[str, OSMWay],
    nodes: Dict[str, OSMNode],
    projector: IProjector,
    global_seed: int
) -> Optional[BuildingRecord]:
    """
    Parse a multipolygon relation into a BuildingRecord.

    Handles:
    - Multiple outer ways that need stitching
    - Multiple inner ways (holes) that need stitching
    - Proper winding orientation
    """
    # Separate outer and inner members
    outer_way_ids: List[str] = []
    inner_way_ids: List[str] = []

    for member_type, ref, role in relation.members:
        if member_type != 'way':
            continue
        if role == 'outer' or role == '':  # Default role is outer
            outer_way_ids.append(ref)
        elif role == 'inner':
            inner_way_ids.append(ref)

    if not outer_way_ids:
        logger.warning(f"Relation {relation.id} has no outer members")
        return None

    # Build coordinate lookup for all referenced nodes
    node_coords: Dict[str, Tuple[float, float]] = {}
    all_way_ids = outer_way_ids + inner_way_ids

    for way_id in all_way_ids:
        if way_id not in ways:
            logger.warning(f"Way {way_id} referenced in relation {relation.id} not found")
            continue

        way = ways[way_id]
        for node_id in way.node_ids:
            if node_id in node_coords:
                continue
            if node_id not in nodes:
                logger.warning(f"Node {node_id} not found")
                continue

            node = nodes[node_id]
            x, y = projector.project(node.lat, node.lon)
            node_coords[node_id] = (x, y)

    # Stitch outer ways
    outer_segments = [
        WaySegment(way_id, ways[way_id].node_ids)
        for way_id in outer_way_ids
        if way_id in ways
    ]

    if not outer_segments:
        return None

    outer_rings_ids = stitch_ways(outer_segments, validate=True)

    if not outer_rings_ids:
        logger.warning(f"Failed to stitch outer ways for relation {relation.id}")
        return None

    # Convert outer ring to points
    outer_ring = _node_ids_to_points(outer_rings_ids[0], node_coords)

    if len(outer_ring) < 3:
        logger.warning(f"Relation {relation.id} outer ring has < 3 points")
        return None

    # Remove collinear points
    outer_ring = remove_collinear_points(outer_ring)

    if len(outer_ring) < 3:
        logger.warning(f"Relation {relation.id} outer ring degenerate after simplification")
        return None

    # Ensure CCW winding for outer ring
    outer_ring = normalize_ring_orientation(outer_ring, should_be_ccw=True)

    # Process holes
    holes: List[List[Point2D]] = []

    if inner_way_ids:
        inner_segments = [
            WaySegment(way_id, ways[way_id].node_ids)
            for way_id in inner_way_ids
            if way_id in ways
        ]

        if inner_segments:
            inner_rings_ids = stitch_ways(inner_segments, validate=True)

            for ring_ids in inner_rings_ids:
                hole = _node_ids_to_points(ring_ids, node_coords)

                if len(hole) < 3:
                    continue

                hole = remove_collinear_points(hole)

                if len(hole) < 3:
                    continue

                # Ensure CW winding for holes
                hole = normalize_ring_orientation(hole, should_be_ccw=False)
                holes.append(hole)

    # Create polygon
    polygon = Polygon(outer_ring, holes)

    # Extract building attributes from tags
    return _create_building_record(relation.id, polygon, relation.tags, global_seed)


def _parse_way_building(
    way: OSMWay,
    nodes: Dict[str, OSMNode],
    projector: IProjector,
    global_seed: int
) -> Optional[BuildingRecord]:
    """Parse a simple way into a BuildingRecord."""
    if len(way.node_ids) < 3:
        return None

    # Convert nodes to points
    points: List[Point2D] = []
    for node_id in way.node_ids:
        if node_id not in nodes:
            logger.warning(f"Node {node_id} not found for way {way.id}")
            continue

        node = nodes[node_id]
        x, y = projector.project(node.lat, node.lon)
        points.append(Point2D(x, y))

    if len(points) < 3:
        return None

    # Close ring if not already closed
    if points[0].x != points[-1].x or points[0].y != points[-1].y:
        points.append(points[0])

    # Remove collinear points
    points = remove_collinear_points(points)

    if len(points) < 3:
        return None

    # Ensure CCW winding
    points = normalize_ring_orientation(points, should_be_ccw=True)

    # Create polygon (no holes for simple ways)
    polygon = Polygon(points, [])

    return _create_building_record(way.id, polygon, way.tags, global_seed)


def _node_ids_to_points(
    node_ids: List[str],
    node_coords: Dict[str, Tuple[float, float]]
) -> List[Point2D]:
    """Convert a list of node IDs to Point2D list."""
    points = []
    for node_id in node_ids:
        if node_id in node_coords:
            x, y = node_coords[node_id]
            points.append(Point2D(x, y))
    return points


def _create_building_record(
    osm_id: str,
    polygon: Polygon,
    tags: Dict[str, str],
    global_seed: int
) -> BuildingRecord:
    """Create a BuildingRecord from polygon and OSM tags."""
    # Determine category
    building_type = tags.get('building', 'yes')
    category = BuildingCategory.from_osm_tag(building_type)

    # Determine roof type
    roof_shape = tags.get('roof:shape')
    roof_type = RoofType.from_osm_tag(roof_shape)

    # Parse height and floors
    # These must be kept in sync: height_m = floors * 3.0 (when no explicit height tag)
    has_explicit_height = _parse_height(tags) is not None
    height_m = _parse_height(tags)
    if height_m is None:
        # No explicit height tag - estimate from footprint area
        estimated_floors, height_m = BuildingRecord.estimate_height(polygon.area(), category)
    else:
        estimated_floors = None

    # Parse floors (may override estimated_floors with building:levels tag)
    floors = _parse_floors(tags, height_m, estimated_floors)

    # Sync height_m with floors when there's no explicit height tag.
    # Bug fix: when building:levels overrides estimated_floors but height
    # comes from estimate_height(), they can diverge. Example:
    #   HOUSE with building:levels=1 (no height tag):
    #     estimate_height() -> (2, 6.0)  [HOUSE always 2 floors]
    #     _parse_floors() -> 1           [from building:levels tag]
    #     Result: floors=1, height_m=6.0 -> 6m wall with 1-floor UV = stretched!
    # Fix: recompute height_m = floors * 3.0 when height was estimated
    if not has_explicit_height and floors != estimated_floors:
        height_m = floors * 3.0

    # Clamp absurd OSM values (e.g. building:levels="233" typo on a house).
    floors, height_m = _clamp_building_size(
        floors, height_m, tags, osm_id, has_explicit_height
    )

    # Parse roof pitch
    roof_pitch_deg = _parse_roof_pitch(tags)

    # Parse roof direction
    roof_direction_deg, roof_direction_source = _parse_roof_direction(tags)

    # Compute seed deterministically (hashlib is stable across Python processes)
    stable_hash = int(hashlib.sha256(osm_id.encode()).hexdigest(), 16) % (2**31)
    seed = global_seed + stable_hash

    return BuildingRecord(
        osm_id=osm_id,
        category=category,
        footprint=polygon,
        floors=floors,
        height_m=height_m,
        roof_type=roof_type,
        roof_pitch_deg=roof_pitch_deg,
        roof_direction_deg=roof_direction_deg,
        roof_direction_source=roof_direction_source,
        floor_z=0.0,  # Will be computed later
        seed=seed,
    )


def _clamp_building_size(
    floors: int,
    height_m: float,
    tags: Dict[str, str],
    osm_id: str,
    has_explicit_height: bool,
) -> Tuple[int, float]:
    """Clamp clearly-bad OSM sizes (typos) to sane limits.

    OSM is community-edited, so a single bad tag (e.g. building:levels="233"
    on a house) produces an absurd ~700m skyscraper. We clamp floors/height to
    config caps and emit a WARNING with the OSM id + address so it can be
    reported upstream. Generous caps never clip a real building in low-rise
    scenery (The Shard, tallest in the UK, is 72 floors / 310m).
    """
    from ..config import MAX_BUILDING_LEVELS, MAX_BUILDING_HEIGHT_M

    orig_floors, orig_height = floors, height_m
    clamped = False

    if floors > MAX_BUILDING_LEVELS:
        floors = MAX_BUILDING_LEVELS
        clamped = True
        if not has_explicit_height:
            height_m = floors * 3.0

    if height_m > MAX_BUILDING_HEIGHT_M:
        height_m = MAX_BUILDING_HEIGHT_M
        floors = max(1, min(floors, round(height_m / 3.0)))
        clamped = True

    if clamped:
        addr_parts = [
            tags.get('addr:housenumber', ''),
            tags.get('addr:street', ''),
            tags.get('addr:city', ''),
        ]
        addr = ' '.join(p for p in addr_parts if p).strip() or 'no address'
        logger.warning(
            "Clamped absurd OSM size on way %s (%s, building=%s): "
            "floors %s->%s, height %.1fm->%.1fm (likely an OSM tag typo)",
            osm_id, addr, tags.get('building', 'yes'),
            orig_floors, floors, orig_height, height_m,
        )

    return floors, height_m


def _parse_height(tags: Dict[str, str]) -> Optional[float]:
    """Parse building height from OSM tags."""
    # Try height tag
    if 'height' in tags:
        try:
            height_str = tags['height'].replace('m', '').strip()
            return float(height_str)
        except ValueError:
            pass

    # Try building:height
    if 'building:height' in tags:
        try:
            height_str = tags['building:height'].replace('m', '').strip()
            return float(height_str)
        except ValueError:
            pass

    return None


def _parse_floors(
    tags: Dict[str, str],
    height_m: float,
    estimated_floors: Optional[int] = None
) -> int:
    """Parse number of floors from OSM tags."""
    # Try building:levels
    if 'building:levels' in tags:
        try:
            return int(tags['building:levels'])
        except ValueError:
            pass

    # Try levels
    if 'levels' in tags:
        try:
            return int(tags['levels'])
        except ValueError:
            pass

    # Use pre-computed estimate if available
    if estimated_floors is not None:
        return estimated_floors

    # Estimate from height (assuming 3m per floor)
    # Use round() instead of int() to avoid truncation errors:
    # int(5.0/3.0) = 1 (wrong), round(5.0/3.0) = 2 (correct)
    # int(11.0/3.0) = 3 (wrong), round(11.0/3.0) = 4 (correct)
    # Buildings with non-exact-3m heights (e.g., height=5, height=11)
    # were getting one fewer floor than expected, causing UV mapping
    # to show fewer texture floors than the wall height warranted.
    return max(1, round(height_m / 3.0))


def _parse_roof_pitch(tags: Dict[str, str]) -> float:
    """Parse roof pitch from OSM tags."""
    from ..config import ROOF_PITCH_DEFAULT, ROOF_PITCH_MIN, ROOF_PITCH_MAX

    if 'roof:angle' in tags:
        try:
            pitch = float(tags['roof:angle'])
            return max(ROOF_PITCH_MIN, min(ROOF_PITCH_MAX, pitch))
        except ValueError:
            pass

    return ROOF_PITCH_DEFAULT


def _parse_roof_direction(
    tags: Dict[str, str]
) -> Tuple[Optional[float], RoofDirectionSource]:
    """Parse roof ridge direction from OSM tags."""
    if 'roof:direction' in tags:
        try:
            direction = float(tags['roof:direction'])
            return (direction % 360, RoofDirectionSource.OSM_TAG)
        except ValueError:
            pass

    if 'roof:ridge:direction' in tags:
        try:
            direction = float(tags['roof:ridge:direction'])
            return (direction % 360, RoofDirectionSource.OSM_TAG)
        except ValueError:
            pass

    # Will be computed from longest axis later
    return (None, RoofDirectionSource.LONGEST_AXIS)
