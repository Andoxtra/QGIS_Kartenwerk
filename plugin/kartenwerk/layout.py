"""Print layout templates (front topo / back mirrored aerial).

The map itself is kept free of text: title, scale, north arrow, corner
coordinate and the four neighbour indicators live in the white border.
"""
from __future__ import annotations

import html
from pathlib import Path

from qgis.PyQt.QtCore import QRect, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen
from qgis.core import (
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemPicture,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsUnitTypes,
)

from . import config

FRONT_LAYOUT = "Vorderseite"
BACK_LAYOUT = "Rueckseite"
_MM = QgsUnitTypes.LayoutMillimeters


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _label(
    layout: QgsPrintLayout,
    item_id: str,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    align=Qt.AlignCenter,
    valign=Qt.AlignVCenter,
    rotation: float = 0.0,
    font_size: int = 8,
    bold: bool = False,
) -> QgsLayoutItemLabel:
    label = QgsLayoutItemLabel(layout)
    label.setId(item_id)
    label.setMode(QgsLayoutItemLabel.ModeHtml)
    label.setText(text)
    label.setFont(QFont("DejaVu Sans", font_size, QFont.Bold if bold else QFont.Normal))
    label.setHAlign(align)
    label.setVAlign(valign)
    label.setMargin(0.3)
    label.attemptMove(QgsLayoutPoint(x, y, _MM))
    label.attemptResize(QgsLayoutSize(w, h, _MM))
    if rotation:
        label.setItemRotation(rotation, True)
    layout.addLayoutItem(label)
    return label


def _create_layout(project: QgsProject, name: str) -> QgsPrintLayout:
    geom = config.page_geometry()
    layout = QgsPrintLayout(project)
    layout.setName(name)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(geom.page_w, geom.page_h, _MM)
    )

    picture = QgsLayoutItemPicture(layout)
    picture.setId("map_picture")
    picture.setResizeMode(QgsLayoutItemPicture.Stretch)
    picture.attemptMove(QgsLayoutPoint(geom.map_x, geom.map_y, _MM))
    picture.attemptResize(QgsLayoutSize(geom.map_w, geom.map_h, _MM))
    layout.addLayoutItem(picture)

    title_w = max(60.0, geom.page_w * 0.27)
    top_w = max(85.0, geom.page_w * 0.40)
    bottom_w = max(80.0, geom.page_w * 0.38)
    scale_w = max(34.0, geom.page_w * 0.12)
    coord_w = max(60.0, geom.page_w * 0.25)
    side_w = 80.0

    # Top-left: sheet number, sheet id and map type.
    _label(
        layout, "title", "", 1.0, 1.0, title_w, 4.5,
        align=Qt.AlignLeft, font_size=8,
    )
    # Top-centre: north neighbour.
    _label(
        layout, "neighbor_top", "", geom.page_w / 2.0 - top_w / 2.0, 1.0, top_w, 4.5,
        font_size=8, bold=True,
    )
    # Top-right: north arrow in the border.
    _label(
        layout, "north",
        "<span style='font-size:10pt'>▲</span> "
        "<span style='font-size:8pt;font-weight:bold'>N</span>",
        geom.right - 12, 0.9, 16, 4.8, font_size=8,
    )
    # Bottom-left: scale as text.
    _label(
        layout, "scale",
        f"<span style='font-size:8pt;font-weight:bold'>{config.format_scale()}</span>",
        1.0, geom.bottom + 0.9, scale_w, 4.4, align=Qt.AlignLeft, font_size=8,
    )
    # Bottom-centre: south neighbour.
    _label(
        layout, "neighbor_bottom", "", geom.page_w / 2.0 - bottom_w / 2.0,
        geom.bottom + 0.9, bottom_w, 4.4,
        font_size=8, bold=True,
    )
    # Bottom-right: SW coordinate and attribution.
    _label(
        layout, "coord", "", geom.right - coord_w + 2, geom.bottom + 0.6, coord_w, 3.0,
        align=Qt.AlignRight, font_size=6,
    )
    _label(
        layout, "source", "", geom.right - coord_w + 2, geom.bottom + 3.4, coord_w, 2.4,
        align=Qt.AlignRight, font_size=4,
    )

    # Left/right: rotated neighbour indicators.
    _label(
        layout, "neighbor_left", "", geom.margin / 2.0 - side_w / 2.0,
        geom.page_h / 2.0 - 2.0, side_w, 4.0,
        rotation=90, font_size=8, bold=True,
    )
    _label(
        layout, "neighbor_right", "", geom.right + geom.margin / 2.0 - side_w / 2.0,
        geom.page_h / 2.0 - 2.0, side_w, 4.0,
        rotation=-90, font_size=8, bold=True,
    )

    project.layoutManager().addLayout(layout)
    return layout


