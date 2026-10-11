"""
Aerialway parser for Condor Buildings Generator.

Finds OSM ``aerialway=*`` ways (cable cars, gondolas, chair lifts, drag lifts,
surface tows) and their ``aerialway=pylon`` supports, and projects them to Condor
patch coordinates. Mirrors ``powerline_parser.py`` - an aerialway has the same
structure as a power line (route = way, supports = nodes), so the generator can
reuse the powerline geometry helpers.

Phase 1: route + pylons only (cabins/chairs come in phase 2).
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Dict, Optional

from ..projection import IProjector
from ..config import PATCH_HALF

logger = logging.getLogger(__name__)

# Cabin-type aerialways -> big cabin pylon (Pylon_AerialCab) + Telecabine.
CABIN_VALUES = {"cable_car", "gondola"}
# Chair / drag / surface lifts -> chair pylon (Pylon_Aerialway) + Aerialway_Cab.
CHAIR_VALUES = {
    "chair_lift", "drag_lift", "t-bar", "j-bar", "platter",
    "mixed_lift", "magic_carpet", "rope_tow", "zip_line",
}
AERIALWAY_VALUES = CABIN_VALUES | CHAIR_VALUES


@dataclass
class AerialPoint:
    """A single vertex along an aerialway way (maybe a pylon support)."""
    node_id: str
    x: float
    y: float
    in_patch: bool
    is_support: bool                 # tagged aerialway=pylon -> pylon location


@dataclass
class AerialLine:
    """One OSM aerialway way, projected to Condor coordinates."""
    way_id: str
    aerialway_value: str             # raw OSM value (cable_car / chair_lift / ...)
    line_class: str                  # 'cabin' | 'chair'
    pylon_type: str                  # 'Pylon_AerialCab' | 'Pylon_Aerialway'
    name: Optional[str]
    points: List[AerialPoint]


@dataclass
class AerialwayParseResult:
    lines: List[AerialLine]
    stats: Dict[str, int]
    warnings: List[str]


def _classify(aerialway_value: str):
    """Map the OSM aerialway value to (line_class, pylon_type)."""
    if aerialway_value in CABIN_VALUES:
        return "cabin", "Pylon_AerialCab"
    return "chair", "Pylon_Aerialway"


def parse_aerialways(filepath: str, projector: IProjector,
                     patch_half: float = PATCH_HALF) -> AerialwayParseResult:
    """Parse aerialway ways from an OSM file into projected AerialLine records."""
    logger.info(f"Parsing aerialways from: {filepath}")
    tree = ET.parse(filepath)
    root = tree.getroot()

    # Pass 1: node coordinates + which nodes are aerialway pylons.
    node_coords: Dict[str, tuple] = {}
    node_is_pylon: Dict[str, bool] = {}
    for node_elem in root.findall("node"):
        nid = node_elem.get("id")
        node_coords[nid] = (float(node_elem.get("lat")), float(node_elem.get("lon")))
        for tag in node_elem.findall("tag"):
            if tag.get("k") == "aerialway" and tag.get("v") == "pylon":
                node_is_pylon[nid] = True

    # Pass 2: aerialway ways.
    lines: List[AerialLine] = []
    warnings: List[str] = []
    for way_elem in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way_elem.findall("tag")}
        av = tags.get("aerialway")
        if av not in AERIALWAY_VALUES:
            continue

        points: List[AerialPoint] = []
        for nd in way_elem.findall("nd"):
            nid = nd.get("ref")
            if nid not in node_coords:
                warnings.append(f"way {way_elem.get('id')}: missing node {nid}")
                continue
            lat, lon = node_coords[nid]
            x, y = projector.project(lat, lon)
            in_patch = -patch_half <= x <= patch_half and -patch_half <= y <= patch_half
            points.append(AerialPoint(nid, x, y, in_patch, nid in node_is_pylon))

        if len(points) < 2:
            warnings.append(f"way {way_elem.get('id')}: < 2 usable points, skipped")
            continue

        line_class, pylon_type = _classify(av)
        lines.append(AerialLine(
            way_id=way_elem.get("id"),
            aerialway_value=av,
            line_class=line_class,
            pylon_type=pylon_type,
            name=tags.get("name"),
            points=points,
        ))

    stats = {
        "lines_total": len(lines),
        "lines_cabin": sum(1 for l in lines if l.line_class == "cabin"),
        "lines_chair": sum(1 for l in lines if l.line_class == "chair"),
        "supports_total": sum(sum(1 for p in l.points if p.is_support) for l in lines),
    }
    logger.info(
        f"Parsed {stats['lines_total']} aerialways "
        f"({stats['lines_cabin']} cabin / {stats['lines_chair']} chair), "
        f"{stats['supports_total']} pylons"
    )
    return AerialwayParseResult(lines=lines, stats=stats, warnings=warnings)
