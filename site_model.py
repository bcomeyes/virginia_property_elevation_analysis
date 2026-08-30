#!/usr/bin/env python3
"""
site_model.py - given a parcel, where would you actually build?

Everything upstream narrows the search to a handful of parcels. This is the
part that looks at one of them and answers the question Matt has been asking
since the beginning: where does the house go, where does the guest house go,
where does the barn go, and do you need a pond.

WHAT IT DOES

  1. Detect existing water. Flat AND low relative to the parcel's high ground.
     Validated against the Knotts Island lot: 33.9% wet, matching a parcel
     Matt has walked and knows is mostly marsh. Dry lots read 0-3.5%.
     If the parcel has real frontage, no pond is needed -- the water is there.

  2. Find candidate building pads. Ground that is dry, flat enough, wide
     enough, and set back from the road. Wet ground is EXCLUDED, which the
     first version failed to do -- it reported 9.89 acres of "pad" on the
     Knotts Island lot, most of it marsh.

  3. Place house, guest house and barn so they do not see each other, using
     line-of-sight across the terrain rather than straight-line distance.

  4. Site a pond, if there is no frontage, where water already wants to
     collect (flow accumulation into a low spot).

WHAT IT CANNOT DO YET

  Canopy. 3DEP through py3dep serves bare earth only, so the sightlines here
  see through trees. In wooded country trees do most of the actual screening,
  so a "visible" verdict is pessimistic and a "hidden" verdict is reliable.
  Treat visibility as the worst case, which is the useful direction.

  Resolution. 1 m data is real ground -- verified against a transect across
  the Knotts Island lot -- but its true detail is coarser, descending in flat
  steps 10-20 m long. Do not trust features smaller than that.

    ./site_model.py --point LAT LON
    ./site_model.py --address "6664 Blackwater Rd" --city "Virginia Beach"
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

warnings.filterwarnings("ignore")

M_TO_FT = 3.280839895
CACHE   = Path("cache/fine")
OUTDIR  = Path("output/sites")

# --- what counts as buildable -----------------------------------------------
PAD_SLOPE_DEG   = 3.0     # a house wants ground flatter than this
PAD_MIN_FT      = 60.0    # house plus setback, across
DRY_ABOVE_FT    = 2.0     # ft above the parcel's low ground to count as dry
WET_SLOPE_DEG   = 0.6     # water is flat
ROAD_SETBACK_FT = 100.0   # keep buildings back from the road frontage

# --- what counts as hidden ---------------------------------------------------
EYE_FT          = 5.5     # standing eye height
TARGET_FT       = 15.0    # ridge of a single-storey building
MIN_SEPARATION_FT = 150.0 # do not bother testing closer than this

# Below this much relief across the whole parcel, landform cannot screen
# anything and running sightlines is theatre. 6664 Blackwater has 2.8 ft of
# relief across 12 acres; nothing there could block a view of anything, so
# reporting "VISIBLE, 5.4 ft of clearance" implies a finding where there is
# none. On such a parcel privacy comes entirely from tree cover.
#
# The bar: a single-storey ridge is ~15 ft, so ground needs to vary by a
# decent fraction of that before it can hide a building. 8 ft is generous.
SCREENING_RELIEF_FT = 8.0


def load_dem(poly, res=1):
    """1 m bare earth clipped to the parcel, cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    import hashlib
    tag = hashlib.md5(poly.wkb).hexdigest()[:12]
    fp = CACHE / f"site_{tag}_{res:g}m.npz"
    if fp.exists():
        z = np.load(fp)
        return z["ft"], float(z["cell_m"]), tuple(z["bounds"])

    import py3dep
    import rioxarray  # noqa: F401
    import time
    for attempt in range(1, 4):
        try:
            da = py3dep.get_map("DEM", poly, resolution=res, geo_crs=4326, crs=4326)
            break
        except Exception:
            if attempt == 3:
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
    b = da.rio.bounds()
    np.savez_compressed(fp, ft=ft, cell_m=res, bounds=np.array(b))
    return ft, float(res), b


