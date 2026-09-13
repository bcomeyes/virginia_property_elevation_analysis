#!/usr/bin/env python3
"""
sketch.py - draw one parcel, so the numbers become a shape.

A land budget in acres tells you how much of what. It does not tell you where,
and where is what decides whether a 20-acre pad is a building site or a strip
you cannot use.

This renders four panels for a single parcel:

  1. HILLSHADE   simulated low sun over the bare earth. Your eye reads shading
                 as topography instantly -- it is a flat picture that looks 3D.
                 Vertical exaggeration is applied and LABELLED, because on
                 ground with 15 ft of relief across 50 acres an honest render
                 is featureless.
  2. ELEVATION   coloured bands with the FEMA base flood elevation drawn as a
                 contour, so you can see exactly which ground is above it.
  3. WET / DRY   the water mask and the soil drainage classes.
  4. PADS        buildable ground, numbered to match deep_dive.py's table.

    ./sketch.py --point 36.88694 -76.52842
    ./sketch.py --address "6664 Blackwater Rd"
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
OUTDIR  = Path("output/sketches")

PAD_SLOPE_DEG = 3.0
PAD_MIN_FT    = 60.0
DRY_ABOVE_FT  = 2.0
WET_SLOPE_DEG = 0.6


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
    good = np.isfinite(ft)
    if good.sum() < 9:
        return np.full(ft.shape, np.nan)
    filled = np.where(good, ft, np.nanmean(ft))
    gy, gx = np.gradient(filled, cell_m * M_TO_FT)
    s = np.degrees(np.arctan(np.hypot(gy, gx)))
    s[~ndimage.binary_erosion(good, np.ones((3, 3)), border_value=0)] = np.nan
    return s


def hillshade(ft, cell_m, az=315.0, alt=35.0, z=8.0):
    """Grey relief. z exaggerates vertically -- on 15 ft of relief across 50
    acres a true-scale hillshade is a blank grey square."""
    filled = np.where(np.isfinite(ft), ft, np.nanmean(ft))
    gy, gx = np.gradient(filled * z, cell_m * M_TO_FT)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    azr, altr = np.radians(360 - az + 90), np.radians(alt)
    hs = (np.sin(altr) * np.sin(slope) +
          np.cos(altr) * np.cos(slope) * np.cos(azr - aspect))
    hs[~np.isfinite(ft)] = np.nan
    return hs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--point", nargs=2, type=float, metavar=("LAT", "LON"))
    ap.add_argument("--address")
    ap.add_argument("--city", default="Virginia Beach")
    ap.add_argument("--state", default="VA")
    ap.add_argument("--zip", default=None)
    ap.add_argument("--res", type=float, default=1.0)
    ap.add_argument("--exag", type=float, default=8.0,
                    help="vertical exaggeration for the hillshade")
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
    label = a.address or f"{lat:.5f}, {lon:.5f}"
    print(f"{label}  {acres:.2f} ac")

    ft, cell, bounds = load_dem(poly, a.res)
    good = np.isfinite(ft)
    cell_ac = (cell ** 2) / 4046.8564224
    s = slope_deg(ft, cell)
    # Water by FLATNESS, not elevation. The old test -- flat and within 2 ft
    # of the parcel's 2nd percentile -- reported this parcel's 26-acre pond as
    # a 20-acre BUILDING PAD, because the percentile anchored on tidal water
    # six feet lower. See water_detect.py.
    wet, water_bodies = water_mask(ft, cell)

    fl = pe.flood_zones(poly, verbose=False)
    bfe = fl.get("flood_bfe")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    fig, axes = plt.subplots(2, 2, figsize=(15, 14))
    extent = [0, ft.shape[1] * cell * M_TO_FT, 0, ft.shape[0] * cell * M_TO_FT]

    # 1. hillshade
    ax = axes[0][0]
    ax.imshow(hillshade(ft, cell, z=a.exag), cmap="gray", extent=extent,
              origin="upper")
    ax.set_title(f"relief, {a.exag:g}x vertical exaggeration\n"
                 f"(true scale would look flat: "
                 f"{np.nanpercentile(ft,95)-np.nanpercentile(ft,5):.1f} ft "
                 f"across {acres:.0f} ac)", fontsize=10)

    # 2. elevation with the base flood elevation drawn on
    ax = axes[0][1]
    im = ax.imshow(ft, cmap="terrain", extent=extent, origin="upper")
    plt.colorbar(im, ax=ax, label="ft", fraction=0.046)
    if bfe:
        try:
            ax.contour(np.flipud(ft), levels=[float(bfe)], colors="red",
                       linewidths=2, extent=extent)
            ax.set_title(f"elevation -- RED = base flood elevation {bfe} ft\n"
                         f"ground inside the red line is BELOW it", fontsize=10)
        except Exception:
            ax.set_title("elevation (ft)", fontsize=10)
    else:
        ax.set_title("elevation (ft)", fontsize=10)

    # 3. wet / dry
    ax = axes[1][0]
    cat = np.full(ft.shape, np.nan)
    cat[good] = 0
    cat[wet] = 1
    ax.imshow(cat, cmap=ListedColormap(["#d9c9a3", "#3b7dd8"]),
              extent=extent, origin="upper", vmin=0, vmax=1)
    wet_ac = wet.sum() * cell_ac
    detail = ", ".join(f"{b['acres']:.1f} ac @ {b['elev_ft']:.0f} ft"
                       for b in water_bodies[:3])
    ax.set_title(f"water, detected by flatness (any elevation)\n"
                 f"{wet_ac:.2f} ac total -- {detail}", fontsize=10)

    # 4. pads
    ax = axes[1][1]
    ok = np.isfinite(s) & (s <= PAD_SLOPE_DEG) & ~wet
    base = np.full(ft.shape, np.nan)
    base[good] = 0
    ax.imshow(base, cmap=ListedColormap(["#e8e8e8"]), extent=extent,
              origin="upper", vmin=0, vmax=1)
    n_pads = 0
    if ok.sum():
        lbl, n = ndimage.label(ok)
        sizes = ndimage.sum(ok, lbl, range(1, n + 1))
        show = np.zeros_like(ft)
        show[:] = np.nan
        for idx in np.argsort(sizes)[::-1]:
            m = lbl == (idx + 1)
            edt = ndimage.distance_transform_edt(m) * cell * M_TO_FT
            if edt.max() * 2 < PAD_MIN_FT:
                continue
            n_pads += 1
            show[m] = n_pads
            wy, wx = np.unravel_index(np.argmax(edt * m), edt.shape)
            ax.annotate(f"{n_pads}",
                        ((wx + 0.5) * cell * M_TO_FT,
                         (ft.shape[0] - wy - 0.5) * cell * M_TO_FT),
                        color="black", fontsize=13, fontweight="bold",
                        ha="center", va="center",
                        bbox=dict(boxstyle="circle", fc="yellow", alpha=0.85))
            if n_pads >= 8:
                break
        ax.imshow(show, cmap="tab10", extent=extent, origin="upper",
                  alpha=0.65, vmin=1, vmax=10)
    ax.set_title(f"buildable: dry, <= {PAD_SLOPE_DEG:g} deg, "
                 f">= {PAD_MIN_FT:.0f} ft wide\n{n_pads} pad(s), "
                 f"numbered as in deep_dive.py", fontsize=10)

    for row in axes:
        for ax in row:
            ax.set_xlabel("feet")
            ax.set_ylabel("feet")

    fig.suptitle(f"{label}   {acres:.2f} acres   {fl.get('flood','')}",
                 fontsize=13)
    fig.tight_layout()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in label)[:40]
    out = OUTDIR / f"{safe}.png"
    fig.savefig(out, dpi=130)
    print(f"  -> {out}")
    print("\n  Everything here is BARE EARTH. Trees are not in this data, so the")
    print("  hillshade shows ground, not what you would see standing on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
