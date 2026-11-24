"""Constants and persisted settings for the Kartenwerk plugin."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from qgis.core import QgsSettings

# ---------------------------------------------------------------------------
# Page / scale geometry (all layout units are millimetres)
# ---------------------------------------------------------------------------
MARGIN_MM = 6.0
DPI = 300

# Available map scales (denominators). A smaller denominator zooms in further,
# making text larger and more readable on paper at the cost of less area/sheet.
SCALES = {
    10000: "1:10 000",
    12500: "1:12 500",
    15000: "1:15 000",
    20000: "1:20 000",
    25000: "1:25 000",
}
DEFAULT_SCALE = 15000

# Available landscape page formats (width × height in millimetres).
PAGE_FORMATS = {
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
    "A5": (210.0, 148.0),
}
DEFAULT_PAGE_FORMAT = "A5"

# Fixed grid origin in EPSG:25832 so sheet ids stay stable between runs.
GRID_ORIGIN_E = 0.0
GRID_ORIGIN_N = 0.0


@dataclass(frozen=True)
class PageGeometry:
    name: str
    page_w: float
    page_h: float
    margin: float
    map_x: float
    map_y: float
    map_w: float
    map_h: float
    cell_w: float
    cell_h: float

    @property
    def right(self) -> float:
        return self.page_w - self.margin

    @property
    def bottom(self) -> float:
        return self.page_h - self.margin


def page_format() -> str:
    """Return the configured page format key (validated)."""
    name = str(get_setting("page_format") or DEFAULT_PAGE_FORMAT).upper()
    return name if name in PAGE_FORMATS else DEFAULT_PAGE_FORMAT


def scale() -> int:
    """Return the configured map scale denominator (validated)."""
    value = int(get_setting("scale") or DEFAULT_SCALE)
    return value if value in SCALES else DEFAULT_SCALE


def format_scale(value: int | None = None) -> str:
    """Human-readable scale label, e.g. '1:15 000'."""
    s = value if value is not None else scale()
    return f"1:{s:,}".replace(",", " ")


def page_geometry(name: str | None = None, scale_denom: int | None = None) -> PageGeometry:
    """Compute map-frame / cell geometry for a page format and scale."""
    name = name or page_format()
    if name not in PAGE_FORMATS:
        name = DEFAULT_PAGE_FORMAT
    s = scale_denom if scale_denom is not None else scale()
    page_w, page_h = PAGE_FORMATS[name]
    margin = MARGIN_MM
    map_w = page_w - 2.0 * margin
    map_h = page_h - 2.0 * margin
    return PageGeometry(
        name=name,
        page_w=page_w,
        page_h=page_h,
        margin=margin,
        map_x=margin,
        map_y=margin,
        map_w=map_w,
        map_h=map_h,
        cell_w=map_w / 1000.0 * s,
        cell_h=map_h / 1000.0 * s,
    )

# ---------------------------------------------------------------------------
# Project / data locations
# ---------------------------------------------------------------------------
PROJECT_DIR = Path.home() / "Documents" / "QGIS_Kartenwerk"
PROJECT_PATH = PROJECT_DIR / "kartenwerk.qgz"
GPKG_PATH = PROJECT_DIR / "kartenwerk.gpkg"
CACHE_DIR = PROJECT_DIR / "cache"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "ausgabe"

AOI_LAYER = "arbeitsgebiet"
GRID_LAYER = "raster"
AOI_GROUP = "Arbeitsgebiet"
GRID_GROUP = "Raster"
BASEMAP_GROUP = "Basiskarten"
OPTIONAL_GROUP = "Optional (ForstBW / Schutzgebiete)"

# ---------------------------------------------------------------------------
# Web services (verified 2026-11)
# ---------------------------------------------------------------------------
OPENTOPO_URL = "https://tile.opentopomap.org/{z}/{x}/{y}.png"
OPENTOPO_NAME = "OpenTopoMap"

TOPO_WMS = "https://sgx.geodatenzentrum.de/wms_topplus_open"
DOP_RGB_WMS = "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C"
DOP_CIR_WMS = "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_CIR"
DGM_WMS = "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DGM_025"

TOPO_LAYERS = {
    "web": ("web", "TopPlusOpen"),
    "web_light": ("web_light", "TopPlusOpen Light"),
    "web_grau": ("web_grau", "TopPlusOpen Graustufen"),
}

IMAGERY_LAYERS = {
    "IMAGES_DOP_20_RGB": ("IMAGES_DOP_20_RGB", "DOP20 Farbe"),
    "IMAGES_DOP_20_CIR": ("IMAGES_DOP_20_CIR", "DOP20 Infrarot (CIR)"),
}

DGM_LAYER = ("DGM025_Hoehen_Graustufen", "DGM 25 Graustufen")

WMS_CRS = "EPSG:25832"
PROJECT_CRS = "EPSG:25832"

# ---------------------------------------------------------------------------
# Persisted settings
# ---------------------------------------------------------------------------
_SETTINGS_PREFIX = "kartenwerk"


def _defaults() -> dict:
    return {
        "output_dir": str(DEFAULT_OUTPUT_DIR),
        "topo_style": "web",
        "imagery": "IMAGES_DOP_20_RGB",
        "dpi": DPI,
        "auto_number": True,
        "open_folder": True,
        "page_format": DEFAULT_PAGE_FORMAT,
        "scale": DEFAULT_SCALE,
        "front_layer": "",
        "back_layer": "",
        "front_format": "PNG",
        "back_format": "PNG",
        "jpeg_quality": 92,
        "front_source": "UTM 32N · © OpenTopoMap (CC-BY-SA) · © OpenStreetMap-Mitwirkende",
        "back_source": "UTM 32N · © LGL BW · ATKIS DOP20",
    }


def get_setting(key: str):
    defaults = _defaults()
    value = QgsSettings().value(f"{_SETTINGS_PREFIX}/{key}", defaults.get(key))
    return value


def set_setting(key: str, value) -> None:
    QgsSettings().setValue(f"{_SETTINGS_PREFIX}/{key}", value)


def output_dir() -> Path:
    path = Path(os.path.expanduser(str(get_setting("output_dir"))))
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def wms_uri(base_url: str, layer: str, fmt: str = "image/png") -> str:
    return (
        f"url={base_url}&layers={layer}&styles=&format={fmt}"
        f"&crs={WMS_CRS}"
    )


def format_coord(value: float) -> str:
    """German thousands separators, no decimals."""
    return f"{int(round(value)):,}".replace(",", ".")
