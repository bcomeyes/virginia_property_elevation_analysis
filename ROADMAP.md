
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
