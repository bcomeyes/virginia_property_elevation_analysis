#!/usr/bin/env python3
"""
How big a pond fits in the green block Matt circled (munden_rectangle.png)
once the house, septic and well are placed the way he and Larissa picture it:

    trees (50 ft wetland strip) | house | pond, well at its edge | property line
                                 septic pushed to the north end

The block is Rick's upland inside the purple loop. The loop is georeferenced
from the screenshot by fitting six of Rick's pins (residuals under 1 m).

Every distance is a rule, with its source; see RULES. Within that layout the
script tries house and septic positions on a 3 m grid and keeps the one that
leaves the biggest pond. It is a picture of what the rules allow, not a site
plan.

    source ../.venv/bin/activate && python pond_budget.py
"""
import itertools
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from shapely.geometry import MultiPoint, Point, box
from shapely.ops import nearest_points, unary_union

from common import ACRE_M2, CACHE, DOCS, M_TO_FT, OUT, parcel, rick

FT = 1 / M_TO_FT
SQFT_PER_AC = 43560

# ---- the things we place ---------------------------------------------------
HOUSE_FT = (50, 60)          # 3,000 sq ft, one story; long side north-south
YARD_FT = 20                 # lawn ring kept around the house
# 4 BR = 600 gpd. Soil is mapped Nimmo (6 in water table): assume an
# engineered (AOSS) system. City requires a reserve field the same size.
FIELD_FT = (50, 100)         # primary + reserve together, 5,000 sq ft
HOUSE_TO_TREES_MAX_FT = 15   # their layout: house backs onto the wooded strip

# ---- the rules (ft) --------------------------------------------------------
WETLAND_STRIP = 50           # VA Beach SRWMO §7(c): no house, digging, lawn
HOUSE_TO_LINE = 20           # VA Beach zoning AG-1/AG-2 side yard (CZO §402)
TANK_TO_HOUSE = 10           # 12VAC5-610-592 Table 4.1
TANK_TO_POND = 50            # 12VAC5-610-592 Table 4.1, impounded water
TANK_TO_WELL = 50            # 12VAC5-630-380 Table 1
FIELD_TO_HOUSE = 10          # 12VAC5-610-592 Table 4.2
FIELD_TO_POND = 50           # 12VAC5-610-592 Table 4.2, impounded water
FIELD_TO_WELL = 100          # 12VAC5-630-380: class IIIC well, incl. reserve
FIELD_TO_DITCH = 20          # 12VAC5-613-200: ditch within 6 in of groundwater
FIELD_TO_LINE = 5            # 12VAC5-610-592 Table 4.2
WELL_TO_HOUSE = 15           # 12VAC5-630-380 Table 1
WELL_TO_LINE = 5             # 12VAC5-630-380 (50 next to a 3+ ac farm, waivable)
POND_TO_LINE = 25            # VA Beach §30-1 farm-pond rule, used as a guide

RULES = [  # thing, must be at least this far from ..., source
    ("House", f"{WETLAND_STRIP} ft from wetland (the L strip)", "VA Beach SRWMO §7(c)"),
    ("", f"{HOUSE_TO_LINE} ft from property line", "VA Beach zoning, AG side yard"),
    ("Septic tank", f"{TANK_TO_HOUSE} ft from house", "12VAC5-610-592"),
    ("", f"{TANK_TO_POND} ft from pond", "12VAC5-610-592"),
    ("", f"{TANK_TO_WELL} ft from well", "12VAC5-630-380"),
    ("Septic field + reserve", f"{FIELD_TO_HOUSE} ft from house", "12VAC5-610-592"),
    ("", f"{FIELD_TO_POND} ft from pond", "12VAC5-610-592"),
    ("", f"{FIELD_TO_WELL} ft from well (50 for some well types)", "12VAC5-630-380"),
    ("", f"{FIELD_TO_DITCH} ft from ditch", "12VAC5-613-200"),
    ("", f"{FIELD_TO_LINE} ft from property line", "12VAC5-610-592"),
    ("Well", f"{WELL_TO_HOUSE} ft from house", "12VAC5-630-380"),
    ("", f"{WELL_TO_LINE} ft from property line (50 if neighbor farms 3+ ac)", "12VAC5-630-380"),
    ("", "no distance to the pond; not in a low, swampy spot", "12VAC5-630-380"),
    ("Pond", f"{POND_TO_LINE} ft from property line", "VA Beach §30-1 (farm ponds)"),
    ("", "nothing required from house or well", ""),
]


