#!/usr/bin/env python3
"""
Does 1 m lidar exist where we are actually looking?

Everything downstream -- building pads, line-of-sight between buildings, pond
siting, canopy height -- depends on the answer, and 3DEP's 1 m coverage is
project-based rather than seamless. It exists where lidar was flown AND
processed to that product. So we check before designing around it.

Three questions, in order of how much they matter:

  1. Is 1 m DEM (bare earth) available at these coordinates?
  2. Is a SURFACE model available? Canopy height is first-return minus bare
     earth, so without a surface product there is no canopy measurement.
     py3dep reliably serves bare earth; the surface side is UNVERIFIED.
  3. Does a real 1 m fetch actually work, and what does the terrain look like
     compared to the 10 m numbers we calibrated on?

Addresses are geocoded through the Census Bureau geocoder, which is free and
needs no key.

    mv -f ~/Downloads/probe_1m.py . && chmod +x probe_1m.py && ./probe_1m.py
"""

import json
import sys
import urllib.parse
import urllib.request

# The real Virginia Beach candidates, land under $400k, from the MLS list.
# Seven Blackwater Rd lots are one subdivided tract -- adjacent parcels on the
# same terrain, so differences between them are signal rather than noise.
ADDRESSES = [
    ("6600 Blackwater Road, Virginia Beach, VA 23457", 3.641, 225000),
    ("6592 Blackwater Road, Virginia Beach, VA 23457", 3.972, 235000),
    ("6584 Blackwater Road, Virginia Beach, VA 23457", 4.349, 235000),
    ("6608 Blackwater Road, Virginia Beach, VA 23457", 3.440, 245000),
    ("6628 Blackwater Road, Virginia Beach, VA 23457", 6.343, 235000),
    ("6636 Blackwater Road, Virginia Beach, VA 23457", 4.284, 225000),
    ("6664 Blackwater Road, Virginia Beach, VA 23457", 9.713, 325000),
    ("2084 Camden Court, Virginia Beach, VA 23457",    3.000, 395000),
    ("2 Hungarian Road, Virginia Beach, VA 23457",     2.800, 375000),
    ("3452 Head River Road, Virginia Beach, VA 23457", 1.000, 300000),
    ("1500 Back Bay Landing Road, Virginia Beach, VA 23457", 0.14, 48200),
]

# Calibration anchors, coordinates already known exactly.
KNOWN = [
    ("cousin's lot, Knotts Island NC", 36.53916, -75.99679),
    ("2281 Kings Fork Rd, Suffolk VA", 36.7852, -76.6103),
]

CENSUS = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
          "?address={addr}&benchmark=Public_AR_Current&format=json")


def geocode(addr):
    url = CENSUS.format(addr=urllib.parse.quote(addr))
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            js = json.loads(r.read().decode())
        m = js["result"]["addressMatches"]
        if not m:
            return None, None
        c = m[0]["coordinates"]
        return c["y"], c["x"]          # lat, lon
    except Exception as e:
        print(f"    geocode failed: {type(e).__name__}")
        return None, None


def sources_at(lat, lon, pad=0.002):
    """Ask py3dep which 3DEP products cover this spot."""
    import py3dep
    bbox = (lon - pad, lat - pad, lon + pad, lat + pad)
    for fn in ("query_3dep_sources", "query_3dep"):
        if hasattr(py3dep, fn):
            try:
                res = getattr(py3dep, fn)(bbox)
                # returns a GeoDataFrame of coverage polygons with a 'dem_res'
                if hasattr(res, "columns"):
                    for col in ("dem_res", "resolution", "name"):
                        if col in res.columns:
                            return sorted(set(str(v) for v in res[col]))
                    return [str(c) for c in res.columns]
                return [str(res)]
            except Exception as e:
                return [f"ERR {type(e).__name__}: {str(e)[:60]}"]
    return ["py3dep has no source-query function in this version"]


def try_fetch(lat, lon, res, pad=0.0015):
    """Actually pull a small tile. Availability listings can lie."""
    import py3dep
    from shapely.geometry import box
    geom = box(lon - pad, lat - pad, lon + pad, lat + pad)
    try:
        da = py3dep.get_map("DEM", geom, resolution=res, geo_crs=4326, crs=4326)
        import numpy as np
        vals = da.values.astype("float64")
        good = np.isfinite(vals)
        return (f"OK  shape {da.shape}, {good.sum()} valid cells, "
                f"{np.nanmin(vals):.1f}..{np.nanmax(vals):.1f} m")
    except Exception as e:
        return f"FAILED  {type(e).__name__}: {str(e)[:70]}"


def main():
    print("=" * 78)
    print("PART 1 - geocode the real candidates")
    print("=" * 78)
    located = []
    for addr, acres, price in ADDRESSES:
        lat, lon = geocode(addr)
        if lat is None:
            print(f"  {addr[:44]:44}  NOT FOUND")
            continue
        print(f"  {addr[:44]:44}  {lat:.5f}, {lon:.5f}  {acres:>6.2f}ac  ${price:,}")
        located.append((addr, lat, lon))

    print("\n" + "=" * 78)
    print("PART 2 - what 3DEP products cover these spots")
    print("=" * 78)
    spots = [(n, la, lo) for n, la, lo in KNOWN]
    if located:
        spots.append(("Blackwater Rd cluster, VB", located[0][1], located[0][2]))
        if len(located) > 8:
            spots.append((located[-2][0][:30], located[-2][1], located[-2][2]))
    for name, lat, lon in spots:
        print(f"\n  {name}  ({lat:.5f}, {lon:.5f})")
        for s in sources_at(lat, lon):
            print(f"      {s}")

    print("\n" + "=" * 78)
    print("PART 3 - does a real 1 m fetch work?")
    print("=" * 78)
    for name, lat, lon in spots:
        print(f"\n  {name}")
        for res in (10, 1):
            print(f"      {res:>2} m : {try_fetch(lat, lon, res)}")

    print("\n" + "=" * 78)
    print("If 1 m works everywhere, terrain metrics move to 1 m and the 10 m")
    print("calibration (Knotts Island 0.146 vs Kings Fork 1.12) has to be redone.")
    print("If coverage is patchy, 10 m stays the screen and 1 m is per-parcel.")
    print("Canopy needs a SURFACE product, which this does not confirm either way.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
