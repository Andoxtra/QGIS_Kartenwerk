"""Constants and persisted settings for the Kartenwerk plugin."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from qgis.core import QgsApplication, QgsProject, QgsSettings

# ---------------------------------------------------------------------------
# Page / scale geometry (all layout units are millimetres)
# ---------------------------------------------------------------------------
# Printer margin: most printers cannot print the outer ~4–5 mm of the paper, so
# nothing is placed closer than this to the paper edge (setting "printer_margin").
PRINTER_MARGINS = {5: "5 mm – safe for most printers", 4: "4 mm",
                   3: "3 mm – printers with small margins, print shops"}
DEFAULT_PRINTER_MARGIN = 5
# Border lanes between the printer margin and the map frame on A5 (larger formats
# scale them, see TEXT_SCALE). Left and right must be equal: the back is mirrored
# about the vertical axis and has to register with the front.
LANE_SIDE_MM = 7.0  # northing km labels / side neighbours
LANE_TOP_MM = 6.0  # title row + easting km labels
LANE_BOTTOM_MM = 7.0  # scale bar row + attribution row (eastings are labelled at the top)
DPI = 300
GRID_SPACING_M = 1000.0

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

# Border text and the border lanes grow with the page so they stay in proportion.
TEXT_SCALE = {"A5": 1.0, "A4": 1.15, "A3": 1.35, "A2": 1.6}

# "Two sheets per page" print file: each format on the next larger paper (portrait).
TWO_UP_PAPER = {"A5": "A4", "A4": "A3", "A3": "A2"}

# Overlap between neighbouring sheets (percent of the map frame), so features
# near a sheet edge also appear on the next sheet.
OVERLAPS = {0: "None", 5: "5 %", 10: "10 %"}
DEFAULT_OVERLAP = 5

# Fixed grid origin (E 0 / N 0 of the UTM zone) so sheet ids stay stable.
GRID_ORIGIN_E = 0.0
GRID_ORIGIN_N = 0.0


@dataclass(frozen=True)
class PageGeometry:
    name: str
    scale: int
    overlap: int  # percent
    page_w: float
    page_h: float
    margin_side: float
    margin_top: float
    margin_bottom: float
    map_w: float
    map_h: float
    cell_w: float  # ground metres per grid cell (sheet step)
    cell_h: float
    pad_w: float  # ground metres shown beyond the cell on the left and on the right
    pad_h: float  # … above and below
    text_scale: float  # border text/lane size relative to A5
    safe: float  # printer margin in mm

    @property
    def map_x(self) -> float:
        return self.margin_side

    @property
    def map_y(self) -> float:
        return self.margin_top

    @property
    def right(self) -> float:
        return self.page_w - self.margin_side

    @property
    def bottom(self) -> float:
        return self.page_h - self.margin_bottom

    def mm_per_m(self) -> float:
        return 1000.0 / self.scale

    def signature(self) -> str:
        """Identifies everything that changes the layout geometry."""
        return (f"{self.name}|{self.scale}|{self.overlap}|{self.margin_side}|"
                f"{self.margin_top}|{self.margin_bottom}|{self.safe}|{self.text_scale}")


def page_format() -> str:
    """Return the configured page format key (validated)."""
    name = str(get_setting("page_format") or DEFAULT_PAGE_FORMAT).upper()
    return name if name in PAGE_FORMATS else DEFAULT_PAGE_FORMAT


def scale() -> int:
    """Return the configured map scale denominator (validated)."""
    try:
        value = int(get_setting("scale") or DEFAULT_SCALE)
    except (TypeError, ValueError):
        value = DEFAULT_SCALE
    return value if value in SCALES else DEFAULT_SCALE


def safe_mm() -> float:
    """Printer margin in mm (validated): nothing is printed closer to the paper edge."""
    try:
        value = int(get_setting("printer_margin"))
    except (TypeError, ValueError):
        value = DEFAULT_PRINTER_MARGIN
    return float(value if value in PRINTER_MARGINS else DEFAULT_PRINTER_MARGIN)


def overlap() -> int:
    """Return the configured sheet overlap in percent (validated)."""
    try:
        value = int(get_setting("overlap"))
    except (TypeError, ValueError):
        value = DEFAULT_OVERLAP
    return value if value in OVERLAPS else DEFAULT_OVERLAP


def format_scale(value: int | None = None) -> str:
    """Human-readable scale label, e.g. '1:15 000'."""
    s = value if value is not None else scale()
    return f"1:{s:,}".replace(",", " ")


def page_geometry(name: str | None = None, scale_denom: int | None = None,
                  overlap_percent: int | None = None) -> PageGeometry:
    """Compute map-frame / cell geometry for a page format, scale and overlap."""
    name = name or page_format()
    if name not in PAGE_FORMATS:
        name = DEFAULT_PAGE_FORMAT
    s = scale_denom if scale_denom is not None else scale()
    ov = overlap_percent if overlap_percent is not None else overlap()
    page_w, page_h = PAGE_FORMATS[name]
    k = TEXT_SCALE[name]
    safe = safe_mm()
    side = safe + LANE_SIDE_MM * k
    top = safe + LANE_TOP_MM * k
    bottom = safe + LANE_BOTTOM_MM * k
    map_w = page_w - 2.0 * side
    map_h = page_h - top - bottom
    frame_w = map_w / 1000.0 * s  # ground metres covered by the map frame
    frame_h = map_h / 1000.0 * s
    cell_w = frame_w * (1.0 - ov / 100.0)
    cell_h = frame_h * (1.0 - ov / 100.0)
    return PageGeometry(
        name=name, scale=s, overlap=ov,
        page_w=page_w, page_h=page_h,
        margin_side=side, margin_top=top, margin_bottom=bottom,
        map_w=map_w, map_h=map_h,
        cell_w=cell_w, cell_h=cell_h,
        pad_w=(frame_w - cell_w) / 2.0, pad_h=(frame_h - cell_h) / 2.0,
        text_scale=k,
        safe=safe,
    )


# ---------------------------------------------------------------------------
# Layer names
# ---------------------------------------------------------------------------
AOI_LAYER = "work_area"
GRID_LAYER = "sheet_grid"
# Names used by version 1 (German), still recognised in existing projects.
LEGACY_AOI_LAYERS = ("arbeitsgebiet",)
LEGACY_GRID_LAYERS = ("raster",)
AOI_GROUP = "Work area"
GRID_GROUP = "Sheet grid"
BASEMAP_GROUP = "Basemaps"

GPKG_NAME = "kartenwerk.gpkg"


# ---------------------------------------------------------------------------
# Locations (evaluated at call time: they follow the open project)
# ---------------------------------------------------------------------------
def data_dir() -> Path:
    """Folder of the saved QGIS project, else <QGIS profile>/kartenwerk."""
    home = QgsProject.instance().homePath()
    if home:
        path = Path(home)
    else:
        path = Path(QgsApplication.qgisSettingsDirPath()) / "kartenwerk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def gpkg_path() -> Path:
    return data_dir() / GPKG_NAME


def default_output_dir() -> Path:
    return data_dir() / "output"


_cache_override: Path | None = None


def set_cache_dir(path: Path | None) -> None:
    """Override the cache folder (used by the headless tests)."""
    global _cache_override
    _cache_override = Path(path) if path else None


def cache_dir() -> Path:
    path = _cache_override or Path(tempfile.gettempdir()) / "kartenwerk_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Persisted settings
# ---------------------------------------------------------------------------
_SETTINGS_PREFIX = "kartenwerk"
AUTO = "auto"

# Keys from version 1. They held BW-specific values (e.g. attribution text with
# a hard-coded "UTM 32N"), so they are removed instead of migrated.
_LEGACY_KEYS = (
    "front_layer", "back_layer", "front_source", "back_source", "topo_style", "imagery",
)


def _defaults() -> dict:
    return {
        "output_dir": "",  # empty → <data dir>/output
        "dpi": DPI,
        "auto_number": True,
        "open_folder": True,
        "page_format": DEFAULT_PAGE_FORMAT,
        "scale": DEFAULT_SCALE,
        "overlap": DEFAULT_OVERLAP,
        "printer_margin": DEFAULT_PRINTER_MARGIN,
        "two_up": False,
        "render_cache": True,
        # Duplex offset of the back side in mm (see duplex_offset()).
        "duplex_dx": 0.0,
        "duplex_dy": 0.0,
        "duplex_2up_dx": 0.0,
        "duplex_2up_dy": 0.0,
        # "auto", a catalog key from sources.py, or "layer:<name>"
        "front_basemap": AUTO,
        "back_basemap": AUTO,
        "front_format": "PNG",
        "back_format": "PNG",
        "jpeg_quality": 92,
        # optional override; empty → the source's own attribution
        "front_attribution": "",
        "back_attribution": "",
    }


def get_setting(key: str):
    return QgsSettings().value(f"{_SETTINGS_PREFIX}/{key}", _defaults().get(key))


def set_setting(key: str, value) -> None:
    QgsSettings().setValue(f"{_SETTINGS_PREFIX}/{key}", value)


def to_bool(value) -> bool:
    """QSettings (INI backend) returns booleans as 'true'/'false' strings."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def get_bool(key: str) -> bool:
    return to_bool(get_setting(key))


