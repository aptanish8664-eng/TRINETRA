"""Local ENU <-> WGS84 conversion for route planning.

Forward conversion reuses the research module's ECEF-based `geodetic_to_enu`. The inverse here is its exact
mathematical inverse (ENU -> ECEF -> geodetic), so a round trip returns the input to numerical precision.

Flat-earth assumption: patterns are drawn on the local tangent plane at the start position (up = 0). The ENU
conversion itself is exact, but treating the plane as flat means a point d metres away sits about d^2/(2R)
above the true surface (0.08 m at 1 km, 1.3 m at 4 km, 7.8 m at 10 km, 31 m at 20 km) and straight ENU
lines are not geodesics. Latitude/longitude of a planned waypoint is therefore accurate to centimetres within a few km and
degrades slowly; the planner caps the distance from start (config) and the assumption is stated on every
generated route. It is not suitable for long-range navigation.
"""
from __future__ import annotations

import math

import numpy as np

from ..research.sensor_fusion import geodetic_to_enu

_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)

FLAT_EARTH_ASSUMPTION = (
    "Waypoints are drawn on the local tangent plane at the start position (ENU, up = 0) and converted to WGS84 "
    "with an exact ECEF transform. Straight lines are not geodesics and the plane ignores earth curvature "
    "(about d^2/2R vertical drop at distance d), so use only for areas of a few kilometres around the start."
)


def to_enu(origin: tuple[float, float], latitude: float, longitude: float) -> tuple[float, float, float]:
    vector = geodetic_to_enu(
        latitude, longitude, 0.0, origin_latitude_deg=origin[0], origin_longitude_deg=origin[1], origin_altitude_m=0.0,
    )
    return float(vector[0]), float(vector[1]), float(vector[2])


def _ecef(latitude: float, longitude: float, altitude: float) -> np.ndarray:
    lat, lon = math.radians(latitude), math.radians(longitude)
    n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
    return np.asarray(((n + altitude) * math.cos(lat) * math.cos(lon),
                       (n + altitude) * math.cos(lat) * math.sin(lon),
                       (n * (1 - _E2) + altitude) * math.sin(lat)))


def from_enu(origin: tuple[float, float], east: float, north: float, up: float = 0.0) -> tuple[float, float]:
    """Exact inverse of `to_enu`: returns (latitude, longitude) in degrees."""
    lat0, lon0 = math.radians(origin[0]), math.radians(origin[1])
    rotation = np.asarray(((-math.sin(lon0), math.cos(lon0), 0),
                           (-math.sin(lat0) * math.cos(lon0), -math.sin(lat0) * math.sin(lon0), math.cos(lat0)),
                           (math.cos(lat0) * math.cos(lon0), math.cos(lat0) * math.sin(lon0), math.sin(lat0))))
    x, y, z = _ecef(origin[0], origin[1], 0.0) + rotation.T @ np.asarray((east, north, up))
    longitude = math.atan2(y, x)
    p = math.hypot(x, y)
    latitude = math.atan2(z, p * (1 - _E2))
    for _ in range(8):
        n = _A / math.sqrt(1 - _E2 * math.sin(latitude) ** 2)
        height = p / math.cos(latitude) - n
        latitude = math.atan2(z, p * (1 - _E2 * n / (n + height)))
    return math.degrees(latitude), math.degrees(longitude)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    east, north, _ = to_enu(a, b[0], b[1])
    return math.hypot(east, north)


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    inside = False
    x, y = point
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside
