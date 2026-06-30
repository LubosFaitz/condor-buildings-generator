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


def _nearest_edge_outward(fc_x: float, fc_y: float, ring):
    min_dist_sq = float('inf')
    best_nx = 0.0; best_ny = 0.0
    n = len(ring)
    for i in range(n):
        j = (i + 1) % n
        ax, ay = ring[i].x, ring[i].y
        bx, by = ring[j].x, ring[j].y
        ex = bx - ax; ey = by - ay
        len_sq = ex * ex + ey * ey
        if len_sq < 1e-12:
            continue
        t = max(0.0, min(1.0, ((fc_x - ax) * ex + (fc_y - ay) * ey) / len_sq))
        px = ax + t * ex; py = ay + t * ey
        dist_sq = (fc_x - px) ** 2 + (fc_y - py) ** 2
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            length = len_sq ** 0.5
            best_nx = ey / length; best_ny = -ex / length
    return min_dist_sq ** 0.5, best_nx, best_ny


def _fix_normals_outward(mesh_data: MeshData, outer_ring, holes=None) -> None:
    verts = mesh_data.vertices
    has_uvs = len(mesh_data.face_uvs) == len(mesh_data.faces)
    holes = holes or []
    for i, face in enumerate(mesh_data.faces):
        if len(face) < 3:
            continue
        n = len(face)
        fc_x = sum(verts[idx - 1][0] for idx in face) / n
        fc_y = sum(verts[idx - 1][1] for idx in face) / n
        v0 = verts[face[0]-1]; v1 = verts[face[1]-1]; v2 = verts[face[2]-1]
        ex = v1[0]-v0[0]; ey = v1[1]-v0[1]; ez = v1[2]-v0[2]
        fx = v2[0]-v0[0]; fy = v2[1]-v0[1]; fz = v2[2]-v0[2]
        nx = ey*fz-ez*fy; ny = ez*fx-ex*fz; nz = ex*fy-ey*fx
        if nx*nx + ny*ny + nz*nz < 1e-10:
            continue
        if nx*nx + ny*ny < 1e-6:
            should_flip = nz < 0
        else:
            out_d, out_nx, out_ny = _nearest_edge_outward(fc_x, fc_y, outer_ring)
            exp_nx, exp_ny = out_nx, out_ny
            best_d = out_d
            for hole in holes:
                h_d, h_nx, h_ny = _nearest_edge_outward(fc_x, fc_y, hole)
                if h_d < best_d:
                    best_d = h_d
                    exp_nx, exp_ny = h_nx, h_ny
            should_flip = (nx * exp_nx + ny * exp_ny) < 0
        if should_flip:
            mesh_data.faces[i] = list(reversed(face))
            if has_uvs:
                mesh_data.face_uvs[i] = list(reversed(mesh_data.face_uvs[i]))


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

    def __init__(self, num_flat_roof_groups: int = 6, flat_roof_merge: bool = False, is_lod0: bool = False, flat_roof_industrial_only: bool = False):
        """
        Initialize mesh grouper.

        Args:
            num_flat_roof_groups: Number of flat roof texture groups (default 6)
            flat_roof_merge: If True, merge all flat roofs into single object
            is_lod0: If True, gabled roofs are kept separate for LOD0 doubling in Blender
            flat_roof_industrial_only: If True, only industrial flat roofs go to merged flat_roof
        """
        self.flat_roof_merge = flat_roof_merge
        self.flat_roof_industrial_only = flat_roof_industrial_only
        self.num_flat_roof_groups = 1 if (flat_roof_merge and not flat_roof_industrial_only) else num_flat_roof_groups
        self.is_lod0 = is_lod0

        # Pitched buildings: walls (+ hipped roofs combined)
        self.houses = MeshData()

        # LOD0 gabled roofs stored separately so they can be doubled in Blender
        # after validate + recalculate_outside (hipped roofs stay in houses as usual)
        self.gabled_roofs = MeshData()

        # Hipped roofs stored separately: _fix_normals_outward must not run on them
        # (they use double_sided_roof=True with intentional down-facing faces).
        # Blender-side _recalc_normals_outside is applied instead after validate().
        self.hipped_roofs = MeshData()

        # Highrise walls: apartment + commercial merged (Highrise_atlas.dds)
        self.highrise_walls = MeshData()

        # Industrial walls (unchanged, uses original atlas)
        self.industrial_walls = MeshData()

        # Flat roofs distributed across multiple texture groups
        self.flat_roofs: List[MeshData] = [
            MeshData() for _ in range(self.num_flat_roof_groups)
        ]

        # Industrial flat roofs merged separately (used when flat_roof_industrial_only=True)
        self.industrial_flat_roof = MeshData()

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
        outer_ring = building.footprint.outer_ring
        holes = building.footprint.holes
        _fix_normals_outward(result.walls, outer_ring, holes)
        if result.actual_roof_type != RoofType.HIPPED:
            _fix_normals_outward(result.roof, outer_ring, holes)

        if result.actual_roof_type in (RoofType.GABLED, RoofType.HIPPED):
            # Pitched roof: walls + roof go to houses
            self._add_pitched_building(result)
        else:
            # Flat roof: walls and roofs go to separate groups
            self._add_flat_roof_walls(building, result)
            self._add_flat_roof(building, result.roof, result.category)

    def _add_pitched_building(self, result: SeparatedBuildingResult) -> None:
        """Add a pitched roof building (walls + roof) to houses group."""
        self.houses.merge(result.walls)
        if self.is_lod0 and result.actual_roof_type == RoofType.GABLED:
            self.gabled_roofs.merge(result.roof)
        elif result.actual_roof_type == RoofType.HIPPED:
            self.hipped_roofs.merge(result.roof)
        else:
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

    def _add_flat_roof(self, building: BuildingRecord, roof: MeshData, category: BuildingCategory) -> None:
        """
        Add a flat roof to one of the texture groups.

        Uses building seed for deterministic random distribution.
        """
        if self.flat_roof_industrial_only:
            is_industrial = (
                category == BuildingCategory.INDUSTRIAL or
                (category == BuildingCategory.OTHER and building.footprint.area() > 200)
            )
            if is_industrial:
                self.industrial_flat_roof.merge(roof)
                return
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

        if self.is_lod0 and not self.gabled_roofs.is_empty():
            groups['gabled_roofs_lod0'] = self.gabled_roofs

        if not self.hipped_roofs.is_empty():
            groups['hipped_roofs'] = self.hipped_roofs

        if self.flat_roof_industrial_only:
            groups['flat_roof'] = self.industrial_flat_roof
            for i, roof in enumerate(self.flat_roofs):
                groups[f'flat_roof_{i + 1}'] = roof
        elif self.flat_roof_merge:
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
