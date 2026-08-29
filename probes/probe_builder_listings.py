#!/usr/bin/env python3
"""
What separates a builder's lot-plus-spec-house listing from a real house?

The Blackwater Rd lots are in the feed, but typed single_family with a package
price: 6664 is "land, $325,000" in the MLS and "single_family, $954,409" here.
Same 9.71 acres, same parcel. Realtor.com is carrying lot + house-to-be-built.

Both our filters fire on a house that does not exist yet -- the type filter
drops it, and the package price blows the ceiling. The land Matt can afford is
hidden behind the price of a house he does not want.

Loosening the type filter is not the fix; that pulls in all 721 Virginia Beach
single_family listings. We need a signal. The obvious candidate is that a real
house has beds, baths and square footage and a to-be-built one does not.

This dumps the raw fields for the suspect listings next to known-real houses
so we can see what actually differs. It does NOT assume the answer.

    mv -f ~/Downloads/probe_builder_listings.py . && chmod +x probe_builder_listings.py && ./probe_builder_listings.py
"""

import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, ".")
try:
    import land_watch as lw
except ImportError:
    sys.exit("run this from the repo root, next to land_watch.py")

SUSPECT = ["6608 blackwater", "6628 blackwater", "6664 blackwater", "mm blackwater"]
REAL_HOUSE = ["5361 blackwater", "5409 blackwater", "6336 blackwater", "6576 blackwater"]
KNOWN_LAND = ["6584 blackwater", "camden ct", "head river"]

# Fields worth looking at. Printed only if present and non-empty.
WATCH = ["type", "sub_type", "beds", "baths", "baths_full", "sqft",
         "lot_sqft", "year_built", "stories", "sold_price", "sold_date",
         "name", "sub_name", "is_new_construction", "is_plan", "is_subdivision"]


def fetch_all(location, max_pages=25):
    out, offset, total = [], 0, None
    for _ in range(max_pages):
        url = lw.URL + "?" + urllib.parse.urlencode(
            {"location": location, "offset": offset, "limit": 50, "sort": "newest"})
        req = urllib.request.Request(url, headers={
            "x-rapidapi-key": lw.RAPIDAPI_KEY, "x-rapidapi-host": lw.HOST,
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            print(f"  fetch error: {type(e).__name__}")
            break
        batch = data.get("listings") or []
        out.extend(batch)
        if total is None:
            total = data.get("totalResultCount", len(batch))
        offset += 50
        if not batch or offset >= (total or 0):
            break
        time.sleep(0.8)
    return out


def addr_of(L):
    loc = (L.get("location") or {}).get("address") or {}
    return (loc.get("line") or "").lower()


def show(L, tag):
    d = L.get("description") or {}
    loc = (L.get("location") or {}).get("address") or {}
    print(f"\n  [{tag}] {loc.get('line')}  ${L.get('list_price') or 0:,}")
    bits = []
    for k in WATCH:
        v = d.get(k, L.get(k))
        bits.append(f"{k}={v!r}")
    print("      " + "  ".join(bits))
    # anything at top level we have not looked at that might flag new builds
    extras = {k: v for k, v in L.items()
              if k not in ("location", "description", "photos", "primary_photo",
                           "other_listings", "branding", "tags")
              and v not in (None, "", [], {})}
    interesting = {k: v for k, v in extras.items()
                   if any(w in k.lower() for w in
                          ("new", "plan", "builder", "construct", "flag", "advantage",
                           "status", "type", "sub"))}
    if interesting:
        print(f"      top-level: {interesting}")
    if d.get("tags") or L.get("tags"):
        print(f"      tags: {(d.get('tags') or L.get('tags'))[:12]}")


def main():
    if not lw.RAPIDAPI_KEY:
        sys.exit("no RAPIDAPI_KEY in .env")

    print("pulling 23457 (59 listings, 2 calls)")
    listings = fetch_all("23457")
    print(f"got {len(listings)}")

    print("\n" + "=" * 74)
    print("SUSPECT - MLS says land, feed says single_family")
    print("=" * 74)
    for L in listings:
        a = addr_of(L)
        if any(s in a for s in SUSPECT):
            show(L, "suspect")

    print("\n" + "=" * 74)
    print("REAL HOUSES on the same road - the comparison group")
    print("=" * 74)
    for L in listings:
        a = addr_of(L)
        if any(s in a for s in REAL_HOUSE):
            show(L, "house")

    print("\n" + "=" * 74)
    print("KNOWN LAND - correctly typed, for reference")
    print("=" * 74)
    for L in listings:
        a = addr_of(L)
        if any(s in a for s in KNOWN_LAND):
            show(L, "land")

    print("\n" + "=" * 74)
    print("Look for a field that is set on every real house and absent on every")
    print("suspect. beds/baths/sqft are the likely candidates. If nothing")
    print("separates them cleanly, do NOT invent a rule -- a false positive here")
    print("means ranking a $950k house as if it were a $325k lot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
