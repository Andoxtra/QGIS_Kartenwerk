#!/usr/bin/env python3
import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "plugin"))

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry,
    QgsProject, QgsRectangle,
)

app = QgsApplication([], False)
app.initQgis()
print("QGIS init", flush=True)

from kartenwerk import config, grid, services, exporter

dev = HERE
gpkg = dev / "test_data.gpkg"
if gpkg.exists():
    gpkg.unlink()
config.CACHE_DIR = dev / "cache"
config.CACHE_DIR.mkdir(exist_ok=True)
config.set_setting("output_dir", str(dev / "out"))
config.set_setting("auto_number", True)
config.set_setting("dpi", 300)
config.set_setting("topo_style", "web")
config.set_setting("imagery", "IMAGES_DOP_20_RGB")
config.set_setting("page_format", "A5")
config.set_setting("scale", 25000)
config.set_setting("front_layer", "")
config.set_setting("back_layer", "")
config.set_setting("front_format", "PNG")
config.set_setting("back_format", "JPEG")
config.set_setting("jpeg_quality", 92)

project = QgsProject.instance()
project.clear()
project.setCrs(QgsCoordinateReferenceSystem(config.PROJECT_CRS))

try:
    aoi, grid_layer = grid.ensure_layers(project, gpkg)
    print("layers", aoi.isValid(), grid_layer.isValid(), flush=True)

    aoi.startEditing()
    feat = QgsFeature(aoi.fields())
    feat.setGeometry(QgsGeometry.fromRect(QgsRectangle(468000, 5415000, 472850, 5415300)))
    feat.setAttributes(["Testgebiet", ""])
    aoi.dataProvider().addFeatures([feat])
    aoi.commitChanges()
    print("aoi features", aoi.featureCount(), flush=True)

    count = grid.generate_grid(project, aoi, grid_layer)
    print("grid cells", count, flush=True)

    grid_layer.selectByExpression('"in_aoi" = 1')
    print("selected", len(grid_layer.selectedFeatures()), flush=True)
    grid.number_selected(grid_layer)
    for s in grid.selected_sheets(grid_layer):
        print("sheet", s["sheet_no"], s["sheet_id"], s["e_min"], s["e_max"], s["n_min"], s["n_max"], flush=True)

    services.ensure_basemaps(project)
    print("basemaps", [l.name() for l in project.mapLayers().values()], flush=True)

    written = exporter.export_sheets(
        project, grid_layer,
        {"auto_number": True, "dpi": 300, "topo_style": "web", "imagery": "IMAGES_DOP_20_RGB"},
        progress=lambda i, n, name: print(f"[{i}/{n}] {name}", flush=True),
        keep_cache=True,
    )
    print("written:", flush=True)
    for p in written:
        print(" ", p, p.stat().st_size, flush=True)
except Exception:
    traceback.print_exc()
finally:
    app.exitQgis()
