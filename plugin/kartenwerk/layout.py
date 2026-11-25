"""Print layout (page 1: front topo, page 2: mirrored aerial back) and the overview sheet.

The map itself is kept free of text. The white border carries, inside the
printer margin (config.safe_mm() from the paper edge):

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

from qgis.PyQt.QtCore import QPointF, QRect, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
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
LAYOUT_VERSION = "6"  # bump when the border design changes, so saved layouts are rebuilt
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
    """Where one side of one sheet goes.

    page: page index; y0: vertical offset on that page (two sheets per page);
    dx, dy: duplex offset applied to all of this side's items (back only);
    prefix: item-id prefix.
    """

    def __init__(self, side: str, page: int, y0: float = 0.0, prefix: str | None = None,
                 dx: float = 0.0, dy: float = 0.0):
        self.side = side
        self.page = page
        self.y0 = y0
        self.prefix = prefix if prefix is not None else f"{side}_"
        self.dx = dx
        self.dy = dy


def default_slot(side: str, offset: tuple[float, float] | None = None) -> Slot:
    if side == "back":
        dx, dy = offset if offset is not None else config.duplex_offset(two_up=False)
        return Slot(side, SIDES.index(side), dx=dx, dy=dy)
    return Slot(side, SIDES.index(side))


def _shift(layout: QgsPrintLayout, prefix: str, dx: float, dy: float) -> None:
    """Move every item whose id starts with ``prefix`` by (dx, dy) mm (duplex offset)."""
    if not dx and not dy:
        return
    for item in list(layout.items()):
        item_id = getattr(item, "id", None)
        if callable(item_id) and item.id().startswith(prefix):
            pos = item.positionWithUnits()
            item.attemptMove(QgsLayoutPoint(pos.x() + dx, pos.y() + dy, pos.units()))


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
        _rect(layout, f"{p}sb_seg{i}", x0 + i * seg, bottom + u(2.3), seg, u(1.1), page,
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
               x, bottom + u(0.4), u(16.0), u(1.8), page=page, align=align)
    _label(layout, f"{p}scale", _span(config.format_scale(geom.scale), 7.5, bold=True),
           x0 + length + u(8.0), bottom + u(0.8), u(22.0), u(3.2), page=page, align=Qt.AlignLeft)


def _add_side(layout: QgsPrintLayout, slot: Slot, geom: config.PageGeometry) -> None:
    p, page, y0 = slot.prefix, slot.page, slot.y0
    safe = config.safe_mm()
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
    _label(layout, p + "neighbor_bottom", "", geom.page_w / 2 - u(20), bottom + u(0.5), u(40),
           u(3.8), page=page)
    coord_right = geom.right - u(_LOCATOR_MAX_W) - u(2.0)
    # Row B right: UTM zone + MGRS square; row C: attribution left, SW corner right.
    _label(layout, p + "coord", "", coord_right - u(40), bottom + u(0.7), u(40), u(2.8),
           page=page, align=Qt.AlignRight)
    _label(layout, p + "corner", "", coord_right - u(50), bottom + u(4.5), u(50), u(2.3),
           page=page, align=Qt.AlignRight)
    _label(layout, p + "source", "", geom.map_x, bottom + u(4.5),
           coord_right - u(52) - geom.map_x, u(2.3), page=page, align=Qt.AlignLeft)

    locator = QgsLayoutItemPicture(layout)
    locator.setId(p + "locator")
    locator.setResizeMode(QgsLayoutItemPicture.Stretch)
    layout.addLayoutItem(locator)
    locator.attemptResize(QgsLayoutSize(u(_LOCATOR_H), u(_LOCATOR_H), _MM))
    locator.attemptMove(QgsLayoutPoint(geom.right - u(_LOCATOR_H), bottom + u(0.3), _MM),
                        True, False, page)
    _shift(layout, p, slot.dx, slot.dy)


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
    dx, dy = config.duplex_offset(two_up=False)
    return f"{LAYOUT_VERSION}|{config.page_geometry().signature()}|{dx:.2f}|{dy:.2f}"


def _is_current(layout: QgsPrintLayout) -> bool:
    return (
        layout.customProperty(_PROP) == _signature()
        and layout.pageCollection().pageCount() == 2
        and all(layout.itemById(f"{side}_corner") is not None for side in SIDES)
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
def two_up_layout(project: QgsProject, count: int,
                  offset: tuple[float, float] | None = None,
                  ) -> tuple[QgsPrintLayout, list[tuple[Slot, Slot]]]:
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
    dx, dy = config.duplex_offset(two_up=True) if offset is None else offset
    slots = []
    for i in range(count):
        pair, pos = divmod(i, 2)
        front = Slot("front", 2 * pair, offsets[pos], f"s{i}f_")
        back = Slot("back", 2 * pair + 1, offsets[pos], f"s{i}b_", dx=dx, dy=dy)
        _add_side(layout, front, geom)
        _add_side(layout, back, geom)
        slots.append((front, back))

    # Cut marks on the front pages, inside the printable area at both paper edges.
    mark = u(6.0)
    for pair in range(pairs):
        for x in (config.safe_mm(), paper_w - config.safe_mm() - mark):
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


def _title_html(sheet: dict, side: str, mirrored: bool | None = None) -> str:
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
        mirrored = True if mirrored is None else mirrored
        note = "aerial image, mirrored for duplex" if mirrored else "aerial image"
        parts.append(_span(f"&nbsp;· {note}", 6.5, color=GREY))
    return "".join(parts)


def _angle_text(value: float) -> str:
    return f"{abs(value):.1f}° {'E' if value >= 0 else 'W'}"


def _north_html(side: str, info: dict, mirrored: bool = False) -> str:
    """North arrow with true/magnetic north; only the arrow on a mirrored page."""
    arrow = _span("▲ N", 10, bold=True)
    if mirrored:  # angles measured on a mirrored image would point the wrong way
        return arrow
    parts = [f"true N {_angle_text(-info['convergence'])}"]
    if info.get("grid_magnetic") is not None:
        parts.append(f"magnetic N {_angle_text(info['grid_magnetic'])} ({info['year']})")
    small = _span("Grid north ↑ · " + " · ".join(parts) + "&nbsp;&nbsp;", 5, color=GREY)
    return small + arrow


def _coord_html(sheet: dict, info: dict) -> str:
    zone = crs_mod.zone_label(int(sheet["utm_zone"]), str(sheet["hemisphere"]))
    html_ = _span(f"UTM {zone}", 6.5, bold=True)
    if info.get("mgrs"):
        html_ += _span(" · MGRS ", 6, color=GREY) + _span(html.escape(info["mgrs"]), 6.5, bold=True)
    return html_


def _corner_html(extent) -> str:
    f = config.format_coord
    return _span(f"SW corner E {f(extent.xMinimum())} · N {f(extent.yMinimum())}", 5.5,
                 color=GREY)


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


def _add_km_labels(layout, slot: Slot, extent, geom: config.PageGeometry,
                   mirrored: bool = False) -> None:
    """Kilometre labels in the border at every 1 km grid line (rebuilt per sheet)."""
    p = f"{slot.prefix}km_"
    page, y0 = slot.page, slot.y0
    _remove_items(layout, p)
    safe = config.safe_mm()
    spacing = config.GRID_SPACING_M
    sx = geom.map_w / extent.width()
    sy = geom.map_h / extent.height()

    e = math.ceil(extent.xMinimum() / spacing) * spacing
    i = 0
    while e <= extent.xMaximum():
        x = geom.map_x + (e - extent.xMinimum()) * sx
        if mirrored:  # the mirrored back image runs right↔left
            x = geom.map_x + geom.map_w - (x - geom.map_x)
        w = u(8.0)
        if x - w / 2 >= safe and x + w / 2 <= geom.page_w - safe:
            text = _km_html(e)
            # Eastings are labelled along the top only (the bottom row holds the scale bar).
            _label(layout, f"{p}e{i}t", text, x - w / 2,
                   y0 + geom.map_y - u(_KM_ROW_H) - u(0.2), w, u(_KM_ROW_H), page=page)
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
                and y + h / 2 <= geom.bottom + u(0.2):
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


def locator_hints(sheets: list[dict], current: dict) -> set[str]:
    """Sides ('north', 'south', 'west', 'east') where the series continues beyond the window."""
    r0, r1, c0, c1 = locator_window(sheets, current)
    rows = [int(s["row"]) for s in sheets]
    cols = [int(s["col"]) for s in sheets]
    hints = set()
    if r1 < max(rows):
        hints.add("north")
    if r0 > min(rows):
        hints.add("south")
    if c0 > min(cols):
        hints.add("west")
    if c1 < max(cols):
        hints.add("east")
    return hints


def _locator_image(sheets: list[dict], current: dict, aspect: float, mirror: bool) -> QImage:
    r0, r1, c0, c1 = locator_window(sheets, current)
    hints = locator_hints(sheets, current)
    selected = {(int(s["row"]), int(s["col"])) for s in sheets}
    cw = 40
    ch = max(10, int(round(cw / aspect)))
    ncols, nrows = c1 - c0 + 1, r1 - r0 + 1
    pad = 24 if hints else 2  # room for the "series continues" marks
    gw, gh = ncols * cw, nrows * ch
    image = QImage(gw + 2 * pad, gh + 2 * pad, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    for row in range(r1, r0 - 1, -1):  # north at the top
        for col in range(c0, c1 + 1):
            x = pad + (col - c0) * cw
            y = pad + (r1 - row) * ch
            if (row, col) == (int(current["row"]), int(current["col"])):
                fill, pen = QColor(25, 70, 140), QColor(25, 70, 140)
            elif (row, col) in selected:
                fill, pen = QColor(205, 222, 245), QColor(120, 140, 170)
            else:
                continue
            painter.setPen(QPen(pen, 2))
            painter.setBrush(fill)
            painter.drawRect(QRectF(x + 1, y + 1, cw - 2, ch - 2))
    # Small grey triangles where the series continues beyond the window.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(110, 110, 110))
    t = 16.0
    mx, my = pad + gw / 2.0, pad + gh / 2.0
    marks = {
        "north": [(mx - t, pad - 3), (mx + t, pad - 3), (mx, pad - 3 - t)],
        "south": [(mx - t, pad + gh + 3), (mx + t, pad + gh + 3), (mx, pad + gh + 3 + t)],
        "west": [(pad - 3, my - t), (pad - 3, my + t), (pad - 3 - t, my)],
        "east": [(pad + gw + 3, my - t), (pad + gw + 3, my + t), (pad + gw + 3 + t, my)],
    }
    for side in hints:
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in marks[side]]))
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

    # A mirrored back (default) lines up with the front when held against the light.
    mirrored = side == "back" and bool(info.get("mirror_back", True))
    layout.itemById(p + "title").setText(_title_html(sheet, side, mirrored))
    layout.itemById(p + "north").setText(_north_html(side, info, mirrored))
    layout.itemById(p + "coord").setText(_coord_html(sheet, info))
    layout.itemById(p + "corner").setText(_corner_html(extent))
    layout.itemById(p + "source").setText(_source_html(source_text, info["date"]))
    layout.itemById(p + "neighbor_top").setText(_neighbour_html(neighbour.get("north"), "▲"))
    layout.itemById(p + "neighbor_bottom").setText(_neighbour_html(neighbour.get("south"), "▼"))
    # Side labels are rotated to read upwards: ▲ then points left, ▼ points right.
    # On a mirrored back east and west swap sides.
    west, east = neighbour.get("west"), neighbour.get("east")
    left, right = (east, west) if mirrored else (west, east)
    layout.itemById(p + "neighbor_left").setText(_neighbour_html(left, "▲"))
    layout.itemById(p + "neighbor_right").setText(_neighbour_html(right, "▼"))

    _add_km_labels(layout, slot, extent, geom, mirrored)
    _shift(layout, f"{p}km_", slot.dx, slot.dy)

    # Locator diagram, right-aligned in its reserved box.
    aspect = geom.cell_w / geom.cell_h
    image = _locator_image(info["sheets"], sheet, aspect, mirror=mirrored)
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
        QgsLayoutPoint(geom.right - w, slot.y0 + geom.bottom + u(0.3) + (u(_LOCATOR_H) - h) / 2,
                       _MM), True, False, slot.page)
    _shift(layout, p + "locator", slot.dx, slot.dy)
    return [path]


# ---------------------------------------------------------------------------
# duplex test pages
# ---------------------------------------------------------------------------
def _calibration_pages(layout: QgsPrintLayout, w: float, h: float, page0: int, title: str) -> None:
    """Front: a cross at the paper centre and a 100 mm scale check. Back: mm rulers."""
    cx, cy = w / 2.0, h / 2.0
    safe = config.safe_mm() + 2.0
    front, back = page0, page0 + 1
    # ---- front
    _label(layout, f"cal{front}_title", _span(f"Duplex test – FRONT · {title}", 11, bold=True),
           safe, safe, w - 2 * safe, 6.0, page=front, align=Qt.AlignLeft)
    _label(layout, f"cal{front}_text", _span(
        "1. Print this file double-sided at actual size (100 %), with the same duplex setting "
        "you use for the maps. 2. The bar below must measure exactly 100 mm. "
        "3. Turn the page over and follow the instructions on the back.", 7),
        safe, safe + 7.0, w - 2 * safe, 12.0, page=front, align=Qt.AlignLeft, valign=Qt.AlignTop)
    _rect(layout, f"cal{front}_h", cx - 15.0, cy - 0.15, 30.0, 0.3, front, "black", stroke=0)
    _rect(layout, f"cal{front}_v", cx - 0.15, cy - 15.0, 0.3, 30.0, front, "black", stroke=0)
    bar_y = min(h - safe - 8.0, cy + 25.0)
    _rect(layout, f"cal{front}_bar", cx - 50.0, bar_y, 100.0, 1.2, front, "black", stroke=0)
    _label(layout, f"cal{front}_barlbl", _span("100 mm", 7), cx - 20.0, bar_y + 1.6, 40.0, 3.0,
           page=front)
    # ---- back: two rulers next to the centre, read from this side. The front's
    # cross has 15 mm arms, which reach both rulers when seen through the paper.
    _label(layout, f"cal{back}_title", _span(
        "Duplex test – BACK · look at THIS side, held against a window", 11, bold=True),
        safe, safe, w - 2 * safe, 6.0, page=back, align=Qt.AlignLeft)
    _label(layout, f"cal{back}_text", _span(
        "The cross from the front shows through. Read where its vertical line meets the "
        "LOWER ruler (+ = right) and where its horizontal line meets the RIGHT ruler "
        "(+ = down), in mm. Enter both values in Settings → Printing → Duplex offset. "
        "The values replace the old ones (this test page is always printed without offset). "
        "Both on 0: no correction needed.", 7),
        safe, safe + 7.0, w - 2 * safe, 16.0, page=back, align=Qt.AlignLeft, valign=Qt.AlignTop)
    ruler_y = cy + 8.0  # horizontal ruler (reads dx), ticks pointing down
    ruler_x = cx + 8.0  # vertical ruler (reads dy), ticks pointing right
    _rect(layout, f"cal{back}_hbase", cx - 5.5, ruler_y - 0.06, 11.0, 0.12, back, "black", stroke=0)
    _rect(layout, f"cal{back}_vbase", ruler_x - 0.06, cy - 5.5, 0.12, 11.0, back, "black", stroke=0)
    for i in range(-10, 11):  # 0.5 mm ticks from -5 to +5 mm
        mm = i / 2.0
        length = 3.0 if i % 10 == 0 else 2.0 if i % 2 == 0 else 1.0
        _rect(layout, f"cal{back}_tx{i}", cx + mm - 0.05, ruler_y, 0.1, length, back,
              "black", stroke=0)
        _rect(layout, f"cal{back}_ty{i}", ruler_x, cy + mm - 0.05, length, 0.1, back,
              "black", stroke=0)
        if i % 4 == 0:  # labels every 2 mm; on the lower ruler in two staggered rows
            text = "0" if i == 0 else f"{mm:+g}"
            row = 0.0 if i % 8 == 0 else 2.4
            _label(layout, f"cal{back}_lx{i}", _span(text, 5), cx + mm - 2.0, ruler_y + 3.3 + row,
                   4.0, 2.2, page=back)
            _label(layout, f"cal{back}_ly{i}", _span(text, 5), ruler_x + 3.4, cy + mm - 1.1, 4.0,
                   2.2, page=back, align=Qt.AlignLeft)
    _label(layout, f"cal{back}_arrx", _span("+ → (right)", 6, bold=True), cx + 6.5, ruler_y + 0.2,
           16.0, 2.6, page=back, align=Qt.AlignLeft)
    _label(layout, f"cal{back}_arry", _span("+ ↓ (down)", 6, bold=True), ruler_x + 8.5,
           cy + 1.6, 16.0, 2.6, page=back, align=Qt.AlignLeft)
    _label(layout, f"cal{back}_nameh", _span("reads RIGHT/LEFT", 5, color=GREY), cx - 15.0,
           ruler_y + 8.6, 30.0, 2.2, page=back)
    _label(layout, f"cal{back}_namev", _span("reads DOWN/UP", 5, color=GREY), ruler_x + 8.5,
           cy - 1.1, 20.0, 2.2, page=back, align=Qt.AlignLeft)


def export_duplex_test(project: QgsProject, out_dir: Path) -> list[Path]:
    """Duplex test PDFs for the current page format (and its two-per-page paper).

    Always printed WITHOUT duplex offset: the reading replaces the stored offset.
    """
    global _K
    geom = config.page_geometry()
    _K = 1.0
    out_dir = Path(out_dir)
    jobs = [(geom.name, geom.page_w, geom.page_h, f"Duplex_test_{geom.name}.pdf",
             f"{geom.name} landscape, for the sheet PDFs")]
    paper = config.TWO_UP_PAPER.get(geom.name)
    if paper:
        paper_h, paper_w = config.PAGE_FORMATS[paper]
        jobs.append((paper, paper_w, paper_h, f"Duplex_test_2-per-page_{paper}.pdf",
                     f"{paper} portrait, for the two-per-page print file"))
    written = []
    for _, w, h, filename, title in jobs:
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        pages = layout.pageCollection()
        pages.page(0).setPageSize(QgsLayoutSize(w, h, _MM))
        back = QgsLayoutItemPage(layout)
        back.setPageSize(QgsLayoutSize(w, h, _MM))
        pages.addPage(back)
        _calibration_pages(layout, w, h, 0, title)
        written.append(export_pdf(layout, out_dir / filename, 300))
    return written


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


DEFAULT_OVERVIEW_COLOR = "#c8005a"


def overview_diagram_size(sheets: list[dict], geom: config.PageGeometry) -> tuple[int, int, float, float]:
    """(rows, cols, width mm, height mm) of the overview diagram / map on the overview page.

    The diagram keeps the true sheet proportions, uses the page width and 55 % of the
    page height, with at most 18 mm per sheet row.
    """
    cells = overview_cells(sheets)
    cols = max(c[0] for c in cells) + 1
    rows = max(c[1] for c in cells) + 1
    aspect = geom.cell_w / geom.cell_h
    margin = geom.margin_side
    avail_w = geom.page_w - 2 * margin
    avail_h = (geom.page_h - 2 * margin) * 0.55
    cell_h_mm = min(avail_w / (cols * aspect), avail_h / rows, 18.0)
    return rows, cols, cell_h_mm * aspect * cols, cell_h_mm * rows


def _halo_text(painter: QPainter, rect: QRectF, text: str, font: QFont, color: QColor) -> None:
    """Centered text in ``color`` with a white halo, readable on any map."""
    from qgis.PyQt.QtGui import QFontMetricsF, QPainterPath

    metrics = QFontMetricsF(font)
    width = metrics.horizontalAdvance(text) if hasattr(metrics, "horizontalAdvance") \
        else metrics.width(text)
    x = rect.x() + (rect.width() - width) / 2.0
    y = rect.y() + (rect.height() + metrics.ascent() - metrics.descent()) / 2.0
    path = QPainterPath()
    path.addText(QPointF(x, y), font, text)
    halo = max(2.0, font.pixelSize() * 0.18)
    painter.strokePath(path, QPen(QColor(255, 255, 255, 230), halo, Qt.SolidLine, Qt.RoundCap,
                                  Qt.RoundJoin))
    painter.fillPath(path, color)


def _overview_image(sheets: list[dict], aspect: float, background: QImage | None = None,
                    color: str = DEFAULT_OVERVIEW_COLOR) -> tuple[QImage, int, int]:
    """Diagram of the selected sheets; ``aspect`` = sheet width / height.

    With ``background`` (a map of exactly the rows × cols block, north up) the sheets are
    drawn on top of it: other cells veiled, exported sheets outlined in ``color`` with
    their number and id. Without it, the plain diagram.
    """
    cells = overview_cells(sheets)
    grid_cols = max(c[0] for c in cells) + 1
    grid_rows = max(c[1] for c in cells) + 1

    if background is not None:
        image = background.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        pad = 0.0
        cell_w = image.width() / grid_cols
        cell_h = image.height() / grid_rows
    else:
        cell_w = 240.0
        cell_h = float(max(40, int(round(cell_w / aspect))))
        pad = 6.0
        image = QImage(int(grid_cols * cell_w + pad * 2), int(grid_rows * cell_h + pad * 2),
                       QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    base = min(cell_w, cell_h)
    num_font = QFont()
    num_font.setPixelSize(max(6, int(base * 0.34)))
    num_font.setBold(True)
    id_font = QFont()
    id_font.setPixelSize(max(4, int(base * 0.12)))
    accent = QColor(color) if QColor(color).isValid() else QColor(DEFAULT_OVERVIEW_COLOR)
    line = max(1.5, base * 0.025)

    for xi, yi, sheet in cells:
        rect = QRectF(pad + xi * cell_w, pad + yi * cell_h, cell_w, cell_h)
        if background is not None:
            if sheet is None:
                painter.fillRect(rect, QColor(255, 255, 255, 140))  # veil: not exported
                continue
            painter.setPen(QPen(accent, line))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(line / 2, line / 2, -line / 2, -line / 2))
            _halo_text(painter, QRectF(rect.x(), rect.y(), rect.width(), rect.height() * 0.58),
                       sheet_number_text(sheet, "?"), num_font, accent)
            _halo_text(painter, QRectF(rect.x(), rect.y() + rect.height() * 0.52, rect.width(),
                                       rect.height() * 0.42), str(sheet["sheet_id"]), id_font,
                       accent)
            continue
        box = rect.adjusted(0, 0, -1, -1)
        if sheet is None:
            painter.setPen(QPen(QColor(212, 212, 212)))
            painter.setBrush(QColor(246, 246, 246))
            painter.drawRect(box)
            continue
        painter.setPen(QPen(QColor(90, 90, 90)))
        painter.setBrush(QColor(232, 242, 255))
        painter.drawRect(box)
        painter.setFont(num_font)
        painter.setPen(QColor(15, 60, 120))
        painter.drawText(QRectF(rect.x(), rect.y(), cell_w, cell_h * 0.58), Qt.AlignCenter,
                         sheet_number_text(sheet, "?"))
        painter.setFont(id_font)
        painter.setPen(QColor(80, 80, 80))
        painter.drawText(QRectF(rect.x(), rect.y() + cell_h * 0.52, cell_w, cell_h * 0.42),
                         Qt.AlignCenter, str(sheet["sheet_id"]))
    if background is not None:  # thin frame around the whole map
        painter.setPen(QPen(QColor(40, 40, 40), max(1.0, line * 0.5)))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(0.5, 0.5, image.width() - 1, image.height() - 1))
    painter.end()
    return image, grid_rows, grid_cols


def _legend_items(sheets: list[dict]) -> list[str]:
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
        line = f"<b>{sheet_number_text(sheet, '?')}</b>&nbsp;{html.escape(str(sheet['sheet_id']))}"
        if sheet.get("sheet_name"):
            line += f"&nbsp;{html.escape(str(sheet['sheet_name']))}"
        items.append(line)
    return items


def _legend_text_length(sheet: dict) -> int:
    length = len(sheet_number_text(sheet, "?")) + 1 + len(str(sheet["sheet_id"]))
    if sheet.get("sheet_name"):
        length += 1 + len(str(sheet["sheet_name"]))
    return length


def legend_layout(sheets: list[dict], width: float, first_h: float, next_h: float):
    """Fit the sheet list: (font pt, columns, rows on the first page, rows on later pages).

    Tries smaller fonts and more columns until the list fits on the first page; with
    very large series it continues on extra pages at the smallest size.
    """
    longest = max(_legend_text_length(s) for s in sheets)
    for size in (6.0, 5.5, 5.0, 4.5, 4.0):
        pt = size * _K
        line_h = pt * 0.3528 * 1.5  # mm per text line (QGIS HTML labels need ~1.5 × font size)
        col_w = longest * pt * 0.3528 * 0.62 + 3.0  # mm per column incl. gap
        columns = max(1, int(width // col_w))
        rows_first = max(1, int(first_h // line_h))
        rows_next = max(1, int(next_h // line_h))
        if columns * rows_first >= len(sheets) or size == 4.0:
            return pt, columns, rows_first, rows_next
    raise AssertionError("unreachable")


def _legend_html(items: list[str], columns: int, rows: int, pt: float) -> str:
    """Items in reading order down the columns."""
    cols = [items[c * rows:(c + 1) * rows] for c in range(columns)]
    body = "<table width='100%' cellspacing='0' cellpadding='0'><tr>"
    for col in cols:
        body += (f"<td valign='top' style='font-size:{pt:.2f}pt'>" + "<br/>".join(col)
                 + "</td>")
    return body + "</tr></table>"


def export_overview(
    project: QgsProject,
    sheets: list[dict],
    out_path: Path,
    dpi: int = config.DPI,
    background: QImage | None = None,
    attribution: str = "",
    color: str = DEFAULT_OVERVIEW_COLOR,
) -> Path:
    """Overview page; ``background`` is an optional map of the sheet block (north up)."""
    geom = config.page_geometry()
    _use_scale(geom)
    aspect = geom.cell_w / geom.cell_h
    image, grid_rows, grid_cols = _overview_image(sheets, aspect, background, color)
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

    _, _, diag_w, diag_h = overview_diagram_size(sheets, geom)
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
    if background is not None and attribution:
        _label(layout, "overview_attribution",
               _span(f"Overview map: {html.escape(attribution)}", 5, color=GREY),
               margin, diag_y + diag_h + 0.6, geom.page_w - 2 * margin, u(2.4),
               align=Qt.AlignCenter)
        legend_y += u(2.4)
    # Sheet list: shrinks and adds columns to fit; very large series continue on extra pages.
    width = geom.page_w - 2 * margin
    first_h = (geom.page_h - margin - u(5.5)) - legend_y
    next_top = margin + u(10.0)
    next_h = (geom.page_h - margin - u(5.5)) - next_top
    pt, columns, rows_first, rows_next = legend_layout(sheets, width, first_h, next_h)
    items = _legend_items(sheets)
    chunks = [items[:columns * rows_first]]
    rest = items[columns * rows_first:]
    while rest:
        chunks.append(rest[:columns * rows_next])
        rest = rest[columns * rows_next:]
    for page_index, chunk in enumerate(chunks):
        if page_index == 0:
            y, rows = legend_y, rows_first
        else:
            page = QgsLayoutItemPage(layout)
            page.setPageSize(QgsLayoutSize(geom.page_w, geom.page_h, _MM))
            layout.pageCollection().addPage(page)
            _label(layout, f"overview_title_{page_index}",
                   _span(f"Sheet overview – list continued ({page_index + 1}/{len(chunks)})", 10,
                         bold=True), margin, margin, width, u(8.0), page=page_index,
                   align=Qt.AlignLeft)
            y, rows = next_top, rows_next
        _label(layout, f"overview_legend_{page_index}" if page_index else "overview_legend",
               _legend_html(chunk, columns, min(rows, math.ceil(len(chunk) / columns)), pt),
               margin, y, width, (geom.page_h - margin - u(5.5)) - y, page=page_index,
               align=Qt.AlignLeft, valign=Qt.AlignTop)
    _label(layout, "overview_date", _span(f"Printed {today()}", 5, color=GREY),
           margin, geom.page_h - margin - u(2.5), u(60), u(2.5), align=Qt.AlignLeft)
    if geom.overlap:
        _label(layout, "overview_overlap", _span(
            f"Dashed red lines in the maps: where the neighbouring sheet begins. Sheets overlap "
            f"by {geom.overlap} %, so the strip outside a dashed line is printed on the "
            "neighbour too.", 5, color=GREY),
            margin + u(62), geom.page_h - margin - u(5.0), geom.page_w - 2 * margin - u(62),
            u(5.0), align=Qt.AlignRight, valign=Qt.AlignBottom)

    try:
        return export_pdf(layout, Path(out_path), dpi)
    finally:
        tmp.unlink(missing_ok=True)
