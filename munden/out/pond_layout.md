# 1832 Munden Point Rd: house, septic, well, and how much pond

## The layout we settled on

**Map:** `final_layout.png`. **Why the pond is this size:** `explain_layout.png`.

| | Acres |
|---|---|
| Rick's upland, all of it | 3.88 |
| **Pond** | **0.86** |
| Pond, if the city also makes it stay 50 ft from the wetland | 0.53 |

What's where:

- **Septic:** Matt's oval (~3,500 sq ft, an engineered system) at the north
  tip of the arm. It is 20 ft from the ditch and 5 ft from the property line.
  The arm has about 41,000 sq ft of legal septic ground, so the reserve field
  the city requires fits there too without costing any pond.
- **Sewer line:** about 470 ft from the house to the septic tank.
- **House:** Matt's drawn box, which includes the yard (~8,300 sq ft). It's
  moved as far south-west as it can go while staying 50 ft from the wetland.
- **Pond:**
  - East of the house, wrapping around its south end.
  - Nothing up the arm.
  - The biggest single pond that fits.
  - A 15 ft excavator lane of dry land all the way around it.
- **Well:** at the house's south end, 18 ft from the house.
- **Driveway:** from the road, north along the east property line, then west
  into the house's south end.

## What each rule costs

| | Pond |
|---|---|
| No lane, pond may go up the arm | 2.13 ac |
| No lane, nothing up the arm | 1.55 ac |
| 15 ft lane, pond may go up the arm | 1.31 ac |
| **15 ft lane, nothing up the arm (chosen)** | **0.86 ac** |

- **The lane costs the most**, about 0.7 ac. The pond is long and narrow, so
  it has a lot of edge, and 15 ft around all of it adds up.
- **Keeping the pond out of the arm** costs about 0.45 ac.

## The driveway can't stay 50 ft from the wetland

The ground that is at least 50 ft from the wetland stops about 220 ft short of
the road, because the upland strip that reaches the road is too narrow. The
driveway runs as far from the foot of the L as the property allows, along the
east line. About **275 ft of it is still within 50 ft** of the wetland, and at
one point it touches the wetland edge. No route avoids this. Ask the city
whether a driveway is allowed across its 50 ft buffer.

## Questions to settle

1. **City:** does its 50 ft wetland rule (Southern Rivers Watershed ordinance
   §7(c)) apply to Rick's wetland at all? If it does, does it cover digging
   the pond and building the driveway? This question moves the pond between
   0.86 and 0.53 ac and decides whether the driveway is allowed.
2. **Health department:** is an engineered septic at the tip of the arm
   approved, with a 470 ft sewer line?
3. **Well driller:** which well class? That decides whether the well needs 50
   or 100 ft from the septic. The well is far from it either way.

## Scripts

From `munden/` with the venv active:

- `python final_layout.py` draws this layout.
- `python explain_layout.py` draws the comparison.

Everything below is the first pass, which used only the circled block. Its
pond numbers are superseded. Its **minimum distance table**, assumptions, and
pond-and-well notes still apply. The optimiser runs (`optimize_layout*.png`,
up to 2.7 ac) let the pond run up the arm with no lane, so they are
superseded too.

---

This covers the green block circled in `site_docs/munden/munden_rectangle.png`,
which is Rick's upland inside the purple loop. Map: `pond_budget.png`.

## Bottom line

| | Acres |
|---|---|
| Green block you circled | **1.9** |
| No-go strip: 50 ft from the wetland, an L down the west side plus the south tip (stays trees) | 0.7 |
| Usable | 1.2 |
| **Most pond the rules allow, with a house, septic and well also fitted in** | **0.64** |
| The same, if the city decides its 50 ft wetland rule doesn't apply here | 1.34 |

**The biggest unknown is one question for the city.** The pond comes out at
0.64 ac or 1.34 ac, depending on whether the city counts Rick's wetland as
"wetland" under its own Southern Watersheds ordinance. That ordinance's
definition is narrower than the Army Corps one. See the last section.

0.64 ac is the most the rules allow, with water right up to every legal line.
A real pond with a mowable edge and a gentle bank will be somewhat smaller.

