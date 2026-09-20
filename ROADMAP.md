# ROADMAP

## Where we are — 2026-09-20

The pipeline runs end to end and writes the shared Google Sheet. Today's sweep
put **37 parcels** in front of Matt and Larissa across Virginia Beach and
Chesapeake.

**Working:**

* `find_land.py --sweep` → Google Sheet + csv + xlsx in one run, ~15 min.
* The sheet is the source of truth for `status`, `notes`, `price_verified`.
  `read_existing()` reads it *before* measuring, so a sweep that dies halfway
  loses nothing. It falls back to xlsx, then csv, then blank — never fatal.
* Auth is a Google Cloud service account, `land-sheets-writer@…`, whose JSON
  key lives at `~/.config/land-sheets-key.json` (mode 600, deliberately
  outside the repo) with its *path* in the env file. The Sheets API is enabled
  on project `land-sheets-509221` and the sheet is shared with that account as
  Editor. Rotating the key means replacing one file; no code changes.
* Annotations carry across filter tweaks with `!carried`, and release — named
  out loud, never silently — when a jurisdiction is dropped.

**Open:**

* **The sheet's basic filter hides rows.** It filters by exact *value*, and
  the script rewrites the grid every run, so its `hiddenValues` lists go stale
  immediately. Today 37 rows were written and 9 were visible. Blank `acres` is
  among the excluded values, which kills every new-construction row. Fix is
  Data → Remove filter; filter on `parcel_acres` instead, which is the number
  we measured rather than the number the listing claimed.
* **The drive-time cliff is still not pinned down.** Kept rows sit at 34–35
  min to Sandbridge, deleted rows at 47–48. Nothing has been measured in
  between, so the boundary remains an assumption.
* **`above_bfe.py` is written and has never been run.**
* **The sheet backup is local only.** `output/` is gitignored, so
  `output/sheet_backup_*.csv` — the only copy of the released Suffolk notes —
  exists on exactly one disk.

**Next:** walk the short list in December. Before that, decide whether the
Blackwater Rd cluster survives its soil numbers — 6664 has zero drained acres,
67% hydric, a 15-inch water table — despite reading 85–92% canopy.

## Suffolk: opened, measured, cut — 2026-09-20

Suffolk was added because Virginia Beach alone was 14 parcels, four of them
one Blackwater cluster. It worked: it produced the best-measuring land in the
whole set, including the Crittenden Rd parcels — real acreage, heavy canopy, a
freshwater pond, inside the price ceiling.

It was cut anyway. Suffolk is 1.25+ hours to Sandbridge and further to the
Outer Banks. Suffolk has plenty of water; it is not the water they are moving
for. That is the clearest statement so far of what this search is actually
for, and it is why `min_sandbridge` is the primary sort key.

Seven annotated Suffolk rows released on the way out, preserved in
`output/sheet_backup_20260920_1608.csv`:

```
7399 Crittenden Rd      [candidate]      "Peninsula on lake; super cool"
6701 Crittenden Rd      [watching]       "close to Suffolk and Chesapeake proper; heavily wood"
1101 Cypress Chapel Rd  [reject-other]   "too denuded"
949 Cherry Grove Rd N   [reject-other]   "only wooded lot in a sea of clear cut"
2508 Pittmantown Rd     [reject-other]   "house on property"
2105 Holland Corner Rd  [reject-terrain]
145 Dutch Rd            [reject-other]
```

Re-adding Suffolk is one line in `search_config.py`. The rejects would come
back unmarked, so that backup is the only record of work already done on them.

## Parameters as of today

`MAX_PRICE` 400k → **$1M**, because the old ceiling was hiding acreage they
can afford. `MIN_ACRES` 1.0 → **2.0**, because everything under 2 in the sweep
was subdivision infill. `MAX_FINE_PULLS` 60, raised alongside the price cap.
`EXCLUDE_UNIT_ADDRESSES` closes the Sandpiper hole: eleven listings on one
61.46 ac common tract held the top of the sheet for weeks on the strength of a
9-minute drive time. `ENFORCE_MEASURED_ACRES` drops rows whose *measured*
parcel is under the floor despite passing the listing gate; annotated rows are
exempt.

## Measurement (solved)

