#!/usr/bin/env python3
"""
deep_dive.py - a land budget for ONE parcel. How many acres of what, and where.

The table in find_land.py gives one row per parcel so forty can be compared.
This is the opposite: everything known about a single lot, broken down by
acreage, because "50 acres" means nothing until you know how much of it is
water, how much is buildable, and what the rest is.

7399 Crittenden Rd is the case that prompted it: 50.43 acres, 75% Zone AE,
40.26 drained acres, and the listing photo shows most of it is a lake with a
peninsula. Those numbers cannot all describe the same thing until you split
them by area.

WHAT IT REPORTS
  water       flat and low ground -- pond, marsh, tidal flat
  elevation   acres in each band, so you can see the peninsula as a shape
  flood       acres in each FEMA zone
  soil        acres of each map unit with its drainage class
  pads        dry, flat, wide enough to build on, with coordinates
  screening   whether the landform can hide buildings from each other

    ./deep_dive.py --address "7399 Crittenden Rd" --city Suffolk
    ./deep_dive.py --point 36.53916 -75.99679
"""

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, ".")
import land_watch as lw          # noqa: E402
import parcel_elevation as pe    # noqa: E402
from water_detect import water_mask  # noqa: E402

warnings.filterwarnings("ignore")

M_TO_FT = 3.280839895
CACHE   = Path("cache/fine")

PAD_SLOPE_DEG   = 3.0
PAD_MIN_FT      = 60.0
DRY_ABOVE_FT    = 2.0
WET_SLOPE_DEG   = 0.6
SCREENING_RELIEF_FT = 8.0


def load_dem(poly, res=1):
    CACHE.mkdir(parents=True, exist_ok=True)
    import hashlib
    tag = hashlib.md5(poly.wkb).hexdigest()[:12]
    fp = CACHE / f"dd_{tag}_{res:g}m.npz"
    if fp.exists():
        z = np.load(fp)
        return z["ft"], float(z["cell_m"]), tuple(z["bounds"])

    import time
    import py3dep
    import rioxarray  # noqa: F401
    for attempt in range(1, 4):
        try:
            da = py3dep.get_map("DEM", poly, resolution=res, geo_crs=4326, crs=4326)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * attempt)
    da = da.rio.reproject(pe.WORKING_EPSG, resolution=res)
    da = da.rio.clip([pe.shp_transform(pe._to_utm, poly)], pe.WORKING_EPSG, drop=True)
    arr = da.values.astype("float64")
    if arr.ndim == 3:
        arr = arr[0]
    if da.rio.nodata is not None:
        arr[arr == da.rio.nodata] = np.nan
    arr[arr < -100] = np.nan
    ft = arr * M_TO_FT
    b = da.rio.bounds()
    np.savez_compressed(fp, ft=ft, cell_m=res, bounds=np.array(b))
    return ft, float(res), b


def slope_deg(ft, cell_m):
    """NaN wherever a neighbour was missing -- filling invents a cliff at the
    parcel edge and reports 12-15 degrees on ground 8 ft above sea level."""
    good = np.isfinite(ft)
    if good.sum() < 9:
        return np.full(ft.shape, np.nan)
    filled = np.where(good, ft, np.nanmean(ft))
    gy, gx = np.gradient(filled, cell_m * M_TO_FT)
    s = np.degrees(np.arctan(np.hypot(gy, gx)))
    s[~ndimage.binary_erosion(good, np.ones((3, 3)), border_value=0)] = np.nan
    return s


def rc_to_lonlat(row, col, bounds, cell_m):
    left, bottom, right, top = bounds
    import pyproj
    tr = pyproj.Transformer.from_crs(pe.WORKING_EPSG, 4326, always_xy=True)
    lon, lat = tr.transform(left + (col + 0.5) * cell_m, top - (row + 0.5) * cell_m)
    return lat, lon


