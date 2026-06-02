"""
Building generator orchestrator for Condor Buildings Generator.

Combines wall generation, roof generation (flat, gabled, hipped, polyskel),
and handles roof type fallback logic.
"""

from typing import Optional, List, Tuple
from dataclasses import dataclass, field
import logging
import math

from ..models.building import (
    BuildingRecord,
    RoofType,
    BuildingCategory,
    RoofFallbackReason,
)
from ..models.mesh import MeshData
from ..processing.footprint import (
    process_footprint,
    GabledEligibility,
    FootprintAnalysis,
    compute_obb,
)
from .walls import generate_walls, generate_walls_lod1, generate_walls_for_gabled, generate_walls_for_hipped, generate_highrise_walls
from .roof_flat import generate_flat_roof
from .roof_gabled import (
    generate_gabled_roof,
    generate_gabled_roof_lod0,
    generate_gabled_roof_lod1,
    GabledRoofConfig,
    _get_ridge_direction,
    _get_roof_pitch,
)
from .roof_hipped import (
    generate_hipped_roof,
    generate_hipped_roof_lod0,
    generate_hipped_roof_lod1,
    HippedRoofConfig,
)
from ..config import (
    ROOF_OVERHANG_LOD0,
    GABLED_MAX_VERTICES,
    GABLED_REQUIRE_CONVEX,
    GABLED_REQUIRE_NO_HOLES,
    # House-scale constraints
    HOUSE_MAX_FOOTPRINT_AREA,
    HOUSE_MAX_SIDE_LENGTH,
    HOUSE_MIN_SIDE_LENGTH,
    HOUSE_MAX_ASPECT_RATIO,
    # Phase 1 geometry
    GABLE_HEIGHT_FIXED,
    HIPPED_HEIGHT_FIXED,
    # Floor restrictions
    GABLED_MAX_FLOORS,
    HIPPED_MAX_FLOORS,
    # Roof selection mode
    RoofSelectionMode,
    # Polyskel constraints
    POLYSKEL_MAX_VERTICES,
)

# Conditional import of polyskel roof generator
# Available only in Blender (requires mathutils + bpypolyskel)
try:
    from .roof_polyskel import (
        generate_polyskel_roof,
        PolyskelRoofConfig,
        POLYSKEL_AVAILABLE,
    )
except ImportError:
    POLYSKEL_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# RUNTIME CONFIGURATION
# =============================================================================
# These values can be updated at runtime via configure_generator()
# to allow Blender addon users to override defaults without modifying code.

@dataclass
class GeneratorRuntimeConfig:
    """
    Runtime configuration for the building generator.

    These parameters can be set before processing a batch of buildings
    to override module-level constants.
    """
    # Roof geometry
    gable_height: float = GABLE_HEIGHT_FIXED
    hipped_height: float = HIPPED_HEIGHT_FIXED
    roof_overhang_lod0: float = ROOF_OVERHANG_LOD0

    # Floor Z
    floor_z_epsilon: float = 0.3  # From config.FLOOR_Z_EPSILON

    # Eligibility constraints
    gabled_max_floors: int = GABLED_MAX_FLOORS
    hipped_max_floors: int = HIPPED_MAX_FLOORS
    gabled_min_rectangularity: float = 0.70  # From config.GABLED_MIN_RECTANGULARITY
    polyskel_max_vertices: int = POLYSKEL_MAX_VERTICES

    # House-scale constraints
    house_max_area: float = HOUSE_MAX_FOOTPRINT_AREA
    house_max_side: float = HOUSE_MAX_SIDE_LENGTH
    house_min_side: float = HOUSE_MIN_SIDE_LENGTH
    house_max_aspect: float = HOUSE_MAX_ASPECT_RATIO

    # Flat roof merge: merge all flat roofs into a single object
    flat_roof_merge: bool = False

    # Terrain orthophoto on flat roofs: drives global (world-coordinate) UV projection
    # so the merged flat_roof samples the patch aerial photo. Independent from merging.
    flat_roof_terrain_photo: bool = False


# Global runtime config instance (can be updated via configure_generator)
_runtime_config = GeneratorRuntimeConfig()


