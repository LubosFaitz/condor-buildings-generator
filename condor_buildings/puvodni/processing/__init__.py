"""
Processing modules for Condor Buildings Generator.

Contains spatial indexing, footprint processing, floor Z solving, and patch filtering.
"""

from .spatial_index import SpatialIndex, GridSpatialIndex
from .footprint import (
    process_footprint,
    compute_longest_axis,
    compute_obb,
    check_gabled_eligibility,
    GabledEligibility,
    FootprintAnalysis,
)
from .floor_z_solver import (
    FloorZSolver,
    compute_floor_z,
    FloorZResult,
)
from .patch_filter import (
    filter_buildings_by_patch_bounds,
    is_building_in_bounds,
    FilterResult,
    FilterReason,
)

__all__ = [
    'SpatialIndex',
    'GridSpatialIndex',
    'process_footprint',
    'compute_longest_axis',
    'compute_obb',
    'check_gabled_eligibility',
    'GabledEligibility',
    'FootprintAnalysis',
    'FloorZSolver',
    'compute_floor_z',
    'FloorZResult',
    'filter_buildings_by_patch_bounds',
    'is_building_in_bounds',
    'FilterResult',
    'FilterReason',
]
