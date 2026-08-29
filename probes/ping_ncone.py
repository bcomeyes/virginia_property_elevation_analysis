#!/usr/bin/env python3
"""
Ping NC OneMap's parcel service from THIS machine and report exactly what it says.

No fallbacks, no cleverness. Just: is it up, and if so what does it return for
a real point? The point is your cousin's lot on Knotts Island, Currituck County.

Run:  python3 ping_ncone.py
"""

import json
import sys

import requests

BASE = "https://services.nconemap.gov/secure/rest/services"
PARCELS = f"{BASE}/NC1Map_Parcels/MapServer"

# 302 Marsh Causeway, Knotts Island, Currituck County NC
LON, LAT = -75.99679, 36.53916

TIMEOUT = 30


def get(url, params=None, label=""):
    print(f"\n--- {label}")
    print(f"    {url}")
    try:
        r = requests.get(url, params=params or {}, timeout=TIMEOUT)
    except Exception as e:
        print(f"    NETWORK ERROR: {type(e).__name__}: {e}")
        return None

    print(f"    HTTP {r.status_code}, {len(r.content)} bytes, {r.headers.get('content-type','?')}")

    if "json" not in r.headers.get("content-type", ""):
        # ArcGIS web adaptor returns an HTML error page when its backends are down
        body = r.text[:400].replace("\n", " ")
        print(f"    NOT JSON. First 400 chars:\n    {body}")
        return None

    try:
        j = r.json()
    except Exception as e:
        print(f"    JSON PARSE FAILED: {e}")
        return None

    if "error" in j:
        print(f"    ARCGIS ERROR: {j['error']}")
        return None
    return j


def main():
    # 1. Is the service directory reachable at all?
    j = get(BASE, {"f": "json"}, "services root")
    if j:
        print(f"    OK -- {len(j.get('services', []))} services listed")

    # 2. Is the parcel service itself alive?
    j = get(PARCELS, {"f": "json"}, "parcel service metadata")
    if not j:
        print("\n=> Parcel service is NOT reachable from this machine.")
        return 1

    print(f"    OK -- {j.get('description','')[:80]}")
    for lyr in j.get("layers", []):
        print(f"    layer {lyr['id']}: {lyr['name']} ({lyr.get('geometryType','?')})")

    # 3. What fields does the POLYGON layer actually have?
    #    (This is the thing I could not verify. Never hardcode from memory.)
    for lyr_id in (0, 1):
        j = get(f"{PARCELS}/{lyr_id}", {"f": "json"}, f"layer {lyr_id} fields")
        if j:
            print(f"    geometryType: {j.get('geometryType')}")
            names = [f["name"] for f in j.get("fields", [])]
            print(f"    {len(names)} fields: {', '.join(names)}")

    # 4. The real test: point-in-polygon at your cousin's lot.
    #    inSR/outSR matter -- the service natively uses NC State Plane feet,
    #    so without these the lon/lat gets read as feet and matches nothing.
    for lyr_id in (0, 1):
        j = get(
            f"{PARCELS}/{lyr_id}/query",
            {
                "f": "json",
                "geometry": json.dumps({"x": LON, "y": LAT,
                                        "spatialReference": {"wkid": 4326}}),
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "outSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "true",
            },
            f"layer {lyr_id} query at {LAT}, {LON}",
        )
        if not j:
            continue
        feats = j.get("features", [])
        print(f"    {len(feats)} feature(s) returned")
        if feats:
            attrs = feats[0].get("attributes", {})
            for k, v in list(attrs.items())[:25]:
                if v not in (None, "", " "):
                    print(f"      {k} = {v}")
            geom = feats[0].get("geometry", {})
            rings = geom.get("rings", [])
            if rings:
                print(f"      geometry: {len(rings)} ring(s), "
                      f"{len(rings[0])} vertices in the first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