def configure_generator(
    gable_height: Optional[float] = None,
    hipped_height: Optional[float] = None,
    roof_overhang_lod0: Optional[float] = None,
    floor_z_epsilon: Optional[float] = None,
    gabled_max_floors: Optional[int] = None,
    hipped_max_floors: Optional[int] = None,
    gabled_min_rectangularity: Optional[float] = None,
    polyskel_max_vertices: Optional[int] = None,
    house_max_area: Optional[float] = None,
    house_max_side: Optional[float] = None,
    house_min_side: Optional[float] = None,
    house_max_aspect: Optional[float] = None,
    flat_roof_merge: Optional[bool] = None,
    flat_roof_terrain_photo: Optional[bool] = None,
) -> None:
    """
    Configure the building generator runtime parameters.

    Call this function before processing a batch of buildings to override
    the default values. Pass None to keep the current value.

    Example:
        configure_generator(gable_height=4.0, gabled_max_floors=3)
        result = generate_building_lod0(building)
    """
    global _runtime_config

    if gable_height is not None:
        _runtime_config.gable_height = gable_height
    if hipped_height is not None:
        _runtime_config.hipped_height = hipped_height
    if roof_overhang_lod0 is not None:
        _runtime_config.roof_overhang_lod0 = roof_overhang_lod0
    if floor_z_epsilon is not None:
        _runtime_config.floor_z_epsilon = floor_z_epsilon
    if gabled_max_floors is not None:
        _runtime_config.gabled_max_floors = gabled_max_floors
    if hipped_max_floors is not None:
        _runtime_config.hipped_max_floors = hipped_max_floors
    if gabled_min_rectangularity is not None:
        _runtime_config.gabled_min_rectangularity = gabled_min_rectangularity
    if polyskel_max_vertices is not None:
        _runtime_config.polyskel_max_vertices = polyskel_max_vertices
    if house_max_area is not None:
        _runtime_config.house_max_area = house_max_area
    if house_max_side is not None:
        _runtime_config.house_max_side = house_max_side
    if house_min_side is not None:
        _runtime_config.house_min_side = house_min_side
    if house_max_aspect is not None:
        _runtime_config.house_max_aspect = house_max_aspect
    if flat_roof_merge is not None:
        _runtime_config.flat_roof_merge = flat_roof_merge
    if flat_roof_terrain_photo is not None:
        _runtime_config.flat_roof_terrain_photo = flat_roof_terrain_photo


def reset_generator_config() -> None:
    """Reset generator configuration to defaults."""
    global _runtime_config
    _runtime_config = GeneratorRuntimeConfig()


def get_runtime_config() -> GeneratorRuntimeConfig:
    """Get current runtime configuration (for inspection/debugging)."""
    return _runtime_config


