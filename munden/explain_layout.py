#!/usr/bin/env python3
"""
One picture of why final_layout.png's pond is 0.86 ac: the same layout four
ways (lane on/off, pond up the arm or not), each drawn over the biggest
version so the lost ground shows, plus a panel on why the driveway cannot stay
50 ft from the foot of the L.

    source ../.venv/bin/activate && python explain_layout.py
"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from common import ACRE_M2, CACHE, M_TO_FT, OUT
from final_layout import ARM_BASE_Y, LANE_FT, build
from pond_budget import WETLAND_STRIP

FT = 1 / M_TO_FT
HALO = [pe.withStroke(linewidth=4, foreground="black")]


def fill(ax, g, **kw):
    for p in getattr(g, "geoms", [g]):
        if p.geom_type == "Polygon" and not p.is_empty:
            ax.fill(*p.exterior.xy, **kw)


def base_map(ax, b, img, ext):
    ax.imshow(img, extent=ext)
    fill(ax, b["wet"], fc="#2e8b3e", alpha=0.3, ec="none")
    ax.plot(*b["par"].exterior.xy, c="white", lw=1.5)
    ax.plot(*b["up"].exterior.xy, c="#ff8c00", lw=2)
    ax.plot(*b["ditch"].xy, c="#1e6fff", lw=2.5)
    bb = b["up"].buffer(20).bounds
    ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3])
    ax.set_xticks([]); ax.set_yticks([])


def main():
    img = mpimg.imread(CACHE / "aerial.jpg")
    ext = json.loads((CACHE / "aerial.json").read_text())
    versions = [  # title, lane ft, arm allowed
        ("No lane, pond up the arm", 0, True),
        ("No lane, nothing up the arm", 0, False),
        ("15 ft lane, pond up the arm", LANE_FT, True),
        ("YOUR RULES: 15 ft lane,\nnothing up the arm", LANE_FT, False),
    ]
    builds = [build(lane, 1e9 if arm else ARM_BASE_Y) for _, lane, arm in versions]
    biggest = builds[0]["ponds"]["no"]
    ac = lambda g: g.area / ACRE_M2

    fig, axes = plt.subplots(1, 5, figsize=(26, 12))
    for ax, (title, lane, arm), b in zip(axes, versions, builds):
        base_map(ax, b, img, ext)
        P = b["ponds"]["no"]
        fill(ax, biggest.difference(P), fc="#ff3030", alpha=0.55, ec="none")   # lost
        if lane:
            fill(ax, P.buffer(b["lane"]).difference(P), fc="#d9c27a", alpha=0.85, ec="none")
        fill(ax, P, fc="#00b4ff", alpha=0.9, ec="k", lw=1)
        fill(ax, b["drive"], fc="#cccccc", ec="k", lw=0.6)
        fill(ax, b["house"], fc="#e8211b", ec="k", lw=1.2)
        fill(ax, b["septic"], fc="#b36b00", ec="k", lw=1.2)
        if not arm:
            ax.axhline(ARM_BASE_Y, c="white", lw=2, ls="--")
            ax.text(b["up"].bounds[2] + 3, ARM_BASE_Y + 4, "arm starts", color="white",
                    fontsize=10, weight="bold", ha="right", path_effects=HALO)
        c = P.centroid
        ax.text(c.x, c.y, f"{ac(P):.2f} ac", fontsize=18, weight="bold", ha="center",
                va="center", color="white", path_effects=HALO)
        lost = ac(biggest) - ac(P)
        ax.set_title(title + (f"\n{ac(P):.2f} ac  (−{lost:.2f})" if lost > 0.005
                              else f"\n{ac(P):.2f} ac  (most possible)"),
                     fontsize=14, weight="bold", color="#b00000" if "YOUR" in title else "k")

    # driveway panel
    ax = axes[4]
    b = builds[3]
    base_map(ax, b, img, ext)
    near = b["wet"].buffer(WETLAND_STRIP * FT)
    fill(ax, b["up"].difference(near), fc="#7ddc5a", alpha=0.55, ec="#2f7a1f", lw=1.5)
    fill(ax, b["up"].intersection(near), fc="#ff3030", alpha=0.35, ec="none")
    fill(ax, b["drive"].difference(near), fc="#cccccc", ec="k", lw=0.6)
    fill(ax, b["drive"].intersection(near), fc="#ffe600", ec="k", lw=0.6)
    fill(ax, b["house"], fc="#e8211b", ec="k", lw=1.2)
    ax.set_title(f"Driveway: {b['drive_close_ft']:.0f} ft of it is inside\n"
                 "50 ft of the wetland (yellow). No route avoids it.",
                 fontsize=14, weight="bold")

    fig.legend(handles=[
        Patch(fc="#00b4ff", ec="k", label="pond"),
        Patch(fc="#ff3030", alpha=0.55, label="pond lost vs. the biggest version (left)"),
        Patch(fc="#d9c27a", alpha=0.85, label="15 ft excavator lane"),
        Patch(fc="#e8211b", ec="k", label="house"),
        Patch(fc="#b36b00", ec="k", label="septic"),
        Patch(fc="#cccccc", ec="k", label="driveway"),
        Patch(fc="#7ddc5a", alpha=0.55, ec="#2f7a1f", label="upland 50+ ft from wetland"),
        Patch(fc="#ff3030", alpha=0.35, label="upland within 50 ft of wetland"),
        Patch(fc="#ffe600", ec="k", label="driveway within 50 ft of wetland"),
        Patch(fc="#2e8b3e", alpha=0.3, label="Rick's wetland"),
    ], loc="lower center", ncol=5, fontsize=12, frameon=False)
    fig.suptitle("1832 Munden Point Rd: where the pond goes, and what each rule costs",
                 fontsize=19, weight="bold")
    fig.tight_layout(rect=(0, 0.08, 1, 0.95))
    fig.savefig(OUT / "explain_layout.png", dpi=90)
    print(f"wrote {OUT / 'explain_layout.png'}")


if __name__ == "__main__":
    main()
