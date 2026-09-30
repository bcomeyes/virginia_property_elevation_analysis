#!/usr/bin/env python3
"""
The grounds for each ask, same #1-#4 as proposal_map.png. Two panels:

  left   1 m lidar (2023): ground height compared with the wetland's middle
         height. Brown = higher, teal = lower.
  right  county soil survey (SSURGO): which soils are mapped where.

Rick's upland outline and data points on both, so each ask can be compared
with what he found next to it.

    source ../.venv/bin/activate && python evidence_map.py
"""
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.patches import Patch
from rasterio.features import geometry_mask
from scipy import ndimage as nd

from common import CACHE, CRS, M_TO_FT, OUT, dem_path, parcel, rick
from proposal_map import ASKS

SOIL_COLORS = {"7": "#c8913a", "19": "#f0d27a", "13": "#c9dfa0",
               "24": "#8fb8cf", "29": "#4a78a8"}
SOIL_WORDS = {"7": "Bojac: well drained",
              "19": "Munden: moderately well drained",
              "13": "Dragston: somewhat poorly drained",
              "24": "Nimmo: poorly drained (wetland soil)",
              "29": "Portsmouth: very poorly drained (wetland soil)"}


def draw_common(ax, feats, pts, par, cands, halo):
    ax.plot(*par.exterior.xy, c="k", lw=2)
    for g in getattr(feats["upland"], "geoms", [feats["upland"]]):
        ax.plot(*g.exterior.xy, c="#ff8c00", lw=3)
    ax.plot(*feats["ditch"].xy, c="#1e6fff", lw=3)
    for cid, num, col in ASKS:
        g = cands.loc[cid].geometry
        for part in getattr(g, "geoms", [g]):
            ax.plot(*part.exterior.xy, c="k", lw=5)
            ax.plot(*part.exterior.xy, c=col, lw=3, ls="--" if cid == "H2" else "-")
        c = g.representative_point()
        if cid == "S1":
            from shapely.geometry import box
            b = g.bounds
            c = g.intersection(box(b[0], b[1], b[0] + 45, b[3])).representative_point()
        ax.text(c.x, c.y, f"#{num}", color=col, fontsize=22, weight="bold",
                ha="center", va="center", path_effects=halo)
    for _, p in pts.iterrows():
        up = p.rick == "upland"
        ax.plot(p.geometry.x, p.geometry.y, "o", ms=11, mec="k", mew=1.5,
                c="#ff8c00" if up else "#2e8b3e")
        ax.text(p.geometry.x + 6, p.geometry.y + 4, str(p.point), color="white",
                fontsize=12, weight="bold", path_effects=halo)
    b = par.buffer(30).bounds
    ax.set_xlim(b[0], b[2])
    ax.set_ylim(b[1], b[3])
    ax.set_xticks([])
    ax.set_yticks([])


def main():
    feats, pts = rick()
    par, _ = parcel()
    cands = gpd.read_file(OUT / "candidates.geojson").to_crs(CRS).set_index("id")
    soil = gpd.read_file(CACHE / "soils.geojson")
    halo = [pe.withStroke(linewidth=4, foreground="black")]

    with rasterio.open(dem_path("2023")) as s:
        a = s.read(1) * M_TO_FT
        T = s.transform
    ext = (T.c, T.c + a.shape[1] * T.a, T.f + a.shape[0] * T.e, T.f)
    sm = nd.gaussian_filter(np.nan_to_num(a), 3)
    wet = ~geometry_mask([feats["wetland"]], a.shape, T)
    rel = sm - np.median(sm[wet])
    rel[geometry_mask([par.buffer(30)], a.shape, T)] = np.nan

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 13))

    im = ax1.imshow(rel, extent=ext, cmap="BrBG_r", vmin=-1.5, vmax=1.5)
    draw_common(ax1, feats, pts, par, cands, halo)
    cb = fig.colorbar(im, ax=ax1, shrink=0.55, pad=0.01)
    cb.set_label("feet higher (brown) or lower (teal) than the\nmiddle height of Rick's wetland", fontsize=11)
    ax1.set_title("1 m lidar (USGS 2023): ground height\n"
                  "The whole lot spans only ~2 ft outside the road edge", fontsize=13)

    for _, r in soil.iterrows():
        for g in getattr(r.geometry, "geoms", [r.geometry]):
            ax2.fill(*g.exterior.xy, fc=SOIL_COLORS.get(str(r.musym), "#ccc"),
                     ec="0.35", lw=0.8)
    draw_common(ax2, feats, pts, par, cands, halo)
    handles = [Patch(fc=SOIL_COLORS[k], ec="0.35", label=v) for k, v in SOIL_WORDS.items()]
    ax2.legend(handles=handles, loc="upper left", fontsize=10.5, framealpha=0.92,
               title="County soil survey (coarse)", title_fontsize=11)
    ax2.set_title("Soil survey (USDA SSURGO): mapped soil types\n"
                  "Drawn at ~1:24,000 -- boundaries are approximate", fontsize=13)

    key = [Patch(fc="none", ec="#ff8c00", lw=3, label="Rick's upland"),
           Patch(fc="none", ec="#1e6fff", lw=3, label="ditch"),
           plt.Line2D([], [], marker="o", ls="", ms=10, c="#ff8c00", mec="k", label="Rick point: upland"),
           plt.Line2D([], [], marker="o", ls="", ms=10, c="#2e8b3e", mec="k", label="Rick point: wetland")]
    key += [Patch(fc="none", ec=col, lw=3, ls="--" if cid == "H2" else "-", label=f"ask #{num}")
            for cid, num, col in sorted(ASKS, key=lambda x: x[1])]
    fig.legend(handles=key, loc="lower center", ncol=8, fontsize=11, frameon=False)
    fig.suptitle("1832 Munden Point Rd: what the data shows under each ask", fontsize=16, weight="bold")
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.savefig(OUT / "evidence_map.png", dpi=100)
    print(f"wrote {OUT / 'evidence_map.png'}")


if __name__ == "__main__":
    main()
