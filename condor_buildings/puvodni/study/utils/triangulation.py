"""
Triangulation utilities for Condor Buildings Generator.

Provides ear clipping triangulation algorithm with support for
polygons with holes. Used for flat roof generation.
"""

from typing import List, Tuple, Optional
import math

from ..models.geometry import Point2D


class TriangulationError(Exception):
    """Raised when triangulation fails."""
    pass


def triangulate_polygon(ring: List[Point2D]) -> List[Tuple[int, int, int]]:
    """
    Triangulate a simple polygon using ear clipping algorithm.

    Args:
        ring: List of polygon vertices in CCW order

    Returns:
        List of triangle tuples (i, j, k) as indices into the input ring

    Raises:
        TriangulationError: If triangulation fails
    """
    n = len(ring)
    if n < 3:
        raise TriangulationError("Polygon must have at least 3 vertices")

    if n == 3:
        return [(0, 1, 2)]

    # Make working copy of indices
    indices = list(range(n))
    triangles = []

    while len(indices) > 3:
        ear_found = False

        for i in range(len(indices)):
            prev_i = (i - 1) % len(indices)
            next_i = (i + 1) % len(indices)

            prev_idx = indices[prev_i]
            curr_idx = indices[i]
            next_idx = indices[next_i]

            # Check if this vertex forms an ear
            if _is_ear(ring, indices, prev_i, i, next_i):
                triangles.append((prev_idx, curr_idx, next_idx))
                indices.pop(i)
                ear_found = True
                break

        if not ear_found:
            # Fallback: try to find any valid ear (less strict)
            for i in range(len(indices)):
                prev_i = (i - 1) % len(indices)
                next_i = (i + 1) % len(indices)

                prev_idx = indices[prev_i]
                curr_idx = indices[i]
                next_idx = indices[next_i]

                if _is_convex_vertex(ring[prev_idx], ring[curr_idx], ring[next_idx]):
                    triangles.append((prev_idx, curr_idx, next_idx))
                    indices.pop(i)
                    ear_found = True
                    break

            if not ear_found:
                raise TriangulationError(
                    f"Failed to find ear in polygon with {len(indices)} remaining vertices"
                )

    # Add final triangle
    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))

    return triangles


def triangulate_with_holes(
    outer: List[Point2D],
    holes: List[List[Point2D]]
) -> Tuple[List[Point2D], List[Tuple[int, int, int]]]:
    """
    Triangulate a polygon with holes using bridge-and-earclip method.

    Creates bridges connecting holes to outer ring, then triangulates
    the resulting simple polygon.

    Args:
        outer: Outer ring vertices (CCW order)
        holes: List of hole rings (CW order each)

    Returns:
        (merged_vertices, triangles) where triangles are indices
        into merged_vertices

    Raises:
        TriangulationError: If triangulation fails

    FALLBACK: If this function raises an error, the caller should
    fall back to triangulating outer ring only (ignoring holes).
    """
    if not holes:
        triangles = triangulate_polygon(outer)
        return (outer, triangles)

    # Sort holes by rightmost x-coordinate (descending)
    sorted_holes = sorted(
        enumerate(holes),
        key=lambda ih: max(p.x for p in ih[1]),
        reverse=True
    )

    # Start with outer ring
    merged = list(outer)

    # Process each hole
    for hole_idx, hole in sorted_holes:
        try:
            merged = _bridge_hole(merged, hole)
        except TriangulationError as e:
            raise TriangulationError(
                f"Failed to bridge hole {hole_idx}: {e}"
            )

    # Triangulate merged polygon
    triangles = triangulate_polygon(merged)
    return (merged, triangles)


def _bridge_hole(outer: List[Point2D], hole: List[Point2D]) -> List[Point2D]:
    """
    Create a bridge connecting a hole to the outer ring.

    Finds the rightmost point of the hole and connects it to a
    visible vertex on the outer ring.

    Args:
        outer: Outer ring vertices
        hole: Hole ring vertices

    Returns:
        Merged polygon with bridge
    """
    if len(hole) < 3:
        raise TriangulationError("Hole must have at least 3 vertices")

    # Find rightmost vertex of hole
    hole_right_idx = max(range(len(hole)), key=lambda i: hole[i].x)
    hole_right = hole[hole_right_idx]

    # Find visible vertex on outer ring
    outer_visible_idx = _find_visible_vertex(outer, hole_right)

    if outer_visible_idx is None:
        raise TriangulationError(
            f"No visible vertex found on outer ring for hole point at ({hole_right.x}, {hole_right.y})"
        )

    # Build merged polygon:
    # outer[0..visible] + hole[right..] + hole[0..right] + outer[visible..]
    merged = []

    # Add outer vertices up to and including visible vertex
    for i in range(outer_visible_idx + 1):
        merged.append(outer[i])

    # Add hole vertices starting from rightmost, going around
    for i in range(len(hole)):
        idx = (hole_right_idx + i) % len(hole)
        merged.append(hole[idx])

    # Add bridge back (duplicate of hole rightmost and outer visible)
    merged.append(hole[hole_right_idx])
    merged.append(outer[outer_visible_idx])

    # Add remaining outer vertices
    for i in range(outer_visible_idx + 1, len(outer)):
        merged.append(outer[i])

    return merged