**1 m bare earth is REAL GROUND, not interpolation.** Verified against a
transect across the Knotts Island lot, which Matt has walked: 6.4 ft at Marsh
Causeway, stepping down to ~4 ft across the dry ground, a genuine break at
300 m dropping to 1.2 ft, flat swamp for 250 m, then a cliff to the water at
740 m. Right shape, right heights, right order.

**But 1 m resolution is not 1 m of information.** The profile descends in flat
steps 10-20 m long, so the grid holds values constant between real
measurements. 786 samples along that line carry maybe 50 samples of content.
Fine for pad-finding and sightlines; do not expect sub-10 m features.

**Water detection works.** Flat AND low relative to the parcel's high ground.
Knotts Island reads 33.9% wet / 3.43 ac; the dry Blackwater lots read 0-3.5%.
An earlier percentile-based version failed on this exact parcel -- when 74% of
a lot is wet, the 20th percentile is also wet.

**Slope must mask the parcel edge.** Filling NaN cells outside the boundary
before taking the gradient invents a cliff at every edge, and reported 12-15
degrees on coastal flats 8 ft above sea level. Erode the valid mask by one
cell and discard the rest; small parcels correctly lose their outer ring.

## Canopy and soil (solved)

**Canopy: NLCD Tree Canopy Cover via MRLC GeoServer WMS**, 30 m, percent per
pixel. Validated against parcels Matt labelled from imagery: 6584 Blackwater
(he says completely wooded) 91.5%, Sandpiper campground 6.4%, cousin's mixed
lot 58.1%. Layer name changes each release, so it is discovered from
GetCapabilities rather than hardcoded. 2021 vintage, so recent clearing will
not show.

**NAIP NDVI at 0.3 m was tested and REJECTED.** It measures greenness, not
trees: scored the cousin's marsh-heavy lot at 93% against canopy's 58%, because
marsh grass is green. Do not re-add it. Note the USGS service publishes only
'NDVI_Color', a colourised 3-band picture, not NDVI values -- request the
'None' rule and compute from raw bands if it is ever needed.

**Soil: USDA SSURGO via Soil Data Access.** Four columns -- drained_ac (acres
moderately-well-drained or better, the septic number), hydric_pct, wt_depth_in,
soils. Validated against the cousin's lot: Altavista (moderately well drained,
not hydric) on the dry corner where the house sits, Currituck mucky peat on the
marsh, Roanoke between. 70% hydric against FEMA's 74% Zone AE -- two
independent datasets within four points.

Traps, both cost real time:
  * The SDA spatial query returns HTTP 400. The WFS works, but WFS 1.1.0
    returns EPSG:4326 as LAT,LON -- axis order changed from 1.0.0 -- so
    coordinates arrive swapped. Detect by sign: in the continental US
    longitude is negative and latitude positive, so x>0 with y<0 means
    swapped. A magnitude test fails (y is -75.996 and 75.996 < 90).
  * wtdepannmin lives on **muaggatt**, not component. Querying it on component
    returns HTTP 400 with no useful message.
  * SDA returns every value as a STRING, including numerics.

## The finding that opened the search (historical)

Virginia Beach under $400k is 14 parcels, four of them one Blackwater cluster,
all under 3 ft of relief with 0.00-3.62 drained acres and a 15-inch water
table. Adding Chesapeake and Suffolk took it to 64 parcels and produced what
the coast structurally cannot:

  1624 W Adams Dr, Suffolk     TRI 2.27 -- double anything coastal, $265k
  7362 Quaker Dr, Suffolk      74.45 ac, $399,999, 60 open ac, 37 drained ac
  1421 Saint Brides Rd W, Ches 20.44 ac, X 100, 79% well-drained soil

Suffolk trade-off: inland, 40-90 min to Sandbridge, no North Landing kayak
access, and the western side borders the Great Dismal Swamp (heavy federal
wetland). But no 50 ft Southern Watershed buffer -- that is a Virginia Beach
ordinance -- and possibly outside a Bay Preservation Area, so the land
disturbance trigger may be 10,000 sq ft rather than 2,500.

**Superseded 2026-09-20.** The drive time settled it; see "Suffolk: opened,
measured, cut" above. Chesapeake stayed.
