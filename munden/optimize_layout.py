#!/usr/bin/env python3
"""
Space optimisation over all of Rick's upland: where do the house, septic and
well go so that the most ground is left for pond?

The house runs parallel to the ditch with its west wall at the 50 ft minimum
from the wetland (Matt's call); only how far north or south it sits is searched. The septic
(field + the reserve the city requires) can go anywhere in the upland: only a
sewer line has to reach it, so it is pushed wherever its 50 ft pond clearance
costs least, e.g. up the north panhandle. The tank sits beside the field (long
sewer line from the house, grinder pump if needed), so its 50 ft ring overlaps
the field's. The well sits on the pond's edge; it only needs 100 ft from the
field and 50 ft from the tank, which costs no pond.

Brute force on a 3 m grid, every house x field placement, a few field shapes.

    source ../.venv/bin/activate && python optimize_layout.py
"""
import itertools
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep

from common import ACRE_M2, CACHE, M_TO_FT, OUT, parcel, rick
from pond_budget import (FIELD_TO_DITCH, FIELD_TO_HOUSE, FIELD_TO_LINE, FIELD_TO_POND,
                         FIELD_TO_WELL, HOUSE_TO_LINE, POND_TO_LINE, TANK_TO_POND,
                         TANK_TO_WELL, WELL_TO_HOUSE, WETLAND_STRIP, largest)

FT = 1 / M_TO_FT
HOUSE_FT = (40, 75)          # 3,000 sq ft, one story, long side along the ditch
YARD_FT = 15                 # working room around the house
FIELD_SQFT = 7000            # Matt's ~3,500 sq ft oval + a same-size reserve
FIELD_SHAPES_FT = [(35, 200), (50, 140), (70, 100)]
DRIVE_FT = 12
GRID_M = 3


