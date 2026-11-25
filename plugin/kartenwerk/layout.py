"""Print layout (page 1: front topo, page 2: mirrored aerial back) and the overview sheet.

The map itself is kept free of text. The white border carries, inside the
printer-safe zone (config.SAFE_MM from the paper edge):

    top     sheet number + id · north neighbour · north arrow with grid/true/magnetic north
    sides   km grid labels (northings) · west/east neighbours (rotated, reading upwards)
    bottom  km grid labels (eastings) · scale bar + scale · south neighbour ·
            UTM zone + SW corner · locator diagram · attribution + print date
"""
from __future__ import annotations

import datetime as _dt
import html
import math
import uuid
from pathlib import Path

from qgis.PyQt.QtCore import QRect, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen
from qgis.core import (
    QgsFillSymbol,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemShape,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsUnitTypes,
)

from . import config, crs as crs_mod

LAYOUT_NAME = "Kartenwerk"
LAYOUT_VERSION = "4"  # bump when the border design changes, so saved layouts are rebuilt
LEGACY_LAYOUTS = ("Vorderseite", "Rueckseite")
SIDES = ("front", "back")  # page 0, page 1
_MM = QgsUnitTypes.LayoutMillimeters
_PROP = "kartenwerk_signature"

GREY = "#555555"
LIGHT_GREY = "#8a8a8a"
# Border dimensions on A5 in mm; multiplied by the page's text scale (see u()).
_LOCATOR_H = 6.4
_LOCATOR_MAX_W = 30.0  # reserved at the bottom right
_SIDE_LABEL_LEN = 30.0  # rotated neighbour label box
_KM_ROW_H = 1.8

# Text/lane scale of the page being built (config.TEXT_SCALE); set by _use_scale().
_K = 1.0


def _use_scale(geom: config.PageGeometry) -> None:
    global _K
    _K = geom.text_scale


def u(value: float) -> float:
    """A border dimension in mm, scaled for the current page format."""
    return value * _K


class Slot:
    """Where one side of one sheet goes: page index, vertical offset and item-id prefix."""

    def __init__(self, side: str, page: int, y0: float = 0.0, prefix: str | None = None):
        self.side = side
        self.page = page
        self.y0 = y0
        self.prefix = prefix if prefix is not None else f"{side}_"


def default_slot(side: str) -> Slot:
    return Slot(side, SIDES.index(side))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _font(size: float = 7, bold: bool = False) -> QFont:
    font = QFont()  # platform default family: available on Linux, Windows and macOS
    font.setPointSizeF(size * _K)
    font.setBold(bold)
    return font


def _span(text: str, size: float, bold: bool = False, color: str = "#000000") -> str:
    weight = "bold" if bold else "normal"
    return (f"<span style='font-size:{size * _K:.2f}pt;font-weight:{weight};color:{color}'>"
            f"{text}</span>")


def _label(
    layout: QgsPrintLayout,
    item_id: str,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    page: int = 0,
    align=Qt.AlignCenter,
    valign=Qt.AlignVCenter,
    rotation: float = 0.0,
) -> QgsLayoutItemLabel:
    label = QgsLayoutItemLabel(layout)
    label.setId(item_id)
    label.setMode(QgsLayoutItemLabel.ModeHtml)
    label.setText(text)
    label.setFont(_font())
    label.setHAlign(align)
    label.setVAlign(valign)
    label.setMargin(0.0)
    layout.addLayoutItem(label)
    label.attemptResize(QgsLayoutSize(w, h, _MM))
    label.attemptMove(QgsLayoutPoint(x, y, _MM), True, False, page)
    if rotation:
        label.setItemRotation(rotation, True)  # rotates about the box centre
    return label


