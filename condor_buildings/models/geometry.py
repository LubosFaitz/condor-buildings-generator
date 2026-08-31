"""
Core geometry types for Condor Buildings Generator.

Provides Point2D, Point3D, BBox, and Polygon classes used throughout
the pipeline for representing building footprints and spatial data.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import math


@dataclass(frozen=True, slots=True)
class Point2D:
    """2D point in local Condor coordinates."""
    x: float
    y: float

    def distance_to(self, other: 'Point2D') -> float:
        """Euclidean distance to another point."""
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def __sub__(self, other: 'Point2D') -> 'Point2D':
        """Vector subtraction."""
        return Point2D(self.x - other.x, self.y - other.y)

    def __add__(self, other: 'Point2D') -> 'Point2D':
        """Vector addition."""
        return Point2D(self.x + other.x, self.y + other.y)

    def dot(self, other: 'Point2D') -> float:
        """Dot product."""
        return self.x * other.x + self.y * other.y

    def cross(self, other: 'Point2D') -> float:
        """2D cross product (returns scalar z-component)."""
        return self.x * other.y - self.y * other.x


@dataclass(frozen=True, slots=True)
class Point3D:
    """3D point in local Condor coordinates."""
    x: float
    y: float
    z: float

    def to_2d(self) -> Point2D:
        """Project to XY plane."""
        return Point2D(self.x, self.y)

    def distance_to(self, other: 'Point3D') -> float:
        """Euclidean distance to another point."""
        dx = self.x - other.x
        dy = self.y - other.y
        dz = self.z - other.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def __sub__(self, other: 'Point3D') -> 'Point3D':
        """Vector subtraction."""
        return Point3D(self.x - other.x, self.y - other.y, self.z - other.z)

    def __add__(self, other: 'Point3D') -> 'Point3D':
        """Vector addition."""
        return Point3D(self.x + other.x, self.y + other.y, self.z + other.z)


@dataclass(slots=True)
class BBox:
    """Axis-aligned bounding box in 2D."""
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def intersects(self, other: 'BBox') -> bool:
        """Check if this bbox intersects another."""
        return not (
            self.max_x < other.min_x or
            self.min_x > other.max_x or
            self.max_y < other.min_y or
            self.min_y > other.max_y
        )

    def contains_point(self, p: Point2D) -> bool:
        """Check if point is inside bbox (inclusive)."""
        return (
            self.min_x <= p.x <= self.max_x and
            self.min_y <= p.y <= self.max_y
        )

    def expand(self, margin: float) -> 'BBox':
        """Return a new bbox expanded by margin on all sides."""
        return BBox(
            self.min_x - margin,
            self.min_y - margin,
            self.max_x + margin,
            self.max_y + margin
        )

    @property
    def width(self) -> float:
        """Width in X direction."""
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        """Height in Y direction."""
        return self.max_y - self.min_y

    @property
    def center(self) -> Point2D:
        """Center point of bbox."""
        return Point2D(
            (self.min_x + self.max_x) / 2,
            (self.min_y + self.max_y) / 2
        )

    @property
    def area(self) -> float:
        """Area of bbox."""
        return self.width * self.height

    @staticmethod
    def from_points(points: List[Point2D]) -> 'BBox':
        """Create bbox from a list of points."""
        if not points:
            raise ValueError("Cannot create BBox from empty point list")

        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return BBox(min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def from_points_3d(points: List[Point3D]) -> 'BBox':
        """Create 2D bbox from a list of 3D points (ignores Z)."""
        if not points:
            raise ValueError("Cannot create BBox from empty point list")

        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return BBox(min(xs), min(ys), max(xs), max(ys))


@dataclass
class Polygon:
    """
    2D polygon with optional holes, representing a building footprint.

    Attributes:
        outer_ring: List of Point2D forming the outer boundary (should be CCW)
        holes: List of inner rings (each should be CW for proper winding)
        bbox: Cached bounding box (computed on demand if None)

    Winding convention:
        - Outer ring: counter-clockwise (positive signed area)
        - Holes: clockwise (negative signed area)
    """
    outer_ring: List[Point2D]
    holes: List[List[Point2D]] = field(default_factory=list)
    _bbox: Optional[BBox] = field(default=None, repr=False)

    @property
    def bbox(self) -> BBox:
        """Get or compute bounding box."""
        if self._bbox is None:
            self._bbox = BBox.from_points(self.outer_ring)
        return self._bbox

    def invalidate_bbox(self) -> None:
        """Clear cached bbox (call after modifying rings)."""
        self._bbox = None

    @property
    def vertex_count(self) -> int:
        """Total number of vertices including holes."""
        count = len(self.outer_ring)
        for hole in self.holes:
            count += len(hole)
        return count

    @property
    def has_holes(self) -> bool:
        """Check if polygon has any holes."""
        return len(self.holes) > 0

    def signed_area(self) -> float:
        """
        Compute signed area using shoelace formula.
        Positive = CCW, Negative = CW.
        """
        return _signed_area(self.outer_ring)

    def area(self) -> float:
        """
        Compute total area (outer minus holes).
        """
        total = abs(self.signed_area())
        for hole in self.holes:
            total -= abs(_signed_area(hole))
        return total

    def is_ccw(self) -> bool:
        """Check if outer ring is counter-clockwise."""
        return self.signed_area() > 0

    def ensure_ccw_outer(self) -> None:
        """Ensure outer ring is CCW (reverse if needed)."""
        if not self.is_ccw():
            self.outer_ring = list(reversed(self.outer_ring))
            self.invalidate_bbox()

    def ensure_cw_holes(self) -> None:
        """Ensure all holes are CW (reverse if needed)."""
        for i, hole in enumerate(self.holes):
            if _signed_area(hole) > 0:  # CCW, should be CW
                self.holes[i] = list(reversed(hole))


def _signed_area(ring: List[Point2D]) -> float:
    """
    Compute signed area of a ring using shoelace formula.
    Positive = CCW, Negative = CW.
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
