#!/usr/bin/env python3
"""
Does the unit-address rule remove the campground and nothing else?

3665 Sandpiper Rd Unit 235 sat at the TOP of the sheet for weeks. It is a
deeded site in the Sandpiper RV resort: 11 listings on one 61.46 ac county
parcel, canopy 1.3%, 100% SFHA, water table 0 in. Every column on that row
described the campground's common ground, not any lot for sale. It sorted
first because min_sandbridge = 9 is the best drive time in the set.

Nothing caught it. The feed publishes no lot_sqft for it, so MIN_ACRES had
nothing to compare; VGIN carries no acreage field, so the measured-vs-reported
check had no reference either. It cleared land-type and the price ceiling
honestly.

search_config.EXCLUDE_UNIT_ADDRESSES now drops addresses carrying a unit
designator, as part of the existing "is it land" gate. This checks that claim
against the LIVE feed rather than against the 36 rows that survived the last
sweep -- the other 10 Sandpiper listings were merged away before the output,
so the csv cannot answer the question.

It reports every listing the rule would remove, so a legitimate lot caught by
mistake is visible by address instead of just vanishing.

READ ONLY. Fetches the feed, writes nothing, touches no sheet.

    ./probes/probe_unit_addresses.py
"""

import sys
from collections import Counter

sys.path.insert(0, ".")
import land_watch as lw          # noqa: E402
import search_config as cfg      # noqa: E402


def main():
    print(f"\njurisdictions: {', '.join(cfg.JURISDICTIONS)}")
    print(f"rule active  : EXCLUDE_UNIT_ADDRESSES = "
          f"{getattr(cfg, 'EXCLUDE_UNIT_ADDRESSES', True)}\n")

    raw = []
    for city in cfg.JURISDICTIONS:
        got = lw.fetch_city(city, days_on=None)
        print(f"  {city:28} {len(got):>5} listings")
        raw.extend(got)
    print(f"  {'TOTAL':28} {len(raw):>5}\n")

    # Every listing the feed gave us, before any gate.
    land, dropped, kept = 0, [], 0
    for L in raw:
        e = lw.extract(L)
        # only judge the rule on things that reach it: plain land or a lot
        # carrying a proposed build
        if "land" not in (e["type"] or "") and not e["new_construction"]:
            continue
        land += 1
        if lw.is_unit_address(e["addr"]):
            dropped.append(e)
        else:
            kept += 1

    print(f"{land} listings reach the unit check "
          f"(land-typed or new-construction)")
    print(f"{len(dropped)} removed, {kept} kept\n")

    if not dropped:
        print("  nothing matched -- either the campground is off the market "
              "or the rule is not firing. Check by hand before trusting it.\n")
        return 0

    print("REMOVED -- read this list. A real lot here is a false positive:")
    print(f"  {'address':52} {'type':14} {'acres':>7} {'price':>10}")
    print("  " + "-" * 86)
    for e in sorted(dropped, key=lambda x: str(x["addr"] or "")):
        ac = f"{e['acres']:.2f}" if e.get("acres") else "--"
        pr = f"${e['price']:,}" if e.get("price") else "--"
        print(f"  {str(e['addr'])[:51]:52} {str(e['type'])[:13]:14} "
              f"{ac:>7} {pr:>10}")

    streets = Counter(
        " ".join(str(e["addr"]).split()[1:3]) for e in dropped if e["addr"])
    print("\n  grouped by street:")
    for st, n in streets.most_common():
        print(f"    {n:>3}  {st}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
