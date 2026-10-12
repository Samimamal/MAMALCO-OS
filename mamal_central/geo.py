"""Coordinate helpers: UTM (WGS84) -> lat/lng, polygon areas, KML parsing.

Iranian survey sketches (کروکی) give UTM coordinates; Oshnavieh is in zone 38N.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

_A = 6378137.0
_F = 1 / 298.257223563
_K0 = 0.9996
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
OSHNAVIEH_UTM_ZONE = 38


def utm_to_latlng(easting: float, northing: float, zone: int = OSHNAVIEH_UTM_ZONE,
                  northern: bool = True) -> tuple[float, float]:
    x = easting - 500_000.0
    y = northing if northern else northing - 10_000_000.0
    mu = (y / _K0) / (_A * (1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = _A / math.sqrt(1 - _E2 * sin1**2)
    t1 = tan1**2
    c1 = _EP2 * cos1**2
    r1 = _A * (1 - _E2) / (1 - _E2 * sin1**2) ** 1.5
    d = x / (n1 * _K0)
    lat = phi1 - (n1 * tan1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * _EP2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * _EP2 - 3 * c1**2) * d**6 / 720
    )
    lng = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * _EP2 + 24 * t1**2) * d**5 / 120
    ) / cos1
    lng0 = math.radians((zone - 1) * 6 - 180 + 3)
    return round(math.degrees(lat), 7), round(math.degrees(lng0 + lng), 7)


def shoelace_area(points: list[tuple[float, float]]) -> float:
    """Area of a polygon given in metric coordinates (e.g. UTM)."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def latlng_area(points: list[tuple[float, float]]) -> float:
    """Approximate area in m² of a small lat/lng polygon (local equirectangular)."""
    if len(points) < 3:
        return 0.0
    lat0 = math.radians(sum(p[0] for p in points) / len(points))
    r = 6_371_008.8
    xy = [(math.radians(lng) * r * math.cos(lat0), math.radians(lat) * r) for lat, lng in points]
    return shoelace_area(xy)


def parse_kml(text: str) -> list[dict]:
    """Placemarks from a Google Earth KML: [{'name', 'type': 'point'|'polygon', 'coords': [(lat, lng)]}]."""
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("KML with DTD/entities is not accepted")
    root = ET.fromstring(text)
    out = []
    for pm in root.iter():
        if not pm.tag.endswith("Placemark"):
            continue
        name = next((el.text or "" for el in pm.iter() if el.tag.endswith("name")), "").strip()
        for geom in pm.iter():
            kind = "polygon" if geom.tag.endswith("Polygon") else "point" if geom.tag.endswith("Point") else None
            if not kind:
                continue
            coords_el = next((el for el in geom.iter() if el.tag.endswith("coordinates")), None)
            if coords_el is None or not coords_el.text:
                continue
            coords = []
            for chunk in coords_el.text.split():
                parts = chunk.split(",")
                coords.append((float(parts[1]), float(parts[0])))
            if kind == "polygon" and len(coords) > 1 and coords[0] == coords[-1]:
                coords = coords[:-1]
            out.append({"name": name, "type": kind, "coords": coords})
            break
    return out


def point_issues(points) -> list[str]:
    """Data problems worth raising with the municipality: duplicate coordinates."""
    seen: dict[tuple[float, float], list] = {}
    for p in points:
        seen.setdefault((round(p.latitude, 5), round(p.longitude, 5)), []).append(p)
    return [
        " و ".join(f"{p.number}. {p.name}" for p in group) + " مختصات یکسان دارند"
        for group in seen.values() if len(group) > 1
    ]
