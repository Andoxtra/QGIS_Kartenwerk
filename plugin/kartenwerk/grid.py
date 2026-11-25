"""Working area and sheet grid handling."""
from __future__ import annotations

import math
from pathlib import Path

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsProject,
    QgsRectangle,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)

from . import config, crs as crs_mod
from .services import find_layer, group

AOI_CRS = "EPSG:4326"  # neutral; the grid itself is always in the UTM zone

_GRID_FIELDS = (
    ("sheet_id", QVariant.String, "string", 32),
    ("utm_zone", QVariant.Int, "integer", 0),
    ("hemisphere", QVariant.String, "string", 1),
    ("row", QVariant.Int, "integer", 0),
    ("col", QVariant.Int, "integer", 0),
    ("e_min", QVariant.Double, "double", 0),
    ("e_max", QVariant.Double, "double", 0),
    ("n_min", QVariant.Double, "double", 0),
    ("n_max", QVariant.Double, "double", 0),
    ("center_e", QVariant.Double, "double", 0),
    ("center_n", QVariant.Double, "double", 0),
    ("sheet_no", QVariant.Int, "integer", 0),
    ("sheet_name", QVariant.String, "string", 80),
    ("in_aoi", QVariant.Int, "integer", 0),
)


# ---------------------------------------------------------------------------
# Layer creation / styling
# ---------------------------------------------------------------------------
def _fields(spec) -> list[QgsField]:
    return [QgsField(name, vtype, tname, length) for name, vtype, tname, length in spec]


def _gpkg_tables(path: Path) -> set[str]:
    if not path.exists():
        return set()
    import sqlite3

    try:
        with sqlite3.connect(str(path)) as conn:
            return {row[0] for row in conn.execute("SELECT table_name FROM gpkg_contents")}
    except sqlite3.Error:
        return set()


def _write_layer(path: Path, layer_name: str, crs: str, fields: list[QgsField]) -> QgsVectorLayer:
    mem = QgsVectorLayer(f"Polygon?crs={crs}", layer_name, "memory")
    mem.dataProvider().addAttributes(fields)
    mem.updateFields()

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = (
        QgsVectorFileWriter.CreateOrOverwriteLayer
        if path.exists()
        else QgsVectorFileWriter.CreateOrOverwriteFile
    )
    err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem, str(path), QgsCoordinateTransformContext(), options
    )
    if err != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"GeoPackage layer '{layer_name}' could not be created: {msg}")
    return _open(path, layer_name)


def _open(path: Path, layer_name: str) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{path}|layername={layer_name}", layer_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"GeoPackage layer '{layer_name}' could not be opened: {path}")
    return layer


def _migrate_legacy_aoi(path: Path, tables: set[str], target: QgsVectorLayer) -> None:
    """Copy polygons from the version-1 'arbeitsgebiet' table into the new layer."""
    for legacy in config.LEGACY_AOI_LAYERS:
        if legacy not in tables:
            continue
        old = _open(path, legacy)
        xform = QgsCoordinateTransform(old.crs(), target.crs(), QgsProject.instance())
        names = [f.name() for f in old.fields()]
        feats = []
        for src in old.getFeatures():
            if not src.hasGeometry():
                continue
            geom = QgsGeometry(src.geometry())
            geom.transform(xform)
            feat = QgsFeature(target.fields())
            feat.setGeometry(geom)
            feat["name"] = src["name"] if "name" in names else None
            feat["note"] = src["bemerkung"] if "bemerkung" in names else None
            feats.append(feat)
        if feats:
            target.dataProvider().addFeatures(feats)
            target.updateExtents()
        return


