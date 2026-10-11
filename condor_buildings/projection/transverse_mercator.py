"""
Transverse Mercator (UTM) Projection for Condor Buildings Generator.

Standalone implementation adapted from blender-osm, with Blender
dependencies removed. Provides conversion between WGS84 lat/lon
and local Condor coordinates.

Original source:
    blender-osm (OpenStreetMap importer for Blender)
    Copyright (C) 2014-2018 Vladimir Elistratov
    Modified by Wiek Schoenmakers for Condor 3

This implementation removes the bpy dependency and makes projection
parameters explicit constructor arguments.
"""

import math
from typing import Tuple


class TransverseMercatorProjector:
    """
    UTM-style Transverse Mercator projection for Condor coordinates.

    Converts WGS84 lat/lon to local Condor coordinates using UTM
    projection with Condor-specific translation offsets.

    Attributes:
        zone_number: UTM zone (1-60)
        translate_x: X offset applied after projection
        translate_y: Y offset applied after projection
    """

    # WGS84 ellipsoid parameters
    EQUATORIAL_RADIUS = 6378137.0  # meters
    ECC_SQUARED = 0.00669438       # eccentricity squared

    # UTM scale factor
    K0 = 0.9996

    def __init__(
        self,
        zone_number: int,
        translate_x: float,
        translate_y: float,
        lat: float = 0.0,
        lon: float = 0.0
    ):
        """
        Initialize projector with Condor patch parameters.

        Args:
            zone_number: UTM zone number (e.g., 33 for Slovenia)
            translate_x: X translation offset from h*.txt
            translate_y: Y translation offset from h*.txt
            lat: Reference latitude (optional, for inverse projection)
            lon: Reference longitude (optional, for inverse projection)
        """
        self.zone_number = zone_number
        self.translate_x = translate_x
        self.translate_y = translate_y

        # Reference point for inverse projection
        self.lat = lat
        self.lon = lon
        self.lat_in_radians = math.radians(lat)

        # Central meridian of UTM zone
        self.long_origin = (zone_number - 1) * 6 - 180 + 3

    def project(self, lat: float, lon: float) -> Tuple[float, float]:
        """
        Project lat/lon to local Condor coordinates.

        This is the main projection method used for converting OSM
        coordinates to the Condor coordinate system.

        Args:
            lat: Latitude in degrees
            lon: Longitude in degrees

        Returns:
            (x, y) tuple in local Condor coordinates
        """
        deg2rad = math.pi / 180.0

        # Normalize longitude
        long_temp = (lon + 180) - int((lon + 180) / 360) * 360 - 180

        lat_rad = lat * deg2rad
        long_rad = long_temp * deg2rad
        long_origin_rad = self.long_origin * deg2rad

        ecc_prime_squared = self.ECC_SQUARED / (1 - self.ECC_SQUARED)

        # Radius of curvature in prime vertical
        N = self.EQUATORIAL_RADIUS / math.sqrt(
            1 - self.ECC_SQUARED * math.sin(lat_rad) ** 2
        )

        T = math.tan(lat_rad) ** 2
        C = ecc_prime_squared * math.cos(lat_rad) ** 2
        A = math.cos(lat_rad) * (long_rad - long_origin_rad)

        # Meridional arc length
        M = self.EQUATORIAL_RADIUS * (
            (1 - self.ECC_SQUARED / 4
             - 3 * self.ECC_SQUARED ** 2 / 64
             - 5 * self.ECC_SQUARED ** 3 / 256) * lat_rad
            - (3 * self.ECC_SQUARED / 8
               + 3 * self.ECC_SQUARED ** 2 / 32
               + 45 * self.ECC_SQUARED ** 3 / 1024) * math.sin(2 * lat_rad)
            + (15 * self.ECC_SQUARED ** 2 / 256
               + 45 * self.ECC_SQUARED ** 3 / 1024) * math.sin(4 * lat_rad)
            - (35 * self.ECC_SQUARED ** 3 / 3072) * math.sin(6 * lat_rad)
        )

        # UTM easting
        x = self.K0 * N * (
            A
            + (1 - T + C) * A ** 3 / 6
            + (5 - 18 * T + T ** 2 + 72 * C - 58 * ecc_prime_squared)
              * A ** 5 / 120
        ) + 500000.0

        # UTM northing
        y = self.K0 * (
            M + N * math.tan(lat_rad) * (
                A ** 2 / 2
                + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
                + (61 - 58 * T + T ** 2 + 600 * C - 330 * ecc_prime_squared)
                  * A ** 6 / 720
            )
        )

        # Southern hemisphere offset
        if lat < 0:
            y += 10000000.0

        # Apply Condor translation offsets
        x += self.translate_x
        y += self.translate_y

        return (x, y)

    def unproject(self, x: float, y: float) -> Tuple[float, float]:
        """
        Convert local coordinates back to lat/lon.

        Note: This is a simplified inverse using the reference point.
        For more accurate results, a full inverse UTM would be needed.

        Args:
            x: Local X coordinate
            y: Local Y coordinate

        Returns:
            (lat, lon) tuple in degrees
        """
        # Remove Condor offsets
        x -= self.translate_x
        y -= self.translate_y

        # Remove UTM false easting
        x -= 500000.0

        # Simplified inverse using Mercator approximation
        # This is less accurate than full inverse UTM but sufficient
        # for most purposes

        k = 1.0  # scale factor at reference
        radius = self.EQUATORIAL_RADIUS

        x_scaled = x / (k * radius)
        y_scaled = y / (k * radius)

        D = y_scaled + self.lat_in_radians

        lon_rad = math.atan(math.sinh(x_scaled) / math.cos(D))
        lat_rad = math.asin(math.sin(D) / math.cosh(x_scaled))

        lon = self.lon + math.degrees(lon_rad)
        lat = math.degrees(lat_rad)

        return (lat, lon)

    def from_geographic(self, lat: float, lon: float) -> Tuple[float, float, float]:
        """
        Legacy method name for compatibility with original blosm code.

        Args:
            lat: Latitude in degrees
            lon: Longitude in degrees

        Returns:
            (x, y, 0.0) tuple - Z is always 0 from projection
        """
        x, y = self.project(lat, lon)
        return (x, y, 0.0)

    def to_geographic(self, x: float, y: float) -> Tuple[float, float]:
        """
        Legacy method name for compatibility.

        Args:
            x: Local X coordinate
            y: Local Y coordinate

        Returns:
            (lat, lon) tuple in degrees
        """
        return self.unproject(x, y)