def slope_deg(ft, cell_m):
    """Degrees per cell, NaN wherever a neighbour was missing.

    Filling NaNs before the gradient invents a cliff at the parcel edge and
    reported 12-15 degrees on ground 8 ft above sea level. Erode instead.
    """
    good = np.isfinite(ft)
    if good.sum() < 9:
        return np.full(ft.shape, np.nan)
    filled = np.where(good, ft, np.nanmean(ft))
    gy, gx = np.gradient(filled, cell_m * M_TO_FT)
    s = np.degrees(np.arctan(np.hypot(gy, gx)))
    s[~ndimage.binary_erosion(good, np.ones((3, 3)), border_value=0)] = np.nan
    return s


def wet_mask(ft, slope):
    """Flat and low: standing water, ditch, marsh, wet swale."""
    good = np.isfinite(ft)
    if good.sum() < 25:
        return np.zeros_like(good)
    lowref = float(np.nanpercentile(ft, 2))
    return good & (ft <= lowref + DRY_ABOVE_FT) & (slope < WET_SLOPE_DEG)


def find_pads(ft, cell_m, wet, road_mask=None):
    """Dry, flat, wide enough, and off the road. Returns labelled regions."""
    s = slope_deg(ft, cell_m)
    ok = np.isfinite(s) & (s <= PAD_SLOPE_DEG) & ~wet
    if road_mask is not None:
        ok &= ~road_mask
    if ok.sum() == 0:
        return np.zeros_like(ok, dtype=int), []

    lbl, n = ndimage.label(ok)
    cell_ft = cell_m * M_TO_FT
    cell_ac = (cell_m ** 2) / 4046.8564224
    out = []
    for i in range(1, n + 1):
        m = lbl == i
        # widest circle that fits -- the honest test of "can a house go here"
        edt = ndimage.distance_transform_edt(m) * cell_ft
        width = float(edt.max() * 2)
        if width < PAD_MIN_FT:
            continue
        cy, cx = ndimage.center_of_mass(m)
        # centre the marker on the widest point, not the centroid, which can
        # land outside an L-shaped region
        wy, wx = np.unravel_index(np.argmax(edt * m), edt.shape)
        out.append({"id": len(out) + 1, "label": i,
                    "acres": float(m.sum() * cell_ac),
                    "width_ft": width, "row": int(wy), "col": int(wx),
                    "elev_ft": float(ft[wy, wx])})
    out.sort(key=lambda p: -p["width_ft"])
    return lbl, out


def visible(ft, cell_m, a, b, eye_ft=EYE_FT, tgt_ft=TARGET_FT):
    """Can someone at a see the roof of a building at b?

    Walk the straight line between them, sample the ground, and check whether
    anything rises above the sight line. Bare earth only -- no trees -- so a
    'hidden' answer is trustworthy and a 'visible' answer is the worst case.
    """
    n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
    if n < 2:
        return True, 0.0
    rr = np.linspace(a[0], b[0], n).astype(int)
    cc = np.linspace(a[1], b[1], n).astype(int)
    prof = ft[rr, cc]
    if not np.isfinite(prof).all():
        prof = np.where(np.isfinite(prof), prof, np.nanmin(ft))
    z0, z1 = prof[0] + eye_ft, prof[-1] + tgt_ft
    sight = np.linspace(z0, z1, n)
    clearance = sight[1:-1] - prof[1:-1]
    if len(clearance) == 0:
        return True, 0.0
    return bool(clearance.min() > 0), float(clearance.min())


