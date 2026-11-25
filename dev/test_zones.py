#!/usr/bin/env python3
"""Offline tests for UTM zone maths and basemap selection (no QGIS needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin"))

from kartenwerk import crs, sources, wmm  # noqa: E402

failures = 0


def check(label, got, expected):
    global failures
    ok = got == expected
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (expected {expected!r})"))


# --- zones -------------------------------------------------------------------
for name, lon, lat, expected in [
    ("Stuttgart", 9.18, 48.78, (32, "N")),
    ("Colorado", -105.5, 39.5, (13, "N")),
    ("Tokyo", 139.69, 35.69, (54, "N")),
    ("Sydney", 151.21, -33.87, (56, "S")),
    ("Buenos Aires", -58.38, -34.60, (21, "S")),
    ("Quito (equator)", -78.47, -0.18, (17, "S")),
    ("lon 180", 180.0, 10.0, (1, "N")),
    ("lon -180", -180.0, 10.0, (1, "N")),
    ("lon 179.9", 179.9, 10.0, (60, "N")),
]:
    check(f"zone {name}", crs.utm_zone_for(lon, lat), expected)

check("epsg 32N", crs.zone_authid(32, "N"), "EPSG:32632")
check("epsg 56S", crs.zone_authid(56, "S"), "EPSG:32756")
check("parse EPSG:32756", crs.zone_from_authid("EPSG:32756"), (56, "S"))
check("parse EPSG:25832 (v1 grid)", crs.zone_from_authid("EPSG:25832"), None)
check("sheet id", crs.sheet_id(32, "N", 1592, 94), "32N-R1592C0094")
check("outside zone 32", round(crs.degrees_outside_zone(32, 4.0, 13.0), 2), 2.0)

for lat in (85.0, -81.0):
    try:
        crs.utm_zone_for(10.0, lat)
        check(f"polar {lat} rejected", False, True)
    except ValueError:
        check(f"polar {lat} rejected", True, True)

# --- source selection ----------------------------------------------------------
for name, bbox, topo, imagery in [
    ("BW (Schwarzwald)", (8.1, 48.0, 8.3, 48.1), "topplusopen", "lgl_bw_dop20"),
    ("Bavaria (Munich)", (11.5, 48.1, 11.6, 48.2), "topplusopen", "bayern_dop40"),
    ("Netherlands", (5.0, 52.0, 5.1, 52.1), "opentopomap", "pdok_luchtfoto"),
    ("Switzerland (Bern)", (7.4, 46.9, 7.5, 47.0), "swisstopo_map", "swisstopo_image"),
    ("Austria (Graz)", (15.4, 47.0, 15.5, 47.1), "opentopomap", "basemap_at_ortho"),
    ("France (Lyon)", (4.8, 45.7, 4.9, 45.8), "opentopomap", "ign_ortho"),
    ("Colorado", (-105.6, 39.4, -105.4, 39.6), "usgs_topo", "usgs_imagery"),
    ("Alaska", (-150.0, 61.0, -149.8, 61.2), "usgs_topo", "usgs_imagery"),
    ("Japan", (139.6, 35.6, 139.8, 35.8), "opentopomap", "esri_imagery"),
    ("New Zealand", (168.5, -45.1, 168.7, -44.9), "opentopomap", "esri_imagery"),
]:
    check(f"topo {name}", sources.best_source(sources.TOPO, bbox).key, topo)
    check(f"imagery {name}", sources.best_source(sources.IMAGERY, bbox).key, imagery)

check("no bbox → world", sources.best_source(sources.IMAGERY, None).key, "esri_imagery")
check("WMS CRS BW zone 32", sources.get("lgl_bw_dop20").request_crs(32, "N"), "EPSG:25832")
check("WMS CRS BY zone 33", sources.get("bayern_dop40").request_crs(33, "N"), "EPSG:25833")
check("WMS CRS IGN zone 31", sources.get("ign_ortho").request_crs(31, "N"), "EPSG:32631")
check("WMS CRS CH fallback", sources.get("swisstopo_image").request_crs(31, "N"), "EPSG:2056")
check("Esri zmax", "zmax=17" in sources.layer_uri(sources.get("esri_imagery")), True)

# --- MGRS 100 km squares: documented reference values ----------------------------
# movable-type.co.uk (latlong-utm-mgrs + geodesy docs), Washington Monument, Wikipedia (Honolulu 4QFJ)
for label, zone, e, n, expected in [
    ("31N 303760/5787415 (odd zone)", 31, 303760, 5787415, "CT"),
    ("31N 448251/5411932 (odd zone)", 31, 448251, 5411932, "DQ"),
    ("Washington 18N 323487/4306483 (even zone)", 18, 323487, 4306483, "UJ"),
    ("Honolulu 4N ~612000/2368000 (even zone)", 4, 612000, 2368000, "FJ"),
]:
    check(f"MGRS square {label}", crs.mgrs_square(zone, e, n), expected)
row_letters = "ABCDEFGHJKLMNPQRSTUV"
for zone in (31, 32):  # rows continue across the equator: 10 000 km = 5 full letter cycles
    south = crs.mgrs_square(zone, 500000, 9_999_999)[1]
    north = crs.mgrs_square(zone, 500000, 0)[1]
    check(f"MGRS rows continue across the equator (zone {zone})",
          (row_letters.index(south) + 1) % 20, row_letters.index(north))
check("MGRS first row letter odd zone = A", crs.mgrs_square(31, 500000, 0)[1], "A")
check("MGRS first row letter even zone = F", crs.mgrs_square(32, 500000, 0)[1], "F")
check("MGRS easting out of range → None", crs.mgrs_square(32, 95000, 5300000), None)
check("MGRS band Stuttgart", crs.mgrs_band(48.78), "U")
check("MGRS band Tromsø (69.6° N)", crs.mgrs_band(69.65), "W")
check("MGRS band 80° N", crs.mgrs_band(80.0), "X")
check("MGRS band 79° S", crs.mgrs_band(-79.0), "C")
check("MGRS band Sydney", crs.mgrs_band(-33.86), "H")
check("MGRS label", crs.mgrs_label(18, -77.03, 38.89, ["UJ", "UJ"]), "18S UJ")
check("MGRS label two squares", crs.mgrs_label(32, 9.0, 48.0, ["MU", "NU", "MU", "NU"]), "32U MU / NU")
check("MGRS omitted in Norway exception (Bergen)", crs.mgrs_label(32, 5.3, 60.4, ["KN"]), None)
check("MGRS omitted on Svalbard", crs.mgrs_label(33, 15.6, 78.2, ["WA"]), None)
check("MGRS kept outside exceptions (Tromsø)", crs.mgrs_label(34, 18.95, 69.65, ["DC"]), "34W DC")

# --- World Magnetic Model: every official NOAA test value ---------------------------
worst = 0.0
rows = 0
for line in (Path(__file__).parent / "data" / "WMM2025_TestValues.txt").read_text().splitlines():
    if line.startswith("#") or not line.strip():
        continue
    v = [float(x) for x in line.split()]
    year, height, lat, lon, expected = v[:5]
    worst = max(worst, abs(wmm.declination(lat, lon, year, height) - expected))
    rows += 1
check(f"WMM2025 declination, {rows} NOAA test rows within 0.01°", worst < 0.01, True)
check("WMM outside 2025–2030 → None", wmm.declination(48.0, 9.0, 2031.0), None)

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
