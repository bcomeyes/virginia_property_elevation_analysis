#!/usr/bin/env python3
"""
parcel_elevation.py - does a for-sale parcel have surge-safe high ground on it?

THE QUESTION (Matt's, refined):
  Not "is the listing's pin at 20 ft" and not "is it near a high-ground pocket."
  The real test: does ANY real bare ground inside the parcel boundary reach the
  threshold - because you'll dig a pond and raise a pad, so you only need one
  buildable high corner, not the whole lot high.

HOW:
  1. Take the listing's lat/lon, ask VGIN which parcel polygon contains it.
  2. Clip the cached bare-earth DEM to that polygon.
  3. Report the elevation distribution on the lot: max, and how many acres sit
     at or above the threshold. Bare-earth DEM means barns/outbuildings are
     already mostly stripped, so we're measuring dirt, not roofs.

STANDALONE by design. Prove it against real listings first (--test), THEN it
gets folded into land_watch.py's daily filter once trusted.

USAGE:
    ./parcel_elevation.py --probe 36.72 -76.24     # dump raw VGIN parcel (debug)
    ./parcel_elevation.py --point 36.72 -76.24     # full check on one coordinate
    ./parcel_elevation.py --test                   # run on today's real land listings
    ./parcel_elevation.py --test --threshold 15    # same, 15 ft bar
"""

import argparse, json, sys, urllib.parse, urllib.request, urllib.error
from pathlib import Path

import numpy as np
import rasterio
import rasterio.mask
from shapely.geometry import shape, Point
from shapely.ops import transform as shp_transform
from pyproj import Transformer

HERE     = Path(__file__).resolve().parent
DEM_DIR  = HERE / "data" / "dem"
WORKING_EPSG = 32618
M_TO_FT  = 3.280839895
ACRE_M2  = 4046.8564224

VGIN = ("https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
        "VA_Base_Layers/VA_Parcels/MapServer/0/query")

_to_utm = Transformer.from_crs(4326, WORKING_EPSG, always_xy=True).transform


# --------------------------------------------------------------------------- #
def get_parcel_polygon(lat, lon, verbose=False):
    """Ask VGIN for the parcel polygon containing (lat, lon). Returns (shapely
    polygon in 4326, attrs dict) or (None, None)."""
    params = {
        "f": "geojson",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326, "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
    }
    url = VGIN + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            js = json.loads(r.read().decode())
    except (urllib.error.URLError, ValueError) as e:
        if verbose:
            print(f"    VGIN error: {type(e).__name__}: {e}")
        return None, None

    feats = js.get("features") or []
    if verbose:
        print(json.dumps(js, indent=2)[:1500])
    if not feats:
        return None, None
    f = feats[0]
    try:
        poly = shape(f["geometry"])
    except Exception:
        return None, None
    return poly, f.get("properties", {})


def pick_dem(poly_utm):
    """Which cached county DEM covers this parcel? Return path or None."""
    if not DEM_DIR.exists():
        return None
    cx, cy = poly_utm.centroid.x, poly_utm.centroid.y
    for tif in sorted(DEM_DIR.glob("*.tif")):
        with rasterio.open(tif) as src:
            b = src.bounds
            if b.left <= cx <= b.right and b.bottom <= cy <= b.top:
                return tif
    return None


def sample_parcel_elevation(poly_4326, threshold_ft=20.0, verbose=False):
    """Clip the bare-earth DEM to the parcel and summarize its elevations."""
    poly_utm = shp_transform(_to_utm, poly_4326)
    tif = pick_dem(poly_utm)
    if tif is None:
        return {"error": "no cached DEM covers this parcel"}

    with rasterio.open(tif) as src:
        try:
            arr, _ = rasterio.mask.mask(src, [poly_utm.__geo_interface__],
                                        crop=True, filled=True,
                                        nodata=src.nodata)
        except ValueError:
            return {"error": "parcel does not overlap DEM"}
        cell_m = abs(src.transform.a)
        nod = src.nodata

    z = arr[0].astype("float32")
    if nod is not None:
        z[z == nod] = np.nan
    z[z < -100] = np.nan
    ft = z * M_TO_FT
    ft[ft < -2] = np.nan                       # water / nodata leak

    valid = ft[~np.isnan(ft)]
    if valid.size == 0:
        return {"error": "no valid ground cells in parcel"}

    cell_acres = (cell_m * cell_m) / ACRE_M2
    high = valid >= threshold_ft
    res = {
        "parcel_acres": round(valid.size * cell_acres, 2),
        "max_ft":  round(float(valid.max()), 1),
        "mean_ft": round(float(valid.mean()), 1),
        "min_ft":  round(float(valid.min()), 1),
        "high_acres": round(int(high.sum()) * cell_acres, 2),
        "high_frac": round(float(high.mean()), 3),
        "cell_m": round(cell_m, 1),
        "dem": tif.stem,
    }
    if res["max_ft"] > 60:
        res["warn"] = "max >60 ft - possible structure/artifact, verify by eye"
    return res