def main():
    feats, _ = rick()
    par, _ = parcel()
    up = feats["upland"]
    line = par.exterior
    wet = unary_union([w for w in getattr(feats["wetland"], "geoms", [feats["wetland"]])
                       if w.buffer(-3).area > 0])      # drop the east tracing sliver
    strip = wet.buffer(WETLAND_STRIP * FT)
    ditch = feats["ditch"]
    edge = line.buffer(6 * FT)
    band = line.buffer((6 + DRIVE_FT) * FT).difference(edge).intersection(par)
    pond_room = {"no": up.difference(line.buffer(POND_TO_LINE * FT))}
    pond_room["yes"] = pond_room["no"].difference(strip)

    x0, y0, x1, y1 = up.bounds
    grid = [(x, y) for x in np.arange(x0, x1, GRID_M) for y in np.arange(y0, y1, GRID_M)]
    up_p = prep(up)
    houses = []
    hw, hl = HOUSE_FT[0] * FT, HOUSE_FT[1] * FT
    for d in np.arange(0, ditch.length, GRID_M):
        p, q = ditch.interpolate(d), ditch.interpolate(min(d + 10, ditch.length))
        t = np.array([q.x - p.x, q.y - p.y])
        if not np.hypot(*t):
            continue
        t /= np.hypot(*t)
        n = np.array([t[1], -t[0]]) if t[1] > 0 else np.array([-t[1], t[0]])  # east
        corners = lambda off: [(p.x + n[0] * (off + i * hw) + t[0] * j * hl,
                                p.y + n[1] * (off + i * hw) + t[1] * j * hl)
                               for i, j in [(0, 0), (1, 0), (1, 1), (0, 1)]]
        lo, hi = 0.0, 60.0               # slide east until the west wall is 50 ft out
        for _ in range(25):
            mid = (lo + hi) / 2
            if Polygon(corners(mid)).distance(wet) < WETLAND_STRIP * FT:
                lo = mid
            else:
                hi = mid
        H = Polygon(corners(hi))
        if up_p.contains(H) and H.distance(line) >= HOUSE_TO_LINE * FT:
            houses.append(H)
    # --in-block: keep the house inside the green block Matt circled
    tag = ""
    if "--in-block" in sys.argv:
        from pond_budget import loop_polygon
        blk = up.intersection(loop_polygon())
        houses = [H for H in houses if blk.contains(H.centroid)]
        tag = "_in_block"
    fields = []
    for a, b in FIELD_SHAPES_FT:
        for w, h in [(a * FT, b * FT), (b * FT, a * FT)]:
            for x, y in grid[::2]:
                F = box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
                if (up_p.contains(F) and F.distance(ditch) >= FIELD_TO_DITCH * FT
                        and F.distance(line) >= FIELD_TO_LINE * FT):
                    fields.append(F)
    print(f"{len(houses)} house spots x {len(fields)} septic spots")

    def drive_for(H):
        yy0, yy1 = H.centroid.y - DRIVE_FT / 2 * FT, H.centroid.y + DRIVE_FT / 2 * FT
        down = band.intersection(box(H.bounds[2], y0 - 10, x1 + 10, yy1))
        spur = box(H.bounds[2], yy0, x1 + 10, yy1).intersection(par).difference(edge)
        return unary_union([down, spur])

    results = {}
    for key in ("no", "yes"):
        room = pond_room[key]
        best = None
        drives = {}
        for H, F in itertools.product(houses, fields):
            if H.distance(F) < FIELD_TO_HOUSE * FT:
                continue
            a, _ = nearest_points(F.boundary, H)       # tank: beside the field
            v = np.array([H.centroid.x - a.x, H.centroid.y - a.y])
            v /= np.hypot(*v)
            tank = Point(a.x + v[0] * 10 * FT, a.y + v[1] * 10 * FT)
            keep_out = unary_union([F.buffer(FIELD_TO_POND * FT), tank.buffer(TANK_TO_POND * FT),
                                    H.buffer(YARD_FT * FT, join_style=2)])
            # cheap upper bound before building the drive
            if best and room.difference(keep_out).area <= best["pond"].area:
                continue
            if id(H) not in drives:
                drives[id(H)] = drive_for(H)
            pond = room.difference(keep_out).difference(drives[id(H)])
            if best is None or pond.area > best["pond"].area:
                best = dict(house=H, field=F, tank=tank, pond=pond, drive=drives[id(H)])
        # well: on the pond's edge, legal, nearest the house
        P = best["pond"]
        cands = [pt for g in getattr(P, "geoms", [P])
                 for pt in (g.exterior.interpolate(t, normalized=True)
                            for t in np.linspace(0, 1, 150))]
        ok = [w for w in cands if w.distance(best["field"]) >= FIELD_TO_WELL * FT
              and w.distance(best["tank"]) >= TANK_TO_WELL * FT
              and w.distance(best["house"]) >= WELL_TO_HOUSE * FT
              and w.distance(wet) >= 10 * FT]
        best["well"] = min(ok, key=lambda w: w.distance(best["house"]))
        results[key] = best
        print(f"pond may reach wetland: {key:3s} | pond {P.area / ACRE_M2:.2f} ac, "
              f"biggest piece {largest(P).area / ACRE_M2:.2f} ac")

    ac = lambda g: g.area / ACRE_M2
    (OUT / f"optimize_layout{tag}.json").write_text(json.dumps({
        k: {"pond_ac": ac(r["pond"]), "biggest_piece_ac": ac(largest(r["pond"])),
            "house_to_field_ft": r["house"].distance(r["field"]) * M_TO_FT,
            "well_to_house_ft": r["well"].distance(r["house"]) * M_TO_FT}
        for k, r in results.items()}, indent=1))

    img = mpimg.imread(CACHE / "aerial.jpg")
    ext = json.loads((CACHE / "aerial.json").read_text())
    halo = [pe.withStroke(linewidth=4, foreground="black")]
    fig, axes = plt.subplots(1, 2, figsize=(16, 13))
    for ax, key in zip(axes, ["no", "yes"]):
        r = results[key]
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
        for g in getattr(r["pond"], "geoms", [r["pond"]]):
            ax.fill(*g.exterior.xy, fc="#00b4ff", alpha=0.85, ec="k", lw=1)
        for g in getattr(r["drive"], "geoms", [r["drive"]]):
            ax.fill(*g.exterior.xy, fc="#cccccc", ec="k", lw=0.8)
        ax.fill(*r["house"].exterior.xy, fc="#e8211b", ec="k", lw=1.5)
        ax.fill(*r["field"].exterior.xy, fc="#b36b00", ec="k", lw=1.5)
        ax.plot(*LineString([r["house"].centroid, r["tank"]]).xy, c="#6b3f00", lw=2, ls="--")
        ax.plot(r["tank"].x, r["tank"].y, "s", ms=9, c="#6b3f00", mec="white")
        ax.plot(r["well"].x, r["well"].y, "o", ms=12, c="#ff2bd6", mec="white", mew=2)
        c = largest(r["pond"]).centroid
        ax.text(c.x, c.y, f"POND\n{ac(r['pond']):.2f} ac", fontsize=16, weight="bold",
                ha="center", va="center", color="white", path_effects=halo)
        bb = up.buffer(30).bounds
        ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3])
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title("Pond may go right up to the wetland" if key == "no" else
                     "Pond also stays 50 ft from the wetland (red)", fontsize=14, weight="bold")
    fig.legend(handles=[
        Patch(fc="none", ec="#ff8c00", lw=2.5, label=f"Rick's upland: {ac(up):.2f} ac"),
        Patch(fc="#2e8b3e", alpha=0.3, label="Rick's wetland"),
        Patch(fc="#e8211b", ec="k", label="house, 3,000 sq ft, 50 ft from wetland"),
        Patch(fc="#b36b00", ec="k", label="septic field + reserve, 7,000 sq ft"),
        plt.Line2D([], [], c="#6b3f00", ls="--", label="sewer line house -> tank"),
        plt.Line2D([], [], marker="s", ls="", c="#6b3f00", label="septic tank"),
        plt.Line2D([], [], marker="o", ls="", ms=10, c="#ff2bd6", mec="white", label="well, on the pond"),
        Patch(fc="#cccccc", ec="k", label="driveway"),
        Patch(fc="#00b4ff", ec="k", label="pond: 50 ft from septic + tank, 25 ft from property lines"),
    ], loc="lower center", ncol=3, fontsize=11, frameon=False)
    fig.suptitle("1832 Munden Point Rd: best arrangement for the most pond", fontsize=17,
                 weight="bold")
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    fig.savefig(OUT / f"optimize_layout{tag}.png", dpi=100)
    print(f"wrote {OUT / f'optimize_layout{tag}.png'}")


if __name__ == "__main__":
    main()
