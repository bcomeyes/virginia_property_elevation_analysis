#!/usr/bin/env python3
"""
Probe FEMA's National Flood Hazard Layer against real parcels.

Run this from the repo root -- it imports parcel_elevation.py to get the same
parcel polygons the pipeline uses.

Why query with the POLYGON and not the pin: a parcel routinely spans several
flood zones. Your cousin's lot is the obvious case -- dry ground near the road,
marsh running down to the water. A single point would report one zone and hide
that. We want every zone the parcel touches, with the share of the lot in each.

Test lots:
  302 Marsh Causeway, Knotts Island NC -- cousin's, expected Zone X (he pays no
      flood insurance), 6.9 ft max elevation. The lot that proved the old 20 ft
      rule was answering the wrong question.
  2281 Kings Fork Rd, Suffolk VA -- 47 ft, best TRI in the set.
  3316 Holland Rd, Suffolk VA -- 74.8 ft, flattest ground we measured.

Run:
    mv -f ~/Downloads/ping_fema.py . && chmod +x ping_fema.py && ./ping_fema.py
"""

import json
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, ".")
try:
    import parcel_elevation as pe
except ImportError:
    sys.exit("run this from the repo root, next to parcel_elevation.py")

from shapely.geometry import shape, mapping  # noqa: E402
from shapely.ops import transform as shp_transform  # noqa: E402

NFHL = ("https://hazards.fema.gov/arcgis/rest/services/public/"
        "NFHL/MapServer/28/query")

LOTS = [
    ("cousin's lot, Knotts Island NC", 36.53916, -75.99679),
    ("2281 Kings Fork Rd, Suffolk VA", None, None),      # filled from --test data
    ("3316 Holland Rd, Suffolk VA",    None, None),
]

# coordinates for the two Suffolk lots, so this runs without hitting the
# listings API. Replace if the listings move.
LOTS[1] = ("2281 Kings Fork Rd, Suffolk VA", 36.7852, -76.6103)
LOTS[2] = ("3316 Holland Rd, Suffolk VA",    36.6689, -76.6541)


def esri_polygon(poly):
    """shapely -> Esri rings JSON, which is what the query parameter wants."""
    gj = mapping(poly)
    if gj["type"] == "Polygon":
        rings = [list(map(list, r)) for r in gj["coordinates"]]
    else:                                   # MultiPolygon
        rings = [list(map(list, r)) for part in gj["coordinates"] for r in part]
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def zones_for(poly):
    """Every NFHL zone polygon intersecting this parcel."""
    params = {
        "f": "json",
        "geometry": json.dumps(esri_polygon(poly)),
        "geometryType": "esriGeometryPolygon",
        "inSR": 4326, "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DEPTH",
        "returnGeometry": "true",
    }
    url = NFHL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            body = r.read().decode()
    except Exception as e:
        print(f"    NETWORK: {type(e).__name__}: {e}")
        return None
    try:
        js = json.loads(body)
    except ValueError:
        print(f"    NOT JSON: {body[:200]}")
        return None
    if "error" in js:
        print(f"    SERVICE ERROR: {js['error']}")
        return None
    return js


def main():
    print(f"NFHL layer 28\n{NFHL}\n")

    for label, lat, lon in LOTS:
        print("=" * 70)
        print(f"{label}   ({lat}, {lon})")

        poly, attrs = pe.get_parcel_polygon(lat, lon)
        if poly is None:
            print("    no parcel found -- skipping")
            continue
        src = (attrs or {}).get("_source", "?")
        print(f"    parcel from {src}, {poly.bounds[0]:.5f}..{poly.bounds[2]:.5f} lon")

        js = zones_for(poly)
        if js is None:
            continue
        feats = js.get("features", [])
        print(f"    {len(feats)} flood zone polygon(s) intersect this parcel")
        if not feats:
            print("    -> NOT MAPPED. No NFHL coverage here, which is itself")
            print("       information: unmapped is not the same as safe.")
            continue

        # area of the parcel in each zone, in the local UTM so acres are honest
        parcel_utm = shp_transform(pe._to_utm, poly)
        total = parcel_utm.area or 1.0

        seen = {}
        for f in feats:
            a = f.get("attributes", {})
            zone = a.get("FLD_ZONE") or "?"
            sub = (a.get("ZONE_SUBTY") or "").strip()
            key = f"{zone}" + (f" ({sub})" if sub else "")
            zpoly = pe._rings_to_polygon(f.get("geometry", {}))
            frac = 0.0
            if zpoly is not None and not zpoly.is_empty:
                try:
                    inter = parcel_utm.intersection(shp_transform(pe._to_utm, zpoly))
                    frac = inter.area / total
                except Exception:
                    pass
            rec = seen.setdefault(key, {"frac": 0.0, "sfha": a.get("SFHA_TF"),
                                        "bfe": a.get("STATIC_BFE")})
            rec["frac"] += frac

        for key, rec in sorted(seen.items(), key=lambda kv: -kv[1]["frac"]):
            bfe = rec["bfe"]
            bfe_s = f", BFE {bfe}" if bfe not in (None, -9999, "-9999") else ""
            sfha = "SFHA" if rec["sfha"] == "T" else "not SFHA"
            print(f"      {key:28} {rec['frac']*100:5.1f}% of lot   {sfha}{bfe_s}")
        print()

    print("=" * 70)
    print("Zone X / X (unshaded) = minimal risk, no mandatory insurance.")
    print("A, AE, AO, VE = Special Flood Hazard Area, insurance required with a")
    print("federally backed mortgage. VE is coastal wave action, the worst of them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
