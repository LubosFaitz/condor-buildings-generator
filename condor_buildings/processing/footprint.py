"""
Footprint processing for Condor Buildings Generator.

Handles footprint analysis including:
- Longest axis computation for ridge direction
- Gabled roof eligibility checking
- Convexity analysis
- Rectangle detection for safe gabled roofs
"""

from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import math

from ..models.geometry import Point2D, Polygon, BBox
from ..utils.polygon_utils import polygon_signed_area
from ..config import (
    GABLED_MAX_VERTICES,
    GABLED_REQUIRE_CONVEX,
    GABLED_REQUIRE_NO_HOLES,
    GABLED_MIN_CONVEXITY,
    GABLED_MIN_RECTANGULARITY,
    GABLED_MIN_ASPECT_RATIO,
    GABLED_MAX_ASPECT_RATIO,
    GABLED_ANGLE_TOLERANCE_DEG,
    # House-scale constraints
    HOUSE_MAX_FOOTPRINT_AREA,
    HOUSE_MAX_SIDE_LENGTH,
    HOUSE_MIN_SIDE_LENGTH,
    HOUSE_MAX_ASPECT_RATIO,
)


class GabledEligibility(Enum):
    """
    Result of gabled roof eligibility check.

    Each value represents a specific reason why a footprint
    may or may not be eligible for gabled roof generation.
    """
    ELIGIBLE = "eligible"
    TOO_MANY_VERTICES = "too_many_vertices"
    NOT_CONVEX = "not_convex"
    NOT_CONVEX_ENOUGH = "not_convex_enough"
    NOT_RECTANGULAR_ENOUGH = "not_rectangular_enough"
    BAD_ASPECT_RATIO = "bad_aspect_ratio"
    HAS_HOLES = "has_holes"
    NOT_RECTANGLE_ANGLES = "not_rectangle_angles"
    DEGENERATE = "degenerate"
    # House-scale size checks
    TOO_LARGE_AREA = "too_large_area"  # Footprint area > max for house
    TOO_LONG_SIDE = "too_long_side"  # Side length > max for house
    TOO_SHORT_SIDE = "too_short_side"  # Side length < min (shed/garage)
    TOO_ELONGATED = "too_elongated"  # Aspect ratio > max for house


@dataclass
class FootprintAnalysis:
    """Analysis results for a building footprint."""
    longest_axis_angle: float  # Degrees, 0 = East, CCW
    convexity_ratio: float  # 0-1, 1 = fully convex
    rectangularity: float  # 0-1, area / OBB_area
    aspect_ratio: float  # OBB length / width (>= 1)
    gabled_eligible: GabledEligibility
    bbox: BBox
    obb_length: float  # Oriented bounding box length
    obb_width: float  # Oriented bounding box width
    area: float
    perimeter: float
    vertex_count: int  # Unique vertex count (excluding closing point)
    is_convex: bool = False  # True if polygon is strictly convex
    is_rectangle_like: bool = False  # True if 4 vertices with ~90° angles
    # House-scale check result (separate from gabled_eligible)
    is_house_scale: bool = False  # True if dimensions are house-like
    house_scale_reason: Optional[GabledEligibility] = None  # Reason if not house-scale


