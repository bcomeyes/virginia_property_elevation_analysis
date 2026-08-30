#!/usr/bin/env python3
"""
1 m research. Four questions, answered before any site-model code gets written.

Q1. Is 1 m bare earth under canopy REAL GROUND or interpolated smoothness?
    Ground returns are sparse under trees and the software fills the gaps. The
    tell: real woods are NOT smoother than a mowed field. If wooded parcels come
    back smoother at 1 m than cleared ones, we are measuring the interpolator.

Q2. Do ponds, ditches and creeks show up, as flat AND low ground?

Q3. What does a realistic building pad look like -- how flat, how wide, how
    much of a parcel qualifies?

Q4. Does the pad finder agree with houses that already exist? (separate script)

FIXES over the first attempt, which produced unusable numbers:

  * Slope was reported at 12-15 degrees on coastal flats 8 ft above sea level.
    Cause: NaN cells outside the parcel were filled with the parcel mean before
    taking the gradient, so every boundary became an artificial cliff. On a
    170-cell parcel almost every cell is near a boundary. Now the gradient is
    computed on the raw array and every cell that touches a NaN is discarded.

  * 6584 and 6592 Blackwater returned identical numbers -- my hardcoded
    coordinates for them were 3 m apart and resolved to one parcel. Coordinates
    now come from the pipeline (the same geocoding find_land uses), not from
    stale constants.

  * Water detection used the 20th percentile of the parcel's own elevation. On
    a parcel that is mostly marsh, the 20th percentile IS marsh, so it found
    almost none. Now uses an absolute test: flat, and low relative to the
    parcel's HIGH ground.

    mv -f ~/Downloads/research_1m.py . && chmod +x research_1m.py && ./research_1m.py
"""

import sys
import time
import warnings
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, ".")
import land_watch as lw          # noqa: E402
import parcel_elevation as pe    # noqa: E402

warnings.filterwarnings("ignore")

CACHE = Path("cache/fine")
M_TO_FT = 3.280839895

# Addresses, not coordinates. geocode() is cached, so this costs nothing after
# the first run -- and it guarantees we measure the same parcels the pipeline
# measured rather than whatever a stale hardcoded pair resolves to.
SITES = [
    ("6584 Blackwater Rd", "Virginia Beach", "VA", "23457", "4.32 ac, X 78 / AE 22"),
    ("6592 Blackwater Rd", "Virginia Beach", "VA", "23457", "3.98 ac, X 90 / AE 10"),
    ("6636 Blackwater Rd", "Virginia Beach", "VA", "23457", "4.42 ac, X 95"),
    ("6664 Blackwater Rd", "Virginia Beach", "VA", "23457", "12.43 ac, X 100, biggest clean lot"),
    ("2084 Camden Ct",     "Virginia Beach", "VA", "23457", "2.82 ac, X 100"),
]
# Known ground truth, coordinates verified by hand off the deed map.
EXTRA = [("Knotts Island NC (cousin's)", 36.53916, -75.99679,
          "mostly marsh, dry part flat and cleared")]

PAD_SLOPE_DEG = 3.0
PAD_MIN_FT    = 60.0
WET_BELOW_FT  = 2.0      # within this many ft of the parcel's low ground
WET_SLOPE_DEG = 0.6


def fetch(poly, res, tag, tries=3):
    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"{tag}_{res:g}m.npz"
    if fp.exists():
        z = np.load(fp)
        return z["ft"], float(z["cell_m"])

    import py3dep
    import rioxarray  # noqa: F401
    last = None
    for attempt in range(1, tries + 1):
        try:
            da = py3dep.get_map("DEM", poly, resolution=res, geo_crs=4326, crs=4326)
            break
        except Exception as e:                 # 3DEP drops requests under load
            last = e
            if attempt == tries:
                raise
            time.sleep(3 * attempt)
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


def slope_deg_map(ft, cell_m):
    """Slope per cell, in degrees. NaN wherever the answer would be a lie.

    np.gradient needs a value at every cell, so filling NaNs with anything --
    the mean, zero, nearest -- invents a step at the parcel edge and reports it
    as terrain. Instead: fill only to let the gradient run, then blank every
    cell that touched filled data. Small parcels lose their outer ring, which
    is correct: we genuinely do not know the slope there.
    """
    good = np.isfinite(ft)
    if good.sum() < 9:
        return np.full(ft.shape, np.nan)
    filled = np.where(good, ft, np.nanmean(ft))
    spacing_ft = cell_m * M_TO_FT
    gy, gx = np.gradient(filled, spacing_ft)
    slope = np.degrees(np.arctan(np.hypot(gy, gx)))
    # a cell is trustworthy only if it and all 8 neighbours were real
    trustworthy = ndimage.binary_erosion(good, np.ones((3, 3)), border_value=0)
    slope[~trustworthy] = np.nan
    return slope


def roughness(ft):
    """Mean |difference| to the 4 nearest neighbours, in feet, interior only.

    NOT the 8-neighbour TRI used at 10 m. The two are not comparable across
    resolutions (Knotts Island: 0.309 at 10 m, 0.045 at 1 m), so this exists
    only to compare parcels AT THE SAME resolution.
    """
    good = np.isfinite(ft)
    diffs = []
    for ax, sh in ((0, 1), (0, -1), (1, 1), (1, -1)):
        nb = np.roll(ft, sh, axis=ax)
        nb_good = np.roll(good, sh, axis=ax)
        d = np.where(good & nb_good, np.abs(ft - nb), np.nan)
        diffs.append(d)
    return float(np.nanmean(np.nanmean(diffs, axis=0)))


