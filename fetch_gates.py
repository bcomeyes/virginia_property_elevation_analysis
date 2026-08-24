#!/usr/bin/env python3
"""
Fetch the Gates County, NC bare-earth DEM from 3DEP.

Why this is a standalone script and not a notebook cell:
  - 3DEP drops LARGE requests probabilistically but serves small ones reliably.
    (Proven last month: whole-locality fetches failed repeatedly while a 2 km
    probe returned instantly.) So we fetch in small tiles.
  - Each tile is written to disk the moment it lands. If the run dies halfway,
    re-running skips everything already saved. You never lose progress.
  - Resolution is pinned to exactly 10.0 m on the way OUT as well as the way in.
    The VA notebook forgot the output pin, which is why Suffolk landed at 7.79 m
    and Virginia Beach at 9.58 m instead of 10. Matching NC's exact 10.0 m is
    what makes terrain metrics comparable across the state line.

Run:  python3 fetch_gates.py
Re-run it as many times as you like; it resumes.
"""

import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import py3dep
import rioxarray  # noqa: F401  (registers the .rio accessor)
from rioxarray.merge import merge_arrays
from shapely.geometry import box

# ---------------------------------------------------------------- config

COUNTY = "Gates"
STATE_FIPS = "37"          # North Carolina
DEM_RES = 10               # metres, 3DEP 1/3 arc-second
WORKING_EPSG = 32618       # UTM 18N -- matches every existing tile
TILE_DEG = 0.07            # tile size in degrees; small enough that 3DEP obliges
PAD_DEG = 0.0015           # slight overlap so tiles meet with no seam gap
TRIES = 4

DEM_DIR = Path("data/dem")
TILE_DIR = Path("cache/gates_tiles")   # per-tile scratch, safe to delete after
OUT = DEM_DIR / f"{COUNTY}_10m.tif"

# ---------------------------------------------------------------- boundary


def county_geometry():
    """Gates County boundary in WGS84, from the local gpkg if present."""
    local = Path("data/nc_counties.gpkg")
    if local.exists():
        gdf = gpd.read_file(local)
        # column naming varies by source; find whichever holds county names
        for col in ("NAME", "name", "County", "COUNTY", "NAMELSAD"):
            if col in gdf.columns:
                hit = gdf[gdf[col].astype(str).str.contains(COUNTY, case=False, na=False)]
                if len(hit):
                    print(f"  boundary from data/nc_counties.gpkg (column {col!r})")
                    return hit.to_crs(4326).geometry.iloc[0]
        print(f"  [warn] {local} has no recognisable name column: {list(gdf.columns)[:8]}")

    print("  boundary from pygris (downloading TIGER county file)")
    import pygris
    gdf = pygris.counties(state=STATE_FIPS, cb=True, cache=True)
    hit = gdf[gdf.NAME.str.contains(COUNTY, case=False, na=False)]
    if not len(hit):
        sys.exit(f"Could not find {COUNTY} County in TIGER data")
    return hit.to_crs(4326).geometry.iloc[0]


# ---------------------------------------------------------------- fetch


def fetch_tiles(geom_wgs):
    """Fetch the county as small tiles. Each lands on disk immediately."""
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    minx, miny, maxx, maxy = geom_wgs.bounds
    nx = int(np.ceil((maxx - minx) / TILE_DEG))
    ny = int(np.ceil((maxy - miny) / TILE_DEG))
    print(f"  {COUNTY}: {nx}x{ny} grid, {TILE_DEG} deg tiles")

    paths, failed, skipped = [], 0, 0
    for i in range(nx):
        for j in range(ny):
            tile_path = TILE_DIR / f"t_{i}_{j}.tif"

            if tile_path.exists():           # resume: already have this one
                paths.append(tile_path)
                skipped += 1
                continue

            cell = box(minx + i * TILE_DEG - PAD_DEG,
                       miny + j * TILE_DEG - PAD_DEG,
                       minx + (i + 1) * TILE_DEG + PAD_DEG,
                       miny + (j + 1) * TILE_DEG + PAD_DEG)
            piece = cell.intersection(geom_wgs)
            if piece.is_empty:               # tile is entirely outside the county
                continue

            for attempt in range(1, TRIES + 1):
                try:
                    da = py3dep.get_map("DEM", piece, resolution=DEM_RES,
                                        geo_crs=4326, crs=4326)
                    da.rio.to_raster(tile_path)   # save NOW, before anything else
                    paths.append(tile_path)
                    print(f"    [{i},{j}] ok", flush=True)
                    break
                except Exception as e:
                    if attempt == TRIES:
                        failed += 1
                        print(f"    [{i},{j}] FAILED ({type(e).__name__})", flush=True)
                    else:
                        time.sleep(3 * attempt)
            time.sleep(0.3)                  # be a polite guest

    if skipped:
        print(f"  resumed: {skipped} tiles already on disk")
    if failed:
        print(f"  [warn] {failed} tiles failed -- re-run this script to retry just those")
    return paths


# ---------------------------------------------------------------- mosaic


def mosaic(paths):
    """Stitch tiles, reproject to UTM 18N at EXACTLY 10 m, write once."""
    print(f"  mosaicking {len(paths)} tiles ...")
    arrays = [rioxarray.open_rasterio(p, masked=True) for p in paths]
    m = merge_arrays(arrays) if len(arrays) > 1 else arrays[0]

    # The pin the VA notebook was missing. Without `resolution=`, rioxarray asks
    # GDAL to *suggest* an output cell size from the extent, which is how Suffolk
    # ended up at 7.79 m after ordering 10 m.
    m = m.rio.reproject(WORKING_EPSG, resolution=DEM_RES)

    DEM_DIR.mkdir(parents=True, exist_ok=True)
    m.rio.to_raster(OUT)
    return m


def verify():
    """Confirm we got what we ordered. Loud failure beats a silent 22% drift."""
    import rasterio
    with rasterio.open(OUT) as src:
        cw, ch = abs(src.transform.a), abs(src.transform.e)
        epsg = src.crs.to_epsg()
        print(f"\n  wrote {OUT}")
        print(f"  size {src.width} x {src.height}, cell {cw:.4f} x {ch:.4f} m, EPSG:{epsg}")
        ok = True
        if epsg != WORKING_EPSG:
            print(f"  [FAIL] CRS is {epsg}, expected {WORKING_EPSG}")
            ok = False
        if abs(cw - DEM_RES) > 0.01 or abs(ch - DEM_RES) > 0.01:
            print(f"  [FAIL] cell size drifted from {DEM_RES} m")
            ok = False
        if ok:
            print("  [ok] matches the NC tiles exactly -- metrics are comparable")
        return ok


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    if OUT.exists():
        print(f"{OUT} already exists. Delete it to refetch.")
        verify()
        sys.exit(0)

    print(f"Fetching {COUNTY} County, NC")
    geom = county_geometry()
    paths = fetch_tiles(geom)
    if not paths:
        sys.exit("  [skip] every tile failed -- 3DEP may be refusing requests; try later")
    mosaic(paths)
    good = verify()
    print(f"\n  tile scratch is in {TILE_DIR}/ -- safe to delete once this looks right")
    sys.exit(0 if good else 1)