def process_footprint(
    polygon: Polygon,
    max_vertices: int = GABLED_MAX_VERTICES,
    require_convex: bool = GABLED_REQUIRE_CONVEX,
    require_no_holes: bool = GABLED_REQUIRE_NO_HOLES,
    min_rectangularity: float = GABLED_MIN_RECTANGULARITY,
    # House-scale constraints
    house_max_area: float = HOUSE_MAX_FOOTPRINT_AREA,
    house_max_side: float = HOUSE_MAX_SIDE_LENGTH,
    house_min_side: float = HOUSE_MIN_SIDE_LENGTH,
    house_max_aspect: float = HOUSE_MAX_ASPECT_RATIO,
) -> FootprintAnalysis:
    """
    Analyze a building footprint for roof generation.

    Uses strict eligibility checking by default (4 vertices max).

    Args:
        polygon: Building footprint polygon
        max_vertices: Maximum vertices for gabled eligibility (default from config)
        require_convex: Require strictly convex polygon (default from config)
        require_no_holes: Require no holes in polygon (default from config)
        min_rectangularity: Minimum area/OBB_area ratio (default from config)
        house_max_area: Maximum footprint area for house classification (m²)
        house_max_side: Maximum side length for house classification (m)
        house_min_side: Minimum side length for house classification (m)
        house_max_aspect: Maximum aspect ratio for house classification

    Returns:
        FootprintAnalysis with computed properties
    """
    outer = polygon.outer_ring

    # Get unique vertex count (excluding closing point)
    vertex_count = get_unique_vertex_count(outer)

    if vertex_count < 3:
        return FootprintAnalysis(
            longest_axis_angle=0.0,
            convexity_ratio=0.0,
            rectangularity=0.0,
            aspect_ratio=1.0,
            gabled_eligible=GabledEligibility.DEGENERATE,
            bbox=BBox(0, 0, 0, 0),
            obb_length=0.0,
            obb_width=0.0,
            area=0.0,
            perimeter=0.0,
            vertex_count=vertex_count,
            is_convex=False,
            is_rectangle_like=False,
            is_house_scale=False,
            house_scale_reason=GabledEligibility.DEGENERATE,
        )

    # Compute properties
    bbox = polygon.bbox
    area = polygon.area()
    perimeter = _compute_perimeter(outer)
    convexity = _compute_convexity_ratio(outer)

    # For OBB, use the longest EDGE axis (not the farthest points axis)
    # This ensures rectangles have OBB aligned with their edges
    longest_edge_axis = compute_longest_edge_axis(outer)

    # Compute OBB (oriented bounding box) along longest edge
    obb = compute_obb(outer, longest_edge_axis)

    # For ridge direction, we also use longest edge axis
    longest_axis = longest_edge_axis
    obb_length = obb['length']
    obb_width = obb['width']
    obb_area = obb_length * obb_width

    # Rectangularity: how well the footprint fills its OBB
    rectangularity = area / obb_area if obb_area > 1e-6 else 0.0

    # Aspect ratio: length / width (always >= 1)
    if obb_width > 1e-6:
        aspect_ratio = obb_length / obb_width
        if aspect_ratio < 1.0:
            aspect_ratio = 1.0 / aspect_ratio
    else:
        aspect_ratio = float('inf')

    # Check eligibility with STRICT criteria
    eligibility, is_convex, is_rect_like = check_gabled_eligibility_strict(
        polygon,
        max_vertices=max_vertices,
        require_convex=require_convex,
        require_no_holes=require_no_holes,
        min_rectangularity=min_rectangularity,
        rectangularity=rectangularity,
        aspect_ratio=aspect_ratio,
    )

    # Check house-scale size constraints
    is_house, house_reason = check_house_scale(
        area=area,
        obb_length=obb_length,
        obb_width=obb_width,
        aspect_ratio=aspect_ratio,
        max_area=house_max_area,
        max_side=house_max_side,
        min_side=house_min_side,
        max_aspect=house_max_aspect,
    )

    return FootprintAnalysis(
        longest_axis_angle=longest_axis,
        convexity_ratio=convexity,
        rectangularity=rectangularity,
        aspect_ratio=aspect_ratio,
        gabled_eligible=eligibility,
        bbox=bbox,
        obb_length=obb_length,
        obb_width=obb_width,
        area=area,
        perimeter=perimeter,
        vertex_count=vertex_count,
        is_convex=is_convex,
        is_rectangle_like=is_rect_like,
        is_house_scale=is_house,
        house_scale_reason=house_reason,
    )


