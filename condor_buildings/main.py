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
from .config import PipelineConfig, RoofSelectionMode
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
from .models.building import RoofType


@dataclass
class PipelineStats:
    """Statistics from the pipeline run."""
    buildings_parsed: int = 0
    buildings_filtered_edge: int = 0  # Filtered due to patch edge
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
    terrain_triangles: int = 0
    processing_time_ms: int = 0
    filtered_building_ids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


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
        'osm_tag': 0,
        'longest_axis': 0,
        'default': 0,
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
    logger.info("Loading terrain mesh")
    try:
        terrain_path = os.path.join(
            config.patch_dir,
            f"h{config.patch_id}.obj"
        )
        terrain = load_terrain(terrain_path)
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
    floor_z_solver = FloorZSolver(terrain, spatial_index)

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

    # Configure generator with flat_roof_merge setting
    configure_generator(flat_roof_merge=config.flat_roof_merge)

    # Create mesh groupers for LOD0 and LOD1
    # Groups: houses, apartment_walls, commercial_walls, industrial_walls, flat_roof_1..6
    grouper_lod0 = MeshGrouper(num_flat_roof_groups=6, flat_roof_merge=config.flat_roof_merge)
    grouper_lod1 = MeshGrouper(num_flat_roof_groups=6, flat_roof_merge=config.flat_roof_merge)

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
            source = building.roof_direction_source.value.lower()
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

    for name, mesh in lod1_groups.items():
        if not mesh.is_empty():
            opt_result = mesh.optimize(precision=4)
            stats.lod1_vertices_removed += opt_result.vertices_removed

    # Count mesh totals after optimization
    for name, mesh in lod0_groups.items():
        stats.lod0_vertices += len(mesh.vertices)
        stats.lod0_faces += len(mesh.faces)
    for name, mesh in lod1_groups.items():
        stats.lod1_vertices += len(mesh.vertices)
        stats.lod1_faces += len(mesh.faces)

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
            result_lod0_path = os.path.join(config.output_dir, f"o{config.patch_id}_LOD0.obj")
            export_mesh_groups(
                lod0_groups,
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
                lod1_groups,
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

    # Flat roof merge: single object with global UV projection
    parser.add_argument(
        '--flat-roof-merge',
        action='store_true',
        help='Merge all flat roofs into single object with global UV projection (for terrain texture)'
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
