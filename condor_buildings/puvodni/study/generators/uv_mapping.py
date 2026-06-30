"""
UV Mapping utilities for Condor Buildings Generator.

This module provides UV coordinate calculations for the texture atlas system.

Atlas layout (with V=1.0 at top, V=0.0 at bottom):
- 6 roof patterns (TOP of atlas, V in [0.75, 1.0])
- 12 facade styles (BELOW roofs, V in [0.0, 0.75])
  - Each facade has 3 sections: gable (top), upper (middle), ground (bottom)

UV Rules:
- U can exceed 1.0 (horizontal wrapping allowed)
- V must stay within selected slice (no vertical wrapping)
"""

import random
from typing import List, Tuple

from ..config import (
    ROOF_PATTERN_COUNT,
    ROOF_SLICE_V,
    ROOF_REGION_V_MAX,
    FACADE_STYLE_COUNT,
    FACADE_REGION_V_MAX,
    FACADE_REGION_V_MIN,
    WALL_MODULE_M,
    WALL_BLOCK_METERS,
    WALL_BLOCK_U,
    WALL_U_OFFSET,
    WALL_MIN_METERS,
    HIGHRISE_ATLAS_HEIGHT_PX,
    HIGHRISE_REGION_COUNT,
    HIGHRISE_REGION_HEIGHT_PX,
    HIGHRISE_FLOOR_HEIGHT_PX,
    HIGHRISE_FLOORS_PER_REGION,
    HIGHRISE_UPPER_FLOORS,
    HIGHRISE_UPPER_SECTION_HEIGHT_PX,
    HIGHRISE_APARTMENT_REGION_START,
    HIGHRISE_APARTMENT_REGION_END,
    HIGHRISE_COMMERCIAL_REGION_START,
    HIGHRISE_COMMERCIAL_REGION_END,
    HIGHRISE_U_METERS,
)
from ..models.building import BuildingCategory

# Facade dimensions calculated to fit within [0.0, 0.75] V range
# Total facade region: 0.75 - 0.0 = 0.75
# 12 styles fit proportionally: 0.75 / 12 = 0.0625 per style
# 3 sections per style: 0.0625 / 3 = 0.020833... per section
FACADE_REGION_V_SIZE = FACADE_REGION_V_MAX - FACADE_REGION_V_MIN  # 0.75
FACADE_BLOCK_V = FACADE_REGION_V_SIZE / FACADE_STYLE_COUNT  # 0.0625
FACADE_SECTION_V = FACADE_BLOCK_V / 3.0  # 0.020833...


# =============================================================================
# VARIATION SELECTION
# =============================================================================

def select_building_variations(seed: int) -> Tuple[int, int]:
    """
    Select roof and facade variations for a building using its seed.

    Provides deterministic random selection - same seed always produces
    the same variation choices.

    Args:
        seed: Building seed (typically from BuildingRecord.seed)

    Returns:
        Tuple of (roof_index [0..5], facade_index [0..11])
    """
    rng = random.Random(seed)
    roof_index = rng.randint(0, ROOF_PATTERN_COUNT - 1)
    facade_index = rng.randint(0, FACADE_STYLE_COUNT - 1)
    return roof_index, facade_index


# =============================================================================
# ATLAS V RANGE HELPERS
# =============================================================================

