# Kartenwerk – A2–A5 map series (Baden-Württemberg)

A reusable QGIS project and plugin that turns a working area into landscape
map sheets (A2, A3, A4 or A5) at a selectable scale (default 1:15 000):

- **Front:** a topographic map (default OpenTopoMap) with an unlabelled 1 km
  UTM grid. The white border carries the scale, north arrow, SW coordinate and
  neighbour indicators; the map itself stays free of text.
- **Back:** the same extent as aerial imagery (default LGL-BW DOP20),
  **mirrored horizontally** so that the back can be printed duplex and
  folded/registered against the front.
- **Selection:** draw an AOI, generate a grid buffered by one cell, then select
  the sheets you actually want to print.
- **Configurable:** front and back layers, PNG/JPEG output, the page format
  (A2–A5) and the map scale (1:10 000 – 1:25 000).

The workflow was built for Baden-Württemberg; the topographic front uses
OpenTopoMap (worldwide, © OpenStreetMap contributors) and the aerial back uses
LGL-BW DOP20 (Baden-Württemberg only). TopPlusOpen remains available as an
alternative in the project.

---

## Requirements

- **QGIS 3.28+** (tested with QGIS 3.34.4 Prizren).
- Internet access to:
  - OpenTopoMap tiles: `https://tile.opentopomap.org/{z}/{x}/{y}.png`
  - TopPlusOpen WMS: `https://sgx.geodatenzentrum.de/wms_topplus_open`
  - LGL-BW DOP20 RGB/CIR WMS: `https://owsproxy.lgl-bw.de/...`
- **pdfunite** (package `poppler-utils`) **or** Ghostscript (`gs`) for
  two-page PDFs and the merged `alle_blaetter.pdf`. Without either, the export
  cannot merge the front/back pages.

The plugin itself uses only PyQGIS; no additional Python packages are needed.

---

## Installation

The project is already installed in this environment, but the installer makes
the setup reproducible:

```bash
cd ~/Documents/QGIS_Kartenwerk
python3 install_plugin.py
```

This links `plugin/kartenwerk` into the active QGIS profile and adds
`kartenwerk=true` to the `[PythonPlugins]` section of the profile's
`QGIS/QGIS3.ini`.

Options:

```bash
python3 install_plugin.py --profile default   # choose a profile
python3 install_plugin.py --copy              # copy instead of symlink
python3 install_plugin.py --force             # replace an existing install
```

Restart QGIS. The plugin appears under **Plugins → Kartenwerk** and as a
toolbar/dock named **Kartenwerk**.

Manual installation (if you prefer):

1. Copy or link `plugin/kartenwerk` to
   `~/.local/share/QGIS/QGIS3/profiles/<profile>/python/plugins/kartenwerk`.
2. Add `kartenwerk=true` under `[PythonPlugins]` in
   `~/.local/share/QGIS/QGIS3/profiles/<profile>/QGIS/QGIS3.ini`.
3. Restart QGIS and enable **Kartenwerk** in
   **Plugins → Manage and Install Plugins → Installed** if necessary.

---

## Quick start

1. Open `~/Documents/QGIS_Kartenwerk/kartenwerk.qgz`.
2. **Arbeitsgebiet zeichnen** – draw a polygon in the `arbeitsgebiet` layer,
   finish with right-click, then save with `Ctrl+S`.
3. **Raster erzeugen** – creates grid cells buffered by one cell around the
   AOI. The cell size follows the selected page format and scale (e.g.
   2.97 × 2.04 km for A5 at 1:15 000).
4. **Blätter auswählen** – click cells on the map; use `Ctrl` / `Shift` for
   multiple selection.
5. Optional: **Nummerieren** – numbers the selected sheets north → south,
   west → east.
6. **Auswahl exportieren** – writes the PDFs to the output folder.

The dock shows the current selection count and disables the export button while
nothing is selected.

---

## Settings

**Einstellungen** opens the plugin settings:

