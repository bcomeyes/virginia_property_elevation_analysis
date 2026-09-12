
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

## The finding that changes the search

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
