#!/usr/bin/env python3
"""
Walk a line across the cousin's lot and print the ground profile.

Matt has stood on this land. He describes it as: high at Marsh Causeway,
stepping down past the house, then swamp, then zero at the waterfront. That is
ground truth of a kind no rendering can give us, and it is the only parcel in
the search where we have it.

The question this settles: does 1 m bare-earth data under canopy describe real
ground, or a surface the interpolator invented between sparse returns? Numbers
alone could not tell us -- the wooded lot came back roughest, which is equally
consistent with real texture and with triangulation artifacts. A profile Matt
can compare against his own memory can tell us.

The transect (drawn by Matt on Google Earth, NE to SW) is a natural experiment:
it crosses cleared field near the road, then dense woods, then marsh, then
water. Same line, three covers. If the data is honest, the cleared section
should look organic, the wooded section should look like the same kind of
ground, and the marsh should go flat. If the wooded middle is visibly
different in character -- straight facets, or implausibly smooth -- that is
the interpolator showing through.

    mv -f ~/Downloads/transect.py . && chmod +x transect.py && ./transect.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
import parcel_elevation as pe    # noqa: E402

warnings.filterwarnings("ignore")

M_TO_FT = 3.280839895
OUT = Path("output/transect_knotts_island.png")

# Endpoints read off Matt's Google Earth screenshot. NE end on Marsh Causeway
# near the buildings, SW end at the shoreline. The line deliberately runs past
# the parcel at both ends -- more transect is better here, and the parcel
# boundary is marked on the plot.
NE = (36.5405, -75.9950)
SW = (36.5368, -76.0025)

# A point inside the cousin's parcel, so we can mark where his land starts and
# ends along the line.
PARCEL_PT = (36.53916, -75.99679)


def sample_line(lat0, lon0, lat1, lon1, res, n):
    """Elevation every n samples along the line, in feet."""
    import py3dep
    lats = np.linspace(lat0, lat1, n)
    lons = np.linspace(lon0, lon1, n)
    vals = py3dep.elevation_bycoords(list(zip(lons, lats)), crs=4326,
                                     source="tep" if res == 1 else "airmap")
    return np.asarray(vals, dtype="float64") * M_TO_FT


def line_length_m(lat0, lon0, lat1, lon1):
    import math
    R = 6371000.0
    p = math.radians
    dla, dlo = p(lat1 - lat0), p(lon1 - lon0)
    a = (math.sin(dla / 2) ** 2 +
         math.cos(p(lat0)) * math.cos(p(lat1)) * math.sin(dlo / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def main():
    length = line_length_m(*NE, *SW)
    print(f"transect {length:,.0f} m ({length * 3.28084:,.0f} ft) NE -> SW")
    print(f"  from {NE[0]:.5f}, {NE[1]:.5f}  (Marsh Causeway)")
    print(f"  to   {SW[0]:.5f}, {SW[1]:.5f}  (shoreline)")

    # where does the parcel start and end along the line?
    poly, attrs = pe.get_parcel_polygon(*PARCEL_PT)
    inside = None
    if poly is not None:
        from shapely.geometry import Point
        n_probe = 400
        lats = np.linspace(NE[0], SW[0], n_probe)
        lons = np.linspace(NE[1], SW[1], n_probe)
        flags = np.array([poly.contains(Point(lo, la)) for la, lo in zip(lats, lons)])
        if flags.any():
            i0, i1 = np.argmax(flags), n_probe - 1 - np.argmax(flags[::-1])
            inside = (i0 / n_probe * length, i1 / n_probe * length)
            print(f"  cousin's parcel spans {inside[0]:,.0f} - {inside[1]:,.0f} m "
                  f"along the line ({(attrs or {}).get('_acres_reported','?')} ac)")

    series = {}
    for res, n in ((10, int(length / 10)), (1, int(length / 1))):
        print(f"\n  sampling {n:,} points at {res} m ...", flush=True)
        try:
            ft = sample_line(*NE, *SW, res, n)
        except Exception as e:
            print(f"    FAILED {type(e).__name__}: {str(e)[:80]}")
            continue
        d = np.linspace(0, length, n)
        series[res] = (d, ft)
        good = np.isfinite(ft)
        print(f"    {good.sum():,} valid, "
              f"{np.nanmin(ft):.1f} to {np.nanmax(ft):.1f} ft")

        # step down the line in 50 m bins so Matt can read it as a walk
        print(f"    {'metres':>7} {'feet':>7}   profile")
        for start in range(0, int(length), 50):
            seg = ft[(d >= start) & (d < start + 50)]
            seg = seg[np.isfinite(seg)]
            if not len(seg):
                continue
            h = float(np.median(seg))
            bar = "#" * max(1, int(round(h * 3)))
            mark = ""
            if inside and inside[0] <= start <= inside[1]:
                mark = "  <- parcel"
            print(f"    {start:7d} {h:7.1f}   {bar}{mark}")

    # ---- plot -------------------------------------------------------------
    if series:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(13, 4.5))
            for res, (d, ft) in sorted(series.items(), reverse=True):
                ax.plot(d, ft, lw=0.8 if res == 1 else 1.8,
                        label=f"{res} m", alpha=0.9 if res == 1 else 0.75)
            if inside:
                ax.axvspan(inside[0], inside[1], color="0.85", zorder=0,
                           label="cousin's parcel")
            ax.axhline(0, color="steelblue", lw=1, ls=":")
            ax.set_xlabel("metres along transect, NE (Marsh Causeway) to SW (shoreline)")
            ax.set_ylabel("bare-earth elevation (ft)")
            ax.set_title("Knotts Island transect - road, through woods, to water")
            ax.legend(loc="upper right")
            ax.grid(alpha=0.3)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            fig.tight_layout()
            fig.savefig(OUT, dpi=140)
            print(f"\n  plot -> {OUT}")
        except Exception as e:
            print(f"\n  plot failed ({type(e).__name__}: {e})")

    print("\n" + "=" * 74)
    print("Compare against what you remember walking it:")
    print("  high at the road, stepping down past the house, swamp, then water.")
    print("If the shape matches, the data describes reality and the site model")
    print("has something real to work with. If the wooded middle looks like")
    print("straight facets or is implausibly smooth, 1 m under canopy is")
    print("interpolation and pad-finding in the woods is not trustworthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
