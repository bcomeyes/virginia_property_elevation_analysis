# CLAUDE.md — Virginia land search

## What this is

Matt and Larissa are moving from New Mexico to coastal Virginia / northeastern
North Carolina to buy rural land and build on it. This repo measures every
land listing in the search area against the things they actually care about,
and puts the numbers in a spreadsheet they read by eye. A trip to walk the
short list is planned for December.

**It ranks nothing.** There is no score, no weighting, no composite. Every
measurement stays its own column and the humans do the weighing. This was a
deliberate decision after a scoring scheme with arbitrary weights
(`w_texture=3`, `w_canopy=2`) was thrown out for being unjustifiable.

## What they're optimizing for

Learned from a hand-curated keep list, not stated up front:

1. **Drive time to the right water.** Sandbridge and the Outer Banks,
   specifically. Suffolk was cut despite having the best-measuring land in the
   set because it is 1.25+ hours to Sandbridge. Suffolk has plenty of water.
   It is not the water they are moving for. Kept rows sit at 34–35 minutes;
   rows deleted despite good acreage and canopy sit at 47–48. The cliff is
   somewhere in between and has not been pinned down.
2. **Canopy.** On ground this flat, trees are the only privacy that exists —
   landform screens nothing under roughly 8 ft of relief. Canopy also
   clusters: whole subdivisions read 0% (Cedarville Ct, Beaver Dam Rd, Carley
   Ct, Camden Ct) while Blackwater Rd and Seven Eleven Rd read 85–92%. It
   measures the neighborhood as much as the lot.
3. **Soil**, for septic viability and whether the driveway needs geotextile
   fabric under the road base. This is the measurement that has changed minds:
   6664 Blackwater looked best on every other axis and has **zero** acres of
   moderately-well-drained soil, 67% hydric, 15-inch seasonal water table.
4. **Flood**, reported as the FEMA breakdown in one column plus the largest
   *contiguous* non-SFHA acreage. Score on the best available ground, not the
   average. Flood zone alone has not been a dealbreaker for them.
5. **Acreage**, floor of 2.

Terrain ruggedness (`tri`) is measured and shown but has never driven a
decision. Do not promote it.

## Architecture

```
search_config.py      THE PARAMETER PAGE. Everything that decides what we look
                      at or how it is ordered lives here, with reasoning.
land_watch.py         realtor.com feed (RapidAPI) + qualify() — the only gate
parcel_elevation.py   parcels, DEM clip, terrain, FEMA, SSURGO, NLCD canopy
drive_time.py         one OpenRouteService Matrix call for the whole set
find_land.py          ties it together -> Google Sheet + csv + xlsx
site_model.py         single-parcel: pads, sightlines, screening
water_detect.py       water by flatness, not elevation (see traps)
deep_dive.py          single-parcel land budget
sketch.py             four-panel render of one parcel
above_bfe.py          patches above base flood elevation — WRITTEN, NEVER RUN
setup/                one-time data prep (DEM fetch, resolution standardize)
probes/               one file per service, recording how it actually behaved
ROADMAP.md            state, decisions, dead ends, open problems
```

Run with `--sweep` (full inventory) or `--check` (new only, for weekly cron).
Always inside `.venv` — `source .venv/bin/activate` first, every time.

## Rules that are not negotiable

**Never write `status`, `notes`, or `price_verified`.** Those are Matt's and
Larissa's columns. The script reads them before it writes anything and puts
them back untouched. They are keyed on the **county parcel ID**, not the MLS
number, so a verdict survives a relisting.

**Annotations survive tweaks, not decisions.** Raising a price ceiling or an
acreage floor is a tweak — annotated rows carry forward with their
measurements frozen and a `!carried` flag. Removing a jurisdiction is a
decision made for a reason — those rows release, and get printed by address
and status on the way out. Never a silent deletion either way.

**No new hard gates.** The gate is jurisdiction, price ceiling, acreage floor,
and "is it land." That is all. An 8-acre minimum once sat buried in a constant
for months quietly contradicting what they wanted; an elevation threshold
excluded good lots for years on a theory that turned out to be wrong. When in
doubt, measure it, show it as a column, and let them filter by eye.

**Parameters live in `search_config.py`, never in code.** If a number decides
what gets looked at, it belongs on the parameter page with a comment saying
why.

## Traps, all paid for in real debugging time

- **DEM resolution drift.** `rio.reproject()` without `resolution=` lets GDAL
  pick, producing 7.79/7.98/9.58 m tiles that silently break TRI comparison.
  TRI is scale-dependent — the same parcel reads 0.309 at 10 m and 0.045 at
  1 m. All tiles are now pinned to exactly 10.0 m. Never compare TRI across
  resolutions.
- **VGIN has no address and no acreage field.** Only geometry and IDs. Address
  lookup is impossible; geocoders are the only route. NC OneMap *does* carry
  `gisacres`, `ownname`, `siteadd` — layer 1 is polygons, layer 0 is centroids.
- **FEMA NFHL must be queried with the parcel polygon, not the pin.** Large
  surveyed boundaries blow the URL length; `_esri_polygon()` simplifies to
  250 vertices, preserving area to ~0.1%.
- **SSURGO WFS returns EPSG:4326 as LAT,LON** in WFS 1.1.0. Detect by **sign**,
  not magnitude: in the continental US longitude is negative and latitude
  positive, so `minx > 0 and miny < 0` means swapped. A magnitude test fails
  because 75.996 < 90.
- **`wtdepannmin` lives on `muaggatt`, not `component`.** Querying it on
  `component` returns HTTP 400 with no useful message. SDA returns every value
  as a string.
- **Water is flat, at any elevation.** A lidar water surface has local standard
  deviation under 0.1 ft; real ground never does. An elevation-anchored test
  once reported an 18-acre pond as a 20-acre building pad.
- **Fill NaN with `binary_erosion` of the valid mask**, never with the parcel
  mean — filling with the mean creates an artificial cliff at every parcel
  edge and produces 12–15° slopes on coastal flats.
- **Test services from Matt's machine before concluding they're down.** NC
  OneMap appeared completely unreachable from a cloud sandbox and was fine
  from his box. Give him a small ping script rather than guessing.
- **Close LibreOffice before running.** An open xlsx gets clobbered.
- **`.env` holds `RAPIDAPI_KEY`.** Check `git status` before every commit. The
  Google service-account key lives at `~/.config/land-sheets-key.json`,
  deliberately outside the repo.

## Ground truth

Everything is validated against Matt's cousin's lot at **302 Marsh Causeway,
Knotts Island, NC (36.53916, −75.99679)** — land he has physically walked.
Four independent datasets agree there: FEMA 74% Zone AE, SSURGO 70% hydric,
water detection 33%, and an elevation transect matching his memory of the walk
(6.4 ft at the road, break at 300 m, flat swamp, drop to water at 740 m). Any
new measurement gets checked against that lot first.

## Working with Matt

- **Explanation first, then the file, then the command immediately after it.**
  Never put text between a download and its CLI command. Ever.
- One combined command, not a sequence:
  `rm -f X.py && cp "$(ls -t ~/Downloads/X*.py | head -1)" ./X.py && chmod +x X.py && ./X.py`
- No separate `.sh` files, no helper shell functions.
- When he says "slow down" or "start over," give one step and wait.
- He runs everything himself. Give him the command; don't narrate the steps.
- He is new to lidar and GIS and says so. Explain the concept, not the API.
- If he pushes back on a conclusion, he is usually right that it was
  under-tested. Give him a way to check it rather than re-arguing.
