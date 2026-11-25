"""QGIS plugin entry point and UI."""
from __future__ import annotations

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsMapLayer,
    QgsProject,
    QgsReferencedRectangle,
)
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont
from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import config, crs as crs_mod, dialog, exporter, grid, layout, services

_GRID_NAMES = (config.GRID_LAYER, *config.LEGACY_GRID_LAYERS)


class KartenwerkPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions: list[QAction] = []
        self.toolbar = None
        self.dock = None
        self.title_label = None
        self.count_label = None
        self.export_button = None
        self._grid_layer = None

    # ------------------------------------------------------------------
    # QGIS plugin lifecycle
    # ------------------------------------------------------------------
    def initGui(self):
        config.remove_legacy_settings()
        self._create_actions()

        self.toolbar = self.iface.addToolBar("Kartenwerk")
        self.toolbar.setObjectName("KartenwerkToolbar")
        for action in self.actions:
            self.toolbar.addAction(action)
            self.iface.addPluginToMenu("Kartenwerk", action)

        self._create_dock()
        project = QgsProject.instance()
        project.layerWasAdded.connect(self._on_layer_added)
        project.layersWillBeRemoved.connect(self._on_layers_removed)
        project.readProject.connect(self._on_project_read)
        self._connect_grid()

    def unload(self):
        project = QgsProject.instance()
        for signal, slot in (
            (project.layerWasAdded, self._on_layer_added),
            (project.layersWillBeRemoved, self._on_layers_removed),
            (project.readProject, self._on_project_read),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._disconnect_grid()

        for action in self.actions:
            self.iface.removePluginMenu("Kartenwerk", action)
            if self.toolbar is not None:
                self.toolbar.removeAction(action)
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        self.actions = []

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _create_actions(self):
        def make(text, icon, slot):
            action = QAction(QgsApplication.getThemeIcon(icon), text, self.iface.mainWindow())
            action.triggered.connect(slot)
            self.actions.append(action)
            return action

        make("Draw work area", "mActionAddPolygon.svg", self.draw_aoi)
        make("Generate grid", "mActionAddTable.svg", self.generate_grid)
        make("Select sheets", "mActionSelect.svg", self.select_sheets)
        make("Number sheets", "mActionOrderBy.svg", self.number_sheets)
        make("Name sheet", "mActionLabeling.svg", self.name_sheet)
        make("Export selection", "mActionSharingExport.svg", self.export_sheets)
        make("Settings", "mActionOptions.svg", self.show_settings)

    def _create_dock(self):
        self.dock = QgsDockWidget("Kartenwerk", self.iface.mainWindow())
        self.dock.setObjectName("KartenwerkDock")
        self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setSpacing(6)

        bold = QFont()
        bold.setBold(True)
        self.title_label = QLabel()
        self.title_label.setFont(bold)
        self.title_label.setWordWrap(True)
        box.addWidget(self.title_label)

        help_text = QLabel(
            "1. Draw work area\n"
            "2. Generate grid\n"
            "3. Select sheets (click / Ctrl / Shift)\n"
            "4. Optionally number / name them\n"
            "5. Export selection"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: palette(mid);")
        box.addWidget(help_text)

        for action in self.actions:
            button = QPushButton(action.text())
            button.clicked.connect(action.trigger)
            box.addWidget(button)
            if action.text() == "Export selection":
                self.export_button = button

        self.count_label = QLabel()
        self.count_label.setFont(bold)
        box.addWidget(self.count_label)
        box.addStretch(1)

        self.dock.setWidget(widget)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.show()

    # ------------------------------------------------------------------
    # grid layer / selection tracking
    # ------------------------------------------------------------------
    def _on_layer_added(self, layer):
        if layer.name() in _GRID_NAMES:
            self._connect_grid()

    def _on_layers_removed(self, layer_ids):
        if self._grid_layer is not None and self._grid_layer.id() in layer_ids:
            self._disconnect_grid()
            self.update_count()

    def _on_project_read(self, *args):
        self._disconnect_grid()
        self._connect_grid()

    def _disconnect_grid(self):
        if self._grid_layer is not None:
            try:
                self._grid_layer.selectionChanged.disconnect(self.update_count)
            except (TypeError, RuntimeError):
                pass
        self._grid_layer = None

    def _connect_grid(self):
        layer = grid.get_grid_layer(QgsProject.instance())
        if layer is not self._grid_layer:
            self._disconnect_grid()
            self._grid_layer = layer
            if layer is not None:
                layer.selectionChanged.connect(self.update_count)
        self.update_count()

    def update_count(self, *args):
        layer = self._grid_layer
        count = len(layer.selectedFeatures()) if layer is not None else 0
        if self.count_label is not None:
            self.count_label.setText(f"Selected: {count} sheet{'s' if count != 1 else ''}")
        if self.export_button is not None:
            self.export_button.setEnabled(count > 0)
        if self.title_label is not None:
            parts = ["Kartenwerk", config.page_format(), config.format_scale()]
            zone = grid.grid_zone(layer)
            if zone is not None:
                parts.append(f"UTM {crs_mod.zone_label(*zone)}")
            self.title_label.setText(" · ".join(parts))

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def draw_aoi(self):
        project = QgsProject.instance()
        added_basemap = False
        try:
            # A new project shows an empty white canvas: add a basemap so users
            # can see where to draw.
            if not any(layer.type() == QgsMapLayer.RasterLayer
                       for layer in project.mapLayers().values()):
                services.ensure_basemaps(project)
                added_basemap = True
            aoi = grid.ensure_aoi_layer(project)
        except Exception as exc:
            self._error("Work area", str(exc))
            return
        if added_basemap and project.crs().authid() in ("", "EPSG:4326"):
            project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))  # familiar web-map view
        if not aoi.isEditable():
            aoi.startEditing()
        self.iface.setActiveLayer(aoi)
        self.iface.actionAddFeature().trigger()
        hint = "Draw a polygon, finish with right-click, then save (Ctrl+S)."
        if added_basemap:
            hint = ("OpenTopoMap added. Zoom to your area (the search bar at the bottom left "
                    "finds place names via its 'Nominatim Geocoder' search), then: " + hint)
        self.iface.messageBar().pushInfo("Kartenwerk", hint)

    def generate_grid(self):
        project = QgsProject.instance()
        try:
            aoi = grid.ensure_aoi_layer(project)
            if aoi.isEditable():
                aoi.commitChanges()
            if aoi.featureCount() == 0:
                self._error("Generate grid", "Please draw a work area first.")
                return
            grid_layer, count, warning = grid.generate_grid(project, aoi)
        except Exception as exc:
            self._error("Generate grid", str(exc))
            return

        grid_layer.removeSelection()
        self._connect_grid()
        canvas = self.iface.mapCanvas()
        canvas.setReferencedExtent(QgsReferencedRectangle(grid_layer.extent(), grid_layer.crs()))
        canvas.refresh()
        zone = crs_mod.zone_label(*grid.grid_zone(grid_layer))
        self.iface.messageBar().pushSuccess(
            "Kartenwerk", f"{count} grid cells created in UTM zone {zone}. Now select the sheets you want."
        )
        if warning:
            self.iface.messageBar().pushWarning("Kartenwerk", warning)

    def select_sheets(self):
        grid_layer = grid.get_grid_layer(QgsProject.instance())
        if grid_layer is None:
            self._error("Select sheets", "Please generate a grid first.")
            return
        self.iface.setActiveLayer(grid_layer)
        self.iface.actionSelect().trigger()
        self.iface.messageBar().pushInfo(
            "Kartenwerk", "Click sheets; hold Ctrl/Shift to select several."
        )

    def number_sheets(self):
        grid_layer = grid.get_grid_layer(QgsProject.instance())
        if grid_layer is None:
            self._error("Number sheets", "There is no grid yet.")
            return
        count = grid.number_selected(grid_layer)
        if count == 0:
            self._error("Number sheets", "No sheets selected.")
            return
        self.iface.messageBar().pushSuccess(
            "Kartenwerk", f"{count} sheets numbered (north → south, west → east)."
        )
        self.update_count()

    def name_sheet(self):
        grid_layer = grid.get_grid_layer(QgsProject.instance())
        if grid_layer is None:
            self._error("Name sheet", "There is no grid yet.")
            return
        features = list(grid_layer.selectedFeatures())
        if len(features) != 1:
            self._error("Name sheet", "Select exactly one sheet to name.")
            return
        feature = features[0]
        current = feature["sheet_name"] or ""
        name, ok = QInputDialog.getText(
            self.iface.mainWindow(), "Kartenwerk – Name sheet",
            f"Name for sheet {feature['sheet_id']} (empty removes the name):",
            text=str(current),
        )
        if not ok:
            return
        try:
            grid.set_sheet_name(grid_layer, feature.id(), name)
        except Exception as exc:
            self._error("Name sheet", str(exc))
            return
        self.iface.messageBar().pushSuccess(
            "Kartenwerk", f"Sheet {feature['sheet_id']} named '{name.strip()}'." if name.strip()
            else f"Name of sheet {feature['sheet_id']} removed.")

    def export_sheets(self):
        project = QgsProject.instance()
        grid_layer = grid.get_grid_layer(project)
        if grid_layer is None:
            self._error("Export", "There is no grid yet.")
            return
        selected = grid.selected_sheets(grid_layer)
        if not selected:
            self._error("Export", "No sheets selected (or the grid is from an older version: "
                                  "please generate it again).")
            return

        settings = config.export_settings()
        progress = QProgressDialog(
            "Creating sheets …", "Cancel", 0, len(selected), self.iface.mainWindow()
        )
        progress.setWindowTitle("Kartenwerk – Export")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def report(index, total, name):
            progress.setValue(index - 1)
            progress.setLabelText(f"Sheet {index}/{total}: {name}")
            QApplication.processEvents()
            return not progress.wasCanceled()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            written = exporter.export_sheets(project, grid_layer, settings, progress=report)
        except Exception as exc:
            progress.close()
            QMessageBox.warning(self.iface.mainWindow(), "Kartenwerk – Export", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        progress.setValue(len(selected))
        progress.close()
        self.update_count()

        out_dir = settings["output_dir"]
        if written:
            extra = f" Print file: {written.print_file.name}." if written.print_file else ""
            self.iface.messageBar().pushSuccess(
                "Kartenwerk", f"{len(written)} PDF(s) written to {out_dir}.{extra}"
            )
            for warning in written.warnings:
                self.iface.messageBar().pushWarning("Kartenwerk", warning)
            if settings["open_folder"]:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir)))
        else:
            self._error("Export", "No PDFs were created.")

    def show_settings(self):
        dlg = dialog.SettingsDialog(self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return
        old_format = config.page_format()
        old_scale = config.scale()
        old_overlap = config.overlap()
        values = dlg.values()
        for key, value in values.items():
            config.set_setting(key, value)
        if (values["page_format"], int(values["scale"]), int(values["overlap"])) != \
                (old_format, old_scale, old_overlap):
            layout.ensure_layout(QgsProject.instance())
            self.iface.messageBar().pushWarning(
                "Kartenwerk", "Page format, scale or overlap changed. Please run 'Generate grid' again."
            )
        else:
            self.iface.messageBar().pushSuccess("Kartenwerk", "Settings saved.")
        self.update_count()

    # ------------------------------------------------------------------
    def _error(self, title: str, message: str):
        QMessageBox.warning(self.iface.mainWindow(), f"Kartenwerk – {title}", message)
