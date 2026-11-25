"""Settings dialog."""
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsMapLayer, QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import config, crs as crs_mod, grid, sources


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kartenwerk – Settings")
        self.setMinimumWidth(560)

        self.output_edit = QLineEdit(str(config.get_setting("output_dir") or ""))
        self.output_edit.setPlaceholderText(str(config.default_output_dir()))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        output_row = self._row(self.output_edit, browse)

        self.page_combo = QComboBox()
        for name in ("A2", "A3", "A4", "A5"):
            w, h = config.PAGE_FORMATS[name]
            self.page_combo.addItem(f"{name} ({w:.0f} × {h:.0f} mm)", name)
        self._select_data(self.page_combo, config.page_format())

        self.scale_combo = QComboBox()
        for denom in sorted(config.SCALES):
            self.scale_combo.addItem(config.SCALES[denom], denom)
        self._select_data(self.scale_combo, config.scale())

        self.overlap_combo = QComboBox()
        for percent, text in config.OVERLAPS.items():
            self.overlap_combo.addItem(text, percent)
        self.overlap_combo.setToolTip("Neighbouring sheets share this strip, so paths near an edge "
                                      "also appear on the next sheet. A dashed line marks where "
                                      "the next sheet begins.")
        self._select_data(self.overlap_combo, config.overlap())

        self.front_combo = QComboBox()
        self.back_combo = QComboBox()
        self._populate_basemaps(
            str(config.get_setting("front_basemap") or config.AUTO),
            str(config.get_setting("back_basemap") or config.AUTO),
        )
        self.front_combo.currentIndexChanged.connect(self._update_auto_note)
        self.back_combo.currentIndexChanged.connect(self._update_auto_note)

        refresh_button = QPushButton("Refresh")
        refresh_button.setToolTip("Reload the list after adding a layer to the project")
        refresh_button.clicked.connect(lambda: self._populate_basemaps())
        swap_button = QPushButton("Swap")
        swap_button.setToolTip("Swap front and back basemap, format and attribution")
        swap_button.clicked.connect(self._swap)
        front_row = self._row(self.front_combo, refresh_button)
        back_row = self._row(self.back_combo, swap_button)

        self.auto_note = QLabel()
        self.auto_note.setWordWrap(True)
        self.auto_note.setStyleSheet("color: palette(mid);")

        self.front_format = QComboBox()
        self.back_format = QComboBox()
        for combo in (self.front_format, self.back_format):
            combo.addItem("PNG (lossless)", "PNG")
            combo.addItem("JPEG (faster, smaller)", "JPEG")
        self._select_data(self.front_format, str(config.get_setting("front_format")).upper())
        self._select_data(self.back_format, str(config.get_setting("back_format")).upper())
        self.front_format.currentIndexChanged.connect(self._update_quality_enabled)
        self.back_format.currentIndexChanged.connect(self._update_quality_enabled)

        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(50, 100)
        self.jpeg_quality.setSuffix(" %")
        self.jpeg_quality.setValue(config.get_int("jpeg_quality"))

        self.front_attribution = QLineEdit(str(config.get_setting("front_attribution") or ""))
        self.back_attribution = QLineEdit(str(config.get_setting("back_attribution") or ""))
        for edit in (self.front_attribution, self.back_attribution):
            edit.setPlaceholderText("Automatic (from the basemap)")

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(150, 600)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(config.get_int("dpi"))

        self.auto_number = QCheckBox("Number sheets automatically on export")
        self.auto_number.setChecked(config.get_bool("auto_number"))
        self.open_folder = QCheckBox("Open the output folder after export")
        self.open_folder.setChecked(config.get_bool("open_folder"))

        note = QLabel(
            "Page format, scale and overlap set the grid cell size. Regenerate the grid after "
            "changing them. "
            "Coordinates are WGS 84 / UTM; the zone is chosen from the work area."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")

        form = QFormLayout()
        form.addRow("Output folder", output_row)
        form.addRow("Page format", self.page_combo)
        form.addRow("Scale", self.scale_combo)
        form.addRow("Sheet overlap", self.overlap_combo)
        form.addRow("", note)
        form.addRow("Front (map)", front_row)
        form.addRow("Back (imagery)", back_row)
        form.addRow("", self.auto_note)
        form.addRow("Front format", self.front_format)
        form.addRow("Back format", self.back_format)
        form.addRow("JPEG quality", self.jpeg_quality)
        form.addRow("Front attribution", self.front_attribution)
        form.addRow("Back attribution", self.back_attribution)
        form.addRow("Resolution (dpi)", self.dpi_spin)
        form.addRow("", self.auto_number)
        form.addRow("", self.open_folder)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        box = QVBoxLayout(self)
        box.addLayout(form)
        box.addWidget(buttons)

        self._update_quality_enabled()
        self._update_auto_note()

    # ------------------------------------------------------------------
    @staticmethod
    def _row(main: QWidget, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(main, 1)
        layout.addWidget(button)
        return row

    def _browse(self):
        start = self.output_edit.text() or self.output_edit.placeholderText()
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", start)
        if folder:
            self.output_edit.setText(folder)

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _populate_basemaps(self, front_value=None, back_value=None):
        front_value = self.front_combo.currentData() if front_value is None else front_value
        back_value = self.back_combo.currentData() if back_value is None else back_value

        project_layers = sorted(
            (
                layer for layer in QgsProject.instance().mapLayers().values()
                if layer.type() == QgsMapLayer.RasterLayer
                and not layer.customProperty("kartenwerk_source")
            ),
            key=lambda layer: layer.name().lower(),
        )

        for combo, first_role in ((self.front_combo, sources.TOPO),
                                  (self.back_combo, sources.IMAGERY)):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Automatic (best for the work area)", config.AUTO)
            other = sources.IMAGERY if first_role == sources.TOPO else sources.TOPO
            for role in (first_role, other):
                combo.insertSeparator(combo.count())
                for source in sources.for_role(role):
                    combo.addItem(source.name, source.key)
                    combo.setItemData(combo.count() - 1, source.note or source.attribution,
                                      Qt.ToolTipRole)
            if project_layers:
                combo.insertSeparator(combo.count())
                for layer in project_layers:
                    combo.addItem(f"Project layer: {layer.name()}", f"layer:{layer.name()}")
            combo.blockSignals(False)

        self._select_data(self.front_combo, front_value)
        self._select_data(self.back_combo, back_value)
        self._update_auto_note()

    def _update_auto_note(self, *args):
        if not hasattr(self, "auto_note"):
            return
        try:
            bbox = crs_mod.layer_lonlat_bbox(grid.get_aoi_layer(QgsProject.instance()))
        except Exception:
            bbox = None
        if bbox is None:
            self.auto_note.setText(
                "Automatic uses national services where available, otherwise "
                f"{sources.world_default(sources.TOPO).name} / "
                f"{sources.world_default(sources.IMAGERY).name}."
            )
            return
        front = sources.best_source(sources.TOPO, bbox).name
        back = sources.best_source(sources.IMAGERY, bbox).name
        self.auto_note.setText(f"Automatic for the current work area: front = {front}, back = {back}.")

    def _swap(self):
        for a, b in ((self.front_combo, self.back_combo), (self.front_format, self.back_format)):
            ia, ib = a.currentData(), b.currentData()
            self._select_data(a, ib)
            self._select_data(b, ia)
        fa, ba = self.front_attribution.text(), self.back_attribution.text()
        self.front_attribution.setText(ba)
        self.back_attribution.setText(fa)
        self._update_quality_enabled()

    def _update_quality_enabled(self):
        jpeg = "JPEG" in (self.front_format.currentData(), self.back_format.currentData())
        self.jpeg_quality.setEnabled(jpeg)

    def values(self) -> dict:
        output = self.output_edit.text().strip()
        return {
            "output_dir": str(Path(output).expanduser()) if output else "",
            "page_format": self.page_combo.currentData(),
            "scale": int(self.scale_combo.currentData()),
            "overlap": int(self.overlap_combo.currentData()),
            "front_basemap": self.front_combo.currentData() or config.AUTO,
            "back_basemap": self.back_combo.currentData() or config.AUTO,
            "front_format": self.front_format.currentData(),
            "back_format": self.back_format.currentData(),
            "jpeg_quality": self.jpeg_quality.value(),
            "front_attribution": self.front_attribution.text().strip(),
            "back_attribution": self.back_attribution.text().strip(),
            "dpi": self.dpi_spin.value(),
            "auto_number": self.auto_number.isChecked(),
            "open_folder": self.open_folder.isChecked(),
        }
