#!/usr/bin/env python3
"""Headless end-to-end test (needs PyQGIS and internet).

Exports a few sheets in several regions of the world and checks zones, sources,
two-page PDFs, the overview orientation and a UTM zone switch.

    QT_QPA_PLATFORM=offscreen python3 dev/test_core.py [region ...]
    QT_QPA_PLATFORM=offscreen python3 dev/test_core.py --offline   # no downloads (CI)

Regions: bw nl us jp nz fallback lu bz westland
"""
import csv
import os
import re
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

from kartenwerk import cache, config, crs, exporter, grid, layout, sources  # noqa: E402

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
    big = [{"row": r, "col": c, "sheet_id": f"{r}-{c}"} for r in range(12) for c in range(12)]
    check("locator 12×12, middle sheet: series continues on all sides",
          layout.locator_hints(big, {"row": 6, "col": 6}) == {"north", "south", "west", "east"},
          sorted(layout.locator_hints(big, {"row": 6, "col": 6})))
    check("locator 12×12, NW corner sheet: continues south and east only",
          layout.locator_hints(big, {"row": 11, "col": 0}) == {"south", "east"},
          sorted(layout.locator_hints(big, {"row": 11, "col": 0})))
    check("locator 3×3: no marks", layout.locator_hints(
        [{"row": r, "col": c} for r in range(3) for c in range(3)], {"row": 1, "col": 1}) == set())
    cells = layout.overview_cells(sheets)
    top = [c for c in cells if c[1] == 0][0][2]
    check("overview: north row on top", top["sheet_id"] == "north")
    check("format_coord", config.format_coord(5415000) == "5 415 000", config.format_coord(5415000))
    for raw, expected in ((17300, 17500), (12500, 12500), (3000, 5000), (250000, 100000),
                          ("abc", config.DEFAULT_SCALE), (None, config.DEFAULT_SCALE),
                          ("20000", 20000), (99760, 100000)):
        check(f"normalize_scale({raw!r})", config.normalize_scale(raw) == expected,
              config.normalize_scale(raw))
    config.set_setting("scale", 12500)
    check("a stored 1:12 500 stays valid", config.scale() == 12500, config.scale())
    check("format_scale(100000)", config.format_scale(100000) == "1:100 000")


def safe_zone_violations(layout_):
    """Items whose box comes closer than the printer margin to the paper edge."""
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
        s = config.safe_mm() - 0.01
        if rect.left() < s or rect.right() > size.width() - s or top < s \
                or top + rect.height() > size.height() - s:
            bad.append((item.id(), round(rect.left(), 1), round(top, 1),
                        round(rect.width(), 1), round(rect.height(), 1)))
    return bad


def slot_violations(layout_, slots, geom):
    """Items of a slot that leave their own sheet, or border items on any map picture."""
    from qgis.PyQt.QtCore import QRectF
    pages = layout_.pageCollection()
    items = [it for it in layout_.items() if hasattr(it, "id") and callable(it.id) and it.id()]
    bad = []
    for slot in slots:
        page_y = pages.page(slot.page).pos().y()
        sheet = QRectF(slot.x0 - 0.01, page_y + slot.y0 - 0.01, geom.page_w + 0.02,
                       geom.page_h + 0.02)
        for it in items:
            if it.id().startswith(slot.prefix) and not sheet.contains(it.sceneBoundingRect()):
                bad.append(("outside its sheet", it.id()))
    maps = [it.sceneBoundingRect().adjusted(0.3, 0.3, -0.3, -0.3) for it in items
            if it.id().endswith("map_picture")]
    for it in items:
        if it.id().endswith("map_picture"):
            continue
        if any(it.sceneBoundingRect().intersects(m) for m in maps):
            bad.append(("on a map", it.id()))
    return bad


