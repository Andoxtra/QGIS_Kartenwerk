# Licence notice: code vs. map content

## The code: public domain (Unlicense)

All code and files in this repository — the QGIS plugin, scripts, tests,
documentation and the plugin icon, except the example maps in `examples/`
(see below) — are released into the public domain under
the [Unlicense](LICENSE). You may use, copy, modify and sell them for any
purpose without asking and without attribution.

The World Magnetic Model coefficients (`plugin/kartenwerk/WMM2025.COF`) and
their test values (`dev/data/WMM2025_TestValues.txt`) are published by NOAA
NCEI and the British Geological Survey and are in the public domain as well.

## The maps: NOT covered by the Unlicense

The PDFs this plugin produces contain map data and imagery that the plugin
downloads from third-party services at export time. That content is not part
of this repository and is **not** public domain: it stays under the licence and
terms of use of its provider. The Unlicense only covers the code.

| Source | Provider's licence / terms |
|---|---|
| OpenTopoMap | CC-BY-SA, data © OpenStreetMap contributors (ODbL) |
| Esri World Imagery | Esri terms of use; attribution required |
| TopPlusOpen (Germany) | © BKG, CC BY 4.0 |
| DOP20 Baden-Württemberg | © LGL BW, dl-de/by-2-0 |
| DOP40 Bavaria | © Bayerische Vermessungsverwaltung, CC BY 4.0 |
| PDOK Luchtfoto (Netherlands) | © Beeldmateriaal Nederland, CC BY 4.0 |
| IGN orthophotos (France) | © IGN, Licence Ouverte 2.0 |
| swisstopo map / SWISSIMAGE (Switzerland) | © swisstopo |
| basemap.at Orthofoto (Austria) | © basemap.at, CC BY 4.0 |
| USGS Topo / Imagery (USA) | public domain |
| Your own project layers | whatever licence that data has |

What this means for the maps you make:

- Keep the attribution that is printed on every sheet.
- Share-alike: maps made from OpenTopoMap that you publish or pass on must
  themselves be under CC-BY-SA.
- Commercial use (e.g. selling printed maps) can be restricted by a provider's
  terms, notably Esri's. Check the terms of the sources you use.
- Private use for yourself is fine with all of the sources above.

## The example maps in `examples/`

`examples/Sheet_05_32N-R2985C0167.pdf` is an export made with this plugin, and
`examples/example_preview.jpg` is an image of it. Both contain third-party map
content, so they are **not** public domain:

- front: TopPlusOpen, © BKG (www.bkg.bund.de), CC BY 4.0
- back: DOP20, © LGL BW (www.lgl-bw.de), dl-de/by-2-0

Both licences allow sharing it with attribution, which is printed on the sheet.

QGIS itself (GPL) is not part of this repository; the plugin only runs inside
it.
