#!/usr/bin/env python3
"""Create the Kartenwerk QGIS project, GeoPackage, themes and layouts."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "plugin"))

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsRectangle,
    QgsReferencedRectangle,
)

app = QgsApplication([], False)
app.initQgis()

from kartenwerk import config, grid, layout, services


def main() -> int:
    config.PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config.DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem(config.PROJECT_CRS))
    project.setTitle("Kartenwerk – A5 1:25 000")
    project.viewSettings().setDefaultViewExtent(
        QgsReferencedRectangle(
            QgsRectangle(380000, 5180000, 620000, 5520000),
            QgsCoordinateReferenceSystem(config.PROJECT_CRS),
        )
    )

    print("Lade Basiskarten …")
    basemaps = services.ensure_basemaps(project)
    print("  ", ", ".join(f"{role}: {layer.name()}" for role, layer in basemaps.items()))

    print("Erzeuge Arbeitsgebiet/Raster …")
    aoi, grid_layer = grid.ensure_layers(project, config.GPKG_PATH)
    print("  ", aoi.name(), grid_layer.name())

    print("Erzeuge Kartenansichten …")
    services.ensure_map_themes(project, basemaps)

    print("Erzeuge Layouts …")
    front, back = layout.ensure_layouts(project, force=True)
    print("  ", front.name(), back.name())

    ok = project.write(str(config.PROJECT_PATH))
    if not ok:
        print("FEHLER: Projekt konnte nicht gespeichert werden.")
        return 1

    print(f"Projekt gespeichert: {config.PROJECT_PATH}")
    print(f"GeoPackage:          {config.GPKG_PATH}")
    print(f"Ausgabeordner:       {config.DEFAULT_OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        app.exitQgis()
