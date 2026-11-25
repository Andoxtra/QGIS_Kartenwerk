"""Map rendering, UTM grid lines and horizontal mirroring."""
from __future__ import annotations

import math
import time
from pathlib import Path

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLineSymbol,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsPointXY,
    QgsRectangle,
    QgsSimpleLineSymbolLayer,
    QgsVectorLayer,
)

from . import config


def grid_lines_layer(extent: QgsRectangle, crs: QgsCoordinateReferenceSystem) -> QgsVectorLayer:
    """Memory layer with 1 km UTM grid lines for the given extent."""
    layer = QgsVectorLayer(f"LineString?crs={crs.authid()}", "utm_grid", "memory")
    spacing = config.GRID_SPACING_M
    features = []

    e = math.ceil(extent.xMinimum() / spacing) * spacing
    while e <= extent.xMaximum():
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(e, extent.yMinimum()), QgsPointXY(e, extent.yMaximum())]))
        features.append(feat)
        e += spacing

    n = math.ceil(extent.yMinimum() / spacing) * spacing
    while n <= extent.yMaximum():
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(extent.xMinimum(), n), QgsPointXY(extent.xMaximum(), n)]))
        features.append(feat)
        n += spacing

    layer.dataProvider().addFeatures(features)

    symbol = QgsLineSymbol.createSimple({"line_color": "255,255,255,110", "line_width": "0.55"})
    dark = QgsSimpleLineSymbolLayer()
    dark.setColor(QColor(30, 30, 30, 190))
    dark.setWidth(0.18)
    symbol.appendSymbolLayer(dark)
    layer.renderer().setSymbol(symbol)
    layer.setOpacity(0.9)
    return layer


def sheet_edge_layer(extent: QgsRectangle, cell: QgsRectangle,
                     crs: QgsCoordinateReferenceSystem) -> QgsVectorLayer:
    """Dashed lines where the neighbouring sheets begin (inside the overlap strip)."""
    layer = QgsVectorLayer(f"LineString?crs={crs.authid()}", "sheet_edges", "memory")
    lines = [
        [(cell.xMinimum(), extent.yMinimum()), (cell.xMinimum(), extent.yMaximum())],
        [(cell.xMaximum(), extent.yMinimum()), (cell.xMaximum(), extent.yMaximum())],
        [(extent.xMinimum(), cell.yMinimum()), (extent.xMaximum(), cell.yMinimum())],
        [(extent.xMinimum(), cell.yMaximum()), (extent.xMaximum(), cell.yMaximum())],
    ]
    features = []
    for line in lines:
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in line]))
        features.append(feat)
    layer.dataProvider().addFeatures(features)
    symbol = QgsLineSymbol.createSimple({
        "line_color": "200,0,90,200", "line_width": "0.25", "line_style": "dash",
    })
    layer.renderer().setSymbol(symbol)
    return layer


def missing_fraction(image: QImage, white_is_missing: bool = False) -> float:
    """Share of transparent pixels, i.e. areas where the basemap delivered no data.

    The map is rendered on a transparent background, so missing XYZ tiles and
    areas outside a WMS's coverage stay transparent, while white map paper
    (e.g. on topographic maps) is opaque and not counted. ``white_is_missing``
    also counts near-white pixels, for services that fill gaps with white.
    """
    step = max(8, image.width() // 400)
    total = 0
    missing = 0
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            total += 1
            color = image.pixelColor(x, y)
            if color.alpha() < 16 or (
                white_is_missing and min(color.red(), color.green(), color.blue()) >= 250
            ):
                missing += 1
    return missing / total if total else 1.0


def _flatten(image: QImage) -> QImage:
    """Composite onto white (PDF/JPEG output has no transparency)."""
    out = QImage(image.size(), QImage.Format_RGB32)
    out.fill(QColor("white"))
    painter = QPainter(out)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


def save_image(image: QImage, path: Path, jpeg_quality: int = 92, mirror: bool = False) -> Path:
    """Optionally mirror (duplex back side), encode and write a rendered map.

    Pure Qt image work without any QGIS objects, so it is safe to run in a
    worker thread; PyQt releases the GIL while Qt encodes, so several images
    really are compressed on separate CPU cores.
    """
    path = Path(path)
    if mirror:
        image = image.mirrored(True, False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        ok = image.save(str(path), "JPEG", int(jpeg_quality))
    else:
        ok = image.save(str(path), "PNG")
    if not ok:
        raise RuntimeError(f"Map image could not be saved: {path}")
    return path


def render_image(
    extent: QgsRectangle,
    layers: list,
    crs: QgsCoordinateReferenceSystem,
    dpi: int = config.DPI,
    width_px: int | None = None,
    height_px: int | None = None,
    retries: int = 0,
    retry_delay: float = 2.0,
    retry_threshold: float = 0.002,
    white_is_missing: bool = False,
    cell: QgsRectangle | None = None,
) -> tuple[QImage, float]:
    """Render basemap + grid lines in memory. Returns (image, missing-data fraction).

    Runs on the calling (main) thread and downloads one map at a time.
    ``retries`` re-renders when tiles are missing (tile servers that throttle).
    ``cell`` (the sheet's own grid cell, smaller than ``extent`` when sheets
    overlap) adds dashed lines where the neighbouring sheets begin.
    """
    geom = config.page_geometry()
    width_px = int(width_px or round(geom.map_w / 25.4 * dpi))
    height_px = int(height_px or round(geom.map_h / 25.4 * dpi))

    basemap_settings = QgsMapSettings()
    basemap_settings.setLayers(list(layers))
    basemap_settings.setDestinationCrs(crs)
    basemap_settings.setExtent(extent)
    basemap_settings.setOutputSize(QSize(width_px, height_px))
    basemap_settings.setOutputDpi(dpi)
    basemap_settings.setBackgroundColor(QColor(0, 0, 0, 0))
    basemap_settings.setFlag(QgsMapSettings.Antialiasing, True)
    basemap_settings.setFlag(QgsMapSettings.UseAdvancedEffects, True)

    image = None
    missing = 1.0
    for attempt in range(retries + 1):
        job = QgsMapRendererSequentialJob(basemap_settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if image.isNull():
            raise RuntimeError("Map image could not be rendered.")
        missing = missing_fraction(image, white_is_missing)
        if missing <= retry_threshold:
            break
        if attempt < retries:
            time.sleep(retry_delay)

    image = _flatten(image)

    # Grid lines are drawn in a second pass so they never count as map data.
    grid_settings = QgsMapSettings(basemap_settings)
    overlay = [grid_lines_layer(extent, crs)]
    if cell is not None and (cell.width() < extent.width() - 0.5):
        overlay.insert(0, sheet_edge_layer(extent, cell, crs))
    grid_settings.setLayers(overlay)
    job = QgsMapRendererSequentialJob(grid_settings)
    job.start()
    job.waitForFinished()
    painter = QPainter(image)
    painter.drawImage(0, 0, job.renderedImage())
    painter.end()
    return image, missing