def get_roof_v_range(roof_index: int) -> Tuple[float, float]:
    """
    Get the V range for a roof pattern slice.

    CORRECTED: Roofs are at TOP of atlas (high V values).
    V = 1.0 at atlas top (pixel y = 0).

    Args:
        roof_index: Roof pattern index [0..5]

    Returns:
        (v_min, v_max) for the roof pattern slice
        v_max is closer to 1.0 (ridge/top of texture)
        v_min is closer to 0.75 (eave/bottom of roof region)

    Example:
        roof_index=0 -> (0.9583, 1.0)   - TOP of atlas
        roof_index=5 -> (0.75, 0.7917)  - bottom of roof region
    """
    if not 0 <= roof_index < ROOF_PATTERN_COUNT:
        raise ValueError(f"roof_index must be 0-{ROOF_PATTERN_COUNT-1}, got {roof_index}")

    # V decreases as roof_index increases (going DOWN the atlas)
    v_max = ROOF_REGION_V_MAX - roof_index * ROOF_SLICE_V       # = 1.0 - i * 0.0417
    v_min = ROOF_REGION_V_MAX - (roof_index + 1) * ROOF_SLICE_V # = 1.0 - (i+1) * 0.0417
    return v_min, v_max


def get_facade_section_v_range(facade_index: int, section: str) -> Tuple[float, float]:
    """
    Get the V range for a specific facade section.

    CORRECTED: Facades are BELOW roofs in atlas (V in [0.0, 0.75]).
    Each facade block has 3 sections stacked vertically (in atlas space):
    - 'gable': TOP of block (highest V within the style) - no windows
    - 'upper': MIDDLE of block - windows only
    - 'ground': BOTTOM of block (lowest V within the style) - doors + windows

    Args:
        facade_index: Facade style index [0..11]
        section: Which section - 'ground', 'upper', or 'gable'

    Returns:
        (v_min, v_max) for the section

    Example:
        facade_index=0, section='gable'  -> (0.7292, 0.75)
        facade_index=0, section='upper'  -> (0.7083, 0.7292)
        facade_index=0, section='ground' -> (0.6875, 0.7083)
    """
    if not 0 <= facade_index < FACADE_STYLE_COUNT:
        raise ValueError(f"facade_index must be 0-{FACADE_STYLE_COUNT-1}, got {facade_index}")

    # Section offsets from TOP of the facade block
    # 'gable' is at the top (offset 0), 'ground' is at the bottom (offset 2)
    section_offsets = {
        'gable': 0,   # Top of block (highest V)
        'upper': 1,   # Middle of block
        'ground': 2,  # Bottom of block (lowest V)
    }

    if section not in section_offsets:
        raise ValueError(f"section must be 'ground', 'upper', or 'gable', got '{section}'")

    # Facade block starts at this V (top of the block)
    # V decreases as facade_index increases (going DOWN the atlas)
    block_v_top = FACADE_REGION_V_MAX - facade_index * FACADE_BLOCK_V  # e.g., 0.75 for index 0

    # Section within block (offset from top)
    section_offset = section_offsets[section]
    v_max = block_v_top - section_offset * FACADE_SECTION_V
    v_min = v_max - FACADE_SECTION_V

    return v_min, v_max


# =============================================================================
# U SPAN CALCULATION
# =============================================================================

def compute_wall_u_span(wall_width_m: float) -> float:
    """
    Compute the U span for a wall of given width.

    New Wiek rule (2025-01-21):
    - Round UP to nearest 3m multiple: ceil(width / 3.0) * 3.0
    - Minimum 3.0m to avoid zero-width UV
    - Each 3m block = 0.33 U

    Args:
        wall_width_m: Wall width in meters

    Returns:
        U span (can exceed 1.0 for wrapping)

    Examples:
        2.0m  -> ceil(2.0/3)*3 = 3.0m  -> 3/3 * 0.33 = 0.33
        10.6m -> ceil(10.6/3)*3 = 12.0m -> 12/3 * 0.33 = 1.3333
        1.0m  -> ceil(1.0/3)*3 = 3.0m  -> 3/3 * 0.33 = 0.33
        3.0m  -> ceil(3.0/3)*3 = 3.0m  -> 3/3 * 0.33 = 0.33
    """
    import math
    rounded_width = math.ceil(wall_width_m / WALL_BLOCK_METERS) * WALL_BLOCK_METERS
    if rounded_width < WALL_MIN_METERS:
        rounded_width = WALL_MIN_METERS
    return (rounded_width / WALL_BLOCK_METERS) * WALL_BLOCK_U


