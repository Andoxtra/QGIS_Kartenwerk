# Kartenwerk – printable duplex map series, anywhere in the world

A QGIS plugin that turns a work area into landscape map sheets (A2, A3, A4 or
A5) at a selectable scale (default 1:15 000), ready for double-sided printing:

- **Front:** a topographic map with a 1 km UTM grid, numbered in the border.
  The border also carries the sheet number, a scale bar, grid/true/magnetic
  north, the UTM zone, neighbour sheets, a locator diagram, attribution and
  print date; the map itself stays free of text.
- **Back:** the same extent as aerial imagery, **mirrored horizontally**, so the
  back can be printed duplex and held up against the light in register with
  the front.
- **Selection:** draw a work area, generate a sheet grid buffered by one cell,
  then pick the sheets you actually want to print.
- **Worldwide:** coordinates are always metric WGS 84 / UTM with the zone chosen
  automatically. Basemaps default to worldwide sources and switch to better
  national services automatically where they exist.

---

## Coordinate system: WGS 84 / UTM

Every map series uses **WGS 84 / UTM** (EPSG:326zz north of the equator,
EPSG:327zz south), the worldwide standard for metric topographic map grids.

- The zone (1–60) is chosen from the centre of the work area, e.g. 32N for
  Stuttgart, 13N for Colorado, 54N for Hokkaido, 56S for Sydney.
- Coordinates are easting/northing in metres, printed with spaces as thousands
  separators (`UTM 32N · SW E 435 600 / N 5 317 600`).
- The 1 km grid lines are true UTM kilometre lines, so they also match GPS
  devices set to UTM and MGRS grids.
- The grid origin is fixed at E 0 / N 0 of the zone, so sheet IDs stay stable
  between runs. A sheet ID like `32N-R1564C0088` is zone 32N, row 1564,
  column 88.
- In Europe WGS 84 and ETRS89 differ by less than 1 m, so the coordinates match
  official ETRS89/UTM maps.
- If a work area reaches more than 3° beyond its zone you get a warning (scale
  distortion grows slowly); polar areas beyond 84° N / 80° S are not supported.

---

## Basemap sources

**Automatic** (the default) picks the most specific source whose coverage
contains the selected sheets. If a national service still leaves a sheet
largely empty (the coverage boxes are coarse), that sheet falls back to the
worldwide source and its attribution is adjusted.