def loop_polygon():
    """Purple loop from the screenshot, in UTM, to the middle of the pen stroke."""
    feats, pts = rick()
    P = pts.set_index("point").geometry
    pix = {9: (464, 678), 8: (689, 838), 1: (808, 876), 2: (782, 1146),
           7: (567, 1195), 3: (850, 1474)}          # pin tips, original px
    A = np.array([[x, y, 1] for x, y in pix.values()])
    U = np.array([[P[k].x, P[k].y] for k in pix])
    M, *_ = np.linalg.lstsq(A, U, rcond=None)
    im = (mpimg.imread(DOCS / "munden_rectangle.png")[..., :3] * 255).astype(int)
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    pur = (r > 90) & (b > 120) & (g < 60)
    pur[:950] = False; pur[1420:] = False; pur[:, :580] = False; pur[:, 1030:] = False
    ys, xs = np.nonzero(pur)
    xy = np.c_[xs, ys, np.ones_like(xs)] @ M
    return MultiPoint(xy).convex_hull.buffer(-4.5)


def rect(cx, cy, w, h):
    return box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def largest(g):
    parts = sorted(getattr(g, "geoms", [g]), key=lambda p: -p.area)
    return parts[0] if parts and not g.is_empty else g


def main():
    feats, _ = rick()
    par, _ = parcel()
    line = par.exterior
    block = feats["upland"].intersection(loop_polygon()).intersection(par)

    # Real wetland only: drop the thin gap between Rick's traced upland and the
    # property line on the east (a tracing artifact, ~6 ft wide).
    wet = [w for w in getattr(feats["wetland"], "geoms", [feats["wetland"]])
           if w.buffer(-3).area > 0]
    wet = unary_union(wet)
    strip = block.intersection(wet.buffer(WETLAND_STRIP * FT))
    usable = block.difference(strip)

    x0, y0, x1, y1 = block.bounds
    grid = [(x, y) for x in np.arange(x0, x1, 3) for y in np.arange(y0, y1, 3)]
    hw, hh = HOUSE_FT[0] * FT, HOUSE_FT[1] * FT
    fw, fh = FIELD_FT[0] * FT, FIELD_FT[1] * FT
    houses = [rect(x, y, hw, hh) for x, y in grid
              if usable.contains(rect(x, y, hw, hh))
              and rect(x, y, hw, hh).distance(line) >= HOUSE_TO_LINE * FT
              and rect(x, y, hw, hh).distance(strip) <= HOUSE_TO_TREES_MAX_FT * FT]
    fields = [F for (x, y), (w, h) in itertools.product(grid, [(fw, fh), (fh, fw)])
              for F in [rect(x, y, w, h)]
              if usable.contains(F) and F.distance(feats["ditch"]) >= FIELD_TO_DITCH * FT
              and F.distance(line) >= FIELD_TO_LINE * FT]
    pond_room = usable.difference(line.buffer(POND_TO_LINE * FT))

    best = None
    for H, F in itertools.product(houses, fields):
        if H.distance(F) < FIELD_TO_HOUSE * FT:
            continue
        yard = H.buffer(YARD_FT * FT, join_style=2)
        a, _ = nearest_points(H.boundary, F)            # tank: off the house, toward field
        v = np.array([F.centroid.x - a.x, F.centroid.y - a.y])
        v /= np.hypot(*v)
        tank = Point(a.x + v[0] * TANK_TO_HOUSE * FT, a.y + v[1] * TANK_TO_HOUSE * FT)
        pond = largest(pond_room.difference(yard)
                       .difference(F.buffer(FIELD_TO_POND * FT))
                       .difference(tank.buffer(TANK_TO_POND * FT)))
        if pond.is_empty or (best and pond.area <= best["pond"].area):
            continue
        # well on the pond's edge, legal distance from everything
        edge = pond.exterior
        cands = [edge.interpolate(t, normalized=True) for t in np.linspace(0, 1, 200)]
        ok = [w for w in cands if usable.buffer(-10 * FT).contains(w)
              and w.distance(F) >= FIELD_TO_WELL * FT
              and w.distance(tank) >= TANK_TO_WELL * FT
              and w.distance(H) >= WELL_TO_HOUSE * FT
              and w.distance(line) >= WELL_TO_LINE * FT]
        if not ok:
            continue
        W = min(ok, key=lambda w: w.distance(H))        # nearest the house
        best = dict(house=H, yard=yard.intersection(usable), field=F, tank=tank,
                    well=W, pond=pond)

    pond = best["pond"]
    H = best["house"]
    ac = lambda g: g.area / ACRE_M2
    nums = {
        "block_ac": ac(block), "strip_ac": ac(strip), "usable_ac": ac(usable),
        "pond_max_ac": ac(pond),
        "house_to_strip_ft": H.distance(strip) * M_TO_FT,
        "well_to_house_ft": best["well"].distance(H) * M_TO_FT,
        "field_in_block": best["field"].within(block),
    }
    for k, v in nums.items():
        print(f"{k:20s} {v:.2f}" if isinstance(v, float) else f"{k:20s} {v}")
    (OUT / "pond_budget.json").write_text(json.dumps(nums, indent=1))

    # ---- map + rules table ----------------------------------------------------
    img = mpimg.imread(CACHE / "aerial.jpg")
    ext = json.loads((CACHE / "aerial.json").read_text())
    halo = [pe.withStroke(linewidth=4, foreground="black")]
    fig = plt.figure(figsize=(17, 13))
    ax = fig.add_axes([0.01, 0.15, 0.50, 0.78])
    tx = fig.add_axes([0.53, 0.08, 0.46, 0.84]); tx.axis("off")

    ax.imshow(img, extent=ext)
    ax.plot(*par.exterior.xy, c="white", lw=2)
    ax.plot(*feats["ditch"].xy, c="#1e6fff", lw=3)
    for g in getattr(strip, "geoms", [strip]):
        ax.fill(*g.exterior.xy, fc="#ff3030", alpha=0.35, ec="#ff3030", hatch="//")
    ax.fill(*pond.exterior.xy, fc="#00b4ff", alpha=0.8, ec="k", lw=1.5)
    for g in getattr(best["yard"], "geoms", [best["yard"]]):
        ax.fill(*g.exterior.xy, fc="#7ddc5a", alpha=0.5, ec="none")
    ax.fill(*H.exterior.xy, fc="white", ec="k", lw=1.5)
    ax.fill(*best["field"].exterior.xy, fc="#b36b00", ec="k", lw=1.5)
    ax.plot(best["tank"].x, best["tank"].y, "s", ms=10, c="#6b3f00", mec="white")
    ax.plot(best["well"].x, best["well"].y, "o", ms=14, c="#ff2bd6", mec="white", mew=2)
    ax.fill(*block.exterior.xy, fc="none", ec="#ffe600", lw=3)
    for g, label in [(H, "HOUSE"), (best["field"], "SEPTIC")]:
        c = g.centroid
        ax.text(c.x, c.y, label, fontsize=11, weight="bold", ha="center", va="center",
                color="white", path_effects=halo)
    c = pond.centroid
    ax.text(c.x, c.y - 12, f"POND\n{ac(pond):.2f} ac", fontsize=17, weight="bold",
            ha="center", va="center", color="white", path_effects=halo)
    ax.text(best["well"].x - 6, best["well"].y + 5, "WELL", ha="right", fontsize=11, weight="bold",
            color="white", path_effects=halo)
    bb = block.buffer(25).bounds
    ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3])
    ax.set_xticks([]); ax.set_yticks([])
    xs, ys = bb[0] + 5, bb[1] + 6
    ax.plot([xs, xs + 100 * FT], [ys, ys], c="white", lw=5)
    ax.text(xs, ys + 3, "100 ft", color="white", fontsize=11, weight="bold", path_effects=halo)
    ax.legend(handles=[
        Patch(fc="none", ec="#ffe600", lw=3, label=f"green block you circled: {ac(block):.1f} ac"),
        Patch(fc="#ff3030", alpha=0.35, hatch="//", label=f"no-go L: 50 ft from wetland ({ac(strip):.2f} ac, stays trees)"),
        Patch(fc="white", ec="k", label="house, 3,000 sq ft"),
        Patch(fc="#7ddc5a", alpha=0.5, label="lawn, 20 ft around house"),
        Patch(fc="#b36b00", ec="k", label="septic field + reserve, 5,000 sq ft"),
        plt.Line2D([], [], marker="s", ls="", ms=9, c="#6b3f00", label="septic tank"),
        plt.Line2D([], [], marker="o", ls="", ms=11, c="#ff2bd6", mec="white", label="well, at the pond"),
        Patch(fc="#00b4ff", alpha=0.8, ec="k", label=f"most pond the rules allow: {ac(pond):.2f} ac"),
    ], loc="upper left", bbox_to_anchor=(0, -0.005), ncol=2, fontsize=10, frameon=False)

    tx.text(0, 1, "Minimum distances", fontsize=17, weight="bold", va="top")
    y = 0.95
    for thing, dist, src in RULES:
        if thing:
            y -= 0.012
            tx.plot([0, 1], [y + 0.006, y + 0.006], c="0.8", lw=0.8)
        tx.text(0, y, thing, fontsize=12.5, weight="bold", va="top")
        tx.text(0.33, y, dist, fontsize=12.5, va="top")
        tx.text(0.33, y - 0.026, src, fontsize=9.5, va="top", color="0.4")
        y -= 0.055
    tx.text(0, y - 0.01,
            "Septic assumes an engineered system: the soil survey maps this block as\n"
            "Nimmo, with the wet-season water table ~6 in down. The city requires a\n"
            "reserve field the same size as the main one. The 50 ft wetland strip\n"
            "applies only if the city counts Rick's wetland under its own definition.",
            fontsize=10.5, va="top", color="0.25")
    fig.suptitle("1832 Munden Point Rd: trees | house | pond, in the green block",
                 fontsize=18, weight="bold")
    fig.savefig(OUT / "pond_budget.png", dpi=100)
    print(f"wrote {OUT / 'pond_budget.png'}")


if __name__ == "__main__":
    main()
