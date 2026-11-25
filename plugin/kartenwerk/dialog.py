"""Settings dialog (tabs: Map, Printing, Advanced)."""
from __future__ import annotations

from pathlib import Path

from qgis.core import QgsMapLayer, QgsProject
from qgis.gui import QgsColorButton
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import cache, config, crs as crs_mod, grid, layout, sources

_OWN_LAYERS = (config.AOI_LAYER, config.GRID_LAYER, *config.LEGACY_AOI_LAYERS,
               *config.LEGACY_GRID_LAYERS)


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(mid);")
    return label


def _offset_spin(value: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(-config.MAX_DUPLEX_OFFSET_MM, config.MAX_DUPLEX_OFFSET_MM)
    spin.setSingleStep(0.5)
    spin.setDecimals(1)
    spin.setSuffix(" mm")
    spin.setValue(value)
    return spin


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kartenwerk – Settings")
        self.setMinimumWidth(580)

        tabs = QTabWidget()
        tabs.addTab(self._map_tab(), "Map")
        tabs.addTab(self._printing_tab(), "Printing")
        tabs.addTab(self._advanced_tab(), "Advanced")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        box = QVBoxLayout(self)
        box.addWidget(tabs)
        box.addWidget(buttons)

        self._update_quality_enabled()
        self._update_auto_note()
        self._update_two_up()
        self._update_scale_hint()

    # ------------------------------------------------------------------
    # tabs
    # ------------------------------------------------------------------
    def _map_tab(self) -> QWidget:
        self.page_combo = QComboBox()
        for name in ("A2", "A3", "A4", "A5"):
            w, h = config.PAGE_FORMATS[name]
            self.page_combo.addItem(f"{name} ({w:.0f} × {h:.0f} mm)", name)
        self._select_data(self.page_combo, config.page_format())

        self.scale_spin = QSpinBox()
        self.scale_spin.setPrefix("1:")
        self.scale_spin.setRange(config.MIN_SCALE, config.MAX_SCALE)
        self.scale_spin.setSingleStep(config.SCALE_STEP)
        self.scale_spin.setGroupSeparatorShown(True)
        self.scale_spin.setValue(config.scale())
        self.scale_spin.setToolTip(
            f"1:{config.MIN_SCALE:,} to 1:{config.MAX_SCALE:,}, rounded to "
            f"{config.SCALE_ROUND}. Smaller numbers zoom in (larger text, less area per sheet)."
            .replace(",", " "))
        self.scale_spin.editingFinished.connect(self._snap_scale)
        self.scale_spin.valueChanged.connect(self._update_scale_hint)
        self.scale_hint = _note(
            f"Below 1:{config.SHARP_SCALE_LIMIT:,}".replace(",", " ")
            + " OpenTopoMap and Esri imagery are enlarged and look soft; national aerial "
            "images (e.g. DOP20) stay sharp. The 1 km grid lines are 200 mm apart at 1:5 000.")

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
        refresh_button.setToolTip("Reload the lists after adding a layer to the project")
        refresh_button.clicked.connect(self._refresh_layers)
        swap_button = QPushButton("Swap")
        swap_button.setToolTip("Swap front and back basemap, format and attribution")
        swap_button.clicked.connect(self._swap)
        self.auto_note = _note("")

        self.overview_combo = QComboBox()
        self.overview_combo.setToolTip("Map shown behind the sheet diagram on Overview.pdf")
        self.overview_color = QgsColorButton()
        self.overview_color.setColor(QColor(str(config.get_setting("overview_color")
                                                or layout.DEFAULT_OVERVIEW_COLOR)))
        self.overview_color.setDefaultColor(QColor(layout.DEFAULT_OVERVIEW_COLOR))
        self.overview_color.setShowNull(False)
        self.overview_color.setAllowOpacity(False)
        self.overview_color.setColorDialogTitle("Overview: sheet outline and number colour")
        self.overview_color.setToolTip("Sheet outlines and numbers on the overview map. Change "
                                       "it if they are hard to read on your map.")
        self._populate_overview(str(config.get_setting("overview_basemap") or "opentopomap"))

        self.overlay_list = QListWidget()
        self.overlay_list.setMaximumHeight(96)
        self.overlay_list.setToolTip("Your own vector layers (e.g. a GPX route or waypoints), "
                                     "drawn on top of the map with their QGIS style.")
        self._populate_overlays(config.project_overlays())
        self.overlays_back = QCheckBox("Also draw them on the back (on a mirrored back "
                                       "without labels)")
        self.overlays_back.setChecked(config.project_overlays_on_back())

        form = QFormLayout()
        form.addRow("Page format", self.page_combo)
        form.addRow("Scale", self.scale_spin)
        form.addRow("", self.scale_hint)
        form.addRow("Sheet overlap", self.overlap_combo)
        form.addRow("", _note(
            "Page format, scale and overlap set the grid cell size. Regenerate the grid after "
            "changing them. Coordinates are WGS 84 / UTM; the zone is chosen from the work area."))
        form.addRow("Front (map)", self._row(self.front_combo, refresh_button))
        form.addRow("Back (imagery)", self._row(self.back_combo, swap_button))
        form.addRow("", self.auto_note)
        form.addRow("Overview map", self._row(self.overview_combo, self.overview_color))
        form.addRow("Overlays", self.overlay_list)
        form.addRow("", self.overlays_back)
        form.addRow("", _note("Overlays are saved in this project. Add a GPX file to QGIS "
                              "(drag and drop) to make it appear in the list."))
        return self._page(form)

    def _printing_tab(self) -> QWidget:
        self.output_edit = QLineEdit(str(config.get_setting("output_dir") or ""))
        self.output_edit.setPlaceholderText(str(config.default_output_dir()))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)

        self.margin_combo = QComboBox()
        for mm, text in config.PRINTER_MARGINS.items():
            self.margin_combo.addItem(text, mm)
        self.margin_combo.setToolTip("Nothing is printed closer than this to the paper edge. "
                                     "Smaller = bigger map, but text may be cut off on printers "
                                     "that cannot print that close. Regenerate the grid after "
                                     "changing it.")
        self._select_data(self.margin_combo, int(config.safe_mm()))

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(150, 600)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(config.get_int("dpi"))

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

        self.mirror_back = QCheckBox("Mirror the back side (lines up with the front when held "
                                     "against the light)")
        self.mirror_back.setChecked(config.get_bool("mirror_back"))
        self.mirror_back.setToolTip(
            "On: the aerial image on the back is mirrored left↔right, so its features lie exactly "
            "behind the same features on the front.\nOff: the back shows the aerial image "
            "normally oriented, easier to read on its own.")

        self.two_up = QCheckBox()
        self.two_up.setChecked(config.get_bool("two_up"))
        self.two_up.setToolTip("Extra PDF for home printers: two sheets stacked on the next larger "
                               "paper with cut marks. Print it double-sided, 'flip on long edge'.")
        self.page_combo.currentIndexChanged.connect(self._update_two_up)

        dx, dy = config.duplex_offset(two_up=False)
        self.offset_dx, self.offset_dy = _offset_spin(dx), _offset_spin(dy)
        dx2, dy2 = config.duplex_offset(two_up=True)
        self.offset2_dx, self.offset2_dy = _offset_spin(dx2), _offset_spin(dy2)
        test_button = QPushButton("Create duplex test pages")
        test_button.setToolTip("Writes Duplex_test_*.pdf to the output folder")
        test_button.clicked.connect(self._duplex_test)

        form = QFormLayout()
        form.addRow("Output folder", self._row(self.output_edit, browse))
        form.addRow("Printer margin", self.margin_combo)
        form.addRow("Resolution (dpi)", self.dpi_spin)
        form.addRow("Front format", self.front_format)
        form.addRow("Back format", self.back_format)
        form.addRow("JPEG quality", self.jpeg_quality)
        form.addRow("", self.mirror_back)
        form.addRow("", self.two_up)
        form.addRow("Duplex offset, sheet PDFs",
                    self._row(self.offset_dx, self.offset_dy, labels=("right", "down")))
        form.addRow("Duplex offset, 2-per-page",
                    self._row(self.offset2_dx, self.offset2_dy, labels=("right", "down")))
        form.addRow("", test_button)
        form.addRow("", _note(
            "Most duplex printers shift the back by 1–2 mm. Print the test pages, hold the sheet "
            "against a window, look at the back and enter where the front's cross appears on "
            "its rulers. The test pages are always printed without offset, so the reading "
            "replaces the old value."))
        return self._page(form)

    def _advanced_tab(self) -> QWidget:
        self.front_attribution = QLineEdit(str(config.get_setting("front_attribution") or ""))
        self.back_attribution = QLineEdit(str(config.get_setting("back_attribution") or ""))
        for edit in (self.front_attribution, self.back_attribution):
            edit.setPlaceholderText("Automatic (from the basemap)")

        self.auto_number = QCheckBox("Number unnumbered sheets automatically on export "
                                     "(existing numbers are kept)")
        self.auto_number.setChecked(config.get_bool("auto_number"))
        self.open_folder = QCheckBox("Open the output folder after export")
        self.open_folder.setChecked(config.get_bool("open_folder"))

        self.render_cache = QCheckBox("Keep downloaded maps for faster re-exports")
        self.render_cache.setChecked(config.get_bool("render_cache"))
        self.render_cache.setToolTip(
            f"Stores rendered map images for {cache.MAX_AGE_DAYS} days (max. "
            f"{cache.MAX_BYTES // 1024 ** 3} GB), so exporting a sheet again downloads nothing. "
            "Esri imagery is never stored (its terms do not allow it).")
        self.cache_label = QLabel()
        clear_button = QPushButton("Clear cache")
        clear_button.clicked.connect(self._clear_cache)
        self._update_cache_label()

        form = QFormLayout()
        form.addRow("Front attribution", self.front_attribution)
        form.addRow("Back attribution", self.back_attribution)
        form.addRow("", self.auto_number)
        form.addRow("", self.open_folder)
        form.addRow("", self.render_cache)
        form.addRow("Cache", self._row(self.cache_label, clear_button))
        return self._page(form)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _page(form: QFormLayout) -> QWidget:
        page = QWidget()
        page.setLayout(form)
        return page

    @staticmethod
    def _row(*widgets, labels=None) -> QWidget:
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        for index, widget in enumerate(widgets):
            if labels:
                box.addWidget(QLabel(labels[index]))
            box.addWidget(widget, 1 if index == 0 and not labels else 0)
        if labels:
            box.addStretch(1)
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

    def _refresh_layers(self):
        self._populate_basemaps()
        self._populate_overview(self.overview_combo.currentData())
        self._populate_overlays(self.overlay_ids())

    def _populate_overview(self, value: str):
        combo = self.overview_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("None (plain sheet diagram)", "none")
        for role in (sources.TOPO, sources.IMAGERY):
            combo.insertSeparator(combo.count())
            for source in sources.for_role(role):
                combo.addItem(source.name, source.key)
                combo.setItemData(combo.count() - 1, source.note or source.attribution,
                                  Qt.ToolTipRole)
        rasters = sorted((layer for layer in QgsProject.instance().mapLayers().values()
                          if layer.type() == QgsMapLayer.RasterLayer
                          and not layer.customProperty("kartenwerk_source")),
                         key=lambda layer: layer.name().lower())
        if rasters:
            combo.insertSeparator(combo.count())
            for layer in rasters:
                combo.addItem(f"Project layer: {layer.name()}", f"layer:{layer.name()}")
        combo.blockSignals(False)
        self._select_data(combo, value)
        if combo.currentData() is None:
            self._select_data(combo, "opentopomap")

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

    def _populate_overlays(self, checked_ids: list[str]):
        self.overlay_list.clear()
        layers = sorted(
            (layer for layer in QgsProject.instance().mapLayers().values()
             if layer.type() == QgsMapLayer.VectorLayer and layer.name() not in _OWN_LAYERS),
            key=lambda layer: layer.name().lower(),
        )
        for layer in layers:
            item = QListWidgetItem(layer.name())
            item.setData(Qt.UserRole, layer.id())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if layer.id() in checked_ids else Qt.Unchecked)
            self.overlay_list.addItem(item)
        if not layers:
            item = QListWidgetItem("(no vector layers in this project)")
            item.setFlags(Qt.NoItemFlags)
            self.overlay_list.addItem(item)

    def overlay_ids(self) -> list[str]:
        ids = []
        for row in range(self.overlay_list.count()):
            item = self.overlay_list.item(row)
            if item.checkState() == Qt.Checked and item.data(Qt.UserRole):
                ids.append(item.data(Qt.UserRole))
        return ids

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

    def _update_two_up(self, *args):
        paper = config.TWO_UP_PAPER.get(self.page_combo.currentData())
        if paper:
            self.two_up.setText(f"Also create a print file with two sheets per {paper} page")
        else:
            self.two_up.setText("Two sheets per page: not available for A2")
        self.two_up.setEnabled(paper is not None)
        self.offset2_dx.setEnabled(paper is not None)
        self.offset2_dy.setEnabled(paper is not None)

    def _snap_scale(self):
        snapped = config.normalize_scale(self.scale_spin.value())
        if snapped != self.scale_spin.value():
            self.scale_spin.setValue(snapped)

    def _update_scale_hint(self, *args):
        self.scale_hint.setVisible(self.scale_spin.value() < config.SHARP_SCALE_LIMIT)

    def _update_quality_enabled(self):
        jpeg = "JPEG" in (self.front_format.currentData(), self.back_format.currentData())
        self.jpeg_quality.setEnabled(jpeg)

    def _update_cache_label(self):
        try:
            size = cache.size_bytes() / 1024 ** 2
            self.cache_label.setText(f"{size:.0f} MB in {len(cache.entries())} map images")
        except OSError:
            self.cache_label.setText("not available")

    def _clear_cache(self):
        cache.clear()
        self._update_cache_label()

    def _duplex_test(self):
        # Uses the page format chosen in the dialog, even before OK is pressed.
        old_format = config.get_setting("page_format")
        config.set_setting("page_format", self.page_combo.currentData())
        try:
            output = self.output_edit.text().strip()
            out_dir = Path(output).expanduser() if output else config.default_output_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            files = layout.export_duplex_test(QgsProject.instance(), out_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Kartenwerk – Duplex test", str(exc))
            return
        finally:
            config.set_setting("page_format", old_format)
        QMessageBox.information(
            self, "Kartenwerk – Duplex test",
            "Written:\n" + "\n".join(str(f) for f in files)
            + "\n\nPrint double-sided at actual size, then follow the instructions on the back.")

    # ------------------------------------------------------------------
    def values(self) -> dict:
        """Settings stored in the QGIS profile (see project_values for per-project ones)."""
        output = self.output_edit.text().strip()
        return {
            "output_dir": str(Path(output).expanduser()) if output else "",
            "page_format": self.page_combo.currentData(),
            "scale": config.normalize_scale(self.scale_spin.value()),
            "overlap": int(self.overlap_combo.currentData()),
            "printer_margin": int(self.margin_combo.currentData()),
            "front_basemap": self.front_combo.currentData() or config.AUTO,
            "back_basemap": self.back_combo.currentData() or config.AUTO,
            "front_format": self.front_format.currentData(),
            "back_format": self.back_format.currentData(),
            "jpeg_quality": self.jpeg_quality.value(),
            "front_attribution": self.front_attribution.text().strip(),
            "back_attribution": self.back_attribution.text().strip(),
            "dpi": self.dpi_spin.value(),
            "auto_number": self.auto_number.isChecked(),
            "two_up": self.two_up.isChecked(),
            "render_cache": self.render_cache.isChecked(),
            "mirror_back": self.mirror_back.isChecked(),
            "overview_basemap": self.overview_combo.currentData() or "opentopomap",
            "overview_color": self.overview_color.color().name(),
            "duplex_dx": self.offset_dx.value(),
            "duplex_dy": self.offset_dy.value(),
            "duplex_2up_dx": self.offset2_dx.value(),
            "duplex_2up_dy": self.offset2_dy.value(),
            "open_folder": self.open_folder.isChecked(),
        }

    def project_values(self) -> tuple[list[str], bool]:
        """(overlay layer ids, overlays on back) — saved in the project."""
        return self.overlay_ids(), self.overlays_back.isChecked()
