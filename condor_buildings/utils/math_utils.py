"""
Mathematical utilities for Condor Buildings Generator.

Provides functions for line intersection, distance calculations,
and other geometric operations.
"""

from typing import Optional, Tuple
import math

from ..models.geometry import Point2D, Point3D


def line_segment_intersection_2d(
    p1: Point2D, p2: Point2D,
    p3: Point2D, p4: Point2D
) -> Optional[Point2D]:
    """
    Find intersection point of two line segments.

    Uses parametric line intersection algorithm.

    Args:
        p1, p2: First line segment endpoints
        p3, p4: Second line segment endpoints

    Returns:
        Intersection point, or None if segments don't intersect
    """
    x1, y1 = p1.x, p1.y
    x2, y2 = p2.x, p2.y
    x3, y3 = p3.x, p3.y
    x4, y4 = p4.x, p4.y

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)

    if abs(denom) < 1e-10:
        return None  # Lines are parallel

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom

    # Check if intersection is within both segments
    if 0 <= t <= 1 and 0 <= u <= 1:
        x = x1 + t * (x2 - x1)
        y = y1 + t * (y2 - y1)
        return Point2D(x, y)

    return None


def line_intersection_2d(
    p1: Point2D, p2: Point2D,
    p3: Point2D, p4: Point2D
) -> Optional[Point2D]:
    """
    Find intersection point of two infinite lines.

    Args:
        p1, p2: Two points on first line
        p3, p4: Two points on second line

    Returns:
        Intersection point, or None if lines are parallel
    """
    x1, y1 = p1.x, p1.y
    x2, y2 = p2.x, p2.y
    x3, y3 = p3.x, p3.y
    x4, y4 = p4.x, p4.y

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)

    if abs(denom) < 1e-10:
        return None  # Lines are parallel

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom

    x = x1 + t * (x2 - x1)
    y = y1 + t * (y2 - y1)
    return Point2D(x, y)


def point_to_line_distance(point: Point2D, line_p1: Point2D, line_p2: Point2D) -> float:
    """
    Compute perpendicular distance from point to infinite line.

    Args:
        point: The point
        line_p1, line_p2: Two points defining the line

    Returns:
        Distance from point to line
    """
    # Line direction vector
    dx = line_p2.x - line_p1.x
    dy = line_p2.y - line_p1.y

    # Normalize
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1e-10:
        return point.distance_to(line_p1)

    dx /= length
    dy /= length

    # Vector from line_p1 to point
    px = point.x - line_p1.x
    py = point.y - line_p1.y

    # Perpendicular distance = |cross product|
    return abs(px * dy - py * dx)


def point_to_segment_distance(point: Point2D, seg_p1: Point2D, seg_p2: Point2D) -> float:
    """
    Compute minimum distance from point to line segment.

    Args:
        point: The point
        seg_p1, seg_p2: Segment endpoints

    Returns:
        Distance from point to nearest point on segment
    """
    dx = seg_p2.x - seg_p1.x
    dy = seg_p2.y - seg_p1.y

    length_sq = dx * dx + dy * dy
    if length_sq < 1e-10:
        return point.distance_to(seg_p1)

    # Project point onto line, clamped to [0, 1]
    t = max(0, min(1, (
        (point.x - seg_p1.x) * dx +
        (point.y - seg_p1.y) * dy
    ) / length_sq))

    # Nearest point on segment
    nearest = Point2D(
        seg_p1.x + t * dx,
        seg_p1.y + t * dy
    )

    return point.distance_to(nearest)


def barycentric_coords(
    p: Point2D,
    v0: Point2D, v1: Point2D, v2: Point2D
) -> Optional[Tuple[float, float, float]]:
    """
    Compute barycentric coordinates of point p in triangle v0,v1,v2.

    Args:
        p: Point to compute coordinates for
        v0, v1, v2: Triangle vertices

    Returns:
        (u, v, w) barycentric coordinates, or None if degenerate triangle
    """
    # Using the more numerically stable method
    v0v1 = Point2D(v1.x - v0.x, v1.y - v0.y)
    v0v2 = Point2D(v2.x - v0.x, v2.y - v0.y)
    v0p = Point2D(p.x - v0.x, p.y - v0.y)

    d00 = v0v1.dot(v0v1)
    d01 = v0v1.dot(v0v2)
    d11 = v0v2.dot(v0v2)
    d20 = v0p.dot(v0v1)
    d21 = v0p.dot(v0v2)

    denom = d00 * d11 - d01 * d01
    if abs(denom) < 1e-10:
        return None  # Degenerate triangle

    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w

    return (u, v, w)


def normalize_angle(angle: float) -> float:
    """
    Normalize angle to [0, 360) degrees.

    Args:
        angle: Angle in degrees

    Returns:
        Normalized angle in [0, 360)
    """
    while angle < 0:
        angle += 360
    while angle >= 360:
        angle -= 360
    return angle


def angle_between_vectors(v1: Point2D, v2: Point2D) -> float:
    """
    Compute angle between two 2D vectors in degrees.

    Args:
        v1, v2: Direction vectors

    Returns:
        Angle in degrees [0, 180]
    """
    dot = v1.x * v2.x + v1.y * v2.y
    len1 = math.sqrt(v1.x * v1.x + v1.y * v1.y)
    len2 = math.sqrt(v2.x * v2.x + v2.y * v2.y)

    if len1 < 1e-10 or len2 < 1e-10:
        return 0.0

    cos_angle = clamp(dot / (len1 * len2), -1.0, 1.0)
    return math.degrees(math.acos(cos_angle))


def clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Clamp value to [min_val, max_val] range.

    Args:
        value: Value to clamp
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        Clamped value
    """
    return max(min_val, min(max_val, value))


def lerp(a: float, b: float, t: float) -> float:
    """
    Linear interpolation between a and b.

    Args:
        a: Start value
        b: End value
        t: Interpolation factor [0, 1]

    Returns:
        Interpolated value
    """
    return a + (b - a) * t


def cross_product_2d(v1: Point2D, v2: Point2D) -> float:
    """
    2D cross product (z-component of 3D cross product).

    Args:
        v1, v2: 2D vectors

    Returns:
        Scalar z-component of cross product
    """
    return v1.x * v2.y - v1.y * v2.x


def dot_product_2d(v1: Point2D, v2: Point2D) -> float:
    """
    2D dot product.

    Args:
        v1, v2: 2D vectors

    Returns:
        Dot product
    """
    return v1.x * v2.x + v1.y * v2.y


def vector_length(v: Point2D) -> float:
    """
    Length of 2D vector.

    Args:
        v: 2D vector

    Returns:
        Vector length
    """
    return math.sqrt(v.x * v.x + v.y * v.y)


def normalize_vector(v: Point2D) -> Point2D:
    """
    Normalize 2D vector to unit length.

    Args:
        v: 2D vector

    Returns:
        Unit vector, or (0, 0) if input is zero-length
    """
    length = vector_length(v)
    if length < 1e-10:
        return Point2D(0.0, 0.0)
    return Point2D(v.x / length, v.y / length)


def perpendicular_vector(v: Point2D) -> Point2D:
    """
    Get perpendicular vector (rotated 90 degrees CCW).

    Args:
        v: 2D vector

    Returns:
        Perpendicular vector
    """
    return Point2D(-v.y, v.x)