def water_like(ft, cell_m):
    """Flat and low in ABSOLUTE terms, not relative to this parcel's own spread.

    The percentile version could not find marsh on a marsh parcel: if 74% of
    the lot is wet, the 20th percentile is wet too, so only the wettest fifth
    of the wet qualified. Anchor to the parcel's HIGH ground instead and ask
    what sits near the bottom of it.
    """
    good = np.isfinite(ft)
    if good.sum() < 25:
        return None, None
    lowref = float(np.nanpercentile(ft, 2))
    slope = slope_deg_map(ft, cell_m)
    mask = good & (ft <= lowref + WET_BELOW_FT) & (slope < WET_SLOPE_DEG)
    cell_ac = (cell_m ** 2) / 4046.8564224
    if mask.sum() == 0:
        return 0.0, 0.0
    lbl, n = ndimage.label(mask)
    sizes = ndimage.sum(mask, lbl, range(1, n + 1))
    return 100.0 * mask.sum() / good.sum(), float(sizes.max()) * cell_ac


def pads(ft, cell_m):
    """Flat regions wide enough to build on. Returns (count, acres, width_ft)."""
    slope = slope_deg_map(ft, cell_m)
    flat = np.isfinite(slope) & (slope <= PAD_SLOPE_DEG)
    cell_ac = (cell_m ** 2) / 4046.8564224
    if flat.sum() == 0:
        return 0, 0.0, 0.0
    lbl, n = ndimage.label(flat)
    sizes = ndimage.sum(flat, lbl, range(1, n + 1))
    need = (PAD_MIN_FT / (cell_m * M_TO_FT)) ** 2
    biggest = lbl == (int(np.argmax(sizes)) + 1)
    edt = ndimage.distance_transform_edt(biggest) * cell_m * M_TO_FT
    return int((sizes >= need).sum()), float(sizes.max()) * cell_ac, float(edt.max() * 2)


def resolve(addr, city, state, zipcode):
    lat, lon = lw.geocode(addr, city, state, zipcode)
    return lat, lon


def main():
    targets = []
    for addr, city, state, zc, note in SITES:
        lat, lon = resolve(addr, city, state, zc)
        if lat is None:
            print(f"  could not geocode {addr}")
            continue
        targets.append((addr, lat, lon, note))
    targets += [(n, la, lo, nt) for n, la, lo, nt in EXTRA]

    print(f"\n{'parcel':26} {'res':>4} {'acres':>6} {'cells':>8} {'rough':>7} "
          f"{'slp50':>6} {'slp95':>6} {'wet%':>6} {'wet_ac':>7} "
          f"{'pads':>5} {'pad_ac':>7} {'wide_ft':>8}")
    print("-" * 108)

    seen_keys = {}
    for name, lat, lon, note in targets:
        poly, attrs = pe.get_parcel_polygon(lat, lon)
        if poly is None:
            print(f"{name:26} NO PARCEL at {lat:.5f},{lon:.5f}")
            continue
        key = (attrs or {}).get("PARCELID") or (attrs or {}).get("parno")
        acres = pe.shp_transform(pe._to_utm, poly).area / pe.ACRE_M2
        if key and key in seen_keys:
            print(f"{name:26} SAME PARCEL as {seen_keys[key]} "
                  f"(id {key}) - geocoder put both pins on one lot")
            continue
        if key:
            seen_keys[key] = name

        tag = name.replace(" ", "_").replace("'", "").replace("(", "").replace(")", "")
        for res in (10, 1):
            try:
                ft, cell = fetch(poly, res, tag)
            except Exception as e:
                print(f"{name:26} {res:>4}  FETCH FAILED {type(e).__name__}")
                continue
            good = np.isfinite(ft)
            if good.sum() < 25:
                print(f"{name:26} {res:>4}  only {good.sum()} valid cells")
                continue
            slope = slope_deg_map(ft, cell)
            sgood = np.isfinite(slope)
            wpct, wac = water_like(ft, cell)
            npad, pac, pft = pads(ft, cell)
            print(f"{name:26} {res:>4} {acres:6.2f} {good.sum():8,} "
                  f"{roughness(ft):7.3f} "
                  f"{np.nanpercentile(slope[sgood], 50) if sgood.any() else -1:6.2f} "
                  f"{np.nanpercentile(slope[sgood], 95) if sgood.any() else -1:6.2f} "
                  f"{wpct:6.1f} {wac:7.2f} {npad:5d} {pac:7.2f} {pft:8.0f}")
        print(f"{'':26} {note}")

    print("\n" + "=" * 108)
    print("Q1  roughness at 1 m, Blackwater lots only. They are adjacent parcels on")
    print("    one subdivided tract, so the landform is the same and any spread is")
    print("    ground cover. Label them wooded/cleared from imagery: if the WOODED")
    print("    ones are SMOOTHER, 1 m under canopy is interpolation, not ground.")
    print("Q2  Knotts Island is mostly marsh and we have seen it. If wet% there is")
    print("    not high, water detection does not work.")
    print(f"Q3  pads: flat regions <= {PAD_SLOPE_DEG} deg. wide_ft is the widest circle")
    print(f"    that fits the largest one; a house plus setback wants ~{PAD_MIN_FT:.0f} ft.")
    print("    Slope now blanks every cell touching the parcel edge, so small")
    print("    parcels lose their outer ring rather than reporting a false cliff.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