def compute_wall_u_range(wall_width_m: float) -> Tuple[float, float]:
    """
    Compute the U start and end for a wall.

    Starts at U_OFFSET (0.33) to skip door section (U 0.0-0.33).
    This reduces door frequency and makes doors appear more centrally
    when tiling happens.

    Args:
        wall_width_m: Wall width in meters

    Returns:
        (u_start, u_end) tuple
    """
    u_span = compute_wall_u_span(wall_width_m)
    u_start = WALL_U_OFFSET
    u_end = u_start + u_span
    return u_start, u_end


# =============================================================================
# ROOF UV MAPPING
# =============================================================================

def compute_roof_slope_uvs(
    roof_length_m: float,
    roof_width_m: float,
    roof_index: int
) -> List[Tuple[float, float]]:
    """
    Compute UV coordinates for a roof slope quad.

    CORRECTED UV mapping (V=1.0 at atlas top):
    - V: eave at v_min (lower V), ridge at v_max (higher V toward atlas top)
    - U: along ridge direction, u_span = roof_length / roof_width

    Vertex order (matches roof_gabled.py quad generation):
    - [0] eave-front (v=v_min, at eave edge - lower V)
    - [1] eave-back (v=v_min, at eave edge - lower V)
    - [2] ridge-back (v=v_max, at ridge - higher V)
    - [3] ridge-front (v=v_max, at ridge - higher V)

    Args:
        roof_length_m: Length along ridge direction (meters)
        roof_width_m: Width from ridge to eave (half the roof span, meters)
        roof_index: Roof pattern index [0..5]

    Returns:
        List of 4 (u, v) tuples for quad corners
    """
    v_min, v_max = get_roof_v_range(roof_index)

    # Preserve aspect ratio: U spans proportionally to dimensions
    if roof_width_m > 0.01:
        u_span = roof_length_m / roof_width_m
    else:
        u_span = 1.0

    # Quad corners: eave at v_min (lower V), ridge at v_max (higher V)
    # With V=1.0 at atlas top, higher V means closer to atlas top
    return [
        (0.0, v_min),      # [0] eave-front
        (u_span, v_min),   # [1] eave-back
        (u_span, v_max),   # [2] ridge-back
        (0.0, v_max),      # [3] ridge-front
    ]


# =============================================================================
# WALL UV MAPPING
# =============================================================================

def compute_wall_quad_uvs(
    wall_width_m: float,
    facade_index: int,
    section: str
) -> List[Tuple[float, float]]:
    """
    Compute UV coordinates for a rectangular wall quad.

    Maps the wall to a single facade section (3m tall).
    Uses U offset (0.33) to skip door section.

    Vertex order (matches walls.py quad generation - CCW from outside):
    - [0] bottom-left
    - [1] bottom-right
    - [2] top-right
    - [3] top-left

    Args:
        wall_width_m: Wall width in meters
        facade_index: Facade style index [0..11]
        section: Which section - 'ground', 'upper', or 'gable'

    Returns:
        List of 4 (u, v) tuples for quad corners
    """
    u_start, u_end = compute_wall_u_range(wall_width_m)
    v_min, v_max = get_facade_section_v_range(facade_index, section)

    return [
        (u_start, v_min),  # [0] bottom-left
        (u_end, v_min),    # [1] bottom-right
        (u_end, v_max),    # [2] top-right
        (u_start, v_max),  # [3] top-left
    ]


