"""Working area and sheet grid handling."""
from __future__ import annotations

import math
import os
from pathlib import Path

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
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
    QgsWkbTypes,
)

from . import config


# ---------------------------------------------------------------------------
# Layer creation / styling
# ---------------------------------------------------------------------------
def get_layer(project: QgsProject, name: str):
    layers = project.mapLayersByName(name)
    return layers[0] if layers else None


def _group(project: QgsProject, name: str):
    root = project.layerTreeRoot()
    node = root.findGroup(name)
    return node if node is not None else root.addGroup(name)


def _write_layer(path: Path, layer_name: str, fields: list[QgsField]) -> QgsVectorLayer:
    mem = QgsVectorLayer(f"Polygon?crs={config.PROJECT_CRS}", layer_name, "memory")
    provider = mem.dataProvider()
    provider.addAttributes(fields)
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
        raise RuntimeError(f"GeoPackage-Layer '{layer_name}' konnte nicht erstellt werden: {msg}")
    return QgsVectorLayer(f"{path}|layername={layer_name}", layer_name, "ogr")


def ensure_layers(project: QgsProject, gpkg_path: Path | None = None):
    """Return (aoi_layer, grid_layer), creating both if necessary."""
    gpkg_path = Path(gpkg_path or config.GPKG_PATH)
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)

    aoi = get_layer(project, config.AOI_LAYER)
    if aoi is None:
        aoi = _write_layer(
            gpkg_path,
            config.AOI_LAYER,
            [
                QgsField("name", QVariant.String, "string", 80),
                QgsField("bemerkung", QVariant.String, "string", 200),
            ],
        )
        project.addMapLayer(aoi, False)
        _group(project, config.AOI_GROUP).addLayer(aoi)

    grid = get_layer(project, config.GRID_LAYER)
    if grid is None:
        grid = _write_layer(
            gpkg_path,
            config.GRID_LAYER,
            [
                QgsField("sheet_id", QVariant.String, "string", 32),
                QgsField("row", QVariant.Int),
                QgsField("col", QVariant.Int),
                QgsField("e_min", QVariant.Double),
                QgsField("e_max", QVariant.Double),
                QgsField("n_min", QVariant.Double),
                QgsField("n_max", QVariant.Double),
                QgsField("center_e", QVariant.Double),
                QgsField("center_n", QVariant.Double),
                QgsField("sheet_no", QVariant.Int),
                QgsField("sheet_name", QVariant.String, "string", 80),
                QgsField("in_aoi", QVariant.Int),
            ],
        )
        project.addMapLayer(grid, False)
        _group(project, config.GRID_GROUP).addLayer(grid)

    style_aoi(aoi)
    style_grid(grid)
    return aoi, grid


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
    fmt.setFont(QFont("DejaVu Sans", 8))
    fmt.setSize(8)
    fmt.setColor(QColor(0, 45, 110))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setColor(QColor("white"))
    buf.setSize(1.0)
    fmt.setBuffer(buf)

    pal = QgsPalLayerSettings()
    pal.fieldName = 'coalesce("sheet_no", "sheet_id")'
    pal.isExpression = True
    pal.placement = QgsPalLayerSettings.Horizontal
    pal.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------
def _aoi_geometry(aoi_layer: QgsVectorLayer) -> QgsGeometry:
    if aoi_layer is None:
        raise ValueError("Arbeitsgebiet-Layer nicht gefunden.")
    if aoi_layer.isEditable() and not aoi_layer.commitChanges():
        raise ValueError(
            "Das Arbeitsgebiet konnte nicht gespeichert werden: "
            + "; ".join(aoi_layer.commitErrors())
        )

    geoms = []
    for feature in aoi_layer.getFeatures():
        if not feature.hasGeometry():
            continue
        geom = feature.geometry()
        if geom.isEmpty():
            continue
        if not geom.isGeosValid():
            geom = geom.makeValid()
        if geom.isEmpty():
            continue
        geoms.append(geom)

    if not geoms:
        raise ValueError(
            "Das Arbeitsgebiet ist leer. Bitte zuerst ein gültiges Polygon zeichnen."
        )

    aoi = QgsGeometry.unaryUnion(geoms)
    if aoi.isNull() or aoi.isEmpty():
        raise ValueError(
            "Das Arbeitsgebiet konnte nicht zusammengeführt werden. "
            "Bitte ungültige oder leere Polygone löschen und erneut zeichnen."
        )
    if not aoi.isGeosValid():
        aoi = aoi.makeValid()
    if aoi.isEmpty():
        raise ValueError(
            "Das Arbeitsgebiet ist ungültig. Bitte das Polygon neu zeichnen."
        )
    return aoi