def _rect(layout, item_id: str, x: float, y: float, w: float, h: float, page: int,
          fill: str, stroke: float = 0.15) -> QgsLayoutItemShape:
    shape = QgsLayoutItemShape(layout)
    shape.setId(item_id)
    shape.setShapeType(QgsLayoutItemShape.Rectangle)
    outline = {"outline_color": "black", "outline_width": str(stroke), "outline_width_unit": "MM"}
    if stroke <= 0:
        outline = {"outline_style": "no"}
    shape.setSymbol(QgsFillSymbol.createSimple({"color": fill, **outline}))
    layout.addLayoutItem(shape)
    shape.attemptResize(QgsLayoutSize(w, h, _MM))
    shape.attemptMove(QgsLayoutPoint(x, y, _MM), True, False, page)
    return shape


def scale_bar_length(geom: config.PageGeometry) -> tuple[float, float]:
    """(ground metres, paper mm) of the scale bar: the longest round length that fits."""
    limit = min(80.0 * geom.text_scale, geom.page_w * 0.25)
    for metres in (5000, 2000, 1000, 500, 250, 200, 100):
        mm = metres * geom.mm_per_m()
        if mm <= limit:
            return float(metres), mm
    return 100.0, 100.0 * geom.mm_per_m()


def _distance_text(metres: float) -> str:
    return f"{metres / 1000:g} km" if metres >= 1000 else f"{metres:g} m"


# ---------------------------------------------------------------------------
# layout construction
# ---------------------------------------------------------------------------
def _add_scale_bar(layout, slot: Slot, geom: config.PageGeometry) -> None:
    p, page, y0 = slot.prefix, slot.page, slot.y0
    metres, length = scale_bar_length(geom)
    x0 = geom.map_x
    bottom = geom.bottom + y0
    segments = 4
    seg = length / segments
    for i in range(segments):
        _rect(layout, f"{p}sb_seg{i}", x0 + i * seg, bottom + u(4.4), seg, u(1.1), page,
              "black" if i % 2 == 0 else "white", stroke=0.15 * _K)
    for i, frac in enumerate((0.0, 0.5, 1.0)):
        value = metres * frac
        text = _distance_text(value) if frac == 1.0 else f"{value / 1000:g}" if metres >= 1000 \
            else f"{value:g}"
        if frac == 0.0:  # left-aligned at the bar start, so the box stays in the safe zone
            x, align = x0 - u(0.4), Qt.AlignLeft
        else:
            x, align = x0 + length * frac - u(8.0), Qt.AlignCenter
        _label(layout, f"{p}sb_lbl{i}", _span(text, 5, color=GREY),
               x, bottom + u(2.5), u(16.0), u(1.8), page=page, align=align)
    _label(layout, f"{p}scale", _span(config.format_scale(geom.scale), 7.5, bold=True),
           x0 + length + u(8.0), bottom + u(2.9), u(22.0), u(3.2), page=page, align=Qt.AlignLeft)


