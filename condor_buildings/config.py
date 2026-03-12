"""
Configuration constants for Condor Buildings Generator.

Contains all tunable parameters for building generation, including
geometry constraints, default values, and export settings.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# =============================================================================
# ROOF SELECTION MODE
# =============================================================================

class RoofSelectionMode(Enum):
    """
    Mode for selecting which buildings receive pitched (gabled/hipped) roofs.

    GEOMETRY: (Default) Use geometry constraints + category heuristics + area.
              Buildings with category HOUSE, small OTHER, etc. may get pitched roofs
              if they pass geometry eligibility checks.

    OSM_TAGS_ONLY: Only buildings explicitly tagged as houses in OSM get pitched roofs.
                   This includes: house, detached, villa, bungalow, cabin, farm, etc.
                   Buildings with building=yes or other categories get flat roofs.
                   The --random-hipped flag still works to mix gabled/hipped on houses.
    """
    GEOMETRY = "geometry"
    OSM_TAGS_ONLY = "osm_tags_only"


# =============================================================================
# GEOMETRY CONSTANTS
# =============================================================================

# Patch dimensions (meters)
PATCH_SIZE = 5760.0  # 5760m x 5760m per patch
PATCH_HALF = 2880.0  # Patch extends ±2880m from center

# Terrain grid
TERRAIN_GRID_STEP = 30.0  # 30m terrain grid spacing

# =============================================================================
# FLOOR AND HEIGHT DEFAULTS
# =============================================================================

# Default floor height (meters)
DEFAULT_FLOOR_HEIGHT = 3.0

# Default building heights by category (meters)
DEFAULT_HEIGHT_HOUSE = 6.0       # 2 floors
DEFAULT_HEIGHT_APARTMENT = 9.0   # 3 floors
DEFAULT_HEIGHT_INDUSTRIAL = 6.0  # 1-2 floors, tall ceilings
DEFAULT_HEIGHT_COMMERCIAL = 6.0  # 2 floors
DEFAULT_HEIGHT_OTHER = 6.0       # 2 floors

# =============================================================================
# ROOF PARAMETERS
# =============================================================================

# Roof pitch range (degrees)
ROOF_PITCH_MIN = 30.0
ROOF_PITCH_MAX = 60.0
ROOF_PITCH_DEFAULT = 45.0

# Roof overhang (meters) - LOD0 only
ROOF_OVERHANG_LOD0 = 0.5
ROOF_OVERHANG_LOD1 = 0.0

# Fixed gable height (meters) - Phase 1 geometry requirement
# The gable triangle is always exactly 3.0m tall, independent of building width
# Pitch angle becomes a consequence: pitch = atan(GABLE_HEIGHT_FIXED / half_width)
GABLE_HEIGHT_FIXED = 3.0

# Fixed hipped roof height (meters) - same as gabled for visual consistency
# For hipped roofs, this is the height from eave to ridge (or apex for pyramidal)
HIPPED_HEIGHT_FIXED = 3.0

# =============================================================================
# GABLED ROOF ELIGIBILITY CONSTRAINTS
# =============================================================================

# Maximum vertex count for gabled roof (else fallback to flat)
# Default 4 = only rectangles. Can be increased to 6/8 for experimentation.
# Setting to 4 eliminates almost all roof-mesh failures.
GABLED_MAX_VERTICES = 4

# Require strictly convex footprint for gabled roof
GABLED_REQUIRE_CONVEX = True

# Require no holes (inner rings) for gabled roof
GABLED_REQUIRE_NO_HOLES = True

# Minimum convexity ratio for gabled roof (0-1, 1 = perfectly convex)
# With GABLED_REQUIRE_CONVEX=True, this threshold is less critical
# but still used as a secondary check
GABLED_MIN_CONVEXITY = 0.95

# Minimum rectangularity (area / OBB_area) for gabled roof
# With 4-vertex constraint, rectangles should have ~1.0 rectangularity
# 0.70 is permissive - allows parallelograms and slightly skewed shapes
# which still work well with OBB-based roof generation
GABLED_MIN_RECTANGULARITY = 0.70

# Aspect ratio range for gabled roof (length/width)
GABLED_MIN_ASPECT_RATIO = 0.20
GABLED_MAX_ASPECT_RATIO = 6.0

# Optional: require angles close to 90 degrees for rectangle check
# Tolerance in degrees from 90
# 25° allows parallelograms and rhomboids which work fine with OBB roofs
GABLED_ANGLE_TOLERANCE_DEG = 25.0

# Debug mode for gabled roof generation
# When True, logs detailed info per building (OBB dimensions, ridge direction, etc.)
DEBUG_GABLED_ROOFS = False

# Debug mode for hipped roof generation
DEBUG_HIPPED_ROOFS = False

# =============================================================================
# POLYSKEL ROOF CONSTRAINTS (bpypolyskel-based hipped roofs for >4 vertices)
# =============================================================================

# Maximum vertex count for polyskel roof generation
# Buildings with more vertices than this get flat roofs.
# bpypolyskel handles any count, but very complex footprints
# (churches, stadiums) shouldn't get residential-style roofs.
# 12 vertices covers most L-shaped, T-shaped, and U-shaped houses.
POLYSKEL_MAX_VERTICES = 12

# Debug mode for polyskel roof generation
DEBUG_POLYSKEL_ROOFS = False

# =============================================================================
# FLOOR RESTRICTIONS FOR ROOF TYPES
# =============================================================================

# Maximum floors allowed for gabled roofs
# Gabled roofs look unrealistic on buildings taller than 2 floors
GABLED_MAX_FLOORS = 2

# Maximum floors allowed for hipped roofs (future use)
HIPPED_MAX_FLOORS = 2

# =============================================================================
# HOUSE-SCALE SIZE CONSTRAINTS FOR GABLED ROOFS
# =============================================================================
# Buildings must be "house-scale" to receive gabled roofs.
# Large rectangular buildings (apartments, industrial, office) get flat roofs.

# Maximum footprint area for house classification (square meters)
# Typical houses: 50-200 m², max ~300 m²
# Above this = apartment/industrial/commercial -> flat roof
HOUSE_MAX_FOOTPRINT_AREA = 360.0  # +20% from 300

# Maximum side length for house classification (meters)
# Typical houses: max ~20m on longest side
# Buildings longer than this are likely not houses
HOUSE_MAX_SIDE_LENGTH = 30.0  # +20% from 25

# Minimum side length for house classification (meters)
# Very small structures (sheds, garages) below this get flat roofs
HOUSE_MIN_SIDE_LENGTH = 3.2  # -20% from 4

# Maximum aspect ratio for house classification
# Typical houses: 1.0 to 3.0 (L/W ratio)
# Very elongated buildings (ratio > 4) are likely row houses or industrial
HOUSE_MAX_ASPECT_RATIO = 4.8  # +20% from 4.0

# =============================================================================
# FLOOR Z SOLVER
# =============================================================================

# Default epsilon offset below terrain (meters)
FLOOR_Z_EPSILON = 0.3

# Spatial index cell size for terrain queries
TERRAIN_CELL_SIZE = 60.0  # 2x terrain grid step

# =============================================================================
# FOOTPRINT PROCESSING
# =============================================================================

# Collinear point removal threshold (square meters)
# Points forming triangles smaller than this area are removed
COLLINEAR_EPSILON = 0.01

# =============================================================================
# TEXTURE ATLAS SETTINGS (Phase 2)
# =============================================================================

# Atlas dimensions (pixels)
ATLAS_WIDTH_PX = 512
ATLAS_HEIGHT_PX = 12288

# Roof patterns (TOP of atlas - high V values)
# 6 patterns, each 512x512 pixels
# UV Convention: V=1.0 at atlas top (pixel y=0), V=0.0 at atlas bottom (pixel y=12288)
ROOF_PATTERN_COUNT = 6
ROOF_PATTERN_HEIGHT_PX = 512
ROOF_SLICE_V = ROOF_PATTERN_HEIGHT_PX / ATLAS_HEIGHT_PX  # 0.0416666667

# CORRECTED: Roof region is at TOP of atlas (high V values)
ROOF_REGION_V_MIN = 1.0 - (ROOF_PATTERN_COUNT * ROOF_SLICE_V)  # 0.75
ROOF_REGION_V_MAX = 1.0  # Top of atlas

# Facade styles (BELOW roofs - low V values)
# 12 styles in the remaining V space [0.0, 0.75]
# Each style has 3 sections (gable, upper, ground) of 256px each = 768px total
FACADE_STYLE_COUNT = 12
FACADE_STYLE_HEIGHT_PX = 768
FACADE_SECTION_HEIGHT_PX = 256

# CORRECTED: Facade region is BELOW roof region
FACADE_REGION_V_MAX = ROOF_REGION_V_MIN  # 0.75 (top of facade region)
FACADE_REGION_V_MIN = 0.0  # Bottom of atlas

# Wall module size (meters) - 3m = 1.0 UV unit
WALL_MODULE_M = 3.0

# Wall UV mapping constants (Phase 2 update - 2025-01-21)
# New Wiek rule: 3m blocks with U offset to reduce door frequency
WALL_BLOCK_METERS = 3.0       # Each 3m block maps to WALL_BLOCK_U
WALL_BLOCK_U = 1.0 / 3.0      # U width per 3m block (0.3333...)
WALL_U_OFFSET = 1.0 / 3.0     # Start U offset (skip door section at U 0.0-0.33)
WALL_MIN_METERS = 3.0         # Minimum wall length for UV mapping

# =============================================================================
# HIGHRISE TEXTURE ATLAS SETTINGS (Highrise_atlas.dds)
# =============================================================================
# Separate atlas for APARTMENT and COMMERCIAL building walls.
# 2048x12288 pixels, 12 regions of 2048x1024 each.
# Top 6 regions = apartment textures, bottom 6 = commercial textures.
# Each region has 4 floors at 256px per floor (256px = 3 meters).

HIGHRISE_ATLAS_WIDTH_PX = 2048
HIGHRISE_ATLAS_HEIGHT_PX = 12288

# Region layout
HIGHRISE_REGION_COUNT = 12           # 6 apartment + 6 commercial
HIGHRISE_REGION_HEIGHT_PX = 1024     # Each region = 2048x1024

# Floor dimensions within each region
HIGHRISE_FLOOR_HEIGHT_PX = 256       # 256px per floor = 3 meters
HIGHRISE_FLOORS_PER_REGION = 4       # 4 floors per region (1024 / 256)
HIGHRISE_UPPER_FLOORS = 3            # Overflow floors per cycle (top 768px)
HIGHRISE_UPPER_SECTION_HEIGHT_PX = 768  # 3 upper floors * 256px

# Category region ranges (indices)
HIGHRISE_APARTMENT_REGION_START = 0   # Regions 0-5 for apartments
HIGHRISE_APARTMENT_REGION_END = 6
HIGHRISE_COMMERCIAL_REGION_START = 6  # Regions 6-11 for commercial
HIGHRISE_COMMERCIAL_REGION_END = 12

# U mapping: 2048px width at 256px/3m = 24 meters per full U span
HIGHRISE_U_METERS = 24.0
HIGHRISE_FLOOR_METERS = 3.0           # Same as DEFAULT_FLOOR_HEIGHT

# =============================================================================
# EXPORT SETTINGS
# =============================================================================

# OBJ export precision (decimal places)
OBJ_VERTEX_PRECISION = 6
OBJ_UV_PRECISION = 6

# Export with per-building groups (for potential collision use)
OBJ_EXPORT_GROUPS = True

# =============================================================================
# REPORT SETTINGS
# =============================================================================

# Number of top buildings by vertex count to include in report
REPORT_TOP_BUILDINGS = 10


# =============================================================================
# RUNTIME CONFIGURATION
# =============================================================================

@dataclass
class PipelineConfig:
    """
    Runtime configuration for the building generation pipeline.

    This class holds all configurable parameters that can be
    adjusted per-run via CLI arguments or programmatically.
    """

    # Patch metadata
    patch_id: str = ""
    patch_dir: str = "./"
    zone_number: int = 0
    translate_x: float = 0.0
    translate_y: float = 0.0

    # Random seed for deterministic generation
    global_seed: int = 12345

    # Floor Z
    floor_z_epsilon: float = FLOOR_Z_EPSILON

    # Roof
    roof_overhang_lod0: float = ROOF_OVERHANG_LOD0
    roof_pitch_default: float = ROOF_PITCH_DEFAULT

    # Gabled roof eligibility (can override module-level constants)
    gabled_max_vertices: int = GABLED_MAX_VERTICES
    gabled_require_convex: bool = GABLED_REQUIRE_CONVEX
    gabled_require_no_holes: bool = GABLED_REQUIRE_NO_HOLES
    gabled_min_rectangularity: float = GABLED_MIN_RECTANGULARITY

    # House-scale size constraints for gabled roofs
    house_max_footprint_area: float = HOUSE_MAX_FOOTPRINT_AREA
    house_max_side_length: float = HOUSE_MAX_SIDE_LENGTH
    house_min_side_length: float = HOUSE_MIN_SIDE_LENGTH
    house_max_aspect_ratio: float = HOUSE_MAX_ASPECT_RATIO

    # Roof geometry (new configurable parameters)
    gable_height: float = GABLE_HEIGHT_FIXED
    gabled_max_floors: int = GABLED_MAX_FLOORS
    hipped_max_floors: int = HIPPED_MAX_FLOORS
    polyskel_max_vertices: int = POLYSKEL_MAX_VERTICES

    # Export
    export_groups: bool = OBJ_EXPORT_GROUPS
    output_dir: str = "./output"

    # Debug/report
    verbose: bool = False
    report_top_n: int = REPORT_TOP_BUILDINGS

    # Debug: process only a single building by OSM ID
    debug_osm_id: Optional[str] = None

    # Testing: randomly assign hipped roof to 50% of eligible buildings
    random_hipped: bool = False

    # Roof selection mode: "geometry" (default) or "osm_tags_only"
    # - geometry: Use geometry + category heuristics (current behavior)
    # - osm_tags_only: Only buildings tagged as houses get pitched roofs
    roof_selection_mode: RoofSelectionMode = RoofSelectionMode.GEOMETRY

    # Optional: explicit path to OSM file (overrides auto-detection)
    # Used by Blender addon for downloaded OSM files
    osm_path: Optional[str] = None

    def __post_init__(self):
        """Validate configuration values."""
        if self.floor_z_epsilon < 0:
            raise ValueError("floor_z_epsilon must be non-negative")

        if self.roof_overhang_lod0 < 0:
            raise ValueError("roof_overhang_lod0 must be non-negative")

        if not (ROOF_PITCH_MIN <= self.roof_pitch_default <= ROOF_PITCH_MAX):
            raise ValueError(
                f"roof_pitch_default must be between {ROOF_PITCH_MIN} and {ROOF_PITCH_MAX}"
            )

        if self.gabled_max_vertices < 3:
            raise ValueError("gabled_max_vertices must be at least 3")


# Default configuration instance
DEFAULT_CONFIG = PipelineConfig()
