#!/usr/bin/env python3
"""
Fetch the parcel + 200 ft window from each of the three 1 m DEMs.

Windowed reads over HTTP (the source tiles are ~200 MB cloud-optimised
GeoTIFFs; we need about 400 x 700 pixels of each). All three share one pixel
grid, so the windows line up exactly and differences between them are
differences between flights, not resampling.

Also compares the VGIN parcel polygon to Rick's traced study area.

    source .venv/bin/activate && python munden/fetch_dem.py
"""
import numpy as np
import rasterio
from rasterio.windows import from_bounds

from common import ACRE_M2, BUFFER_M, DEMS, M_TO_FT, dem_path, parcel, rick

feats, points = rick()
par, props = parcel()
study = feats["study"]

print("== Parcel vs Rick's study area ==")
print(f"  VGIN parcel {props['PARCELID']}: {par.area / ACRE_M2:.2f} ac")
print(f"  Rick study area (traced): {study.area / ACRE_M2:.2f} ac  (Rick: 25.39)")
inter = par.intersection(study).area
print(f"  overlap {inter / ACRE_M2:.2f} ac; parcel-only "
      f"{par.difference(study).area / ACRE_M2:.2f} ac; study-only "
      f"{study.difference(par).area / ACRE_M2:.2f} ac")
print(f"  max boundary gap (Hausdorff) {par.boundary.hausdorff_distance(study.boundary) * M_TO_FT:.0f} ft")

minx, miny, maxx, maxy = par.union(study).buffer(BUFFER_M).bounds
minx, miny = np.floor(minx), np.floor(miny)
maxx, maxy = np.ceil(maxx), np.ceil(maxy)

print("\n== DEM windows ==")
for key, (label, url) in DEMS.items():
    out = dem_path(key)
    if out.exists():
        print(f"  {key} cached")
        continue
    with rasterio.open("/vsicurl/" + url) as src:
        win = from_bounds(minx, miny, maxx, maxy, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win).astype("float32")
        nod = src.nodata
        arr[(arr == nod) | (arr < -100)] = np.nan
        prof = src.profile.copy()
        prof.update(width=arr.shape[1], height=arr.shape[0],
                    transform=src.window_transform(win), nodata=np.nan,
                    compress="deflate", tiled=False, blockxsize=None, blockysize=None)
        prof.pop("blockxsize"); prof.pop("blockysize")
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(arr, 1)
    print(f"  {key} {label}: {arr.shape}, {np.isnan(arr).mean() * 100:.1f}% nodata")
