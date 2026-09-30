#!/usr/bin/env python3
"""
The upland screen. Reads the DEM windows (fetch_dem.py) and soils (soils.py),
calibrates against Rick's nine points, flags candidate patches inside his
wetland, and writes everything to out/.

NOTHING HERE IS A DETERMINATION. It is a list of places to put a soil pit, each
with its evidence shown separately. There is no score: patches are ordered by
how many independent lines of evidence agree, then by size.

Three lines of evidence, each its own flag:

  high    Smoothed ground at least HIGH_FT above the wetland's median, in BOTH
          the 2013 and 2023 flights (each flight against its own median, which
          cancels the ~2 in datum offset between them). Holding in two flights
          ten years apart means it is real ground, not one flight's noise.
  soil    SSURGO map unit that is not a hydric soil (Munden, Bojac, Dragston).
  bank    West of the ditch, within the distance Rick's own upland reaches on
          the east side. If the ditch drains the east bank, it drains this one.

Calibration comes first and is reported regardless of what it says. If
elevation does not separate Rick's upland points from his wetland points, the
'high' flag is not calibrated against anything and the summary says so.

    source ../.venv/bin/activate && python screen.py
"""
import warnings

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask, shapes
from scipy import ndimage as nd
from scipy.stats import mannwhitneyu
from shapely.geometry import LineString, Point, shape
from shapely.ops import split, unary_union

from common import (ACRE_M2, CACHE, CRS, DITCH_HALF_WIDTH_M, M_TO_FT, OUT,
                    dem_path, parcel, rick)

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------- parameters
POINT_RADIUS_M = 5       # neighbourhood mean at each of Rick's points
SMOOTH_M = 3             # gaussian sigma: suppresses stumps, tip-up mounds and
                         # the odd shrub classed as ground, keeps 10 m features
HIGH_FT = 0.5            # 'high' flag. ~2x the smoothed flight-to-flight noise
                         # (printed at run time), so a flagged patch is well
                         # outside what the two flights disagree by
MIN_AC = 0.25            # standalone candidate
MIN_AC_ADJ = 0.10        # a patch touching Rick's upland extends it, so a
                         # smaller one still matters
NEAR_DITCH_M = DITCH_HALF_WIDTH_M + 3   # excluded: the ditch channel itself
NONHYDRIC_MAX_PCT = 50   # SSURGO map units with fewer hydric components
STRIP_PTS = [1, 2]       # Rick's upland points on the east-of-ditch strip


def load(k):
    with rasterio.open(dem_path(k)) as s:
        return s.read(1) * M_TO_FT, s.transform


def smooth(a, sigma):
    v = np.nan_to_num(a)
    w = (~np.isnan(a)).astype(float)
    return nd.gaussian_filter(v, sigma) / np.maximum(nd.gaussian_filter(w, sigma), 1e-6)


def mask(g, shp, T):
    return ~geometry_mask([g], shp, T)


def auc(x, a_mask, b_mask):
    """P(random a-pixel > random b-pixel). 0.5 = no separation."""
    a, b = x[a_mask], x[b_mask]
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    rng = np.random.default_rng(0)
    a, b = rng.choice(a, min(20000, len(a))), rng.choice(b, min(20000, len(b)))
    return mannwhitneyu(a, b).statistic / (len(a) * len(b))


def sides_of_ditch(feats):
    """Split the study area along the ditch. Returns (west, east) polygons."""
    d = list(feats["ditch"].coords)
    def ext(p, q, m=80):
        v = np.subtract(p[:2], q[:2])
        v = v / np.hypot(*v)
        return tuple(np.add(p[:2], v * m))
    line = LineString([ext(d[0], d[1])] + [c[:2] for c in d] + [ext(d[-1], d[-2])])
    parts = split(feats["study"].buffer(10), line).geoms
    parts = sorted(parts, key=lambda g: -g.area)
    # 'West' = the side holding the main body of Wetland #1 (Rick's point 7)
    pt7 = feats["_pts"].set_index("point").geometry[7]
    west = next(g for g in parts if g.contains(pt7))
    east = unary_union([g for g in parts if g is not west])
    return west, east


