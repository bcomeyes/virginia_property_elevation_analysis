#!/usr/bin/env python3
"""
=============================================================================
SEARCH CONFIG - the only file you edit to change what gets searched
=============================================================================

Everything here was once a constant buried in land_watch.py or a flag you had
to remember to type. Both hid decisions. An 8-acre minimum sat in land_watch.py
for months contradicting what Matt actually wanted, and nothing ever forced it
to surface.

So: one block, every parameter visible, reasoning next to it. If a decision is
not written here, it is not in effect.

Uncomment the line you want. Comment out the rest.

    python3 search_config.py          # print current settings
=============================================================================
"""

# --------------------------------------------------------------------------
# JURISDICTIONS
# --------------------------------------------------------------------------
# VA entries are independent cities (Virginia Beach and Chesapeake are former
# counties absorbed by cities, so there is no "Virginia Beach County"). NC
# entries are true counties, and the word "County" is load-bearing --
# "Currituck, NC" silently means the TOWN of Currituck, 25 listings instead
# of 675.
#
# NC is real: 54 ac in Barco at $675k against 45.8 ac on Holland Rd at $3.1M,
# same commute. It is also a different state's laws, which is a live
# disagreement in the house. Kept switchable rather than deleted so the
# comparison stays available instead of being decided by omission.

VA_ALL   = ["Virginia Beach, VA", "Chesapeake, VA", "Suffolk, VA"]
NC_ALL   = ["Currituck County, NC", "Camden County, NC", "Gates County, NC"]

# ---- pick one ----
# Virginia Beach alone returned 14 parcels, four of them the same Blackwater
# cluster. That is a neighbourhood, not a search. Suffolk is where the actual
# terrain is: Kings Fork TRI 1.12, Murphys Mill 1.16, Cypress Chapel 18.7 ac
# at $250k -- all of them under the price ceiling and none of them visible
# from Virginia Beach.
JURISDICTIONS = VA_ALL                                      # all three VA cities
# JURISDICTIONS = ["Virginia Beach, VA"]                      # VB only
# JURISDICTIONS = ["Virginia Beach, VA", "Chesapeake, VA"]    # VB + Chesapeake
# JURISDICTIONS = VA_ALL + NC_ALL                             # everything
# JURISDICTIONS = NC_ALL                                      # NC only


# ==========================================================================
# STAGE 1 - THE GATE
# ==========================================================================
# The gate exists to BOUND THE 1 m LIDAR PULLS. Its output count is exactly how
# many parcels get fine-resolution analysis. Price and jurisdiction do that
# work: land in Virginia Beach under $400k is ~14 parcels.
#
# Only FACTS may gate. Never a judgement about quality -- terrain, flood,
# canopy, soil and drive time are measured and shown as columns, never used to
# remove a listing before Matt has seen it. Inventory is thin and one hidden lot
# matters.

MAX_PRICE = 400_000    # from "we cannot afford really anything over $400K".
                       # THE primary cap on lidar pulls.

# MIN_PRICE and MAX_ACRES deliberately do not exist. A floor on price would
# filter out cheap land, which is backwards. A ceiling on acreage was only ever
# a proxy for affordability, and MAX_PRICE does that directly -- 60 acres under
# $400k would be a find, not a problem.

# Acreage has only a floor, and it is a sanity bound, NOT the cap.
#
# The old value was 8.0 and it was wrong -- a holdover from when you needed a
# big lot to find 20 ft of high ground somewhere on it. It hid 6600 Blackwater
# Rd entirely (3.641 ac, $225k, Virginia Beach, split Zone X / Zone AE).
# One waterfront acre is fine: no pond needed, the water is already there. The
# floor exists only to skip quarter-acre residential infill (the VB list
# includes a 0.14 ac lot).
MIN_ACRES = 1.0

# Safety net. If the gate ever returns far more than expected -- a widened
# search, a price typo -- stop rather than quietly pulling hundreds of tiles.
MAX_FINE_PULLS = 40


