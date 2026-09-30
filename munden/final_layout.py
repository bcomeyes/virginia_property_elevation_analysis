#!/usr/bin/env python3
"""
Matt's layout, revision of my_layout2_tip:

  septic   his oval, slid to the north tip of the arm (my_layout.py --tip logic)
  house    his drawn box, moved as far south-west as it goes while staying
           50 ft from the wetland and 20 ft from the property line
  driveway from the road north along the east property line (the farthest
           ground from the foot of the L), then west into the house's south end
  pond     as big as it can be, one piece, not up the arm, with a 15 ft
           excavator lane of land all the way around it
  well     on the lane beside the house

    source ../.venv/bin/activate && python final_layout.py
"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from shapely.affinity import translate
from shapely.geometry import Point, box
from shapely.ops import nearest_points, unary_union

from common import ACRE_M2, CACHE, M_TO_FT, OUT, parcel, rick
from my_layout import read_drawing
from pond_budget import (FIELD_TO_POND, FIELD_TO_WELL, HOUSE_TO_LINE, POND_TO_LINE,
                         TANK_TO_POND, TANK_TO_WELL, WELL_TO_HOUSE, WETLAND_STRIP, largest)

FT = 1 / M_TO_FT
LANE_FT = 15                 # excavator lane around the whole pond
DRIVE_FT = 12
DRIVE_TO_LINE_FT = 5
ARM_BASE_Y = 4048862.9       # parcel vertex where the east line steps in: the arm starts here


def build(lane_ft=LANE_FT, arm_base_y=ARM_BASE_Y):
    """All the geometry for one version of the layout, as a dict."""
    feats, _ = rick()
    par, _ = parcel()
    up = feats["upland"]
    line = par.exterior
    ditch = feats["ditch"]
    wet = unary_union([w for w in getattr(feats["wetland"], "geoms", [feats["wetland"]])
                       if w.buffer(-3).area > 0])
    house0, septic0, _ = read_drawing("my_layout2")

    # septic: slide to the north tip, 20 ft from the ditch, 5 ft from the line
    fits = lambda g: (up.contains(g) and g.distance(ditch) >= 20 * FT
                      and g.distance(line) >= 5 * FT)
    septic = septic0
    for dy in np.arange(0, 400, 1.0):
        for dx in np.arange(-15, 15.5, 1.0):
            g = translate(septic0, dx, dy)
            if fits(g):
                septic = g
                break
    arm = up.intersection(box(0, ARM_BASE_Y, 1e7, 1e8))
    arm_septic_room = (arm.difference(ditch.buffer(20 * FT))
                       .difference(line.buffer(5 * FT)))

    # house: south-west-most legal spot for his box
    legal = lambda H: (up.contains(H) and H.distance(wet) >= WETLAND_STRIP * FT
                       and H.distance(line) >= HOUSE_TO_LINE * FT)
    spots = [translate(house0, dx, dy) for dx in np.arange(-40, 41, 1.0)
             for dy in np.arange(-80, 41, 1.0)]
    spots = [H for H in spots if legal(H)]
    house = min(spots, key=lambda H: H.centroid.x + H.centroid.y)   # most south-west

    # driveway: 5 ft inside the east line, road -> house latitude, then west to house
    edge = line.buffer(DRIVE_TO_LINE_FT * FT)
    band = line.buffer((DRIVE_TO_LINE_FT + DRIVE_FT) * FT).difference(edge).intersection(par)
    y_in = house.bounds[1] + DRIVE_FT / 2 * FT
    east_side = box(house.bounds[2] - 1, par.bounds[1] - 10, par.bounds[2] + 10, y_in + DRIVE_FT / 2 * FT)
    down = largest(band.intersection(east_side))
    spur = (box(house.centroid.x, y_in - DRIVE_FT / 2 * FT, par.bounds[2], y_in + DRIVE_FT / 2 * FT)
            .intersection(par).difference(edge).difference(house))
    drive = unary_union([down, spur])
    drive_clear_ft = drive.distance(wet) * M_TO_FT
    # how much of the drive runs closer than 50 ft to the wetland
    drive_close_ft = drive.intersection(wet.buffer(WETLAND_STRIP * FT)).area / (DRIVE_FT * FT) * M_TO_FT

    # tank beside the septic, toward the house
    a, _ = nearest_points(septic.boundary, house)
    v = np.array([house.centroid.x - a.x, house.centroid.y - a.y])
    v /= np.hypot(*v)
    tank = Point(a.x + v[0] * 10 * FT, a.y + v[1] * 10 * FT)

    lane = lane_ft * FT
    base = (up.buffer(-lane)                                   # lane on upland, not wetland
            .difference(box(0, arm_base_y, 1e7, 1e8))         # nothing up the arm
            .difference(line.buffer(max(POND_TO_LINE * FT, lane)))
            .difference(house.buffer(lane))
            .difference(drive.buffer(lane))
            .difference(septic.buffer(FIELD_TO_POND * FT))
            .difference(tank.buffer(TANK_TO_POND * FT)))
    ponds = {"no": largest(base),
             "yes": largest(base.difference(wet.buffer(WETLAND_STRIP * FT)))}

    wells = {}
    for k, P in ponds.items():
        ring = P.buffer(lane / 2).exterior                      # middle of the lane
        cands = [ring.interpolate(t, normalized=True) for t in np.linspace(0, 1, 400)]
        ok = [w for w in cands if w.distance(septic) >= FIELD_TO_WELL * FT
              and w.distance(tank) >= TANK_TO_WELL * FT
              and w.distance(house) >= WELL_TO_HOUSE * FT and up.contains(w)]
        wells[k] = min(ok, key=lambda w: w.distance(house))
    return dict(feats=feats, par=par, up=up, line=line, ditch=ditch, wet=wet, house=house,
                septic=septic, tank=tank, drive=drive, ponds=ponds, wells=wells, lane=lane,
                drive_clear_ft=drive_clear_ft, drive_close_ft=drive_close_ft,
                arm_septic_room=arm_septic_room)


def main():
    b = build()
    (par, up, ditch, wet, house, septic, tank, drive, ponds, wells, lane) = (
        b[k] for k in ("par", "up", "ditch", "wet", "house", "septic", "tank", "drive",
                       "ponds", "wells", "lane"))
    drive_clear_ft, drive_close_ft, arm_septic_room = (
        b["drive_clear_ft"], b["drive_close_ft"], b["arm_septic_room"])
    ac = lambda g: g.area / ACRE_M2
    nums = {"pond_ac": ac(ponds["no"]), "pond_ac_50ft_from_wetland": ac(ponds["yes"]),
            "house_to_wetland_ft": house.distance(wet) * M_TO_FT,
            "house_sqft": house.area * M_TO_FT ** 2,
            "drive_closest_to_wetland_ft": drive_clear_ft,
            "drive_length_within_50ft_of_wetland_ft": drive_close_ft,
            "septic_sqft": septic.area * M_TO_FT ** 2,
            "arm_room_for_septic_sqft": arm_septic_room.area * M_TO_FT ** 2,
            "sewer_line_ft": house.distance(tank) * M_TO_FT,
            "well_to_house_ft": wells["no"].distance(house) * M_TO_FT}
    for k, val in nums.items():
        print(f"{k:42s} {val:,.2f}")
    (OUT / "final_layout.json").write_text(json.dumps(nums, indent=1))

    img = mpimg.imread(CACHE / "aerial.jpg")
    ext = json.loads((CACHE / "aerial.json").read_text())
    halo = [pe.withStroke(linewidth=4, foreground="black")]
    strip = wet.buffer(WETLAND_STRIP * FT).intersection(up)
    fig, axes = plt.subplots(1, 2, figsize=(16, 13))
    for ax, key in zip(axes, ["no", "yes"]):
        P = ponds[key]
        ax.imshow(img, extent=ext)
        for g in getattr(wet, "geoms", [wet]):
            ax.fill(*g.exterior.xy, fc="#2e8b3e", alpha=0.3, ec="none")
        ax.plot(*par.exterior.xy, c="white", lw=2)
        ax.plot(*up.exterior.xy, c="#ff8c00", lw=2.5)
        ax.plot(*ditch.xy, c="#1e6fff", lw=3)
        if key == "yes":
            for g in getattr(strip, "geoms", [strip]):
                ax.fill(*g.exterior.xy, fc="#ff3030", alpha=0.3, hatch="//", ec="none")
        L = P.buffer(lane, join_style=1).difference(P)
        for g in getattr(L, "geoms", [L]):
            ax.fill(*g.exterior.xy, fc="#d9c27a", alpha=0.75, ec="none")
        ax.fill(*P.exterior.xy, fc="#00b4ff", alpha=0.9, ec="k", lw=1.2)
        for g in getattr(drive, "geoms", [drive]):
            ax.fill(*g.exterior.xy, fc="#cccccc", ec="k", lw=0.8)
        ax.fill(*house.exterior.xy, fc="#e8211b", ec="k", lw=1.5)
        ax.fill(*septic.exterior.xy, fc="#b36b00", ec="k", lw=1.5)
        ax.plot(tank.x, tank.y, "s", ms=9, c="#6b3f00", mec="white")
        ax.plot([house.centroid.x, tank.x], [house.bounds[3], tank.y], c="#6b3f00", lw=2, ls="--")
        ax.plot(wells[key].x, wells[key].y, "o", ms=12, c="#ff2bd6", mec="white", mew=2)
        c = P.centroid
        ax.text(c.x, c.y, f"POND\n{ac(P):.2f} ac", fontsize=16, weight="bold",
                ha="center", va="center", color="white", path_effects=halo)
        bb = up.buffer(30).bounds
        ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3])
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title("Pond may reach the wetland (lane between)" if key == "no" else
                     "Pond also stays 50 ft from the wetland (red)", fontsize=14, weight="bold")
    fig.legend(handles=[
        Patch(fc="none", ec="#ff8c00", lw=2.5, label=f"Rick's upland: {ac(up):.2f} ac"),
        Patch(fc="#2e8b3e", alpha=0.3, label="Rick's wetland"),
        Patch(fc="#e8211b", ec="k", label="your house box, 50 ft from wetland, pushed south-west"),
        Patch(fc="#b36b00", ec="k", label="your septic, at the tip of the arm"),
        plt.Line2D([], [], c="#6b3f00", ls="--", label="sewer line to tank"),
        plt.Line2D([], [], marker="o", ls="", ms=10, c="#ff2bd6", mec="white", label="well"),
        Patch(fc="#cccccc", ec="k", label="driveway from the road"),
        Patch(fc="#d9c27a", alpha=0.75, label="15 ft excavator lane"),
        Patch(fc="#00b4ff", ec="k", label="pond"),
    ], loc="lower center", ncol=3, fontsize=11, frameon=False)
    fig.suptitle("1832 Munden Point Rd: your layout, revised", fontsize=17, weight="bold")
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    fig.savefig(OUT / "final_layout.png", dpi=100)
    print(f"wrote {OUT / 'final_layout.png'}")


if __name__ == "__main__":
    main()