## The layout

- **Trees:** the 50 ft strip along the ditch stays wooded. It's the privacy
  screen on the west.
- **House:** backs onto that strip, at the south end.
- **Septic field** (with the reserve field the city requires): right beside
  the house, on the east.
- **Pond:** fills the north two-thirds of the block.
- **Well:** on the pond's edge. It has to be 100 ft from the septic field, so
  it ends up on the far side of the pond, about 200 ft from the house.
- **Lawn:** 20 ft around the house, plus whatever you don't dig.

The layout comes from a search: it tried every house and septic position on a
10 ft grid and kept the one leaving the most pond. What mainly shrinks the
pond is the septic. The field and the tank must each be 50 ft from the water.

## Minimum distances

| Thing | Must be at least this far from… | Source |
|---|---|---|
| **House** | 50 ft from wetland (the L strip) | VA Beach Southern Rivers Watershed ordinance §7(c) |
| | 20 ft from property line | VA Beach zoning, AG side yard (CZO §402) |
| **Septic tank** | 10 ft from house | 12VAC5-610-592 |
| | 50 ft from pond | 12VAC5-610-592 |
| | 50 ft from well | 12VAC5-630-380 |
| **Septic field + reserve** | 10 ft from house | 12VAC5-610-592 |
| | 50 ft from pond | 12VAC5-610-592 |
| | 100 ft from well (50 for some well types) | 12VAC5-630-380 |
| | 20 ft from ditch | 12VAC5-613-200 |
| | 5 ft from property line | 12VAC5-610-592 |
| **Well** | 15 ft from house | 12VAC5-630-380 |
| | 5 ft from property line (50 if the neighbor farms 3+ ac; they can waive it in writing) | 12VAC5-630-380 |
| | No required distance to the pond, but not in a low, swampy spot | 12VAC5-630-380 |
| **Pond** | 25 ft from property line | VA Beach §30-1 (farm ponds), used as a guide |
| | Nothing required from the house or well | |

## Assumptions

- **House:** 3,000 sq ft on one story (50 × 60 ft).
- **Septic:** 4 bedrooms is 600 gallons a day. The county soil survey maps
  this whole block as Nimmo, a poorly drained soil with the wet-season water
  table about 6 in down. So this assumes an engineered system rather than a
  standard drainfield.
- **Septic footprint:** 5,000 sq ft for the field plus the reserve. The city
  requires a reserve field as big as the main one. Depending on the system,
  the total could run from 2,000 to 10,000 sq ft.
- **Well:** assumed to be the type that needs 100 ft from the septic. A 50 ft
  type would let the well sit much closer to the house.

## The pond and the well

- **The pond will mostly fill itself.** Ground here is 7–8 ft above sea level
  and the water table is shallow, so digging hits water within a foot or two.
  The well just tops it up in a dry summer.
- **The well sets the flow, not the pump.** One inch over one acre is about
  27,000 gallons. At a typical 15 gallons a minute, that's about 30 hours of
  pumping.
- **Ask the driller for a deeper, cased and grouted well.** A shallow well
  beside the pond would partly pull the pond's own water back out through the
  ground.

## Questions to settle before digging

1. **City:** does its 50 ft wetland rule (§7(c)) apply to Rick's wetland? Its
   definition (§4(v)) covers peat and muck soils, which Nimmo and Portsmouth
   are not. It also covers saturated land connected by surface flow to tidal
   wetlands or streams. This one question moves the pond between 0.64 and
   1.34 ac.
2. **City:** the driveway has to reach the road across the south end of that
   strip. Is a driveway allowed there?
3. **Health department:** which septic system does the soil support, and how
   big is the field plus reserve?
4. **Well driller:** which well class? That decides whether the well needs
   50 ft or 100 ft from the septic.

## Caveats

- Rick's lines are traced from his PDF and good to about 20 ft.
- Your circle is traced from the screenshot and good to about 1 m.
- There was a thin 6 ft gap between Rick's traced upland and the east
  property line. It's a tracing artifact, not wetland, and it is ignored.
- This is what the rules allow. It is not a site plan.

To regenerate, from `munden/` with the venv active: `python pond_budget.py`