def main():
    feats, pts = rick()
    feats["_pts"] = pts
    par, props = parcel()
    soil = gpd.read_file(CACHE / "soils.geojson")

    raw = {}
    for k in ("2013", "2023"):
        raw[k], T = load(k)
    shp = raw["2023"].shape
    ext = (T.c, T.c + shp[1] * T.a, T.f + shp[0] * T.e, T.f)
    sm = {k: smooth(v, SMOOTH_M) for k, v in raw.items()}

    m_par = mask(par, shp, T)
    m_up = mask(feats["upland"], shp, T)
    m_wet = mask(feats["wetland"], shp, T)
    m_ditchzone = mask(feats["ditch"].buffer(NEAR_DITCH_M), shp, T)
    wet_med = {k: float(np.nanmedian(sm[k][m_wet & ~m_ditchzone])) for k in sm}
    rel = {k: sm[k] - wet_med[k] for k in sm}
    noise = np.nanstd((rel["2023"] - rel["2013"])[m_par & ~m_ditchzone])

    # Distance to the ditch centreline, metres
    m_dline = mask(feats["ditch"].buffer(0.5), shp, T)
    dist = nd.distance_transform_edt(~m_dline)
    # Height above the ditch: elevation minus the ditch bottom at the nearest
    # ditch pixel (2023 flight, which shows the maintained channel)
    _, (ri, ci) = nd.distance_transform_edt(~m_dline, return_indices=True)
    ditch_bottom = nd.minimum_filter(np.where(m_dline, sm["2023"], np.inf), size=9)
    hand = sm["2023"] - ditch_bottom[ri, ci]

    # ------------------------------------------------------------ calibration
    cal = []
    for _, p in pts.iterrows():
        mm = mask(p.geometry.buffer(POINT_RADIUS_M), shp, T)
        row = {"point": p.point, "rick": p.rick}
        for k in ("2013", "2023"):
            row[f"elev_ft_{k}"] = float(np.nanmean(raw[k][mm]))
            row[f"vs_wet_median_ft_{k}"] = float(np.nanmean(rel[k][mm]))
        row["ft_above_ditch_2023"] = float(np.nanmean(hand[mm]))
        row["ft_to_ditch"] = float(np.nanmean(dist[mm]) * M_TO_FT)
        s = soil[soil.contains(p.geometry)]
        row["soil"] = f"{s.musym.iloc[0]} {s.muname.iloc[0].split()[0]}" if len(s) else ""
        cal.append(row)
    cal = pd.DataFrame(cal)
    cal.to_csv(OUT / "calibration.csv", index=False, float_format="%.2f")

    keep = ~m_ditchzone
    aucs = {
        "elevation 2013": auc(sm["2013"], m_up & keep, m_wet & keep),
        "elevation 2023": auc(sm["2023"], m_up & keep, m_wet & keep),
    }
    for R in (15, 30, 60):
        aucs[f"local relief {R} m (2023)"] = auc(sm["2023"] - smooth(raw["2023"], R / 2),
                                                 m_up & keep, m_wet & keep)
    aucs["height above ditch (2023)"] = auc(hand, m_up & keep, m_wet & keep)
    aucs["closeness to ditch"] = auc(-dist, m_up & keep, m_wet & keep)

    up_pts = cal[cal.rick == "upland"]
    wet_pts = cal[cal.rick == "wetland"]
    print("== Calibration at Rick's points (5 m mean, ft NAVD88) ==")
    print(cal[["point", "rick", "elev_ft_2013", "elev_ft_2023",
               "vs_wet_median_ft_2023", "ft_above_ditch_2023", "ft_to_ditch",
               "soil"]].round(2).to_string(index=False))
    overlap = (wet_pts.elev_ft_2023.max() >= up_pts.elev_ft_2023.min())
    print(f"\n  upland points 2023 span {up_pts.elev_ft_2023.min():.2f}..{up_pts.elev_ft_2023.max():.2f};"
          f" wetland points {wet_pts.elev_ft_2023.min():.2f}..{wet_pts.elev_ft_2023.max():.2f}"
          f"  -> {'OVERLAP' if overlap else 'separated'}")
    print("\n== Pixel separation, Rick upland vs Rick wetland (AUC; 0.5 = none) ==")
    for k, v in aucs.items():
        print(f"  {k:<28} {v:.2f}")
    print(f"\n  wetland median: 2013 {wet_med['2013']:.2f} ft, 2023 {wet_med['2023']:.2f} ft;"
          f" smoothed flight-to-flight noise {noise:.2f} ft (HIGH_FT = {HIGH_FT})")

    # ------------------------------------------------------------ flags
    west, east = sides_of_ditch(feats)
    m_west = mask(west, shp, T)
    m_east = mask(east, shp, T)
    # How far the ditch demonstrably dries the east bank: Rick's upland data
    # points on the ditch strip (1 and 2) sit this far from it. Anchored to his
    # own sampled points, not to a lateral-effect equation we have no inputs for
    # (ditch depth to bottom and soil Ksat are both unknown).
    reach_m = float(cal[cal.point.isin(STRIP_PTS)].ft_to_ditch.max() / M_TO_FT)
    print(f"  bank zone: west of the ditch, within {reach_m * M_TO_FT:.0f} ft "
          f"(farthest of Rick's strip points {STRIP_PTS})")

    nonhyd = soil[soil.hydric_pct < NONHYDRIC_MAX_PCT]
    m_soil = mask(unary_union(nonhyd.geometry), shp, T) if len(nonhyd) else np.zeros(shp, bool)
    zone = m_wet & ~m_ditchzone & m_par
    f_high = zone & (rel["2013"] >= HIGH_FT) & (rel["2023"] >= HIGH_FT)
    f_soil = zone & m_soil
    f_bank = zone & m_west & (dist <= reach_m)
    flags = {"high": f_high, "soil": f_soil, "bank": f_bank}
    prefix = {"high": "H", "soil": "S", "bank": "B"}

    # Patches are built from each flag on its own, so a rise does not vanish
    # into a neighbouring strip. Each patch then reports how much of it the
    # OTHER flags also cover. A patch can therefore overlap one of another type.
    upland_touch = nd.binary_dilation(m_up, iterations=3)
    rows = []
    for fname, fm in flags.items():
        fm = nd.binary_opening(fm, iterations=2)       # drop 1-2 px slivers
        lab, n = nd.label(fm)
        for i in range(1, n + 1):
            pm = lab == i
            ac = pm.sum() * abs(T.a * T.e) / ACRE_M2
            adj = bool((pm & upland_touch).any())
            if ac < (MIN_AC_ADJ if adj else MIN_AC):
                continue
            g = unary_union([shape(s) for s, v in
                             shapes(pm.astype("uint8"), mask=pm, transform=T) if v == 1])
            g = g.simplify(1.0)
            share = {f: (pm & m2).sum() / pm.sum() for f, m2 in flags.items()}
            tags = [f for f, sh in share.items() if sh >= 0.5]
            c = g.representative_point()
            dpts = pts.geometry.distance(g)
            near = pts.loc[dpts.idxmin()]
            inside = [int(p.point) for _, p in pts.iterrows()
                      if g.buffer(POINT_RADIUS_M).contains(p.geometry)]
            sl = soil.copy()
            sl["a"] = sl.intersection(g).area / g.area
            sl = sl.groupby(["musym", "muname"], as_index=False)["a"].sum()
            sl = sl[sl.a >= 0.1].sort_values("a", ascending=False)
            soil_txt = " / ".join(f"{r.musym} {r.muname.split()[0]} {r.a * 100:.0f}%"
                                  for r in sl.itertuples())
            near_row = cal.set_index("point").loc[near.point]
            e23 = float(np.nanmean(raw["2023"][pm]))
            rows.append({
                "type": fname,
                "tags": "+".join(tags),
                "n_evidence": len(tags),
                "acres": round(ac, 2),
                "elev_ft_2013": round(float(np.nanmean(raw["2013"][pm])), 2),
                "elev_ft_2023": round(e23, 2),
                "vs_wet_median_ft": round(float(np.nanmean(rel["2023"][pm])), 2),
                "vs_rick_upland_pts_ft": round(e23 - float(up_pts.elev_ft_2023.median()), 2),
                "pct_high": round(share["high"] * 100),
                "pct_nonhydric_soil": round(share["soil"] * 100),
                "pct_west_bank": round(share["bank"] * 100),
                "soil": soil_txt,
                "ft_to_ditch": round(float(np.nanmin(dist[pm])) * M_TO_FT),
                "touches_rick_upland": adj,
                "nearest_rick_pt": f"{int(near.point)} ({near.rick}, "
                                   f"{dpts.min() * M_TO_FT:.0f} ft, "
                                   f"{e23 - near_row.elev_ft_2023:+.2f} ft vs it)",
                "rick_pt_inside": ",".join(map(str, inside)),
                "_geom": g,
                "_centroid": c,
            })

    df = pd.DataFrame(rows).sort_values(["n_evidence", "acres"], ascending=[False, False])
    counts = {}
    ids = []
    for t in df["type"]:
        counts[t] = counts.get(t, 0) + 1
        ids.append(f"{prefix[t]}{counts[t]}")
    df.insert(0, "id", ids)
    df["lat"], df["lon"] = zip(*[(p.y, p.x) for p in
                                 gpd.GeoSeries(df["_centroid"], crs=CRS).to_crs(4326)])
    df["rationale"] = df.apply(rationale, axis=1)

    gdf = gpd.GeoDataFrame(df.drop(columns=["_geom", "_centroid"]),
                           geometry=list(df["_geom"]), crs=CRS)
    gdf.drop(columns=["geometry"]).to_csv(OUT / "candidates.csv", index=False)
    g4326 = gdf.to_crs(4326)
    g4326.to_file(OUT / "candidates.geojson", driver="GeoJSON")
    kml = g4326[["id", "rationale", "geometry"]].rename(columns={"id": "Name", "rationale": "Description"})
    (OUT / "candidates.kml").unlink(missing_ok=True)
    kml.to_file(OUT / "candidates.kml", driver="KML")

    print(f"\n== {len(df)} candidate patches ==")
    print(df.drop(columns=["_geom", "_centroid", "rationale", "lat", "lon"]).to_string(index=False))
    for _, r in df.iterrows():
        print(f"  {r.id}: {r.rationale}")

    # ------------------------------------------------------------ maps
    ctx = dict(feats=feats, pts=pts, par=par, ext=ext, soil=soil)
    lo, hi = np.nanpercentile(raw["2023"][m_par], [2, 98])
    two_panel(ctx, [np.where(mask(par.buffer(61), shp, T), raw[k], np.nan) for k in ("2013", "2023")],
              ["2013 (Rick's source)", "2023"], "terrain", lo, hi,
              "Elevation, ft NAVD88, stretched to this parcel", OUT / "map_elevation.png")
    two_panel(ctx, [np.where(mask(par.buffer(61), shp, T), rel[k], np.nan) for k in ("2013", "2023")],
              ["2013", "2023"], "BrBG_r", -1.5, 1.5,
              "Feet above/below the wetland's median (3 m smoothing)", OUT / "map_relative.png")
    soils_map(ctx, OUT / "map_soils.png")
    cand_map(ctx, np.where(mask(par.buffer(61), shp, T), rel["2023"], np.nan), gdf,
             OUT / "map_candidates.png")

    # numbers the write-up needs
    pd.Series({**{f"auc: {k}": round(v, 2) for k, v in aucs.items()},
               "wet_median_2013": round(wet_med["2013"], 2),
               "wet_median_2023": round(wet_med["2023"], 2),
               "flight_noise_ft": round(noise, 2),
               "bank_zone_ft": round(reach_m * M_TO_FT),
               "parcel_ac": round(par.area / ACRE_M2, 2),
               "study_traced_ac": round(feats["study"].area / ACRE_M2, 2),
               }).to_csv(OUT / "screen_numbers.csv", header=False)


