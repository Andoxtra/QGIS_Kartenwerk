#!/usr/bin/env python3
"""UI tests with QGIS's mocked interface: dock, actions, settings dialog (no map downloads).

    QT_QPA_PLATFORM=offscreen python3 dev/test_ui.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin"))

from qgis.testing import start_app  # noqa: E402
from qgis.testing.mocked import get_iface  # noqa: E402

app = start_app()

from qgis.core import (  # noqa: E402
    QgsFeature, QgsGeometry, QgsMapLayer, QgsProject, QgsRectangle, QgsSettings, QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox  # noqa: E402

from kartenwerk import config, grid, layout  # noqa: E402
from kartenwerk.dialog import SettingsDialog  # noqa: E402
from kartenwerk.plugin import KartenwerkPlugin  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {label}{(': ' + str(detail)) if detail != '' else ''}", flush=True)
    if not ok:
        failures.append(label)


def set_aoi(aoi, bbox):
    if aoi.isEditable():
        aoi.rollBack()
    aoi.dataProvider().truncate()
    feat = QgsFeature(aoi.fields())
    feat.setGeometry(QgsGeometry.fromRect(QgsRectangle(*bbox)))
    aoi.dataProvider().addFeatures([feat])
    aoi.updateExtents()


def main() -> int:
    errors = []
    QMessageBox.warning = staticmethod(lambda *a, **k: errors.append(a[2]))
    work = Path(tempfile.mkdtemp(prefix="kartenwerk_ui_"))
    try:
        for key, value in {"page_format": "A5", "scale": 15000, "overlap": 5}.items():
            config.set_setting(key, value)
        config.set_setting("front_source", "UTM 32N · old text")  # version-1 key
        project = QgsProject.instance()
        project.clear()
        project.setFileName(str(work / "p.qgz"))

        plugin = KartenwerkPlugin(get_iface())
        plugin.initGui()
        check("legacy settings removed", QgsSettings().value("kartenwerk/front_source") is None)
        actions = [a.text() for a in plugin.actions]
        check("actions", actions == ["Draw work area", "Generate grid", "Select sheets",
                                     "Number sheets", "Name sheet", "Export selection",
                                     "Settings"], actions)
        check("export disabled without selection", not plugin.export_button.isEnabled())

        plugin.generate_grid()
        check("generate without work area → message", errors[-1:] == ["Please draw a work area first."],
              errors[-1:])

        plugin.draw_aoi()
        rasters = sorted(l.name() for l in project.mapLayers().values()
                         if l.type() == QgsMapLayer.RasterLayer)
        check("first draw adds basemaps", rasters == ["Esri World Imagery", "OpenTopoMap"], rasters)
        check("web-map CRS for the empty project", project.crs().authid() == "EPSG:3857")
        plugin.draw_aoi()
        check("no duplicate basemaps", len([l for l in project.mapLayers().values()
                                            if l.type() == QgsMapLayer.RasterLayer]) == 2)

        aoi = grid.get_aoi_layer(project)
        set_aoi(aoi, (-105.30, 39.98, -105.26, 40.00))
        n = len(errors)
        plugin.generate_grid()
        check("grid generated without errors", errors[n:] == [], errors[n:])
        g = grid.get_grid_layer(project)
        g.selectByExpression('"in_aoi" = 1')
        check("dock title shows the zone", plugin.title_label.text().endswith("UTM 13N"),
              plugin.title_label.text())
        check("selection count and export button",
              plugin.count_label.text().startswith(f"Selected: {g.selectedFeatureCount()}")
              and plugin.export_button.isEnabled(), plugin.count_label.text())

        set_aoi(aoi, (9.0, 48.5, 9.04, 48.52))
        plugin.generate_grid()
        g2 = grid.get_grid_layer(project)
        g2.selectByIds(g2.allFeatureIds()[:3])
        check("zone switch re-tracks the new grid", plugin._grid_layer is g2
              and plugin.title_label.text().endswith("UTM 32N")
              and plugin.count_label.text() == "Selected: 3 sheets", plugin.count_label.text())

        g2.selectByIds(g2.allFeatureIds()[:2])
        n = len(errors)
        plugin.name_sheet()
        check("naming needs exactly one sheet", errors[n:] == ["Select exactly one sheet to name."])
        fid = g2.allFeatureIds()[0]
        g2.selectByIds([fid])
        QInputDialog.getText = staticmethod(lambda *a, **k: ("  Schönenbach  ", True))
        plugin.name_sheet()
        check("sheet name stored (trimmed)", g2.getFeature(fid)["sheet_name"] == "Schönenbach")
        sheet = grid.selected_sheets(g2)[0]
        check("name in the sheet title", "Schönenbach" in layout._title_html(sheet, "front"))

        route = QgsVectorLayer("LineString?crs=EPSG:4326", "My route", "memory")
        project.addMapLayer(route)
        dlg = SettingsDialog()
        overlay_names = [dlg.overlay_list.item(i).text() for i in range(dlg.overlay_list.count())]
        check("overlay list: own vector layers only", overlay_names == ["My route"], overlay_names)
        dlg.overlay_list.item(0).setCheckState(Qt.Checked)
        dlg.overlays_back.setChecked(True)
        check("overlay selection returned for the project", dlg.project_values() ==
              ([route.id()], True), dlg.project_values())
        config.set_project_overlays(*dlg.project_values())
        check("overlays stored in the project", config.project_overlays() == [route.id()]
              and config.project_overlays_on_back())
        for fmt, expected_modes in (("A5", [0, 2]), ("A6", [0, 2, 4]), ("A2", [0])):
            dlg._select_data(dlg.page_combo, fmt)
            data = [dlg.print_mode.itemData(i) for i in range(dlg.print_mode.count())]
            enabled = dlg.print_mode.isEnabled()
            check(f"print modes for {fmt}", data == expected_modes
                  and enabled == (len(expected_modes) > 1), data)
        dlg._select_data(dlg.page_combo, "A6")
        dlg._select_data(dlg.print_mode, 4)
        check("4-up A6 enables the print-file offset", dlg.offset2_dx.isEnabled()
              and dlg.offset2_dy.isEnabled())
        dlg._select_data(dlg.print_mode, 0)
        check("no print file disables the print-file offset", not dlg.offset2_dx.isEnabled())
        dlg._select_data(dlg.page_combo, "A5")
        dlg.offset_dx.setValue(9.0)
        check("duplex offset limited to ±5 mm", dlg.offset_dx.value() == 5.0, dlg.offset_dx.value())
        values = dlg.values()
        spin = dlg.scale_spin
        check("scale field: 1:5 000 – 1:100 000, arrows step 2 500",
              (spin.minimum(), spin.maximum(), spin.singleStep(), spin.prefix())
              == (5000, 100000, 2500, "1:"),
              (spin.minimum(), spin.maximum(), spin.singleStep(), spin.prefix()))
        spin.setValue(17300)
        dlg._snap_scale()
        check("typed scale rounded to 500", spin.value() == 17500
              and dlg.values()["scale"] == 17500, spin.value())
        check("no blur hint at 1:17 500", dlg.scale_hint.isHidden())
        spin.setValue(7500)
        check("blur hint below 1:10 000", not dlg.scale_hint.isHidden())
        spin.setValue(100000)
        check("1:100 000 accepted", dlg.values()["scale"] == 100000)
        spin.setValue(config.DEFAULT_SCALE)
        check("overview map defaults to OpenTopoMap",
              dlg.overview_combo.currentData() == "opentopomap", dlg.overview_combo.currentData())
        overview_items = [dlg.overview_combo.itemData(i) for i in range(dlg.overview_combo.count())]
        check("overview map offers None, catalog sources", "none" in overview_items
              and "esri_imagery" in overview_items, overview_items[:4])
        check("overview colour default magenta-red", dlg.overview_color.color().name() == "#c8005a",
              dlg.overview_color.color().name())
        from qgis.PyQt.QtGui import QColor
        dlg.overview_color.setColor(QColor("#00a0ff"))
        check("overview colour returned", dlg.values()["overview_color"] == "#00a0ff"
              and dlg.values()["overview_basemap"] == "opentopomap")
        check("mirror back: default on", dlg.mirror_back.isChecked()
              and dlg.values()["mirror_back"] is True)
        dlg.mirror_back.setChecked(False)
        check("mirror back: off returned", dlg.values()["mirror_back"] is False)
        dlg.mirror_back.setChecked(True)
        check("printer margin choices", [dlg.margin_combo.itemData(i) for i in
                                         range(dlg.margin_combo.count())] == [5, 4, 3])
        check("dialog values complete", {"overlap", "printer_margin", "print_mode", "render_cache",
                                         "duplex_dx",
                                         "duplex_dy", "duplex_2up_dx", "duplex_2up_dy"}
              <= set(values), sorted(values))
        check("auto note names the national sources", "TopPlusOpen" in dlg.auto_note.text(),
              dlg.auto_note.text())

        plugin.unload()
        check("unexpected error messages", [e for e in errors if e not in (
            "Please draw a work area first.", "Select exactly one sheet to name.")] == [])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\n{len(failures)} failure(s): {failures}" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
