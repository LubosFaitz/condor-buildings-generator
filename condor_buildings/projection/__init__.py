"""
Projection module for Condor Buildings Generator.

Provides a pluggable projection interface and implementations for
converting between geographic (lat/lon) and local Condor coordinates.
"""

from abc import ABC, abstractmethod
from typing import Tuple

from .transverse_mercator import TransverseMercatorProjector


class IProjector(ABC):
    """
    Abstract interface for coordinate projection.

    Implementations convert between WGS84 lat/lon and local
    Condor coordinate system.
    """

    @abstractmethod
    def project(self, lat: float, lon: float) -> Tuple[float, float]:
        """
        Project geographic coordinates to local coordinates.

        Args:
            lat: Latitude in degrees (WGS84)
            lon: Longitude in degrees (WGS84)

        Returns:
            (x, y) tuple in local Condor coordinates
        """
        pass

    @abstractmethod
    def unproject(self, x: float, y: float) -> Tuple[float, float]:
        """
        Convert local coordinates back to geographic.

        Args:
            x: Local X coordinate
            y: Local Y coordinate

        Returns:
            (lat, lon) tuple in degrees (WGS84)
        """
        pass


def create_projector(
    zone_number: int,
    translate_x: float,
    translate_y: float
) -> IProjector:
    """
    Factory function to create the default projector.

    Currently uses Transverse Mercator (UTM) projection
    with Condor-specific translation offsets.

    Args:
        zone_number: UTM zone number (e.g., 33 for Slovenia)
        translate_x: X translation offset for local coordinates
        translate_y: Y translation offset for local coordinates

    Returns:
        IProjector implementation
    """
    return TransverseMercatorProjector(
        zone_number=zone_number,
        translate_x=translate_x,
        translate_y=translate_y
    )


__all__ = [
    'IProjector',
    'TransverseMercatorProjector',
    'create_projector',
]