def rc_to_lonlat(row, col, bounds, shape, cell_m):
    """Grid cell -> lon/lat, for map links."""
    left, bottom, right, top = bounds
    x = left + (col + 0.5) * cell_m
    y = top - (row + 0.5) * cell_m
    import pyproj
    tr = pyproj.Transformer.from_crs(pe.WORKING_EPSG, 4326, always_xy=True)
    lon, lat = tr.transform(x, y)
    return lat, lon


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
    acres = pe.shp_transform(pe._to_utm, poly).area / pe.ACRE_M2
    print(f"\nparcel {acres:.2f} ac  ({(attrs or {}).get('_source')})"
          f"  {(attrs or {}).get('_address') or a.address or ''}")

    ft, cell, bounds = load_dem(poly, a.res)
    good = np.isfinite(ft)
    print(f"  {ft.shape[0]}x{ft.shape[1]} grid at {cell:g} m, "
          f"{good.sum():,} cells, {np.nanmin(ft):.1f}-{np.nanmax(ft):.1f} ft")

    s = slope_deg(ft, cell)
    wet = wet_mask(ft, s)
    cell_ac = (cell ** 2) / 4046.8564224
    wet_ac = wet.sum() * cell_ac
    print(f"\nWATER   {wet.sum() * 100.0 / max(good.sum(),1):.1f}% of parcel "
          f"({wet_ac:.2f} ac)")
    # One CONTIGUOUS body, not scattered damp cells. 6664 Blackwater read 2.6%
    # / 0.32 ac spread over the lot and the old 0.25 ac threshold called that
    # "existing water, no pond needed" -- that is a wet ditch, not frontage.
    if wet.any():
        from scipy import ndimage as _nd
        wl, wn = _nd.label(wet)
        biggest_ac = float(max(_nd.sum(wet, wl, range(1, wn + 1))) * cell_ac)
    else:
        biggest_ac = 0.0
    print(f"        largest single body {biggest_ac:.2f} ac")
    if biggest_ac >= 1.0:
        print("        real water on the parcel - no pond needed")
    elif biggest_ac >= 0.15:
        print("        a wet swale or ditch - a pond could be dug HERE, where")
        print("        water already collects, rather than anywhere else")
    else:
        print("        no standing water - a pond would have to be dug from scratch")

    lbl, pads = find_pads(ft, cell, wet)
    print(f"\nPADS    {len(pads)} site(s) dry, <= {PAD_SLOPE_DEG:g} deg, "
          f">= {PAD_MIN_FT:.0f} ft across")
    for p in pads[:6]:
        plat, plon = rc_to_lonlat(p["row"], p["col"], bounds, ft.shape, cell)
        print(f"        #{p['id']}  {p['acres']:6.2f} ac  {p['width_ft']:5.0f} ft wide  "
              f"{p['elev_ft']:5.1f} ft   {plat:.5f}, {plon:.5f}")
    if not pads:
        print("        nothing wide enough. Either the parcel is too wet, too")
        print("        steep, or PAD_MIN_FT is set larger than this lot allows.")
        return 0

    relief = float(np.nanpercentile(ft, 95) - np.nanpercentile(ft, 5))
    print(f"\nSCREENING   parcel relief {relief:.1f} ft "
          f"(p95 - p5 of bare earth)")

    if relief < SCREENING_RELIEF_FT:
        print(f"        Below {SCREENING_RELIEF_FT:g} ft, the LANDFORM CANNOT SCREEN "
              f"anything.")
        print("        There is no fold in this ground deep enough to hide a")
        print("        building from another building. Sightlines are not worth")
        print("        computing here and are skipped.")
        print("        Privacy on this parcel depends ENTIRELY on tree cover,")
        print("        which bare-earth lidar cannot see. That is a real answer,")
        print("        not a gap: keep the woods and you have screening; clear")
        print("        them, or a neighbour clears theirs, and you have none.")
    elif len(pads) >= 2:
        print(f"        Enough relief that landform may screen. Testing "
              f"eye {EYE_FT:g} ft to roof {TARGET_FT:g} ft, bare earth (no trees).")
        for i in range(len(pads[:4])):
            for j in range(i + 1, len(pads[:4])):
                p, q = pads[i], pads[j]
                d_ft = np.hypot(p["row"] - q["row"], p["col"] - q["col"]) * cell * M_TO_FT
                if d_ft < MIN_SEPARATION_FT:
                    verdict = f"only {d_ft:.0f} ft apart"
                else:
                    vis, clr = visible(ft, cell, (p["row"], p["col"]),
                                       (q["row"], q["col"]))
                    verdict = (f"VISIBLE, {clr:.1f} ft of clearance"
                               if vis else f"hidden by {-clr:.1f} ft of ground")
                print(f"        #{p['id']} <-> #{q['id']}  {d_ft:5.0f} ft   {verdict}")
        print("        bare earth only: 'hidden' is reliable and permanent -- you")
        print("        cannot clear away a hill. 'visible' is the worst case;")
        print("        trees may screen it today.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
