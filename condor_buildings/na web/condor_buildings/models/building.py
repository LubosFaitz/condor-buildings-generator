"""
Building data model for Condor Buildings Generator.

Provides BuildingRecord dataclass and related enums for categorizing
buildings and roof types.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .geometry import Polygon


class BuildingCategory(Enum):
    """Building category classification based on OSM tags."""
    HOUSE = "house"
    APARTMENT = "apartment"
    INDUSTRIAL = "industrial"
    COMMERCIAL = "commercial"
    OTHER = "other"

    @classmethod
    def from_osm_tag(cls, building_type: str) -> 'BuildingCategory':
        """
        Classify building category from OSM building=* tag value.

        Args:
            building_type: Value of the building tag (e.g., 'house', 'apartments')

        Returns:
            Appropriate BuildingCategory enum value
        """
        building_type = building_type.lower().strip()

        house_types = {
            'house', 'detached', 'semidetached_house', 'semi',
            'terrace', 'terraced_house', 'farm', 'farmhouse',
            'cabin', 'bungalow', 'villa', 'residential',
            'static_caravan', 'hut', 'shed'
        }

        apartment_types = {
            'apartments', 'apartment', 'flats', 'dormitory',
            'tower', 'block', 'building'
        }

        industrial_types = {
            'industrial', 'warehouse', 'factory', 'hangar',
            'manufacture', 'storage_tank', 'silo', 'barn',
            'greenhouse', 'farm_auxiliary', 'digester'
        }

        commercial_types = {
            'commercial', 'retail', 'supermarket', 'office',
            'hotel', 'shop', 'kiosk', 'store', 'mall',
            'restaurant', 'cafe', 'bank', 'hospital',
            'clinic', 'pharmacy', 'kindergarten', 'school',
            'university', 'college', 'public', 'civic',
            'government', 'transportation', 'train_station',
            'parking', 'service', 'fire_station', 'police'
        }

        if building_type in house_types:
            return cls.HOUSE
        elif building_type in apartment_types:
            return cls.APARTMENT
        elif building_type in industrial_types:
            return cls.INDUSTRIAL
        elif building_type in commercial_types:
            return cls.COMMERCIAL
        else:
            # Default: 'yes' and unknown types
            # Use footprint size heuristic later to refine
            return cls.OTHER


class RoofType(Enum):
    """Roof type classification."""
    GABLED = "gabled"
    HIPPED = "hipped"
    FLAT = "flat"

    @classmethod
    def from_osm_tag(cls, roof_shape: Optional[str]) -> 'RoofType':
        """
        Determine roof type from OSM roof:shape tag.

        Args:
            roof_shape: Value of roof:shape tag (may be None)

        Returns:
            Appropriate RoofType enum value
        """
        if not roof_shape:
            return cls.GABLED  # Default for European houses

        roof_shape = roof_shape.lower().strip()

        flat_types = {'flat', 'skillion', 'lean_to'}
        hipped_types = {'hipped', 'hip', 'half-hipped', 'half_hipped', 'pyramidal'}
        gabled_types = {'gabled', 'gable', 'saltbox', 'gambrel', 'mansard'}

        if roof_shape in flat_types:
            return cls.FLAT
        elif roof_shape in hipped_types:
            return cls.HIPPED
        elif roof_shape in gabled_types:
            return cls.GABLED
        else:
            return cls.GABLED  # Default


class RoofDirectionSource(Enum):
    """Source of roof ridge direction."""
    OSM_TAG = "osm_tag"
    LONGEST_AXIS = "longest_axis_heuristic"
    DEFAULT = "default"


class RoofFallbackReason(Enum):
    """
    Reason why a building's requested roof type was changed.

    Used when a building requested a gabled roof but received flat instead.
    """
    NONE = "none"  # No fallback occurred
    HAS_HOLES = "has_holes"
    TOO_MANY_VERTICES = "too_many_vertices"
    NOT_CONVEX = "not_convex"
    NOT_CONVEX_ENOUGH = "not_convex_enough"
    NOT_RECTANGULAR_ENOUGH = "not_rectangular_enough"
    BAD_ASPECT_RATIO = "bad_aspect_ratio"
    NOT_RECTANGLE_ANGLES = "not_rectangle_angles"
    HIPPED_NOT_SUPPORTED = "hipped_not_supported"
    DEGENERATE = "degenerate"
    # Size-based reasons (house-scale check)
    TOO_LARGE_AREA = "too_large_area"  # Footprint area > max
    TOO_LONG_SIDE = "too_long_side"  # Side length > max
    TOO_SHORT_SIDE = "too_short_side"  # Side length < min (shed/garage)
    TOO_ELONGATED = "too_elongated"  # Aspect ratio > max for house
    # Floor-based reasons
    TOO_MANY_FLOORS = "too_many_floors"  # Building has more floors than allowed for roof type
    # Polyskel-related reasons
    POLYSKEL_FAILED = "polyskel_failed"  # polygonize() raised an exception
    POLYSKEL_NOT_AVAILABLE = "polyskel_not_available"  # bpypolyskel not installed (standalone mode)

    @classmethod
    def from_eligibility(cls, eligibility_value: str) -> 'RoofFallbackReason':
        """Convert GabledEligibility value to RoofFallbackReason."""
        mapping = {
            'eligible': cls.NONE,
            'has_holes': cls.HAS_HOLES,
            'too_many_vertices': cls.TOO_MANY_VERTICES,
            'not_convex': cls.NOT_CONVEX,
            'not_convex_enough': cls.NOT_CONVEX_ENOUGH,
            'not_rectangular_enough': cls.NOT_RECTANGULAR_ENOUGH,
            'bad_aspect_ratio': cls.BAD_ASPECT_RATIO,
            'not_rectangle_angles': cls.NOT_RECTANGLE_ANGLES,
            'degenerate': cls.DEGENERATE,
            # Size-based mappings
            'too_large_area': cls.TOO_LARGE_AREA,
            'too_long_side': cls.TOO_LONG_SIDE,
            'too_short_side': cls.TOO_SHORT_SIDE,
            'too_elongated': cls.TOO_ELONGATED,
            # Polyskel mappings
            'polyskel_failed': cls.POLYSKEL_FAILED,
            'polyskel_not_available': cls.POLYSKEL_NOT_AVAILABLE,
        }
        return mapping.get(eligibility_value, cls.NONE)


@dataclass
class BuildingRecord:
    """
    Complete building record with all attributes needed for mesh generation.

    Attributes:
        osm_id: OSM element ID (way or relation)
        category: Building category (house, apartment, etc.)
        footprint: Polygon with outer ring and optional holes
        floors: Number of floors (default 2)
        height_m: Total wall height in meters (excluding roof)
        roof_type: Type of roof to generate (requested)
        roof_pitch_deg: Roof pitch angle in degrees (30-60)
        roof_direction_deg: Ridge direction in degrees (None = compute from longest axis)
        roof_direction_source: How ridge direction was determined
        floor_z: Computed ground level (from terrain)
        seed: Deterministic random seed for this building

    Computed at generation time:
        ridge_height_m: Height of roof ridge above walls
        actual_roof_type: Final roof type after eligibility check (may differ from roof_type)
        roof_fallback_reason: Why the roof type was changed (if applicable)
        footprint_vertex_count: Number of unique vertices in footprint
    """
    osm_id: str
    category: BuildingCategory
    footprint: Polygon

    # Height parameters
    floors: int = 2
    height_m: float = 6.0  # Default: 2 floors * 3m

    # Roof parameters (requested)
    roof_type: RoofType = RoofType.GABLED
    roof_pitch_deg: float = 45.0
    roof_direction_deg: Optional[float] = None
    roof_direction_source: RoofDirectionSource = RoofDirectionSource.DEFAULT

    # Computed values
    floor_z: float = 0.0
    seed: int = 0
    ridge_height_m: float = 0.0
    actual_roof_type: Optional[RoofType] = None
    roof_fallback_reason: RoofFallbackReason = RoofFallbackReason.NONE
    footprint_vertex_count: int = 0  # Set during processing

    # Tracking
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Validate and adjust parameters after initialization."""
        # Clamp roof pitch to valid range
        self.roof_pitch_deg = max(30.0, min(60.0, self.roof_pitch_deg))

        # Ensure at least 1 floor
        self.floors = max(1, self.floors)

        # Default actual_roof_type to requested roof_type
        if self.actual_roof_type is None:
            self.actual_roof_type = self.roof_type

    @property
    def wall_top_z(self) -> float:
        """Z coordinate of wall top (floor_z + height_m)."""
        return self.floor_z + self.height_m

    @property
    def roof_top_z(self) -> float:
        """Z coordinate of roof peak."""
        return self.wall_top_z + self.ridge_height_m

    def add_warning(self, message: str) -> None:
        """Add a warning message to this building's record."""
        self.warnings.append(message)

    @staticmethod
    def estimate_height(footprint_area: float, category: BuildingCategory) -> tuple:
        """
        Estimate floors and height from footprint area and category.

        Returns:
            (floors, height_m) tuple
        """
        floor_height = 3.0  # meters per floor

        if category == BuildingCategory.INDUSTRIAL:
            # Industrial: typically 1-2 floors, taller ceilings
            floors = 1
            height = 6.0
        elif category == BuildingCategory.APARTMENT:
            # Apartments: estimate from footprint size
            if footprint_area > 500:
                floors = 4
            elif footprint_area > 200:
                floors = 3
            else:
                floors = 2
            height = floors * floor_height
        elif category == BuildingCategory.COMMERCIAL:
            # Commercial: typically 1-2 floors
            floors = 2 if footprint_area > 200 else 1
            height = floors * floor_height
        elif category == BuildingCategory.HOUSE:
            # Houses always estimated at 2 floors (6m).
            # Only explicit OSM tags (building:levels or height) override this.
            # Previous threshold of 150m² was too low - a typical European house
            # of 12m x 14m = 168m² is normal and should still be 2 floors.
            floors = 2
            height = floors * floor_height
        else:
            # Other/unknown: use heuristic based on footprint
            if footprint_area > 300:
                floors = 3
            elif footprint_area > 100:
                floors = 2
            else:
                floors = 1
            height = floors * floor_height

        return floors, height