def compute_longest_axis(ring: List[Point2D]) -> float:
    """
    Compute the angle of the longest axis of a polygon.

    Uses the minimum bounding rectangle (rotating calipers) approach
    to find the principal axis orientation.

    Args:
        ring: Polygon vertices (closed ring)

    Returns:
        Angle in degrees (0 = East, CCW positive)
    """
    if len(ring) < 3:
        return 0.0

    # Simple approach: find the two farthest points and use their direction
    # This works well for rectangular buildings

    max_dist = 0.0
    best_angle = 0.0

    n = len(ring)
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1  # Exclude closing point

    for i in range(n):
        for j in range(i + 1, n):
            dx = ring[j].x - ring[i].x
            dy = ring[j].y - ring[i].y
            dist = dx * dx + dy * dy

            if dist > max_dist:
                max_dist = dist
                best_angle = math.atan2(dy, dx)

    # Convert to degrees
    angle_deg = math.degrees(best_angle)

    # Normalize to 0-180 (ridge direction doesn't have orientation)
    angle_deg = angle_deg % 180

    return angle_deg


def compute_longest_edge_axis(ring: List[Point2D]) -> float:
    """
    Compute the angle of the longest edge of a polygon.

    Alternative to compute_longest_axis that uses edge direction
    instead of point-to-point diagonal.

    Args:
        ring: Polygon vertices (closed ring)

    Returns:
        Angle in degrees (0 = East, CCW positive)
    """
    if len(ring) < 3:
        return 0.0

    max_len = 0.0
    best_angle = 0.0

    n = len(ring)
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1  # Exclude closing point

    for i in range(n):
        j = (i + 1) % n
        dx = ring[j].x - ring[i].x
        dy = ring[j].y - ring[i].y
        length = math.sqrt(dx * dx + dy * dy)

        if length > max_len:
            max_len = length
            best_angle = math.atan2(dy, dx)

    # Convert to degrees and normalize
    angle_deg = math.degrees(best_angle) % 180

    return angle_deg


def check_gabled_eligibility(
    polygon: Polygon,
    convexity_ratio: Optional[float] = None,
    rectangularity: Optional[float] = None,
    aspect_ratio: Optional[float] = None
) -> GabledEligibility:
    """
    Check if a footprint is eligible for gabled roof generation.

    Criteria (all must pass):
    - No holes
    - Maximum vertex count (default 8)
    - Minimum convexity ratio (default 0.9)
    - Minimum rectangularity (default 0.85)
    - Aspect ratio within range (default 0.3-4.0)

    Args:
        polygon: Building footprint
        convexity_ratio: Pre-computed convexity (computed if None)
        rectangularity: Pre-computed rectangularity
        aspect_ratio: Pre-computed aspect ratio

    Returns:
        GabledEligibility result
    """
    # Check for holes FIRST - buildings with holes cannot have gabled roofs
    if polygon.has_holes:
        return GabledEligibility.HAS_HOLES

    outer = polygon.outer_ring

    # Count actual vertices (excluding closing point)
    n = len(outer)
    if n > 0 and outer[0].x == outer[-1].x and outer[0].y == outer[-1].y:
        n -= 1

    if n < 3:
        return GabledEligibility.DEGENERATE

    # Check vertex count - simple footprints only
    if n > GABLED_MAX_VERTICES:
        return GabledEligibility.TOO_MANY_VERTICES

    # Check convexity - must be nearly convex
    if convexity_ratio is None:
        convexity_ratio = _compute_convexity_ratio(outer)

    if convexity_ratio < GABLED_MIN_CONVEXITY:
        return GabledEligibility.NOT_CONVEX_ENOUGH

    # Check rectangularity - must fill OBB well
    if rectangularity is not None and rectangularity < GABLED_MIN_RECTANGULARITY:
        return GabledEligibility.NOT_RECTANGULAR_ENOUGH

    # Check aspect ratio - not too narrow or too wide
    if aspect_ratio is not None:
        if aspect_ratio < GABLED_MIN_ASPECT_RATIO or aspect_ratio > GABLED_MAX_ASPECT_RATIO:
            return GabledEligibility.BAD_ASPECT_RATIO

    return GabledEligibility.ELIGIBLE


