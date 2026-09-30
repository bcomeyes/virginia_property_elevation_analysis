#!/usr/bin/env python3
"""
Ditch cross-sections: is the ground east of the ditch (Rick's upland strip)
different from the ground west of it (his Wetland #2 and #1)?

A transect every 20 m along Rick's ditch, 60 m each way, sampled on both 1 m
flights. Each is re-centred on the lowest point within 8 m of the trace,
because the traced ditch is only good to ~20 ft and a 7 ft ditch is easy to
miss by that much.

Two things to look for:
  * a spoil berm -- a ridge a few metres off one bank, where the dirt went
  * bank symmetry -- if the west bank sits as high as the east bank at the same
    distance from the ditch, the ditch drains both sides equally, and whatever
    makes the east strip upland should apply on the west too

Sign convention: + offsets are WEST (north-west on the southern bend),
- offsets are EAST (south-east on the bend).

    source ../.venv/bin/activate && python transects.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage as nd

from common import M_TO_FT, OUT, dem_path, rick

STEP_M = 20
HALF_M = 60
RECENTRE_M = 8
BANDS = [(4, 10), (10, 25), (25, 45)]      # metres from the ditch centre


def load(k):
    with rasterio.open(dem_path(k)) as s:
        return s.read(1) * M_TO_FT, s.transform


def sample(a, T, x, y):
    c = (np.asarray(x) - T.c) / T.a - 0.5
    r = (np.asarray(y) - T.f) / T.e - 0.5
    return nd.map_coordinates(a, [r, c], order=1, mode="nearest", cval=np.nan)


def main():
    feats, _ = rick()
    ditch = feats["ditch"]
    study = feats["study"]
    dems = {k: load(k) for k in ("2013", "2023")}
    offs = np.arange(-HALF_M, HALF_M + 0.5, 1.0)

    rows, profiles = [], []
    for s in np.arange(STEP_M / 2, ditch.length - 5, STEP_M):
        p = ditch.interpolate(s)
        a_, b_ = ditch.interpolate(max(s - 3, 0)), ditch.interpolate(s + 3)
        tx, ty = b_.x - a_.x, b_.y - a_.y
        n = np.hypot(tx, ty)
        nx, ny = ty / n, -tx / n                  # ditch traced N->S: +normal = west
        a23, T23 = dems["2023"]
        near = np.arange(-RECENTRE_M, RECENTRE_M + 0.5, 1.0)
        z = sample(a23, T23, p.x + near * nx, p.y + near * ny)
        shift = near[np.nanargmin(z)]
        cx, cy = p.x + shift * nx, p.y + shift * ny
        xs, ys = cx + offs * nx, cy + offs * ny
        from shapely.geometry import Point
        inside = np.array([study.buffer(3).contains(Point(x, y)) for x, y in zip(xs, ys)])
        prof = {}
        for k, (a, T) in dems.items():
            v = sample(a, T, xs, ys)
            v[~inside] = np.nan               # keep to the parcel
            prof[k] = v
        profiles.append((s, prof))
        row = {"station_m": s, "recentre_m": shift}
        for k, v in prof.items():
            bottom = np.nanmin(v[np.abs(offs) <= 2])
            row[f"bottom_{k}"] = bottom
            for lo, hi in BANDS:
                w = np.nanmean(v[(offs >= lo) & (offs < hi)]) if np.any(~np.isnan(v[(offs >= lo) & (offs < hi)])) else np.nan
                e = np.nanmean(v[(offs <= -lo) & (offs > -hi)]) if np.any(~np.isnan(v[(offs <= -lo) & (offs > -hi)])) else np.nan
                row[f"W{lo}-{hi}_{k}"] = w
                row[f"E{lo}-{hi}_{k}"] = e
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "transects.csv", index=False, float_format="%.2f")

    print("Ditch depth (2023): bank 4-10 m mean minus bottom, ft")
    d = ((df["W4-10_2023"] + df["E4-10_2023"]) / 2 - df["bottom_2023"])
    print(f"  median {d.median():.2f}, range {d.min():.2f}..{d.max():.2f}")
    d13 = ((df["W4-10_2013"] + df["E4-10_2013"]) / 2 - df["bottom_2013"])
    print(f"  2013 median {d13.median():.2f}")
    print("\nWest minus East, same distance from ditch (ft). + means west is HIGHER")
    print(f"  {'station':>7}  " + "  ".join(f"{lo}-{hi}m 13/23".rjust(15) for lo, hi in BANDS))
    for _, r in df.iterrows():
        cells = []
        for lo, hi in BANDS:
            c = []
            for k in ("2013", "2023"):
                c.append(r[f"W{lo}-{hi}_{k}"] - r[f"E{lo}-{hi}_{k}"])
            cells.append(f"{c[0]:+.2f}/{c[1]:+.2f}".rjust(15))
        print(f"  {r.station_m:7.0f}  " + "  ".join(cells))
    for lo, hi in BANDS:
        for k in ("2013", "2023"):
            v = (df[f"W{lo}-{hi}_{k}"] - df[f"E{lo}-{hi}_{k}"]).dropna()
            print(f"  median {lo}-{hi} m {k}: {v.median():+.2f} ft over {len(v)} transects")

    # Figure: stacked profiles, offset per station, both flights
    fig, axes = plt.subplots(1, 2, figsize=(14, 10), sharey=True)
    for ax, k in zip(axes, ("2013", "2023")):
        for i, (s, prof) in enumerate(profiles):
            v = prof[k]
            base = np.nanmedian(v)
            ax.plot(offs * M_TO_FT, v - base + i * 1.5, lw=1, c="k")
            ax.text(HALF_M * M_TO_FT + 3, i * 1.5, f"{s:.0f} m", fontsize=7, va="center")
        ax.axvline(0, c="b", lw=0.8)
        ax.set_title(f"{k}: ditch cross-sections, north (bottom) to south (top)")
        ax.set_xlabel("feet from ditch    <- EAST (Rick upland)   |   WEST (Rick wetland) ->")
    axes[0].set_ylabel("elevation, each profile offset 1.5 ft")
    fig.tight_layout()
    fig.savefig(OUT / "transects.png", dpi=110)
    print(f"\nwrote {OUT / 'transects.png'}, {OUT / 'transects.csv'}")


if __name__ == "__main__":
    main()
