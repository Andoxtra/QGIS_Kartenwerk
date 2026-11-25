# A6 border: neighbour labels / locator encroaching on the map

## Context

A6 page format was added in `plugin/kartenwerk/config.py`:

- `PAGE_FORMATS["A6"] = (148.0, 105.0)` (landscape, w x h)
- `TEXT_SCALE["A6"] = 0.87` (all border sizes scale by this)

The border layout lives in `plugin/kartenwerk/layout.py` and was originally tuned
for A5 (210 x 148) and larger. On the 148 mm-wide A6 page the fixed mm sizes no
longer fit side by side.

## A6 geometry (1:15 000, 5 % overlap, 5 mm margin)

- margins: side 11.09 mm, top 10.22 mm, bottom 11.09 mm
- map frame: **125.82 x 83.69 mm**
- lane widths inside the 5 mm safe zone: side ~6.09 mm, top ~5.22 mm, bottom ~6.09 mm

## Suspect #1 - top row (`layout.py:226-232`)

Elements placed in a row only ~3.5 mm tall:

- `title` at `geom.map_x`, width `geom.page_w * 0.42` = **11.1 -> 73.3 mm**
- `neighbor_top` at `page_w/2 - u(20)`, width `u(40)` = **56.6 -> 91.4 mm**
- `north` at `right - u(62)`, width `u(62)` = **83.0 -> 136.9 mm**

Boxes collide (title∩neighbour = 56.6-73.3; neighbour∩north = 83.0-91.4). The
sheet number/name (`_title_html`) and the north neighbour (`_neighbour_html`) can
run into each other and the north arrow.

## Suspect #2 - bottom row (`layout.py:182-204` scale bar, `248-262` labels/locator)

- scale bar 500 m = 33.3 mm -> **11.1 -> 44.4 mm**
- `scale` text "1:15 000" -> **51.4 -> 70.5 mm**
- `neighbor_bottom` (south ▼) -> **56.6 -> 91.4 mm**
- `coord` (UTM zone / MGRS) -> **74.3 -> 109.1 mm**
- `corner` (SW corner) -> **65.6 -> 109.1 mm**
- `source` (attribution) -> **11.1 -> 63.8 mm**
- `locator` -> **~129 -> 136.9 mm**

`neighbor_bottom` collides with the scale text and `coord`; `coord` also sits over
`corner`.

## Suspect #3 - side neighbour labels + km labels (`layout.py:236-240`, `480-520`)

- Rotated neighbour label length `u(_SIDE_LABEL_LEN)` = 26.1 mm, box x
  **6.3-9.8 mm (left) / 138.2-141.7 mm (right)**.
- The km-northing labels are right-aligned in the same 5.65 mm lane and are only
  kept clear of the neighbour label inside `|y - mid| <= u(_SIDE_LABEL_LEN/2 - 6)`
  = +/-7.8 mm (`layout.py:515`). The neighbour label actually spans +/-13 mm
  vertically, so km labels can reappear at its top/bottom ends.

## Already verified correct (do not chase these)

- Cut marks in the n-up print files sit at the paper edges, not on the maps.
- Dashed red overlap lines are ~3.1 mm (L/R) and ~2.1 mm (T/B) inside the frame -
  same relative position as other formats.
- No border item's `sceneBoundingRect()` intersects the map picture rect; the map
  frame itself is clean.

## Likely root cause

The border uses hardcoded mm sizes scaled only by `TEXT_SCALE` (0.87). 0.87
shrinks the text/lanes but does not compensate for the page being ~30 % narrower
than A5, so the horizontally arranged elements (title/neighbour/north;
scale/neighbour/zone/locator) no longer fit. A5 shows the same *box* overlaps, but
its actual glyphs are shorter than the boxes, so the problem only manifests on A6
(or with long sheet names/IDs).

## Suggested fix directions (A6-specific, no regression to A5+)

1. Tune `TEXT_SCALE["A6"]` (e.g. 0.75-0.8) and/or reduce the fixed widths for A6
   (title width `0.42`, neighbour box `u(40)`, north `u(62)`, `_LOCATOR_MAX_W`).
2. Or add format-aware clamping in `_add_side`/`_add_scale_bar` so the
   title/neighbour/north and scale/neighbour/zone segments get width = remaining
   space rather than fixed mm.
3. Widen the km-label exclusion around the rotated neighbour in `_add_km_labels`
   (`layout.py:515`) to the full label height.

Files: `plugin/kartenwerk/layout.py` (layout), `plugin/kartenwerk/config.py`
(sizes/scale). Verify by rendering an A6 sheet with a long sheet name/ID and
checking the top/bottom rows against the map frame.
