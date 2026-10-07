"""
Polygon utilities for Condor Buildings Generator.

Provides functions for point-in-polygon tests, area calculations,
convexity checks, and other polygon operations.
"""

from typing import List, Tuple, Optional
import math

from ..models.geometry import Point2D, Polygon, BBox


def point_in_polygon(point: Point2D, ring: List[Point2D]) -> bool:
    """
    Test if point is inside a polygon ring using ray casting algorithm.

    Args:
        point: Point to test
        ring: List of polygon vertices (closed or open)

    Returns:
        True if point is inside or on edge
    """
    n = len(ring)
    if n < 3:
        return False

    inside = False
    j = n - 1

    for i in range(n):
        xi, yi = ring[i].x, ring[i].y
        xj, yj = ring[j].x, ring[j].y

        # Check if point is on edge (approximately)
        if _point_on_segment(point, ring[i], ring[j]):
            return True

        # Ray casting
        if ((yi > point.y) != (yj > point.y)) and \
           (point.x < (xj - xi) * (point.y - yi) / (yj - yi) + xi):
            inside = not inside

        j = i

    return inside


def point_in_polygon_with_holes(point: Point2D, polygon: Polygon) -> bool:
    """
    Test if point is inside polygon (outer ring minus holes).

    CRITICAL: Returns True only if inside_outer AND NOT inside_any_hole.

    Args:
        point: Point to test
        polygon: Polygon with outer ring and optional holes

    Returns:
        True if point is inside outer ring but not in any hole
    """
    # Must be inside outer ring
    if not point_in_polygon(point, polygon.outer_ring):
        return False

    # Must not be inside any hole
    for hole in polygon.holes:
        if point_in_polygon(point, hole):
            return False

    return True


def polygon_signed_area(ring: List[Point2D]) -> float:
    """
    Compute signed area using shoelace formula.

    Args:
        ring: List of polygon vertices

    Returns:
        Signed area (positive = CCW, negative = CW)
    """
    n = len(ring)
    if n < 3:
        return 0.0

    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += ring[i].x * ring[j].y
        area -= ring[j].x * ring[i].y

    return area / 2.0


def polygon_area(ring: List[Point2D]) -> float:
    """
    Compute unsigned area of polygon.

    Args:
        ring: List of polygon vertices

    Returns:
        Absolute area
    """
    return abs(polygon_signed_area(ring))


def polygon_centroid(ring: List[Point2D]) -> Point2D:
    """
    Compute centroid of polygon.

    Args:
        ring: List of polygon vertices

    Returns:
        Centroid point
    """
    n = len(ring)
    if n == 0:
        return Point2D(0.0, 0.0)

    if n == 1:
        return ring[0]

    cx = sum(p.x for p in ring) / n
    cy = sum(p.y for p in ring) / n
    return Point2D(cx, cy)


def is_convex(ring: List[Point2D]) -> bool:
    """
    Check if polygon ring is convex.

    Args:
        ring: List of polygon vertices (CCW order assumed)

    Returns:
        True if polygon is convex
    """
    n = len(ring)
    if n < 3:
        return False

    sign = None

    for i in range(n):
        p1 = ring[i]
        p2 = ring[(i + 1) % n]
        p3 = ring[(i + 2) % n]

        # Cross product of edges
        cross = (p2.x - p1.x) * (p3.y - p2.y) - (p2.y - p1.y) * (p3.x - p2.x)

        if abs(cross) > 1e-10:  # Not collinear
            if sign is None:
                sign = cross > 0
            elif (cross > 0) != sign:
                return False

    return True


def convexity_ratio(ring: List[Point2D]) -> float:
    """
    Compute convexity ratio: polygon_area / convex_hull_area.

    A ratio close to 1.0 means the polygon is nearly convex.
    A ratio significantly less than 1.0 indicates concavity.

    Args:
        ring: List of polygon vertices

    Returns:
        Ratio in [0, 1], where 1.0 = convex
    """
    poly_area = polygon_area(ring)
    if poly_area < 1e-10:
        return 0.0

    hull = convex_hull(ring)
    hull_area = polygon_area(hull)

    if hull_area < 1e-10:
        return 0.0

    return poly_area / hull_area


def convex_hull(points: List[Point2D]) -> List[Point2D]:
    """
    Compute convex hull of points using monotone chain algorithm.

    Args:
        points: List of points

    Returns:
        List of hull vertices in CCW order
    """
    if len(points) < 3:
        return list(points)

    # Sort points by x, then by y
    sorted_pts = sorted(points, key=lambda p: (p.x, p.y))

    # Build lower hull
    lower = []
    for p in sorted_pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # Concatenate (remove last point of each half as it's repeated)
    return lower[:-1] + upper[:-1]


def _cross(o: Point2D, a: Point2D, b: Point2D) -> float:
    """Cross product of vectors OA and OB."""
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)


def _point_on_segment(p: Point2D, a: Point2D, b: Point2D,
                       tolerance: float = 1e-6) -> bool:
    """Check if point is on line segment (within tolerance)."""
    # Vector from a to b
    ab_x = b.x - a.x
    ab_y = b.y - a.y

    # Vector from a to p
    ap_x = p.x - a.x
    ap_y = p.y - a.y

    # Cross product (should be ~0 if collinear)
    cross = abs(ab_x * ap_y - ab_y * ap_x)

    # Length of segment
    ab_len = math.sqrt(ab_x * ab_x + ab_y * ab_y)
    if ab_len < 1e-10:
        return p.distance_to(a) < tolerance

    # Distance from line
    if cross / ab_len > tolerance:
        return False

    # Check if projection is within segment
    dot = ap_x * ab_x + ap_y * ab_y
    t = dot / (ab_len * ab_len)

    return -tolerance <= t <= 1 + tolerance


