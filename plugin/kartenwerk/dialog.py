"""Settings dialog."""
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsMapLayer, QgsProject
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

from . import config


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kartenwerk – Einstellungen")
        self.setMinimumWidth(520)

        self.output_edit = QLineEdit(str(config.get_setting("output_dir")))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_edit, 1)
        output_layout.addWidget(browse)

        self.page_combo = QComboBox()
        for name in ("A2", "A3", "A4", "A5"):
            w, h = config.PAGE_FORMATS[name]
            self.page_combo.addItem(f"{name} ({w:.0f} × {h:.0f} mm)", name)
        self._select_data(self.page_combo, config.page_format())

        self.scale_combo = QComboBox()
        for denom in sorted(config.SCALES):
            self.scale_combo.addItem(config.SCALES[denom], denom)
        self._select_data(self.scale_combo, config.scale())

        self.front_combo = QComboBox()
        self.back_combo = QComboBox()
        self._populate_layers(
            config.get_setting("front_layer") or "",
            config.get_setting("back_layer") or "",
        )

        refresh_button = QPushButton("Aktualisieren")
        refresh_button.clicked.connect(self._refresh_layers)
        front_row = QWidget()
        front_layout = QHBoxLayout(front_row)
        front_layout.setContentsMargins(0, 0, 0, 0)
        front_layout.addWidget(self.front_combo, 1)
        front_layout.addWidget(refresh_button)

        swap_button = QPushButton("Tauschen")
        swap_button.clicked.connect(self._swap)
        back_row = QWidget()
        back_layout = QHBoxLayout(back_row)
        back_layout.setContentsMargins(0, 0, 0, 0)
        back_layout.addWidget(self.back_combo, 1)
        back_layout.addWidget(swap_button)

        self.front_format = QComboBox()
        self.back_format = QComboBox()
        for combo in (self.front_format, self.back_format):
            combo.addItem("PNG (verlustfrei)", "PNG")
            combo.addItem("JPEG (schneller, kleiner)", "JPEG")
        self._select_data(self.front_format, config.get_setting("front_format"))
        self._select_data(self.back_format, config.get_setting("back_format"))
        self.front_format.currentIndexChanged.connect(self._update_quality_enabled)
        self.back_format.currentIndexChanged.connect(self._update_quality_enabled)

        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(50, 100)
        self.jpeg_quality.setSuffix(" %")
        self.jpeg_quality.setValue(int(config.get_setting("jpeg_quality")))

        self.front_source = QLineEdit(str(config.get_setting("front_source")))
        self.back_source = QLineEdit(str(config.get_setting("back_source")))

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(150, 600)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(int(config.get_setting("dpi")))

        self.auto_number = QCheckBox("Blätter beim Export automatisch nummerieren")
        self.auto_number.setChecked(bool(config.get_setting("auto_number")))

        self.open_folder = QCheckBox("Ausgabeordner nach dem Export öffnen")
        self.open_folder.setChecked(bool(config.get_setting("open_folder")))

        note = QLabel(
            "Seitenformat und Maßstab bestimmen die Rasterzellengröße. "
            "Nach einer Änderung bitte das Raster neu erzeugen."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")

        form = QFormLayout()
        form.addRow("Ausgabeordner", output_row)
        form.addRow("Seitenformat", self.page_combo)
        form.addRow("Maßstab", self.scale_combo)
        form.addRow("", note)
        form.addRow("Vorderseite (Karte)", front_row)
        form.addRow("Rückseite (Karte)", back_row)
        form.addRow("Vorderseite Format", self.front_format)
        form.addRow("Rückseite Format", self.back_format)
        form.addRow("JPEG-Qualität", self.jpeg_quality)
        form.addRow("Quellenangabe Vorderseite", self.front_source)
        form.addRow("Quellenangabe Rückseite", self.back_source)
        form.addRow("Auflösung (dpi)", self.dpi_spin)
        form.addRow("", self.auto_number)
        form.addRow("", self.open_folder)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._update_quality_enabled()

    # ------------------------------------------------------------------
    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Ausgabeordner wählen", self.output_edit.text()
        )
        if folder:
            self.output_edit.setText(folder)

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _populate_layers(self, front_value=None, back_value=None):
        front_value = self.front_combo.currentData() if front_value is None else front_value
        back_value = self.back_combo.currentData() if back_value is None else back_value

        layers = [
            layer for layer in QgsProject.instance().mapLayers().values()
            if layer.type() == QgsMapLayer.RasterLayer
        ]
        layers.sort(key=lambda layer: layer.name().lower())

        for combo in (self.front_combo, self.back_combo):
            combo.clear()
            combo.addItem("— Standard (automatisch) —", "")

        for layer in layers:
            self.front_combo.addItem(layer.name(), layer.name())
            self.back_combo.addItem(layer.name(), layer.name())

        self._select_data(self.front_combo, front_value)
        self._select_data(self.back_combo, back_value)

    def _refresh_layers(self):
        self._populate_layers()

    def _swap(self):
        fi = self.front_combo.currentIndex()
        bi = self.back_combo.currentIndex()
        self.front_combo.setCurrentIndex(bi)
        self.back_combo.setCurrentIndex(fi)

        ff = self.front_format.currentIndex()
        bf = self.back_format.currentIndex()
        self.front_format.setCurrentIndex(bf)
        self.back_format.setCurrentIndex(ff)

        fs = self.front_source.text()
        bs = self.back_source.text()
        self.front_source.setText(bs)
        self.back_source.setText(fs)
        self._update_quality_enabled()

    def _update_quality_enabled(self):
        jpeg = (
            self.front_format.currentData() == "JPEG"
            or self.back_format.currentData() == "JPEG"
        )
        self.jpeg_quality.setEnabled(jpeg)

    def values(self) -> dict:
        return {
            "output_dir": str(Path(self.output_edit.text()).expanduser()),
            "page_format": self.page_combo.currentData(),
            "scale": int(self.scale_combo.currentData()),
            "front_layer": self.front_combo.currentData() or "",
            "back_layer": self.back_combo.currentData() or "",
            "front_format": self.front_format.currentData(),
            "back_format": self.back_format.currentData(),
            "jpeg_quality": self.jpeg_quality.value(),
            "front_source": self.front_source.text(),
            "back_source": self.back_source.text(),
            "dpi": self.dpi_spin.value(),
            "auto_number": self.auto_number.isChecked(),
            "open_folder": self.open_folder.isChecked(),
        }
