"""
Shared elevation palette for the NC high-ground project.

Two design rules, both learned the hard way:

1. NOTHING IS BLUE. In a coastal map the eye reads blue as water. The previous
   scheme put blue at the top of the elevation ramp, so the safest, highest
   ground rendered as ocean. Elevation runs warm (danger, low) -> green (first
   safe band) -> brown (high, topographic convention). Blue is reserved for
   water and water only, and we don't even use it -- water is left blank.

2. WATER IS EXPLICIT. It gets its own color and its own legend entry, so it can
   never be confused with an elevation class. Anything with no ground return
   renders white.

Band spacing is deliberately uneven: two-foot steps from 0 to 12 where the whole
decision lives, coarser above 30 where the difference stops mattering.
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch

# Fine at the bottom, coarse at the top. The 20 ft line is a band edge on purpose.
BREAKS_FT = [0, 2, 4, 6, 8, 10, 12, 15, 20, 25, 30, 40, 60, 250]

BAND_COLORS = [
    "#7f0000",  #  0-2   near-black red   underwater at any surge
    "#b30000",  #  2-4
    "#d7301f",  #  4-6                    the Sarasota in-laws' 7 ft pad
    "#ef6548",  #  6-8
    "#fc8d59",  #  8-10
    "#fdbb84",  # 10-12
    "#fdd49e",  # 12-15
    "#fee8c8",  # 15-20  palest warm -- still a warning
    # ---- 20 ft threshold: hard hue jump, warm to cool ----
    "#78c679",  # 20-25  first safe band
    "#41ab5d",  # 25-30
    "#a68a64",  # 30-40  into topographic browns
    "#8a6f4a",  # 40-60
    "#5c4630",  # 60+
]

WATER_COLOR = "#ffffff"   # blank. not blue, not gray, not competing with a band.
THRESHOLD_FT = 20.0


def elevation_cmap():
    """Returns (cmap, norm) for banded elevation in FEET."""
    cmap = ListedColormap(BAND_COLORS)
    cmap.set_bad(WATER_COLOR)          # NaN -> water/no-return
    norm = BoundaryNorm(BREAKS_FT, cmap.N, clip=True)
    return cmap, norm


def legend_handles(include_water=True):
    """Legend patches. Water first so it reads as a separate category."""
    h = []
    if include_water:
        h.append(Patch(facecolor=WATER_COLOR, edgecolor="#999999",
                       linewidth=0.8, label="water / no return"))
    for i in range(len(BAND_COLORS)):
        lo, hi = BREAKS_FT[i], BREAKS_FT[i + 1]
        lab = f"{lo:g}\u2013{hi:g} ft" if i < len(BAND_COLORS) - 1 else f"{lo:g}+ ft"
        if lo == THRESHOLD_FT:
            lab += "   \u25c0 threshold"
        h.append(Patch(facecolor=BAND_COLORS[i], edgecolor="none", label=lab))
    return h


def plot_county(arr_ft, county, ax=None, extent=None, show_legend=True,
                threshold_contour=True):
    """
    Render one county's elevation array (already in FEET, water as NaN).

    Draws a thin dark contour at the threshold so the 20 ft line is a line, not
    just a color change -- readable even on a phone screen.
    """
    if ax is None:
        _, ax = plt.subplots(figsize=(11, 9))

    cmap, norm = elevation_cmap()
    masked = np.ma.masked_invalid(arr_ft)
    ax.imshow(masked, cmap=cmap, norm=norm, extent=extent,
              interpolation="nearest", origin="upper")

    if threshold_contour:
        filled = np.nan_to_num(arr_ft, nan=-999.0)
        ax.contour(filled, levels=[THRESHOLD_FT], colors="#1a1a1a",
                   linewidths=0.6, extent=extent, origin="upper")

    pct = float(np.nansum(arr_ft >= THRESHOLD_FT)) / max(np.sum(~np.isnan(arr_ft)), 1)
    ax.set_title(f"{county} County  \u2014  {pct*100:.1f}% of land \u2265 {THRESHOLD_FT:g} ft",
                 fontsize=13, loc="left")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    if show_legend:
        ax.legend(handles=legend_handles(), loc="center left",
                  bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=8)
    return ax


def preview(path="palette_preview.png"):
    """Synthetic ramp to eyeball the palette without loading a DEM."""
    grad = np.tile(np.linspace(0, 70, 400), (60, 1))
    grad[:12, :80] = np.nan                       # a patch of "water"
    fig, ax = plt.subplots(figsize=(12, 3.2))
    plot_county(grad, "Palette check", ax=ax, threshold_contour=True)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    print(f"[ok] {path}")
    return fig