def _is_current(layout: QgsPrintLayout) -> bool:
    geom = config.page_geometry()
    page = layout.pageCollection().page(0).pageSize()
    if abs(page.width() - geom.page_w) > 0.1 or abs(page.height() - geom.page_h) > 0.1:
        return False
    scale_item = layout.itemById("scale")
    if scale_item is None or config.format_scale() not in scale_item.text():
        return False
    return layout.itemById("source") is not None


def ensure_layouts(project: QgsProject, force: bool = False) -> tuple[QgsPrintLayout, QgsPrintLayout]:
    manager = project.layoutManager()
    if force:
        for name in (FRONT_LAYOUT, BACK_LAYOUT):
            existing = manager.layoutByName(name)
            if existing is not None:
                manager.removeLayout(existing)

    front = manager.layoutByName(FRONT_LAYOUT)
    if front is not None and not _is_current(front):
        manager.removeLayout(front)
        front = None
    if front is None:
        front = _create_layout(project, FRONT_LAYOUT)

    back = manager.layoutByName(BACK_LAYOUT)
    if back is not None and not _is_current(back):
        manager.removeLayout(back)
        back = None
    if back is None:
        back = _create_layout(project, BACK_LAYOUT)

    return front, back


# ---------------------------------------------------------------------------
# per-sheet updates
# ---------------------------------------------------------------------------
def _title_html(sheet: dict) -> str:
    number = sheet.get("sheet_no")
    if number not in (None, "") and str(number).isdigit():
        number_text = f"{int(number):02d}"
    elif number not in (None, ""):
        number_text = str(number)
    else:
        number_text = ""

    label = html.escape(str(sheet.get("sheet_name") or sheet["sheet_id"]))
    header = f"Blatt {number_text} · {label}" if number_text else label
    return f"<span style='font-size:8pt;font-weight:bold'>{header}</span>"


def _coord_html(sheet: dict) -> str:
    f = config.format_coord
    return (
        f"<span style='font-size:6.5pt;font-weight:bold'>"
        f"SW E {f(sheet['e_min'])} / N {f(sheet['n_min'])}</span>"
    )


def _source_html(text: str) -> str:
    return f"<span style='font-size:4.5pt'>{html.escape(str(text))}</span>"


def _neighbor_text(neighbour: dict | None, arrow: str = "", axis: str = "E") -> str:
    if neighbour is None:
        return "<span style='font-size:8pt'>—</span>"
    value = config.format_coord(neighbour["edge_value"])
    prefix = f"{arrow} " if arrow else ""
    return (
        f"<span style='font-size:8pt;font-weight:bold'>"
        f"{prefix}Blatt {html.escape(neighbour['label'])} · {axis} {value}</span>"
    )


def update_layout(
    layout: QgsPrintLayout,
    side: str,
    sheet: dict,
    image_path: Path,
    neighbour: dict,
    source_text: str | None = None,
) -> None:
    picture = layout.itemById("map_picture")
    picture.setPicturePath(str(image_path))
    picture.refreshPicture()

    layout.itemById("title").setText(_title_html(sheet))
    layout.itemById("coord").setText(_coord_html(sheet))
    if source_text is None:
        source_text = str(config.get_setting(f"{side}_source"))
    layout.itemById("source").setText(_source_html(source_text))

    layout.itemById("neighbor_top").setText(
        _neighbor_text(neighbour.get("north"), "▲", "N")
    )
    layout.itemById("neighbor_bottom").setText(
        _neighbor_text(neighbour.get("south"), "▼", "N")
    )
    if side == "front":
        left, right = neighbour.get("west"), neighbour.get("east")
    else:
        left, right = neighbour.get("east"), neighbour.get("west")
    layout.itemById("neighbor_left").setText(_neighbor_text(left, "", "E"))
    layout.itemById("neighbor_right").setText(_neighbor_text(right, "", "E"))


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
        raise RuntimeError(f"PDF-Export fehlgeschlagen ({result}): {path}")
    return path


# ---------------------------------------------------------------------------
# overview sheet (Blattübersicht)
# ---------------------------------------------------------------------------
def _sheet_number_text(sheet: dict) -> str:
    number = sheet.get("sheet_no")
    if number not in (None, "") and str(number).isdigit():
        return f"{int(number):02d}"
    if number not in (None, ""):
        return str(number)
    return "?"


