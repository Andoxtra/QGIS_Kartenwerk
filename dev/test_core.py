#!/usr/bin/env python3
"""Headless end-to-end test (needs PyQGIS and internet).

Exports a few sheets in several regions of the world and checks zones, sources,
two-page PDFs, the overview orientation and a UTM zone switch.

    QT_QPA_PLATFORM=offscreen python3 dev/test_core.py [region ...]

Regions: bw nl us jp nz fallback lu bz westland
"""
import csv
import os
import shutil
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "plugin"))

from qgis.core import (  # noqa: E402
    QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsProject, QgsRectangle,
)

app = QgsApplication([], False)
app.initQgis()

from kartenwerk import config, crs, exporter, grid, layout, sources  # noqa: E402

OUT = HERE / "out"
failures = []


def check(label, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {label}{(': ' + str(detail)) if detail != '' else ''}", flush=True)
    if not ok:
        failures.append(label)


# (name, lon/lat AOI, expected zone, expected topo key, expected imagery key, sheets to export)
REGIONS = {
    "bw": ((8.20, 48.00, 8.26, 48.05), "32N", "topplusopen", "lgl_bw_dop20", 4),
    "nl": ((5.10, 52.08, 5.14, 52.10), "31N", "opentopomap", "pdok_luchtfoto", 1),
    "us": ((-105.30, 39.98, -105.26, 40.00), "13N", "usgs_topo", "usgs_imagery", 1),
    "jp": ((142.60, 43.60, 142.64, 43.62), "54N", "opentopomap", "esri_imagery", 1),
    "nz": ((168.66, -45.04, 168.70, -45.02), "59S", "opentopomap", "esri_imagery", 1),
    # Cologne lies in PDOK's coarse bbox but outside NL: the back must fall back to Esri.
    "fallback": ((6.94, 50.93, 6.96, 50.94), "32N", "topplusopen", "pdok_luchtfoto", 1),
    # IGN / basemap.at answer with opaque white outside FR / AT: must fall back too.
    "lu": ((6.12, 49.60, 6.14, 49.61), "32N", "topplusopen", "ign_ortho", 1),
    "bz": ((11.34, 46.49, 11.36, 46.50), "32N", "opentopomap", "basemap_at_ortho", 1),
    # Bright greenhouse roofs must not trigger PDOK's white-means-missing fallback.
    "westland": ((4.24, 51.99, 4.26, 52.00), "31N", "opentopomap", "pdok_luchtfoto", 1),
}
EXPECTED_BACK_ATTRIBUTION = {"fallback": "Esri", "lu": "Esri", "bz": "Esri", "westland": "PDOK"}


def set_aoi(aoi, bbox):
    aoi.dataProvider().truncate()
    feat = QgsFeature(aoi.fields())
    feat.setGeometry(QgsGeometry.fromRect(QgsRectangle(*bbox)))
    feat["name"] = "Test"
    aoi.dataProvider().addFeatures([feat])
    aoi.updateExtents()


def unit_checks():
    print("== unit", flush=True)
    check("to_bool('false')", config.to_bool("false") is False)
    check("to_bool('true')", config.to_bool("true") is True)
    check("to_bool(False)", config.to_bool(False) is False)
    sheets = [
        {"row": 10, "col": 5, "sheet_id": "south"},
        {"row": 11, "col": 5, "sheet_id": "north"},
    ]
    cells = layout.overview_cells(sheets)
    top = [c for c in cells if c[1] == 0][0][2]
    check("overview: north row on top", top["sheet_id"] == "north")
    check("format_coord", config.format_coord(5415000) == "5 415 000", config.format_coord(5415000))


def safe_zone_violations(layout_):
    """Items whose box comes closer than config.SAFE_MM to the paper edge."""
    from qgis.core import QgsLayoutItemPage
    pages = layout_.pageCollection()
    bad = []
    for item in layout_.items():
        if isinstance(item, QgsLayoutItemPage) or not hasattr(item, "page"):
            continue
        rect = item.sceneBoundingRect()
        page = pages.page(item.page())
        top = rect.top() - page.pos().y()
        size = page.pageSize()
        s = config.SAFE_MM - 0.01
        if rect.left() < s or rect.right() > size.width() - s or top < s \
                or top + rect.height() > size.height() - s:
            bad.append((item.id(), round(rect.left(), 1), round(top, 1),
                        round(rect.width(), 1), round(rect.height(), 1)))
    return bad


def runs(values, dark):
    """Centres of consecutive runs where dark(v) is true."""
    centres, start = [], None
    for i, v in enumerate(values + [None]):
        if v is not None and dark(v):
            start = i if start is None else start
        elif start is not None:
            centres.append((start + i - 1) / 2.0)
            start = None
    return centres


def layout_checks(fmt, scale_denom):
    """Render a page with only the grid (no basemap) and measure the real scale on paper."""
    from qgis.PyQt.QtCore import QSize
    from qgis.core import QgsLayoutExporter
    from kartenwerk import render
    print(f"== layout {fmt} 1:{scale_denom}", flush=True)
    config.set_setting("page_format", fmt)
    config.set_setting("scale", scale_denom)
    project = QgsProject.instance()
    project.clear()
    geom = config.page_geometry()
    utm = crs.utm_crs(32, "N")
    cell = QgsRectangle(440_000, 5_320_000, 440_000 + geom.cell_w, 5_320_000 + geom.cell_h)
    extent = QgsRectangle(cell.xMinimum() - geom.pad_w, cell.yMinimum() - geom.pad_h,
                          cell.xMaximum() + geom.pad_w, cell.yMaximum() + geom.pad_h)
    sheet = {"sheet_id": "32N-R1234C0567", "utm_zone": 32, "hemisphere": "N", "sheet_no": 7,
             "sheet_name": None, "row": 1234, "col": 567,
             "e_min": cell.xMinimum(), "e_max": cell.xMaximum(),
             "n_min": cell.yMinimum(), "n_max": cell.yMaximum()}
    long_id = {"sheet_id": "32N-R1234C0568", "sheet_no": None, "sheet_name": None, "edge_value": 0}
    neighbour = {"north": {**long_id, "sheet_no": 3}, "south": long_id, "east": long_id, "west": None}
    info = {"extent": extent, "sheets": [sheet], "convergence": 1.23, "grid_magnetic": 2.34,
            "year": 2026, "date": "2026-11-25"}
    lay = layout.ensure_layout(project, force=True)
    image, _ = render.render_image(extent, [], utm, 254, cell=None)
    img_path = config.cache_dir() / f"grid_only_{fmt}_{scale_denom}.png"  # unique: QGIS caches by path
    render.save_image(image, img_path)
    for side in layout.SIDES:
        layout.update_side(lay, side, sheet, img_path, neighbour, "© Test attribution", info)

    bad = safe_zone_violations(lay)
    check(f"{fmt}: all border items inside the {config.SAFE_MM} mm safe zone", not bad, bad[:5])

    page = QgsLayoutExporter(lay).renderPageToImage(0, QSize(), 254)  # 10 px per mm
    px = page.width() / geom.page_w
    row = int((geom.map_y + geom.map_h * 0.37) * px)
    values = [page.pixelColor(x, row).lightness()
              for x in range(int(geom.map_x * px) + 3, int(geom.right * px) - 3)]
    lines = runs(values, lambda v: v < 120)
    gaps = [(b - a) / px for a, b in zip(lines, lines[1:])]
    expected = config.GRID_SPACING_M * geom.mm_per_m()
    check(f"{fmt}: 1 km grid spacing on paper = {expected:.2f} mm",
          gaps and all(abs(g - expected) < 0.25 for g in gaps), [round(g, 2) for g in gaps])

    metres, length = layout.scale_bar_length(geom)
    bar_row = int((geom.bottom + 4.95) * px)
    bar = [page.pixelColor(x, bar_row).lightness()
           for x in range(int((geom.map_x - 1) * px), int((geom.map_x + length + 1) * px))]
    dark = [i for i, v in enumerate(bar) if v < 100]
    measured = (dark[-1] - dark[0]) / px if dark else 0
    check(f"{fmt}: scale bar {metres:g} m = {length:.2f} mm", abs(measured - length) < 0.4,
          round(measured, 2))


def legacy_migration(work: Path):
    print("== legacy migration", flush=True)
    gpkg = work / "legacy.gpkg"
    shutil.copy(ROOT / "kartenwerk.gpkg", gpkg)
    project = QgsProject.instance()
    project.clear()
    aoi = grid.ensure_aoi_layer(project, gpkg)
    check("migrated AOI name", aoi.name() == config.AOI_LAYER, aoi.name())
    check("migrated AOI CRS", aoi.crs().authid() == "EPSG:4326", aoi.crs().authid())
    check("migrated AOI features", aoi.featureCount() == 6, aoi.featureCount())
    grid_layer, count, _ = grid.generate_grid(project, aoi, gpkg_path=gpkg)
    check("legacy AOI → zone 32N", crs.zone_label(*grid.grid_zone(grid_layer)) == "32N")


def run_region(name, work: Path, project, aoi, gpkg):
    bbox, zone_label, topo_key, img_key, n_sheets = REGIONS[name]
    print(f"== {name}", flush=True)
    set_aoi(aoi, bbox)
    grid_layer, count, warning = grid.generate_grid(project, aoi, gpkg_path=gpkg)
    zone = grid.grid_zone(grid_layer)
    check(f"{name}: zone", crs.zone_label(*zone) == zone_label, crs.zone_label(*zone))
    check(f"{name}: one grid layer", len(project.mapLayersByName(config.GRID_LAYER)) == 1)
    check(f"{name}: project CRS", project.crs().authid() == grid_layer.crs().authid())
    check(f"{name}: auto topo", sources.best_source(sources.TOPO, bbox).key == topo_key)
    check(f"{name}: auto imagery", sources.best_source(sources.IMAGERY, bbox).key == img_key)

    grid_layer.selectByExpression('"in_aoi" = 1')
    ids = sorted(grid_layer.selectedFeatureIds())[:n_sheets]
    grid_layer.selectByIds(ids)
    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    written = exporter.export_sheets(
        project, grid_layer, {"output_dir": str(out)},
        progress=lambda i, n, s: print(f"   [{i}/{n}] {s}", flush=True),
        keep_cache=True,
    )
    check(f"{name}: PDFs written", len(written) == len(ids), [p.name for p in written])
    for warning in written.warnings:
        print("   warning:", warning)
    rows = list(csv.DictReader((out / "index.csv").open(encoding="utf-8")))
    check(f"{name}: index.csv zone", all(r["utm_zone"] == zone_label for r in rows))
    check(f"{name}: index.csv epsg", rows and rows[0]["epsg"] == grid_layer.crs().authid())
    check(f"{name}: Overview.pdf", (out / "Overview.pdf").exists())
    if name in EXPECTED_BACK_ATTRIBUTION:
        text = layout.ensure_layout(project).itemById("back_source").text()
        check(f"{name}: back fell back", EXPECTED_BACK_ATTRIBUTION[name] in text, text)
    return grid_layer


def main(selected):
    unit_checks()
    work = HERE / "cache"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    config.set_cache_dir(work)
    legacy_migration(work)
    for fmt, scale_denom in (("A5", 15000), ("A2", 25000), ("A4", 10000)):
        layout_checks(fmt, scale_denom)

    config.set_setting("page_format", "A5")
    config.set_setting("scale", 25000)
    config.set_setting("overlap", 5)
    config.set_setting("dpi", 200)
    config.set_setting("auto_number", True)
    config.set_setting("front_basemap", "auto")
    config.set_setting("back_basemap", "auto")
    config.set_setting("front_format", "PNG")
    config.set_setting("back_format", "JPEG")
    config.set_setting("front_attribution", "")
    config.set_setting("back_attribution", "")

    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    gpkg = work / "test_data.gpkg"
    aoi = grid.ensure_aoi_layer(project, gpkg)
    # Regions run in one session, so every region after the first is a zone switch.
    for name in selected:
        try:
            run_region(name, work, project, aoi, gpkg)
        except Exception:
            traceback.print_exc()
            failures.append(name)

    print(f"\n{len(failures)} failure(s): {failures}" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:] or list(REGIONS))
    finally:
        app.exitQgis()
    sys.exit(code)