# ==========================================================================
# STAGE 2 - THE COLUMNS
# ==========================================================================
# There are deliberately NO SCORING WEIGHTS here.
#
# An earlier draft had W_TEXTURE = 3.0, W_CANOPY = 2.0 and so on. They were
# invented -- nothing justified texture counting 1.5x canopy -- and two of them
# weighted measurements that did not exist yet, against data we had not
# confirmed was available. A number on a guess about a maybe.
#
# More fundamentally, a composite score claims you can trade texture against
# price against flood exposure at a fixed exchange rate. You cannot. They are
# different kinds of thing, and collapsing them hides which one drove a result:
# a lot at rank 8 does not tell you whether it was the flood exposure or the
# price per acre.
#
# The gate keeps the list to roughly a dozen per jurisdiction. At that size the
# columns ARE the product -- sort by whichever one you care about today.
#
# If the list ever grows past what you can eyeball, revisit. Until then,
# weights would be solving a problem you do not have.

# Default sort for the output table. Any column name works; this is only what
# it is sorted by when written, not a judgement about what matters.
#
# NOTE: tri does NOT discriminate in Virginia Beach -- the whole candidate set
# falls between 0.25 and 0.52 because that terrain genuinely does not vary.
# Chesapeake is similarly flat. Suffolk is where tri earns its keep: Kings Fork
# 1.12 and Murphys Mill 1.16 against 23.8 ft of relief, versus under 3 ft
# anywhere on the coast.
#
# Suffolk trade-off, so it is not a surprise later: real terrain and better
# soil (it sits above the Suffolk Scarp, off the old lagoon floor -- Tetotum
# and Bojac rather than Tomotley and Nawney), no 50 ft Southern Watershed
# buffer since that is a Virginia Beach ordinance, and possibly outside a
# Chesapeake Bay Preservation Area so the disturbance trigger may be 10,000
# sq ft instead of 2,500. Against that: inland, 40-60 min to Sandbridge, no
# kayak access to North Landing River, and western Suffolk borders the Great
# Dismal Swamp with heavy federal wetland. Let the hyd% and flood columns say
# which parcels are near swamp rather than judging the city as a whole.
SORT_BY   = "tri"      # tri, flood_open, price_per_open_acre, relief, acres, price
SORT_DESC = True


# --------------------------------------------------------------------------
# RESOLUTION
# --------------------------------------------------------------------------
# 10 m screens; every tile in data/dem is exactly 10.0 m so metrics are
# comparable across the state line. 1 m is per-parcel and cheap -- a 3.6 acre
# lot is ~15,000 cells. It is what makes building pads, line-of-sight and pond
# siting possible at all; at 10 m a cell is 33 ft across and two buildings
# 200 ft apart are 6 cells apart, so the rise that hides one from the other
# does not exist in the data.
#
# NOTE: TRI at 1 m is NOT comparable to the 10 m numbers (Knotts Island: 0.309
# at 10 m, 0.045 at 1 m). Switching resolutions means recalibrating.

FINE_RES_M       = 1.0
FETCH_FINE_LIDAR = False    # turn on once the short list is short


# --------------------------------------------------------------------------

def show():
    """Print current settings. Run before any expensive sweep."""
    print("\n  GATE (bounds the 1 m lidar pulls)")
    print("    jurisdictions :", ", ".join(JURISDICTIONS))
    price = f"<= ${MAX_PRICE:,}" if MAX_PRICE else "no ceiling"
    print(f"    price         : {price}")
    print(f"    acres         : >= {MIN_ACRES:g}  (sanity bound, not the cap)")
    print(f"    max pulls     : {MAX_FINE_PULLS}  (stops runaway sweeps)")
    print("\n  OUTPUT")
    print(f"    sort by       : {SORT_BY} "
          f"({'descending' if SORT_DESC else 'ascending'})")
    print("    no composite score - every measurement stays its own column")
    print(f"    fine lidar    : {'on' if FETCH_FINE_LIDAR else 'off'} "
          f"({FINE_RES_M:g} m)\n")


if __name__ == "__main__":
    show()