def bar(frac, width=28):
    n = int(round(frac * width))
    return "#" * n + "." * (width - n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--point", nargs=2, type=float, metavar=("LAT", "LON"))
    ap.add_argument("--address")
    ap.add_argument("--city", default="Virginia Beach")
    ap.add_argument("--state", default="VA")
    ap.add_argument("--zip", default=None)
    ap.add_argument("--res", type=float, default=1.0)
    a = ap.parse_args()

    if a.address:
        lat, lon = lw.geocode(a.address, a.city, a.state, a.zip)
        if lat is None:
            sys.exit(f"could not geocode {a.address}")
    elif a.point:
        lat, lon = a.point
    else:
        sys.exit("need --point LAT LON or --address")

    poly, attrs = pe.get_parcel_polygon(lat, lon)
    if poly is None:
        sys.exit(f"no parcel at {lat:.5f}, {lon:.5f}")
    poly_utm = pe.shp_transform(pe._to_utm, poly)
    acres = poly_utm.area / pe.ACRE_M2

    print("=" * 76)
    print(f"{a.address or f'{lat:.5f}, {lon:.5f}'}")
    print(f"{acres:.2f} acres   parcel from {(attrs or {}).get('_source')}"
          f"   {(attrs or {}).get('_address') or ''}")
    print("=" * 76)

    ft, cell, bounds = load_dem(poly, a.res)
    good = np.isfinite(ft)
    cell_ac = (cell ** 2) / 4046.8564224
    dem_ac = good.sum() * cell_ac
    print(f"\n{ft.shape[0]}x{ft.shape[1]} grid at {cell:g} m, {good.sum():,} cells "
          f"= {dem_ac:.2f} ac of DEM coverage")
    if dem_ac < acres * 0.9:
        print(f"  NOTE only {dem_ac/acres*100:.0f}% of the parcel has elevation data.")
        print(f"  Open water is often masked out of bare-earth lidar, so a big")
        print(f"  gap here IS the lake.")

    s = slope_deg(ft, cell)

    # ---- water ------------------------------------------------------------
    # Water by FLATNESS, not elevation. The old test -- flat and within 2 ft
    # of the parcel's 2nd percentile -- reported this parcel's 26-acre pond as
    # a 20-acre BUILDING PAD, because the percentile anchored on tidal water
    # six feet lower. See water_detect.py.
    wet, water_bodies = water_mask(ft, cell)
    wet_ac = wet.sum() * cell_ac
    print(f"\nWATER   {wet_ac:6.2f} ac "
          f"({wet_ac/max(dem_ac,0.01)*100:.0f}% of measured ground)")
    if water_bodies:
        for b in water_bodies[:5]:
            print(f"        {b['acres']:7.2f} ac at {b['elev_ft']:6.1f} ft")
        print(f"        Detected by FLATNESS, not elevation: a lidar water")
        print(f"        surface has local std under 0.1 ft and real ground")
        print(f"        never does. Bodies at different elevations are separate")
        print(f"        -- a freshwater pond above tidal water is common here.")
    else:
        print(f"        no body larger than 0.25 ac")
    missing = acres - dem_ac
    if missing > 0.5:
        print(f"        + {missing:.2f} ac with NO elevation data -- likely open")
        print(f"          water, masked out of the lidar. Total water is probably")
        print(f"          nearer {wet_ac + missing:.2f} ac.")

    # ---- elevation bands --------------------------------------------------
    print(f"\nELEVATION BANDS")
    lo, hi = np.nanmin(ft), np.nanmax(ft)
    edges = np.linspace(lo, hi, 9)
    print(f"        {'band (ft)':>16} {'acres':>7} {'%':>5}")
    for i in range(len(edges) - 1):
        m = good & (ft >= edges[i]) & (ft < edges[i + 1] if i < len(edges) - 2
                                       else ft <= edges[i + 1])
        ac = m.sum() * cell_ac
        if ac < 0.01:
            continue
        print(f"        {edges[i]:6.1f}-{edges[i+1]:6.1f} {ac:7.2f} "
              f"{ac/dem_ac*100:5.1f}  {bar(ac/dem_ac)}")

    # ---- flood ------------------------------------------------------------
    fl = pe.flood_zones(poly, verbose=False)
    if fl.get("flood"):
        print(f"\nFLOOD   {fl['flood']}")
        if fl.get("flood_open") is not None:
            print(f"        {fl['flood_open']:.2f} ac in the largest CONTIGUOUS "
                  f"non-SFHA piece")
        if fl.get("flood_bfe"):
            print(f"        base flood elevation {fl['flood_bfe']} ft -- compare "
                  f"against the bands above")

    # ---- soil -------------------------------------------------------------
    try:
        polys = pe._soil_polygons(poly)
        shares = {}
        for mukey, g in polys:
            inter = poly.intersection(g)
            if inter.is_empty:
                continue
            shares[mukey] = shares.get(mukey, 0.0) + (
                pe.shp_transform(pe._to_utm, inter).area / pe.ACRE_M2)
        if shares:
            keylist = ",".join(f"'{k}'" for k in shares)
            rows = pe._sda(f"""
                SELECT c.mukey, c.compname, c.comppct_r, c.drainagecl,
                       c.hydricrating, m.wtdepannmin
                FROM component c
                LEFT JOIN muaggatt m ON m.mukey = c.mukey
                WHERE c.mukey IN ({keylist})
                ORDER BY c.mukey, c.comppct_r DESC""")
            dom = {}
            for r in rows[1:] if len(rows) > 1 else []:
                mk, nm, pct, dr, hy, wt = (list(r) + [None]*6)[:6]
                dom.setdefault(str(mk), (nm, dr, hy, pe._sda_num(wt)))
            print(f"\nSOIL    {'name':22} {'acres':>7} {'%':>5} {'drainage':24} "
                  f"{'hyd':4} {'wt_in':>5}")
            for mk, ac in sorted(shares.items(), key=lambda kv: -kv[1]):
                nm, dr, hy, wt = dom.get(mk, (mk, "?", "?", None))
                print(f"        {str(nm)[:22]:22} {ac:7.2f} {ac/acres*100:5.1f} "
                      f"{str(dr)[:24]:24} {str(hy):4} "
                      f"{wt if wt is not None else float('nan'):5.0f}")
    except Exception as e:
        print(f"\nSOIL    lookup failed: {type(e).__name__}")

    # ---- canopy -----------------------------------------------------------
    cp = pe.canopy_pct(poly)
    if cp.get("canopy_pct") is not None:
        print(f"\nCANOPY  {cp['canopy_pct']:.0f}% tree cover (NLCD, 30 m, 2021)")

    # ---- discrete pieces of dry land --------------------------------------
    # "50 acres" says nothing when most of it is water. Split the dry ground
    # into connected pieces and measure each: the peninsula becomes a number,
    # the far bank becomes a number, and an isolated corner you cannot reach
    # without a boat is visibly separate.
    #
    # Shape matters as much as area. A piece 900 ft long and 60 ft wide is a
    # peninsula; the same acreage in a blob is a building site. Reported as
    # length (longest span), width (widest circle that fits) and the ratio.
    dry = good & ~wet
    print(f"\nLAND PIECES   contiguous dry ground, largest first")
    if dry.sum():
        dl, dn = ndimage.label(dry)
        dsizes = ndimage.sum(dry, dl, range(1, dn + 1))
        print(f"        {'#':>2} {'acres':>7} {'len_ft':>7} {'wide_ft':>7} "
              f"{'shape':>9}  {'elev ft':>8}  centre")
        shown = 0
        for idx in np.argsort(dsizes)[::-1]:
            m = dl == (idx + 1)
            ac = m.sum() * cell_ac
            if ac < 0.15:                 # ignore slivers
                continue
            ys, xs = np.where(m)
            # longest span across the piece, from its extreme points
            pts = np.column_stack([ys, xs]).astype(float)
            span = 0.0
            if len(pts) > 2:
                from scipy.spatial import ConvexHull
                try:
                    h = pts[ConvexHull(pts).vertices]
                    d2 = ((h[:, None, :] - h[None, :, :]) ** 2).sum(-1)
                    span = float(np.sqrt(d2.max()))
                except Exception:
                    span = float(np.hypot(ys.ptp(), xs.ptp()))
            span_ft = span * cell * M_TO_FT
            edt = ndimage.distance_transform_edt(m) * cell * M_TO_FT
            wide_ft = float(edt.max() * 2)
            ratio = span_ft / wide_ft if wide_ft > 1 else 0
            shape = ("peninsula" if ratio > 6 else
                     "strip" if ratio > 3.5 else "blob")
            wy, wx = np.unravel_index(np.argmax(edt * m), edt.shape)
            plat, plon = rc_to_lonlat(wy, wx, bounds, cell)
            shown += 1
            print(f"        {shown:>2} {ac:7.2f} {span_ft:7.0f} {wide_ft:7.0f} "
                  f"{shape:>9}  {np.nanmean(ft[m]):5.1f}-{np.nanmax(ft[m]):.1f}  "
                  f"{plat:.5f}, {plon:.5f}")
            if shown >= 8:
                break
        if not shown:
            print("        no dry piece larger than 0.15 ac")
    else:
        print("        none -- the whole measured parcel reads as water")

    # ---- pads -------------------------------------------------------------
    ok = np.isfinite(s) & (s <= PAD_SLOPE_DEG) & ~wet
    print(f"\nPADS    dry, <= {PAD_SLOPE_DEG:g} deg, >= {PAD_MIN_FT:.0f} ft across")
    if ok.sum():
        lbl, n = ndimage.label(ok)
        found = 0
        sizes = ndimage.sum(ok, lbl, range(1, n + 1))
        for idx in np.argsort(sizes)[::-1]:
            m = lbl == (idx + 1)
            edt = ndimage.distance_transform_edt(m) * cell * M_TO_FT
            width = float(edt.max() * 2)
            if width < PAD_MIN_FT:
                continue
            wy, wx = np.unravel_index(np.argmax(edt * m), edt.shape)
            plat, plon = rc_to_lonlat(wy, wx, bounds, cell)
            found += 1
            print(f"        #{found}  {m.sum()*cell_ac:6.2f} ac  {width:5.0f} ft wide  "
                  f"{ft[wy,wx]:5.1f} ft   {plat:.5f}, {plon:.5f}")
            if found >= 6:
                break
        if not found:
            print("        none wide enough")
    else:
        print("        none")

    # ---- screening --------------------------------------------------------
    relief = float(np.nanpercentile(ft, 95) - np.nanpercentile(ft, 5))
    print(f"\nSCREEN  parcel relief {relief:.1f} ft")
    if relief < SCREENING_RELIEF_FT:
        print(f"        Below {SCREENING_RELIEF_FT:g} ft the landform screens nothing.")
        print(f"        Privacy here depends entirely on tree cover.")
    else:
        print(f"        Enough relief that ground can hide one building from")
        print(f"        another. Worth running site_model.py for sightlines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