def ensure_aoi_layer(project: QgsProject, gpkg_path: Path | None = None) -> QgsVectorLayer:
    """Return the working-area layer, creating (or migrating) it if necessary."""
    aoi = get_aoi_layer(project)
    if aoi is None:
        path = Path(gpkg_path or config.gpkg_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        tables = _gpkg_tables(path)
        if config.AOI_LAYER in tables:
            aoi = _open(path, config.AOI_LAYER)  # never overwrite drawn areas
        else:
            aoi = _write_layer(
                path, config.AOI_LAYER, AOI_CRS,
                _fields((("name", QVariant.String, "string", 80),
                         ("note", QVariant.String, "string", 200))),
            )
            _migrate_legacy_aoi(path, tables, aoi)
        project.addMapLayer(aoi, False)
        group(project, config.AOI_GROUP).addLayer(aoi)
    style_aoi(aoi)
    return aoi


def get_aoi_layer(project: QgsProject):
    return find_layer(project, config.AOI_LAYER, config.LEGACY_AOI_LAYERS)


def get_grid_layer(project: QgsProject):
    return find_layer(project, config.GRID_LAYER, config.LEGACY_GRID_LAYERS)


def _grid_layer_matches(layer: QgsVectorLayer, authid: str) -> bool:
    names = {f.name() for f in layer.fields()}
    return (
        layer.name() == config.GRID_LAYER
        and layer.crs().authid() == authid
        and all(spec[0] in names for spec in _GRID_FIELDS)
    )


def ensure_grid_layer(project: QgsProject, zone: int, hemisphere: str,
                      gpkg_path: Path | None = None) -> QgsVectorLayer:
    """Return a grid layer in the given UTM zone, recreating it when the zone changed."""
    authid = crs_mod.zone_authid(zone, hemisphere)
    existing = get_grid_layer(project)
    if existing is not None and _grid_layer_matches(existing, authid):
        style_grid(existing)
        return existing

    # Remove the old layer(s) from the project first so the GeoPackage file is
    # released before the table is overwritten (required on Windows).
    for name in (config.GRID_LAYER, *config.LEGACY_GRID_LAYERS):
        for layer in project.mapLayersByName(name):
            project.removeMapLayer(layer.id())

    path = Path(gpkg_path or config.gpkg_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    grid = _write_layer(path, config.GRID_LAYER, authid, _fields(_GRID_FIELDS))
    project.addMapLayer(grid, False)
    group(project, config.GRID_GROUP).addLayer(grid)
    style_grid(grid)
    return grid


def ensure_layers(project: QgsProject, gpkg_path: Path | None = None):
    """Return (aoi_layer, grid_layer or None). The grid is created by generate_grid."""
    return ensure_aoi_layer(project, gpkg_path), get_grid_layer(project)


def style_aoi(layer: QgsVectorLayer) -> None:
    symbol = QgsFillSymbol.createSimple(
        {
            "color": "255,0,0,0",
            "outline_color": "210,0,0,255",
            "outline_width": "0.6",
            "outline_style": "dash",
        }
    )
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()


def style_grid(layer: QgsVectorLayer) -> None:
    symbol = QgsFillSymbol.createSimple(
        {
            "color": "0,110,255,25",
            "outline_color": "0,70,160,210",
            "outline_width": "0.35",
        }
    )
    layer.renderer().setSymbol(symbol)

    fmt = QgsTextFormat()
    font = QFont()
    font.setPointSize(8)
    fmt.setFont(font)
    fmt.setSize(8)
    fmt.setColor(QColor(0, 45, 110))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setColor(QColor("white"))
    buf.setSize(1.0)
    fmt.setBuffer(buf)

    pal = QgsPalLayerSettings()
    pal.fieldName = 'coalesce(to_string("sheet_no"), "sheet_id") || coalesce(\'\\n\' || "sheet_name", \'\')'
    pal.isExpression = True
    pal.placement = QgsPalLayerSettings.Horizontal
    pal.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
# UTM zone of the working area
# ---------------------------------------------------------------------------
def grid_zone(grid_layer) -> tuple[int, str] | None:
    """(zone, hemisphere) of a grid layer, or None for a version-1 / non-UTM grid."""
    if grid_layer is None:
        return None
    return crs_mod.zone_from_authid(grid_layer.crs().authid())


def aoi_zone(aoi_layer: QgsVectorLayer) -> tuple[int, str, str | None]:
    """Return (zone, hemisphere, warning or None) for the working area."""
    bbox = crs_mod.layer_lonlat_bbox(aoi_layer)
    if bbox is None:
        raise ValueError("The work area is empty. Please draw a polygon first.")
    lon_min, lat_min, lon_max, lat_max = bbox
    for lat in (lat_min, lat_max):
        if not (crs_mod.MIN_LAT <= lat <= crs_mod.MAX_LAT):
            raise ValueError(
                "The work area reaches beyond the UTM range (80° S to 84° N). "
                "Polar areas are not supported."
            )
    zone, hemisphere = crs_mod.utm_zone_for((lon_min + lon_max) / 2.0, (lat_min + lat_max) / 2.0)
    warning = None
    outside = crs_mod.degrees_outside_zone(zone, lon_min, lon_max)
    if outside > crs_mod.ZONE_OVERLAP_WARN_DEG:
        warning = (
            f"The work area extends {outside:.1f}° beyond UTM zone "
            f"{crs_mod.zone_label(zone, hemisphere)}; sheets far from the zone are slightly distorted."
        )
    return zone, hemisphere, warning


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------
def _aoi_geometry(aoi_layer: QgsVectorLayer, target_crs: QgsCoordinateReferenceSystem) -> QgsGeometry:
    if aoi_layer is None:
        raise ValueError("Work area layer not found.")
    if aoi_layer.isEditable() and not aoi_layer.commitChanges():
        raise ValueError(
            "The work area could not be saved: " + "; ".join(aoi_layer.commitErrors())
        )

    xform = QgsCoordinateTransform(aoi_layer.crs(), target_crs, QgsProject.instance())
    geoms = []
    for feature in aoi_layer.getFeatures():
        if not feature.hasGeometry():
            continue
        geom = QgsGeometry(feature.geometry())
        if geom.isEmpty():
            continue
        geom.transform(xform)
        if not geom.isGeosValid():
            geom = geom.makeValid()
        if geom.isEmpty():
            continue
        geoms.append(geom)

    if not geoms:
        raise ValueError("The work area is empty. Please draw a valid polygon first.")

    aoi = QgsGeometry.unaryUnion(geoms)
    if aoi.isNull() or aoi.isEmpty():
        raise ValueError(
            "The work area could not be merged. "
            "Please delete invalid or empty polygons and draw again."
        )
    if not aoi.isGeosValid():
        aoi = aoi.makeValid()
    if aoi.isEmpty():
        raise ValueError("The work area is invalid. Please redraw the polygon.")
    return aoi


def generate_grid(
    project: QgsProject,
    aoi_layer: QgsVectorLayer,
    cell_w: float | None = None,
    cell_h: float | None = None,
    gpkg_path: Path | None = None,
    set_project_crs: bool = True,
) -> tuple[QgsVectorLayer, int, str | None]:
    """Build the sheet grid in the work area's UTM zone.

    Returns (grid_layer, cell count, warning or None).
    """
    geom = config.page_geometry()
    cell_w = float(cell_w) if cell_w is not None else geom.cell_w
    cell_h = float(cell_h) if cell_h is not None else geom.cell_h

    if aoi_layer.isEditable() and not aoi_layer.commitChanges():
        raise ValueError("The work area could not be saved: " + "; ".join(aoi_layer.commitErrors()))
    zone, hemisphere, warning = aoi_zone(aoi_layer)
    target_crs = crs_mod.utm_crs(zone, hemisphere)
    aoi = _aoi_geometry(aoi_layer, target_crs)
    buffered = aoi.buffer(max(cell_w, cell_h), 8)
    bbox = buffered.boundingBox()

    col_min = math.floor((bbox.xMinimum() - config.GRID_ORIGIN_E) / cell_w)
    col_max = math.floor((bbox.xMaximum() - config.GRID_ORIGIN_E) / cell_w)
    row_min = math.floor((bbox.yMinimum() - config.GRID_ORIGIN_N) / cell_h)
    row_max = math.floor((bbox.yMaximum() - config.GRID_ORIGIN_N) / cell_h)

    grid_layer = ensure_grid_layer(project, zone, hemisphere, gpkg_path)
    previous = _numbers_and_names(grid_layer, cell_w, cell_h)
    field_names = [f.name() for f in grid_layer.fields()]
    features = []
    for row in range(row_min, row_max + 1):
        for col in range(col_min, col_max + 1):
            e_min = config.GRID_ORIGIN_E + col * cell_w
            n_min = config.GRID_ORIGIN_N + row * cell_h
            e_max = e_min + cell_w
            n_max = n_min + cell_h
            cell = QgsGeometry.fromRect(QgsRectangle(e_min, n_min, e_max, n_max))
            if not cell.intersects(buffered):
                continue
            feat = QgsFeature(grid_layer.fields())
            feat.setGeometry(cell)
            sheet_id = crs_mod.sheet_id(zone, hemisphere, row, col)
            number, name = previous.get(sheet_id, (None, None))
            values = {
                "sheet_id": sheet_id,
                "utm_zone": zone,
                "hemisphere": hemisphere,
                "row": row,
                "col": col,
                "e_min": e_min,
                "e_max": e_max,
                "n_min": n_min,
                "n_max": n_max,
                "center_e": (e_min + e_max) / 2.0,
                "center_n": (n_min + n_max) / 2.0,
                "sheet_no": number,
                "sheet_name": name,
                "in_aoi": 1 if cell.intersects(aoi) else 0,
            }
            feat.setAttributes([values.get(name) for name in field_names])
            features.append(feat)

    if not features:
        raise RuntimeError(
            "No grid cells were created. The work area may be invalid or empty."
        )

    clear_grid(grid_layer)
    grid_layer.dataProvider().addFeatures(features)
    grid_layer.updateExtents()
    grid_layer.triggerRepaint()

    if set_project_crs:
        project.setCrs(target_crs)
    return grid_layer, len(features), warning


def _numbers_and_names(grid_layer: QgsVectorLayer, cell_w: float, cell_h: float) -> dict:
    """{sheet_id: (number, name)} of the current grid, kept when the grid is regenerated.

    Only taken over when the cell size is unchanged: after a change of page format,
    scale or overlap the same sheet id covers different ground.
    """
    result = {}
    for feat in grid_layer.getFeatures():
        try:
            same_size = (abs(float(feat["e_max"]) - float(feat["e_min"]) - cell_w) < 0.5
                         and abs(float(feat["n_max"]) - float(feat["n_min"]) - cell_h) < 0.5)
        except (TypeError, ValueError):
            continue
        number, name = feat["sheet_no"], feat["sheet_name"]
        if same_size and (number not in (None, "") or name not in (None, "")):
            result[str(feat["sheet_id"])] = (
                None if number in (None, "") else int(number),
                None if name in (None, "") else str(name),
            )
    return result


def clear_grid(grid_layer: QgsVectorLayer) -> None:
    if grid_layer.isEditable():
        grid_layer.rollBack()
    ids = [f.id() for f in grid_layer.getFeatures()]
    if ids:
        grid_layer.dataProvider().deleteFeatures(ids)
    grid_layer.updateExtents()
    grid_layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------
def _sheet_dict(feat) -> dict:
    return {
        "fid": feat.id(),
        "sheet_id": feat["sheet_id"],
        "utm_zone": feat["utm_zone"],
        "hemisphere": feat["hemisphere"],
        "sheet_no": feat["sheet_no"],
        "sheet_name": feat["sheet_name"],
        "row": feat["row"],
        "col": feat["col"],
        "e_min": float(feat["e_min"]),
        "e_max": float(feat["e_max"]),
        "n_min": float(feat["n_min"]),
        "n_max": float(feat["n_max"]),
        "center_e": float(feat["center_e"]),
        "center_n": float(feat["center_n"]),
        "in_aoi": feat["in_aoi"],
    }


def selected_sheets(grid_layer: QgsVectorLayer) -> list[dict]:
    if grid_layer is None or grid_zone(grid_layer) is None:
        return []
    return [_sheet_dict(feat) for feat in grid_layer.selectedFeatures()]


def number_selected(grid_layer: QgsVectorLayer) -> int:
    feats = list(grid_layer.selectedFeatures())
    if not feats:
        return 0
    feats.sort(key=lambda f: (-float(f["n_max"]), float(f["e_min"])))
    idx = grid_layer.fields().indexFromName("sheet_no")
    if grid_layer.isEditable():
        grid_layer.rollBack()
    grid_layer.startEditing()
    for number, feat in enumerate(feats, start=1):
        grid_layer.changeAttributeValue(feat.id(), idx, number)
    grid_layer.commitChanges()
    grid_layer.triggerRepaint()
    return len(feats)


def set_sheet_name(grid_layer: QgsVectorLayer, fid: int, name: str) -> None:
    """Set (or clear, with an empty name) the display name of one sheet."""
    name = (name or "").strip()[:80]
    idx = grid_layer.fields().indexFromName("sheet_name")
    if grid_layer.isEditable():
        grid_layer.rollBack()
    grid_layer.startEditing()
    grid_layer.changeAttributeValue(fid, idx, name or None)
    if not grid_layer.commitChanges():
        raise RuntimeError("The sheet name could not be saved: "
                           + "; ".join(grid_layer.commitErrors()))
    grid_layer.triggerRepaint()


def number_missing(grid_layer: QgsVectorLayer) -> int:
    """Number only the selected sheets that have no number yet.

    New numbers continue after the highest number in the grid (north → south,
    west → east), so numbers kept from earlier exports stay unchanged.
    """
    feats = [f for f in grid_layer.selectedFeatures() if f["sheet_no"] in (None, "")]
    if not feats:
        return 0
    used = [int(f["sheet_no"]) for f in grid_layer.getFeatures() if f["sheet_no"] not in (None, "")]
    start = max(used, default=0) + 1
    feats.sort(key=lambda f: (-float(f["n_max"]), float(f["e_min"])))
    idx = grid_layer.fields().indexFromName("sheet_no")
    if grid_layer.isEditable():
        grid_layer.rollBack()
    grid_layer.startEditing()
    for offset, feat in enumerate(feats):
        grid_layer.changeAttributeValue(feat.id(), idx, start + offset)
    grid_layer.commitChanges()
    grid_layer.triggerRepaint()
    return len(feats)


def neighbour_map(grid_layer: QgsVectorLayer) -> dict:
    result = {}
    for feat in grid_layer.getFeatures():
        result[(int(feat["row"]), int(feat["col"]))] = {
            "sheet_id": feat["sheet_id"],
            "sheet_no": feat["sheet_no"],
            "sheet_name": feat["sheet_name"],
        }
    return result


def neighbour_info(grid_layer: QgsVectorLayer, sheet: dict, lookup: dict | None = None) -> dict:
    """Return north/south/east/west neighbour descriptions for a sheet."""
    lookup = lookup if lookup is not None else neighbour_map(grid_layer)
    row, col = int(sheet["row"]), int(sheet["col"])

    def make(dr: int, dc: int, edge_value: float):
        neighbour = lookup.get((row + dr, col + dc))
        if neighbour is None:
            return None
        number = neighbour["sheet_no"]
        label = number if number not in (None, "") else neighbour["sheet_id"]
        return {
            "label": str(label),
            "sheet_id": neighbour["sheet_id"],
            "sheet_no": number,
            "sheet_name": neighbour["sheet_name"],
            "edge_value": edge_value,
        }

    return {
        "north": make(1, 0, sheet["n_max"]),
        "south": make(-1, 0, sheet["n_min"]),
        "east": make(0, 1, sheet["e_max"]),
        "west": make(0, -1, sheet["e_min"]),
    }
