#!/usr/bin/env python3
"""
search_config.py - the parameter page. Everything that decides what we look at
and how it is ordered lives here, in the open, with the reasoning next to it.

Why: an 8-acre minimum once sat buried in a constant for months, quietly
contradicting what we actually wanted. Nothing that shapes the search hides
in code any more.

Nothing here is a score. Every measurement stays its own column and Matt and
Larissa do the weighing. Filters bound the work; the sort orders it.
"""

# --------------------------------------------------------------------------- #
# WHERE
# --------------------------------------------------------------------------- #
VA_ALL = ["Virginia Beach, VA", "Chesapeake, VA", "Suffolk, VA"]
NC_ALL = ["Currituck County, NC", "Camden County, NC", "Gates County, NC"]

JURISDICTIONS = ["Virginia Beach, VA", "Chesapeake, VA"]
# JURISDICTIONS = ["Virginia Beach, VA"]
# JURISDICTIONS = VA_ALL                    # adds Suffolk back
# JURISDICTIONS = VA_ALL + NC_ALL
# JURISDICTIONS = NC_ALL

# Suffolk removed 2026-09-20. The Crittenden Rd parcels were the best land we
# had measured on nearly every axis - acreage, canopy, price, a real
# freshwater pond - and we still cut them. Suffolk is 1.25+ hours to
# Sandbridge and further to the Outer Banks. Suffolk has plenty of water.
# It is not the water we are moving for.

# --------------------------------------------------------------------------- #
# WHAT
# --------------------------------------------------------------------------- #
MAX_PRICE = 1_000_000     # was 400k, which was hiding acreage we can afford.
MIN_ACRES = 2.0           # was 1.0. Everything under 2 in the sweep was
                          # subdivision infill.

# Deliberately no MIN_PRICE (would filter out cheap land, the thing we hunt)
# and no MAX_ACRES (only ever a proxy for affordability; MAX_PRICE does that
# job honestly).

MAX_FINE_PULLS = 60       # ceiling on parcels measured per sweep so a bad
                          # query cannot run away. Raised with the price cap.

# Part of the "is it land" gate, not a new one. A parcel of rural land does
# not have a unit number. An address like "3665 Sandpiper Rd Unit 235" is a
# deeded site inside a development - an RV resort, a condo, a marina - and it
# cannot be measured, because the county parcel under it is the development's
# COMMON tract. Sandpiper is 11 listings on one 61.46 ac parcel: canopy 1.3%,
# 100% SFHA, water table 0 in, soils "Water / Udorthents / Backbay". Those
# numbers describe the campground, not any lot for sale.
#
# It reached the TOP of the sheet for weeks because min_sandbridge = 9 is the
# best drive time in the whole set and that is the primary sort key. Nothing
# caught it: the feed publishes no lot_sqft, so MIN_ACRES has nothing to
# compare, and VGIN carries no acreage field, so the measured-vs-reported
# check has no reference either.
#
# This matches ONLY unit/apt/ste/suite/trlr and '#'. It deliberately does NOT
# match "Lot 12", which is ordinary for raw land.
EXCLUDE_UNIT_ADDRESSES = True

# --------------------------------------------------------------------------- #
# ORDER
# --------------------------------------------------------------------------- #
# Sort keys applied in order: (column, descending).
#   1. min_sandbridge ascending  -> shortest drive to Sandbridge first
#   2. canopy_pct     descending -> greatest canopy first
#
# Drive time to the RIGHT water is the criterion that killed Suffolk, so it
# leads. Canopy breaks ties: on ground this flat, trees are the only privacy
# there is. Blanks always sort to the bottom, never the top.
SORT = [
    ("min_sandbridge", False),
    ("canopy_pct",     True),
]

# Single-key fallback, for parcel_elevation.sort_results() and --test.
SORT_BY   = "min_sandbridge"
SORT_DESC = False