| Setting | Default | Description |
|---|---|---|
| Ausgabeordner | `~/Documents/QGIS_Kartenwerk/ausgabe` | Output directory |
| Seitenformat | `A5` | `A2`, `A3`, `A4` or `A5` landscape. Changes the grid cell size; regenerate the grid after switching. |
| Maßstab | `1:15 000` | `1:10 000`, `1:12 500`, `1:15 000`, `1:20 000`, `1:25 000`. A larger scale (smaller denominator) makes text bigger/more readable but covers less area per sheet. |
| Vorderseite (Karte) | automatic | Any raster layer in the project; defaults to OpenTopoMap. |
| Rückseite (Karte) | automatic | Any raster layer in the project; defaults to DOP20 RGB. |
| Vorderseite/Rückseite Format | `PNG` | `PNG` (lossless) or `JPEG` (faster, smaller). |
| JPEG-Qualität | `92` | 50–100, used when either side is JPEG. |
| Quellenangabe Vorderseite/Rückseite | OpenTopoMap / LGL BW | Attribution text printed in the border. |
| Auflösung (dpi) | `300` | Layout/render resolution |
| Automatisch nummerieren | on | Number unnumbered selected sheets on export |
| Ausgabeordner öffnen | on | Open the folder after a successful export |

The **Aktualisieren** button reloads the layer lists (after you add a new layer
in QGIS); **Tauschen** swaps front/back layers, formats and attributions.
Settings are stored under `kartenwerk/*` in QGIS settings.

---

## Output

For each selected sheet:

```
ausgabe/
├── Uebersicht.pdf            # overview sheet: grid + list of all sheet numbers/IDs
├── Blatt_01_R1592C0094.pdf   # 2 pages: front topo, back mirrored aerial
├── Blatt_02_R1592C0095.pdf
├── alle_blaetter.pdf         # merged (overview first), when more than one sheet
└── index.csv                 # ;-separated, UTF-8 with BOM
```

`Uebersicht.pdf` is a single extra page that shows the selected sheets arranged
as a grid (each cell labelled with its number and ID) plus a list of all sheet
numbers and IDs, so you can see at a glance which map covers which area.

`index.csv` columns:

```
blatt;sheet_id;name;e_min;e_max;n_min;n_max;datei
```

`sheet_id` is `R<row>C<column>` from the fixed grid origin (see below). The
`blatt` column is the assigned sheet number (zero-padded in the filename).

Temporary front/back images and single-page PDFs are written to `cache/` and
deleted after each sheet.

---

## Sheet geometry

The page format and the scale are selectable; the map frame is always the page
minus a 6 mm white margin, and one cell is the map frame at the chosen scale.
Example for A5 at the default scale 1:15 000:

| Property | Value |
|---|---|
| Page | A5 landscape, 210 × 148 mm |
| White margin | 6 mm |
| Map frame | 198 × 136 mm |
| Scale | 1:15 000 (selectable 1:10 000 – 1:25 000) |
| Ground size per sheet | 2 970 × 2 040 m |
| Grid | 1 km = 66.7 mm |
| CRS | EPSG:25832 (UTM 32N) |
| Grid origin | E 0, N 0 (fixed) |
| Render resolution | 300 dpi → 2 339 × 1 606 px map frame |

At 1:25 000 the same A5 sheet covers 4 950 × 3 400 m (grid 1 km = 40 mm); for
A2/A3/A4 the map frame, cell size and pixel dimensions scale accordingly.
Because the grid origin is fixed, sheet IDs stay stable between runs. A sheet
ID such as `R1592C0094` is row 1592, column 94 of the fixed grid.

---

## Front / back details

- The **front** uses the selected map layer and draws an unlabelled 1 km UTM
  grid. The map area itself has no title or coordinate block.
- The **back** uses the selected imagery layer. The basemap and the 1 km grid
  lines are rendered to an image and flipped horizontally with
  `QImage.mirrored(True, False)`. All border text is drawn unflipped in the
  layout, so it remains readable. Each side can be saved as PNG (lossless) or
  JPEG (quality 50–100).
