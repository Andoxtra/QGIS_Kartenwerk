"""Magnetic declination from the World Magnetic Model (WMM2025).

Pure Python (no QGIS), so it can be tested offline. The coefficients in
``WMM2025.COF`` are published by NOAA NCEI / British Geological Survey and are
in the public domain. The model is valid from 2025.0 to 2030.0; outside that
period ``declination`` returns None instead of extrapolating.

Algorithm: WMM2025 Technical Report, section 1.2 (spherical harmonic synthesis
in geocentric coordinates, rotated to the geodetic frame).
"""
from __future__ import annotations

import datetime as _dt
import math
from functools import lru_cache
from pathlib import Path

COF_PATH = Path(__file__).with_name("WMM2025.COF")
_A = 6371.2  # geomagnetic reference radius (km)
_WGS84_A = 6378.137
_WGS84_F = 1.0 / 298.257223563
_E2 = _WGS84_F * (2.0 - _WGS84_F)


@lru_cache(maxsize=1)
def _model():
    lines = COF_PATH.read_text(encoding="ascii").splitlines()
    epoch = float(lines[0].split()[0])
    name = lines[0].split()[1]
    coeffs = {}
    nmax = 0
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6 or parts[0].startswith("9999"):
            break
        n, m = int(parts[0]), int(parts[1])
        g, h, gdot, hdot = (float(v) for v in parts[2:6])
        coeffs[(n, m)] = (g, h, gdot, hdot)
        nmax = max(nmax, n)
    return epoch, name, nmax, coeffs


def model_name() -> str:
    return _model()[1]


def valid_range() -> tuple[float, float]:
    epoch = _model()[0]
    return epoch, epoch + 5.0


def decimal_year(date: _dt.date | None = None) -> float:
    date = date or _dt.date.today()
    start = _dt.date(date.year, 1, 1)
    days = (_dt.date(date.year + 1, 1, 1) - start).days
    return date.year + (date - start).days / days


def field(lat: float, lon: float, height_km: float, year: float) -> tuple[float, float, float]:
    """Return the geodetic (X north, Y east, Z down) field components in nT."""
    epoch, _, nmax, coeffs = _model()
    dt = year - epoch

    # Geodetic → geocentric spherical coordinates.
    phi = math.radians(lat)
    lam = math.radians(lon)
    rc = _WGS84_A / math.sqrt(1.0 - _E2 * math.sin(phi) ** 2)
    p = (rc + height_km) * math.cos(phi)
    z = (rc * (1.0 - _E2) + height_km) * math.sin(phi)
    r = math.hypot(p, z)
    phi_c = math.asin(z / r)

    # Associated Legendre functions in colatitude θ (Gauss-normalised recursion,
    # then Schmidt semi-normalisation), with derivatives d/dθ.
    cos_t = math.sin(phi_c)
    sin_t = max(math.cos(phi_c), 1e-12)
    P = [[0.0] * (nmax + 1) for _ in range(nmax + 1)]
    dP = [[0.0] * (nmax + 1) for _ in range(nmax + 1)]
    P[0][0] = 1.0
    for n in range(1, nmax + 1):
        for m in range(n + 1):
            if n == m:
                P[n][m] = sin_t * P[n - 1][m - 1]
                dP[n][m] = sin_t * dP[n - 1][m - 1] + cos_t * P[n - 1][m - 1]
            elif n == 1 or m == n - 1:
                P[n][m] = cos_t * P[n - 1][m]
                dP[n][m] = cos_t * dP[n - 1][m] - sin_t * P[n - 1][m]
            else:
                k = ((n - 1) ** 2 - m ** 2) / ((2 * n - 1) * (2 * n - 3))
                P[n][m] = cos_t * P[n - 1][m] - k * P[n - 2][m]
                dP[n][m] = cos_t * dP[n - 1][m] - sin_t * P[n - 1][m] - k * dP[n - 2][m]

    schmidt = [[0.0] * (nmax + 1) for _ in range(nmax + 1)]
    schmidt[0][0] = 1.0
    for n in range(1, nmax + 1):
        schmidt[n][0] = schmidt[n - 1][0] * (2 * n - 1) / n
        for m in range(1, n + 1):
            factor = 2.0 if m == 1 else 1.0
            schmidt[n][m] = schmidt[n][m - 1] * math.sqrt((n - m + 1) * factor / (n + m))

    x_c = y_c = z_c = 0.0
    for n in range(1, nmax + 1):
        ratio = (_A / r) ** (n + 2)
        for m in range(n + 1):
            g, h, gdot, hdot = coeffs[(n, m)]
            g += gdot * dt
            h += hdot * dt
            pnm = schmidt[n][m] * P[n][m]
            dpnm = schmidt[n][m] * dP[n][m]
            cos_ml, sin_ml = math.cos(m * lam), math.sin(m * lam)
            gh = g * cos_ml + h * sin_ml
            x_c += ratio * gh * dpnm
            y_c += ratio * m * (g * sin_ml - h * cos_ml) * pnm
            z_c -= (n + 1) * ratio * gh * pnm
    y_c /= sin_t

    # Rotate from geocentric to geodetic frame.
    psi = phi_c - phi
    x = x_c * math.cos(psi) - z_c * math.sin(psi)
    zz = x_c * math.sin(psi) + z_c * math.cos(psi)
    return x, y_c, zz


def declination(lat: float, lon: float, year: float | None = None, height_km: float = 0.0):
    """Magnetic declination in degrees (positive = magnetic north east of true north).

    Returns None when ``year`` lies outside the model's validity period.
    """
    year = decimal_year() if year is None else year
    start, end = valid_range()
    if not (start <= year < end):
        return None
    x, y, _ = field(lat, lon, height_km, year)
    return math.degrees(math.atan2(y, x))