def _add_side(layout: QgsPrintLayout, slot: Slot, geom: config.PageGeometry) -> None:
    p, page, y0 = slot.prefix, slot.page, slot.y0
    safe = config.SAFE_MM
    top = y0 + safe
    bottom = y0 + geom.bottom
    map_y = y0 + geom.map_y

    # Map picture with a thin neatline.
    picture = QgsLayoutItemPicture(layout)
    picture.setId(p + "map_picture")
    picture.setResizeMode(QgsLayoutItemPicture.Stretch)
    picture.setFrameEnabled(True)
    picture.setFrameStrokeWidth(QgsLayoutMeasurement(u(0.3), _MM))
    picture.setFrameStrokeColor(QColor("black"))
    layout.addLayoutItem(picture)
    picture.attemptResize(QgsLayoutSize(geom.map_w, geom.map_h, _MM))
    picture.attemptMove(QgsLayoutPoint(geom.map_x, map_y, _MM), True, False, page)

    # --- top row -------------------------------------------------------------
    row_h = geom.map_y - u(_KM_ROW_H) - u(0.2) - safe
    _label(layout, p + "title", "", geom.map_x, top, geom.page_w * 0.42, row_h,
           page=page, align=Qt.AlignLeft)
    _label(layout, p + "neighbor_top", "", geom.page_w / 2 - u(20), top, u(40), row_h, page=page)
    north_w = u(62.0)
    _label(layout, p + "north", "", geom.right - north_w, top, north_w, row_h,
           page=page, align=Qt.AlignRight)

    # --- sides: neighbours, rotated to read upwards on both sides --------------
    lane_cx_left = (safe + geom.map_x) / 2.0
    lane_cx_right = geom.page_w - lane_cx_left
    cy = map_y + geom.map_h / 2.0
    length = u(_SIDE_LABEL_LEN)
    for name, cx in (("neighbor_left", lane_cx_left), ("neighbor_right", lane_cx_right)):
        _label(layout, p + name, "", cx - length / 2, cy - u(2.0), length, u(4.0),
               page=page, rotation=-90)

    # --- bottom rows -----------------------------------------------------------
    _add_scale_bar(layout, slot, geom)
    _label(layout, p + "neighbor_bottom", "", geom.page_w / 2 - u(20), bottom + u(2.6), u(40),
           u(3.8), page=page)
    coord_right = geom.right - u(_LOCATOR_MAX_W) - u(2.0)
    _label(layout, p + "coord", "", coord_right - u(70), bottom + u(2.8), u(70), u(2.8),
           page=page, align=Qt.AlignRight)
    _label(layout, p + "source", "", geom.map_x, bottom + u(6.6),
           coord_right - geom.map_x, u(2.3), page=page, align=Qt.AlignLeft)

    locator = QgsLayoutItemPicture(layout)
    locator.setId(p + "locator")
    locator.setResizeMode(QgsLayoutItemPicture.Stretch)
    layout.addLayoutItem(locator)
    locator.attemptResize(QgsLayoutSize(u(_LOCATOR_H), u(_LOCATOR_H), _MM))
    locator.attemptMove(QgsLayoutPoint(geom.right - u(_LOCATOR_H), bottom + u(2.4), _MM),
                        True, False, page)


def _create_layout(project: QgsProject) -> QgsPrintLayout:
    geom = config.page_geometry()
    _use_scale(geom)
    layout = QgsPrintLayout(project)
    layout.setName(LAYOUT_NAME)
    layout.initializeDefaults()
    pages = layout.pageCollection()
    pages.page(0).setPageSize(QgsLayoutSize(geom.page_w, geom.page_h, _MM))
    back_page = QgsLayoutItemPage(layout)
    back_page.setPageSize(QgsLayoutSize(geom.page_w, geom.page_h, _MM))
    pages.addPage(back_page)

    for side in SIDES:
        _add_side(layout, default_slot(side), geom)

    layout.setCustomProperty(_PROP, _signature())
    project.layoutManager().addLayout(layout)
    return layout


def _signature() -> str:
    return f"{LAYOUT_VERSION}|{config.page_geometry().signature()}"


def _is_current(layout: QgsPrintLayout) -> bool:
    return (
        layout.customProperty(_PROP) == _signature()
        and layout.pageCollection().pageCount() == 2
        and all(layout.itemById(f"{side}_locator") is not None for side in SIDES)
    )


def ensure_layout(project: QgsProject, force: bool = False) -> QgsPrintLayout:
    """Return the two-page layout, rebuilding it when page format, scale, overlap or design changed."""
    manager = project.layoutManager()
    for name in LEGACY_LAYOUTS:
        old = manager.layoutByName(name)
        if old is not None:
            manager.removeLayout(old)

    layout = manager.layoutByName(LAYOUT_NAME)
    if layout is not None and (force or not _is_current(layout)):
        manager.removeLayout(layout)
        layout = None
    return layout if layout is not None else _create_layout(project)