def rationale(r):
    bits = []
    if r.pct_high >= 50:
        bits.append(f"{r.vs_wet_median_ft:+.1f} ft above the wetland median in both flights")
    if r.pct_nonhydric_soil >= 50:
        bits.append(f"mapped {r.soil}")
    if r.pct_west_bank >= 50:
        bits.append("west bank of the ditch, same distance as your east-side upland")
    if r.touches_rick_upland:
        bits.append("adjoins your upland")
    s = "; ".join(bits) if bits else "partial overlap of several flags"
    if r.rick_pt_inside:
        s += f". Against: your point {r.rick_pt_inside} is in it"
    return s


# ------------------------------------------------------------------ drawing
def overlay(ax, ctx):
    f, pts, par = ctx["feats"], ctx["pts"], ctx["par"]
    ax.plot(*par.exterior.xy, c="k", lw=1.6, label="parcel (VGIN)")
    ax.plot(*f["upland"].exterior.xy, c="darkorange", lw=1.8, label="Rick upland")
    ax.plot(*f["wet2"].exterior.xy, c="limegreen", lw=1.6, label="Rick Wetland #2")
    ax.plot(*f["ditch"].xy, c="blue", lw=1.6, label="Rick ditch")
    for _, p in pts.iterrows():
        ax.plot(p.geometry.x, p.geometry.y, "o", ms=8, mec="k",
                c="darkorange" if p.rick == "upland" else "limegreen")
        ax.annotate(str(p.point), (p.geometry.x + 4, p.geometry.y + 4),
                    fontsize=11, weight="bold",
                    bbox=dict(boxstyle="round,pad=0.1", fc="white", alpha=0.7, lw=0))
    b = par.buffer(40).bounds
    ax.set_xlim(b[0], b[2])
    ax.set_ylim(b[1], b[3])
    ax.set_xticks([])
    ax.set_yticks([])
    # 100 ft scale bar
    x0, y0 = b[0] + 8, b[1] + 8
    ax.plot([x0, x0 + 100 / M_TO_FT], [y0, y0], c="k", lw=3)
    ax.text(x0, y0 + 4, "100 ft", fontsize=9)