| Side | Source | Coverage | Licence / terms |
|---|---|---|---|
| Front | OpenTopoMap | worldwide (default) | CC-BY-SA, © OpenStreetMap contributors. [Fair-use tile policy](https://opentopomap.org/about): avoid very large batch exports. |
| Front | TopPlusOpen | Germany | © BKG, CC BY 4.0 |
| Front | swisstopo national map | Switzerland | © swisstopo |
| Front | USGS Topo | USA (incl. AK, HI) | public domain |
| Back | Esri World Imagery | worldwide (default) | Check [Esri's terms of use](https://www.esri.com/en-us/legal/terms/full-master-agreement); attribution required. |
| Back | DOP20 | Baden-Württemberg | © LGL BW, dl-de/by-2-0 |
| Back | DOP40 | Bavaria | © Bayerische Vermessungsverwaltung, CC BY 4.0 |
| Back | PDOK Luchtfoto | Netherlands | © Beeldmateriaal Nederland, CC BY 4.0 |
| Back | IGN orthophotos | France | © IGN, Licence Ouverte 2.0 |
| Back | SWISSIMAGE | Switzerland | © swisstopo |
| Back | basemap.at Orthofoto | Austria | © basemap.at, CC BY 4.0 |
| Back | USGS Imagery | USA | public domain |

You can also pick any source for either side, or any raster layer you added to
the project (e.g. your own WMS, a GeoTIFF, a national service not listed here).
Esri imagery is limited to zoom level 17, the highest level with real tiles
almost everywhere; in some remote areas Esri's imagery itself is coarse.

New sources are added in `plugin/kartenwerk/sources.py` (one `Source(...)`
entry with URL, coverage box and attribution).

---

## Requirements

- **QGIS 3.28+** on Linux, Windows or macOS (tested with QGIS 3.44 headless).
- Internet access to the basemap services.
- Optional: **pdfunite** (poppler-utils) or **Ghostscript** to also get one
  merged `all_sheets.pdf`. Each sheet PDF is complete without them.

The plugin uses only PyQGIS; no additional Python packages are needed.

---

## Installation

**From ZIP (any platform):**

```bash
python3 install_plugin.py --zip        # writes kartenwerk.zip
```

In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.

**Into a profile (for development):** close QGIS, then

```bash
python3 install_plugin.py              # last-used profile; symlink (copy on Windows)
python3 install_plugin.py --profile default --copy --force
```

This links/copies `plugin/kartenwerk` into the profile's `python/plugins`
folder and sets `kartenwerk=true` in the profile's `QGIS3.ini`. Restart QGIS;
the plugin appears under **Plugins → Kartenwerk** and as a toolbar and dock.

---

## Quick start

1. Open a QGIS project (or `kartenwerk.qgz`, see below) and save it. The
   plugin stores its GeoPackage (`kartenwerk.gpkg`) and the default `output/`
   folder next to the project. Without a saved project they go to
   `<QGIS profile>/kartenwerk/`.
2. **Draw work area** – draw a polygon, finish with right-click, save with
   `Ctrl+S`.
3. **Generate grid** – creates sheet cells in the work area's UTM zone,
   buffered by one cell. The project CRS switches to that zone so the grid
   appears square. The cell size follows the page format and scale (e.g.
   2.97 × 2.04 km for A5 at 1:15 000).
4. **Select sheets** – click cells; use `Ctrl` / `Shift` for several.
5. Optional: **Number sheets** – numbers the selected sheets north → south,
   west → east.
6. **Export selection** – writes the PDFs to the output folder.

The dock shows page format, scale and UTM zone, plus the current selection
count.

---

## Settings

| Setting | Default | Description |
|---|---|---|
| Output folder | `<project folder>/output` | Leave empty to follow the project. |
| Page format | `A5` | `A2`, `A3`, `A4` or `A5` landscape. Regenerate the grid after changing it. |
| Scale | `1:15 000` | `1:10 000` – `1:25 000`. A smaller denominator gives larger, more readable text but less area per sheet. |
| Sheet overlap | `5 %` | None, 5 % or 10 %. Neighbouring sheets share this strip; a dashed line marks where the next sheet begins. Regenerate the grid after changing it. |
| Front (map) / Back (imagery) | Automatic | Automatic, a catalog source, or a raster layer from the project. The dialog shows what Automatic picks for the current work area. |
| Front/Back format | `PNG` | `PNG` (lossless) or `JPEG` (faster, smaller). |
| JPEG quality | `92` | 50–100. |
| Front/Back attribution | automatic | Leave empty to print the source's own attribution. |
| Resolution (dpi) | `300` | Render and PDF resolution. |
| Number sheets automatically | on | Numbers unnumbered selected sheets on export. |
| Open output folder | on | Opens the folder after a successful export. |

**Refresh** reloads the layer list; **Swap** swaps front/back basemap, format
and attribution. Settings are stored under `kartenwerk/*` in the QGIS settings.

---

## Output

```
output/
├── Overview.pdf                    # sheet overview (north up) + list of all sheets
├── Sheet_01_32N-R1564C0088.pdf     # 2 pages: front topo, back mirrored imagery
├── Sheet_02_32N-R1564C0089.pdf
├── all_sheets.pdf                  # overview + all sheets (needs pdfunite or Ghostscript)
└── index.csv                       # comma-separated, UTF-8
```

`index.csv` columns:

```
sheet,sheet_id,utm_zone,epsg,name,e_min,e_max,n_min,n_max,file
```

Temporary images are written to the system temp folder and deleted after each
sheet.

---

## Sheet geometry

Nothing is printed closer than 5 mm to the paper edge, the area most printers
cannot reach. The white border is 12 mm left and right (equal, so the mirrored
back registers with the front), 11 mm at the top and 14 mm at the bottom. One
grid cell is the map frame at the chosen scale minus the overlap. Example for
A5 at 1:15 000 with 5 % overlap:

| Property | Value |
|---|---|
| Page | A5 landscape, 210 × 148 mm |
| Map frame | 186 × 123 mm |
| Ground shown per sheet | 2 790 × 1 845 m |
| Grid step (new ground per sheet) | 2 650.5 × 1 752.75 m |
| Grid | 1 km = 66.7 mm |
| Render resolution | 300 dpi → 2 197 × 1 453 px map frame |

---

## Front / back details

The border, from top to bottom:

- **Top:** sheet number (large) and sheet ID; north neighbour; north arrow with
  the angles from grid north to true north (grid convergence) and to magnetic
  north (declination from NOAA's World Magnetic Model 2025, valid 2025–2030;
  omitted outside that period). The magnetic angle is what a compass user needs.
- **Km labels** at every 1 km grid line on all four sides, UTM style: small
  leading digits + bold kilometre, e.g. `⁴36` = E 436 000, `⁵³18` = N 5 318 000.
- **Sides:** west/east neighbours, rotated to read upwards. A numbered
  neighbour shows its number (`▲ 04`), an unselected one its short ID in grey,
  a missing one `—`.
- **Bottom:** scale bar (stays correct even if the page is printed scaled) and
  scale; south neighbour; UTM zone and SW corner of the printed map; a locator
  diagram showing the sheet within the series; attribution and print date.
- A thin frame line surrounds the map; with overlap, dashed lines inside the
  map mark where the neighbouring sheets begin.

The back image (basemap and grid lines) is flipped in memory with
`QImage.mirrored(True, False)` before it is saved; all border text is drawn
unflipped, so it stays readable. On the back the left/right neighbours, the
easting labels and the locator are mirrored to match the image; the north block
shows only the arrow, since angles measured on a mirrored image are reversed.

---

## Project layout

```
QGIS_Kartenwerk/
├── kartenwerk.qgz              # example QGIS project
├── kartenwerk.gpkg             # work area (+ generated grid)
├── setup_project.py            # rebuild the project (keeps work-area polygons)
├── install_plugin.py           # install into a QGIS profile / build ZIP
├── plugin/kartenwerk/
│   ├── plugin.py               # toolbar, dock, actions
│   ├── dialog.py               # settings dialog
│   ├── config.py               # geometry, paths, settings
│   ├── crs.py                  # UTM zone selection, grid convergence
│   ├── wmm.py, WMM2025.COF     # World Magnetic Model (magnetic declination)
│   ├── sources.py              # basemap catalog + automatic selection
│   ├── services.py             # layer-tree helpers, default basemaps
│   ├── grid.py                 # work area, grid, numbering, neighbours
│   ├── render.py               # map rendering, grid lines, mirroring
│   ├── layout.py               # two-page layout, PDF export, overview
│   └── exporter.py             # batch export, merge, index.csv
└── dev/                        # tests
```

`setup_project.py` recreates `kartenwerk.qgz` (worldwide basemaps, themes
`Topo`/`Imagery`, layout `Kartenwerk`) without deleting work-area polygons.

### Upgrading from version 1 (Baden-Württemberg only)

- Work-area polygons in the old `arbeitsgebiet` table are copied into the new
  `work_area` layer automatically; old projects keep working.
- Old grids (`raster`, EPSG:25832) have no UTM zone in their sheet IDs: run
  **Generate grid** again before exporting.
- Old settings (front/back layer, attribution text) are reset once.
- The old `Vorderseite`/`Rueckseite` layouts are replaced by one two-page
  layout `Kartenwerk`.
- Automatic now uses TopPlusOpen for the front inside Germany. Choose
  **OpenTopoMap** in the settings to keep the previous look.

---

## Tests

Offline (no QGIS needed):

```bash
python3 dev/test_zones.py
```

End-to-end export for several regions (needs PyQGIS and internet; writes to
`dev/out/`):

```bash
QT_QPA_PLATFORM=offscreen python3 dev/test_core.py            # bw nl us jp nz fallback lu bz westland
QT_QPA_PLATFORM=offscreen python3 dev/test_core.py bw nl      # selected regions
```

Without a local QGIS, use the official Docker image:

```bash
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v "$PWD":/work -w /work \
  qgis/qgis:ltr python3 dev/test_core.py
```

---

## Troubleshooting

- **Blank map:** check internet/proxy access to the basemap service.
- **Back shows Esri instead of a national service:** the national service had
  no data for that sheet (outside its coverage), so the plugin fell back.
- **OpenTopoMap front has gaps:** the tile server throttles large exports. The
  plugin retries missing tiles twice; for big series choose a WMS source or a
  national topo map.
- **No `all_sheets.pdf`:** install `poppler-utils` or Ghostscript. The single
  sheet PDFs are complete anyway.
- **"The grid does not match the current page format":** run **Generate grid**
  again after changing page format or scale.
- **Plugin not listed:** check the QGIS Python console for errors and confirm
  `kartenwerk=true` in `[PythonPlugins]` of the profile's `QGIS3.ini`.
- **Slow export:** most of the time is spent waiting for the map servers.
  Downloads are deliberately sequential, one request stream at a time,
  following the tile servers' fair-use policies. Local work does use several
  CPU cores: while the next sheet downloads, up to 4 worker threads mirror and
  encode the previous sheet's images. To speed up further, use JPEG and/or
  lower the dpi.
