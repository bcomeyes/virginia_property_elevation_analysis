#!/usr/bin/env python3
"""
One plain-English map: what Rick found, and the three spots we'd ask him to
retest. Aerial photo background, like his Exhibit 2.

    source ../.venv/bin/activate && python proposal_map.py
"""
import json
import urllib.parse
import urllib.request

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from common import ACRE_M2, CACHE, CRS, M_TO_FT, OUT, parcel, rick

IMAGERY = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
           "World_Imagery/MapServer/export")

# The asks, in order. Keys are candidate ids from screen.py.
ASKS = [
    ("S1", "2", "#ff2bd6"),
    ("H3", "1", "#ffe600"),
    ("B1", "3", "#00e5ff"),
    ("H2", "4", "#b6ff3b"),     # Rick tested it (pt 8) -- drawn dashed
]
TEXT = {
    "1": "#1  ~{ac:.1f} ac  Same height as his upland\n      point 3, next to his upland,\n      never sampled",
    "2": "#2  ~{ac:.1f} ac  Soil map says well-drained\n      (Munden/Bojac); next to his\n      upland. Ask about the WEST end.",
    "3": "#3  ~{ac:.1f} ac  West bank of the ditch -- same\n      height as his upland on the\n      east bank. Ask: why one side only?",
    "4": "#4  ~{ac:.1f} ac  Highest ground in his wetland.\n      He tested it (point 8): wetland.\n      Ask only: was it a close call?",
}


def aerial(bounds):
    f = CACHE / "aerial.jpg"
    meta = CACHE / "aerial.json"
    if not f.exists() or not meta.exists():
        w = 1200                 # export caps each side near 2048 px
        h = int(w * (bounds[3] - bounds[1]) / (bounds[2] - bounds[0]))
        # f=json returns an empty href on this service, so ask for the image
        # directly at the bbox's exact aspect ratio -- then the extent is the bbox.
        q = urllib.parse.urlencode({
            "bbox": ",".join(map(str, bounds)), "bboxSR": CRS, "imageSR": CRS,
            "size": f"{w},{h}", "format": "jpg", "f": "image"})
        f.write_bytes(urllib.request.urlopen(IMAGERY + "?" + q, timeout=60).read())
        meta.write_text(json.dumps([bounds[0], bounds[2], bounds[1], bounds[3]]))
    return mpimg.imread(f), json.loads(meta.read_text())


def main():
    feats, pts = rick()
    par, _ = parcel()
    cands = gpd.read_file(OUT / "candidates.geojson").to_crs(CRS).set_index("id")
    bounds = par.buffer(45).bounds
    img, ext = aerial(bounds)

    halo = [pe.withStroke(linewidth=4, foreground="black")]
    fig, ax = plt.subplots(figsize=(13, 15))
    ax.imshow(img, extent=ext)

    # Rick's findings: wetland = green tint, upland = orange fill
    ax.fill(*feats["study"].exterior.xy, fc="#2e8b3e", alpha=0.35, ec="none")
    for g in getattr(feats["upland"], "geoms", [feats["upland"]]):
        ax.fill(*g.exterior.xy, fc="#ff8c00", alpha=0.55, ec="#ff8c00", lw=2.5)
    ax.plot(*par.exterior.xy, c="white", lw=2.5)
    ax.plot(*feats["ditch"].xy, c="#1e6fff", lw=4)

    # Rick's data points
    for _, p in pts.iterrows():
        up = p.rick == "upland"
        ax.plot(p.geometry.x, p.geometry.y, "o", ms=13, mec="white", mew=2,
                c="#ff8c00" if up else "#2e8b3e")
        ax.text(p.geometry.x + 6, p.geometry.y + 4, str(p.point), color="white",
                fontsize=13, weight="bold", path_effects=halo)

    # Our asks
    for cid, num, col in ASKS:
        g = cands.loc[cid].geometry
        tested = cid == "H2"
        for part in getattr(g, "geoms", [g]):
            ax.fill(*part.exterior.xy, fc=col, alpha=0.30 if tested else 0.35,
                    ec=col, lw=4.5 if tested else 3.5, ls="--" if tested else "-")
        c = g.representative_point()
        if cid == "S1":                     # label the west end, the part we ask about
            from shapely.geometry import box
            b = g.bounds
            c = g.intersection(box(b[0], b[1], b[0] + 45, b[3])).representative_point()
        ax.text(c.x, c.y, f"#{num}", color=col, fontsize=26, weight="bold",
                ha="center", va="center", path_effects=halo)

    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_xticks([])
    ax.set_yticks([])
    x0, y0 = bounds[0] + 10, bounds[1] + 12
    ax.plot([x0, x0 + 200 / M_TO_FT], [y0, y0], c="white", lw=5)
    ax.text(x0, y0 + 6, "200 ft", color="white", fontsize=12, weight="bold",
            path_effects=halo)

    up_ac = feats["upland"].area / ACRE_M2
    handles = [
        Patch(fc="#ff8c00", alpha=0.7, label=f"RICK: upland (~{up_ac:.0f} ac, buildable)"),
        Patch(fc="#2e8b3e", alpha=0.5, label="RICK: wetland (~21 ac)"),
        Line2D([], [], c="#1e6fff", lw=4, label="RICK: ditch"),
        Line2D([], [], marker="o", ls="", ms=11, c="#ff8c00", mec="white",
               label="RICK: soil test point, found upland"),
        Line2D([], [], marker="o", ls="", ms=11, c="#2e8b3e", mec="white",
               label="RICK: soil test point, found wetland"),
        Line2D([], [], c="0.45", lw=2.5, label="property line (white on map)"),
    ]
    for cid, num, col in sorted(ASKS, key=lambda a: a[1]):
        ac = cands.loc[cid].geometry.area / ACRE_M2
        handles.append(Patch(fc=col, alpha=0.3 if cid == "H2" else 0.6, ec=col, lw=2,
                             ls="--" if cid == "H2" else "-", label=TEXT[num].format(ac=ac)))
    leg = ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.01),
                    ncol=2, fontsize=11.5, frameon=False,
                    title="Rick found (left)   /   We propose he retest (right)",
                    title_fontsize=13, labelspacing=1.0)
    leg.get_title().set_weight("bold")
    ax.set_title("1832 Munden Point Rd: Rick's delineation and four spots to ask about\n"
                 "If #1 and #2 pass: ~4 -> ~5 ac.   Plus #3: ~6.7 ac.   Plus #4: ~7.3 ac.",
                 fontsize=15, weight="bold")
    fig.savefig(OUT / "proposal_map.png", dpi=110, bbox_inches="tight")
    print(f"wrote {OUT / 'proposal_map.png'}")


if __name__ == "__main__":
    main()