@dataclass
class BuildingGeneratorResult:
    """Result of building generation."""
    mesh: MeshData
    actual_roof_type: RoofType
    fallback_reason: RoofFallbackReason = RoofFallbackReason.NONE
    warnings: List[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    footprint_analysis: Optional[FootprintAnalysis] = None


def _should_use_highrise_walls(building: BuildingRecord, actual_roof_type: RoofType = None) -> bool:
    """
    Determine if a building should use the highrise wall generator.

    Highrise walls use the Highrise_atlas.dds (2048x12288) with multi-floor
    quads instead of per-floor quads. This must match the classification
    logic in MeshGrouper._add_flat_roof_walls() to ensure UV mapping
    matches the texture group.

    Returns True for: APARTMENT, COMMERCIAL, small OTHER (area <= 200),
                      HOUSE with flat roof fallback.
    Returns False for: INDUSTRIAL, large OTHER (area > 200),
                       HOUSE with pitched roof.
    """
    if building.category in (BuildingCategory.APARTMENT, BuildingCategory.COMMERCIAL):
        return True
    if building.category == BuildingCategory.INDUSTRIAL:
        return False
    if building.category == BuildingCategory.OTHER:
        return building.footprint.area() <= 200
    # HOUSE: use highrise walls only when flat roof (fallback case)
    if building.category == BuildingCategory.HOUSE:
        return actual_roof_type == RoofType.FLAT
    return False


def generate_building(
    building: BuildingRecord,
    lod: int = 0
) -> BuildingGeneratorResult:
    """
    Generate complete building mesh (walls + roof).

    Handles roof type selection and fallback:
    - If roof type is FLAT, always use flat roof
    - If roof type is GABLED, check eligibility and fallback if needed
    - If roof type is HIPPED, use flat roof (simplified)

    Args:
        building: Building record with all parameters set
        lod: Level of detail (0 = detailed, 1 = simplified)

    Returns:
        BuildingGeneratorResult with mesh and metadata
    """
    if lod == 0:
        return generate_building_lod0(building)
    else:
        return generate_building_lod1(building)


def generate_building_lod0(building: BuildingRecord) -> BuildingGeneratorResult:
    """
    Generate LOD0 (detailed) building mesh.

    Features:
    - Full wall geometry (pentagonal for gabled roofs)
    - Gabled roofs with 0.5m overhang (if eligible)
    - Proper hole handling

    Geometry v4 design:
    - For gabled roofs, walls are pentagonal (include gable as part of wall)
    - Roof is just 2 slope planes (open at ends)
    - This matches the reference house geometry

    Args:
        building: Building record

    Returns:
        BuildingGeneratorResult
    """
    result = BuildingGeneratorResult(
        mesh=MeshData(osm_id=building.osm_id),
        actual_roof_type=building.roof_type,
        warnings=[],
    )

    # Use runtime config for overhang
    overhang = _runtime_config.roof_overhang_lod0

    # First, determine what roof type will actually be used
    # This is needed BEFORE generating walls for pentagonal gable walls
    actual_roof_type, ridge_params = _determine_roof_type(
        building,
        overhang=overhang,
        result=result,
        house_max_area=_runtime_config.house_max_area,
        house_max_side=_runtime_config.house_max_side,
        house_min_side=_runtime_config.house_min_side,
        house_max_aspect=_runtime_config.house_max_aspect,
        gabled_max_floors=_runtime_config.gabled_max_floors,
    )

    # Generate walls based on actual roof type
    if actual_roof_type == RoofType.GABLED and ridge_params is not None:
        # Gabled roof: use pentagonal walls for gable ends
        # Phase 1: For 1-floor buildings, separate gable triangle for UV mapping
        ridge_direction, ridge_z, obb_center = ridge_params
        wall_mesh = generate_walls_for_gabled(
            building,
            ridge_direction,
            ridge_z,
            obb_center,
            separate_gable_for_single_floor=True
        )
    elif actual_roof_type == RoofType.HIPPED:
        # Hipped roof: rectangular walls on all sides (roof covers everything)
        wall_mesh = generate_walls_for_hipped(building)
    else:
        # Flat or other roof: use rectangular walls
        if _should_use_highrise_walls(building, actual_roof_type):
            wall_mesh = generate_highrise_walls(building)
        else:
            wall_mesh = generate_walls(building)

    result.mesh.merge(wall_mesh)

    # Generate roof
    roof_mesh, actual_roof_type = _generate_roof(
        building,
        overhang=overhang,
        result=result,
        gabled_max_floors=_runtime_config.gabled_max_floors,
    )
    result.mesh.merge(roof_mesh)
    result.actual_roof_type = actual_roof_type

    # Update building record
    building.actual_roof_type = actual_roof_type

    # Collect stats
    result.stats = {
        'vertex_count': len(result.mesh.vertices),
        'face_count': len(result.mesh.faces),
        'requested_roof': building.roof_type.value,
        'actual_roof': actual_roof_type.value,
    }

    return result


def generate_building_lod1(building: BuildingRecord) -> BuildingGeneratorResult:
    """
    Generate LOD1 (simplified) building mesh.

    Features:
    - Same wall geometry as LOD0 (pentagonal for gabled)
    - No roof overhang
    - Same roof type logic

    Args:
        building: Building record

    Returns:
        BuildingGeneratorResult
    """
    result = BuildingGeneratorResult(
        mesh=MeshData(osm_id=building.osm_id),
        actual_roof_type=building.roof_type,
        warnings=[],
    )

    # First, determine what roof type will actually be used
    # This is needed BEFORE generating walls for pentagonal gable walls
    actual_roof_type, ridge_params = _determine_roof_type(
        building,
        overhang=0.0,  # LOD1 has no overhang
        result=result,
        house_max_area=_runtime_config.house_max_area,
        house_max_side=_runtime_config.house_max_side,
        house_min_side=_runtime_config.house_min_side,
        house_max_aspect=_runtime_config.house_max_aspect,
        gabled_max_floors=_runtime_config.gabled_max_floors,
    )

    # Generate walls based on actual roof type
    if actual_roof_type == RoofType.GABLED and ridge_params is not None:
        # Gabled roof: use pentagonal walls for gable ends
        # Phase 1: For 1-floor buildings, separate gable triangle for UV mapping
        ridge_direction, ridge_z, obb_center = ridge_params
        wall_mesh = generate_walls_for_gabled(
            building,
            ridge_direction,
            ridge_z,
            obb_center,
            separate_gable_for_single_floor=True
        )
    elif actual_roof_type == RoofType.HIPPED:
        # Hipped roof: rectangular walls on all sides (roof covers everything)
        wall_mesh = generate_walls_for_hipped(building)
    else:
        # Flat or other roof: use rectangular walls
        if _should_use_highrise_walls(building, actual_roof_type):
            wall_mesh = generate_highrise_walls(building)
        else:
            wall_mesh = generate_walls_lod1(building)

    result.mesh.merge(wall_mesh)

    # Generate roof (no overhang)
    roof_mesh, actual_roof_type = _generate_roof(
        building,
        overhang=0.0,
        result=result,
        gabled_max_floors=_runtime_config.gabled_max_floors,
    )
    result.mesh.merge(roof_mesh)
    result.actual_roof_type = actual_roof_type

    # Update building record
    building.actual_roof_type = actual_roof_type

    # Collect stats
    result.stats = {
        'vertex_count': len(result.mesh.vertices),
        'face_count': len(result.mesh.faces),
        'requested_roof': building.roof_type.value,
        'actual_roof': actual_roof_type.value,
    }

    return result


# Import SeparatedBuildingResult for the new function
from ..processing.mesh_grouper import SeparatedBuildingResult


def generate_building_separated(
    building: BuildingRecord,
    overhang: float = 0.0
) -> SeparatedBuildingResult:
    """
    Generate building with walls and roof as SEPARATE meshes.

    This is used by the MeshGrouper to classify and route walls and roofs
    to different texture groups. Unlike generate_building_lod0/lod1, this
    function does NOT merge walls and roof into a single mesh.

    Args:
        building: Building record with all parameters set
        overhang: Roof overhang in meters (0.0 for LOD1, ~0.5 for LOD0)

    Returns:
        SeparatedBuildingResult with walls and roof as separate MeshData
    """
    # Create temporary result for _determine_roof_type
    temp_result = BuildingGeneratorResult(
        mesh=MeshData(osm_id=building.osm_id),
        actual_roof_type=building.roof_type,
        warnings=[],
    )

    # Determine actual roof type
    actual_roof_type, ridge_params = _determine_roof_type(
        building,
        overhang=overhang,
        result=temp_result,
        house_max_area=_runtime_config.house_max_area,
        house_max_side=_runtime_config.house_max_side,
        house_min_side=_runtime_config.house_min_side,
        house_max_aspect=_runtime_config.house_max_aspect,
        gabled_max_floors=_runtime_config.gabled_max_floors,
    )

    # Generate walls based on actual roof type
    if actual_roof_type == RoofType.GABLED and ridge_params is not None:
        ridge_direction, ridge_z, obb_center = ridge_params
        wall_mesh = generate_walls_for_gabled(
            building,
            ridge_direction,
            ridge_z,
            obb_center,
            separate_gable_for_single_floor=True
        )
    elif actual_roof_type == RoofType.HIPPED:
        wall_mesh = generate_walls_for_hipped(building)
    else:
        if _should_use_highrise_walls(building, actual_roof_type):
            wall_mesh = generate_highrise_walls(building)
        else:
            wall_mesh = generate_walls(building)

    # Generate roof (separate, NOT merged)
    roof_mesh, actual_roof_type = _generate_roof(
        building,
        overhang=overhang,
        result=temp_result,
        gabled_max_floors=_runtime_config.gabled_max_floors,
    )

    # Update building record
    building.actual_roof_type = actual_roof_type

    # Return SEPARATED meshes
    return SeparatedBuildingResult(
        walls=wall_mesh,
        roof=roof_mesh,
        actual_roof_type=actual_roof_type,
        category=building.category,
        fallback_reason=temp_result.fallback_reason.value if temp_result.fallback_reason else None,
        warnings=temp_result.warnings,
    )


def _determine_roof_type(
    building: BuildingRecord,
    overhang: float,
    result: BuildingGeneratorResult,
    max_vertices: int = GABLED_MAX_VERTICES,
    require_convex: bool = GABLED_REQUIRE_CONVEX,
    require_no_holes: bool = GABLED_REQUIRE_NO_HOLES,
    house_max_area: float = HOUSE_MAX_FOOTPRINT_AREA,
    house_max_side: float = HOUSE_MAX_SIDE_LENGTH,
    house_min_side: float = HOUSE_MIN_SIDE_LENGTH,
    house_max_aspect: float = HOUSE_MAX_ASPECT_RATIO,
    gabled_max_floors: int = GABLED_MAX_FLOORS,
) -> Tuple[RoofType, Optional[Tuple[float, float, Tuple[float, float]]]]:
    """
    Determine the actual roof type and compute ridge parameters if gabled.

    This function determines BEFORE wall generation whether the roof will be
    gabled or flat, so we can generate the correct wall geometry (pentagonal
    for gabled, rectangular for flat).

    Args:
        building: Building record
        overhang: Roof overhang distance
        result: Result object for collecting analysis
        max_vertices: Maximum vertices for gabled eligibility
        require_convex: Require strictly convex polygon
        require_no_holes: Require no holes in polygon
        house_max_area: Maximum footprint area for house classification
        house_max_side: Maximum side length for house classification
        house_min_side: Minimum side length for house classification
        house_max_aspect: Maximum aspect ratio for house classification

    Returns:
        Tuple of:
        - actual_roof_type: The roof type that will be generated
        - ridge_params: If gabled, tuple of (ridge_direction_deg, ridge_z, obb_center)
                        If not gabled, None
    """
    requested_type = building.roof_type

    # Flat roof - no ridge params needed
    if requested_type == RoofType.FLAT:
        return RoofType.FLAT, None

    # Hipped roof - check eligibility
    if requested_type == RoofType.HIPPED:
        # Check floor count restriction
        if building.floors > _runtime_config.hipped_max_floors:
            result.fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            building.roof_fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            return RoofType.FLAT, None

        # Analyze footprint (with gabled constraints for analytical hipped)
        analysis = process_footprint(
            building.footprint,
            max_vertices=max_vertices,
            require_convex=require_convex,
            require_no_holes=require_no_holes,
            house_max_area=house_max_area,
            house_max_side=house_max_side,
            house_min_side=house_min_side,
            house_max_aspect=house_max_aspect,
        )
        result.footprint_analysis = analysis
        eligibility = analysis.gabled_eligible
        building.footprint_vertex_count = analysis.vertex_count

        # Check eligibility and house-scale
        if eligibility == GabledEligibility.ELIGIBLE and analysis.is_house_scale:
            # ≤4 verts, convex, house-scale → analytical hipped
            return RoofType.HIPPED, None
        elif _is_polyskel_eligible(eligibility, analysis, building):
            # >4 verts, no holes, house-scale, polyskel available → polyskel hipped
            return RoofType.HIPPED, None
        else:
            # Falls back to flat
            return RoofType.FLAT, None

    # Gabled roof - check eligibility
    if requested_type == RoofType.GABLED:
        # Check floor count restriction FIRST
        if building.floors > gabled_max_floors:
            result.fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            building.roof_fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            return RoofType.FLAT, None

        # Analyze footprint
        analysis = process_footprint(
            building.footprint,
            max_vertices=max_vertices,
            require_convex=require_convex,
            require_no_holes=require_no_holes,
            house_max_area=house_max_area,
            house_max_side=house_max_side,
            house_min_side=house_min_side,
            house_max_aspect=house_max_aspect,
        )
        result.footprint_analysis = analysis
        eligibility = analysis.gabled_eligible
        building.footprint_vertex_count = analysis.vertex_count

        # Check eligibility and house-scale
        if eligibility == GabledEligibility.ELIGIBLE and analysis.is_house_scale:
            # Will be gabled - compute ridge parameters
            outer_ring = building.footprint.outer_ring
            ridge_direction = _get_ridge_direction(building, outer_ring)
            obb = compute_obb(outer_ring, ridge_direction)

            # Phase 1: Fixed gable height (default 3.0m, configurable)
            # Pitch becomes a consequence: pitch = atan(gable_height / half_width)
            ridge_height = _runtime_config.gable_height
            ridge_z = building.wall_top_z + ridge_height

            obb_center = (obb['center_x'], obb['center_y'])

            return RoofType.GABLED, (ridge_direction, ridge_z, obb_center)
        elif _is_polyskel_eligible(eligibility, analysis, building):
            # GABLED ineligible due to >4 verts → fallback to polyskel hipped
            # Better a hipped roof than a flat one for a house
            logger.debug(
                f"Building {building.osm_id}: GABLED→HIPPED(polyskel) fallback "
                f"[{analysis.vertex_count} verts]"
            )
            return RoofType.HIPPED, None
        else:
            # Will fall back to flat
            return RoofType.FLAT, None

    # Default to flat
    return RoofType.FLAT, None


def _generate_roof(
    building: BuildingRecord,
    overhang: float,
    result: BuildingGeneratorResult,
    max_vertices: int = GABLED_MAX_VERTICES,
    require_convex: bool = GABLED_REQUIRE_CONVEX,
    require_no_holes: bool = GABLED_REQUIRE_NO_HOLES,
    # House-scale constraints
    house_max_area: float = HOUSE_MAX_FOOTPRINT_AREA,
    house_max_side: float = HOUSE_MAX_SIDE_LENGTH,
    house_min_side: float = HOUSE_MIN_SIDE_LENGTH,
    house_max_aspect: float = HOUSE_MAX_ASPECT_RATIO,
    # Floor restriction
    gabled_max_floors: int = GABLED_MAX_FLOORS,
) -> tuple[MeshData, RoofType]:
    """
    Generate appropriate roof based on building type and eligibility.

    Args:
        building: Building record
        overhang: Roof overhang distance
        result: Result object for collecting warnings
        max_vertices: Maximum vertices for gabled eligibility
        require_convex: Require strictly convex polygon
        require_no_holes: Require no holes in polygon
        house_max_area: Maximum footprint area for house classification (m²)
        house_max_side: Maximum side length for house classification (m)
        house_min_side: Minimum side length for house classification (m)
        house_max_aspect: Maximum aspect ratio for house classification

    Returns:
        (roof_mesh, actual_roof_type)
    """
    requested_type = building.roof_type

    # Flat roof - always use flat
    if requested_type == RoofType.FLAT:
        result.fallback_reason = RoofFallbackReason.NONE
        return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT

    # Hipped roof - check eligibility
    if requested_type == RoofType.HIPPED:
        # Check floor count restriction
        if building.floors > _runtime_config.hipped_max_floors:
            result.fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            building.roof_fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            result.warnings.append(
                f"Hipped roof fallback to flat: too many floors "
                f"({building.floors} > {_runtime_config.hipped_max_floors})"
            )
            return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT

        # Analyze footprint (with gabled constraints for analytical hipped)
        analysis = process_footprint(
            building.footprint,
            max_vertices=max_vertices,
            require_convex=require_convex,
            require_no_holes=require_no_holes,
            house_max_area=_runtime_config.house_max_area,
            house_max_side=_runtime_config.house_max_side,
            house_min_side=_runtime_config.house_min_side,
            house_max_aspect=_runtime_config.house_max_aspect,
        )
        result.footprint_analysis = analysis
        eligibility = analysis.gabled_eligible
        building.footprint_vertex_count = analysis.vertex_count

        # Check eligibility and house-scale
        if eligibility == GabledEligibility.ELIGIBLE and analysis.is_house_scale:
            # ≤4 verts, convex, house-scale → analytical hipped roof
            config = HippedRoofConfig(
                overhang=overhang,
                double_sided_roof=True
            )
            result.fallback_reason = RoofFallbackReason.NONE
            return generate_hipped_roof(building, config), RoofType.HIPPED
        elif _is_polyskel_eligible(eligibility, analysis, building):
            # >4 verts, no holes, house-scale → polyskel hipped roof
            return _generate_polyskel_roof_with_fallback(
                building, overhang, result, analysis
            )
        else:
            # Fallback to flat
            fallback_reason = RoofFallbackReason.from_eligibility(eligibility.value)
            result.fallback_reason = fallback_reason
            building.roof_fallback_reason = fallback_reason

            reason_str = _eligibility_reason(eligibility, analysis)
            result.warnings.append(f"Hipped roof fallback to flat: {reason_str}")
            building.warnings.append(f"Hipped ineligible: {reason_str}")

            return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT

    # Gabled roof - check eligibility with strict criteria
    if requested_type == RoofType.GABLED:
        # Check floor count restriction FIRST
        if building.floors > gabled_max_floors:
            result.fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            building.roof_fallback_reason = RoofFallbackReason.TOO_MANY_FLOORS
            result.warnings.append(
                f"Gabled roof fallback to flat: too many floors "
                f"({building.floors} > {gabled_max_floors})"
            )
            return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT

        # Analyze footprint with current config settings (includes house-scale check)
        analysis = process_footprint(
            building.footprint,
            max_vertices=max_vertices,
            require_convex=require_convex,
            require_no_holes=require_no_holes,
            house_max_area=_runtime_config.house_max_area,
            house_max_side=_runtime_config.house_max_side,
            house_min_side=_runtime_config.house_min_side,
            house_max_aspect=_runtime_config.house_max_aspect,
        )
        result.footprint_analysis = analysis
        eligibility = analysis.gabled_eligible

        # Store vertex count on building
        building.footprint_vertex_count = analysis.vertex_count

        # Check both gabled eligibility AND house-scale requirements
        if eligibility == GabledEligibility.ELIGIBLE:
            # Gabled-eligible, now check house-scale
            if analysis.is_house_scale:
                # All checks passed - generate gabled roof
                # Note: include_gable_walls=False because gable walls are now
                # generated as pentagonal walls in walls.py (Geometry v4)
                config = GabledRoofConfig(
                    overhang=overhang,
                    include_gable_walls=False
                )
                result.fallback_reason = RoofFallbackReason.NONE
                return generate_gabled_roof(building, config), RoofType.GABLED
            else:
                # Gabled-eligible but NOT house-scale: fallback to flat
                size_reason = analysis.house_scale_reason
                fallback_reason = RoofFallbackReason.from_eligibility(size_reason.value)
                result.fallback_reason = fallback_reason
                building.roof_fallback_reason = fallback_reason

                reason_str = _eligibility_reason(size_reason, analysis)
                result.warnings.append(f"Gabled roof fallback to flat: {reason_str}")
                building.warnings.append(f"Not house-scale: {reason_str}")

                logger.debug(
                    f"Building {building.osm_id}: Not house-scale ({reason_str}) "
                    f"[area={analysis.area:.1f}m², OBB={analysis.obb_length:.1f}x{analysis.obb_width:.1f}m, "
                    f"aspect={analysis.aspect_ratio:.2f}]"
                )

                return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT
        elif _is_polyskel_eligible(eligibility, analysis, building):
            # GABLED ineligible (>4 verts) → fallback to polyskel hipped
            # Better a hipped roof than a flat one for a house
            logger.debug(
                f"Building {building.osm_id}: GABLED→HIPPED(polyskel) fallback "
                f"[{analysis.vertex_count} verts]"
            )
            return _generate_polyskel_roof_with_fallback(
                building, overhang, result, analysis
            )
        else:
            # Fallback to flat with detailed reason (gabled eligibility failed)
            fallback_reason = RoofFallbackReason.from_eligibility(eligibility.value)
            result.fallback_reason = fallback_reason
            building.roof_fallback_reason = fallback_reason

            reason_str = _eligibility_reason(eligibility, analysis)
            result.warnings.append(f"Gabled roof fallback to flat: {reason_str}")
            building.warnings.append(f"Gabled ineligible: {reason_str}")

            logger.debug(
                f"Building {building.osm_id}: Gabled fallback ({reason_str}) "
                f"[vertices={analysis.vertex_count}, convex={analysis.is_convex}, "
                f"rect_like={analysis.is_rectangle_like}]"
            )

            return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT

    # Default to flat
    result.fallback_reason = RoofFallbackReason.NONE
    return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT


def _is_polyskel_eligible(
    eligibility: GabledEligibility,
    analysis: FootprintAnalysis,
    building: BuildingRecord
) -> bool:
    """
    Check if a building is eligible for polyskel hipped roof generation.

    A building qualifies if ALL conditions are met:
    1. bpypolyskel is available (Blender environment)
    2. Ineligible for analytical roof due to TOO_MANY_VERTICES
    3. Vertex count is within polyskel limits
    4. No holes in footprint
    5. House-scale dimensions
    6. Floor count within hipped limits

    Args:
        eligibility: Result from gabled eligibility check
        analysis: Footprint analysis results
        building: Building record

    Returns:
        True if building should use polyskel roof generator
    """
    if not POLYSKEL_AVAILABLE:
        return False

    # Only intercept buildings that failed due to vertex count
    if eligibility != GabledEligibility.TOO_MANY_VERTICES:
        return False

    # Check polyskel vertex limit
    if analysis.vertex_count > _runtime_config.polyskel_max_vertices:
        return False

    # No holes (for now)
    if building.footprint.has_holes:
        return False

    # Must be house-scale
    if not analysis.is_house_scale:
        return False

    # Floor count check
    if building.floors > _runtime_config.hipped_max_floors:
        return False

    return True


def _generate_polyskel_roof_with_fallback(
    building: BuildingRecord,
    overhang: float,
    result: BuildingGeneratorResult,
    analysis: FootprintAnalysis
) -> Tuple[MeshData, RoofType]:
    """
    Generate polyskel hipped roof with fallback to flat on failure.

    If polygonize() fails for any reason, falls back to a flat roof
    instead of crashing.

    Args:
        building: Building record
        overhang: Roof overhang distance
        result: Result object for collecting warnings
        analysis: Footprint analysis (for logging)

    Returns:
        (roof_mesh, actual_roof_type)
    """
    polyskel_config = PolyskelRoofConfig(
        overhang=overhang,
        double_sided_roof=True
    )

    try:
        roof_mesh = generate_polyskel_roof(building, polyskel_config)

        # Check if polyskel actually produced geometry
        if len(roof_mesh.faces) == 0:
            logger.warning(
                f"Building {building.osm_id}: polyskel produced no faces, "
                f"falling back to flat [{analysis.vertex_count} verts]"
            )
            result.fallback_reason = RoofFallbackReason.POLYSKEL_FAILED
            building.roof_fallback_reason = RoofFallbackReason.POLYSKEL_FAILED
            result.warnings.append("Polyskel roof produced no faces, fallback to flat")
            return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT

        result.fallback_reason = RoofFallbackReason.NONE
        return roof_mesh, RoofType.HIPPED

    except Exception as e:
        logger.warning(
            f"Building {building.osm_id}: polyskel roof failed: {e}, "
            f"falling back to flat [{analysis.vertex_count} verts]"
        )
        result.fallback_reason = RoofFallbackReason.POLYSKEL_FAILED
        building.roof_fallback_reason = RoofFallbackReason.POLYSKEL_FAILED
        result.warnings.append(f"Polyskel roof failed: {e}, fallback to flat")
        building.warnings.append(f"Polyskel failed: {e}")
        return generate_flat_roof(building, use_global_uv=_runtime_config.flat_roof_terrain_photo), RoofType.FLAT


def _eligibility_reason(
    eligibility: GabledEligibility,
    analysis: Optional[FootprintAnalysis] = None
) -> str:
    """
    Convert eligibility enum to human-readable reason with details.

    Args:
        eligibility: The eligibility result
        analysis: Optional footprint analysis for additional context

    Returns:
        Human-readable reason string
    """
    base_reasons = {
        GabledEligibility.TOO_MANY_VERTICES: "too many vertices",
        GabledEligibility.NOT_CONVEX: "not strictly convex",
        GabledEligibility.NOT_CONVEX_ENOUGH: "not convex enough",
        GabledEligibility.NOT_RECTANGULAR_ENOUGH: "low rectangularity",
        GabledEligibility.BAD_ASPECT_RATIO: "bad aspect ratio",
        GabledEligibility.HAS_HOLES: "footprint has holes",
        GabledEligibility.NOT_RECTANGLE_ANGLES: "angles not 90°",
        GabledEligibility.DEGENERATE: "degenerate footprint",
        # House-scale reasons
        GabledEligibility.TOO_LARGE_AREA: "footprint too large",
        GabledEligibility.TOO_LONG_SIDE: "side too long",
        GabledEligibility.TOO_SHORT_SIDE: "side too short",
        GabledEligibility.TOO_ELONGATED: "too elongated",
    }

    reason = base_reasons.get(eligibility, str(eligibility.value))

    # Add details if analysis available
    if analysis:
        if eligibility == GabledEligibility.TOO_MANY_VERTICES:
            reason = f"{reason} ({analysis.vertex_count}>{GABLED_MAX_VERTICES})"
        elif eligibility == GabledEligibility.NOT_RECTANGULAR_ENOUGH:
            reason = f"{reason} ({analysis.rectangularity:.2f})"
        elif eligibility == GabledEligibility.BAD_ASPECT_RATIO:
            reason = f"{reason} ({analysis.aspect_ratio:.2f})"
        # House-scale details
        elif eligibility == GabledEligibility.TOO_LARGE_AREA:
            reason = f"{reason} ({analysis.area:.1f}m²>{HOUSE_MAX_FOOTPRINT_AREA}m²)"
        elif eligibility == GabledEligibility.TOO_LONG_SIDE:
            max_side = max(analysis.obb_length, analysis.obb_width)
            reason = f"{reason} ({max_side:.1f}m>{HOUSE_MAX_SIDE_LENGTH}m)"
        elif eligibility == GabledEligibility.TOO_SHORT_SIDE:
            min_side = min(analysis.obb_length, analysis.obb_width)
            reason = f"{reason} ({min_side:.1f}m<{HOUSE_MIN_SIDE_LENGTH}m)"
        elif eligibility == GabledEligibility.TOO_ELONGATED:
            reason = f"{reason} (ratio={analysis.aspect_ratio:.2f}>{HOUSE_MAX_ASPECT_RATIO})"

    return reason


def select_roof_type(
    building: BuildingRecord,
    force_flat: bool = False,
    selection_mode: RoofSelectionMode = RoofSelectionMode.GEOMETRY
) -> RoofType:
    """
    Select appropriate roof type for a building.

    Two modes are supported:
    - GEOMETRY (default): Use geometry + category heuristics + area
    - OSM_TAGS_ONLY: Only buildings tagged as houses get pitched roofs

    Args:
        building: Building record
        force_flat: If True, always return FLAT
        selection_mode: How to select roof types (GEOMETRY or OSM_TAGS_ONLY)

    Returns:
        Selected RoofType
    """
    if force_flat:
        return RoofType.FLAT

    # If OSM specifies roof type explicitly (not default GABLED), always use it
    if building.roof_type != RoofType.GABLED:  # GABLED is default
        return building.roof_type

    # =========================================================================
    # OSM_TAGS_ONLY mode: Only HOUSE category gets pitched roofs
    # =========================================================================
    if selection_mode == RoofSelectionMode.OSM_TAGS_ONLY:
        if building.category == BuildingCategory.HOUSE:
            # Tall houses (explicit OSM levels > 2) get flat roofs
            if building.floors > GABLED_MAX_FLOORS:
                return RoofType.FLAT
            return RoofType.GABLED
        else:
            # Everything else (apartments, commercial, industrial, OTHER/yes) = flat
            return RoofType.FLAT

    # =========================================================================
    # GEOMETRY mode (default): Use category + area heuristics
    # =========================================================================
    category = building.category

    if category == BuildingCategory.INDUSTRIAL:
        return RoofType.FLAT

    if category == BuildingCategory.COMMERCIAL:
        # Taller commercial = flat
        if building.floors > 2 or building.height_m > 8:
            return RoofType.FLAT
        return RoofType.GABLED

    if category == BuildingCategory.APARTMENT:
        # Most apartments are flat
        if building.floors > 3 or building.height_m > 10:
            return RoofType.FLAT
        return RoofType.GABLED

    if category == BuildingCategory.HOUSE:
        # Tall houses (explicit OSM levels > 2) get flat roofs
        if building.floors > GABLED_MAX_FLOORS:
            return RoofType.FLAT
        return RoofType.GABLED

    # Category OTHER (building=yes or unknown): use area heuristic
    if category == BuildingCategory.OTHER:
        area = building.footprint.area()

        # Small footprint (<200 m²) = likely a house
        if area < 200:
            return RoofType.GABLED

        # Medium footprint (200-400 m²) = could be house or small commercial
        # Use height as secondary indicator
        if area < 400:
            if building.floors <= 2 and building.height_m <= 8:
                return RoofType.GABLED
            return RoofType.FLAT

        # Large footprint (>400 m²) = likely industrial/commercial
        return RoofType.FLAT

    # Default fallback
    return RoofType.GABLED