def generate_grid(
    project: QgsProject,
    aoi_layer: QgsVectorLayer,
    grid_layer: QgsVectorLayer,
    cell_w: float | None = None,
    cell_h: float | None = None,
) -> int:
    geom = config.page_geometry()
    cell_w = float(cell_w) if cell_w is not None else geom.cell_w
    cell_h = float(cell_h) if cell_h is not None else geom.cell_h
    aoi = _aoi_geometry(aoi_layer)
    buffered = aoi.buffer(max(cell_w, cell_h), 8)
    bbox = buffered.boundingBox()

    col_min = math.floor((bbox.xMinimum() - config.GRID_ORIGIN_E) / cell_w)
    col_max = math.floor((bbox.xMaximum() - config.GRID_ORIGIN_E) / cell_w)
    row_min = math.floor((bbox.yMinimum() - config.GRID_ORIGIN_N) / cell_h)
    row_max = math.floor((bbox.yMaximum() - config.GRID_ORIGIN_N) / cell_h)

    field_names = [f.name() for f in grid_layer.fields()]
    features = []
    for row in range(row_min, row_max + 1):
        for col in range(col_min, col_max + 1):
            e_min = config.GRID_ORIGIN_E + col * cell_w
            n_min = config.GRID_ORIGIN_N + row * cell_h
            e_max = e_min + cell_w
            n_max = n_min + cell_h
            geom = QgsGeometry.fromRect(QgsRectangle(e_min, n_min, e_max, n_max))
            if not geom.intersects(buffered):
                continue
            in_aoi = 1 if geom.intersects(aoi) else 0
            feat = QgsFeature(grid_layer.fields())
            feat.setGeometry(geom)
            values = {
                "sheet_id": f"R{row:04d}C{col:04d}",
                "row": row,
                "col": col,
                "e_min": e_min,
                "e_max": e_max,
                "n_min": n_min,
                "n_max": n_max,
                "center_e": (e_min + e_max) / 2.0,
                "center_n": (n_min + n_max) / 2.0,
                "sheet_no": None,
                "sheet_name": None,
                "in_aoi": in_aoi,
            }
            feat.setAttributes([values.get(name) for name in field_names])
            features.append(feat)

    if not features:
        raise RuntimeError(
            "Keine Rasterzellen erzeugt. Das Arbeitsgebiet ist möglicherweise "
            "ungültig oder liegt außerhalb des Rasters."
        )

    if grid_layer.isEditable():
        grid_layer.rollBack()
    grid_layer.startEditing()
    ids = [f.id() for f in grid_layer.getFeatures()]
    if ids:
        grid_layer.dataProvider().deleteFeatures(ids)
    grid_layer.dataProvider().addFeatures(features)
    if not grid_layer.commitChanges():
        raise RuntimeError("Raster konnte nicht gespeichert werden: " + grid_layer.commitErrors()[0])
    grid_layer.updateExtents()
    grid_layer.triggerRepaint()
    return len(features)


def clear_grid(grid_layer: QgsVectorLayer) -> None:
    if grid_layer.isEditable():
        grid_layer.rollBack()
    grid_layer.startEditing()
    ids = [f.id() for f in grid_layer.getFeatures()]
    if ids:
        grid_layer.dataProvider().deleteFeatures(ids)
    grid_layer.commitChanges()
    grid_layer.updateExtents()
    grid_layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------
def selected_sheets(grid_layer: QgsVectorLayer) -> list[dict]:
    if grid_layer is None:
        return []
    sheets = []
    for feat in grid_layer.selectedFeatures():
        sheets.append(
            {
                "fid": feat.id(),
                "sheet_id": feat["sheet_id"],
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
        )
    return sheets


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


def neighbour_map(grid_layer: QgsVectorLayer) -> dict:
    return _neighbour_map(grid_layer)


def _neighbour_map(grid_layer: QgsVectorLayer) -> dict:
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
    lookup = lookup if lookup is not None else _neighbour_map(grid_layer)
    row, col = int(sheet["row"]), int(sheet["col"])

    def make(dr: int, dc: int, edge_key: str, edge_value: float):
        neighbour = lookup.get((row + dr, col + dc))
        if neighbour is None:
            return None
        label = neighbour["sheet_no"] if neighbour["sheet_no"] not in (None, "") else neighbour["sheet_id"]
        return {
            "label": str(label),
            "sheet_id": neighbour["sheet_id"],
            "sheet_no": neighbour["sheet_no"],
            "sheet_name": neighbour["sheet_name"],
            "edge_value": edge_value,
        }

    return {
        "north": make(1, 0, "n", sheet["n_max"]),
        "south": make(-1, 0, "n", sheet["n_min"]),
        "east": make(0, 1, "e", sheet["e_max"]),
        "west": make(0, -1, "e", sheet["e_min"]),
    }