# ---------------------------------------------------------------------------
# "two sheets per page" print file
# ---------------------------------------------------------------------------
def two_up_layout(project: QgsProject, count: int) -> tuple[QgsPrintLayout, list[tuple[Slot, Slot]]]:
    """Layout with two sheets stacked on the next larger paper (portrait).

    Page 2k holds the fronts of sheets 2k and 2k+1 (top, bottom), page 2k+1 their
    backs in the same positions: duplex printing with "flip on long edge" turns the
    paper about its vertical axis, which keeps top and bottom, so every back lands
    behind its front. Returns the layout (not added to the project) and the
    (front slot, back slot) of each sheet.
    """
    geom = config.page_geometry()
    _use_scale(geom)
    paper = config.TWO_UP_PAPER[geom.name]
    paper_h, paper_w = config.PAGE_FORMATS[paper]  # portrait: long side vertical
    layout = QgsPrintLayout(project)
    layout.setName(f"Kartenwerk 2-up {paper}")
    layout.initializeDefaults()
    pages = layout.pageCollection()
    pairs = (count + 1) // 2
    for index in range(pairs * 2):
        page = pages.page(0) if index == 0 else QgsLayoutItemPage(layout)
        page.setPageSize(QgsLayoutSize(paper_w, paper_h, _MM))
        if index:
            pages.addPage(page)

    half = paper_h / 2.0
    offsets = ((half - geom.page_h) / 2.0, half + (half - geom.page_h) / 2.0)
    slots = []
    for i in range(count):
        pair, pos = divmod(i, 2)
        front = Slot("front", 2 * pair, offsets[pos], f"s{i}f_")
        back = Slot("back", 2 * pair + 1, offsets[pos], f"s{i}b_")
        _add_side(layout, front, geom)
        _add_side(layout, back, geom)
        slots.append((front, back))

    # Cut marks on the front pages, inside the printable area at both paper edges.
    mark = u(6.0)
    for pair in range(pairs):
        for x in (config.SAFE_MM, paper_w - config.SAFE_MM - mark):
            _rect(layout, f"cut{pair}_{int(x)}", x, half - 0.1, mark, 0.2, 2 * pair, "black",
                  stroke=0)
    return layout, slots


# ---------------------------------------------------------------------------
# per-sheet content
# ---------------------------------------------------------------------------
def sheet_number_text(sheet: dict, missing: str = "") -> str:
    number = sheet.get("sheet_no")
    if number in (None, ""):
        return missing
    return f"{int(number):02d}" if str(number).isdigit() else str(number)


def neighbour_label(neighbour: dict | None) -> str:
    """'05' for a numbered neighbour, else its short id without the zone ('R2608C0149')."""
    if neighbour is None:
        return ""
    number = sheet_number_text(neighbour)
    if number:
        return number
    return str(neighbour["sheet_id"]).split("-")[-1]


def _neighbour_html(neighbour: dict | None, arrow: str) -> str:
    if neighbour is None:
        return _span("—", 8, color=LIGHT_GREY)
    text = neighbour_label(neighbour)
    numbered = bool(sheet_number_text(neighbour))
    return _span(f"{arrow} {html.escape(text)}", 8.5 if numbered else 6.5, bold=numbered,
                 color="#000000" if numbered else GREY)


def _title_html(sheet: dict, side: str) -> str:
    number = sheet_number_text(sheet)
    name = str(sheet.get("sheet_name") or "").strip()
    sheet_id = html.escape(str(sheet["sheet_id"]))
    parts = []
    if number:
        parts.append(_span(number, 12, bold=True) + "&nbsp;&nbsp;")
    if name:
        parts.append(_span(html.escape(name), 9 if number else 10, bold=True) + "&nbsp;&nbsp;")
    parts.append(_span(sheet_id, 6.5, color=GREY) if (number or name)
                 else _span(sheet_id, 9, bold=True))
    if side == "back":
        parts.append(_span("&nbsp;· aerial image, mirrored for duplex", 6.5, color=GREY))
    return "".join(parts)


def _angle_text(value: float) -> str:
    return f"{abs(value):.1f}° {'E' if value >= 0 else 'W'}"


