"""
Mesh grouping for Condor Buildings Generator.

Groups building meshes by texture/material for efficient rendering in Condor 3.
Each group corresponds to a single texture atlas and will be exported as a
separate object in the OBJ file.

Groups:
- houses: Pitched roof buildings (walls + roofs combined)
- Highrise_walls: Apartment + commercial building walls (Highrise_atlas.dds)
- industrial_walls: Flat roof industrial building walls
- flat_roof_1..6: Flat roofs distributed randomly across 6 texture groups
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..models.mesh import MeshData
from ..models.building import BuildingRecord, BuildingCategory, RoofType


@dataclass
class SeparatedBuildingResult:
    """
    Result of generating a building with walls and roof as separate meshes.

    This allows the MeshGrouper to classify and route walls and roofs
    to different texture groups.
    """
    walls: MeshData
    roof: MeshData
    actual_roof_type: RoofType
    category: BuildingCategory
    fallback_reason: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


class MeshGrouper:
    """
    Groups building meshes by texture/material type.

    For pitched roofs (gabled/hipped): walls and roof go together to 'houses'
    For flat roofs: walls go to category-specific groups, roofs to random flat_roof groups

    This ensures each group uses a single texture atlas for efficient rendering.
    """

    def __init__(self, num_flat_roof_groups: int = 6, flat_roof_merge: bool = False):
        """
        Initialize mesh grouper.

        Args:
            num_flat_roof_groups: Number of flat roof texture groups (default 6)
            flat_roof_merge: If True, merge all flat roofs into single object
        """
        self.flat_roof_merge = flat_roof_merge
        self.num_flat_roof_groups = 1 if flat_roof_merge else num_flat_roof_groups

        # Pitched buildings: walls + roofs combined
        self.houses = MeshData()

        # Highrise walls: apartment + commercial merged (Highrise_atlas.dds)
        self.highrise_walls = MeshData()

        # Industrial walls (unchanged, uses original atlas)
        self.industrial_walls = MeshData()

        # Flat roofs distributed across multiple texture groups
        self.flat_roofs: List[MeshData] = [
            MeshData() for _ in range(num_flat_roof_groups)
        ]

        # Statistics
        self.stats = {
            'houses_count': 0,
            'highrise_walls_count': 0,
            'industrial_walls_count': 0,
            'flat_roof_counts': [0] * num_flat_roof_groups,
        }

    def add_building(
        self,
        building: BuildingRecord,
        result: SeparatedBuildingResult
    ) -> None:
        """
        Add a building's meshes to the appropriate groups.

        Args:
            building: The building record with metadata
            result: The separated building result with walls and roof meshes
        """
        if result.actual_roof_type in (RoofType.GABLED, RoofType.HIPPED):
            # Pitched roof: walls + roof go to houses
            self._add_pitched_building(result)
        else:
            # Flat roof: walls and roofs go to separate groups
            self._add_flat_roof_walls(building, result)
            self._add_flat_roof(building, result.roof)

    def _add_pitched_building(self, result: SeparatedBuildingResult) -> None:
        """Add a pitched roof building (walls + roof) to houses group."""
        self.houses.merge(result.walls)
        self.houses.merge(result.roof)
        self.stats['houses_count'] += 1

    def _add_flat_roof_walls(
        self,
        building: BuildingRecord,
        result: SeparatedBuildingResult
    ) -> None:
        """
        Add flat roof building walls to the appropriate category group.

        Classification rules:
        - APARTMENT -> Highrise_walls
        - COMMERCIAL -> Highrise_walls
        - INDUSTRIAL -> industrial_walls
        - OTHER: area > 200m² -> industrial_walls, else -> Highrise_walls
        - HOUSE (rare, fallback to flat) -> Highrise_walls (regions 0-5)
        """
        category = result.category

        if category == BuildingCategory.APARTMENT:
            self.highrise_walls.merge(result.walls)
            self.stats['highrise_walls_count'] += 1
        elif category == BuildingCategory.COMMERCIAL:
            self.highrise_walls.merge(result.walls)
            self.stats['highrise_walls_count'] += 1
        elif category == BuildingCategory.INDUSTRIAL:
            self.industrial_walls.merge(result.walls)
            self.stats['industrial_walls_count'] += 1
        elif category == BuildingCategory.OTHER:
            # Use footprint area to classify OTHER buildings
            area = building.footprint.area()
            if area > 200:
                self.industrial_walls.merge(result.walls)
                self.stats['industrial_walls_count'] += 1
            else:
                self.highrise_walls.merge(result.walls)
                self.stats['highrise_walls_count'] += 1
        else:
            # HOUSE with flat roof (rare fallback case) - route to Highrise_walls
            # Uses highrise atlas (apartment regions 0-5), NOT houses atlas
            self.highrise_walls.merge(result.walls)
            self.stats['highrise_walls_count'] += 1

    def _add_flat_roof(self, building: BuildingRecord, roof: MeshData) -> None:
        """
        Add a flat roof to one of the texture groups.

        Uses building seed for deterministic random distribution.
        """
        idx = building.seed % self.num_flat_roof_groups
        self.flat_roofs[idx].merge(roof)
        self.stats['flat_roof_counts'][idx] += 1

    def get_all_groups(self) -> Dict[str, MeshData]:
        """
        Get all mesh groups as a dictionary.

        Returns:
            Dictionary mapping group name to MeshData
        """
        groups = {
            'houses': self.houses,
            'Highrise_walls': self.highrise_walls,
            'industrial_walls': self.industrial_walls,
        }

        if self.flat_roof_merge:
            groups['flat_roof'] = self.flat_roofs[0]
        else:
            for i, roof in enumerate(self.flat_roofs):
                groups[f'flat_roof_{i + 1}'] = roof

        return groups

    def get_non_empty_groups(self) -> Dict[str, MeshData]:
        """
        Get only non-empty mesh groups.

        Returns:
            Dictionary mapping group name to non-empty MeshData
        """
        return {
            name: mesh
            for name, mesh in self.get_all_groups().items()
            if not mesh.is_empty()
        }

    def get_stats_summary(self) -> str:
        """Get a human-readable summary of grouping statistics."""
        lines = [
            f"houses: {self.stats['houses_count']} buildings",
            f"Highrise_walls: {self.stats['highrise_walls_count']} buildings",
            f"industrial_walls: {self.stats['industrial_walls_count']} buildings",
        ]

        for i, count in enumerate(self.stats['flat_roof_counts']):
            lines.append(f"flat_roof_{i + 1}: {count} roofs")

        return "\n".join(lines)
