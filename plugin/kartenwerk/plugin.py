"""QGIS plugin entry point and UI."""
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsApplication, QgsProject
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont
from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import config, dialog, exporter, grid, layout


class KartenwerkPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions: list[QAction] = []
        self.toolbar = None
        self.dock = None
        self.count_label = None
        self.export_button = None
        self._grid_layer = None

    # ------------------------------------------------------------------
    # QGIS plugin lifecycle
    # ------------------------------------------------------------------
    def initGui(self):
        self._create_actions()

        self.toolbar = self.iface.addToolBar("Kartenwerk")
        self.toolbar.setObjectName("KartenwerkToolbar")
        for action in self.actions:
            self.toolbar.addAction(action)
            self.iface.addPluginToMenu("Kartenwerk", action)

        self._create_dock()
        QgsProject.instance().layerWasAdded.connect(self._on_layer_added)
        self._connect_grid()
        self.update_count()

    def unload(self):
        try:
            QgsProject.instance().layerWasAdded.disconnect(self._on_layer_added)
        except (TypeError, RuntimeError):
            pass
        if self._grid_layer is not None:
            try:
                self._grid_layer.selectionChanged.disconnect(self.update_count)
            except (TypeError, RuntimeError):
                pass

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
    def _icon(self, name: str):
        return QgsApplication.getThemeIcon(name)

    def _create_actions(self):
        def make(text, icon, slot):
            action = QAction(self._icon(icon), text, self.iface.mainWindow())
            action.triggered.connect(slot)
            self.actions.append(action)
            return action

        make("Arbeitsgebiet zeichnen", "mActionAddPolygon.svg", self.draw_aoi)
        make("Raster erzeugen", "mActionAddTable.svg", self.generate_grid)
        make("Blätter auswählen", "mActionSelect.svg", self.select_sheets)
        make("Nummerieren", "mActionOrderBy.svg", self.number_sheets)
        make("Auswahl exportieren", "mActionSharingExport.svg", self.export_sheets)
        make("Einstellungen", "mActionOptions.svg", self.show_settings)

    def _create_dock(self):
        self.dock = QgsDockWidget("Kartenwerk", self.iface.mainWindow())
        self.dock.setObjectName("KartenwerkDock")
        self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(6)

        title = QLabel("Kartenwerk · A5 · 1:25 000")
        title_font = QFont()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        help_text = QLabel(
            "1. Arbeitsgebiet zeichnen\n"
            "2. Raster erzeugen\n"
            "3. Blätter auswählen (Klick / Strg / Shift)\n"
            "4. Optional nummerieren\n"
            "5. Auswahl exportieren"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: palette(mid);")
        layout.addWidget(help_text)

        buttons = [
            ("Arbeitsgebiet zeichnen", self.draw_aoi),
            ("Raster erzeugen", self.generate_grid),
            ("Blätter auswählen", self.select_sheets),
            ("Nummerieren", self.number_sheets),
            ("Auswahl exportieren", self.export_sheets),
            ("Einstellungen", self.show_settings),
        ]
        for text, slot in buttons:
            button = QPushButton(text)
            button.clicked.connect(slot)
            layout.addWidget(button)
            if text == "Auswahl exportieren":
                self.export_button = button

        self.count_label = QLabel("Ausgewählt: 0 Blätter")
        self.count_label.setFont(title_font)
        layout.addWidget(self.count_label)
        layout.addStretch(1)

        self.dock.setWidget(widget)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.show()

    # ------------------------------------------------------------------
    # grid layer / selection tracking
    # ------------------------------------------------------------------
    def _on_layer_added(self, layer):
        if layer.name() == config.GRID_LAYER:
            self._connect_grid()

    def _connect_grid(self):
        layer = grid.get_layer(QgsProject.instance(), config.GRID_LAYER)
        if layer is self._grid_layer:
            return
        if self._grid_layer is not None:
            try:
                self._grid_layer.selectionChanged.disconnect(self.update_count)
            except (TypeError, RuntimeError):
                pass
        self._grid_layer = layer
        if layer is not None:
            layer.selectionChanged.connect(self.update_count)
        self.update_count()

    def update_count(self):
        if self._grid_layer is None:
            self._grid_layer = grid.get_layer(QgsProject.instance(), config.GRID_LAYER)
        count = len(self._grid_layer.selectedFeatures()) if self._grid_layer else 0
        if self.count_label is not None:
            self.count_label.setText(f"Ausgewählt: {count} Blätter")
        if self.export_button is not None:
            self.export_button.setEnabled(count > 0)

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def draw_aoi(self):
        project = QgsProject.instance()
        try:
            aoi, _ = grid.ensure_layers(project)
        except Exception as exc:
            self._error("Arbeitsgebiet", str(exc))
            return
        if not aoi.isEditable():
            aoi.startEditing()
        self.iface.setActiveLayer(aoi)
        self.iface.actionAddFeature().trigger()
        self.iface.messageBar().pushInfo(
            "Kartenwerk",
            "Polygon zeichnen, mit Rechtsklick abschließen, dann Speichern (Strg+S).",
        )

    def generate_grid(self):
        project = QgsProject.instance()
        try:
            aoi, grid_layer = grid.ensure_layers(project)
            if aoi.isEditable():
                aoi.commitChanges()
            if aoi.featureCount() == 0:
                self._error("Raster erzeugen", "Bitte zuerst ein Arbeitsgebiet zeichnen.")
                return
            count = grid.generate_grid(project, aoi, grid_layer)
        except Exception as exc:
            self._error("Raster erzeugen", str(exc))
            return

        if count == 0:
            self._error(
                "Raster erzeugen",
                "Keine Rasterzellen erzeugt. Bitte das Arbeitsgebiet prüfen.",
            )
            return

        grid_layer.removeSelection()
        self._grid_layer = grid_layer
        self._connect_grid()
        canvas = self.iface.mapCanvas()
        canvas.setExtent(grid_layer.extent())
        canvas.refresh()
        self.iface.messageBar().pushSuccess(
            "Kartenwerk",
            f"{count} Rasterzellen erzeugt. Jetzt die gewünschten Blätter auswählen.",
        )
        self.update_count()

    def select_sheets(self):
        project = QgsProject.instance()
        grid_layer = grid.get_layer(project, config.GRID_LAYER)
        if grid_layer is None:
            self._error("Blätter auswählen", "Bitte zuerst ein Raster erzeugen.")
            return
        self.iface.setActiveLayer(grid_layer)
        self.iface.actionSelect().trigger()
        self.iface.messageBar().pushInfo(
            "Kartenwerk",
            "Blätter anklicken; mit Strg/Shift mehrere auswählen.",
        )

    def number_sheets(self):
        grid_layer = grid.get_layer(QgsProject.instance(), config.GRID_LAYER)
        if grid_layer is None:
            self._error("Nummerieren", "Kein Raster vorhanden.")
            return
        count = grid.number_selected(grid_layer)
        if count == 0:
            self._error("Nummerieren", "Keine Blätter ausgewählt.")
            return
        self.iface.messageBar().pushSuccess(
            "Kartenwerk", f"{count} Blätter nummeriert (Norden → Süden, Westen → Osten)."
        )
        self.update_count()

    def export_sheets(self):
        project = QgsProject.instance()
        grid_layer = grid.get_layer(project, config.GRID_LAYER)
        if grid_layer is None:
            self._error("Export", "Kein Raster vorhanden.")
            return
        selected = grid.selected_sheets(grid_layer)
        if not selected:
            self._error("Export", "Keine Blätter ausgewählt.")
            return

        settings = {
            "output_dir": str(config.get_setting("output_dir")),
            "topo_style": str(config.get_setting("topo_style")),
            "imagery": str(config.get_setting("imagery")),
            "front_layer": str(config.get_setting("front_layer") or ""),
            "back_layer": str(config.get_setting("back_layer") or ""),
            "front_format": str(config.get_setting("front_format")),
            "back_format": str(config.get_setting("back_format")),
            "jpeg_quality": int(config.get_setting("jpeg_quality")),
            "front_source": str(config.get_setting("front_source")),
            "back_source": str(config.get_setting("back_source")),
            "page_format": str(config.get_setting("page_format")),
            "dpi": int(config.get_setting("dpi")),
            "auto_number": bool(config.get_setting("auto_number")),
            "open_folder": bool(config.get_setting("open_folder")),
        }

        progress = QProgressDialog(
            "Blätter werden erzeugt …", "Abbrechen", 0, len(selected),
            self.iface.mainWindow(),
        )
        progress.setWindowTitle("Kartenwerk – Export")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def report(index, total, name):
            progress.setValue(index - 1)
            progress.setLabelText(f"Blatt {index}/{total}: {name}")
            QApplication.processEvents()
            return not progress.wasCanceled()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            written = exporter.export_sheets(
                project, grid_layer, settings, progress=report
            )
        except Exception as exc:
            progress.close()
            QMessageBox.warning(
                self.iface.mainWindow(), "Kartenwerk – Export", str(exc)
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        progress.setValue(len(selected))
        progress.close()

        out_dir = config.output_dir()
        if written:
            self.iface.messageBar().pushSuccess(
                "Kartenwerk",
                f"{len(written)} PDF(s) in {out_dir} erzeugt.",
            )
            if settings.get("open_folder"):
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir)))
        else:
            self._error("Export", "Es wurden keine PDFs erzeugt.")

    def show_settings(self):
        dlg = dialog.SettingsDialog(self.iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return
        old_format = config.page_format()
        old_scale = config.scale()
        values = dlg.values()
        for key, value in values.items():
            config.set_setting(key, value)
        if values.get("page_format") != old_format or int(values.get("scale")) != old_scale:
            layout.ensure_layouts(QgsProject.instance())
            self.iface.messageBar().pushWarning(
                "Kartenwerk",
                "Seitenformat oder Maßstab geändert. Bitte 'Raster erzeugen' erneut ausführen.",
            )
        else:
            self.iface.messageBar().pushSuccess("Kartenwerk", "Einstellungen gespeichert.")

    # ------------------------------------------------------------------
    def _error(self, title: str, message: str):
        QMessageBox.warning(
            self.iface.mainWindow(), f"Kartenwerk – {title}", message
        )