def compute_gable_triangle_uvs(
    wall_width_m: float,
    facade_index: int
) -> List[Tuple[float, float]]:
    """
    Compute UV coordinates for a gable triangle face.

    The gable triangle maps to the 'gable' section (no windows).
    Triangle is 3m tall (fixed gable height).
    Uses U offset (0.33) to skip door section.

    Vertex order (matches walls.py triangle generation):
    - [0] left corner (at eave/wall_top)
    - [1] right corner (at eave/wall_top)
    - [2] apex (at ridge)

    Args:
        wall_width_m: Base width of triangle (wall width)
        facade_index: Facade style index [0..11]

    Returns:
        List of 3 (u, v) tuples for triangle corners
    """
    u_start, u_end = compute_wall_u_range(wall_width_m)
    u_center = (u_start + u_end) / 2.0
    v_min, v_max = get_facade_section_v_range(facade_index, 'gable')

    return [
        (u_start, v_min),   # [0] left corner (base)
        (u_end, v_min),     # [1] right corner (base)
        (u_center, v_max),  # [2] apex (centered)
    ]


def compute_pentagon_wall_uvs(
    wall_width_m: float,
    facade_index: int,
    building_floors: int
) -> List[Tuple[float, float]]:
    """
    Compute UV coordinates for a pentagonal gable wall.

    Pentagon covers from floor_z to ridge_z, so it spans:
    - Ground floor (3m) -> ground section
    - Upper floors (3m each) -> upper section (repeated)
    - Gable triangle (3m) -> gable section

    For multi-floor buildings, the pentagon cannot cleanly map to
    multiple V sections, so we use a simplified approach:
    - Map the entire pentagon proportionally within the combined sections

    Uses U offset (0.33) to skip door section.

    Vertex order (matches walls.py pentagon generation - CCW):
    - [0] bottom-left (floor_z)
    - [1] bottom-right (floor_z)
    - [2] top-right (wall_top_z / eave)
    - [3] apex (ridge_z)
    - [4] top-left (wall_top_z / eave)

    Args:
        wall_width_m: Wall width at base
        facade_index: Facade style index [0..11]
        building_floors: Number of floors (determines wall height)

    Returns:
        List of 5 (u, v) tuples for pentagon corners
    """
    u_start, u_end = compute_wall_u_range(wall_width_m)
    u_center = (u_start + u_end) / 2.0

    # Get the three section V ranges
    v_ground_min, v_ground_max = get_facade_section_v_range(facade_index, 'ground')
    v_upper_min, v_upper_max = get_facade_section_v_range(facade_index, 'upper')
    v_gable_min, v_gable_max = get_facade_section_v_range(facade_index, 'gable')

    # Total wall height = floors * 3m (wall) + 3m (gable)
    total_height = (building_floors * 3.0) + 3.0  # wall + gable
    wall_height = building_floors * 3.0

    # For pentagon UV mapping, we need to map:
    # - Bottom vertices (floor_z) to ground section bottom
    # - Eave vertices (wall_top_z) to appropriate height
    # - Apex (ridge_z) to gable section top

    # Simple proportional mapping based on height ratios
    # Ground floor: 0 to 3m
    # Wall top: at floor * 3m
    # Ridge: at floor * 3m + 3m

    # For now, use a simplified mapping:
    # - Bottom at ground section bottom
    # - Eave at upper section top (or ground section top if 1 floor)
    # - Apex at gable section top

    if building_floors == 1:
        # 1 floor: ground + gable only (should use separated faces, but fallback)
        v_bottom = v_ground_min
        v_eave = v_ground_max
        v_apex = v_gable_max
    else:
        # Multi-floor: use upper section for middle
        v_bottom = v_ground_min
        v_eave = v_upper_max  # Top of upper section
        v_apex = v_gable_max

    return [
        (u_start, v_bottom),   # [0] bottom-left
        (u_end, v_bottom),     # [1] bottom-right
        (u_end, v_eave),       # [2] top-right (eave)
        (u_center, v_apex),    # [3] apex
        (u_start, v_eave),     # [4] top-left (eave)
    ]


# =============================================================================
# MULTI-FLOOR WALL UV HELPERS
# =============================================================================

