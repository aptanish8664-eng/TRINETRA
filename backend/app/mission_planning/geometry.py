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


def geodesic_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Shortest distance on the WGS84 ellipsoid (Vincenty's inverse formula, millimetre-accurate; haversine if it does not converge)."""
    lat1, lon1, lat2, lon2 = math.radians(a[0]), math.radians(a[1]), math.radians(b[0]), math.radians(b[1])
    if lat1 == lat2 and lon1 == lon2:
        return 0.0
    flat = 1 / 298.257223563
    semi_minor = _A * (1 - flat)
    u1, u2 = math.atan((1 - flat) * math.tan(lat1)), math.atan((1 - flat) * math.tan(lat2))
    diff = lon2 - lon1
    lam = diff
    for _ in range(200):
        sin_s = math.hypot(math.cos(u2) * math.sin(lam), math.cos(u1) * math.sin(u2) - math.sin(u1) * math.cos(u2) * math.cos(lam))
        if sin_s == 0:
            return 0.0
        cos_s = math.sin(u1) * math.sin(u2) + math.cos(u1) * math.cos(u2) * math.cos(lam)
        sigma = math.atan2(sin_s, cos_s)
        sin_alpha = math.cos(u1) * math.cos(u2) * math.sin(lam) / sin_s
        cos2_alpha = 1 - sin_alpha ** 2
        cos_2sm = cos_s - 2 * math.sin(u1) * math.sin(u2) / cos2_alpha if cos2_alpha else 0.0
        c = flat / 16 * cos2_alpha * (4 + flat * (4 - 3 * cos2_alpha))
        previous = lam
        lam = diff + (1 - c) * flat * sin_alpha * (sigma + c * sin_s * (cos_2sm + c * cos_s * (-1 + 2 * cos_2sm ** 2)))
        if abs(lam - previous) < 1e-12:
            break
    else:
        h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(diff / 2) ** 2
        return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))
    u_sq = cos2_alpha * (_A ** 2 - semi_minor ** 2) / semi_minor ** 2
    big_a = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    big_b = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    delta = big_b * sin_s * (cos_2sm + big_b / 4 * (cos_s * (-1 + 2 * cos_2sm ** 2) - big_b / 6 * cos_2sm * (-3 + 4 * sin_s ** 2) * (-3 + 4 * cos_2sm ** 2)))
    return semi_minor * big_a * (sigma - delta)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Horizontal distance between two WGS84 points: geodesic, so it is right at 1 m and at 1,000 km alike."""
    return geodesic_m(a, b)


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
