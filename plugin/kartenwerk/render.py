"""Map rendering, UTM grid lines and horizontal mirroring."""
from __future__ import annotations

import math
import time
from pathlib import Path

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLineSymbol,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSimpleLineSymbolLayer,
    QgsVectorLayer,
)

from . import config, services


def grid_lines_layer(extent: QgsRectangle) -> QgsVectorLayer:
    """Memory layer with 1 km UTM grid lines for the given extent."""
    layer = QgsVectorLayer(f"LineString?crs={config.PROJECT_CRS}", "utm_gitter", "memory")
    provider = layer.dataProvider()

    features = []
    e_start = math.ceil(extent.xMinimum() / 1000.0) * 1000.0
    e = e_start
    while e <= extent.xMaximum():
        geom = QgsGeometry.fromPolylineXY(
            [QgsPointXY(e, extent.yMinimum()), QgsPointXY(e, extent.yMaximum())]
        )
        feat = QgsFeature()
        feat.setGeometry(geom)
        features.append(feat)
        e += 1000.0

    n_start = math.ceil(extent.yMinimum() / 1000.0) * 1000.0
    n = n_start
    while n <= extent.yMaximum():
        geom = QgsGeometry.fromPolylineXY(
            [QgsPointXY(extent.xMinimum(), n), QgsPointXY(extent.xMaximum(), n)]
        )
        feat = QgsFeature()
        feat.setGeometry(geom)
        features.append(feat)
        n += 1000.0

    provider.addFeatures(features)

    symbol = QgsLineSymbol.createSimple(
        {"line_color": "255,255,255,110", "line_width": "0.55"}
    )
    dark = QgsSimpleLineSymbolLayer()
    dark.setColor(QColor(30, 30, 30, 190))
    dark.setWidth(0.18)
    symbol.appendSymbolLayer(dark)
    layer.renderer().setSymbol(symbol)
    layer.setOpacity(0.9)
    return layer


def _blank_fraction(image: QImage, step: int = 8) -> float:
    """Share of (near-)white pixels, used to detect missing XYZ tiles."""
    total = 0
    blank = 0
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            color = image.pixelColor(x, y)
            total += 1
            if color.red() >= 250 and color.green() >= 250 and color.blue() >= 250:
                blank += 1
    return blank / total if total else 1.0


def render_map(
    extent: QgsRectangle,
    layers: list,
    out_path: Path,
    dpi: int = config.DPI,
    width_px: int | None = None,
    height_px: int | None = None,
    jpeg_quality: int = 92,
    retry_on_blank: bool = False,
    max_retries: int = 2,
    retry_delay: float = 2.0,
    blank_threshold: float = 0.25,
) -> Path:
    """Render basemap + grid lines to a PNG/JPEG and return the path."""
    geom = config.page_geometry()
    width_px = int(width_px or round(geom.map_w / 25.4 * dpi))
    height_px = int(height_px or round(geom.map_h / 25.4 * dpi))

    grid = grid_lines_layer(extent)
    settings = QgsMapSettings()
    settings.setLayers(list(layers) + [grid])
    settings.setDestinationCrs(QgsCoordinateReferenceSystem(config.PROJECT_CRS))
    settings.setExtent(extent)
    settings.setOutputSize(QSize(width_px, height_px))
    settings.setOutputDpi(dpi)
    settings.setBackgroundColor(QColor("white"))
    settings.setFlag(QgsMapSettings.Antialiasing, True)
    settings.setFlag(QgsMapSettings.UseAdvancedEffects, True)

    image = None
    for attempt in range(max_retries + 1):
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if image.isNull():
            raise RuntimeError(f"Kartenbild konnte nicht gerendert werden: {out_path}")
        if not retry_on_blank or _blank_fraction(image) <= blank_threshold:
            break
        if attempt < max_retries:
            time.sleep(retry_delay)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = out_path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        ok = image.save(str(out_path), "JPEG", jpeg_quality)
    else:
        ok = image.save(str(out_path), "PNG")
    if not ok:
        raise RuntimeError(f"Kartenbild konnte nicht gespeichert werden: {out_path}")
    return out_path


def flip_image(path: Path, jpeg_quality: int = 92) -> Path:
    """Mirror a rendered map horizontally (for the duplex back side)."""
    image = QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"Bild konnte nicht gelesen werden: {path}")
    mirrored = image.mirrored(True, False)
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        ok = mirrored.save(str(path), "JPEG", jpeg_quality)
    else:
        ok = mirrored.save(str(path), "PNG")
    if not ok:
        raise RuntimeError(f"Gespiegeltes Bild konnte nicht gespeichert werden: {path}")
    return path


def topo_layer(project: QgsProject, style: str | None = None):
    """Return the front topographic layer.

    Without a style (or with an unknown style) the default is OpenTopoMap.
    A known style key returns the matching TopPlusOpen WMS layer instead.
    """
    if style in config.TOPO_LAYERS:
        layer_id, name = config.TOPO_LAYERS[style]
        layer = services.get_layer(project, name)
        if layer is None:
            layer = services.create_wms_layer(config.TOPO_WMS, layer_id, name, "image/png")
            project.addMapLayer(layer, False)
            services._group(project, config.BASEMAP_GROUP).addLayer(layer)
        return layer

    layer = services.get_layer(project, config.OPENTOPO_NAME)
    if layer is None:
        layer = services.create_xyz_layer(config.OPENTOPO_URL, config.OPENTOPO_NAME)
        project.addMapLayer(layer, False)
        services._group(project, config.BASEMAP_GROUP).addLayer(layer)
    return layer


def imagery_layer(project: QgsProject, imagery: str = "IMAGES_DOP_20_RGB"):
    imagery = imagery if imagery in config.IMAGERY_LAYERS else "IMAGES_DOP_20_RGB"
    layer_id, name = config.IMAGERY_LAYERS[imagery]
    layer = services.get_layer(project, name)
    if layer is None:
        url = config.DOP_RGB_WMS if imagery == "IMAGES_DOP_20_RGB" else config.DOP_CIR_WMS
        layer = services.create_wms_layer(url, layer_id, name, "image/jpeg")
        project.addMapLayer(layer, False)
        services._group(project, config.BASEMAP_GROUP).addLayer(layer)
    return layer