def _overview_image(sheets: list[dict]) -> tuple[QImage, int, int]:
    rows = [int(s["row"]) for s in sheets]
    cols = [int(s["col"]) for s in sheets]
    min_row, max_row = min(rows), max(rows)
    min_col, max_col = min(cols), max(cols)
    grid_rows = max_row - min_row + 1
    grid_cols = max_col - min_col + 1

    cell = 200
    pad = 6
    width = grid_cols * cell + pad * 2
    height = grid_rows * cell + pad * 2

    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    num_font = QFont("DejaVu Sans")
    num_font.setPixelSize(int(cell * 0.30))
    num_font.setBold(True)
    id_font = QFont("DejaVu Sans")
    id_font.setPixelSize(int(cell * 0.13))

    by_key = {(int(s["row"]), int(s["col"])): s for s in sheets}
    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            x = pad + (col - min_col) * cell
            y = pad + (row - min_row) * cell
            rect = QRect(x, y, cell - 1, cell - 1)
            sheet = by_key.get((row, col))
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
            painter.drawText(
                QRect(x, y, cell, int(cell * 0.55)),
                Qt.AlignCenter, _sheet_number_text(sheet),
            )
            painter.setFont(id_font)
            painter.setPen(QColor(80, 80, 80))
            painter.drawText(
                QRect(x, y + int(cell * 0.5), cell, int(cell * 0.45)),
                Qt.AlignCenter, str(sheet["sheet_id"]),
            )
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
        line = f"<b>{_sheet_number_text(sheet)}</b> &nbsp;{html.escape(str(sheet['sheet_id']))}"
        name = sheet.get("sheet_name")
        if name:
            line += f" &nbsp;{html.escape(str(name))}"
        items.append(line)

    cols: list[list[str]] = [[] for _ in range(columns)]
    for index, item in enumerate(items):
        cols[index % columns].append(item)

    body = "<table width='100%' cellspacing='0' cellpadding='2'><tr>"
    for col in cols:
        body += "<td valign='top' style='font-size:6pt'>" + "<br/>".join(col) + "</td>"
    body += "</tr></table>"
    return body


def export_overview(
    project: QgsProject,
    sheets: list[dict],
    out_path: Path,
    dpi: int = config.DPI,
) -> Path:
    geom = config.page_geometry()
    image, grid_rows, grid_cols = _overview_image(sheets)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(geom.page_w, geom.page_h, _MM)
    )

    _label(
        layout, "overview_title",
        "<span style='font-size:12pt;font-weight:bold'>Blattübersicht</span> "
        f"<span style='font-size:8pt'>· {len(sheets)} Blätter · {config.format_scale()}</span>",
        6.0, 6.0, geom.page_w - 12.0, 8.0, align=Qt.AlignLeft,
    )

    avail_w = geom.page_w - 12.0
    avail_h = (geom.page_h - 12.0) * 0.55
    cell_mm = min(avail_w / grid_cols, avail_h / grid_rows)
    cell_mm = max(3.0, min(18.0, cell_mm))
    diag_w = cell_mm * grid_cols
    diag_h = cell_mm * grid_rows
    diag_x = (geom.page_w - diag_w) / 2.0
    diag_y = 16.0

    tmp = config.cache_dir() / "uebersicht.png"
    image.save(str(tmp))

    picture = QgsLayoutItemPicture(layout)
    picture.setId("overview_diagram")
    picture.setResizeMode(QgsLayoutItemPicture.Stretch)
    picture.attemptMove(QgsLayoutPoint(diag_x, diag_y, _MM))
    picture.attemptResize(QgsLayoutSize(diag_w, diag_h, _MM))
    picture.setPicturePath(str(tmp))
    layout.addLayoutItem(picture)

    legend_y = diag_y + diag_h + 2.0
    legend_h = (geom.page_h - 6.0) - legend_y
    _label(
        layout, "overview_legend", _overview_legend_html(sheets),
        6.0, legend_y, geom.page_w - 12.0, legend_h,
        align=Qt.AlignLeft, valign=Qt.AlignTop,
    )

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = dpi
    settings.rasterizeWholeImage = False
    settings.exportMetadata = True
    result = exporter.exportToPdf(str(path), settings)
    tmp.unlink(missing_ok=True)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"Übersicht konnte nicht erstellt werden ({result}): {path}")
    return path