# Column order as Matt and Larissa arranged it by hand. Triage fields first,
# the human columns early enough to reach without scrolling, deep
# measurements trailing behind.
COLUMN_ORDER = [
    "address", "listed", "price", "url",
    "status", "notes",                      # yours - script never writes these
    "acres", "min_sandbridge", "soils", "flood", "canopy_pct", "parcel_acres",
    "price_verified",                       # yours
    "drained_ac", "hydric_pct", "wt_depth_in",
    "flood_open", "flood_sfha",
    "relief_ft", "tri_ft", "std_ft", "max_ft",
    "min_coinjock", "source", "n_listings", "geocoded", "warn",
    "parcel_key", "rejected_at_price",
]

HUMAN_COLS = ["status", "notes", "price_verified"]

# Filtered-out rows are not carried into the output...
KEEP_FILTERED_ROWS = False

# ...with one exception, and it is narrower than it first looks.
#
# A row with anything typed into status, notes or price_verified survives a
# change to MAX_PRICE or MIN_ACRES, with its last measurements frozen. Those
# are TWEAKS - a number nudged to see what shakes loose - and a tweak must
# never be able to delete work done by hand.
#
# Removing a jurisdiction is not a tweak. It is a DECISION, made once, for a
# reason. Suffolk came out because it is 1.25+ hours to Sandbridge, and 7399
# Crittenden being marked 'candidate' does not make that untrue. An annotated
# row in a jurisdiction we have dropped goes with it - and gets named on the
# way out, so it is never a silent deletion.
ANNOTATED_ROWS_BYPASS_FILTERS = True

# The gate filters on the acreage the LISTING claims. When the feed publishes
# none - common for plain land - nothing is compared and the row passes. We
# then measure the county parcel and sometimes find 0.05 acres.
#
# This is the double check: after measuring, drop anything whose MEASURED
# parcel_acres is under MIN_ACRES. Caught five sub-acre lots on 2026-09-20
# (Hawk Ave 0.05, Gale 0.10, Clifton 0.15, Girard 0.47, Hermitage 0.67).
# Annotated rows are exempt, same as above - acreage is a tweak.
ENFORCE_MEASURED_ACRES = True

# --------------------------------------------------------------------------- #
# GOOGLE SHEET - the one live sheet Matt and Larissa both edit
# --------------------------------------------------------------------------- #
SHEET_ID  = "1xq2IHY2oZsluCq5lkWiUY2GcyfteuoBAOmczBj8feJI"
SHEET_TAB = "candidates"
# Blank SHEET_ID = skip the sheet entirely and write csv + xlsx only.

# Service-account key path comes from the environment, never from this file:
#   export GOOGLE_SHEETS_KEY=/home/matt/.config/land-sheets-key.json
GOOGLE_KEY_ENV = "GOOGLE_SHEETS_KEY"

# --------------------------------------------------------------------------- #
# LIDAR
# --------------------------------------------------------------------------- #
FINE_RES_M       = 1.0
FETCH_FINE_LIDAR = False  # 10 m ranks fine; 1 m is deep_dive.py's job


# --------------------------------------------------------------------------- #
def show():
    """Print the parameters in force. Called at the top of every sweep so a
    surprising result can always be traced back to a setting."""
    order = " then ".join(
        f"{c} {'desc' if d else 'asc'}" for c, d in SORT)
    print(f"""
  GATE (bounds the 1 m lidar pulls)
    jurisdictions : {', '.join(JURISDICTIONS)}
    price         : <= ${MAX_PRICE:,}
    acres         : >= {MIN_ACRES:g}
    max pulls     : {MAX_FINE_PULLS}  (stops runaway sweeps)
    unit addresses: {'excluded (not land)' if EXCLUDE_UNIT_ADDRESSES else 'ALLOWED'}

  OUTPUT
    sort by       : {order}
    no composite score - every measurement stays its own column
    annotated rows bypass every filter : {ANNOTATED_ROWS_BYPASS_FILTERS}
    google sheet  : {SHEET_ID[:12] + '...' if SHEET_ID else 'off (csv + xlsx only)'}
    fine lidar    : {'on' if FETCH_FINE_LIDAR else 'off'} ({FINE_RES_M:g} m)
""")


if __name__ == "__main__":
    show()
