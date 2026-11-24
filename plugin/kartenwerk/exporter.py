"""Batch export of selected sheets to two-page A5 PDFs."""
from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

from qgis.core import QgsProject, QgsRectangle, QgsVectorLayer

from . import config, grid, layout, render, services


def _pdfunite() -> str | None:
    return shutil.which("pdfunite")


def _merge_pdfs(files: list[Path], out_path: Path) -> None:
    tool = _pdfunite()
    if tool:
        cmd = [tool, *[str(f) for f in files], str(out_path)]
    else:
        gs = shutil.which("gs")
        if not gs:
            raise RuntimeError("Weder pdfunite noch Ghostscript gefunden.")
        cmd = [
            gs, "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
            f"-sOutputFile={out_path}", *[str(f) for f in files],
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"PDF-Zusammenführung fehlgeschlagen: {result.stderr.strip() or result.stdout.strip()}"
        )


def export_sheets(
    project: QgsProject,
    grid_layer: QgsVectorLayer,
    settings: dict,
    progress=None,
    keep_cache: bool = False,
) -> list[Path]:
    """Export all selected grid cells. Returns the list of written PDFs."""
    sheets = grid.selected_sheets(grid_layer)
    if not sheets:
        raise ValueError("Keine Blätter ausgewählt. Bitte zuerst Rasterzellen auswählen.")

    if settings.get("auto_number", True) and any(s["sheet_no"] in (None, "") for s in sheets):
        grid.number_selected(grid_layer)
        sheets = grid.selected_sheets(grid_layer)

    sheets.sort(key=lambda s: (s["sheet_no"] if s["sheet_no"] not in (None, "") else 10**9,
                               -s["n_max"], s["e_min"]))

    front_layout, back_layout = layout.ensure_layouts(project)
    lookup = grid.neighbour_map(grid_layer)

    dpi = int(settings.get("dpi", config.DPI))
    geom = config.page_geometry()
    width_px = int(round(geom.map_w / 25.4 * dpi))
    height_px = int(round(geom.map_h / 25.4 * dpi))

    front = services.get_layer(project, settings.get("front_layer") or "") \
        or render.topo_layer(project)
    back = services.get_layer(project, settings.get("back_layer") or "") \
        or render.imagery_layer(project, settings.get("imagery", "IMAGES_DOP_20_RGB"))

    front_format = str(settings.get("front_format") or config.get_setting("front_format")).upper()
    back_format = str(settings.get("back_format") or config.get_setting("back_format")).upper()
    jpeg_quality = int(settings.get("jpeg_quality") or config.get_setting("jpeg_quality"))
    front_ext = ".jpg" if front_format == "JPEG" else ".png"
    back_ext = ".jpg" if back_format == "JPEG" else ".png"
    front_source = settings.get("front_source") or config.get_setting("front_source")
    back_source = settings.get("back_source") or config.get_setting("back_source")

    out_dir = Path(settings.get("output_dir") or config.output_dir()).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = config.cache_dir()
    written: list[Path] = []
    written_sheets: list[dict] = []
    errors: list[str] = []

    total = len(sheets)
    for index, sheet in enumerate(sheets, start=1):
        number = sheet["sheet_no"] if sheet["sheet_no"] not in (None, "") else index
        stem = f"Blatt_{int(number):02d}_{sheet['sheet_id']}"
        if progress and progress(index, total, stem) is False:
            break

        extent = QgsRectangle(sheet["e_min"], sheet["n_min"], sheet["e_max"], sheet["n_max"])
        if abs((extent.xMaximum() - extent.xMinimum()) - geom.cell_w) > 1.0 \
                or abs((extent.yMaximum() - extent.yMinimum()) - geom.cell_h) > 1.0:
            raise ValueError(
                "Das Raster passt nicht zum aktuellen Seitenformat. "
                "Bitte 'Raster erzeugen' erneut ausführen."
            )
        front_png = cache / f"{sheet['sheet_id']}_front{front_ext}"
        back_png = cache / f"{sheet['sheet_id']}_back{back_ext}"
        front_pdf = cache / f"{sheet['sheet_id']}_front.pdf"
        back_pdf = cache / f"{sheet['sheet_id']}_back.pdf"
        out_pdf = out_dir / f"{stem}.pdf"

        try:
            render.render_map(extent, [front], front_png, dpi, width_px, height_px,
                              jpeg_quality=jpeg_quality, retry_on_blank=True)
            render.render_map(extent, [back], back_png, dpi, width_px, height_px,
                              jpeg_quality=jpeg_quality)
            render.flip_image(back_png, jpeg_quality=jpeg_quality)

            neighbour = grid.neighbour_info(grid_layer, sheet, lookup)

            layout.update_layout(front_layout, "front", sheet, front_png, neighbour,
                                 source_text=front_source)
            layout.export_pdf(front_layout, front_pdf, dpi)

            layout.update_layout(back_layout, "back", sheet, back_png, neighbour,
                                 source_text=back_source)
            layout.export_pdf(back_layout, back_pdf, dpi)

            _merge_pdfs([front_pdf, back_pdf], out_pdf)
            written.append(out_pdf)
            written_sheets.append(sheet)
        except Exception as exc:  # keep going, report at the end
            errors.append(f"{stem}: {exc}")
        finally:
            if not keep_cache:
                for tmp in (front_png, back_png, front_pdf, back_pdf):
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass

    if written:
        _write_index(out_dir, written_sheets, written)
        overview_path = None
        try:
            overview_path = layout.export_overview(
                project, written_sheets, out_dir / "Uebersicht.pdf", dpi
            )
        except Exception as exc:
            errors.append(f"Uebersicht.pdf: {exc}")
        if len(written) > 1:
            try:
                merge_files = [overview_path] + written if overview_path else written
                _merge_pdfs(merge_files, out_dir / "alle_blaetter.pdf")
            except Exception as exc:
                errors.append(f"alle_blaetter.pdf: {exc}")

    if errors:
        raise RuntimeError("Einige Blätter konnten nicht erzeugt werden:\n" + "\n".join(errors))
    return written


def _write_index(out_dir: Path, sheets: list[dict], written: list[Path]) -> None:
    by_id = {s["sheet_id"]: s for s in sheets}
    path = out_dir / "index.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            ["blatt", "sheet_id", "name", "e_min", "e_max", "n_min", "n_max", "datei"]
        )
        for pdf in written:
            sheet_id = pdf.stem.split("_")[-1]
            sheet = by_id.get(sheet_id)
            if not sheet:
                continue
            writer.writerow(
                [
                    sheet["sheet_no"] if sheet["sheet_no"] not in (None, "") else "",
                    sheet["sheet_id"],
                    sheet["sheet_name"] or "",
                    int(sheet["e_min"]),
                    int(sheet["e_max"]),
                    int(sheet["n_min"]),
                    int(sheet["n_max"]),
                    pdf.name,
                ]
            )
