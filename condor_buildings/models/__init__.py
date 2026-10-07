"""
Data models for Condor Buildings Generator.
"""

from .geometry import Point2D, Point3D, BBox, Polygon
from .building import BuildingRecord, BuildingCategory, RoofType
from .terrain import TerrainMesh, TerrainTriangle
from .mesh import MeshData, OptimizeResult

__all__ = [
    'Point2D', 'Point3D', 'BBox', 'Polygon',
    'BuildingRecord', 'BuildingCategory', 'RoofType',
    'TerrainMesh', 'TerrainTriangle',
    'MeshData', 'OptimizeResult',
]
