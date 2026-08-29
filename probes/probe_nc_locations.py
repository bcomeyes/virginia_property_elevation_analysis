#!/usr/bin/env python3
"""
Find out how the listings API wants North Carolina locations named.

Virginia is easy: Chesapeake, Virginia Beach and Suffolk are single
municipalities, so one location string covers a whole jurisdiction. North
Carolina counties are not -- Currituck contains Moyock, Barco, Coinjock,
Grandy, Knotts Island and more, and an API that resolves city names may return
nothing at all for "Currituck County, NC".

This asks one page per candidate string and reports what came back. Cheap:
about a dozen calls against a 6,000/month plan.

What to look for:
  total = 0        -> the API did not resolve this string. Unusable.
  total > 0        -> resolved. Check the cities column: if a county string
                      returns listings spread across several towns, county
                      queries work and we are done. If it returns one town,
                      it silently resolved to something narrower and lying.

    mv -f ~/Downloads/probe_nc_locations.py . && chmod +x probe_nc_locations.py && ./probe_nc_locations.py
"""

import collections
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")
try:
    import land_watch as lw
except ImportError:
    sys.exit("run this from the repo root, next to land_watch.py")

CANDIDATES = [
    # county-level, the string we would prefer to use
    "Currituck County, NC",
    "Camden County, NC",
    "Gates County, NC",
    # bare county name -- some APIs treat this as a place
    "Currituck, NC",
    "Camden, NC",
    "Gates, NC",
    # towns inside Currituck, to see what county coverage would cost
    "Moyock, NC",
    "Barco, NC",
    "Coinjock, NC",
    "Grandy, NC",
    "Knotts Island, NC",
    # a known-good VA control: if this misbehaves, the probe is wrong, not NC
    "Suffolk, VA",
]


def one_page(location):
    params = {"location": location, "offset": 0, "limit": 50, "sort": "newest"}
    url = lw.URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "x-rapidapi-key": lw.RAPIDAPI_KEY, "x-rapidapi-host": lw.HOST,
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_err": f"HTTP {e.code}"}
    except Exception as e:
        return {"_err": type(e).__name__}


def main():
    if not lw.RAPIDAPI_KEY:
        sys.exit("no RAPIDAPI_KEY found in .env")

    print(f"{'location':26} {'total':>6} {'land':>5} {'inwin':>6}  cities returned")
    print("-" * 100)

    for loc in CANDIDATES:
        data = one_page(loc)
        if "_err" in data:
            print(f"{loc:26} {data['_err']}")
            time.sleep(1.0)
            continue

        listings = data.get("listings") or []
        total = data.get("totalResultCount", len(listings))

        land = inwin = 0
        cities = collections.Counter()
        for L in listings:
            e = lw.extract(L)
            if e["city"]:
                cities[e["city"]] += 1
            if "land" not in (e["type"] or ""):
                continue
            land += 1
            if e["acres"] and lw.MIN_ACRES <= e["acres"] <= lw.MAX_ACRES:
                inwin += 1

        top = ", ".join(f"{c}({n})" for c, n in cities.most_common(5))
        print(f"{loc:26} {total:6d} {land:5d} {inwin:6d}  {top[:58]}")
        time.sleep(1.0)

    print("\nland  = property type contains 'land', on the first page only")
    print(f"inwin = also within the {lw.MIN_ACRES:g}-{lw.MAX_ACRES:g} acre window")
    print("\nIf the county strings return 0 but the towns return listings, we")
    print("need a town list per county rather than three county names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