def border_text_overlaps(layout_, prefix):
    """Border texts that wrap (wider than their box) or touch a neighbour in their row."""
    from qgis.PyQt.QtCore import Qt
    left_a, right_a = int(Qt.AlignLeft), int(Qt.AlignRight)
    extents = {}
    bad = []
    for name in ("title", "neighbor_top", "north", "scale", "neighbor_bottom", "coord",
                 "source", "corner"):
        item = layout_.itemById(prefix + name)
        rect = item.sceneBoundingRect()
        width = layout.text_width(item.text())
        if width > rect.width() + 0.05 and not name.startswith("neighbor"):
            bad.append((name, "wraps", round(width, 1), round(rect.width(), 1)))
        align = int(item.hAlign())
        if align & left_a:
            extents[name] = (rect.left(), rect.left() + width)
        elif align & right_a:
            extents[name] = (rect.right() - width, rect.right())
        else:
            extents[name] = (rect.center().x() - width / 2, rect.center().x() + width / 2)
    loc = layout_.itemById(prefix + "locator").sceneBoundingRect()
    extents["locator"] = (loc.left(), loc.right())
    for row in (("title", "neighbor_top", "north"),
                ("scale", "neighbor_bottom", "coord", "locator"),
                ("source", "corner", "locator")):
        spans = sorted(extents[name] + (name,) for name in row)
        for (a0, a1, a), (b0, b1, b) in zip(spans, spans[1:]):
            if a1 > b0:
                bad.append((a, b, round(a1 - b0, 1)))
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
    print(f"== layout {fmt} 1:{scale_denom}, printer margin {config.safe_mm():g} mm", flush=True)
    config.set_setting("page_format", fmt)
    config.set_setting("scale", scale_denom)
    project = QgsProject.instance()
    project.clear()
    geom = config.page_geometry()
    utm = crs.utm_crs(32, "N")
    cell = QgsRectangle(440_000, 5_320_000, 440_000 + geom.cell_w, 5_320_000 + geom.cell_h)
    extent = QgsRectangle(cell.xMinimum() - geom.pad_w, cell.yMinimum() - geom.pad_h,
                          cell.xMaximum() + geom.pad_w, cell.yMaximum() + geom.pad_h)
    sheet = {"sheet_id": "32N-R1234C0567", "utm_zone": 32, "hemisphere": "N", "sheet_no": 17,
             "sheet_name": "Schönenbach-Hinterzarten", "row": 1234, "col": 567,
             "e_min": cell.xMinimum(), "e_max": cell.xMaximum(),
             "n_min": cell.yMinimum(), "n_max": cell.yMaximum()}
    long_id = {"sheet_id": "32N-R1234C0568", "sheet_no": None, "sheet_name": None, "edge_value": 0}
    neighbour = {"north": {**long_id, "sheet_no": 3}, "south": long_id, "east": long_id, "west": None}
    info = {"extent": extent, "sheets": [sheet], "convergence": 1.23, "grid_magnetic": 2.34,
            "year": 2026, "date": "2026-11-25", "mgrs": "32U MU"}
    attribution = "© OpenTopoMap (CC-BY-SA) · © OpenStreetMap contributors"
    lay = layout.ensure_layout(project, force=True)
    image, _ = render.render_image(extent, [], utm, 254, cell=None)
    # unique per case: QGIS caches pictures by path
    img_path = config.cache_dir() / f"grid_only_{fmt}_{scale_denom}_{config.safe_mm():g}.png"
    render.save_image(image, img_path)
    for side in layout.SIDES:
        layout.update_side(lay, side, sheet, img_path, neighbour, attribution, info)

    bad = safe_zone_violations(lay)
    check(f"{fmt}: all border items inside the {config.safe_mm():g} mm printer margin", not bad, bad[:5])
    bad = slot_violations(lay, [layout.default_slot(s, (0.0, 0.0)) for s in layout.SIDES], geom)
    check(f"{fmt}: border items stay off the map", not bad, bad[:5])
    for side in layout.SIDES:
        bad = border_text_overlaps(lay, f"{side}_")
        check(f"{fmt}: {side} border texts fit without overlapping", not bad, bad[:5])

    page = QgsLayoutExporter(lay).renderPageToImage(0, QSize(), 254)  # 10 px per mm
    px = page.width() / geom.page_w
    row = int((geom.map_y + geom.map_h * 0.37) * px)
    values = [page.pixelColor(x, row).lightness()
              for x in range(int(geom.map_x * px) + 3, int(geom.right * px) - 3)]
    lines = runs(values, lambda v: v < 200)  # thin lines on white; resampling lightens them
    gaps = [(b - a) / px for a, b in zip(lines, lines[1:])]
    expected = config.GRID_SPACING_M * geom.mm_per_m()
    check(f"{fmt}: 1 km grid spacing on paper = {expected:.2f} mm",
          gaps and all(abs(g - expected) < 0.25 for g in gaps), [round(g, 2) for g in gaps])

    for mode in config.PRINT_MODES.get(fmt, ()):
        sheets, paper, orientation, cols, rows = mode
        n_up, slots = layout.n_up_layout(project, 3, mode)  # odd count: last page pair half empty
        for i, (front_slot, back_slot) in enumerate(slots):
            for side, slot in (("front", front_slot), ("back", back_slot)):
                layout.update_side(n_up, side, {**sheet, "sheet_no": i + 1}, img_path, neighbour,
                                   attribution, info, slot)
        paper_w, paper_h = config.print_mode_paper(mode)
        pairs = (3 + cols * rows - 1) // (cols * rows)
        size = n_up.pageCollection().page(0).pageSize()
        check(f"{fmt}: {sheets}-per-page layout on {paper}, {2 * pairs} pages",
              n_up.pageCollection().pageCount() == 2 * pairs
              and abs(size.width() - paper_w) < 0.01 and abs(size.height() - paper_h) < 0.01)
        bad = safe_zone_violations(n_up)
        check(f"{fmt}: {sheets}-per-page items inside the safe zone", not bad, bad[:5])
        bad = slot_violations(n_up, [s for pair in slots for s in pair], geom)
        check(f"{fmt}: {sheets}-per-page items stay on their own sheet, off the maps",
              not bad, bad[:5])
        bad = [b for pair in slots for s in pair for b in border_text_overlaps(n_up, s.prefix)]
        check(f"{fmt}: {sheets}-per-page border texts fit without overlapping", not bad, bad[:5])
        # Duplex turns the paper about its vertical axis: columns swap, rows stay.
        check(f"{fmt}: {sheets}-per-page backs mirrored across the paper", all(
            abs(b.x0 - (paper_w - f.x0 - geom.page_w)) < 0.01 and b.y0 == f.y0
            for f, b in slots), [(round(f.x0, 1), round(b.x0, 1)) for f, b in slots])
        front_b = slots[1][0].y0
        back_b = slots[1][1].y0
        check(f"{fmt}: {sheets}-per-page back sits behind its front", front_b == back_b
              and slots[1][1].page == slots[1][0].page + 1)

    metres, length = layout.scale_bar_length(geom)
    bar_row = int((geom.bottom + 2.85 * geom.text_scale) * px)  # middle of the scale bar
    bar = [page.pixelColor(x, bar_row).lightness()
           for x in range(int((geom.map_x - 1) * px), int((geom.map_x + length + 1) * px))]
    dark = [i for i, v in enumerate(bar) if v < 100]
    measured = (dark[-1] - dark[0]) / px if dark else 0
    check(f"{fmt}: scale bar {metres:g} m = {length:.2f} mm", abs(measured - length) < 0.4,
          round(measured, 2))


