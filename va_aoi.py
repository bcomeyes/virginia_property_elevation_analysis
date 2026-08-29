"""
Shared geography for the coastal-Virginia high-ground search.

Everything location-specific lives here so the notebooks don't drift. Retargeted
from coastal NC per the July 2026 relocation: the home search is now the narrow
band of southeastern Virginia within Larissa's 30-mile-of-the-beach rule.

The organizing fact of this landscape: the Suffolk Scarp, a relict shoreline
running roughly north-south ~20 mi inland. Ground east of it is the low terrace
that stood under water at a +20-25 ft highstand; the buildable high ground is on
and west of the scarp. Larissa's rule pulls east (toward the beach, low);
the 20 ft rule pulls west (inland, high). Winners live in the thin overlap.
"""

from pyproj import Transformer, Geod
import numpy as np

# --- Localities in scope (Census county-equivalent FIPS) --------------------
# Virginia's independent cities ARE county-equivalents; pygris/py3dep treat them
# as counties. Isle of Wight / Southampton were dropped: too far from the beach
# under the 30-mile rule.
LOCALITIES = {
    "Virginia_Beach": "51810",   # southern/rural half is the target, not the resort strip
    "Chesapeake":     "51550",   # immediately west, holds the scarp high ground
    "Suffolk":        "51800",   # only the EASTERN edge survives the buffer
}
STATE_FIPS = "51"

# --- Screening thresholds (UNCHANGED from NC per instructions) --------------
THRESHOLD_FT   = 20.0
MIN_PAD_ACRES  = 2.0

# Two DIFFERENT size windows, kept distinct on purpose:
#  - REGION filter (triage, notebook 02): a contiguous high-ground blob. Keep
#    from MIN_SITE_ACRES (below which it can't hold a buildable parcel) up to
#    MAX_REGION_ACRES (above which it's a landscape, usually federal). A 118-acre
#    high-ground region is NOT a blob to discard -- it holds several parcels.
#  - PARCEL filter (parcels, notebook 04): an actual lot to buy, 8-60 acres.
MIN_SITE_ACRES   = 8.0
MAX_SITE_ACRES   = 60.0        # parcel ceiling (notebook 04)
MAX_REGION_ACRES = 2000.0      # blob cutoff for regions (notebook 02)

# --- Larissa's hard rule ----------------------------------------------------
# 30-mile DRIVE from the ocean beach. Drive distance >= straight-line distance,
# so anything beyond 30 mi straight-line ALSO fails the drive rule and can be
# dropped safely now. Anything within 30 mi straight-line is a maybe -- water
# detours (Back Bay, North Landing River) can push the real drive over 30, so
# those get a true drive-time check at the finalist stage. Small margin added
# against DEM/line imprecision.
BEACH_RULE_MI   = 30.0
BEACH_BUFFER_MI = 31.0     # coarse-cut buffer for fetch + region gate

# Atlantic oceanfront, Cape Henry (mouth of the Chesapeake) south to the NC line.
# This is "the beach" the rule measures from. (lon, lat)
OCEANFRONT = [
    (-76.008, 36.926),   # Cape Henry
    (-75.977, 36.880),   # 60th St
    (-75.967, 36.830),   # Rudee Inlet
    (-75.962, 36.773),   # Dam Neck
    (-75.938, 36.735),   # Sandbridge
    (-75.897, 36.650),   # Little Island / False Cape
    (-75.868, 36.551),   # NC border at the coast
]

# --- Protected / federal land to flag (approx centroids, VERIFY boundaries) --
# Not authoritative. First-pass so the triage doesn't hand you a swamp or a
# naval air station. dist_km is a rough radius to flag within.
EXCLUSIONS = [
    ("Great Dismal Swamp NWR", 36.630, -76.460, 12.0),   # huge, Chesapeake+Suffolk
    ("Back Bay NWR",           36.660, -75.920,  4.0),    # ocean edge, southern VB
    ("False Cape State Park",  36.600, -75.890,  4.0),    # below Back Bay
    ("NAS Oceana",             36.820, -76.030,  3.5),    # VB
    ("NALF Fentress",          36.700, -76.130,  2.0),    # Chesapeake
    ("Dam Neck Annex",         36.770, -75.960,  2.0),    # VB
]

WORKING_EPSG = 32618        # UTM 18N -- covers ~76W, unchanged from NC
_GEOD = Geod(ellps="WGS84")


def _to_utm():
    return Transformer.from_crs(4326, WORKING_EPSG, always_xy=True)


def beach_buffer_geom(buffer_mi=BEACH_BUFFER_MI):
    """Oceanfront line buffered by buffer_mi, returned in UTM 18N (meters)."""
    from shapely.geometry import LineString
    tf = _to_utm()
    line = LineString([tf.transform(lon, lat) for lon, lat in OCEANFRONT])
    return line.buffer(buffer_mi * 1609.344)


def oceanfront_line_utm():
    from shapely.geometry import LineString
    tf = _to_utm()
    return LineString([tf.transform(lon, lat) for lon, lat in OCEANFRONT])


def dist_to_beach_mi(lon, lat):
    """
    Straight-line miles from a lon/lat to the nearest point on the oceanfront.
    Accepts scalars or arrays. This is the COARSE measure; real drive time is
    computed only on finalists.
    """
    lon = np.atleast_1d(np.asarray(lon, dtype=float))
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    line = oceanfront_line_utm()
    tf = _to_utm()
    from shapely.geometry import Point
    out = np.empty(len(lon))
    for i, (x, y) in enumerate(zip(lon, lat)):
        px, py = tf.transform(x, y)
        out[i] = line.distance(Point(px, py)) / 1609.344
    return out            # always an ndarray; assign to a DataFrame column safely


def flag_exclusion(lon, lat):
    """Name of the nearest protected area if within its flag radius, else ''."""
    for name, clat, clon, rkm in EXCLUSIONS:
        _, _, dist_m = _GEOD.inv(lon, lat, clon, clat)
        if dist_m / 1000.0 <= rkm:
            return name
    return ""
