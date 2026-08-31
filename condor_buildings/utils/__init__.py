"""
Utility functions for Condor Buildings Generator.
"""

from .math_utils import (
    line_segment_intersection_2d,
    point_to_line_distance,
    normalize_angle,
    clamp,
)
from .polygon_utils import (
    point_in_polygon,
    point_in_polygon_with_holes,
    polygon_signed_area,
    is_convex,
    convexity_ratio,
)

__all__ = [
    'line_segment_intersection_2d',
    'point_to_line_distance',
    'normalize_angle',
    'clamp',
    'point_in_polygon',
    'point_in_polygon_with_holes',
    'polygon_signed_area',
    'is_convex',
    'convexity_ratio',
]