def two_panel(ctx, arrs, titles, cmap, lo, hi, label, path):
    fig, axes = plt.subplots(1, 2, figsize=(14, 10))
    for ax, a, t in zip(axes, arrs, titles):
        im = ax.imshow(a, extent=ctx["ext"], cmap=cmap, vmin=lo, vmax=hi)
        overlay(ax, ctx)
        ax.set_title(t)
    fig.colorbar(im, ax=axes, shrink=0.6, label=label)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.suptitle("1832 Munden Point Rd", fontsize=14)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def soils_map(ctx, path):
    soil = ctx["soil"]
    colors = {"7": "#d9a441", "19": "#f2d98a", "13": "#c9dfa0",
              "24": "#7fb3c8", "29": "#3f6e9a"}
    fig, ax = plt.subplots(figsize=(9, 11))
    for _, r in soil.iterrows():
        gs = getattr(r.geometry, "geoms", [r.geometry])
        for g in gs:
            ax.fill(*g.exterior.xy, fc=colors.get(str(r.musym), "#ccc"), ec="purple", lw=0.8)
            c = g.representative_point()
            if g.area > 300:
                ax.text(c.x, c.y, str(r.musym), fontsize=12, color="purple", weight="bold")
    from matplotlib.patches import Patch
    names = soil.drop_duplicates("musym").set_index("musym")
    handles = [Patch(fc=colors.get(str(k), "#ccc"),
                     label=f"{k} {v.muname.split(',')[0]} - {v.drainage}"
                           f"{', hydric' if v.hydric_pct >= 50 else ''}")
               for k, v in names.iterrows()]
    overlay(ax, ctx)
    ax.legend(handles=handles, loc="upper left", fontsize=8)
    ax.set_title("SSURGO soil map units (1:24k survey -- a hint, not a rule)")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def cand_map(ctx, rel23, gdf, path):
    fig, ax = plt.subplots(figsize=(9, 11))
    ax.imshow(rel23, extent=ctx["ext"], cmap="BrBG_r", vmin=-1.5, vmax=1.5, alpha=0.8)
    for _, r in gdf.iterrows():
        for g in getattr(r.geometry, "geoms", [r.geometry]):
            ax.fill(*g.exterior.xy, fc="none", ec="red", lw=2.2, hatch="//")
        c = r.geometry.representative_point()
        ax.text(c.x, c.y, r.id, fontsize=14, weight="bold", color="red",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", alpha=0.85, lw=0))
    overlay(ax, ctx)
    ax.set_title("Candidate patches (red) on 2023 relative elevation")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
