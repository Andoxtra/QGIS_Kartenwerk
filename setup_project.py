#!/usr/bin/env python3
"""Create the Kartenwerk QGIS project (next to this script) with basemaps, themes and layout.

Existing work-area polygons in kartenwerk.gpkg are kept (and migrated from the
version-1 'arbeitsgebiet' table if needed).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "plugin"))

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsRectangle,
    QgsReferencedRectangle,
)

app = QgsApplication([], False)
app.initQgis()

from kartenwerk import config, grid, layout, services  # noqa: E402

PROJECT_PATH = ROOT / "kartenwerk.qgz"


def main() -> int:
    project = QgsProject.instance()
    project.clear()
    project.setFileName(str(PROJECT_PATH))  # data_dir() follows the project folder
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    project.setTitle("Kartenwerk")
    project.viewSettings().setDefaultViewExtent(
        QgsReferencedRectangle(
            QgsRectangle(-180, -60, 180, 75), QgsCoordinateReferenceSystem("EPSG:4326")
        )
    )

    print("Adding basemaps …")
    basemaps = services.ensure_basemaps(project)
    print("  ", ", ".join(f"{role}: {layer.name()}" for role, layer in basemaps.items()))

    print("Adding work area …")
    aoi = grid.ensure_aoi_layer(project)
    print(f"   {aoi.name()} ({aoi.featureCount()} polygons)")

    services.ensure_map_themes(project, basemaps)
    print("Creating layout …")
    print("  ", layout.ensure_layout(project, force=True).name())

    if not project.write(str(PROJECT_PATH)):
        print("ERROR: project could not be saved.")
        return 1
    print(f"Project saved: {PROJECT_PATH}")
    print(f"GeoPackage:    {config.gpkg_path()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        app.exitQgis()
