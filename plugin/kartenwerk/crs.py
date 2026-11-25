"""UTM zone handling.

Every map series uses WGS 84 / UTM (EPSG:326zz north, EPSG:327zz south). The
zone is chosen from the centre of the working area, so the grid is metric and
the coordinate system is the same type everywhere in the world.

The zone maths is pure Python so it can be tested without QGIS; the QGIS
helpers import ``qgis.core`` lazily.
"""
from __future__ import annotations

import math
import re

# UTM is defined between 80° S and 84° N; beyond that it is replaced by UPS.
MIN_LAT = -80.0
MAX_LAT = 84.0
# Warn when the working area reaches this far (degrees) outside its zone.
ZONE_OVERLAP_WARN_DEG = 3.0

_EPSG_RE = re.compile(r"^EPSG:(32[67])(\d\d)$")


def utm_zone_for(lon: float, lat: float) -> tuple[int, str]:
    """Return (zone 1–60, hemisphere 'N'/'S') for a WGS 84 position."""
    if not (MIN_LAT <= lat <= MAX_LAT):
        raise ValueError(
            f"Latitude {lat:.2f}° is outside the UTM range "
            f"({MIN_LAT:.0f}° S to {MAX_LAT:.0f}° N). Polar areas are not supported."
        )
    lon = ((lon + 180.0) % 360.0) - 180.0
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return zone, ("N" if lat >= 0 else "S")


def zone_epsg(zone: int, hemisphere: str) -> int:
    return (32600 if hemisphere.upper() == "N" else 32700) + int(zone)


def zone_authid(zone: int, hemisphere: str) -> str:
    return f"EPSG:{zone_epsg(zone, hemisphere)}"


def zone_label(zone: int, hemisphere: str) -> str:
    """Short label such as '32N'."""
    return f"{int(zone)}{hemisphere.upper()}"


def zone_from_authid(authid: str) -> tuple[int, str] | None:
    """Parse 'EPSG:32632' → (32, 'N'). Returns None for non-UTM CRSs."""
    match = _EPSG_RE.match(authid or "")
    if not match:
        return None
    return int(match.group(2)), ("N" if match.group(1) == "326" else "S")


def zone_central_meridian(zone: int) -> float:
    return -183.0 + 6.0 * int(zone)


def degrees_outside_zone(zone: int, lon_min: float, lon_max: float) -> float:
    """How far (degrees of longitude) a lon range reaches outside the zone."""
    west = zone_central_meridian(zone) - 3.0
    east = zone_central_meridian(zone) + 3.0
    return max(0.0, west - lon_min, lon_max - east)


def sheet_id(zone: int, hemisphere: str, row: int, col: int) -> str:
    """Stable sheet id, e.g. '32N-R1592C0094'."""
    return f"{zone_label(zone, hemisphere)}-R{row:04d}C{col:04d}"


# ---------------------------------------------------------------------------
# QGIS helpers
# ---------------------------------------------------------------------------
def utm_crs(zone: int, hemisphere: str):
    from qgis.core import QgsCoordinateReferenceSystem

    return QgsCoordinateReferenceSystem(zone_authid(zone, hemisphere))


def wgs84():
    from qgis.core import QgsCoordinateReferenceSystem

    return QgsCoordinateReferenceSystem("EPSG:4326")


def to_lonlat_bbox(rect, crs):
    """Transform a QgsRectangle in ``crs`` to (lon_min, lat_min, lon_max, lat_max)."""
    from qgis.core import QgsCoordinateTransform, QgsProject

    if crs.authid() != "EPSG:4326":
        xform = QgsCoordinateTransform(crs, wgs84(), QgsProject.instance())
        rect = xform.transformBoundingBox(rect)
    return rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()


def layer_lonlat_bbox(layer):
    """Lon/lat bbox of all features of a vector layer, or None when empty."""
    if layer is None or layer.featureCount() == 0:
        return None
    layer.updateExtents()
    extent = layer.extent()
    if extent.isNull():
        return None
    return to_lonlat_bbox(extent, layer.crs())


def grid_convergence(easting: float, northing: float, utm) -> float:
    """Grid convergence at a UTM point, in degrees.

    Convention: the angle from true north to grid north, positive when grid
    north lies EAST of true north (clockwise). In the northern hemisphere it is
    positive east of the zone's central meridian (e.g. Munich in zone 32: +1.9°).
    Computed numerically by stepping north along the meridian, so it holds for
    both hemispheres without a series formula.
    """
    from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject

    to_geo = QgsCoordinateTransform(utm, wgs84(), QgsProject.instance())
    to_utm = QgsCoordinateTransform(wgs84(), utm, QgsProject.instance())
    geo = to_geo.transform(QgsPointXY(easting, northing))
    north = to_utm.transform(QgsPointXY(geo.x(), geo.y() + 0.001))
    # Azimuth of true north measured clockwise from grid north …
    true_north_azimuth = math.degrees(math.atan2(north.x() - easting, north.y() - northing))
    # … so grid north lies the opposite way from true north.
    return -true_north_azimuth


def lonlat(easting: float, northing: float, utm) -> tuple[float, float]:
    from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject

    geo = QgsCoordinateTransform(utm, wgs84(), QgsProject.instance()).transform(
        QgsPointXY(easting, northing))
    return geo.x(), geo.y()
