# 1832 Munden Point Rd — upland screen

This is a map of where to look. It is not a determination.

## Bottom line

**Elevation can't tell Rick's upland from his wetland on this parcel.** His
upland points 1 and 2 are some of the lowest ground on the lot. His wetland
point 8 sits 1.6 ft higher than point 1. The lidar isn't the problem: two
flights ten years apart agree. The ground's shape simply doesn't follow his
line, so his line is being set by soil and vegetation, which we can't see from
a desk.

Five patches are worth asking about. Together they come to about 2.7 ac,
excluding one Rick has already sampled. None is strong. **Realistically this
turns 4.0 ac into perhaps 5 ac, not 8.** Most of that depends on one question
about the ditch.

## Data used

| | |
|---|---|
| Parcel | VGIN 23180066410000, **25.39 ac**, exactly Rick's study area. The traced KML is within 34 ft of it. |
| Lidar | USGS 1 m bare earth, **Norfolk 2013** (Rick's source) and **VA Hampton Roads 2023** (newer; published March 2026). Both are on the same pixel grid. The NC Florence 2020 tile has no data this far north. |
| Soils | SSURGO via Soil Data Access |

**The two flights agree.** After light smoothing (3 m), they differ by
0.17 ft (2 in) typical, plus a constant 2 in datum offset. So any difference of
half a foot or more that shows in both is real ground.

## Does elevation separate Rick's points? No.

Point elevations are ft NAVD88, averaged over a 5 m (16 ft) circle.

| Pt | Rick | 2013 | 2023 | vs. wetland median | Mapped soil |
|---|---|---|---|---|---|
| 1 | upland | 7.08 | 7.09 | −0.41 | 29 Portsmouth |
| 2 | upland | 7.26 | 7.30 | −0.22 | 24 Nimmo |
| 3 | upland | 8.19 | 8.47 | +0.85 | 24 Nimmo |
| 5 | upland | 8.48 | 8.64 | +1.12 | 19 Munden |
| 6 | upland | 10.86 | 11.03 | +3.45 | 7 Bojac |
| 4 | wetland | 8.20 | 8.37 | +0.75 | 24 Nimmo |
| 7 | wetland | 6.69 | 7.20 | −0.36 | 29 Portsmouth |
| 8 | wetland | 8.57 | 8.72 | +1.13 | 24 Nimmo |
| 9 | wetland | 7.52 | 7.52 | −0.08 | 29 Portsmouth |

Upland points span 7.1–11.0 ft and wetland points span 7.2–8.7 ft. The ranges
overlap completely.

The same test run over every pixel in Rick's polygons, rather than just his
points, agrees. The table uses AUC, where 0.5 means no separation and 1.0 means
perfect:

| Measure | AUC |
|---|---|
| Elevation 2013 / 2023 | 0.73 / 0.64 |
| Local relief (15 / 30 / 60 m) | 0.54 / 0.53 / 0.45 |
| Height above ditch bottom | 0.70 |
| Closeness to ditch | 0.76 |

The raw-elevation numbers are propped up by the high ground at the road (point
6). Local relief, meaning a spot's height compared with its surroundings,
carries no information. I skipped the wetness index. It is built from the same
surface that shows no local-relief signal, and on a lot this flat it would
mostly just trace the ditch.

**SSURGO doesn't follow Rick's line either.** It maps 2.5 of his 3.9 upland
acres as Nimmo, a hydric soil, and puts point 1 on Portsmouth. Munden and Bojac
appear only along the south edge.

## The ditch

- **It's shallow in the lidar:** 0.8 ft below the banks in 2023, 0.4 ft in
  2013. It was cleaned out or deepened between the two flights. Lidar doesn't
  see through water, so the true bottom is deeper.
- **There's no spoil berm along the east strip.** The only exception is one
  short ridge of about 1.5 ft right on the east bank near the south bend. It
  shows only in 2023, so it's spoil from the recent cleaning or a brush pile.
- **The banks are the same height.** At equal distances from the ditch, the
  west bank is within 0.2 ft of the east bank (medians over 21 cross-sections,
  both flights). North and south reaches are sometimes higher on the west;
  the middle reach runs higher on the east.

So the east strip isn't higher ground and isn't a spoil bank. Whatever makes it
upland isn't visible in elevation. If the reason is the ditch drying it out,
the same should apply to the west bank.

## Candidates

The screen flags three kinds of evidence, and each builds its own patches:

- **high:** at least 0.5 ft above the wetland median in *both* flights
- **soil:** a non-hydric SSURGO map unit
- **bank:** west of the ditch, within 67 ft. That's the distance of Rick's
  farthest upland point on the east strip (point 2).

Minimum size is 0.25 ac, or 0.10 ac when a patch touches Rick's upland. The
table is ordered by how many kinds of evidence agree, then by size. There is no
score. Full columns are in `candidates.csv`.

| ID | Acres | Evidence | Against | Take |
|---|---|---|---|---|
| **H3** | 0.31 | 0.7 ft above wetland median; within 0.2 ft of upland pt 3, 42 ft away; adjoins his upland | Mapped Nimmo | **Best simple ask.** No data point inside it. |
| **S1** | 0.68 | Mapped Munden 74% / Bojac 25%; adjoins his south upland pocket | Wetland pt 4 is 17 ft away; ground 0.4 ft below pt 4 | Worth a pit on the west end, away from pt 4. |
| **H1** | 0.20 | High (0.9 ft) plus Munden/Bojac; adjoins upland | **Contains wetland pt 4** | Mostly already answered. Overlaps S1 by 0.15 ac. |
| **B1** | 1.68 | West bank, same height as the east strip | Nimmo/Portsmouth; no elevation edge | **A question, not data.** The only one big enough to matter. It's across the ditch from the existing upland. |
| **H2** | 0.67 | Highest ground in the wetland: 1.6 ft above upland pt 1 | **Contains wetland pt 8** | Rick already put Wetland #2 (the driest class) right on this rise and sampled it. |

- H1 + S1 + H3 together: 1.05 ac, all touching his existing upland.
- Adding B1: 2.7 ac.
- H2: the lidar actually agrees with Rick here. His Wetland #2 outline hugs
  this rise closely.

## Caveats

- Rick's lines and points are traced from his PDF and good to about 20 ft.
  The 2023 lidar puts the ditch channel about 15–20 ft east of the traced line
  in the north half.
- Smoothing (3 m) hides features smaller than about 30 ft across, such as
  individual tip-up mounds. That's deliberate.
- SSURGO is a 1:24k survey. The Munden/Bojac line along the south edge is a
  surveyor's generalisation. It is not a boundary.

## Files

Everything below is in `munden/out/`.

- `map_elevation.png`: elevation, both flights, stretched to the parcel
- `map_relative.png`: height above/below the wetland median, both flights
- `map_soils.png`: SSURGO units
- `map_candidates.png`: the patches
- `transects.png` / `.csv`: 21 ditch cross-sections
- `candidates.kml` / `.geojson` / `.csv`
- `calibration.csv`: Rick's points
- `screen_numbers.csv`

To regenerate, from `munden/` with the venv active:
`python fetch_dem.py && python soils.py && python transects.py && python screen.py`
