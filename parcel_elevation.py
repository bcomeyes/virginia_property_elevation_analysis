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

import argparse, json, sys, urllib.parse, urllib.request, urllib.error, warnings
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
    """Every cached DEM whose bounding box could cover this parcel, best-first.

    Returns a LIST, not one tile, and that matters. Our localities are adjacent
    and each county DEM is a rectangle with nodata everywhere outside the county
    line, so their bounding boxes overlap heavily. The old version returned the
    first alphabetical bbox hit - which meant a Virginia Beach parcel sitting
    inside Chesapeake's bounding box got clipped against Chesapeake's tile, found
    nothing but nodata, and reported "no valid ground cells in parcel". Five of
    twelve listings failed that way.

    Sorted by distance from the parcel to the tile centre, so the tile the parcel
    is genuinely inside gets tried first; the caller falls through the rest until
    one yields real cells.
    """
    if not DEM_DIR.exists():
        return []
    cx, cy = poly_utm.centroid.x, poly_utm.centroid.y
    hits = []
    for tif in sorted(DEM_DIR.glob("*.tif")):
        with rasterio.open(tif) as src:
            b = src.bounds
            if b.left <= cx <= b.right and b.bottom <= cy <= b.top:
                mx, my = (b.left + b.right) / 2, (b.bottom + b.top) / 2
                hits.append((((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5, tif))
    return [t for _, t in sorted(hits, key=lambda h: h[0])]


def _clip_to_dem(tif, poly_utm):
    """Clip one DEM to the parcel. Returns (ft_array, cell_m) or (None, None)."""
    with rasterio.open(tif) as src:
        try:
            arr, _ = rasterio.mask.mask(src, [poly_utm.__geo_interface__],
                                        crop=True, filled=True,
                                        nodata=src.nodata)
        except ValueError:
            return None, None
        cell_m = abs(src.transform.a)
        nod = src.nodata

    z = arr[0].astype("float32")
    if nod is not None:
        z[z == nod] = np.nan
    z[z < -100] = np.nan
    ft = z * M_TO_FT
    ft[ft < -2] = np.nan                       # water / nodata leak
    return ft, cell_m


def terrain_metrics(ft, cell_m):
    """Shape-of-the-ground metrics from an already-clipped, already-masked
    elevation array (feet, NaN outside the parcel).

    Four numbers, deliberately simple, all from the same bare-earth array we
    already have. No new data, no new API call.

      relief_ft  p95 - p5 spread. Robust version of max-minus-min: one spike
                 from a leftover barn roof or a DEM artifact can't inflate it.
                 This is the "does the ground move" number.
      std_ft     standard deviation of elevation. Cheap, and it separates a
                 pancake from a rolling lot better than you'd expect.
      slope_deg  mean slope. Marsh and cleared farm field both sit near zero;
                 anything with a fold in it climbs.
      tri_ft     terrain ruggedness index - mean absolute difference between a
                 cell and its 8 neighbours. Catches SMALL texture (swales,
                 hummocks) that relief and std both average away.

    NOTE ON MARSH: marsh is flat by definition, so it scores near zero on all
    four without any wetland layer, any mask, or any special-case rule. That is
    the whole reason we don't need one.

    These are REPORTED, not thresholded. Nothing here disqualifies a parcel.
    """
    out = {}
    valid = ft[~np.isnan(ft)]
    if valid.size < 9:
        out["terrain_note"] = f"only {valid.size} cells - too small to measure"
        return out

    p5, p95 = np.nanpercentile(ft, [5, 95])
    out["relief_ft"] = round(float(p95 - p5), 2)
    out["std_ft"]    = round(float(np.nanstd(ft)), 2)

    # slope: gradient of the ft surface over cell size in ft
    cell_ft = cell_m * M_TO_FT
    gy, gx = np.gradient(ft, cell_ft)
    grad = np.sqrt(gx ** 2 + gy ** 2)
    out["slope_deg"] = round(float(np.degrees(np.arctan(np.nanmean(grad)))), 2)

    # TRI: mean |centre - neighbour| over the 8 shifts. np.roll wraps at the
    # edges, so blank the wrapped row/column before differencing.
    diffs = []
    for dy, dx in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
        sh = np.roll(np.roll(ft, dy, axis=0), dx, axis=1)
        if dy == 1:   sh[0, :]  = np.nan
        if dy == -1:  sh[-1, :] = np.nan
        if dx == 1:   sh[:, 0]  = np.nan
        if dx == -1:  sh[:, -1] = np.nan
        diffs.append(np.abs(ft - sh))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        tri = np.nanmean(np.dstack(diffs))
    out["tri_ft"] = round(float(tri), 3) if np.isfinite(tri) else None
    return out


def sample_parcel_elevation(poly_4326, threshold_ft=20.0, verbose=False):
    """Clip the bare-earth DEM to the parcel and summarize its elevations."""
    poly_utm = shp_transform(_to_utm, poly_4326)
    cands = pick_dem(poly_utm)
    if not cands:
        return {"error": "no cached DEM covers this parcel"}

    # Try each candidate and keep whichever actually yields ground. A bbox hit
    # is not a data hit: overlapping county rectangles mean the nearest tile can
    # still be all nodata over this parcel. Keep the best, not the first.
    ft = cell_m = tif = None
    best = 0
    for cand in cands:
        f, c = _clip_to_dem(cand, poly_utm)
        if f is None:
            continue
        n = int(np.count_nonzero(~np.isnan(f)))
        if n > best:
            ft, cell_m, tif, best = f, c, cand, n
    if ft is None:
        return {"error": "parcel does not overlap DEM"}
    if best == 0:
        return {"error": f"no valid ground cells in parcel "
                         f"(tried {len(cands)} DEM{'s' if len(cands) > 1 else ''})"}

    valid = ft[~np.isnan(ft)]

    cell_acres = (cell_m * cell_m) / ACRE_M2
    high = valid >= threshold_ft

    # Cross-check: acreage the POLYGON says vs acreage the DEM actually gave us.
    # These should agree closely. When they don't it means either the parcel
    # straddles two DEM tiles (we clip against a single tile, so the far half
    # gets silently dropped) or a big chunk masked out as water/nodata.
    # Either way every stat below is describing less land than you think.
    poly_acres = poly_utm.area / ACRE_M2
    dem_acres  = valid.size * cell_acres
    coverage   = dem_acres / poly_acres if poly_acres > 0 else 0.0

    res = {
        "parcel_acres": round(dem_acres, 2),
        "poly_acres":   round(poly_acres, 2),
        "dem_coverage": round(coverage, 3),
        "max_ft":  round(float(valid.max()), 1),
        "mean_ft": round(float(valid.mean()), 1),
        "min_ft":  round(float(valid.min()), 1),
        "high_acres": round(int(high.sum()) * cell_acres, 2),
        "high_frac": round(float(high.mean()), 3),
        "cell_m": round(cell_m, 1),
        "dem": tif.stem,
    }
    res.update(terrain_metrics(ft, cell_m))

    warns = []
    # Suffolk genuinely reaches 70+ ft, so 60 was firing on real terrain. This
    # only flags a spike that towers over its own lot - the barn-roof signature.
    if res["max_ft"] > 90 or (res.get("relief_ft") and
                              res["max_ft"] - res["mean_ft"] > 40):
        warns.append("tall spike vs lot mean - possible structure, verify by eye")
    if coverage < 0.85:
        warns.append(f"DEM covers only {coverage:.0%} of the polygon - "
                     "tile straddle or water mask, stats are partial")
    if warns:
        res["warn"] = "; ".join(warns)
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
    print(f"threshold = {threshold_ft:g} ft, need >= {min_high_acres} acres of it")
    print("terrain is REPORTED, not filtered: relief/std/tri near zero = flat "
          "(marsh, cleared field)\n")
    hdr = (f"{'qual':4} {'max':>5} {'hi_ac':>6} {'lot_ac':>6} {'cov':>5} "
           f"{'relief':>6} {'std':>5} {'tri':>5}  address")
    print(hdr)
    print("-" * len(hdr))
    for e in rows[:limit]:
        r = check(e["lat"], e["lon"], threshold_ft, min_high_acres)
        if r.get("error"):
            print(f"  ?? {r['error'][:44]:44}  {e['addr']}, {e['city']}")
            continue
        q = "YES" if r["qualifies"] else " no"
        w = "  !" + r["warn"] if r.get("warn") else ""
        def n(k, w_=6, d=2):
            v = r.get(k)
            return f"{v:{w_}.{d}f}" if isinstance(v, (int, float)) else " " * w_
        print(f" {q:4} {r['max_ft']:5.1f} {r['high_acres']:6.2f} "
              f"{r['parcel_acres']:6.2f} {r['dem_coverage']:5.2f} "
              f"{n('relief_ft')} {n('std_ft',5)} {n('tri_ft',5)}  "
              f"{e['addr']}, {e['city']}{w}")
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
