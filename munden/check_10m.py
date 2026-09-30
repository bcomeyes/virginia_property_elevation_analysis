#!/usr/bin/env python3
"""
10 m cross-check. The pipeline's own Virginia Beach 10 m DEM (3DEP 1/3
arc-second, pinned to exactly 10.0 m in UTM 18N) sampled at Rick's nine
points and at the four asks.

What 10 m can and cannot do here: one pixel is ~1/40 acre, so ask #1 (0.3 ac)
is about a dozen pixels and Rick's points each fall in one or two. It cannot
confirm inch-scale detail. It can say whether the coarse, independently
produced product shows the same broad highs and lows as the two 1 m flights.

    source ../.venv/bin/activate && python check_10m.py
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask

from common import CRS, M_TO_FT, OUT, REPO, rick

DEM10 = REPO / "data" / "dem" / "Virginia_Beach_10m.tif"
ASKS = {"#1": "H3", "#2": "S1", "#3": "B1", "#4": "H2"}
RADIUS_M = 10


def main():
    feats, pts = rick()
    cands = gpd.read_file(OUT / "candidates.geojson").to_crs(CRS).set_index("id")
    with rasterio.open(DEM10) as s:
        # Pipeline DEM is WGS84 UTM 18N (32618); ours is NAD83 (26918). ~1 m apart
        # here -- a tenth of one 10 m pixel -- so read it on our coordinates as is.
        assert s.crs.to_epsg() in (CRS, 32618) and abs(s.res[0] - 10) < 1e-6, (s.crs, s.res)
        b = feats["study"].buffer(100).bounds
        win = rasterio.windows.from_bounds(*b, s.transform).round_offsets().round_lengths()
        a = s.read(1, window=win).astype(float)
        T = s.window_transform(win)
        if s.nodata is not None:
            a[a == s.nodata] = np.nan
    a *= M_TO_FT
    m = lambda g: ~geometry_mask([g], a.shape, T, all_touched=True)
    wet_med = np.nanmedian(a[m(feats["wetland"])])

    cal = pd.read_csv(OUT / "calibration.csv").set_index("point")
    rows = []
    for _, p in pts.iterrows():
        v = np.nanmean(a[m(p.geometry.buffer(RADIUS_M))])
        rows.append({"where": f"Rick pt {p.point} ({p.rick})",
                     "10m_ft": v, "10m_vs_wet_median": v - wet_med,
                     "1m_2023_ft": cal.loc[p.point, "elev_ft_2023"]})
    for ask, cid in ASKS.items():
        g = cands.loc[cid].geometry
        v = np.nanmean(a[m(g)])
        rows.append({"where": f"ask {ask}", "10m_ft": v,
                     "10m_vs_wet_median": v - wet_med,
                     "1m_2023_ft": cands.loc[cid, "elev_ft_2023"]})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "check_10m.csv", index=False, float_format="%.2f")
    print(f"10 m wetland median {wet_med:.2f} ft")
    print(df.round(2).to_string(index=False))
    print(f"\n1 m (2023) vs 10 m at Rick's points: correlation "
          f"{np.corrcoef(df['10m_ft'][:9], df['1m_2023_ft'][:9])[0, 1]:.2f}")


if __name__ == "__main__":
    main()