def _find_visible_vertex(outer: List[Point2D], point: Point2D) -> Optional[int]:
    """
    Find a vertex on outer ring that is visible from point.

    Uses ray casting to the right (+X direction) and finds the
    closest intersection with outer ring edges, then selects
    the vertex.

    Args:
        outer: Outer ring vertices
        point: Point inside the polygon

    Returns:
        Index of visible vertex, or None if not found
    """
    n = len(outer)
    best_idx = None
    best_dist = float('inf')

    # Cast ray to the right from point
    ray_end_x = point.x + 100000  # Far enough

    for i in range(n):
        v1 = outer[i]
        v2 = outer[(i + 1) % n]

        # Check if ray intersects this edge
        intersection = _ray_edge_intersection(
            point, ray_end_x, v1, v2
        )

        if intersection is not None:
            ix, _ = intersection
            dist = ix - point.x

            if 0 < dist < best_dist:
                best_dist = dist

                # Choose the vertex closer to intersection point
                d1 = abs(v1.x - ix) + abs(v1.y - point.y)
                d2 = abs(v2.x - ix) + abs(v2.y - point.y)

                if d1 <= d2:
                    best_idx = i
                else:
                    best_idx = (i + 1) % n

    # If no intersection found, find closest vertex
    if best_idx is None:
        for i in range(n):
            dist = outer[i].distance_to(point)
            if dist < best_dist:
                best_dist = dist
                best_idx = i

    return best_idx


def _ray_edge_intersection(
    ray_origin: Point2D,
    ray_end_x: float,
    v1: Point2D,
    v2: Point2D
) -> Optional[Tuple[float, float]]:
    """
    Find intersection of horizontal ray with edge.

    Ray goes from ray_origin to (ray_end_x, ray_origin.y).

    Returns:
        (x, y) intersection point, or None if no intersection
    """
    y = ray_origin.y

    # Check if edge crosses ray's y-level
    if (v1.y <= y < v2.y) or (v2.y <= y < v1.y):
        # Compute x-coordinate of intersection
        t = (y - v1.y) / (v2.y - v1.y)
        x = v1.x + t * (v2.x - v1.x)

        # Check if intersection is to the right of ray origin
        if x > ray_origin.x:
            return (x, y)

    return None


def _is_ear(
    ring: List[Point2D],
    indices: List[int],
    prev_i: int,
    curr_i: int,
    next_i: int
) -> bool:
    """
    Check if vertex at curr_i forms an ear.

    An ear is a triangle that:
    1. Has a convex vertex at curr_i
    2. Contains no other polygon vertices
    """
    prev_idx = indices[prev_i]
    curr_idx = indices[curr_i]
    next_idx = indices[next_i]

    prev_p = ring[prev_idx]
    curr_p = ring[curr_idx]
    next_p = ring[next_idx]

    # Must be convex
    if not _is_convex_vertex(prev_p, curr_p, next_p):
        return False

    # Check that no other vertex is inside the triangle
    for i, idx in enumerate(indices):
        if i in (prev_i, curr_i, next_i):
            continue

        if _point_in_triangle(ring[idx], prev_p, curr_p, next_p):
            return False

    return True


def _is_convex_vertex(prev_p: Point2D, curr_p: Point2D, next_p: Point2D) -> bool:
    """
    Check if vertex curr_p is convex (left turn from prev to next).

    For CCW polygon, convex = positive cross product.
    """
    # Vectors
    v1_x = curr_p.x - prev_p.x
    v1_y = curr_p.y - prev_p.y
    v2_x = next_p.x - curr_p.x
    v2_y = next_p.y - curr_p.y

    # Cross product
    cross = v1_x * v2_y - v1_y * v2_x

    return cross > 0


def _point_in_triangle(
    p: Point2D,
    v0: Point2D,
    v1: Point2D,
    v2: Point2D
) -> bool:
    """
    Check if point is strictly inside triangle (not on edge).
    """
    def sign(p1: Point2D, p2: Point2D, p3: Point2D) -> float:
        return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y)

    d1 = sign(p, v0, v1)
    d2 = sign(p, v1, v2)
    d3 = sign(p, v2, v0)

    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)

    return not (has_neg and has_pos)


def validate_triangulation(
    vertices: List[Point2D],
    triangles: List[Tuple[int, int, int]],
    expected_area: Optional[float] = None
) -> List[str]:
    """
    Validate triangulation result.

    Args:
        vertices: List of vertices
        triangles: List of triangle index tuples
        expected_area: Expected polygon area (optional)

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not triangles:
        errors.append("No triangles generated")
        return errors

    n = len(vertices)

    # Check index validity
    for i, (a, b, c) in enumerate(triangles):
        if a < 0 or a >= n or b < 0 or b >= n or c < 0 or c >= n:
            errors.append(f"Triangle {i} has invalid index")

    # Check for degenerate triangles
    for i, (a, b, c) in enumerate(triangles):
        area = _triangle_area(vertices[a], vertices[b], vertices[c])
        if abs(area) < 1e-10:
            errors.append(f"Triangle {i} is degenerate (zero area)")

    # Check total area
    if expected_area is not None:
        total_area = sum(
            abs(_triangle_area(vertices[a], vertices[b], vertices[c]))
            for a, b, c in triangles
        )
        if abs(total_area - expected_area) > expected_area * 0.01:  # 1% tolerance
            errors.append(
                f"Total triangulated area {total_area:.2f} differs from "
                f"expected {expected_area:.2f}"
            )

    return errors


def _triangle_area(v0: Point2D, v1: Point2D, v2: Point2D) -> float:
    """Compute signed area of triangle."""
    return 0.5 * (
        (v1.x - v0.x) * (v2.y - v0.y) -
        (v2.x - v0.x) * (v1.y - v0.y)
    )