def compute_multi_floor_wall_uvs(
    wall_width_m: float,
    building_floors: int,
    facade_index: int,
    is_gable_end: bool = False
) -> List[List[Tuple[float, float]]]:
    """
    Compute UV coordinates for a multi-floor wall, returning UVs per segment.

    This function handles walls that span multiple floors by returning
    separate UV sets for each 3m vertical segment.

    For walls that need to be split into multiple quads (one per floor),
    this provides the UVs for each segment.

    Uses U offset (0.33) to skip door section.

    Args:
        wall_width_m: Wall width in meters
        building_floors: Number of floors
        facade_index: Facade style index [0..11]
        is_gable_end: If True, this is a gable end (no upper floors visible)

    Returns:
        List of UV lists, one per floor segment:
        - [0] = ground floor UVs (4 tuples)
        - [1..n] = upper floor UVs (4 tuples each)

    Note: Current geometry uses single quads per wall, so this is for
    future use if walls are split per-floor.
    """
    u_start, u_end = compute_wall_u_range(wall_width_m)
    segments = []

    # Ground floor
    v_min, v_max = get_facade_section_v_range(facade_index, 'ground')
    segments.append([
        (u_start, v_min),
        (u_end, v_min),
        (u_end, v_max),
        (u_start, v_max),
    ])

    # Upper floors
    v_min, v_max = get_facade_section_v_range(facade_index, 'upper')
    for _ in range(1, building_floors):
        segments.append([
            (u_start, v_min),
            (u_end, v_min),
            (u_end, v_max),
            (u_start, v_max),
        ])

    return segments


def compute_sidewall_continuous_uvs(
    wall_width_m: float,
    building_floors: int,
    facade_index: int
) -> List[Tuple[float, float]]:
    """
    Compute UV coordinates for a sidewall as a SINGLE continuous quad.

    Unlike compute_multi_floor_wall_uvs() which returns UVs per floor segment,
    this returns 4 UVs for a single quad that spans the entire wall height.

    The UV mapping uses a continuous V range that covers multiple facade sections:
    - 1 floor (3m): Maps to ground section only (doors + windows)
    - 2 floors (6m): Maps to ground + upper sections (continuous V range)

    Uses U offset (0.33) to skip door section.

    Atlas layout:
    - H = 12288 px total
    - facade_top_px = 3072 (where facade region starts, below roofs)
    - style_px_h = 768 (each of 12 facade styles)
    - section_px_h = 256 (gable / upper / ground, top to bottom within style)

    V convention: V=1.0 at atlas top (pixel 0), V=0.0 at atlas bottom (pixel 12288)

    For style j, sections are arranged (top to bottom in pixel space):
    - Gable:   y in [style_top_y + 0*256, style_top_y + 1*256]
    - Upper:   y in [style_top_y + 1*256, style_top_y + 2*256]
    - Ground:  y in [style_top_y + 2*256, style_top_y + 3*256]

    Args:
        wall_width_m: Wall width in meters
        building_floors: Number of floors (1 or 2 for gabled buildings)
        facade_index: Facade style index [0..11]

    Returns:
        List of 4 (u, v) tuples for quad corners:
        - [0] bottom-left
        - [1] bottom-right
        - [2] top-right
        - [3] top-left
    """
    # Constants from atlas layout
    H = 12288.0  # Total atlas height in pixels
    facade_top_px = 3072.0  # Where facade region starts (below roofs)
    style_px_h = 768.0  # Height of each facade style
    section_px_h = 256.0  # Height of each section (gable/upper/ground)

    # U range calculation with offset
    u_start, u_end = compute_wall_u_range(wall_width_m)

    # Calculate style boundaries in pixels (top-down)
    style_top_y = facade_top_px + facade_index * style_px_h

    # Section boundaries within style (top-down):
    # gable:  [style_top_y + 0*256, style_top_y + 1*256]
    # upper:  [style_top_y + 1*256, style_top_y + 2*256]
    # ground: [style_top_y + 2*256, style_top_y + 3*256]
    upper_top_y = style_top_y + section_px_h  # top of upper/windows section
    ground_top_y = style_top_y + 2 * section_px_h  # top of ground section
    ground_bottom_y = style_top_y + 3 * section_px_h  # bottom of ground section

    # Helper: convert pixel Y to V coordinate
    # V(y) = 1.0 - (y / H)
    def pixel_to_v(y_px: float) -> float:
        return 1.0 - (y_px / H)

    if building_floors == 1:
        # 1 floor: Map entire wall to ground section only
        v_top = pixel_to_v(ground_top_y)  # Top of ground section
        v_bottom = pixel_to_v(ground_bottom_y)  # Bottom of ground section
    else:
        # 2 floors (or more, but gabled max is 2): Map to ground + upper sections
        # V_top = top of upper section (windows)
        # V_bottom = bottom of ground section (doors + windows)
        v_top = pixel_to_v(upper_top_y)  # Top of upper/windows section
        v_bottom = pixel_to_v(ground_bottom_y)  # Bottom of ground section

    # Return 4 UVs for single quad (CCW from bottom-left)
    return [
        (u_start, v_bottom),  # [0] bottom-left
        (u_end, v_bottom),    # [1] bottom-right
        (u_end, v_top),       # [2] top-right
        (u_start, v_top),     # [3] top-left
    ]