def feature_checks(work: Path):
    """Offline checks for sheet numbers on regenerate, overlays, render cache, duplex offset."""
    import os
    import time
    from qgis.PyQt.QtGui import QColor, QImage
    from qgis.core import QgsVectorLayer
    from kartenwerk import cache, render
    print("== features (offline)", flush=True)
    config.set_setting("page_format", "A5")
    config.set_setting("scale", 15000)
    config.set_setting("overlap", 5)
    project = QgsProject.instance()
    project.clear()
    gpkg = work / "features.gpkg"
    gpkg.unlink(missing_ok=True)
    aoi = grid.ensure_aoi_layer(project, gpkg)
    set_aoi(aoi, (8.20, 48.00, 8.26, 48.04))

    # --- #4 numbers and names survive regeneration ----------------------------
    g, _, _ = grid.generate_grid(project, aoi, gpkg_path=gpkg)
    ids = sorted(g.allFeatureIds())
    g.selectByIds(ids[:3])
    grid.number_selected(g)
    named = ids[1]
    grid.set_sheet_name(g, named, "Testname")
    before = {f["sheet_id"]: (f["sheet_no"], f["sheet_name"]) for f in g.getFeatures()
              if f["sheet_no"] not in (None, "")}
    g, _, _ = grid.generate_grid(project, aoi, gpkg_path=gpkg)
    after = {f["sheet_id"]: (f["sheet_no"], f["sheet_name"]) for f in g.getFeatures()
             if f["sheet_no"] not in (None, "")}
    check("regenerate keeps numbers and names", before == after and len(after) == 3, after)
    g.selectByIds(sorted(g.allFeatureIds())[:5])
    added = grid.number_missing(g)
    numbers = sorted(int(f["sheet_no"]) for f in g.getFeatures() if f["sheet_no"] not in (None, ""))
    check("auto-numbering only fills gaps (keeps 1–3, adds 4–5)",
          added == 2 and numbers == [1, 2, 3, 4, 5], numbers)
    config.set_setting("scale", 20000)
    g, _, _ = grid.generate_grid(project, aoi, gpkg_path=gpkg)
    kept = [f["sheet_no"] for f in g.getFeatures() if f["sheet_no"] not in (None, "")]
    check("different cell size → numbers not carried over", kept == [], kept)
    config.set_setting("scale", 15000)

    # --- #6 overlays are drawn but never count as basemap data ----------------
    geom = config.page_geometry()
    utm = crs.utm_crs(32, "N")
    extent = QgsRectangle(440_000, 5_320_000, 440_000 + geom.cell_w, 5_320_000 + geom.cell_h)
    route = QgsVectorLayer("LineString?crs=EPSG:32632", "route", "memory")
    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromWkt(
        f"LINESTRING({extent.xMinimum()} {extent.center().y()}, {extent.xMaximum()} {extent.center().y()})"))
    route.dataProvider().addFeatures([feat])
    route.renderer().symbol().setColor(QColor(255, 0, 0))
    route.renderer().symbol().setWidth(1.5)
    image, missing = render.render_image(extent, [], utm, 100, overlays=[route])
    y = image.height() // 2
    red = image.pixelColor(image.width() // 3, y)
    check("overlay drawn on the map", red.red() > 200 and red.green() < 80, red.name())
    check("overlay not counted as basemap data", missing == 1.0, missing)

    # --- #10 render cache ---------------------------------------------------
    cache.set_cache_root(work / "render_cache_unit")
    cache.clear()
    k1 = cache.key("opentopomap", "uri", "EPSG:32632", extent, 100, 80, 300)
    check("cache key changes with dpi", k1 != cache.key("opentopomap", "uri", "EPSG:32632",
                                                         extent, 100, 80, 150))
    img = QImage(100, 80, QImage.Format_ARGB32)
    img.fill(QColor(10, 120, 30))
    cache.store(k1, img)
    loaded = cache.load(k1)
    check("cache store/load round trip", loaded is not None and
          loaded.pixelColor(5, 5).green() == 120)
    old = time.time() - (cache.MAX_AGE_DAYS + 1) * 86400
    os.utime(cache.root() / f"{k1}.png", (old, old))
    check("expired cache entry is dropped", cache.load(k1) is None)
    for i in range(3):
        cache.store(cache.key("opentopomap", f"u{i}", "EPSG:32632", extent, 100, 80, 300), img)
        time.sleep(0.01)
    single = cache.entries()[0].stat().st_size
    cache.prune(max_bytes=int(single * 2.5))
    check("cache pruned to size limit (oldest removed)", len(cache.entries()) == 2,
          len(cache.entries()))
    cache.clear()
    check("cache cleared", cache.entries() == [])
    cache.set_cache_root(work / "render_cache")
    check("Esri imagery is not cacheable", sources.get("esri_imagery").cacheable is False)

    # --- overview page with a map background ----------------------------------
    block = [{"row": r, "col": c, "sheet_id": f"32N-R{r}C{c}", "sheet_no": i + 1,
              "utm_zone": 32, "hemisphere": "N", "sheet_name": None,
              "e_min": c * geom.cell_w, "e_max": (c + 1) * geom.cell_w,
              "n_min": r * geom.cell_h, "n_max": (r + 1) * geom.cell_h}
             for i, (r, c) in enumerate([(11, 5), (11, 6), (10, 5)])]  # 2×2 block, SE missing
    rows_, cols_, w_mm, h_mm = layout.overview_diagram_size(block, geom)
    aspect = geom.cell_w / geom.cell_h
    expected_h = min((geom.page_w - 2 * geom.margin_side) / (2 * aspect),
                     (geom.page_h - 2 * geom.margin_side) * 0.55 / 2, 18.0) * 2
    check("overview size: 2×2 block, true sheet proportions",
          (rows_, cols_) == (2, 2) and abs(h_mm - expected_h) < 1e-6
          and abs(w_mm / h_mm - aspect) < 1e-6, (rows_, cols_, round(w_mm, 1), round(h_mm, 1)))
    bg = QImage(400, int(400 / aspect), QImage.Format_RGB32)
    bg.fill(QColor(60, 140, 60))
    for colour in ("#c8005a", "#00a0ff"):
        img, _, _ = layout._overview_image(block, aspect, bg, colour)
        cw, ch = img.width() / 2, img.height() / 2
        missing_cell = img.pixelColor(int(cw * 1.15), int(ch * 1.85))  # SE cell, not exported
        kept = img.pixelColor(int(cw * 0.15), int(ch * 0.85))  # NW cell corner area
        edge = img.pixelColor(int(cw * 0.5), 2)  # top outline of the NW cell
        check(f"overview {colour}: unexported cell veiled, exported cell shows the map",
              missing_cell.green() > 180 and (kept.red(), kept.green(), kept.blue()) == (60, 140, 60),
              (missing_cell.name(), kept.name()))
        target = QColor(colour)
        check(f"overview {colour}: sheet outline in the chosen colour",
              abs(edge.red() - target.red()) < 40 and abs(edge.blue() - target.blue()) < 40,
              edge.name())
    labels = []
    original_label = layout._label
    layout._label = lambda lay, item_id, *a, **k: labels.append(item_id) or original_label(
        lay, item_id, *a, **k)
    try:
        plain = layout.export_overview(project, block, work / "ov_plain.pdf", 150)
        plain_has = "overview_attribution" in labels
        labels.clear()
        mapped = layout.export_overview(project, block, work / "ov_map.pdf", 150, bg,
                                        "© OpenTopoMap (CC-BY-SA)")
        mapped_has = "overview_attribution" in labels
    finally:
        layout._label = original_label
    pages = [len(re.findall(rb"/Type\s*/Page[^s]", f.read_bytes())) for f in (plain, mapped)]
    check("overview PDFs with and without map, 1 page each", pages == [1, 1], pages)
    check("overview map attribution printed only with a map", mapped_has and not plain_has,
          (plain_has, mapped_has))

    def series(n_rows, n_cols):
        return [{"row": 3000 + r, "col": 100 + c, "sheet_id": crs.sheet_id(32, "N", 3000 + r, 100 + c),
                 "sheet_no": r * n_cols + c + 1, "utm_zone": 32, "hemisphere": "N",
                 "sheet_name": None, "e_min": (100 + c) * geom.cell_w,
                 "e_max": (101 + c) * geom.cell_w, "n_min": (3000 + r) * geom.cell_h,
                 "n_max": (3001 + r) * geom.cell_h}
                for r in range(n_rows) for c in range(n_cols)]

    for n_rows, n_cols, expected_pages in ((12, 12, 1), (30, 30, 3)):  # A5: 180 + 531 per page
        big = series(n_rows, n_cols)
        pdf = layout.export_overview(project, big, work / f"ov_{n_rows}x{n_cols}.pdf", 100)
        pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))
        check(f"overview list for {n_rows * n_cols} sheets fits ({expected_pages} page(s))",
              pages == expected_pages, pages)

    # --- back side mirrored or normal (default) ------------------------------
    nb = {"west": {"sheet_id": "32N-R1234C0566", "sheet_no": 6, "sheet_name": None},
          "east": {"sheet_id": "32N-R1234C0568", "sheet_no": 8, "sheet_name": None}}
    msheet = {"sheet_id": "32N-R1234C0567", "utm_zone": 32, "hemisphere": "N", "sheet_no": 7,
              "sheet_name": None, "row": 1234, "col": 567}
    mimg = config.cache_dir() / "mirror_test.png"
    render.save_image(QImage(10, 10, QImage.Format_RGB32), mimg)
    m_extent = QgsRectangle(440_300, 5_320_000, 440_300 + geom.cell_w, 5_320_000 + geom.cell_h)
    results = {}
    for mirror in (True, False):
        lay = layout.ensure_layout(project, force=True)
        minfo = {"extent": m_extent, "sheets": [msheet], "convergence": 0.5, "grid_magnetic": 2.0,
                 "year": 2026, "date": "2026-11-25", "mgrs": "32U MU", "mirror_back": mirror,
                 "run": f"m{mirror}"}
        for side in layout.SIDES:
            layout.update_side(lay, side, msheet, mimg, nb, "© Test", minfo)
        km_x = {side: sorted(round(it.sceneBoundingRect().center().x(), 1) for it in lay.items()
                             if hasattr(it, "id") and callable(it.id)
                             and it.id().startswith(f"{side}_km_e") and it.id().endswith("t"))
                for side in layout.SIDES}
        results[mirror] = {
            "km_same": km_x["front"] == km_x["back"],
            "left": lay.itemById("back_neighbor_left").text(),
            "title": lay.itemById("back_title").text(),
            "north": lay.itemById("back_north").text(),
        }
    on, off = results[True], results[False]
    check("mirrored back: km labels mirrored, east neighbour on the left",
          not on["km_same"] and "08" in on["left"], on["left"][-30:])
    check("mirrored back: title says mirrored, north block only the arrow",
          "mirrored" in on["title"] and "magnetic" not in on["north"])
    check("normal back: km labels like the front, west neighbour on the left",
          off["km_same"] and "06" in off["left"], off["left"][-30:])
    check("normal back: title not mirrored, full north block",
          "mirrored" not in off["title"] and "magnetic" in off["north"])
    two = QImage(4, 1, QImage.Format_RGB32)
    two.fill(QColor("white"))
    two.setPixelColor(0, 0, QColor("black"))
    for mirror, expected_x in ((True, 3), (False, 0)):
        path = config.cache_dir() / f"mirror_px_{mirror}.png"
        render.save_image(two, path, mirror=mirror)
        loaded = QImage(str(path))
        dark = [x for x in range(4) if loaded.pixelColor(x, 0).lightness() < 50]
        check(f"save_image mirror={mirror}", dark == [expected_x], dark)

    # --- #5 duplex offset moves every back item, nothing on the front --------
    def item_rects(lay):
        return {it.id(): it.sceneBoundingRect() for it in lay.items()
                if hasattr(it, "id") and callable(it.id) and it.id()}

    sheet = {"sheet_id": "32N-R1234C0567", "utm_zone": 32, "hemisphere": "N", "sheet_no": 7,
             "sheet_name": None, "row": 1234, "col": 567}
    info = {"extent": extent, "sheets": [sheet], "convergence": 0.5, "grid_magnetic": 2.0,
            "year": 2026, "date": "2026-11-25", "mgrs": "32U MU"}
    img_path = config.cache_dir() / "features_grid.png"
    render.save_image(QImage(10, 10, QImage.Format_RGB32), img_path)
    rects = {}
    for dx, dy in ((0.0, 0.0), (2.0, -1.5)):
        config.set_setting("duplex_dx", dx)
        config.set_setting("duplex_dy", dy)
        lay = layout.ensure_layout(project, force=True)
        for side in layout.SIDES:
            layout.update_side(lay, side, sheet, img_path, {}, "© Test", info)
        rects[(dx, dy)] = item_rects(lay)
        if (dx, dy) == (0.0, 0.0):
            coord = lay.itemById("front_coord").text()
            check("MGRS square in the border", "32U MU" in coord, coord[:80])
    base, moved = rects[(0.0, 0.0)], rects[(2.0, -1.5)]
    back_ok = all(abs(moved[i].x() - base[i].x() - 2.0) < 0.01 and
                  abs(moved[i].y() - base[i].y() + 1.5) < 0.01
                  for i in base if i.startswith("back_"))
    front_ok = all(abs(moved[i].x() - base[i].x()) < 0.01 and abs(moved[i].y() - base[i].y()) < 0.01
                   for i in base if i.startswith("front_"))
    n_back = len([i for i in base if i.startswith("back_")])
    check(f"duplex offset moves all {n_back} back items by (2, -1.5) mm", back_ok)
    check("duplex offset leaves the front unchanged", front_ok)
    config.set_setting("duplex_dx", 0.0)
    config.set_setting("duplex_dy", 0.0)
    config.set_setting("print_mode", 2)
    files = layout.export_duplex_test(project, work / "duplex")
    config.set_setting("print_mode", 0)
    pages = [len(re.findall(rb"/Type\s*/Page[^s]", f.read_bytes())) for f in files]
    check("duplex test PDFs (sheet + 2-per-page), 2 pages each",
          [f.name for f in files] == ["Duplex_test_A5.pdf", "Duplex_test_2-per-page_A4.pdf"]
          and pages == [2, 2], [f.name for f in files] + pages)
    config.set_setting("page_format", "A6")
    config.set_setting("print_mode", 4)
    files = layout.export_duplex_test(project, work / "duplex_a6")
    config.set_setting("print_mode", 0)
    config.set_setting("page_format", "A5")
    check("duplex test PDFs (A6 + 4-per-page on A4)",
          [f.name for f in files] == ["Duplex_test_A6.pdf", "Duplex_test_4-per-page_A4.pdf"],
          [f.name for f in files])