def get_int(key: str) -> int:
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return int(_defaults()[key])


# Version 1 saved this fixed output folder whenever the settings dialog was confirmed.
_LEGACY_OUTPUT_DIR = Path.home() / "Documents" / "QGIS_Kartenwerk" / "ausgabe"


MAX_DUPLEX_OFFSET_MM = 5.0


def duplex_offset(two_up: bool = False) -> tuple[float, float]:
    """(dx, dy) in mm by which all back-page content is moved, to compensate the printer.

    Convention: the user prints the duplex test page (always without offset), looks
    at the BACK against a window and reads where the front's cross appears on the
    back's rulers (+ = right / down as seen on the back). Those two readings are
    stored as they are and REPLACE any earlier offset; the back content then moves
    by exactly (dx, dy). Separate values for the landscape sheet PDFs and for the
    portrait two-per-page file, because drivers rotate the two differently.
    """
    prefix = "duplex_2up_" if two_up else "duplex_"
    values = []
    for axis in ("dx", "dy"):
        try:
            value = float(get_setting(prefix + axis) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        values.append(max(-MAX_DUPLEX_OFFSET_MM, min(MAX_DUPLEX_OFFSET_MM, value)))
    return values[0], values[1]


def remove_legacy_settings() -> None:
    settings = QgsSettings()
    for key in _LEGACY_KEYS:
        settings.remove(f"{_SETTINGS_PREFIX}/{key}")
    saved = str(settings.value(f"{_SETTINGS_PREFIX}/output_dir", "") or "")
    if saved and Path(os.path.expanduser(saved)) == _LEGACY_OUTPUT_DIR:
        settings.remove(f"{_SETTINGS_PREFIX}/output_dir")  # follow the project folder instead


def output_dir() -> Path:
    value = str(get_setting("output_dir") or "").strip()
    path = Path(os.path.expanduser(value)) if value else default_output_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_settings() -> dict:
    """Snapshot of all settings used by the exporter."""
    return {
        "output_dir": str(output_dir()),
        "page_format": page_format(),
        "scale": scale(),
        "overlap": overlap(),
        "dpi": get_int("dpi"),
        "auto_number": get_bool("auto_number"),
        "two_up": get_bool("two_up") and page_format() in TWO_UP_PAPER,
        "render_cache": get_bool("render_cache"),
        "open_folder": get_bool("open_folder"),
        "front_basemap": str(get_setting("front_basemap") or AUTO),
        "back_basemap": str(get_setting("back_basemap") or AUTO),
        "front_format": str(get_setting("front_format") or "PNG").upper(),
        "back_format": str(get_setting("back_format") or "PNG").upper(),
        "jpeg_quality": get_int("jpeg_quality"),
        "front_attribution": str(get_setting("front_attribution") or ""),
        "back_attribution": str(get_setting("back_attribution") or ""),
    }


# ---------------------------------------------------------------------------
# Project-specific settings (stored in the .qgz, not in the QGIS profile)
# ---------------------------------------------------------------------------
def project_overlays(project=None) -> list[str]:
    """Layer ids drawn on top of the front map (e.g. a GPX route)."""
    project = project or QgsProject.instance()
    values, ok = project.readListEntry(_SETTINGS_PREFIX, "overlays")
    return [v for v in values if v] if ok else []


def project_overlays_on_back(project=None) -> bool:
    project = project or QgsProject.instance()
    value, ok = project.readBoolEntry(_SETTINGS_PREFIX, "overlays_back", False)
    return bool(value) if ok else False


def set_project_overlays(layer_ids: list[str], on_back: bool, project=None) -> None:
    project = project or QgsProject.instance()
    project.writeEntry(_SETTINGS_PREFIX, "overlays", list(layer_ids))
    project.writeEntry(_SETTINGS_PREFIX, "overlays_back", bool(on_back))


def format_coord(value: float) -> str:
    """Metres with a space as thousands separator (SI style), e.g. '5 415 000'."""
    return f"{int(round(value)):,}".replace(",", " ")
