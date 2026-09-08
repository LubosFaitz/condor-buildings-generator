"""
Terrain OBJ loader for Condor Buildings Generator.

Loads terrain meshes from Condor h*.obj files and prepares them
for FloorZ computation. Terrain is a 30m quad grid that must be
triangulated for intersection tests.
"""

from pathlib import Path
from typing import List, Tuple, Optional
import logging

from ..models.geometry import Point3D, BBox
from ..models.terrain import TerrainMesh, TerrainTriangle
from ..config import TERRAIN_GRID_STEP, TERRAIN_CELL_SIZE

logger = logging.getLogger(__name__)


class TerrainLoadError(Exception):
    """Raised when terrain loading fails."""
    pass


def load_terrain(filepath: str, grid_step: float = TERRAIN_GRID_STEP) -> TerrainMesh:
    """
    Load terrain mesh from OBJ file.

    Condor terrain OBJ format:
        - Header comment
        - Object name: o TR3XXXXXX
        - Vertices: v x y z
        - Faces: f v1// v2// v3// v4// (quads, 1-indexed)

    The terrain is a regular grid of quads at ~30m spacing.
    This function triangulates the quads and builds a spatial index.

    Args:
        filepath: Path to h*.obj terrain file
        grid_step: Expected grid spacing (default 30m)

    Returns:
        TerrainMesh with triangles and spatial index

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is invalid
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Terrain file not found: {filepath}")

    logger.info(f"Loading terrain from {filepath}")

    vertices: List[Point3D] = []
    quads: List[Tuple[int, int, int, int]] = []

    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()

            if not line or line.startswith('#') or line.startswith('o '):
                continue

            if line.startswith('v '):
                # Parse vertex: v x y z
                try:
                    parts = line.split()
                    x = float(parts[1])
                    y = float(parts[2])
                    z = float(parts[3])
                    vertices.append(Point3D(x, y, z))
                except (IndexError, ValueError) as e:
                    logger.warning(f"Invalid vertex at line {line_num}: {line}")

            elif line.startswith('f '):
                # Parse face: f v1// v2// v3// v4// or f v1 v2 v3 v4
                try:
                    parts = line.split()[1:]  # Skip 'f'
                    indices = []

                    for part in parts:
                        # Handle various OBJ face formats
                        # v// or v/vt/vn or v
                        idx_str = part.split('/')[0]
                        idx = int(idx_str) - 1  # Convert to 0-indexed
                        indices.append(idx)

                    if len(indices) == 4:
                        quads.append(tuple(indices))
                    elif len(indices) == 3:
                        # Triangle - convert to degenerate quad for uniform handling
                        # (we'll triangulate anyway)
                        quads.append((indices[0], indices[1], indices[2], indices[2]))
                    else:
                        logger.warning(
                            f"Unexpected face vertex count at line {line_num}: {len(indices)}"
                        )

                except (IndexError, ValueError) as e:
                    logger.warning(f"Invalid face at line {line_num}: {line}")

    if not vertices:
        raise ValueError(f"No vertices found in terrain file: {filepath}")

    if not quads:
        raise ValueError(f"No faces found in terrain file: {filepath}")

    logger.info(f"Loaded {len(vertices)} vertices and {len(quads)} quads")

    # Create terrain mesh with triangulation and spatial index
    mesh = TerrainMesh.from_quads(vertices, quads, grid_step)

    logger.info(
        f"Created terrain mesh with {len(mesh.triangles)} triangles, "
        f"elevation range [{mesh.z_min:.1f}, {mesh.z_max:.1f}]m"
    )

    return mesh


def validate_terrain(mesh: TerrainMesh) -> List[str]:
    """
    Validate terrain mesh integrity.

    Args:
        mesh: TerrainMesh to validate

    Returns:
        List of warning/error messages (empty if valid)
    """
    issues = []

    # Check for reasonable bounds
    if mesh.bbox.width < 1000 or mesh.bbox.height < 1000:
        issues.append(
            f"Terrain is smaller than expected: "
            f"{mesh.bbox.width:.0f}x{mesh.bbox.height:.0f}m"
        )

    # Check for degenerate triangles
    degenerate_count = 0
    for tri in mesh.triangles:
        # Check if any edge is too short
        edges = [
            tri.v0.distance_to(tri.v1),
            tri.v1.distance_to(tri.v2),
            tri.v2.distance_to(tri.v0),
        ]
        if min(edges) < 0.1:  # Less than 10cm
            degenerate_count += 1

    if degenerate_count > 0:
        issues.append(f"Found {degenerate_count} degenerate triangles")

    # Check spatial index
    if not mesh.grid_cells:
        issues.append("Spatial index is empty")
    else:
        total_indexed = sum(len(cell) for cell in mesh.grid_cells.values())
        if total_indexed != len(mesh.triangles):
            issues.append(
                f"Spatial index coverage mismatch: "
                f"{total_indexed} indexed vs {len(mesh.triangles)} triangles"
            )

    return issues


def get_terrain_stats(mesh: TerrainMesh) -> dict:
    """
    Get statistics about terrain mesh.

    Args:
        mesh: TerrainMesh to analyze

    Returns:
        Dictionary with terrain statistics
    """
    return {
        'vertex_count': len(mesh.vertices),
        'quad_count': len(mesh.quads),
        'triangle_count': len(mesh.triangles),
        'bbox': {
            'min_x': mesh.bbox.min_x,
            'min_y': mesh.bbox.min_y,
            'max_x': mesh.bbox.max_x,
            'max_y': mesh.bbox.max_y,
            'width': mesh.bbox.width,
            'height': mesh.bbox.height,
        },
        'elevation': {
            'min': mesh.z_min,
            'max': mesh.z_max,
            'range': mesh.z_max - mesh.z_min,
        },
        'grid_step': mesh.grid_step,
        'spatial_index_cells': len(mesh.grid_cells),
    }