def legacy_migration(work: Path):
    """A version-1 GeoPackage ('arbeitsgebiet', EPSG:25832) must migrate to 'work_area'."""
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import QgsField
    print("== legacy migration", flush=True)
    gpkg = work / "legacy.gpkg"
    gpkg.unlink(missing_ok=True)
    old = grid._write_layer(gpkg, "arbeitsgebiet", "EPSG:25832", [
        QgsField("name", QVariant.String, "string", 80),
        QgsField("bemerkung", QVariant.String, "string", 200)])
    feats = []
    for i, e in enumerate((440_000, 446_000)):
        feat = QgsFeature(old.fields())
        feat.setGeometry(QgsGeometry.fromRect(QgsRectangle(e, 5_320_000, e + 2_000, 5_322_000)))
        feat["name"] = f"Area {i}"  # by name: GeoPackage layers start with an 'fid' column
        feat["bemerkung"] = "old note"
        feats.append(feat)
    old.dataProvider().addFeatures(feats)
    del old
    project = QgsProject.instance()
    project.clear()
    aoi = grid.ensure_aoi_layer(project, gpkg)
    check("migrated AOI name", aoi.name() == config.AOI_LAYER, aoi.name())
    check("migrated AOI CRS", aoi.crs().authid() == "EPSG:4326", aoi.crs().authid())
    check("migrated AOI features", aoi.featureCount() == 2, aoi.featureCount())
    notes = sorted(f["note"] for f in aoi.getFeatures())
    check("migrated 'bemerkung' → 'note'", notes == ["old note", "old note"], notes)
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
    if name == "bw":
        # Second export of the same sheets: everything must come from the render cache.
        from kartenwerk import render
        calls = []
        original = render.render_basemap
        render.render_basemap = lambda *a, **k: calls.append(1) or original(*a, **k)
        try:
            exporter.export_sheets(project, grid_layer, {"output_dir": str(out)})
        finally:
            render.render_basemap = original
        check(f"{name}: re-export downloads nothing (render cache)", calls == [], len(calls))
    if name == "jp":
        esri = [p.name for p in cache.entries() if p.name.startswith("esri_imagery")]
        check(f"{name}: Esri imagery never written to the cache", esri == [], esri)
    if name == "bw":
        prints = list(out.glob("Print_2-per-page_*.pdf"))
        pages = len(re.findall(rb"/Type\s*/Page[^s]", prints[0].read_bytes())) if prints else 0
        check(f"{name}: 2-per-page print file with {2 * ((len(ids) + 1) // 2)} pages",
              pages == 2 * ((len(ids) + 1) // 2), [p.name for p in prints] + [pages])
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
    feature_checks(work)
    for fmt, scale_denom, margin in (("A5", 15000, 5), ("A2", 25000, 5), ("A4", 10000, 5),
                                     ("A5", 15000, 3), ("A3", 20000, 4),
                                     ("A2", 5000, 5), ("A5", 100000, 5), ("A4", 37500, 5),
                                     ("A6", 15000, 5)):
        config.set_setting("printer_margin", margin)
        layout_checks(fmt, scale_denom)
    config.set_setting("printer_margin", 5)

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
    config.set_setting("render_cache", True)
    cache.set_cache_root(work / "render_cache")
    cache.clear()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    gpkg = work / "test_data.gpkg"
    aoi = grid.ensure_aoi_layer(project, gpkg)
    # Regions run in one session, so every region after the first is a zone switch.
    for name in selected:
        config.set_setting("print_mode", 2 if name == "bw" else 0)
        try:
            run_region(name, work, project, aoi, gpkg)
        except Exception:
            traceback.print_exc()
            failures.append(name)

    print(f"\n{len(failures)} failure(s): {failures}" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        args = sys.argv[1:]
        offline = "--offline" in args
        regions = [a for a in args if a != "--offline"]
        code = main([] if offline else (regions or list(REGIONS)))
    finally:
        app.exitQgis()
    sys.exit(code)