def compute_obb(ring: List[Point2D], direction_deg: float) -> dict:
    """
    Compute oriented bounding box along a given direction.

    Args:
        ring: Polygon vertices
        direction_deg: Direction in degrees for the "length" axis

    Returns:
        Dictionary with OBB properties:
            - length: Size along the direction
            - width: Size perpendicular to direction
            - center_x, center_y: Center of OBB
    """
    if not ring:
        return {
            'length': 0.0, 'width': 0.0,
            'center_x': 0.0, 'center_y': 0.0,
        }

    rad = math.radians(direction_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    # Project all points onto rotated axes
    along = []
    across = []

    n = len(ring)
    if ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1

    for i in range(n):
        p = ring[i]
        # Rotate point to align direction with X axis
        a = p.x * cos_r + p.y * sin_r  # Along direction
        c = -p.x * sin_r + p.y * cos_r  # Perpendicular
        along.append(a)
        across.append(c)

    if not along:
        return {
            'length': 0.0, 'width': 0.0,
            'center_x': 0.0, 'center_y': 0.0,
        }

    min_along = min(along)
    max_along = max(along)
    min_across = min(across)
    max_across = max(across)

    length = max_along - min_along
    width = max_across - min_across

    # Center in rotated space
    center_along = (min_along + max_along) / 2
    center_across = (min_across + max_across) / 2

    # Convert center back to world space
    center_x = center_along * cos_r - center_across * sin_r
    center_y = center_along * sin_r + center_across * cos_r

    return {
        'length': length,
        'width': width,
        'center_x': center_x,
        'center_y': center_y,
        'min_along': min_along,
        'max_along': max_along,
        'min_across': min_across,
        'max_across': max_across,
    }


def _compute_convexity_ratio(ring: List[Point2D]) -> float:
    """
    Compute convexity ratio: polygon area / convex hull area.

    A ratio of 1.0 means the polygon is fully convex.

    Args:
        ring: Polygon vertices

    Returns:
        Convexity ratio (0-1)
    """
    if len(ring) < 3:
        return 0.0

    polygon_area = abs(polygon_signed_area(ring))

    if polygon_area < 1e-10:
        return 0.0

    # Compute convex hull
    hull = _convex_hull(ring)
    hull_area = abs(polygon_signed_area(hull))

    if hull_area < 1e-10:
        return 0.0

    return polygon_area / hull_area


def _convex_hull(points: List[Point2D]) -> List[Point2D]:
    """
    Compute convex hull using Graham scan algorithm.

    Args:
        points: List of points

    Returns:
        Convex hull vertices in CCW order
    """
    # Remove duplicates and sort
    unique = list(set((p.x, p.y) for p in points))

    if len(unique) < 3:
        return [Point2D(x, y) for x, y in unique]

    # Sort by x, then by y
    unique.sort()

    # Build lower hull
    lower = []
    for p in unique:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(unique):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # Concatenate (excluding last point of each half - they're duplicated)
    hull = lower[:-1] + upper[:-1]

    return [Point2D(x, y) for x, y in hull]


def _cross(
    o: Tuple[float, float],
    a: Tuple[float, float],
    b: Tuple[float, float]
) -> float:
    """Cross product of vectors OA and OB."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _compute_perimeter(ring: List[Point2D]) -> float:
    """Compute perimeter of a polygon ring."""
    if len(ring) < 2:
        return 0.0

    perimeter = 0.0
    for i in range(len(ring) - 1):
        dx = ring[i + 1].x - ring[i].x
        dy = ring[i + 1].y - ring[i].y
        perimeter += math.sqrt(dx * dx + dy * dy)

    return perimeter


# =============================================================================
# NEW HELPERS FOR STRICT GABLED ELIGIBILITY
# =============================================================================

def get_unique_vertex_count(ring: List[Point2D]) -> int:
    """
    Get the number of unique vertices in a ring, excluding the closing point.

    The closing point (first == last) is not counted as a unique vertex.

    Args:
        ring: Polygon vertices (may or may not be closed)

    Returns:
        Number of unique vertices
    """
    if not ring:
        return 0

    n = len(ring)

    # Check if ring is closed (first == last)
    if n > 1 and ring[0].x == ring[-1].x and ring[0].y == ring[-1].y:
        n -= 1

    return n


def is_strictly_convex(ring: List[Point2D]) -> bool:
    """
    Check if a polygon is strictly convex.

    A polygon is strictly convex if all cross products of consecutive
    edges have the same sign (no collinear points, no concave vertices).

    Args:
        ring: Polygon vertices

    Returns:
        True if strictly convex, False otherwise
    """
    n = get_unique_vertex_count(ring)

    if n < 3:
        return False

    # Get the sign of the first non-zero cross product
    sign = 0

    for i in range(n):
        p0 = ring[i]
        p1 = ring[(i + 1) % n]
        p2 = ring[(i + 2) % n]

        # Cross product of edges (p0->p1) and (p1->p2)
        cross = (p1.x - p0.x) * (p2.y - p1.y) - (p1.y - p0.y) * (p2.x - p1.x)

        if abs(cross) < 1e-10:
            # Collinear points - not strictly convex
            continue

        current_sign = 1 if cross > 0 else -1

        if sign == 0:
            sign = current_sign
        elif sign != current_sign:
            # Sign change means concave vertex
            return False

    return sign != 0  # Must have at least one non-collinear turn


def is_rectangle_like(
    ring: List[Point2D],
    angle_tolerance_deg: float = GABLED_ANGLE_TOLERANCE_DEG
) -> bool:
    """
    Check if a 4-vertex polygon has angles close to 90 degrees.

    This is a stricter check than just having 4 vertices - it ensures
    the shape is actually a rectangle (or close to it).

    Args:
        ring: Polygon vertices (should have 4 unique vertices)
        angle_tolerance_deg: Maximum deviation from 90° allowed

    Returns:
        True if all angles are within tolerance of 90°
    """
    n = get_unique_vertex_count(ring)

    if n != 4:
        return False

    tolerance_rad = math.radians(angle_tolerance_deg)

    for i in range(4):
        p0 = ring[i]
        p1 = ring[(i + 1) % 4]
        p2 = ring[(i + 2) % 4]

        # Vectors from p1 to p0 and p1 to p2
        v1x = p0.x - p1.x
        v1y = p0.y - p1.y
        v2x = p2.x - p1.x
        v2y = p2.y - p1.y

        # Lengths
        len1 = math.sqrt(v1x * v1x + v1y * v1y)
        len2 = math.sqrt(v2x * v2x + v2y * v2y)

        if len1 < 1e-6 or len2 < 1e-6:
            return False

        # Dot product gives cos(angle)
        dot = (v1x * v2x + v1y * v2y) / (len1 * len2)
        dot = max(-1.0, min(1.0, dot))  # Clamp for numerical stability

        angle = math.acos(dot)

        # Check if angle is close to 90 degrees (pi/2)
        deviation = abs(angle - math.pi / 2)

        if deviation > tolerance_rad:
            return False

    return True


def check_gabled_eligibility_strict(
    polygon: Polygon,
    max_vertices: int = GABLED_MAX_VERTICES,
    require_convex: bool = GABLED_REQUIRE_CONVEX,
    require_no_holes: bool = GABLED_REQUIRE_NO_HOLES,
    min_rectangularity: float = GABLED_MIN_RECTANGULARITY,
    min_aspect_ratio: float = GABLED_MIN_ASPECT_RATIO,
    max_aspect_ratio: float = GABLED_MAX_ASPECT_RATIO,
    rectangularity: Optional[float] = None,
    aspect_ratio: Optional[float] = None,
) -> Tuple[GabledEligibility, bool, bool]:
    """
    Strict eligibility check for gabled roofs.

    This version is more restrictive, designed for safe gabled generation
    on simple rectangular footprints.

    Args:
        polygon: Building footprint
        max_vertices: Maximum allowed unique vertices (default 4)
        require_convex: If True, polygon must be strictly convex
        require_no_holes: If True, polygon must not have holes
        min_rectangularity: Minimum area/OBB_area ratio
        min_aspect_ratio: Minimum OBB aspect ratio
        max_aspect_ratio: Maximum OBB aspect ratio
        rectangularity: Pre-computed rectangularity (optional)
        aspect_ratio: Pre-computed aspect ratio (optional)

    Returns:
        Tuple of (GabledEligibility, is_convex, is_rectangle_like)
    """
    outer = polygon.outer_ring
    n = get_unique_vertex_count(outer)

    # Check for degenerate polygon
    if n < 3:
        return (GabledEligibility.DEGENERATE, False, False)

    # Check for holes FIRST - buildings with holes cannot have gabled roofs
    if require_no_holes and polygon.has_holes:
        return (GabledEligibility.HAS_HOLES, False, False)

    # Check vertex count - simple footprints only
    if n > max_vertices:
        return (GabledEligibility.TOO_MANY_VERTICES, False, False)

    # Check strict convexity
    convex = is_strictly_convex(outer)

    if require_convex and not convex:
        return (GabledEligibility.NOT_CONVEX, convex, False)

    # Check if rectangle-like (only for 4-vertex polygons)
    rect_like = False
    if n == 4:
        rect_like = is_rectangle_like(outer)

        # If we require rectangles and this is 4 vertices but not rectangle-like
        if max_vertices == 4 and not rect_like:
            return (GabledEligibility.NOT_RECTANGLE_ANGLES, convex, rect_like)

    # Check rectangularity
    if rectangularity is not None and rectangularity < min_rectangularity:
        return (GabledEligibility.NOT_RECTANGULAR_ENOUGH, convex, rect_like)

    # Check aspect ratio
    if aspect_ratio is not None:
        if aspect_ratio < min_aspect_ratio or aspect_ratio > max_aspect_ratio:
            return (GabledEligibility.BAD_ASPECT_RATIO, convex, rect_like)

    return (GabledEligibility.ELIGIBLE, convex, rect_like)


# =============================================================================
# HOUSE-SCALE SIZE VALIDATION
# =============================================================================

def check_house_scale(
    area: float,
    obb_length: float,
    obb_width: float,
    aspect_ratio: float,
    max_area: float = HOUSE_MAX_FOOTPRINT_AREA,
    max_side: float = HOUSE_MAX_SIDE_LENGTH,
    min_side: float = HOUSE_MIN_SIDE_LENGTH,
    max_aspect: float = HOUSE_MAX_ASPECT_RATIO,
) -> Tuple[bool, Optional[GabledEligibility]]:
    """
    Check if a footprint has house-scale dimensions.

    Large buildings (apartments, industrial, commercial) should not
    receive gabled roofs even if they are rectangular.

    Args:
        area: Footprint area in square meters
        obb_length: OBB length (longer dimension) in meters
        obb_width: OBB width (shorter dimension) in meters
        aspect_ratio: OBB length / width ratio
        max_area: Maximum allowed footprint area for houses
        max_side: Maximum allowed side length for houses
        min_side: Minimum required side length (below = shed/garage)
        max_aspect: Maximum allowed aspect ratio for houses

    Returns:
        Tuple of (is_house_scale, rejection_reason)
        - is_house_scale: True if building passes all size checks
        - rejection_reason: GabledEligibility enum if rejected, None if passed
    """
    # Check minimum side length (too small = shed/garage)
    min_dimension = min(obb_length, obb_width)
    if min_dimension < min_side:
        return (False, GabledEligibility.TOO_SHORT_SIDE)

    # Check maximum side length (too long = not a house)
    max_dimension = max(obb_length, obb_width)
    if max_dimension > max_side:
        return (False, GabledEligibility.TOO_LONG_SIDE)

    # Check footprint area (too large = apartment/industrial)
    if area > max_area:
        return (False, GabledEligibility.TOO_LARGE_AREA)

    # Check aspect ratio (too elongated = row house/industrial)
    if aspect_ratio > max_aspect:
        return (False, GabledEligibility.TOO_ELONGATED)

    return (True, None)