def check(lat, lon, threshold_ft=20.0, min_high_acres=0.25, verbose=False):
    """Full test for one coordinate. qualifies = enough bare ground >= threshold
    to place a pad on (min_high_acres), regardless of the pin's own elevation."""
    poly, attrs = get_parcel_polygon(lat, lon, verbose=verbose)
    if poly is None:
        return {"qualifies": None, "error": "no parcel found at point"}
    stats = sample_parcel_elevation(poly, threshold_ft, verbose=verbose)
    if "error" in stats:
        return {"qualifies": None, **stats}
    stats["qualifies"] = stats["high_acres"] >= min_high_acres
    stats["threshold_ft"] = threshold_ft
    # a couple of owner/id fields if VGIN returned them
    for k in ("PARCELID", "LOCALITY", "OWNERNAME", "GPIN"):
        if attrs and k in attrs:
            stats[k.lower()] = attrs[k]
    return stats


# --------------------------------------------------------------------------- #
def run_test(threshold_ft, min_high_acres, limit):
    """End-to-end against today's real land listings (needs land_watch.py)."""
    try:
        import land_watch as lw
    except ImportError:
        print("land_watch.py not found next to this script - needed for --test")
        return 1
    pockets = None
    rows = []
    for city in lw.CITIES:
        raw = lw.fetch_city(city, days_on=0)
        for L in raw:
            e = lw.extract(L)
            if "land" in e["type"] and e["lat"] and e["acres"] and \
               lw.MIN_ACRES <= e["acres"] <= lw.MAX_ACRES:
                rows.append(e)
    print(f"\n{len(rows)} land listings in the {lw.MIN_ACRES:g}-{lw.MAX_ACRES:g} "
          f"acre window across {len(lw.CITIES)} cities")
    print(f"threshold = {threshold_ft:g} ft, need >= {min_high_acres} acres of it\n")
    print(f"{'qual':4} {'max':>5} {'hi_ac':>6} {'lot_ac':>6}  address")
    print("-" * 72)
    for e in rows[:limit]:
        r = check(e["lat"], e["lon"], threshold_ft, min_high_acres)
        if r.get("error"):
            print(f"  ?? {r['error']:20}            {e['addr']}, {e['city']}")
            continue
        q = "YES" if r["qualifies"] else " no"
        w = "  !" + r["warn"] if r.get("warn") else ""
        print(f" {q:4} {r['max_ft']:5.1f} {r['high_acres']:6.2f} "
              f"{r['parcel_acres']:6.2f}  {e['addr']}, {e['city']}{w}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--probe", nargs=2, metavar=("LAT", "LON"), type=float)
    g.add_argument("--point", nargs=2, metavar=("LAT", "LON"), type=float)
    g.add_argument("--test", action="store_true")
    p.add_argument("--threshold", type=float, default=20.0)
    p.add_argument("--min-high-acres", type=float, default=0.25)
    p.add_argument("--limit", type=int, default=40)
    a = p.parse_args()

    if a.probe:
        get_parcel_polygon(a.probe[0], a.probe[1], verbose=True)
    elif a.point:
        print(json.dumps(check(a.point[0], a.point[1], a.threshold,
                               a.min_high_acres, verbose=True), indent=2))
    else:
        sys.exit(run_test(a.threshold, a.min_high_acres, a.limit))
