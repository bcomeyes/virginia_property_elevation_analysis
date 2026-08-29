#!/usr/bin/env python3
"""
=============================================================================
SEARCH CONFIG - the only file you edit to change what gets searched
=============================================================================

Everything in this file was previously a constant buried in land_watch.py or a
flag you had to remember to type. Both hid decisions. The 8-acre minimum sat in
land_watch.py for months contradicting what Matt actually wanted, and nothing
ever forced it to surface.

So: one block, every parameter visible, with the reasoning next to it. If a
decision is not written here, it is not in effect.

Uncomment the line you want. Comment out the rest.

    python3 -c "import search_config as c; c.show()"      # print current settings
=============================================================================
"""

# --------------------------------------------------------------------------
# JURISDICTIONS
# --------------------------------------------------------------------------
# VA entries are independent cities (Virginia Beach and Chesapeake are former
# counties absorbed by cities). NC entries are true counties, and the word
# "County" is load-bearing -- "Currituck, NC" silently means the TOWN of
# Currituck, 25 listings instead of 675.
#
# NC is real: 54 ac in Barco at $675k vs 45.8 ac on Holland Rd at $3.1M, same
# commute. It is also a different state's laws, which is a live disagreement in
# the house. Kept switchable rather than deleted so the comparison stays
# available instead of being decided by omission.

VA_ALL   = ["Virginia Beach, VA", "Chesapeake, VA", "Suffolk, VA"]
NC_ALL   = ["Currituck County, NC", "Camden County, NC", "Gates County, NC"]

# ---- pick one ----
JURISDICTIONS = ["Virginia Beach, VA"]                    # <-- GUESS: the stated first choice
# JURISDICTIONS = ["Virginia Beach, VA", "Chesapeake, VA"]  # VB + eastern Chesapeake
# JURISDICTIONS = VA_ALL                                    # all three VA cities
# JURISDICTIONS = VA_ALL + NC_ALL                           # everything, both states
# JURISDICTIONS = NC_ALL                                    # NC only, for price comparison


# ==========================================================================
# STAGE 1 - THE GATE
# ==========================================================================
# The gate exists to BOUND THE 1 m LIDAR PULLS. Its output count is exactly
# how many parcels get fine-resolution analysis. Price and jurisdiction do
# that work: land in Virginia Beach under $400k is 12 listings, so 12 pulls.
#
# Only facts are allowed to gate. Never a judgement about quality -- terrain,
# flood, canopy and drive time are scored and ranked, never used to remove a
# listing before Matt has seen it. Inventory is thin and one hidden lot matters.

MAX_PRICE = 400_000    # <-- GUESS, from "we cannot afford really anything over
                       # $400K". Did not exist as a parameter at all before.
                       # THE primary cap on lidar pulls.

# MIN_PRICE and MAX_ACRES deliberately do not exist. A floor on price would
# filter out cheap land, which is backwards. A ceiling on acreage was only ever
# a proxy for affordability, and MAX_PRICE does that directly -- 60 acres under
# $400k would be a find, not a problem.

# Acreage has only a floor, and it is a sanity bound, NOT the cap. Price and jurisdiction already do
# the capping, so this only needs to be low enough to hide nothing and high
# enough to skip quarter-acre residential infill.
#
# The old value was 8.0 and it was wrong -- a holdover from when you needed a
# big lot to find 20 ft of high ground somewhere on it. It hid 6600 Blackwater
# Rd entirely (3.641 ac, $225k, Virginia Beach, split Zone X / Zone AE).
# Matt's own stated case: 2 ac waterfront, 1 ac X and 1 ac AE, is a lot he
# would buy.

# One waterfront acre is fine -- no pond needed, the water is already there.
# The floor exists only to skip quarter-acre residential infill (the VB list
# includes a 0.14 ac lot).
MIN_ACRES = 1.0

# Safety net. If the gate ever returns far more than expected -- a widened
# search, a price typo -- stop instead of quietly pulling hundreds of tiles.
MAX_FINE_PULLS = 40


# ==========================================================================
# STAGE 2 - THE COLUMNS
# ==========================================================================
# There are deliberately NO SCORING WEIGHTS here.
#
# An earlier draft had W_TEXTURE = 3.0, W_CANOPY = 2.0 and so on. They were
# invented -- nothing justified texture counting 1.5x canopy -- and two of them
# weighted measurements that do not exist yet, against data we have not
# confirmed is available. A number on a guess about a maybe.
#
# More fundamentally, a composite score claims you can trade texture against
# price against flood exposure at a fixed exchange rate. You cannot. They are
# different kinds of thing, and collapsing them hides which one drove a
# result: a lot at rank 8 does not tell you whether it was the flood exposure
# or the price per acre.
#
# The gate keeps the list to roughly a dozen. At that size the columns ARE the
# product -- sort by whichever one you care about today.
#
# If the list ever grows past what you can eyeball, revisit this. Until then,
# adding weights would be solving a problem you do not have.

# Default sort for the output table. Any column name works; this is only what
# it is sorted by when written, not a judgement about what matters.
SORT_BY   = "tri"      # tri, flood_open, price_per_open_acre, relief, acres, price
SORT_DESC = True


# --------------------------------------------------------------------------
# RESOLUTION
# --------------------------------------------------------------------------
# 10 m screens; every tile in data/dem is now exactly 10.0 m so metrics are
# comparable across the state line. 1 m is per-parcel and cheap -- a 3.6 acre
# lot is ~15,000 cells, and 12 listings is a few MB. It is what makes building
# pads, line-of-sight between buildings, and pond siting possible at all;
# at 10 m a cell is 33 ft across and two buildings 200 ft apart are 6 cells
# apart, so the rise that hides one from the other does not exist in the data.
#
# NOTE: TRI at 1 m is NOT comparable to the numbers above. Switching
# resolutions means recalibrating against Knotts Island and Kings Fork.

FINE_RES_M      = 1.0
FETCH_FINE_LIDAR = False    # <-- GUESS: turn on once the short list is short


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