# =============================================================================
# HIGHRISE WALL UV MAPPING (Highrise_atlas.dds)
# =============================================================================

def select_highrise_region(seed: int, category: BuildingCategory) -> int:
    """
    Select a texture region for a highrise building.

    Deterministic selection based on building seed.
    Apartments use regions 0-5 (top of atlas), commercial uses 6-11 (bottom).

    Args:
        seed: Building seed for deterministic selection
        category: BuildingCategory (APARTMENT or COMMERCIAL)

    Returns:
        Region index [0..11]
    """
    rng = random.Random(seed)
    if category == BuildingCategory.COMMERCIAL:
        return rng.randint(
            HIGHRISE_COMMERCIAL_REGION_START,
            HIGHRISE_COMMERCIAL_REGION_END - 1,
        )
    else:
        # APARTMENT (and any other category routed here)
        return rng.randint(
            HIGHRISE_APARTMENT_REGION_START,
            HIGHRISE_APARTMENT_REGION_END - 1,
        )


def get_highrise_region_v_range(region_index: int) -> Tuple[float, float]:
    """
    Get the full V range for a highrise texture region.

    Region 0 is at the TOP of the atlas (highest V).
    Region 11 is at the BOTTOM.

    Args:
        region_index: Region index [0..11]

    Returns:
        (v_bottom, v_top) for the full 1024px region
    """
    if not 0 <= region_index < HIGHRISE_REGION_COUNT:
        raise ValueError(
            f"region_index must be 0-{HIGHRISE_REGION_COUNT - 1}, got {region_index}"
        )

    H = float(HIGHRISE_ATLAS_HEIGHT_PX)
    region_h = float(HIGHRISE_REGION_HEIGHT_PX)

    region_top_px = region_index * region_h
    region_bottom_px = region_top_px + region_h

    v_top = 1.0 - (region_top_px / H)
    v_bottom = 1.0 - (region_bottom_px / H)

    return v_bottom, v_top


def compute_highrise_wall_u_span(wall_width_m: float) -> float:
    """
    Compute the U span for a highrise wall.

    For the highrise atlas: 2048px width, 256px = 3m, so U=1.0 = 24m.
    U starts at 0 (no door offset like houses).
    U can exceed 1.0 (horizontal wrapping).

    Wall width is rounded UP to nearest 3m block.

    Args:
        wall_width_m: Wall width in meters

    Returns:
        U span value
    """
    import math
    rounded = math.ceil(wall_width_m / 3.0) * 3.0
    rounded = max(rounded, 3.0)
    return rounded / HIGHRISE_U_METERS


