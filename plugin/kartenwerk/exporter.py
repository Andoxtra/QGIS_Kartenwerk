"""Batch export of selected sheets to two-page PDFs (front topo, mirrored aerial back)."""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from qgis.core import QgsProject, QgsRectangle, QgsVectorLayer

from . import config, crs as crs_mod, grid, layout, render, sources, wmm

# A national source that leaves more than this share of a sheet empty (outside
# its coverage) is replaced by the worldwide source for that sheet. Services that
# fill gaps with white get a higher threshold, so bright features such as
# greenhouse roofs never trigger it.
FALLBACK_THRESHOLD = 0.03
FALLBACK_THRESHOLD_WHITE = 0.10


def merge_tool() -> list[str] | None:
    """Command prefix of an available PDF merge tool, or None."""
    tool = shutil.which("pdfunite")
    if tool:
        return [tool]
    for name in ("gs", "gswin64c", "gswin32c"):
        gs = shutil.which(name)
        if gs:
            return [gs, "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite"]
    return None


def _merge_pdfs(files: list[Path], out_path: Path) -> None:
    tool = merge_tool()
    if tool is None:
        raise RuntimeError("no PDF merge tool found (install poppler-utils or Ghostscript)")
    if Path(tool[0]).stem == "pdfunite":
        cmd = [*tool, *[str(f) for f in files], str(out_path)]
    else:
        cmd = [*tool, f"-sOutputFile={out_path}", *[str(f) for f in files]]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"PDF merge failed: {result.stderr.strip() or result.stdout.strip()}")


class ExportResult(list):
    """List of written sheet PDFs, plus non-fatal warnings."""

    def __init__(self):
        super().__init__()
        self.warnings: list[str] = []


def _render_side(project, resolved: sources.Resolved, extent, crs, dpi, width_px, height_px,
                 zone, hemisphere, cell=None):
    """Render one side in memory. Returns (image, attribution of the source actually used)."""
    source = resolved.source
    retries = 2 if source is not None and source.kind == "xyz" else 0
    white = bool(source and source.white_is_nodata)
    image, missing = render.render_image(extent, [resolved.layer], crs, dpi, width_px, height_px,
                                         retries=retries, white_is_missing=white, cell=cell)
    threshold = FALLBACK_THRESHOLD_WHITE if white else FALLBACK_THRESHOLD
    if missing > threshold and resolved.fallback is not None:
        fallback = resolved.fallback
        layer = sources.ensure_layer(project, fallback, zone, hemisphere)
        image, _ = render.render_image(extent, [layer], crs, dpi, width_px, height_px,
                                       retries=2 if fallback.kind == "xyz" else 0, cell=cell)
        return image, fallback.attribution
    return image, resolved.attribution


def _north_info(extent, utm) -> dict:
    """Grid convergence and grid–magnetic angle at the sheet centre (degrees, + = east)."""
    centre = extent.center()
    convergence = crs_mod.grid_convergence(centre.x(), centre.y(), utm)
    lon, lat = crs_mod.lonlat(centre.x(), centre.y(), utm)
    year = wmm.decimal_year()
    decl = wmm.declination(lat, lon, year)
    return {
        "convergence": convergence,
        "declination": decl,
        # Angle from grid north to magnetic north: what a compass user adds/subtracts.
        "grid_magnetic": None if decl is None else decl - convergence,
        "year": int(year),
    }


def encode_workers() -> int:
    """Threads for image encoding: front + back of two sheets at most, never more than the CPU has."""
    return max(1, min(4, os.cpu_count() or 1))


