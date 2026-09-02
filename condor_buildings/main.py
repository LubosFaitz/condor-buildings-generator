"""
Condor Buildings Generator - Main CLI

Generates 3D building meshes from OSM data for Condor 3 flight simulator.

Usage:
    python -m condor_buildings.main --patch-dir <path> --patch-id <id>

Example:
    python -m condor_buildings.main --patch-dir ./CLT3 --patch-id 036019
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from pathlib import Path

from . import __version__
from .config import (
    PipelineConfig, RoofSelectionMode,
    PATCH_HALF, PATCH_SIZE, FLAT_ROOF_ORTHOPHOTO_V_FLIP,
)
from .projection import create_projector
from .io.patch_metadata import load_patch_metadata
from .io.terrain_loader import load_terrain
from .io.osm_parser import parse_osm_file
from .io.obj_exporter import export_obj_lod0, export_obj_lod1, validate_obj_file, export_mesh_groups
from .processing.spatial_index import GridSpatialIndex
from .processing.floor_z_solver import FloorZSolver
from .processing.footprint import process_footprint, GabledEligibility
from .processing.patch_filter import filter_buildings_by_patch_bounds, FilterReason
from .processing.mesh_grouper import MeshGrouper
from .generators.building_generator import (
    generate_building_lod0,
    generate_building_lod1,
    generate_building_separated,
    select_roof_type,
    configure_generator,
)
from .models.building import RoofType, RoofDirectionSource
from .models.mesh import MeshData


@dataclass
class PipelineStats:
    """Statistics from the pipeline run."""
    buildings_parsed: int = 0
    buildings_filtered_edge: int = 0  # Filtered due to patch edge
    buildings_filtered_zone: int = 0  # Dropped inside an airfield / solar farm
    buildings_filtered_water: int = 0  # Dropped for standing wholly on water
    buildings_processed: int = 0
    buildings_skipped: int = 0
    gabled_eligible: int = 0  # Count of buildings eligible for gabled roof (geometry)
    house_scale_pass: int = 0  # Count of buildings passing house-scale check
    house_scale_fail: int = 0  # Count of buildings failing house-scale check
    gabled_roofs: int = 0
    hipped_roofs: int = 0
    flat_roofs: int = 0
    gabled_fallbacks: int = 0
    hipped_fallbacks: int = 0
    lod0_vertices: int = 0
    lod0_faces: int = 0
    lod1_vertices: int = 0
    lod1_faces: int = 0
    # Optimization stats
    lod0_vertices_before_optimize: int = 0
    lod1_vertices_before_optimize: int = 0
    lod0_vertices_removed: int = 0
    lod1_vertices_removed: int = 0
    # Degenerate faces (collapsed edges / duplicate-vertex faces) dropped during
    # optimization. >0 means a patch had geometry that would freeze Blender's
    # Edit Mode and bloat the Condor mesh; now auto-cleaned (v0.8.13).
    degenerate_faces_removed: int = 0
    terrain_triangles: int = 0
    processing_time_ms: int = 0
    filtered_building_ids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Powerlines (optional 'pylones' object)
    powerline_towers: int = 0
    powerline_cables: int = 0
    powerline_lines: int = 0
    wind_turbines: int = 0
    aerialways: int = 0  # aerialway pylons merged into 'pylones' (0 = none)


@dataclass
class VertexCountStats:
    """Statistics about footprint vertex counts."""
    count_4_vertices: int = 0  # Rectangles
    count_5_to_6_vertices: int = 0
    count_7_to_8_vertices: int = 0
    count_9_plus_vertices: int = 0


@dataclass
class PipelineReport:
    """Report from pipeline run."""
    patch_id: str
    version: str
    success: bool
    stats: PipelineStats
    output_files: List[str]
    errors: List[str] = field(default_factory=list)
    roof_direction_stats: Dict[str, int] = field(default_factory=dict)
    fallback_reasons: Dict[str, int] = field(default_factory=dict)
    vertex_count_stats: Dict[str, int] = field(default_factory=dict)
    config_used: Dict[str, any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """
    Complete result of pipeline execution.

    Supports two output modes:
    - 'file': Writes OBJ files to disk (CLI mode)
    - 'memory': Returns mesh data in memory (Blender mode)

    Attributes:
        success: Whether pipeline completed without errors
        report: Detailed statistics and metadata
        lod0_path: Path to LOD0 OBJ file (file mode only)
        lod1_path: Path to LOD1 OBJ file (file mode only)
        lod0_meshes: List of LOD0 MeshData (memory mode, legacy)
        lod1_meshes: List of LOD1 MeshData (memory mode, legacy)
        grouped_lod0: Dict mapping group name to MeshData (memory mode, new)
        grouped_lod1: Dict mapping group name to MeshData (memory mode, new)
    """
    success: bool
    report: PipelineReport

    # File mode outputs
    lod0_path: Optional[str] = None
    lod1_path: Optional[str] = None

    # Memory mode outputs (for Blender integration)
    lod0_meshes: Optional[List] = None  # List[MeshData] - legacy
    lod1_meshes: Optional[List] = None  # List[MeshData] - legacy

    # Grouped meshes (new: Dict[str, MeshData])
    grouped_lod0: Optional[Dict] = None
    grouped_lod1: Optional[Dict] = None


def setup_logging(verbose: bool = False, log_file: Optional[str] = None) -> None:
    """
    Configure logging to console and optionally to file.

    Args:
        verbose: If True, use DEBUG level; otherwise INFO
        log_file: Optional path to log file. If provided, logs will be written to file.
    """
    level = logging.DEBUG if verbose else logging.INFO

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear any existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (if log_file specified)
    if log_file:
        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        file_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)


def _dedupe_reversed_faces(mesh: MeshData) -> None:
    """
    Remove reversed-duplicate faces in place, keeping the FIRST occurrence.

    Hipped roofs are generated CCW (normals up) and then duplicated with reversed
    winding (double_sided_roof). In file mode there is no Blender validate()/recalc
    to clean that up, so the raw mesh shows both up- and down-facing faces
    ("weird edges / flipped normals"). Keeping the first occurrence of each
    vertex-set keeps the original upward faces -> a clean single-sided roof.
    """
    seen = set()
    new_faces = []
    new_uvs = []
    has_uv = len(mesh.face_uvs) == len(mesh.faces)
    for i, face in enumerate(mesh.faces):
        key = frozenset(face)
        if key in seen:
            continue
        seen.add(key)
        new_faces.append(face)
        if has_uv:
            new_uvs.append(mesh.face_uvs[i])
    mesh.faces = new_faces
    if has_uv:
        mesh.face_uvs = new_uvs


def _merge_gabled_for_export(groups: Dict[str, MeshData]) -> Dict[str, MeshData]:
    # File-mode export: merge the separate pitched-roof groups (gabled LOD0 and
    # hipped) back into 'houses' so they carry the houses texture. Without this,
    # 'hipped_roofs' stays a separate object with no material/texture in the
    # Landscape Editor (file mode has no MTL; the LE assigns by object name).
    #
    # File mode skips the Blender import step, so it must reproduce here what
    # mesh_converter does on import:
    #   - gabled LOD0: duplicate faces with reversed winding (double-sided),
    #   - hipped: drop the reversed duplicates, keep the original upward faces.
    from .generators.roof_gabled import _duplicate_faces_reversed

    gabled = groups.get('gabled_roofs_lod0')
    hipped = groups.get('hipped_roofs')
    has_gabled = gabled is not None and not gabled.is_empty()
    has_hipped = hipped is not None and not hipped.is_empty()
    if not has_gabled and not has_hipped:
        return groups

    if has_gabled:
        # Make the gabled roofs double-sided (visible from below), like import.
        _duplicate_faces_reversed(gabled, 0, len(gabled.faces), 0)
    if has_hipped:
        # Clean hipped roofs to single-sided upward faces, like import.
        _dedupe_reversed_faces(hipped)

    merged_houses = MeshData()
    if groups.get('houses') and not groups['houses'].is_empty():
        merged_houses.merge(groups['houses'])
    if has_gabled:
        merged_houses.merge(gabled)
    if has_hipped:
        merged_houses.merge(hipped)
    result = {k: v for k, v in groups.items() if k not in ('gabled_roofs_lod0', 'hipped_roofs', 'houses')}
    result['houses'] = merged_houses
    return result


def _apply_terrain_orthophoto_uvs(
    groups: Dict[str, MeshData],
    patch_half: float = PATCH_HALF,
    patch_size: float = PATCH_SIZE,
    v_flip: bool = FLAT_ROOF_ORTHOPHOTO_V_FLIP,
) -> int:
    """
    Normalize the merged 'flat_roof' group's UVs into patch [0,1] space.

    In flat_roof_merge mode the roof UVs are raw world coordinates (u=X, v=Y).
    The Condor patch spans [-patch_half, +patch_half] in both axes, so mapping
    u'=(X+patch_half)/patch_size, v'=(Y+patch_half)/patch_size makes the terrain
    orthophoto (t<patch>.dds) line up 1:1 with the ground beneath each roof.

    Only the 'flat_roof' group is affected (merge mode). Returns the number of
    UVs rewritten (0 if the group is absent/empty).
    """
    mesh = groups.get('flat_roof')
    if mesh is None or mesh.is_empty() or not mesh.uvs:
        return 0
    new_uvs = []
    for u, v in mesh.uvs:
        nu = (u + patch_half) / patch_size
        nv = (v + patch_half) / patch_size
        if v_flip:
            nv = 1.0 - nv
        new_uvs.append((nu, nv))
    mesh.uvs = new_uvs
    return len(new_uvs)


def _generate_powerline_group(osm_path, projector, terrain, draw_balls=False):
    """
    Build the optional 'pylones' mesh (towers + catenary cables) for a patch.

    Powerlines are parsed from the SAME OSM file as the buildings and generated in
    pipeline coordinates, so they share the patch frame and the Condor axis
    transform and ride in the same object file (Wiek Q18). The current pylon assets
    are Wiek's LOD1 towers; until he delivers LOD0 ones the same geometry is used
    for both LODs, as an independent copy each so any later LOD-specific edit can't
    cross-contaminate.

    Returns (lod0_mesh, lod1_mesh, PowerlineMeshStats), or (None, None, None) when
    the patch has no in-range powerlines (so the caller skips the group).
    """
    from .io.powerline_parser import parse_powerlines, read_airport_zones
    from .generators.powerlines import generate_powerline_meshes

    parse_result = parse_powerlines(osm_path, projector)
    if not parse_result.lines:
        return None, None, None

    # Airport ball zones: prefer the shared airports.json (3x3 search across
    # neighbouring patches), else fall back to this patch's own OSM aeroway.
    airports_json = os.path.join(os.path.dirname(osm_path), "airport", "airports.json")
    zones = read_airport_zones(airports_json, projector)
    if not zones:
        zones = parse_result.airport_zones

    # Substation outlines (OSM power=substation). A large/medium tower must not stand
    # inside a switchyard -- only small pylons may -- so the generator skips those nodes.
    sub_polys = _read_substation_polygons(osm_path, projector)

    # Single pass: LOD0 (towers + cables + balls) and LOD1 (large+medium pylons
    # only) are built together - the terrain foot_z per node is computed once.
    mesh_lod0, mesh_lod1, pl_stats = generate_powerline_meshes(
        parse_result.lines, terrain,
        draw_balls=draw_balls, airport_zones=zones,
        substation_polygons=sub_polys,
    )
    if mesh_lod0.is_empty():
        return None, None, None
    return mesh_lod0, mesh_lod1, pl_stats


def _read_substation_polygons(osm_path, projector, min_size_m=100.0):
    """Substation fences from OSM (power=substation), as (x, y) rings in patch coords.

    Only real switchyards (>= min_size_m across); the little distribution kiosks are
    skipped. Pure ElementTree, so it works with or without Blender. Never raises.
    """
    import xml.etree.ElementTree as _ET
    try:
        root = _ET.parse(osm_path).getroot()
    except Exception:
        return []
    coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
              for n in root.findall("node")}
    rings = []
    for way in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
        if tags.get("power") != "substation":
            continue
        ring = [projector.project(*coords[nd.get("ref")])
                for nd in way.findall("nd") if nd.get("ref") in coords]
        if len(ring) < 3:
            continue
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if max(max(xs) - min(xs), max(ys) - min(ys)) >= min_size_m:
            rings.append(ring)
    return rings


# Water from the patch texture's ALPHA channel (t<patch>.dds, DXT3): water = alpha below
# this, and the DDS byte rows run top-first so V is flipped. Same values blender/bridges.py
# verified on t036024.dds; the reader is repeated here on purpose - bridges.py is a
# removable module and main.py must not depend on it.
WATER_ALPHA_THRESHOLD = 0.5
WATER_IS_LOW_ALPHA = True
ORTHO_V_FLIP = True
PATCH_HALF_M = 2880.0
PATCH_SIZE_M = 5760.0


def _load_water_sampler(path):
    """Return is_water(x, y) read straight from the patch texture's alpha channel, or None
    when the file is missing or is not a DXT3 DDS (then nothing is excluded). Only the
    alpha of each 4x4 block is decoded, on demand. Never raises."""
    if not path or not os.path.exists(path):
        return None
    try:
        import struct
        d = open(path, "rb").read()
        if d[:4] != b"DDS " or d[84:88] != b"DXT3":
            return None
        h = struct.unpack("<I", d[12:16])[0]
        w = struct.unpack("<I", d[16:20])[0]
        bw, bh = w // 4, h // 4
        base = 128
        if len(d) < base + bw * bh * 16:
            return None
    except Exception:
        return None

    thr = WATER_ALPHA_THRESHOLD * 255.0

    def is_water(x, y):
        if not (-PATCH_HALF_M <= x <= PATCH_HALF_M and -PATCH_HALF_M <= y <= PATCH_HALF_M):
            return False                     # outside the patch = treat as land
        u = (x + PATCH_HALF_M) / PATCH_SIZE_M
        v = (y + PATCH_HALF_M) / PATCH_SIZE_M
        if ORTHO_V_FLIP:
            v = 1.0 - v
        bx = min(max(int(u * bw), 0), bw - 1)
        by = min(max(int(v * bh), 0), bh - 1)
        off = base + (by * bw + bx) * 16     # DXT3: 8 alpha bytes, then 8 colour bytes
        tot = 0
        for k in range(8):
            b = d[off + k]
            tot += (b & 0x0F) + ((b >> 4) & 0x0F)
        a = (tot * 17) // 16                 # mean 4-bit alpha -> 0..255
        return (a < thr) if WATER_IS_LOW_ALPHA else (a > thr)

    return is_water


def _find_patch_texture(config):
    """The patch texture t<patch>.dds, wherever this scenery keeps it, or None."""
    ls = os.path.dirname(os.path.dirname(config.patch_dir))   # ...\Working\HeightMaps -> ...
    names = f"t{config.patch_id}.dds"
    for cand in (
        os.path.join(ls, "Textures", names),
        os.path.join(config.patch_dir, names),
        os.path.join(os.path.dirname(config.patch_dir), "Textures", names),
        os.path.join(ls, "Working", "Textures", names),
    ):
        if os.path.exists(cand):
            return cand
    return None


# Small airfields often have NO aerodrome outline in OSM - just a runway way (e.g. LKRY
# Rokycany). The runway then becomes a corridor: this far to each side of the strip and
# this far beyond both ends, which covers the hangars and the apron next to it.
RUNWAY_HALF_WIDTH_M = 200.0
RUNWAY_END_MARGIN_M = 200.0

# Last resort: an aerodrome mapped only as a POINT, with no outline AND no runway. The
# zone becomes a square of this half-size around it. Kept modest on purpose - a bigger
# square would start eating a neighbouring village.
AERODROME_POINT_HALF_M = 600.0


def _assemble_exclusion_rings(segments):
    """Chain open way segments (lists of (x, y)) into CLOSED rings by matching shared
    endpoints. A single already-closed way comes back as one ring. Needed because a
    multipolygon relation's outer boundary can be split across several member ways.
    Same approach as blender/solar.py uses for solar farm relations.
    """
    segs = [list(s) for s in segments if len(s) >= 2]
    used = [False] * len(segs)
    rings = []

    def same(a, b):
        return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        ring = list(segs[i])
        extended = True
        while extended and not same(ring[0], ring[-1]):
            extended = False
            for j in range(len(segs)):
                if used[j]:
                    continue
                s = segs[j]
                if same(ring[-1], s[0]):
                    ring.extend(s[1:]); used[j] = extended = True
                elif same(ring[-1], s[-1]):
                    ring.extend(reversed(s[:-1])); used[j] = extended = True
                elif same(ring[0], s[-1]):
                    ring[:0] = s[:-1]; used[j] = extended = True
                elif same(ring[0], s[0]):
                    ring[:0] = list(reversed(s[1:])); used[j] = extended = True
        rings.append(ring)
    return rings


def _read_exclusion_zones(osm_path, projector):
    """Areas where NO auto-generated object may stand, as (x, y) rings in patch coords.

    Two kinds of area:
      * airfields  - aeroway=aerodrome (Condor sceneries usually already have their own
        hand-made hangars/buildings there, so anything generated only has to be deleted)
      * ground solar farms - power=plant / power=generator with a solar source (the solar
        tool builds the panels itself; rooftop panels are NOT a farm and are skipped,
        same rule as blender/solar.py)

    Both are read from simple ways AND from multipolygon relations (a big aerodrome or a
    farm like FVE Stribro is a relation, not a single way). An aerodrome mapped only as a
    POINT gets a square of AERODROME_POINT_HALF_M around it, unless it already lies inside
    an outline that was read above.

    Pure ElementTree, so it works with or without Blender. Never raises - on any problem
    it returns an empty list and generation simply runs as before.
    """
    import xml.etree.ElementTree as _ET
    try:
        root = _ET.parse(osm_path).getroot()
    except Exception:
        return []

    def is_zone(tags):
        """An airfield, or a solar farm recognised EXACTLY the way blender/solar.py
        recognises it (_is_solar_tags) - same tags, same rules, nothing added."""
        if tags.get("aeroway") == "aerodrome":
            return True
        if tags.get("location") == "roof" or tags.get("building"):
            return False
        return ((tags.get("power") == "plant" and tags.get("plant:source") == "solar")
                or (tags.get("power") == "generator"
                    and tags.get("generator:source") == "solar"))

    coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
              for n in root.findall("node")}

    # Every way's projected points, indexed by id (relation members reference these).
    way_pts = {}
    for way in root.findall("way"):
        pts = [projector.project(*coords[nd.get("ref")])
               for nd in way.findall("nd") if nd.get("ref") in coords]
        if pts:
            way_pts[way.get("id")] = pts

    rings = []
    for way in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
        if not is_zone(tags):
            continue
        pts = way_pts.get(way.get("id"))
        if pts and len(pts) >= 3:
            rings.append(pts)

    for rel in root.findall("relation"):
        tags = {t.get("k"): t.get("v") for t in rel.findall("tag")}
        if not is_zone(tags):
            continue
        outers = [way_pts[m.get("ref")] for m in rel.findall("member")
                  if m.get("type") == "way" and m.get("role") in ("outer", "")
                  and m.get("ref") in way_pts]
        for ring in _assemble_exclusion_rings(outers):
            if len(ring) >= 3:
                rings.append(ring)

    # Runways: a corridor along the strip. Small airfields (LKRY Rokycany) have no
    # aerodrome outline at all, only this way; for a big airport the corridor simply
    # lies inside the outline already collected, which does no harm.
    import math as _math
    for way in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
        if tags.get("aeroway") != "runway":
            continue
        pts = way_pts.get(way.get("id"))
        if not pts or len(pts) < 2:
            continue
        (ax, ay), (bx, by) = pts[0], pts[-1]
        dx, dy = bx - ax, by - ay
        length = _math.hypot(dx, dy)
        if length < 50.0:                # taxiway / mis-tagged stub
            continue
        ux, uy = dx / length, dy / length        # along the strip
        px, py = -uy, ux                         # across the strip
        e, w = RUNWAY_END_MARGIN_M, RUNWAY_HALF_WIDTH_M
        sx, sy = ax - ux * e, ay - uy * e        # extended ends
        ex, ey = bx + ux * e, by + uy * e
        rings.append([(sx + px * w, sy + py * w), (ex + px * w, ey + py * w),
                      (ex - px * w, ey - py * w), (sx - px * w, sy - py * w)])

    # Airfields that have no outline and no runway at all - only a node. Skipped when the
    # node sits in a zone already collected (an airfield is usually mapped BOTH ways).
    from .generators.powerlines import _point_in_polys
    half = AERODROME_POINT_HALF_M
    for node in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in node.findall("tag")}
        if tags.get("aeroway") != "aerodrome":
            continue
        nid = node.get("id")
        if nid not in coords:
            continue
        x, y = projector.project(*coords[nid])
        if rings and _point_in_polys(x, y, rings):
            continue
        rings.append([(x - half, y - half), (x + half, y - half),
                      (x + half, y + half), (x - half, y + half)])
    return rings


def _generate_aerialway_group(osm_path, projector, terrain,
                              heightmaps_dir=None, patch_id=None,
                              translate_x=0.0, translate_y=0.0,
                              exclude_zones=None):
    """
    Build the aerialway mesh (cable cars / chair lifts) for a patch from OSM
    aerialway=* ways: a pylon at every support + a straight cable, as a single
    'aerialway' object (Pylons.dds). Same geometry for LOD0 and LOD1 for now.

    Border pylons (just outside the patch) take their foot height from the
    ADJACENT patch's terrain (NeighborTerrain), so they line up with that patch.

    Returns (lod0_mesh, lod1_mesh, stats), or (None, None, None) when the patch
    has no aerialways.
    """
    from .io.aerialway_parser import parse_aerialways
    from .generators.aerialway import (
        generate_aerialway_meshes, load_aerialway_templates, NeighborTerrain,
    )

    parse_result = parse_aerialways(osm_path, projector)
    if not parse_result.lines:
        return None, None, None

    # 'Exclude airports and solar farms': a lift whose MIDPOINT falls in an excluded
    # area is dropped whole - cutting out single pylons would leave a torn cable.
    lines = parse_result.lines
    if exclude_zones:
        from .generators.powerlines import _point_in_polys
        kept = []
        for line in lines:
            mid = line.points[len(line.points) // 2] if line.points else None
            if mid is not None and _point_in_polys(mid.x, mid.y, exclude_zones):
                continue
            kept.append(line)
        lines = kept
        if not lines:
            return None, None, None

    neighbor = None
    if heightmaps_dir and patch_id is not None:
        neighbor = NeighborTerrain(heightmaps_dir, patch_id, translate_x, translate_y)

    # Load templates once and reuse for both LODs (avoids re-reading the OBJs).
    templates = load_aerialway_templates()

    mesh, aw_stats = generate_aerialway_meshes(
        lines, terrain, templates=templates, neighbor=neighbor)
    if mesh.is_empty():
        return None, None, None

    # LOD1: identical, except the cabin pylon uses its low-poly model (if present).
    mesh_lod1, _ = generate_aerialway_meshes(
        lines, terrain, templates=templates, neighbor=neighbor, low=True)
    return mesh, mesh_lod1, aw_stats


# A turbine's rotor spans 69.1 m (turbine_blades.obj), so two turbines mapped on
# the same spot - or a few metres apart, which OSM does have - end up with their
# blades growing through each other. Keep them this far apart instead.
TURBINE_MIN_GAP = 75.0


def _spread_turbines(turbines, min_gap=TURBINE_MIN_GAP):
    """Drop turbines that stand closer to an already kept one than ``min_gap``.

    OSM sometimes maps one turbine twice (or two of them a few metres apart), and
    with a 69 m rotor the blades then grow through each other. Keeping only the
    first of such a pair leaves one clean turbine instead of two overlapping ones.
    """
    if len(turbines) < 2:
        return turbines

    import math as _math

    kept = []
    for t in turbines:
        if any(_math.hypot(t.x - k.x, t.y - k.y) < min_gap for k in kept):
            continue
        kept.append(t)
    return kept


def _generate_wind_turbines_group(osm_path, projector, terrain, bake_world=False, seed=0,
                                  exclude_zones=None):
    """
    Build wind turbine meshes for a patch from OSM power=generator nodes.

    Returns (lod0_groups, lod1_groups, count) or (None, None, 0) when no turbines.
    lod0_groups / lod1_groups are dicts {name: MeshData} ready to update() into
    the main group dicts.

    bake_world=True (file mode): all turbines are merged into one 'wind_turbine'
    object with real positions baked into the geometry, rotated by one random
    yaw shared across the whole patch (deterministic from seed).
    bake_world=False (Blender import): each turbine stays a separate object with
    its origin at the base, so it can be rotated individually and merged later.
    """
    from .io.powerline_parser import parse_powerlines
    from .generators.powerlines import generate_wind_turbines_mesh

    parse_result = parse_powerlines(osm_path, projector)
    turbines = parse_result.turbines
    if not turbines:
        return None, None, 0

    # 'Exclude airports and solar farms': no turbine inside an excluded area.
    if exclude_zones:
        from .generators.powerlines import _point_in_polys
        turbines = [t for t in turbines
                    if not _point_in_polys(t.x, t.y, exclude_zones)]
        if not turbines:
            return None, None, 0

    turbines = _spread_turbines(turbines)

    yaw = 0.0
    if bake_world:
        import random
        import math as _math
        yaw = random.Random(seed).uniform(0.0, 2.0 * _math.pi)

    # Single pass: LOD0 (detailed model) and LOD1 (low model) at once. The
    # placement (foot_z, yaw, random blade spin) is computed once per turbine and
    # stamped into both LODs, so the terrain lookups aren't repeated.
    meshes0, meshes1, count = generate_wind_turbines_mesh(
        turbines, terrain, bake_world=bake_world, yaw=yaw, seed=seed
    )
    if not meshes0:
        return None, None, 0

    groups0 = {}
    groups1 = {}
    for i, mesh in enumerate(meshes0):
        key = f"wind_turbine_{i}" if i > 0 else "wind_turbine"
        groups0[key] = mesh
    for i, mesh in enumerate(meshes1):
        key = f"wind_turbine_{i}" if i > 0 else "wind_turbine"
        groups1[key] = mesh
    return groups0, groups1, count


def run_pipeline(
    config: PipelineConfig,
    output_mode: str = "file"
) -> PipelineResult:
    """
    Run the complete building generation pipeline.

    Steps:
    1. Load patch metadata
    2. Create projector
    3. Load terrain mesh
    4. Build spatial index
    5. Parse OSM buildings
    6. For each building:
       a. Compute floor Z
       b. Select roof type
       c. Generate LOD0 and LOD1 meshes
    7. Export OBJ files (file mode) or return meshes (memory mode)
    8. Generate report

    Args:
        config: Pipeline configuration
        output_mode: "file" to write OBJ files (default), "memory" to return meshes

    Returns:
        PipelineResult with report and either file paths or mesh data
    """
    logger = logging.getLogger(__name__)

    start_time = time.time()
    stats = PipelineStats()
    errors: List[str] = []
    output_files: List[str] = []
    roof_direction_stats = {
        source.value: 0 for source in RoofDirectionSource
    }

    # Create output directory
    os.makedirs(config.output_dir, exist_ok=True)

    # Step 1: Load patch metadata
    logger.info(f"Loading patch metadata for {config.patch_id}")
    try:
        metadata_path = os.path.join(
            config.patch_dir,
            f"h{config.patch_id}.txt"
        )
        metadata = load_patch_metadata(metadata_path)

        # Override config with metadata if not set
        if config.zone_number == 0:
            config.zone_number = metadata.zone_number
        if config.translate_x == 0.0:
            config.translate_x = metadata.translate_x
        if config.translate_y == 0.0:
            config.translate_y = metadata.translate_y

    except Exception as e:
        errors.append(f"Failed to load patch metadata: {e}")
        error_report = PipelineReport(
            patch_id=config.patch_id,
            version=__version__,
            success=False,
            stats=stats,
            output_files=[],
            errors=errors,
        )
        return PipelineResult(success=False, report=error_report)

    # Step 2: Create projector
    logger.info("Creating coordinate projector")
    projector = create_projector(
        config.zone_number,
        config.translate_x,
        config.translate_y
    )

    # Step 3: Load terrain mesh
    # A hand-edited terrain in Heightmaps/modified takes precedence over the
    # original one, so buildings sit on the same surface the Landscape Editor
    # (and the terrain imported into Blender) uses. Same order as bridges.py
    # and batch_processing.py. With terrain smoothing enabled, the smoothed
    # copy of that file is used instead.
    logger.info("Loading terrain mesh")
    try:
        from .blender.terrain_smooth import (load_terrain_smoothed,
                                             resolve_smooth_or_source)
        terrain_path = resolve_smooth_or_source(config.patch_dir, config.patch_id)
        logger.info(f"Terrain source: {terrain_path}")
        terrain = load_terrain_smoothed(config.patch_dir, config.patch_id)
        stats.terrain_triangles = len(terrain.triangles)
        logger.info(f"Loaded terrain with {stats.terrain_triangles} triangles")
    except Exception as e:
        errors.append(f"Failed to load terrain: {e}")
        error_report = PipelineReport(
            patch_id=config.patch_id,
            version=__version__,
            success=False,
            stats=stats,
            output_files=[],
            errors=errors,
        )
        return PipelineResult(success=False, report=error_report)

    # Step 4: Build spatial index
    logger.info("Building spatial index")
    spatial_index = GridSpatialIndex(terrain.triangles)
    floor_z_solver = FloorZSolver(terrain, spatial_index, floor_z_epsilon=config.floor_z_epsilon)

    # Step 5: Parse OSM buildings
    logger.info("Parsing OSM buildings")
    try:
        # Check if explicit OSM path is provided (e.g., from Blender addon)
        if config.osm_path and os.path.exists(config.osm_path):
            osm_path = config.osm_path
            logger.info(f"Using explicit OSM path: {osm_path}")
        else:
            # Try multiple naming conventions for OSM file
            osm_candidates = [
                os.path.join(config.patch_dir, f"map_{config.patch_id[-2:]}.osm"),
                os.path.join(config.patch_dir, f"map_{int(config.patch_id[-2:])}.osm"),
                os.path.join(config.patch_dir, f"map_{config.patch_id}.osm"),
            ]

            # Find the first existing OSM file
            osm_path = None
            for candidate in osm_candidates:
                if os.path.exists(candidate):
                    osm_path = candidate
                    break

            if osm_path is None:
                # Search for any .osm file in directory
                import glob
                osm_files = glob.glob(os.path.join(config.patch_dir, "*.osm"))
                if osm_files:
                    osm_path = osm_files[0]
                    logger.info(f"Using found OSM file: {osm_path}")
                else:
                    raise FileNotFoundError(
                        f"No OSM file found in {config.patch_dir}"
                    )

        parse_result = parse_osm_file(osm_path, projector, config.global_seed)
        buildings = parse_result.buildings
        stats.buildings_parsed = len(buildings)
        stats.warnings.extend(parse_result.warnings)

        logger.info(f"Parsed {stats.buildings_parsed} buildings")

    except Exception as e:
        errors.append(f"Failed to parse OSM: {e}")
        error_report = PipelineReport(
            patch_id=config.patch_id,
            version=__version__,
            success=False,
            stats=stats,
            output_files=[],
            errors=errors,
        )
        return PipelineResult(success=False, report=error_report)

    # Step 5b: Filter buildings outside patch bounds
    logger.info("Filtering buildings outside patch bounds")
    filter_result = filter_buildings_by_patch_bounds(buildings)
    buildings = filter_result.kept
    stats.buildings_filtered_edge = len(filter_result.filtered)

    # Log filtered building IDs
    for osm_id, reason in filter_result.filtered:
        stats.filtered_building_ids.append(osm_id)
        if config.verbose:
            logger.debug(f"Filtered building {osm_id}: {reason.value}")

    if stats.buildings_filtered_edge > 0:
        logger.info(
            f"Filtered {stats.buildings_filtered_edge} buildings outside patch bounds, "
            f"{len(buildings)} remaining"
        )

    # Step 5b2: Exclusion zones (opt-in). Airfields and ground solar farms already have
    # their own scenery objects in Condor (or, for a farm, get their panels from the
    # solar tool), so a building generated there only has to be deleted by hand again.
    # Read once here and reused below for the turbines and aerialways. Power lines are
    # deliberately NOT filtered - they have to cross the area.
    exclude_zones = []
    if config.exclude_airports_solar:
        exclude_zones = _read_exclusion_zones(osm_path, projector)
        logger.info(f"Exclusion zones (airfields + solar farms): {len(exclude_zones)}")

    if exclude_zones:
        from .generators.powerlines import _point_in_polys
        kept = []
        for b in buildings:
            c = b.footprint.bbox.center
            if _point_in_polys(c.x, c.y, exclude_zones):
                stats.filtered_building_ids.append(b.osm_id)
                continue
            kept.append(b)
        stats.buildings_filtered_zone = len(buildings) - len(kept)
        buildings = kept
        if stats.buildings_filtered_zone > 0:
            logger.info(
                f"Excluded {stats.buildings_filtered_zone} buildings inside airfields / "
                f"solar farms, {len(buildings)} remaining"
            )

    # Step 5b3: Buildings standing on WATER. The patch texture's alpha channel marks the
    # water, so a footprint that is water at EVERY one of its points is a mis-placed
    # building (MSprint footprints are machine-made and sometimes land in a river). One
    # point on dry land is enough to keep it, so quaysides and riverbank houses stay.
    if config.exclude_airports_solar:
        tex = _find_patch_texture(config)
        is_water = _load_water_sampler(tex)
        if is_water is None:
            logger.info("No patch texture found - the water check is skipped")
        else:
            logger.info(f"Water check from {os.path.basename(tex)}")
            kept = []
            for b in buildings:
                ring = b.footprint.outer_ring
                if ring and all(is_water(p.x, p.y) for p in ring):
                    stats.filtered_building_ids.append(b.osm_id)
                    continue
                kept.append(b)
            stats.buildings_filtered_water = len(buildings) - len(kept)
            buildings = kept
            if stats.buildings_filtered_water > 0:
                logger.info(
                    f"Excluded {stats.buildings_filtered_water} buildings standing on "
                    f"water, {len(buildings)} remaining"
                )

    # Step 5c: Filter for debug mode (single building)
    if config.debug_osm_id:
        logger.info(f"Debug mode: processing only building {config.debug_osm_id}")
        buildings = [b for b in buildings if b.osm_id == config.debug_osm_id]
        if not buildings:
            errors.append(f"Debug building {config.debug_osm_id} not found in patch")
            error_report = PipelineReport(
                patch_id=config.patch_id,
                version=__version__,
                success=False,
                stats=stats,
                output_files=[],
                errors=errors,
            )
            return PipelineResult(success=False, report=error_report)

    # Step 6: Process buildings using MeshGrouper for texture-based grouping
    logger.info("Processing buildings")

    # Configure generator with all pipeline config parameters
    configure_generator(
        gable_height=config.gable_height,
        roof_overhang_lod0=config.roof_overhang_lod0,
        floor_z_epsilon=config.floor_z_epsilon,
        gabled_max_floors=config.gabled_max_floors,
        gabled_min_rectangularity=config.gabled_min_rectangularity,
        polyskel_max_vertices=config.polyskel_max_vertices,
        house_max_area=config.house_max_footprint_area,
        house_max_side=config.house_max_side_length,
        house_min_side=config.house_min_side_length,
        house_max_aspect=config.house_max_aspect_ratio,
        flat_roof_merge=config.flat_roof_merge,
        flat_roof_terrain_photo=config.flat_roof_terrain_photo,
    )

    # Create mesh groupers for LOD0 and LOD1
    # Groups: houses, apartment_walls, commercial_walls, industrial_walls, flat_roof_1..6
    grouper_lod0 = MeshGrouper(num_flat_roof_groups=6, flat_roof_merge=config.flat_roof_merge, is_lod0=True, flat_roof_industrial_only=config.flat_roof_industrial_only)
    grouper_lod1 = MeshGrouper(num_flat_roof_groups=6, flat_roof_merge=config.flat_roof_merge, flat_roof_industrial_only=config.flat_roof_industrial_only)

    fallback_reasons: Dict[str, int] = {}
    vertex_count_stats: Dict[str, int] = {
        '4_vertices': 0,
        '5_to_6_vertices': 0,
        '7_to_8_vertices': 0,
        '9_plus_vertices': 0,
    }

    for i, building in enumerate(buildings):
        try:
            # 6a: Compute floor Z
            floor_z_result = floor_z_solver.solve(building.footprint)
            building.floor_z = floor_z_result.floor_z

            # 6b: Select roof type
            building.roof_type = select_roof_type(
                building,
                selection_mode=config.roof_selection_mode
            )

            # 6c: Analyze footprint and check eligibility (including house-scale)
            analysis = process_footprint(
                building.footprint,
                max_vertices=config.gabled_max_vertices,
                require_convex=config.gabled_require_convex,
                require_no_holes=config.gabled_require_no_holes,
                min_rectangularity=config.gabled_min_rectangularity,
                # House-scale constraints
                house_max_area=config.house_max_footprint_area,
                house_max_side=config.house_max_side_length,
                house_min_side=config.house_min_side_length,
                house_max_aspect=config.house_max_aspect_ratio,
            )

            # Track vertex count distribution
            vc = analysis.vertex_count
            if vc == 4:
                vertex_count_stats['4_vertices'] += 1
            elif vc <= 6:
                vertex_count_stats['5_to_6_vertices'] += 1
            elif vc <= 8:
                vertex_count_stats['7_to_8_vertices'] += 1
            else:
                vertex_count_stats['9_plus_vertices'] += 1

            # Track gabled eligibility (geometry only)
            if analysis.gabled_eligible == GabledEligibility.ELIGIBLE:
                stats.gabled_eligible += 1

            # Track house-scale eligibility
            if analysis.is_house_scale:
                stats.house_scale_pass += 1
            else:
                stats.house_scale_fail += 1

            # 6b2: Random hipped assignment (testing mode)
            # If enabled, randomly change 50% of eligible gabled roofs to hipped
            if config.random_hipped and \
               building.roof_type == RoofType.GABLED and \
               analysis.gabled_eligible == GabledEligibility.ELIGIBLE and \
               analysis.is_house_scale:
                import random
                rng = random.Random(building.seed)
                if rng.random() < 0.5:
                    building.roof_type = RoofType.HIPPED

            # Update roof direction source stats
            source = building.roof_direction_source.value
            if source in roof_direction_stats:
                roof_direction_stats[source] += 1

            # 6d: Generate SEPARATED meshes for LOD0 and LOD1
            # Using generate_building_separated() which keeps walls and roof separate
            result_lod0 = generate_building_separated(
                building,
                overhang=config.roof_overhang_lod0
            )
            result_lod1 = generate_building_separated(
                building,
                overhang=0.0
            )

            stats.warnings.extend(result_lod0.warnings)

            # Add to groupers (classifies by roof type and building category)
            grouper_lod0.add_building(building, result_lod0)
            grouper_lod1.add_building(building, result_lod1)

            # Update stats
            if result_lod0.actual_roof_type == RoofType.GABLED:
                stats.gabled_roofs += 1
            elif result_lod0.actual_roof_type == RoofType.HIPPED:
                stats.hipped_roofs += 1
            else:
                stats.flat_roofs += 1

            # Track fallback reasons
            if building.roof_type == RoofType.GABLED and \
               result_lod0.actual_roof_type == RoofType.FLAT:
                stats.gabled_fallbacks += 1
                if result_lod0.fallback_reason:
                    fallback_reasons[result_lod0.fallback_reason] = \
                        fallback_reasons.get(result_lod0.fallback_reason, 0) + 1

            if building.roof_type == RoofType.HIPPED and \
               result_lod0.actual_roof_type == RoofType.FLAT:
                stats.hipped_fallbacks += 1
                if result_lod0.fallback_reason:
                    fallback_reasons[result_lod0.fallback_reason] = \
                        fallback_reasons.get(result_lod0.fallback_reason, 0) + 1

            stats.buildings_processed += 1

            if config.verbose and (i + 1) % 100 == 0:
                logger.info(f"Processed {i + 1}/{len(buildings)} buildings")

        except Exception as e:
            stats.buildings_skipped += 1
            stats.warnings.append(
                f"Building {building.osm_id}: processing failed: {e}"
            )
            logger.warning(f"Failed to process building {building.osm_id}: {e}")

    logger.info(
        f"Processed {stats.buildings_processed} buildings, "
        f"skipped {stats.buildings_skipped}"
    )
    logger.info(f"Grouping stats:\n{grouper_lod0.get_stats_summary()}")

    # Step 6b: Optimize meshes (deduplicate vertices)
    logger.info("Optimizing meshes (vertex deduplication)")

    # Get all mesh groups for optimization
    lod0_groups = grouper_lod0.get_all_groups()
    lod1_groups = grouper_lod1.get_all_groups()

    # Optional powerlines: parse the same OSM file, stamp Wiek's pylons + string
    # catenary cables, and inject the result as a single 'pylones' object so it
    # flows through optimization and export alongside the buildings (Wiek Q18).
    # Fully guarded — a powerline failure must never break the building output.
    if config.generate_powerlines:
        try:
            pl0, pl1, pl_stats = _generate_powerline_group(
                osm_path, projector, terrain,
                draw_balls=config.generate_warning_balls,
            )
            if pl0 is not None:
                lod0_groups['pylones'] = pl0
                lod1_groups['pylones'] = pl1
                stats.powerline_towers = pl_stats.towers
                stats.powerline_cables = pl_stats.cables
                stats.powerline_lines = pl_stats.lines_with_geometry
                logger.info(
                    f"Powerlines: {pl_stats.towers} towers, {pl_stats.cables} "
                    f"cable spans, {pl_stats.balls} warning balls across "
                    f"{pl_stats.lines_with_geometry} lines"
                )
            else:
                logger.info("Powerlines enabled but no in-range lines in this patch")
        except Exception as e:
            stats.warnings.append(f"Powerline generation failed: {e}")
            logger.warning(f"Powerline generation failed: {e}")

    if config.generate_powerlines:
        try:
            wt0, wt1, wt_count = _generate_wind_turbines_group(
                osm_path, projector, terrain,
                bake_world=(output_mode == "file"),
                seed=config.global_seed,
                exclude_zones=exclude_zones,
            )
            if wt0 is not None:
                lod0_groups.update(wt0)
                lod1_groups.update(wt1)
                stats.wind_turbines = wt_count
                logger.info(f"Wind turbines: {wt_count} generated")
        except Exception as e:
            stats.warnings.append(f"Wind turbine generation failed: {e}")
            logger.warning(f"Wind turbine generation failed: {e}")

    # Optional aerialways (cable cars / chair lifts) generated together with the
    # power lines and MERGED into the same 'pylones' object (shared Pylons.dds
    # material). Same geometry for LOD0 and LOD1 for now. Guarded.
    if config.generate_powerlines:
        try:
            aw0, aw1, aw_stats = _generate_aerialway_group(
                osm_path, projector, terrain,
                heightmaps_dir=config.patch_dir, patch_id=config.patch_id,
                translate_x=config.translate_x, translate_y=config.translate_y,
                exclude_zones=exclude_zones,
            )
            if aw0 is not None:
                if 'pylones' in lod0_groups:
                    lod0_groups['pylones'].merge(aw0)
                else:
                    lod0_groups['pylones'] = aw0
                if 'pylones' in lod1_groups:
                    lod1_groups['pylones'].merge(aw1)
                else:
                    lod1_groups['pylones'] = aw1
                stats.aerialways = aw_stats['pylons']
                logger.info(
                    f"Aerialways: {aw_stats['pylons']} pylons, "
                    f"{aw_stats['cables']} cables, {aw_stats['carriers']} carriers "
                    f"across {aw_stats['lines']} lines"
                )
            else:
                logger.info("Aerialways enabled but no in-range lines in this patch")
        except Exception as e:
            stats.warnings.append(f"Aerialway generation failed: {e}")
            logger.warning(f"Aerialway generation failed: {e}")

    # Count vertices before optimization
    for name, mesh in lod0_groups.items():
        stats.lod0_vertices_before_optimize += len(mesh.vertices)
    for name, mesh in lod1_groups.items():
        stats.lod1_vertices_before_optimize += len(mesh.vertices)

    # Optimize each mesh group
    for name, mesh in lod0_groups.items():
        if not mesh.is_empty():
            opt_result = mesh.optimize(precision=4)
            stats.lod0_vertices_removed += opt_result.vertices_removed
            stats.degenerate_faces_removed += opt_result.degenerate_faces_removed

    for name, mesh in lod1_groups.items():
        if not mesh.is_empty():
            opt_result = mesh.optimize(precision=4)
            stats.lod1_vertices_removed += opt_result.vertices_removed
            stats.degenerate_faces_removed += opt_result.degenerate_faces_removed

    # Count mesh totals after optimization
    for name, mesh in lod0_groups.items():
        stats.lod0_vertices += len(mesh.vertices)
        stats.lod0_faces += len(mesh.faces)
    for name, mesh in lod1_groups.items():
        stats.lod1_vertices += len(mesh.vertices)
        stats.lod1_faces += len(mesh.faces)

    # Step 6c: Terrain orthophoto UVs for the merged flat_roof group
    # (Michel/Andy request). Maps the patch aerial photo t<patch>.dds onto flat
    # roofs so they blend with the terrain when viewed from the air. Opt-in
    # (Wiek/Chris/Uros, v0.8.7): only applied when the terrain photo is enabled.
    if config.flat_roof_terrain_photo:
        n0 = _apply_terrain_orthophoto_uvs(lod0_groups)
        n1 = _apply_terrain_orthophoto_uvs(lod1_groups)
        logger.info(
            f"Applied terrain orthophoto UVs to merged flat_roof "
            f"(LOD0: {n0} UVs, LOD1: {n1} UVs, v_flip={FLAT_ROOF_ORTHOPHOTO_V_FLIP})"
        )

    # Log optimization results
    if stats.lod0_vertices_before_optimize > 0:
        lod0_reduction = (stats.lod0_vertices_removed / stats.lod0_vertices_before_optimize) * 100
        logger.info(
            f"LOD0 optimization: {stats.lod0_vertices_before_optimize} -> {stats.lod0_vertices} vertices "
            f"({stats.lod0_vertices_removed} removed, {lod0_reduction:.1f}% reduction)"
        )
    if stats.lod1_vertices_before_optimize > 0:
        lod1_reduction = (stats.lod1_vertices_removed / stats.lod1_vertices_before_optimize) * 100
        logger.info(
            f"LOD1 optimization: {stats.lod1_vertices_before_optimize} -> {stats.lod1_vertices} vertices "
            f"({stats.lod1_vertices_removed} removed, {lod1_reduction:.1f}% reduction)"
        )
    if stats.degenerate_faces_removed > 0:
        logger.info(
            f"Removed {stats.degenerate_faces_removed} degenerate faces "
            f"(collapsed edges / duplicate-vertex faces) during optimization"
        )

    # Step 7: Export (depends on output_mode)
    result_lod0_path = None
    result_lod1_path = None

    if output_mode == "file":
        # Export using new multi-object format (one OBJ with multiple 'o' objects)
        num_lod0_objects = len(grouper_lod0.get_non_empty_groups())
        num_lod1_objects = len(grouper_lod1.get_non_empty_groups())
        logger.info(f"Exporting OBJ files (multi-object: {num_lod0_objects} objects per file)")

        # LOD0
        try:
            # LOD0 uses the bare o<patch>.obj name (no suffix); LOD1 keeps _LOD1.
            # Matches the Condor export operator and the current Landscape Editor
            # convention (Wiek/Uros, 2026-06-09).
            result_lod0_path = os.path.join(config.output_dir, f"o{config.patch_id}.obj")
            export_mesh_groups(
                _merge_gabled_for_export(lod0_groups),
                result_lod0_path,
                comment=f"LOD0 - Patch {config.patch_id}"
            )
            output_files.append(result_lod0_path)

            # Validate
            lod0_errors = validate_obj_file(result_lod0_path)
            if lod0_errors:
                stats.warnings.extend([f"LOD0: {e}" for e in lod0_errors])

            logger.info(
                f"Exported LOD0: {stats.lod0_vertices} vertices, "
                f"{stats.lod0_faces} faces, {num_lod0_objects} objects"
            )

        except Exception as e:
            errors.append(f"Failed to export LOD0: {e}")

        # LOD1
        try:
            result_lod1_path = os.path.join(config.output_dir, f"o{config.patch_id}_LOD1.obj")
            export_mesh_groups(
                _merge_gabled_for_export(lod1_groups),
                result_lod1_path,
                comment=f"LOD1 - Patch {config.patch_id}"
            )
            output_files.append(result_lod1_path)

            # Validate
            lod1_errors = validate_obj_file(result_lod1_path)
            if lod1_errors:
                stats.warnings.extend([f"LOD1: {e}" for e in lod1_errors])

            logger.info(
                f"Exported LOD1: {stats.lod1_vertices} vertices, "
                f"{stats.lod1_faces} faces, {num_lod1_objects} objects"
            )

        except Exception as e:
            errors.append(f"Failed to export LOD1: {e}")
    else:
        # Memory mode - grouped meshes will be returned in PipelineResult
        logger.info(
            f"Memory mode: {stats.lod0_vertices} LOD0 vertices, "
            f"{stats.lod1_vertices} LOD1 vertices"
        )

    # Step 8: Generate report
    elapsed_ms = int((time.time() - start_time) * 1000)
    stats.processing_time_ms = elapsed_ms

    # Capture config used for reproducibility
    config_used = {
        'gabled_max_vertices': config.gabled_max_vertices,
        'gabled_require_convex': config.gabled_require_convex,
        'gabled_require_no_holes': config.gabled_require_no_holes,
        'gabled_min_rectangularity': config.gabled_min_rectangularity,
        'global_seed': config.global_seed,
        'roof_overhang_lod0': config.roof_overhang_lod0,
        # House-scale constraints
        'house_max_footprint_area': config.house_max_footprint_area,
        'house_max_side_length': config.house_max_side_length,
        'house_min_side_length': config.house_min_side_length,
        'house_max_aspect_ratio': config.house_max_aspect_ratio,
    }

    report = PipelineReport(
        patch_id=config.patch_id,
        version=__version__,
        success=len(errors) == 0,
        stats=stats,
        output_files=output_files,
        errors=errors,
        roof_direction_stats=roof_direction_stats,
        fallback_reasons=fallback_reasons,
        vertex_count_stats=vertex_count_stats,
        config_used=config_used,
    )

    # Save report JSON (only in file mode)
    if output_mode == "file":
        report_path = os.path.join(
            config.output_dir,
            f"o{config.patch_id}_report.json"
        )
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(asdict(report), f, indent=2)
        output_files.append(report_path)
        logger.info(f"Report saved to {report_path}")

    logger.info(f"Pipeline completed in {elapsed_ms}ms")

    # Build result based on output mode
    if output_mode == "file":
        return PipelineResult(
            success=report.success,
            report=report,
            lod0_path=result_lod0_path,
            lod1_path=result_lod1_path,
        )
    else:
        # Memory mode: return grouped meshes for Blender import
        return PipelineResult(
            success=report.success,
            report=report,
            lod0_meshes=list(lod0_groups.values()),  # Legacy compatibility
            lod1_meshes=list(lod1_groups.values()),  # Legacy compatibility
            grouped_lod0=lod0_groups,  # New: Dict[str, MeshData]
            grouped_lod1=lod1_groups,  # New: Dict[str, MeshData]
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Condor Buildings Generator - Generate 3D buildings from OSM'
    )

    parser.add_argument(
        '--patch-dir',
        required=True,
        help='Directory containing patch files (h*.txt, h*.obj, map_*.osm)'
    )

    parser.add_argument(
        '--patch-id',
        required=True,
        help='Patch identifier (e.g., 036019)'
    )

    parser.add_argument(
        '--output-dir',
        default='./output',
        help='Output directory for generated files (default: ./output)'
    )

    parser.add_argument(
        '--seed',
        type=int,
        default=42,
        help='Global random seed (default: 42)'
    )

    parser.add_argument(
        '--zone',
        type=int,
        default=0,
        help='UTM zone number (default: from patch metadata)'
    )

    parser.add_argument(
        '--translate-x',
        type=float,
        default=0.0,
        help='X translation offset (default: from patch metadata)'
    )

    parser.add_argument(
        '--translate-y',
        type=float,
        default=0.0,
        help='Y translation offset (default: from patch metadata)'
    )

    parser.add_argument(
        '--groups',
        action='store_true',
        help='Include per-building groups in OBJ output'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    parser.add_argument(
        '--no-log-file',
        action='store_true',
        help='Disable log file output (only console)'
    )

    # Gabled roof configuration
    parser.add_argument(
        '--gabled-max-vertices',
        type=int,
        default=4,
        help='Maximum vertices for gabled roof eligibility (default: 4 = rectangles only)'
    )

    parser.add_argument(
        '--gabled-allow-non-convex',
        action='store_true',
        help='Allow non-convex footprints for gabled roofs (not recommended)'
    )

    # House-scale constraints for gabled roofs
    parser.add_argument(
        '--house-max-area',
        type=float,
        default=300.0,
        help='Maximum footprint area (m²) for house classification (default: 300)'
    )

    parser.add_argument(
        '--house-max-side',
        type=float,
        default=25.0,
        help='Maximum side length (m) for house classification (default: 25)'
    )

    parser.add_argument(
        '--house-min-side',
        type=float,
        default=4.0,
        help='Minimum side length (m) for house classification (default: 4)'
    )

    parser.add_argument(
        '--house-max-aspect',
        type=float,
        default=4.0,
        help='Maximum aspect ratio for house classification (default: 4.0)'
    )

    # Debug mode
    parser.add_argument(
        '--debug-osm-id',
        type=str,
        default=None,
        help='Process only a single building by OSM ID (for debugging)'
    )

    # Roof selection mode
    parser.add_argument(
        '--roof-selection-mode',
        type=str,
        choices=['geometry', 'osm_tags_only'],
        default='geometry',
        help='Roof selection mode: "geometry" (default) uses geometry+category heuristics, '
             '"osm_tags_only" gives pitched roofs only to buildings tagged as houses'
    )

    # Testing: random roof type selection
    parser.add_argument(
        '--random-hipped',
        action='store_true',
        help='Randomly assign hipped roof to 50%% of eligible buildings (for testing)'
    )

    # Flat roof merge: single object
    parser.add_argument(
        '--flat-roof-merge',
        action='store_true',
        help='Merge all flat roofs into a single object'
    )

    # Terrain orthophoto on flat roofs (opt-in; implies --flat-roof-merge)
    parser.add_argument(
        '--flat-roof-terrain-photo',
        action='store_true',
        help='Texture merged flat roofs with the patch orthophoto t<patch>.dds '
             '(global UV; implies --flat-roof-merge)'
    )

    # Powerlines (optional): towers + catenary cables as a 'pylones' object
    parser.add_argument(
        '--powerlines',
        action='store_true',
        help='Also generate powerlines (pylons + cables) from power=line / '
             'minor_line ways into a single "pylones" object'
    )

    parser.add_argument(
        '--warning-balls',
        action='store_true',
        help='Add aviation warning balls on the top conductor near aerodromes '
             '(<=5 km) and over deep valleys (cable >45 m above terrain). '
             'Needs --powerlines'
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}'
    )

    args = parser.parse_args()

    # Create output directory early so we can put log file there
    os.makedirs(args.output_dir, exist_ok=True)

    # Setup logging with file output by default
    log_file = None
    if not args.no_log_file:
        log_file = os.path.join(args.output_dir, f"o{args.patch_id}.log")

    setup_logging(args.verbose, log_file)

    # Parse roof selection mode
    roof_mode = RoofSelectionMode.GEOMETRY
    if args.roof_selection_mode == 'osm_tags_only':
        roof_mode = RoofSelectionMode.OSM_TAGS_ONLY

    config = PipelineConfig(
        patch_id=args.patch_id,
        patch_dir=args.patch_dir,
        zone_number=args.zone,
        translate_x=args.translate_x,
        translate_y=args.translate_y,
        global_seed=args.seed,
        export_groups=args.groups,
        output_dir=args.output_dir,
        verbose=args.verbose,
        gabled_max_vertices=args.gabled_max_vertices,
        gabled_require_convex=not args.gabled_allow_non_convex,
        # House-scale constraints
        house_max_footprint_area=args.house_max_area,
        house_max_side_length=args.house_max_side,
        house_min_side_length=args.house_min_side,
        house_max_aspect_ratio=args.house_max_aspect,
        debug_osm_id=args.debug_osm_id,
        random_hipped=args.random_hipped,
        roof_selection_mode=roof_mode,
        flat_roof_merge=args.flat_roof_merge,
        flat_roof_terrain_photo=args.flat_roof_terrain_photo,
        generate_powerlines=args.powerlines,
        generate_warning_balls=args.warning_balls,
    )

    try:
        result = run_pipeline(config, output_mode="file")
        report = result.report

        if result.success:
            print(f"\nSuccess! Generated {report.stats.buildings_processed} buildings")
            print(f"Parsed: {report.stats.buildings_parsed}, Filtered (edge): {report.stats.buildings_filtered_edge}")
            print(f"LOD0: {report.stats.lod0_vertices} vertices, {report.stats.lod0_faces} faces")
            print(f"LOD1: {report.stats.lod1_vertices} vertices, {report.stats.lod1_faces} faces")

            # Vertex count distribution
            if report.vertex_count_stats:
                print(f"\nFootprint vertex distribution:")
                print(f"  4 vertices (rectangles): {report.vertex_count_stats.get('4_vertices', 0)}")
                print(f"  5-6 vertices: {report.vertex_count_stats.get('5_to_6_vertices', 0)}")
                print(f"  7-8 vertices: {report.vertex_count_stats.get('7_to_8_vertices', 0)}")
                print(f"  9+ vertices: {report.vertex_count_stats.get('9_plus_vertices', 0)}")

            print(f"\nRoof types:")
            print(f"  Gabled eligible (geometry): {report.stats.gabled_eligible}")
            print(f"  House-scale pass: {report.stats.house_scale_pass}")
            print(f"  House-scale fail: {report.stats.house_scale_fail}")
            print(f"  Actual gabled: {report.stats.gabled_roofs}")
            print(f"  Actual hipped: {report.stats.hipped_roofs}")
            print(f"  Flat roofs: {report.stats.flat_roofs}")
            print(f"  Gabled->Flat fallbacks: {report.stats.gabled_fallbacks}")
            print(f"  Hipped->Flat fallbacks: {report.stats.hipped_fallbacks}")

            if config.generate_powerlines:
                print(f"\nPowerlines:")
                print(f"  Towers: {report.stats.powerline_towers}")
                print(f"  Cable spans: {report.stats.powerline_cables}")
                print(f"  Lines with geometry: {report.stats.powerline_lines}")
                print(f"  Wind turbines: {report.stats.wind_turbines}")

            if report.fallback_reasons:
                print(f"\nFallback reasons:")
                for reason, count in sorted(report.fallback_reasons.items(), key=lambda x: -x[1]):
                    print(f"  {reason}: {count}")

            print(f"\nConfig used:")
            print(f"  max_vertices={report.config_used.get('gabled_max_vertices')}")
            print(f"  house_max_area={report.config_used.get('house_max_footprint_area')}m²")
            print(f"  house_max_side={report.config_used.get('house_max_side_length')}m")
            print(f"  house_min_side={report.config_used.get('house_min_side_length')}m")
            print(f"  house_max_aspect={report.config_used.get('house_max_aspect_ratio')}")
            print(f"Output files: {', '.join(report.output_files)}")
            if log_file:
                print(f"Log file: {log_file}")
            return 0
        else:
            print(f"\nPipeline failed with errors:")
            for error in report.errors:
                print(f"  - {error}")
            if log_file:
                print(f"See log file for details: {log_file}")
            return 1

    except Exception as e:
        logging.exception(f"Pipeline failed: {e}")
        if log_file:
            print(f"See log file for details: {log_file}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