def _north_html(side: str, info: dict) -> str:
    arrow = _span("▲ N", 10, bold=True)
    if side == "back":
        return arrow
    parts = [f"true N {_angle_text(-info['convergence'])}"]
    if info.get("grid_magnetic") is not None:
        parts.append(f"magnetic N {_angle_text(info['grid_magnetic'])} ({info['year']})")
    small = _span("Grid north ↑ · " + " · ".join(parts) + "&nbsp;&nbsp;", 5, color=GREY)
    return small + arrow


def _coord_html(sheet: dict, extent) -> str:
    f = config.format_coord
    zone = crs_mod.zone_label(int(sheet["utm_zone"]), str(sheet["hemisphere"]))
    return (_span(f"UTM {zone}", 6.5, bold=True)
            + _span(f" · SW corner E {f(extent.xMinimum())} · N {f(extent.yMinimum())}", 6,
                    color=GREY))


def _source_html(text: str, date: str) -> str:
    return _span(f"{html.escape(str(text))} · Printed {date}", 5, color=GREY)


def _km_html(value_m: float) -> str:
    """UTM style: small leading digits + bold two-digit km, e.g. ⁴36 for E 436 000."""
    km = int(round(value_m / 1000.0))
    main = f"{km % 100:02d}"
    prefix = str(km // 100) if km >= 100 else ""
    return ("<span style='white-space:nowrap'>" + _span(prefix, 3.5, color=GREY)
            + _span(main, 5.5, bold=True) + "</span>")


def _remove_items(layout: QgsPrintLayout, prefix: str) -> None:
    for item in list(layout.items()):
        item_id = getattr(item, "id", None)
        if callable(item_id) and item.id().startswith(prefix):
            layout.removeLayoutItem(item)


def _add_km_labels(layout, slot: Slot, extent, geom: config.PageGeometry) -> None:
    """Kilometre labels in the border at every 1 km grid line (rebuilt per sheet)."""
    p = f"{slot.prefix}km_"
    page, y0 = slot.page, slot.y0
    _remove_items(layout, p)
    safe = config.SAFE_MM
    spacing = config.GRID_SPACING_M
    sx = geom.map_w / extent.width()
    sy = geom.map_h / extent.height()

    e = math.ceil(extent.xMinimum() / spacing) * spacing
    i = 0
    while e <= extent.xMaximum():
        x = geom.map_x + (e - extent.xMinimum()) * sx
        if slot.side == "back":  # the back image is mirrored left↔right
            x = geom.map_x + geom.map_w - (x - geom.map_x)
        w = u(8.0)
        if x - w / 2 >= safe and x + w / 2 <= geom.page_w - safe:
            text = _km_html(e)
            _label(layout, f"{p}e{i}t", text, x - w / 2,
                   y0 + geom.map_y - u(_KM_ROW_H) - u(0.2), w, u(_KM_ROW_H), page=page)
            _label(layout, f"{p}e{i}b", text, x - w / 2, y0 + geom.bottom + u(0.3), w,
                   u(_KM_ROW_H), page=page)
        e += spacing
        i += 1

    n = math.ceil(extent.yMinimum() / spacing) * spacing
    i = 0
    mid = geom.map_y + geom.map_h / 2.0
    lane_w = geom.map_x - u(0.5) - safe
    while n <= extent.yMaximum():
        y = geom.map_y + (extent.yMaximum() - n) * sy
        h = u(2.4)
        # Keep clear of the rotated side neighbours and of the page corners.
        if abs(y - mid) > u(_SIDE_LABEL_LEN / 2 - 6.0) and y - h / 2 >= geom.map_y - u(1.0) \
                and y + h / 2 <= geom.bottom + u(1.0):
            text = _km_html(n)
            _label(layout, f"{p}n{i}l", text, safe, y0 + y - h / 2, lane_w, h, page=page,
                   align=Qt.AlignRight)
            _label(layout, f"{p}n{i}r", text, geom.right + u(0.5), y0 + y - h / 2, lane_w, h,
                   page=page, align=Qt.AlignLeft)
        n += spacing
        i += 1


def locator_window(sheets: list[dict], current: dict, max_cells: int = 8):
    """Rows/cols shown in the locator: the whole selection, or a window around the sheet."""
    rows = [int(s["row"]) for s in sheets]
    cols = [int(s["col"]) for s in sheets]
    r0, r1, c0, c1 = min(rows), max(rows), min(cols), max(cols)
    cr, cc = int(current["row"]), int(current["col"])
    if r1 - r0 + 1 > max_cells:
        r0 = max(r0, min(cr - max_cells // 2, r1 - max_cells + 1))
        r1 = r0 + max_cells - 1
    if c1 - c0 + 1 > max_cells:
        c0 = max(c0, min(cc - max_cells // 2, c1 - max_cells + 1))
        c1 = c0 + max_cells - 1
    return r0, r1, c0, c1


def _locator_image(sheets: list[dict], current: dict, aspect: float, mirror: bool) -> QImage:
    r0, r1, c0, c1 = locator_window(sheets, current)
    selected = {(int(s["row"]), int(s["col"])) for s in sheets}
    cw = 40
    ch = max(10, int(round(cw / aspect)))
    ncols, nrows = c1 - c0 + 1, r1 - r0 + 1
    image = QImage(ncols * cw + 4, nrows * ch + 4, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    for row in range(r1, r0 - 1, -1):  # north at the top
        for col in range(c0, c1 + 1):
            x = 2 + (col - c0) * cw
            y = 2 + (r1 - row) * ch
            if (row, col) == (int(current["row"]), int(current["col"])):
                fill, pen = QColor(25, 70, 140), QColor(25, 70, 140)
            elif (row, col) in selected:
                fill, pen = QColor(205, 222, 245), QColor(120, 140, 170)
            else:
                continue
            painter.setPen(QPen(pen, 2))
            painter.setBrush(fill)
            painter.drawRect(QRectF(x + 1, y + 1, cw - 2, ch - 2))
    painter.end()
    return image.mirrored(True, False) if mirror else image


def update_side(
    layout: QgsPrintLayout,
    side: str,
    sheet: dict,
    image_path: Path,
    neighbour: dict,
    source_text: str,
    info: dict,
    slot: Slot | None = None,
) -> list[Path]:
    """Fill one side of one sheet. Returns temporary files to delete after the PDF export.

    ``info`` keys: extent (rendered QgsRectangle), sheets (all exported sheets, for the
    locator), convergence, grid_magnetic (or None), year, date, run.
    ``slot`` defaults to the side's page in the two-page sheet layout.
    """
    geom = config.page_geometry()
    _use_scale(geom)
    slot = slot or default_slot(side)
    p = slot.prefix
    extent = info["extent"]

    picture = layout.itemById(p + "map_picture")
    picture.setPicturePath(str(image_path))
    picture.refreshPicture()

    layout.itemById(p + "title").setText(_title_html(sheet, side))
    layout.itemById(p + "north").setText(_north_html(side, info))
    layout.itemById(p + "coord").setText(_coord_html(sheet, extent))
    layout.itemById(p + "source").setText(_source_html(source_text, info["date"]))
    layout.itemById(p + "neighbor_top").setText(_neighbour_html(neighbour.get("north"), "▲"))
    layout.itemById(p + "neighbor_bottom").setText(_neighbour_html(neighbour.get("south"), "▼"))
    # Side labels are rotated to read upwards: ▲ then points left, ▼ points right.
    # The back is mirrored horizontally, so east and west swap sides there.
    west, east = neighbour.get("west"), neighbour.get("east")
    left, right = (west, east) if side == "front" else (east, west)
    layout.itemById(p + "neighbor_left").setText(_neighbour_html(left, "▲"))
    layout.itemById(p + "neighbor_right").setText(_neighbour_html(right, "▼"))

    _add_km_labels(layout, slot, extent, geom)

    # Locator diagram, right-aligned in its reserved box.
    aspect = geom.cell_w / geom.cell_h
    image = _locator_image(info["sheets"], sheet, aspect, mirror=(side == "back"))
    # Unique name per export run: QGIS caches pictures by path, so a reused name
    # could show a stale image.
    path = config.cache_dir() / f"locator_{info.get('run', '')}{sheet['sheet_id']}_{side}.png"
    image.save(str(path))
    h = u(_LOCATOR_H)
    w = h * image.width() / image.height()
    if w > u(_LOCATOR_MAX_W):
        w, h = u(_LOCATOR_MAX_W), u(_LOCATOR_MAX_W) * image.height() / image.width()
    locator = layout.itemById(p + "locator")
    locator.setPicturePath(str(path))
    locator.refreshPicture()
    locator.attemptResize(QgsLayoutSize(w, h, _MM))
    locator.attemptMove(
        QgsLayoutPoint(geom.right - w, slot.y0 + geom.bottom + u(2.4) + (u(_LOCATOR_H) - h) / 2,
                       _MM), True, False, slot.page)
    return [path]


def today() -> str:
    return _dt.date.today().isoformat()


def export_pdf(layout: QgsPrintLayout, path: Path, dpi: int = config.DPI) -> Path:
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = dpi
    settings.rasterizeWholeImage = False
    settings.exportMetadata = True
    settings.appendGeoreferencing = False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = exporter.exportToPdf(str(path), settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"PDF export failed ({result}): {path}")
    return path


# ---------------------------------------------------------------------------
# overview sheet
# ---------------------------------------------------------------------------
def overview_cells(sheets: list[dict]) -> list[tuple[int, int, dict | None]]:
    """(x index, y index, sheet or None) for every cell, y = 0 is the northernmost row."""
    rows = [int(s["row"]) for s in sheets]
    cols = [int(s["col"]) for s in sheets]
    by_key = {(int(s["row"]), int(s["col"])): s for s in sheets}
    cells = []
    for row in range(max(rows), min(rows) - 1, -1):  # rows grow northwards
        for col in range(min(cols), max(cols) + 1):
            cells.append((col - min(cols), max(rows) - row, by_key.get((row, col))))
    return cells


def _overview_image(sheets: list[dict], aspect: float) -> tuple[QImage, int, int]:
    """Diagram of the selected sheets; ``aspect`` = sheet width / height."""
    cells = overview_cells(sheets)
    grid_cols = max(c[0] for c in cells) + 1
    grid_rows = max(c[1] for c in cells) + 1

    cell_w = 240
    cell_h = max(40, int(round(cell_w / aspect)))
    pad = 6
    image = QImage(grid_cols * cell_w + pad * 2, grid_rows * cell_h + pad * 2,
                   QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    base = min(cell_w, cell_h)
    num_font = QFont()
    num_font.setPixelSize(int(base * 0.34))
    num_font.setBold(True)
    id_font = QFont()
    id_font.setPixelSize(int(base * 0.12))

    for xi, yi, sheet in cells:
        x = pad + xi * cell_w
        y = pad + yi * cell_h
        rect = QRect(x, y, cell_w - 1, cell_h - 1)
        if sheet is None:
            painter.setPen(QPen(QColor(212, 212, 212)))
            painter.setBrush(QColor(246, 246, 246))
            painter.drawRect(rect)
            continue
        painter.setPen(QPen(QColor(90, 90, 90)))
        painter.setBrush(QColor(232, 242, 255))
        painter.drawRect(rect)
        painter.setFont(num_font)
        painter.setPen(QColor(15, 60, 120))
        painter.drawText(QRect(x, y, cell_w, int(cell_h * 0.58)), Qt.AlignCenter,
                         sheet_number_text(sheet, "?"))
        painter.setFont(id_font)
        painter.setPen(QColor(80, 80, 80))
        painter.drawText(QRect(x, y + int(cell_h * 0.52), cell_w, int(cell_h * 0.42)),
                         Qt.AlignCenter, str(sheet["sheet_id"]))
    painter.end()
    return image, grid_rows, grid_cols


def _overview_legend_html(sheets: list[dict], columns: int = 4) -> str:
    ordered = sorted(
        sheets,
        key=lambda s: (
            s["sheet_no"] if s["sheet_no"] not in (None, "") else 10 ** 9,
            -s["n_max"],
            s["e_min"],
        ),
    )
    items = []
    for sheet in ordered:
        line = f"<b>{sheet_number_text(sheet, '?')}</b> &nbsp;{html.escape(str(sheet['sheet_id']))}"
        if sheet.get("sheet_name"):
            line += f" &nbsp;{html.escape(str(sheet['sheet_name']))}"
        items.append(line)

    cols: list[list[str]] = [[] for _ in range(columns)]
    for index, item in enumerate(items):
        cols[index % columns].append(item)

    body = "<table width='100%' cellspacing='0' cellpadding='2'><tr>"
    for col in cols:
        body += (f"<td valign='top' style='font-size:{6 * _K:.2f}pt'>" + "<br/>".join(col)
                 + "</td>")
    return body + "</tr></table>"


def export_overview(
    project: QgsProject,
    sheets: list[dict],
    out_path: Path,
    dpi: int = config.DPI,
) -> Path:
    geom = config.page_geometry()
    _use_scale(geom)
    aspect = geom.cell_w / geom.cell_h
    image, grid_rows, grid_cols = _overview_image(sheets, aspect)
    zone = crs_mod.zone_label(int(sheets[0]["utm_zone"]), str(sheets[0]["hemisphere"]))
    margin = geom.margin_side  # keeps everything inside the printer-safe zone

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize(QgsLayoutSize(geom.page_w, geom.page_h, _MM))

    overlap = f" · {geom.overlap} % overlap" if geom.overlap else ""
    _label(
        layout, "overview_title",
        _span("Sheet overview", 12, bold=True)
        + _span(f" · {len(sheets)} sheets · {config.format_scale(geom.scale)} · UTM {zone}"
                f"{overlap} · north up", 8, color=GREY),
        margin, margin, geom.page_w - 2 * margin, u(8.0), align=Qt.AlignLeft,
    )

    avail_w = geom.page_w - 2 * margin
    avail_h = (geom.page_h - 2 * margin) * 0.55
    cell_h_mm = min(avail_w / (grid_cols * aspect), avail_h / grid_rows, 18.0)
    diag_w = cell_h_mm * aspect * grid_cols
    diag_h = cell_h_mm * grid_rows
    diag_x = (geom.page_w - diag_w) / 2.0
    diag_y = margin + u(10.0)

    tmp = config.cache_dir() / f"overview_{uuid.uuid4().hex[:8]}.png"  # unique: QGIS caches by path
    image.save(str(tmp))

    picture = QgsLayoutItemPicture(layout)
    picture.setId("overview_diagram")
    picture.setResizeMode(QgsLayoutItemPicture.Stretch)
    layout.addLayoutItem(picture)
    picture.attemptResize(QgsLayoutSize(diag_w, diag_h, _MM))
    picture.attemptMove(QgsLayoutPoint(diag_x, diag_y, _MM))
    picture.setPicturePath(str(tmp))

    legend_y = diag_y + diag_h + 3.0
    _label(
        layout, "overview_legend", _overview_legend_html(sheets),
        margin, legend_y, geom.page_w - 2 * margin, (geom.page_h - margin - u(3.0)) - legend_y,
        align=Qt.AlignLeft, valign=Qt.AlignTop,
    )
    _label(layout, "overview_date", _span(f"Printed {today()}", 5, color=GREY),
           margin, geom.page_h - margin - u(2.5), u(60), u(2.5), align=Qt.AlignLeft)

    try:
        return export_pdf(layout, Path(out_path), dpi)
    finally:
        tmp.unlink(missing_ok=True)
