"""Catalog of basemap sources and automatic selection by area.

Two roles exist: ``topo`` (front side) and ``imagery`` (back side). Each role
has a worldwide default; national services with better data are used
automatically when the working area lies inside their coverage. Coverage is a
coarse lon/lat bounding box, so after rendering the exporter falls back to the
worldwide source when a national service leaves the sheet largely empty.

All URLs verified 2026-11.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import crs as crs_mod

TOPO = "topo"
IMAGERY = "imagery"

WORLD = ((-180.0, -90.0, 180.0, 90.0),)


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    role: str
    kind: str  # "xyz" or "wms"
    url: str
    attribution: str
    coverage: tuple = WORLD  # tuple of (lon_min, lat_min, lon_max, lat_max)
    layer: str = ""  # WMS layer name
    fmt: str = "image/png"  # WMS format; PNG keeps "no data" transparent
    crs_list: tuple = ("EPSG:3857",)  # WMS: supported CRSs, first = fallback
    zmax: int = 19  # XYZ: highest zoom level with real tiles everywhere
    tile_pixel_ratio: int = 1  # XYZ: 2 renders map text larger (easier to read)
    # True when the service returns opaque white (not transparent) outside its coverage.
    white_is_nodata: bool = False
    note: str = field(default="", compare=False)

    @property
    def is_world(self) -> bool:
        return self.coverage == WORLD

    def covers(self, bbox) -> bool:
        lon_min, lat_min, lon_max, lat_max = bbox
        return any(
            c[0] <= lon_min and c[1] <= lat_min and lon_max <= c[2] and lat_max <= c[3]
            for c in self.coverage
        )

    def coverage_area(self) -> float:
        return sum((c[2] - c[0]) * (c[3] - c[1]) for c in self.coverage)

    def request_crs(self, zone: int | None, hemisphere: str | None) -> str:
        """Pick the WMS CRS closest to the target UTM zone (avoids resampling)."""
        if zone is not None and hemisphere is not None:
            wgs = crs_mod.zone_authid(zone, hemisphere)
            etrs = f"EPSG:{25800 + zone}" if hemisphere == "N" else None
            for candidate in (wgs, etrs):
                if candidate and candidate in self.crs_list:
                    return candidate
        return self.crs_list[0]


_UTM_EU = tuple(f"EPSG:{25800 + z}" for z in range(28, 39))

US = (
    (-125.0, 24.4, -66.9, 49.4),  # contiguous states
    (-170.0, 51.0, -129.0, 71.5),  # Alaska
    (-160.5, 18.8, -154.6, 22.4),  # Hawaii
)

CATALOG: tuple[Source, ...] = (
    # ---------------------------------------------------------------- topo
    Source(
        key="opentopomap", name="OpenTopoMap", role=TOPO, kind="xyz",
        url="https://tile.opentopomap.org/{z}/{x}/{y}.png",
        attribution="© OpenTopoMap (CC-BY-SA) · © OpenStreetMap contributors",
        zmax=17, tile_pixel_ratio=2,
        note="Worldwide. Fair-use tile policy: avoid very large batch exports.",
    ),
    Source(
        key="topplusopen", name="TopPlusOpen (DE)", role=TOPO, kind="wms",
        url="https://sgx.geodatenzentrum.de/wms_topplus_open", layer="web",
        attribution="© BKG (www.bkg.bund.de), CC BY 4.0",
        coverage=((5.85, 47.25, 15.05, 55.1),),
        crs_list=("EPSG:3857", "EPSG:25832", "EPSG:25833"),
    ),
    Source(
        key="swisstopo_map", name="swisstopo national map (CH)", role=TOPO, kind="wms",
        url="https://wms.geo.admin.ch/", layer="ch.swisstopo.pixelkarte-farbe",
        attribution="© swisstopo",
        coverage=((5.95, 45.8, 10.5, 47.8),),
        crs_list=("EPSG:2056", "EPSG:32632", "EPSG:3857"),
    ),
    Source(
        key="usgs_topo", name="USGS Topo (US)", role=TOPO, kind="xyz",
        url="https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        attribution="USGS The National Map (public domain)",
        coverage=US, zmax=16,
    ),
    # ------------------------------------------------------------- imagery
    Source(
        key="esri_imagery", name="Esri World Imagery", role=IMAGERY, kind="xyz",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attribution="Imagery: Esri, Maxar, Earthstar Geographics, GIS User Community",
        # z18+ returns "Map data not yet available" placeholders in many rural areas.
        zmax=17,
        note="Worldwide. Check Esri's terms of use; attribution required.",
    ),
    Source(
        key="lgl_bw_dop20", name="DOP20 Baden-Württemberg", role=IMAGERY, kind="wms",
        url="https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C",
        layer="IMAGES_DOP_20_RGB",
        attribution="© LGL BW (www.lgl-bw.de), dl-de/by-2-0",
        coverage=((7.5, 47.5, 10.5, 49.8),),
        crs_list=("EPSG:25832", "EPSG:3857"),
    ),
    Source(
        key="bayern_dop40", name="DOP40 Bayern", role=IMAGERY, kind="wms",
        url="https://geoservices.bayern.de/od/wms/dop/v1/dop40", layer="by_dop40c",
        attribution="© Bayerische Vermessungsverwaltung, CC BY 4.0",
        coverage=((8.95, 47.25, 13.85, 50.6),),
        crs_list=("EPSG:25832", "EPSG:25833", "EPSG:3857"),
    ),
    Source(
        key="pdok_luchtfoto", name="PDOK Luchtfoto (NL)", role=IMAGERY, kind="wms",
        url="https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0", layer="Actueel_orthoHR",
        attribution="© Beeldmateriaal Nederland / PDOK, CC BY 4.0",
        coverage=((3.3, 50.75, 7.25, 53.6),),
        white_is_nodata=True,  # always answers with JPEG, white outside NL
        crs_list=("EPSG:3857", "EPSG:25831", "EPSG:25832"),
    ),
    Source(
        key="ign_ortho", name="IGN Orthophotos (FR)", role=IMAGERY, kind="wms",
        url="https://data.geopf.fr/wms-r/wms", layer="ORTHOIMAGERY.ORTHOPHOTOS",
        attribution="© IGN – Géoplateforme, Licence Ouverte 2.0",
        coverage=((-5.2, 41.3, 9.6, 51.1),),
        white_is_nodata=True,  # white outside France (e.g. Luxembourg, Turin)
        crs_list=("EPSG:2154", "EPSG:32630", "EPSG:32631", "EPSG:32632", "EPSG:3857"),
    ),
    Source(
        key="swisstopo_image", name="SWISSIMAGE (CH)", role=IMAGERY, kind="wms",
        url="https://wms.geo.admin.ch/", layer="ch.swisstopo.swissimage",
        attribution="© swisstopo",
        coverage=((5.95, 45.8, 10.5, 47.8),),
        crs_list=("EPSG:2056", "EPSG:32632", "EPSG:3857"),
    ),
    Source(
        key="basemap_at_ortho", name="basemap.at Orthofoto (AT)", role=IMAGERY, kind="xyz",
        url="https://maps.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
        attribution="© basemap.at, CC BY 4.0",
        coverage=((9.5, 46.35, 17.2, 49.05),), zmax=19,
        white_is_nodata=True,  # white tiles outside Austria (e.g. Bolzano)
    ),
    Source(
        key="usgs_imagery", name="USGS Imagery (US)", role=IMAGERY, kind="xyz",
        url="https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        attribution="USGS The National Map (public domain)",
        coverage=US, zmax=16,
    ),
)

_BY_KEY = {s.key: s for s in CATALOG}


def get(key: str) -> Source | None:
    return _BY_KEY.get(key)


def for_role(role: str) -> list[Source]:
    return [s for s in CATALOG if s.role == role]


def world_default(role: str) -> Source:
    return next(s for s in CATALOG if s.role == role and s.is_world)


def best_source(role: str, bbox) -> Source:
    """Most specific source of ``role`` whose coverage contains ``bbox``."""
    if bbox is None:
        return world_default(role)
    candidates = [s for s in for_role(role) if not s.is_world and s.covers(bbox)]
    if not candidates:
        return world_default(role)
    return min(candidates, key=lambda s: s.coverage_area())


# ---------------------------------------------------------------------------
# QGIS layers
# ---------------------------------------------------------------------------
_PROP = "kartenwerk_source"


def layer_uri(source: Source, request_crs: str | None = None) -> str:
    if source.kind == "xyz":
        return (
            f"type=xyz&url={source.url}&zmin=0&zmax={source.zmax}"
            f"&tilePixelRatio={source.tile_pixel_ratio}"
        )
    return (
        f"crs={request_crs or source.crs_list[0]}&format={source.fmt}"
        f"&layers={source.layer}&styles=&url={source.url}"
    )


def create_layer(source: Source, request_crs: str | None = None):
    from qgis.core import QgsRasterLayer

    layer = QgsRasterLayer(layer_uri(source, request_crs), source.name, "wms")
    if not layer.isValid():
        raise RuntimeError(
            f"Basemap '{source.name}' could not be loaded. Check the internet connection.\n"
            f"URL: {source.url}"
        )
    return layer


def ensure_layer(project, source: Source, zone: int | None = None, hemisphere: str | None = None,
                 visible: bool = False):
    """Return the project layer for ``source``, adding it to the Basemaps group if missing."""
    from . import services

    request_crs = source.request_crs(zone, hemisphere) if source.kind == "wms" else "EPSG:3857"
    tag = f"{source.key}@{request_crs}"
    for layer in project.mapLayers().values():
        if layer.customProperty(_PROP) == tag:
            return layer
    layer = create_layer(source, request_crs)
    layer.setCustomProperty(_PROP, tag)
    project.addMapLayer(layer, False)
    node = services.group(project, _basemap_group()).addLayer(layer)
    if node is not None:
        node.setItemVisibilityChecked(visible)
    return layer


def _basemap_group() -> str:
    from . import config

    return config.BASEMAP_GROUP


@dataclass
class Resolved:
    """A basemap layer chosen for one side, plus how to fall back."""

    layer: object
    attribution: str
    source: Source | None  # None for a user-chosen project layer
    fallback: Source | None  # world source to use if the sheet comes out empty


def resolve(project, role: str, setting: str, bbox, zone=None, hemisphere=None) -> Resolved:
    """Turn a basemap setting ('auto', catalog key, 'layer:<name>') into a layer."""
    from . import services

    setting = (setting or "auto").strip()
    if setting.startswith("layer:"):
        name = setting[len("layer:"):]
        layer = services.get_layer(project, name)
        if layer is None:
            raise RuntimeError(f"Basemap layer '{name}' is not in the project.")
        return Resolved(layer, "", None, None)

    # An explicit catalog choice is honoured even for the other side (e.g. after "Swap").
    source = get(setting) if setting != "auto" else None
    if source is None:
        source = best_source(role, bbox)
    fallback = None if source.is_world else world_default(source.role)
    layer = ensure_layer(project, source, zone, hemisphere)
    return Resolved(layer, source.attribution, source, fallback)