- The **border** contains: sheet number and name/ID (top left), north arrow
  (top right), scale as text (bottom left), SW corner coordinate (bottom
  right), attribution, and the four neighbour indicators. The border is 6 mm
  wide; the neighbour text is 8 pt bold.
- On the back, the **left/right neighbour indicators are swapped** to match the
  mirrored geography; north/south stay in place. The top/bottom indicators use
  ▲/▼; the left/right indicators are simply placed in the side borders.
- Only the SW corner coordinate is printed; the other sheet-edge coordinates
  are given by the neighbour indicators. The 1 km grid lines are unlabelled,
  which keeps the map clean for the limited southern-Germany area.
- Missing neighbours show `—`.

---

## Project layout

```
QGIS_Kartenwerk/
├── kartenwerk.qgz              # QGIS project (layers, themes, layouts)
├── kartenwerk.gpkg             # arbeitsgebiet + raster layers
├── setup_project.py            # rebuild project, themes and layouts
├── install_plugin.py           # install/enable the plugin
├── plugin/kartenwerk/
│   ├── __init__.py             # classFactory
│   ├── metadata.txt
│   ├── plugin.py               # toolbar, dock, actions
│   ├── dialog.py               # settings dialog
│   ├── config.py               # geometry, services, settings
│   ├── services.py             # WMS layers, groups, map themes
│   ├── grid.py                 # AOI/grid, numbering, neighbours
│   ├── render.py               # map rendering, grid lines, flip
│   ├── layout.py               # layout creation/update/PDF export
│   └── exporter.py             # batch export, merge, index.csv
├── ausgabe/                    # output
├── cache/                      # temporary render files
└── dev/                        # headless tests and test outputs
```

`setup_project.py` recreates `kartenwerk.qgz` from scratch (layers, map themes,
`Vorderseite`/`Rueckseite` layouts). It does **not** delete your AOI or grid
features in `kartenwerk.gpkg`.

---

## Headless tests

The core export path can be tested without the QGIS GUI:

```bash
cd ~/Documents/QGIS_Kartenwerk
QT_QPA_PLATFORM=offscreen /usr/bin/python3 dev/test_core.py
```

This creates a test GeoPackage, generates a grid, selects two sheets and writes
two-page PDFs to `dev/out/`. `pdfinfo` can verify the result:

```bash
pdfinfo dev/out/Blatt_01_*.pdf | grep -E 'Pages|Page size'
```

Rebuild the project and layouts:

```bash
QT_QPA_PLATFORM=offscreen /usr/bin/python3 setup_project.py
```

Note: use `/usr/bin/python3` for the headless tests; it is the system Python
that has PyQGIS. The plugin itself runs inside QGIS's bundled Python.

---

## Troubleshooting

- **No map / blank WMS:** check internet/proxy access and that the service
  layers are still available. All services are requested in EPSG:25832.
- **Aerial back is blank outside BW:** LGL-BW DOP20 only covers
  Baden-Württemberg. The topographic front (OpenTopoMap) covers the world.
- **OpenTopoMap front has white gaps/fragments:** the tile server throttles or
  drops requests during large batch exports. The plugin now detects mostly-blank
  front pages and re-renders them automatically (up to two retries). For
  guaranteed reliability, switch the front to a WMS (TopPlusOpen) or cache the
  tiles locally. OpenTopoMap is subject to its fair-use tile policy.
- **Export fails with “Weder pdfunite noch Ghostscript gefunden”:** install
  `poppler-utils` or `ghostscript`.
- **Plugin not listed:** check the QGIS Python console for import errors, verify
  the symlink/copy path, and confirm `kartenwerk=true` in `[PythonPlugins]`.
- **Moving the project folder breaks the plugin:** rerun
  `python3 install_plugin.py --force`, or install with `--copy`.
- **QGIS overwrites the enabled state:** close QGIS before running the
  installer; QGIS writes its settings on exit.
- **Slow export:** rendering 300 dpi WMS tiles is sequential by design
  (`QgsMapRendererSequentialJob` is used because the parallel job crashes in
  headless environments). Switch the back side to JPEG and/or lower the DPI to
  speed it up.
