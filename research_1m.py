#!/usr/bin/env python3
"""
1 m research, part 1: what does the ground actually look like at full parcel scale?

Before building a site model we need to know what 1 m data can and cannot tell
us. Five questions, and this answers the first four:

  1. Does a full parcel fetch cleanly at 1 m, or only the 300 m test boxes?
  2. Do the metrics behave? We know the elevation RANGE barely moved between
     10 m and 1 m (Kings Fork 17.2-22.4 vs 16.7-22.3). Whether texture
     separates parcels the same way is unknown, and that decides whether 1 m
     adds information or only resolution.
  3. Is a SURFACE model available anywhere in py3dep? Canopy height is first
     return minus bare earth. 3DEP may be bare-earth only, in which case
     sightlines through woods will always be optimistic and canopy needs a
     different source entirely.
  4. Does water show up? Ponds, ditches and creeks appear as flat low areas --
     or they get lost in interpolation noise. This decides whether "you already
     have water, skip the pond" is computable.

Bare-earth lidar under canopy is interpolated between sparse ground returns, so
1 m in woods may be noise or may be oversmoothed. Looking at the actual numbers
is the only way to find out.

Arrays are cached to cache/fine/ so later work never refetches.

    mv -f ~/Downloads/research_1m.py . && chmod +x research_1m.py && ./research_1m.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
import parcel_elevation as pe    # noqa: E402

warnings.filterwarnings("ignore")

CACHE = Path("cache/fine")
M_TO_FT = 3.280839895

# Parcels with known coordinates and known character, so the numbers mean
# something rather than being abstract.
SITES = [
    ("cousin's lot, Knotts Island NC", 36.53916, -75.99679,
     "flat, cleared, roadside + marsh. 10 m TRI 0.146 -- known negative"),
    ("2281 Kings Fork Rd, Suffolk VA", 36.7852, -76.6103,
     "best texture we measured. 10 m TRI 1.12"),
    ("6584 Blackwater Rd, Virginia Beach", 36.55573, -76.07317,
     "4.32 ac, X 78 / AE 22, 10 m TRI 0.50 -- a real candidate"),
]


def fetch(poly, res, tag):
    """Clip a DEM to the parcel at the given resolution. Cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"{tag}_{res:g}m.npz"
    if fp.exists():
        z = np.load(fp)
        return z["ft"], float(z["cell_m"])

    import py3dep
    import rioxarray  # noqa: F401
    da = py3dep.get_map("DEM", poly, resolution=res, geo_crs=4326, crs=4326)
    da = da.rio.reproject(pe.WORKING_EPSG, resolution=res)
    poly_utm = pe.shp_transform(pe._to_utm, poly)
    da = da.rio.clip([poly_utm], pe.WORKING_EPSG, drop=True)

    arr = da.values.astype("float64")
    if arr.ndim == 3:
        arr = arr[0]
    nod = da.rio.nodata
    if nod is not None:
        arr[arr == nod] = np.nan
    arr[arr < -100] = np.nan
    ft = arr * M_TO_FT
    np.savez_compressed(fp, ft=ft, cell_m=res)
    return ft, float(res)


def water_guess(ft, cell_m):
    """Flat low ground: candidate standing water, ditch or wet swale.

    Not a water layer -- just 'unusually flat and unusually low'. If real ponds
    show up this way, pond siting is computable. If everything looks like this,
    the signal is too weak.
    """
    good = np.isfinite(ft)
    if good.sum() < 50:
        return None
    lo = np.nanpercentile(ft, 15)
    gy, gx = np.gradient(np.nan_to_num(ft, nan=np.nanmean(ft)), cell_m)
    slope = np.hypot(gy, gx)
    flat_low = good & (ft <= lo) & (slope < 0.02)
    return 100.0 * flat_low.sum() / good.sum()


def main():
    # ---- surface model availability ---------------------------------------
    print("=" * 78)
    print("Q3. Is a SURFACE (first-return) model available in py3dep?")
    print("=" * 78)
    try:
        import py3dep
        layers = getattr(py3dep, "LAYERS", None) or getattr(py3dep, "__all__", [])
        print(f"  py3dep {getattr(py3dep, '__version__', '?')}")
        if isinstance(layers, (list, tuple, set)):
            print(f"  advertised layers: {sorted(layers)}")
        surface = [l for l in (layers or [])
                   if any(w in str(l).lower()
                          for w in ("dsm", "surface", "first", "canopy", "height"))]
        print(f"  surface-like layers: {surface or 'NONE'}")
        if not surface:
            print("  => 3DEP through py3dep looks BARE-EARTH ONLY.")
            print("     Canopy height needs another source, and sightlines")
            print("     through woods will be optimistic until it exists.")
    except Exception as e:
        print(f"  could not inspect: {type(e).__name__}: {e}")

    # ---- per parcel --------------------------------------------------------
    for name, lat, lon, note in SITES:
        print("\n" + "=" * 78)
        print(f"{name}")
        print(f"  {note}")
        print("=" * 78)

        poly, attrs = pe.get_parcel_polygon(lat, lon)
        if poly is None:
            print("  no parcel found -- skipping")
            continue
        poly_utm = pe.shp_transform(pe._to_utm, poly)
        acres = poly_utm.area / pe.ACRE_M2
        print(f"  parcel {acres:.2f} ac from {(attrs or {}).get('_source')}")

        tag = name.split(",")[0].replace(" ", "_").replace("'", "")[:24]
        rows = []
        for res in (10, 1):
            try:
                ft, cell = fetch(poly, res, tag)
            except Exception as e:
                print(f"  {res} m FETCH FAILED: {type(e).__name__}: {str(e)[:70]}")
                continue
            good = np.isfinite(ft)
            if good.sum() < 9:
                print(f"  {res} m: only {good.sum()} valid cells")
                continue
            m = pe.terrain_metrics(ft, cell)
            wet = water_guess(ft, cell)
            rows.append((res, ft, good.sum(), m, wet))
            print(f"\n  {res:>2} m  {ft.shape[0]}x{ft.shape[1]} grid, "
                  f"{good.sum():,} valid cells")
            print(f"        elev {np.nanmin(ft):.1f} to {np.nanmax(ft):.1f} ft"
                  f"   p5 {np.nanpercentile(ft,5):.1f}  "
                  f"p50 {np.nanpercentile(ft,50):.1f}  "
                  f"p95 {np.nanpercentile(ft,95):.1f}")
            print(f"        relief {m.get('relief_ft')}   std {m.get('std_ft')}   "
                  f"slope {m.get('slope_deg')}   TRI {m.get('tri_ft')}")
            print(f"        flat+low (water guess): {wet:.1f}% of parcel")

        if len(rows) == 2:
            (_, _, _, m10, w10), (_, _, _, m1, w1) = rows
            print("\n  10 m -> 1 m")
            for k in ("relief_ft", "std_ft", "slope_deg", "tri_ft"):
                a, b = m10.get(k), m1.get(k)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    chg = "same" if a == 0 else f"{(b - a) / a * 100:+.0f}%"
                    print(f"    {k:10} {a:8.3f} -> {b:8.3f}   {chg}")
            print(f"    {'water%':10} {w10:8.1f} -> {w1:8.1f}")

    print("\n" + "=" * 78)
    print("What to look for:")
    print("  TRI rising a lot at 1 m in WOODED parcels but not cleared ones")
    print("  means we are measuring interpolation noise, not ground.")
    print("  If the ORDER of parcels by TRI changes, 1 m says something new.")
    print("  If the order holds, 1 m is for siting only, not for ranking.")
    print(f"\n  arrays cached in {CACHE}/ - later work will not refetch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
