#!/usr/bin/env python3
"""
above_bfe.py - how many acres sit above the base flood elevation, and where.

The red contour in sketch.py's elevation panel separates ground above the FEMA
base flood elevation from ground below it. That line encloses several separate
patches, and this measures each one.

Not the same as the pads. A pad must also be FLAT and WIDE enough to build on,
so pads are a subset. This is the broader question: which ground would not need
the structure elevated.

    ./above_bfe.py --point 36.88694 -76.52842
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
CACHE = Path("cache/fine")


def load_dem(poly, res=1):
    import hashlib
    tag = hashlib.md5(poly.wkb).hexdigest()[:12]
    fp = CACHE / f"dd_{tag}_{res:g}m.npz"
    if fp.exists():
        z = np.load(fp)
        return z["ft"], float(z["cell_m"]), tuple(z["bounds"])
    sys.exit("no cached DEM -- run deep_dive.py on this parcel first")


def rc_to_lonlat(row, col, bounds, cell_m):
    left, bottom, right, top = bounds
    import pyproj
    tr = pyproj.Transformer.from_crs(pe.WORKING_EPSG, 4326, always_xy=True)
    lon, lat = tr.transform(left + (col + .5) * cell_m, top - (row + .5) * cell_m)
    return lat, lon


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--point", nargs=2, type=float, metavar=("LAT", "LON"))
    ap.add_argument("--address")
    ap.add_argument("--city", default="Virginia Beach")
    ap.add_argument("--level", type=float, default=None,
                    help="ft; defaults to the FEMA base flood elevation")
    a = ap.parse_args()

    if a.address:
        lat, lon = lw.geocode(a.address, a.city, "VA", None)
    else:
        lat, lon = a.point
    poly, _ = pe.get_parcel_polygon(lat, lon)
    if poly is None:
        sys.exit("no parcel")
    acres = pe.shp_transform(pe._to_utm, poly).area / pe.ACRE_M2

    ft, cell, bounds = load_dem(poly)
    good = np.isfinite(ft)
    cell_ac = (cell ** 2) / 4046.8564224
    wet, _ = water_mask(ft, cell)

    fl = pe.flood_zones(poly, verbose=False)
    level = a.level if a.level is not None else fl.get("flood_bfe")
    if level is None:
        sys.exit("no base flood elevation published; pass --level FT")

    high = good & (ft > float(level)) & ~wet
    tot = high.sum() * cell_ac
    print(f"\nparcel {acres:.2f} ac   {fl.get('flood','')}")
    print(f"base flood elevation {level} ft\n")
    print(f"{tot:.2f} ac above {level} ft and not water "
          f"({tot/acres*100:.0f}% of the parcel)\n")

    lbl, n = ndimage.label(high)
    sizes = ndimage.sum(high, lbl, range(1, n + 1))
    print(f"{'#':>2} {'acres':>7} {'wide_ft':>8} {'peak_ft':>8}  centre")
    print("-" * 56)
    shown = 0
    for idx in np.argsort(sizes)[::-1]:
        m = lbl == (idx + 1)
        ac = m.sum() * cell_ac
        if ac < 0.1:
            continue
        edt = ndimage.distance_transform_edt(m) * cell * M_TO_FT
        wy, wx = np.unravel_index(np.argmax(edt * m), edt.shape)
        plat, plon = rc_to_lonlat(wy, wx, bounds, cell)
        shown += 1
        print(f"{shown:>2} {ac:7.2f} {edt.max()*2:8.0f} "
              f"{np.nanmax(ft[m]):8.1f}  {plat:.5f}, {plon:.5f}")
        if shown >= 12:
            break
    if not shown:
        print("  no patch above the flood elevation larger than 0.1 ac")
    return 0


if __name__ == "__main__":
    sys.exit(main())
