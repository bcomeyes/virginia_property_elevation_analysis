#!/usr/bin/env python3
"""
Matt's own layout (site_docs/munden/my_layout.png, drawn on Rick's exhibit):
red box = house, green oval = septic on the north panhandle, blue circle =
well, everything else in Rick's upland = pond, less a driveway.

The drawing is georeferenced from Rick's numbered points 2, 3, 4, 5, 7
(residuals under 0.3 m). Shapes are read by colour. The pond is Rick's upland
minus what the rules keep it away from (see pond_budget.py RULES).

    source ../.venv/bin/activate && python my_layout.py
"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from scipy import ndimage as nd
from shapely.geometry import LineString, MultiPoint, Point, box
from shapely.affinity import translate
from shapely.ops import nearest_points, unary_union

from common import ACRE_M2, CACHE, DOCS, M_TO_FT, OUT, parcel, rick
from pond_budget import (FIELD_TO_POND, FIELD_TO_WELL, POND_TO_LINE,
                         TANK_TO_POND, WETLAND_STRIP, largest)

FT = 1 / M_TO_FT
DRIVE_FT = 12
POND_TO_STREET = 25          # the road is a property line; same 25 ft as optimize_layout.py
# per drawing: Rick's point centres (px), where to look for the oval, the red
# house (rows below the sewer arrow), and the well circle centre
DRAWINGS = {
    "my_layout": dict(pix={7: (491, 395), 4: (646, 897), 3: (1027, 898), 5: (597, 995),
                           2: (902, 302)},
                      oval_box=(860, 0, 2000, 250), red_min_row=0, well=(952, 198)),
    "my_layout2": dict(pix={7: (491, 452), 4: (646, 954), 3: (1027, 955), 5: (597, 1052),
                            2: (902, 360)},
                       oval_box=(880, 0, 1000, 170), red_min_row=372, well=(911, 324)),
}


def read_drawing(name):
    d = DRAWINGS[name]
    _, pts = rick()
    P = pts.set_index("point").geometry
    A = np.array([[x, y, 1] for x, y in d["pix"].values()])
    M, *_ = np.linalg.lstsq(A, np.array([[P[k].x, P[k].y] for k in d["pix"]]), rcond=None)
    print("fit residuals (m):", np.round(np.hypot(*(A @ M - np.array(
        [[P[k].x, P[k].y] for k in d["pix"]])).T), 2))
    im = (mpimg.imread(DOCS / f"{name}.png")[..., :3] * 255).astype(int)
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    red = (r > 190) & (g < 50) & (b < 50)
    red[:d["red_min_row"]] = False                 # drop the sewer arrow
    oval = (g > 150) & (r < 140) & (b < 120) & (g - r > 50)
    x0, y0, x1, y1 = d["oval_box"]
    keep = np.zeros_like(oval)
    keep[y0:y1, x0:x1] = True
    oval &= keep

    def poly(mask):
        m = nd.binary_fill_holes(nd.binary_closing(mask, iterations=3))
        ys, xs = np.nonzero(m)
        return MultiPoint(np.c_[xs, ys, np.ones_like(xs)] @ M).convex_hull

    return poly(red), poly(oval), Point(np.array([*d["well"], 1]) @ M)


def main():
    feats, _ = rick()
    par, _ = parcel()
    up = feats["upland"]
    wet = unary_union([w for w in getattr(feats["wetland"], "geoms", [feats["wetland"]])
                       if w.buffer(-3).area > 0])      # drop the east tracing sliver
    name = sys.argv[1] if len(sys.argv) > 1 else "my_layout"
    house, septic, well = read_drawing(name)

    # street: the parcel edge along Munden Point Rd (the long south-east side)
    ring = list(par.exterior.coords)
    segs = [LineString(ring[i:i + 2]) for i in range(len(ring) - 1)]
    street = max((s for s in segs if s.centroid.y < up.bounds[1] + 60), key=lambda s: s.length)

    if "--tip" in sys.argv:          # slide the septic to the north tip of the panhandle
        ok = lambda g: (up.contains(g) and g.distance(feats["ditch"]) >= 20 * FT
                        and g.distance(par.exterior) >= 5 * FT)
        for dy in np.arange(0, 400, 1.0):
            moved = translate(septic, 0, dy)
            for dx in np.arange(-15, 15.5, 1.0):
                if ok(translate(moved, dx, 0)):
                    best = translate(moved, dx, 0)
                    break
        septic = best
        name += "_tip"
    # tank: beside the septic field (long sewer line from the house), as in
    # optimize_layout.py, so its 50 ft ring overlaps the field's
    a, _ = nearest_points(septic.boundary, house)
    v = np.array([house.centroid.x - a.x, house.centroid.y - a.y])
    v /= np.hypot(*v)
    tank = Point(a.x + v[0] * 10 * FT, a.y + v[1] * 10 * FT)

    # driveway: from the house east to the property line, then south along it
    # to the road, inside the band the pond must keep clear of the line anyway
    edge = par.exterior.buffer(6 * FT)
    band = par.exterior.buffer((6 + DRIVE_FT) * FT).difference(edge).intersection(par)
    y0, y1 = house.centroid.y - DRIVE_FT / 2 * FT, house.centroid.y + DRIVE_FT / 2 * FT
    down = band.intersection(box(house.bounds[2], up.bounds[1] - 10, up.bounds[2] + 10, y1))
    spur = box(house.bounds[2], y0, up.bounds[2] + 10, y1).intersection(par).difference(edge)
    drive = unary_union([down, spur])
    base = (up.difference(house).difference(drive)
            .difference(septic.buffer(FIELD_TO_POND * FT))
            .difference(tank.buffer(TANK_TO_POND * FT))
            .difference(par.exterior.buffer(POND_TO_LINE * FT))
            .difference(street.buffer(POND_TO_STREET * FT)))
    strip = wet.buffer(WETLAND_STRIP * FT)
    ponds = {"no": base, "yes": base.difference(strip)}
    ac = lambda g: g.area / ACRE_M2

    nums = {
        "rick_upland_ac": ac(up),
        "house_sqft": house.area * M_TO_FT ** 2,
        "house_to_wetland_ft": house.distance(wet) * M_TO_FT,
        "septic_sqft": septic.area * M_TO_FT ** 2,
        "septic_to_ditch_ft": septic.distance(feats["ditch"]) * M_TO_FT,
        "well_to_septic_ft": well.distance(septic) * M_TO_FT,
        "well_to_house_ft": well.distance(house) * M_TO_FT,
        "septic_in_upland_frac": septic.intersection(up).area / septic.area,
        "septic_in_parcel_frac": septic.intersection(par).area / septic.area,
        "house_to_septic_ft": house.distance(septic) * M_TO_FT,
        "pond_ac_no_strip": ac(ponds["no"]),
        "pond_ac_with_strip": ac(ponds["yes"]),
        "pond_largest_piece_no_strip": ac(largest(ponds["no"])),
        "pond_largest_piece_with_strip": ac(largest(ponds["yes"])),
    }
    for k, val in nums.items():
        print(f"{k:32s} {val:,.2f}")
    (OUT / f"{name}.json").write_text(json.dumps(nums, indent=1))

    img = mpimg.imread(CACHE / "aerial.jpg")
    ext = json.loads((CACHE / "aerial.json").read_text())
    halo = [pe.withStroke(linewidth=4, foreground="black")]
    fig, axes = plt.subplots(1, 2, figsize=(16, 13))
    for ax, key in zip(axes, ["no", "yes"]):
        ax.imshow(img, extent=ext)
        for g in getattr(wet, "geoms", [wet]):
            ax.fill(*g.exterior.xy, fc="#2e8b3e", alpha=0.3, ec="none")
        ax.plot(*par.exterior.xy, c="white", lw=2)
        ax.plot(*up.exterior.xy, c="#ff8c00", lw=2.5)
        ax.plot(*feats["ditch"].xy, c="#1e6fff", lw=3)
        if key == "yes":
            s = strip.intersection(up)
            for g in getattr(s, "geoms", [s]):
                ax.fill(*g.exterior.xy, fc="#ff3030", alpha=0.35, hatch="//", ec="none")
        pond = ponds[key]
        for g in getattr(pond, "geoms", [pond]):
            ax.fill(*g.exterior.xy, fc="#00b4ff", alpha=0.85, ec="k", lw=1)
        for g in getattr(drive, "geoms", [drive]):
            ax.fill(*g.exterior.xy, fc="#cccccc", ec="k", lw=0.8)
        ax.fill(*house.exterior.xy, fc="#e8211b", ec="k", lw=1.5)
        ax.fill(*septic.exterior.xy, fc="#b36b00", ec="k", lw=1.5)
        ax.plot(tank.x, tank.y, "s", ms=9, c="#6b3f00", mec="white")
        ax.plot(well.x, well.y, "o", ms=12, c="#ff2bd6", mec="white", mew=2)
        ax.plot(*well.buffer(FIELD_TO_WELL * FT).exterior.xy, c="#ff2bd6", lw=1.5, ls=":")
        c = largest(pond).centroid
        ax.text(c.x, c.y, f"POND\n{ac(pond):.2f} ac", fontsize=16, weight="bold",
                ha="center", va="center", color="white", path_effects=halo)
        bb = up.buffer(30).bounds
        ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3])
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title("Pond may go right up to the wetland" if key == "no" else
                     "Pond also stays 50 ft from the wetland (red)",
                     fontsize=14, weight="bold")
    fig.legend(handles=[
        Patch(fc="none", ec="#ff8c00", lw=2.5, label=f"Rick's upland: {ac(up):.2f} ac, all of it"),
        Patch(fc="#2e8b3e", alpha=0.3, label="Rick's wetland"),
        Patch(fc="#e8211b", ec="k", label="your house box"),
        Patch(fc="#b36b00", ec="k", label="your septic oval"),
        plt.Line2D([], [], marker="s", ls="", c="#6b3f00", label="septic tank, beside the field"),
        plt.Line2D([], [], marker="o", ls="", ms=10, c="#ff2bd6", mec="white", label="your well"),
        plt.Line2D([], [], c="#ff2bd6", ls=":", label="100 ft around the well: septic must be outside"),
        Patch(fc="#cccccc", ec="k", label="driveway"),
        Patch(fc="#00b4ff", ec="k", label="pond: 50 ft from septic + tank, 25 ft from property lines"),
    ], loc="lower center", ncol=3, fontsize=11, frameon=False)
    fig.suptitle("1832 Munden Point Rd: your layout; everything else in the upland is pond",
                 fontsize=17, weight="bold")
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    fig.savefig(OUT / f"{name}.png", dpi=100)
    print(f"wrote {OUT / f'{name}.png'}")


if __name__ == "__main__":
    main()