def oriented_bounding_box(ring: List[Point2D], direction_deg: float) -> dict:
    """
    Compute oriented bounding box along a given direction.

    Args:
        ring: List of polygon vertices
        direction_deg: Direction in degrees for the "along" axis

    Returns:
        Dictionary with OBB properties:
            - length: Size along the direction
            - width: Size perpendicular to direction
            - min_along, max_along: Extent along direction
            - min_across, max_across: Extent perpendicular
            - center_along, center_across: Center in local coords
    """
    if not ring:
        return {
            'length': 0, 'width': 0,
            'min_along': 0, 'max_along': 0,
            'min_across': 0, 'max_across': 0,
            'center_along': 0, 'center_across': 0,
        }

    rad = math.radians(direction_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    # Project all points onto rotated axes
    along = []
    across = []

    for p in ring:
        a = p.x * cos_r + p.y * sin_r
        c = -p.x * sin_r + p.y * cos_r
        along.append(a)
        across.append(c)

    min_along = min(along)
    max_along = max(along)
    min_across = min(across)
    max_across = max(across)

    return {
        'length': max_along - min_along,
        'width': max_across - min_across,
        'min_along': min_along,
        'max_along': max_along,
        'min_across': min_across,
        'max_across': max_across,
        'center_along': (min_along + max_along) / 2,
        'center_across': (min_across + max_across) / 2,
    }


def longest_edge_direction(ring: List[Point2D]) -> Tuple[float, float]:
    """
    Find direction of the longest edge in a polygon.

    Args:
        ring: List of polygon vertices

    Returns:
        (direction_degrees, edge_length) tuple
    """
    if len(ring) < 2:
        return (0.0, 0.0)

    max_length = 0.0
    direction = 0.0

    n = len(ring)
    for i in range(n):
        p1 = ring[i]
        p2 = ring[(i + 1) % n]

        dx = p2.x - p1.x
        dy = p2.y - p1.y
        length = math.sqrt(dx * dx + dy * dy)

        if length > max_length:
            max_length = length
            direction = math.degrees(math.atan2(dy, dx))

    return (direction, max_length)


def aspect_ratio(ring: List[Point2D]) -> float:
    """
    Compute aspect ratio (length / width) of polygon's OBB.

    Uses longest edge direction for orientation.

    Args:
        ring: List of polygon vertices

    Returns:
        Aspect ratio (>= 1.0)
    """
    if len(ring) < 3:
        return 1.0

    direction, _ = longest_edge_direction(ring)
    obb = oriented_bounding_box(ring, direction)

    length = obb['length']
    width = obb['width']

    if width < 1e-10:
        return float('inf')

    ratio = length / width
    return ratio if ratio >= 1.0 else 1.0 / ratio


def is_clockwise(ring: List[Point2D]) -> bool:
    """
    Check if polygon ring is clockwise.

    Args:
        ring: List of polygon vertices

    Returns:
        True if clockwise (negative area)
    """
    return polygon_signed_area(ring) < 0


def reverse_ring(ring: List[Point2D]) -> List[Point2D]:
    """
    Reverse the order of vertices in a ring.

    Args:
        ring: List of polygon vertices

    Returns:
        Reversed list (changes winding direction)
    """
    return list(reversed(ring))


def ensure_ccw(ring: List[Point2D]) -> List[Point2D]:
    """
    Ensure ring is counter-clockwise, reversing if needed.

    Args:
        ring: List of polygon vertices

    Returns:
        Ring in CCW order
    """
    if is_clockwise(ring):
        return reverse_ring(ring)
    return ring


def ensure_cw(ring: List[Point2D]) -> List[Point2D]:
    """
    Ensure ring is clockwise, reversing if needed.

    Args:
        ring: List of polygon vertices

    Returns:
        Ring in CW order
    """
    if not is_clockwise(ring):
        return reverse_ring(ring)
    return ring


def remove_collinear_points(
    ring: List[Point2D],
    epsilon: float = 0.01
) -> List[Point2D]:
    """
    Remove collinear points from a polygon ring.

    A point is considered collinear if the triangle formed with its
    neighbors has area less than epsilon.

    Args:
        ring: List of polygon vertices
        epsilon: Area threshold for collinearity (default 0.01 sq meters)

    Returns:
        Ring with collinear points removed
    """
    if len(ring) < 3:
        return ring

    # Handle closed ring
    is_closed = (ring[0].x == ring[-1].x and ring[0].y == ring[-1].y)
    if is_closed:
        working = ring[:-1]  # Work without closing point
    else:
        working = list(ring)

    if len(working) < 3:
        return ring

    result = []
    n = len(working)

    for i in range(n):
        prev_idx = (i - 1) % n
        next_idx = (i + 1) % n

        p_prev = working[prev_idx]
        p_curr = working[i]
        p_next = working[next_idx]

        # Compute area of triangle formed by prev, curr, next
        area = abs(
            (p_prev.x * (p_curr.y - p_next.y) +
             p_curr.x * (p_next.y - p_prev.y) +
             p_next.x * (p_prev.y - p_curr.y)) / 2.0
        )

        if area >= epsilon:
            result.append(p_curr)

    # Ensure we didn't remove too many points
    if len(result) < 3:
        return ring

    # Re-close the ring if it was closed
    if is_closed:
        result.append(result[0])

    return result