def export_sheets(
    project: QgsProject,
    grid_layer: QgsVectorLayer,
    settings: dict | None = None,
    progress=None,
    keep_cache: bool = False,
) -> ExportResult:
    """Export all selected grid cells. Returns the written PDFs (with .warnings)."""
    settings = {**config.export_settings(), **(settings or {})}

    zone_info = grid.grid_zone(grid_layer)
    if zone_info is None:
        raise ValueError(
            "This grid was made by an older version of the plugin. Please run 'Generate grid' again."
        )
    zone, hemisphere = zone_info
    utm = grid_layer.crs()

    sheets = grid.selected_sheets(grid_layer)
    if not sheets:
        raise ValueError("No sheets selected. Please select grid cells first.")

    if settings["auto_number"] and any(s["sheet_no"] in (None, "") for s in sheets):
        grid.number_selected(grid_layer)
        sheets = grid.selected_sheets(grid_layer)

    sheets.sort(key=lambda s: (s["sheet_no"] if s["sheet_no"] not in (None, "") else 10**9,
                               -s["n_max"], s["e_min"]))

    geom = config.page_geometry()
    for sheet in sheets:
        if (int(sheet["utm_zone"]), str(sheet["hemisphere"])) != (zone, hemisphere):
            raise ValueError("The selected sheets belong to another UTM zone. Please regenerate the grid.")
        if abs((sheet["e_max"] - sheet["e_min"]) - geom.cell_w) > 1.0 \
                or abs((sheet["n_max"] - sheet["n_min"]) - geom.cell_h) > 1.0:
            raise ValueError(
                "The grid does not match the current page format and scale. "
                "Please run 'Generate grid' again."
            )

    print_layout = layout.ensure_layout(project)
    lookup = grid.neighbour_map(grid_layer)

    dpi = int(settings["dpi"])
    width_px = int(round(geom.map_w / 25.4 * dpi))
    height_px = int(round(geom.map_h / 25.4 * dpi))
    jpeg_quality = int(settings["jpeg_quality"])

    selection = QgsRectangle()
    for sheet in sheets:
        selection.combineExtentWith(
            QgsRectangle(sheet["e_min"], sheet["n_min"], sheet["e_max"], sheet["n_max"]))
    bbox = crs_mod.to_lonlat_bbox(selection, utm)
    front = sources.resolve(project, sources.TOPO, settings["front_basemap"], bbox, zone, hemisphere)
    back = sources.resolve(project, sources.IMAGERY, settings["back_basemap"], bbox, zone, hemisphere)
    ext = {side: ".jpg" if settings[f"{side}_format"] == "JPEG" else ".png" for side in layout.SIDES}

    out_dir = Path(settings["output_dir"]).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = config.cache_dir()
    result = ExportResult()
    written_sheets: list[tuple[dict, Path]] = []
    errors: list[str] = []

    total = len(sheets)
    print_date = layout.today()
    # Unique per export: QGIS caches pictures by file path, so names reused within
    # one session (e.g. re-exporting after changing the basemap) could show stale images.
    run = uuid.uuid4().hex[:8] + "_"

    def finish(job: dict) -> None:
        """Wait for a sheet's images, then build its PDF (main thread: layouts are not thread-safe)."""
        try:
            for future in job["futures"]:
                future.result()
            neighbour = grid.neighbour_info(grid_layer, job["sheet"], lookup)
            info = {**_north_info(job["extent"], utm), "extent": job["extent"],
                    "sheets": sheets, "date": print_date, "run": run}
            for side in layout.SIDES:
                text = settings[f"{side}_attribution"].strip() or job["attribution"][side]
                job["temp"] += layout.update_side(print_layout, side, job["sheet"],
                                                  job["images"][side], neighbour, text, info)
            layout.export_pdf(print_layout, job["pdf"], dpi)
            result.append(job["pdf"])
            written_sheets.append((job["sheet"], job["pdf"]))
        except Exception as exc:  # keep going, report at the end
            errors.append(f"{job['stem']}: {exc}")
        finally:
            for tmp in job["temp"] + ([] if keep_cache else list(job["images"].values())):
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    # Downloads stay strictly sequential (one map request at a time, as the tile
    # servers' fair-use policies ask). Only local work runs in parallel: while the
    # next sheet downloads, worker threads mirror and encode the previous one.
    pending = None
    with ThreadPoolExecutor(max_workers=encode_workers(),
                            thread_name_prefix="kartenwerk-encode") as pool:
        try:
            for index, sheet in enumerate(sheets, start=1):
                number = sheet["sheet_no"] if sheet["sheet_no"] not in (None, "") else index
                stem = f"Sheet_{int(number):02d}_{sheet['sheet_id']}"
                if progress and progress(index, total, stem) is False:
                    break

                cell = QgsRectangle(sheet["e_min"], sheet["n_min"], sheet["e_max"], sheet["n_max"])
                # The printed map shows the cell plus half the overlap on every side.
                extent = QgsRectangle(cell.xMinimum() - geom.pad_w, cell.yMinimum() - geom.pad_h,
                                      cell.xMaximum() + geom.pad_w, cell.yMaximum() + geom.pad_h)
                job = {
                    "extent": extent,
                    "sheet": sheet,
                    "stem": stem,
                    "pdf": out_dir / f"{stem}.pdf",
                    "images": {side: cache / f"{run}{sheet['sheet_id']}_{side}{ext[side]}"
                               for side in layout.SIDES},
                    "attribution": {},
                    "futures": [],
                    "temp": [],
                }
                try:
                    for side, resolved in (("front", front), ("back", back)):
                        image, job["attribution"][side] = _render_side(
                            project, resolved, extent, utm, dpi, width_px, height_px,
                            zone, hemisphere, cell)
                        job["futures"].append(pool.submit(
                            render.save_image, image, job["images"][side], jpeg_quality,
                            mirror=(side == "back")))
                except Exception as exc:
                    errors.append(f"{stem}: {exc}")
                    for future in job["futures"]:
                        if not future.cancel():
                            future.exception()  # wait, so the temp file can be removed
                    for tmp in job["images"].values():
                        tmp.unlink(missing_ok=True)
                    job = None

                if pending is not None:
                    finish(pending)
                pending = job
        finally:
            if pending is not None:
                finish(pending)

    if written_sheets:
        _write_index(out_dir, written_sheets, utm.authid())
        overview_path = None
        try:
            overview_path = layout.export_overview(
                project, [s for s, _ in written_sheets], out_dir / "Overview.pdf", dpi)
        except Exception as exc:
            errors.append(f"Overview.pdf: {exc}")
        if len(result) > 1:
            merge_files = ([overview_path] if overview_path else []) + list(result)
            if merge_tool() is None:
                result.warnings.append(
                    "all_sheets.pdf was not created: install poppler-utils (pdfunite) or "
                    "Ghostscript to get one merged file. The single sheet PDFs are complete."
                )
            else:
                try:
                    _merge_pdfs(merge_files, out_dir / "all_sheets.pdf")
                except Exception as exc:
                    errors.append(f"all_sheets.pdf: {exc}")

    if errors:
        raise RuntimeError("Some sheets could not be created:\n" + "\n".join(errors))
    return result


def _write_index(out_dir: Path, written: list[tuple[dict, Path]], epsg: str) -> None:
    path = out_dir / "index.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["sheet", "sheet_id", "utm_zone", "epsg", "name",
             "e_min", "e_max", "n_min", "n_max", "file"]
        )
        for sheet, pdf in written:
            writer.writerow(
                [
                    sheet["sheet_no"] if sheet["sheet_no"] not in (None, "") else "",
                    sheet["sheet_id"],
                    crs_mod.zone_label(int(sheet["utm_zone"]), str(sheet["hemisphere"])),
                    epsg,
                    sheet["sheet_name"] or "",
                    int(round(sheet["e_min"])),
                    int(round(sheet["e_max"])),
                    int(round(sheet["n_min"])),
                    int(round(sheet["n_max"])),
                    pdf.name,
                ]
            )
