#!/usr/bin/env python3
"""
Standardise the Virginia DEM tiles to exactly 10.0 m cells.

Why:
  The VA notebook ordered 10 m from 3DEP but reprojected without pinning the
  output cell size, so GDAL picked one from the extent. Result:

      Suffolk         7.7857 m
      Chesapeake      7.9801 m
      Virginia_Beach  9.5812 m
      (every NC tile) 10.0000 m

  Terrain metrics -- relief, std, and especially TRI -- are all sensitive to
  cell size. TRI is literally the mean absolute difference between a cell and
  its neighbours, so a smaller cell samples a tighter footprint and returns a
  systematically different number. Comparing a Suffolk parcel to a Currituck
  parcel today would be comparing measurements taken with different rulers.

  The VA tiles are NOT finer data -- 3DEP was asked for 10 m and delivered it.
  The sub-10 m grid is an artefact of reprojection, so resampling to 10 m
  discards nothing that was ever measured.

Safety:
  Originals are MOVED to data/dem_native/ , not deleted. Nothing is destroyed,
  and they leave data/dem/ so pick_dem() can't match both copies of a locality.

Run:  python3 standardize_va_dem.py
      python3 standardize_va_dem.py --dry-run     (show the plan, change nothing)
"""

import shutil
import sys
from pathlib import Path

import rasterio
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject

DEM_DIR = Path("data/dem")
NATIVE_DIR = Path("data/dem_native")
TARGET_RES = 10.0
WORKING_EPSG = 32618
TOL = 0.01

# The three tiles written by the VA notebook (no _10m suffix).
VA_TILES = ["Suffolk", "Chesapeake", "Virginia_Beach"]

DRY = "--dry-run" in sys.argv


def needs_work(path):
    with rasterio.open(path) as src:
        cw = abs(src.transform.a)
        return abs(cw - TARGET_RES) > TOL, cw


def resample(src_path, dst_path):
    """Reproject/resample onto an exact 10 m grid, bilinear (continuous data)."""
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, src.crs, src.width, src.height, *src.bounds,
            resolution=(TARGET_RES, TARGET_RES),
        )
        profile = src.profile.copy()
        profile.update(transform=transform, width=width, height=height,
                       compress="deflate")

        with rasterio.open(dst_path, "w", **profile) as dst:
            for band in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, band),
                    destination=rasterio.band(dst, band),
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=src.crs,
                    src_nodata=src.nodata, dst_nodata=src.nodata,
                    resampling=Resampling.bilinear,
                )


def main():
    NATIVE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"target: {TARGET_RES} m, EPSG:{WORKING_EPSG}\n")

    for name in VA_TILES:
        src_path = DEM_DIR / f"{name}.tif"
        if not src_path.exists():
            print(f"  {name:<16} not found, skipping")
            continue

        drifted, cw = needs_work(src_path)
        if not drifted:
            print(f"  {name:<16} already {cw:.4f} m, nothing to do")
            continue

        dst_path = DEM_DIR / f"{name}_10m.tif"
        print(f"  {name:<16} {cw:.4f} m -> {TARGET_RES:.1f} m", end="", flush=True)
        if DRY:
            print("   [dry run]")
            continue

        tmp = DEM_DIR / f".{name}_tmp.tif"
        resample(src_path, tmp)
        tmp.replace(dst_path)
        shutil.move(str(src_path), str(NATIVE_DIR / src_path.name))
        print("   done")

    if DRY:
        print("\ndry run -- nothing changed")
        return

    print(f"\n  originals moved to {NATIVE_DIR}/")
    print("\nfinal state of data/dem:")
    bad = 0
    for f in sorted(DEM_DIR.glob("*.tif")):
        with rasterio.open(f) as s:
            cw, epsg = abs(s.transform.a), s.crs.to_epsg()
            flag = ""
            if abs(cw - TARGET_RES) > TOL or epsg != WORKING_EPSG:
                flag, bad = "   <-- MISMATCH", bad + 1
            print(f"  {f.name:<24} {cw:.4f} m  EPSG:{epsg}{flag}")

    if bad:
        print(f"\n  [FAIL] {bad} tile(s) off-grid")
    else:
        print("\n  [ok] every tile is 10.0000 m in EPSG:32618 -- metrics comparable")

    print("\n  NOTE: cache/arrays/*.npz were computed from the old grid and are")
    print("        now stale. parcel_elevation.py reads the .tif files directly,")
    print("        so the listings pipeline is unaffected.")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
