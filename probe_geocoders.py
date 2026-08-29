#!/usr/bin/env python3
"""
Which free geocoder can place the listings realtor.com did not geocode?

Every Blackwater Rd lot we want arrives with (None, None) for coordinates, and
qualify() drops anything without a pin because the whole pipeline is
coordinate-driven. VGIN cannot help: its statewide parcel layer carries only
geometry and ids (VGIN_QPID, FIPS, LOCALITY, PARCELID) -- no address field at
all.

Per-city GIS services would work but mean four or five separate integrations
(Virginia Beach, Chesapeake, Suffolk, plus Currituck etc. if NC comes back on),
each with its own URL and field naming. A geocoder covers all of them with one
code path, so it is worth trying first.

Three candidates, all free and keyless:
  Census   - failed with RemoteDisconnected on every request earlier today.
             Retried here because that looked like a bad afternoon, not a block.
  Nominatim- OpenStreetMap. Requires a real User-Agent and 1 request/sec.
  Photon   - Komoot's Nominatim front end, more forgiving of rate limits.

CONTROLS matter more than the targets. Three Blackwater addresses have known
coordinates from the listing feed. A geocoder that returns a confident answer
in the wrong place is worse than one that returns nothing, so we measure the
error against those before trusting anything.

    mv -f ~/Downloads/probe_geocoders.py . && chmod +x probe_geocoders.py && ./probe_geocoders.py
"""

import json
import math
import sys
import time
import urllib.parse
import urllib.request

UA = "land-search/1.0 (personal property research)"

# Ungeocoded listings we are trying to rescue.
TARGETS = [
    "6584 Blackwater Rd, Virginia Beach, VA 23457",
    "6636 Blackwater Rd, Virginia Beach, VA 23457",
    "6664 Blackwater Rd, Virginia Beach, VA 23457",
    "6608 Blackwater Rd, Virginia Beach, VA 23457",
]

# Known coordinates straight from the feed. Error against these is the test.
CONTROLS = [
    ("5361 Blackwater Rd, Virginia Beach, VA 23457", 36.616955, -76.08818),
    ("6336 Blackwater Rd, Virginia Beach, VA 23457", 36.569496, -76.075299),
    ("5409 Blackwater Rd, Virginia Beach, VA 23457", 36.614774, -76.086735),
]


def haversine_m(a, b, c, d):
    R = 6371000.0
    p = math.radians
    dla, dlo = p(c - a), p(d - b)
    h = (math.sin(dla / 2) ** 2 +
         math.cos(p(a)) * math.cos(p(c)) * math.sin(dlo / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def _json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def census(addr):
    url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
           f"?address={urllib.parse.quote(addr)}"
           "&benchmark=Public_AR_Current&format=json")
    js = _json(url)
    m = js["result"]["addressMatches"]
    if not m:
        return None
    c = m[0]["coordinates"]
    return c["y"], c["x"]


def nominatim(addr):
    url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1"
           f"&q={urllib.parse.quote(addr)}")
    js = _json(url)
    if not js:
        return None
    return float(js[0]["lat"]), float(js[0]["lon"])


def photon(addr):
    url = f"https://photon.komoot.io/api/?limit=1&q={urllib.parse.quote(addr)}"
    js = _json(url)
    feats = js.get("features") or []
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    return lat, lon


GEOCODERS = [("census", census, 0.5),
             ("nominatim", nominatim, 1.2),   # 1 req/sec policy
             ("photon", photon, 0.5)]


def main():
    print("=" * 78)
    print("CONTROLS - error against coordinates we already know")
    print("=" * 78)
    scores = {}
    for name, fn, delay in GEOCODERS:
        errs, fails = [], 0
        print(f"\n  {name}")
        for addr, lat, lon in CONTROLS:
            try:
                got = fn(addr)
            except Exception as e:
                print(f"    {addr[:38]:38} ERROR {type(e).__name__}")
                fails += 1
                time.sleep(delay)
                continue
            if got is None:
                print(f"    {addr[:38]:38} no match")
                fails += 1
            else:
                d = haversine_m(lat, lon, got[0], got[1])
                errs.append(d)
                print(f"    {addr[:38]:38} {got[0]:.5f}, {got[1]:.5f}   "
                      f"off by {d:,.0f} m")
            time.sleep(delay)
        scores[name] = (errs, fails)

    print("\n" + "=" * 78)
    print("TARGETS - the listings with no coordinates at all")
    print("=" * 78)
    for name, fn, delay in GEOCODERS:
        print(f"\n  {name}")
        for addr in TARGETS:
            try:
                got = fn(addr)
            except Exception as e:
                print(f"    {addr[:38]:38} ERROR {type(e).__name__}")
                time.sleep(delay)
                continue
            print(f"    {addr[:38]:38} "
                  f"{f'{got[0]:.5f}, {got[1]:.5f}' if got else 'no match'}")
            time.sleep(delay)

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    for name, (errs, fails) in scores.items():
        if not errs:
            print(f"  {name:10} unusable ({fails} failure(s))")
            continue
        med = sorted(errs)[len(errs) // 2]
        note = ("good - within a parcel" if med < 100 else
                "usable - right property, wrong corner" if med < 300 else
                "TOO COARSE - would hit the neighbour's parcel" if med < 2000 else
                "useless - wrong part of town")
        print(f"  {name:10} median error {med:6,.0f} m, {fails} failure(s)   {note}")
    print("\nA parcel here is 3-10 acres, roughly 110-200 m across. Error much")
    print("over ~100 m risks landing the point on the wrong parcel, which would")
    print("silently attach the neighbour's terrain and flood zone to the listing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