def compute_highrise_wall_uvs(
    wall_width_m: float,
    building_floors: int,
    region_index: int
) -> List[List[Tuple[float, float]]]:
    """
    Compute UV coordinates for highrise wall quads.

    Returns a list of quad UV sets. Each quad covers multiple floors:
    - First quad: up to 4 floors, maps to full 1024px region
    - Subsequent quads: up to 3 floors each, maps to upper 768px of region

    Within a region (1024px, low V = bottom, high V = top):
    - Bottom 256px = ground floor (lowest V)
    - Next 256px = floor 2
    - Next 256px = floor 3
    - Top 256px = floor 4 (highest V)

    Overflow quads map from the bottom of the upper section (floor 2 position)
    growing upward:
    - 1 overflow floor -> bottom 256px of upper section
    - 2 overflow floors -> bottom 512px of upper section
    - 3 overflow floors -> full 768px upper section
    - 4+ overflow -> new cycle from bottom of upper section

    Args:
        wall_width_m: Wall width in meters
        building_floors: Total number of floors
        region_index: Texture region index [0..11]

    Returns:
        List of quad UV sets. Each set has 4 (u,v) tuples:
        [bottom-left, bottom-right, top-right, top-left]
    """
    H = float(HIGHRISE_ATLAS_HEIGHT_PX)
    region_h = float(HIGHRISE_REGION_HEIGHT_PX)
    floor_px = float(HIGHRISE_FLOOR_HEIGHT_PX)

    u_start = 0.0
    u_end = compute_highrise_wall_u_span(wall_width_m)

    # Region pixel boundaries (from atlas top)
    region_top_px = region_index * region_h

    # Upper section boundary: top 768px of region (skip ground floor at bottom)
    # In pixel coords from atlas top:
    #   upper top = region_top_px (top of region)
    #   upper bottom = region_top_px + 768 (boundary between upper and ground)
    upper_bottom_px = region_top_px + HIGHRISE_UPPER_SECTION_HEIGHT_PX

    def px_to_v(y_px: float) -> float:
        return 1.0 - (y_px / H)

    quads = []
    remaining = building_floors

    # --- First quad: up to 4 floors, maps to full region ---
    first_quad_floors = min(HIGHRISE_FLOORS_PER_REGION, remaining)

    if first_quad_floors == HIGHRISE_FLOORS_PER_REGION:
        # Full 4 floors -> full region
        v_bottom = px_to_v(region_top_px + region_h)
        v_top = px_to_v(region_top_px)
    else:
        # Fewer than 4 floors -> bottom portion of region
        # Ground floor at bottom of region, growing upward
        region_bottom_px = region_top_px + region_h
        v_bottom = px_to_v(region_bottom_px)
        v_top = px_to_v(region_bottom_px - first_quad_floors * floor_px)

    quads.append([
        (u_start, v_bottom),
        (u_end, v_bottom),
        (u_end, v_top),
        (u_start, v_top),
    ])
    remaining -= first_quad_floors

    # --- Subsequent quads: up to 3 floors each, upper section only ---
    while remaining > 0:
        overflow_floors = min(HIGHRISE_UPPER_FLOORS, remaining)

        # Map from bottom of upper section growing upward
        # N overflow floors: pixel range (768 - N*256) to 768 from region top
        overflow_top_px = region_top_px + (HIGHRISE_UPPER_SECTION_HEIGHT_PX - overflow_floors * floor_px)
        overflow_bottom_px = upper_bottom_px

        v_bottom = px_to_v(overflow_bottom_px)
        v_top = px_to_v(overflow_top_px)

        quads.append([
            (u_start, v_bottom),
            (u_end, v_bottom),
            (u_end, v_top),
            (u_start, v_top),
        ])
        remaining -= overflow_floors

    return quads
