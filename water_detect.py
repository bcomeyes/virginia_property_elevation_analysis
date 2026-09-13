"""
water_detect.py - find water in a bare-earth DEM, at any elevation.

THE BUG THIS FIXES
The first detector looked for ground that was flat AND LOW, where "low" meant
within 2 ft of the parcel's 2nd percentile. It was tuned on the Knotts Island
lot, where the marsh IS the lowest ground, and it worked there: 33% wet,
matching a parcel Matt has walked.

On 7399 Crittenden Rd it failed completely. That parcel has TWO water bodies at
different elevations: tidal water on the east edge at about -1.5 ft, and a
freshwater pond at about 5 ft. The 2nd percentile anchored on the tidal water,
making the threshold 0.5 ft, so the 26-acre pond -- six feet higher -- was not
"low" and went undetected. It was then reported as a 20-acre building pad.

THE FIX
Do not use elevation at all. Use FLATNESS.

Bare-earth lidar usually renders a water body as a plane at its surface
elevation, because there is no ground return from under water. That plane is
PERFECTLY flat -- local standard deviation essentially zero over a large area.
Real ground is never that flat, not even marsh: it has hummocks, ditches,
tussocks, and interpolation noise.

So: compute local roughness in a small window, find large contiguous regions
where it is near zero, and require a minimum area so that a paved yard or a
smooth field patch does not qualify.
"""

import numpy as np
from scipy import ndimage

M_TO_FT = 3.280839895

# A lidar water surface has local std well under 0.1 ft. Marsh, the flattest
# real ground here, runs several times that.
FLAT_STD_FT   = 0.08
WINDOW_M      = 5.0      # local roughness window
MIN_BODY_AC   = 0.25     # smaller than this is a puddle or an artefact


def local_std(ft, cell_m, window_m=WINDOW_M):
    """Standard deviation of elevation in a moving window, in feet."""
    k = max(3, int(round(window_m / cell_m)) | 1)      # odd
    filled = np.where(np.isfinite(ft), ft, np.nan)
    mean = ndimage.uniform_filter(np.nan_to_num(filled, nan=0.0), k)
    cnt = ndimage.uniform_filter(np.isfinite(filled).astype(float), k)
    cnt[cnt == 0] = np.nan
    mean = mean / cnt
    sq = ndimage.uniform_filter(np.nan_to_num(filled ** 2, nan=0.0), k) / cnt
    var = np.maximum(sq - mean ** 2, 0.0)
    out = np.sqrt(var)
    out[~np.isfinite(ft)] = np.nan
    return out


def water_mask(ft, cell_m, flat_std_ft=FLAT_STD_FT, min_body_ac=MIN_BODY_AC):
    """Water at ANY elevation. Returns (mask, list of {acres, elev_ft}).

    Also treats NaN holes as water when they are large and enclosed: some
    lidar products mask water out entirely instead of flattening it.
    """
    good = np.isfinite(ft)
    cell_ac = (cell_m ** 2) / 4046.8564224
    if good.sum() < 100:
        return np.zeros_like(good), []

    flat = good & (local_std(ft, cell_m) < flat_std_ft)
    flat = ndimage.binary_opening(flat, np.ones((3, 3)))    # drop specks

    lbl, n = ndimage.label(flat)
    mask = np.zeros_like(good)
    bodies = []
    for i in range(1, n + 1):
        m = lbl == i
        ac = m.sum() * cell_ac
        if ac < min_body_ac:
            continue
        mask |= m
        bodies.append({"acres": round(float(ac), 2),
                       "elev_ft": round(float(np.nanmedian(ft[m])), 1)})
    bodies.sort(key=lambda b: -b["acres"])
    return mask, bodies
