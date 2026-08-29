#!/usr/bin/env python3
"""
Can VGIN find a parcel from an address?

Every Blackwater Rd listing we want comes back from the API with
(None, None) for coordinates -- 6584, 6636, 6608, 6628, 6664 and the "Mm"
entries. The ones that DO have coordinates are the old resale houses we do not
want. realtor.com has not geocoded the new subdivisions and new builds.

qualify() drops anything without lat/lon, because the whole pipeline is
coordinate-driven. So the lots Matt most wants are invisible for a reason that
has nothing to do with the land.

The Census geocoder already failed (RemoteDisconnected on every request), and
we are querying VGIN anyway for parcel polygons. If VGIN can search by address
it solves this with a service we already trust and no new dependency.

Unknown and worth testing rather than assuming:
  - does the layer expose an address field at all
  - what is it called (naming varies by contributing locality)
  - does a LIKE query on a house number + street actually match

    mv -f ~/Downloads/probe_vgin_address.py . && chmod +x probe_vgin_address.py && ./probe_vgin_address.py
"""

import json
import sys
import urllib.parse
import urllib.request

VGIN_BASE = ("https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
             "VA_Base_Layers/VA_Parcels/MapServer/0")

# Ungeocoded listings we are trying to rescue.
WANTED = [
    ("6584", "BLACKWATER"),
    ("6636", "BLACKWATER"),
    ("6664", "BLACKWATER"),
]
# A listing WITH known coordinates, as a control: if we can find this one by
# address and the point lookup agrees, address search is trustworthy.
CONTROL = ("6336", "BLACKWATER", 36.569496, -76.075299)


def get(url, params, label):
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=60) as r:
            body = r.read().decode()
    except Exception as e:
        print(f"    {label}: {type(e).__name__}: {e}")
        return None
    try:
        js = json.loads(body)
    except ValueError:
        print(f"    {label}: not JSON -- {body[:150]}")
        return None
    if "error" in js:
        print(f"    {label}: service error {js['error']}")
        return None
    return js


def main():
    # ---- 1. what fields does this layer actually have? ---------------------
    print("=" * 76)
    print("1. VGIN layer fields")
    print("=" * 76)
    meta = get(VGIN_BASE, {"f": "json"}, "metadata")
    if not meta:
        return 1
    fields = [f["name"] for f in meta.get("fields", [])]
    print(f"  {len(fields)} fields:")
    print("  " + ", ".join(fields))

    addr_fields = [f for f in fields
                   if any(w in f.upper() for w in
                          ("ADDR", "SITUS", "STREET", "LOCAT", "PROP"))]
    print(f"\n  address-looking fields: {addr_fields or 'NONE FOUND'}")
    if not addr_fields:
        print("  => no address field. Address search is not possible here;")
        print("     fall back to a geocoder or the locality's own GIS.")
        return 0

    # ---- 2. can we match on them? ------------------------------------------
    print("\n" + "=" * 76)
    print("2. LIKE queries for the ungeocoded listings")
    print("=" * 76)
    for num, street in WANTED:
        print(f"\n  {num} {street}")
        for fld in addr_fields[:4]:
            where = f"UPPER({fld}) LIKE '%{num}%{street}%'"
            js = get(f"{VGIN_BASE}/query",
                     {"f": "geojson", "where": where, "outFields": "*",
                      "outSR": 4326, "returnGeometry": "true",
                      "resultRecordCount": 3},
                     f"{fld}")
            if js is None:
                continue
            feats = js.get("features") or []
            print(f"    {fld:22} {len(feats)} match(es)")
            for ft in feats[:2]:
                pr = ft.get("properties", {}) or {}
                shown = {k: v for k, v in pr.items()
                         if v not in (None, "", " ") and
                         any(w in k.upper() for w in
                             ("ADDR", "SITUS", "OWNER", "ACRE", "LOCAL",
                              "PARCEL", "GPIN", "PIN"))}
                geom = ft.get("geometry") or {}
                coords = geom.get("coordinates")
                cen = ""
                if coords:
                    try:
                        from shapely.geometry import shape
                        c = shape(geom).centroid
                        cen = f"  centroid ({c.y:.5f}, {c.x:.5f})"
                    except Exception:
                        pass
                print(f"        {shown}{cen}")

    # ---- 3. control: does address search agree with point lookup? ----------
    num, street, lat, lon = CONTROL
    print("\n" + "=" * 76)
    print(f"3. CONTROL - {num} {street}, known at ({lat}, {lon})")
    print("=" * 76)
    js = get(f"{VGIN_BASE}/query",
             {"f": "geojson", "geometry": f"{lon},{lat}",
              "geometryType": "esriGeometryPoint", "inSR": 4326, "outSR": 4326,
              "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
              "returnGeometry": "false"}, "by point")
    if js and js.get("features"):
        pr = js["features"][0].get("properties", {}) or {}
        print("  by point:", {k: v for k, v in pr.items()
                              if v not in (None, "", " ")
                              and any(w in k.upper() for w in
                                      ("ADDR", "SITUS", "OWNER", "PARCEL", "GPIN"))})
    else:
        print("  by point: no feature")

    print("\n" + "=" * 76)
    print("If a field matches reliably, ungeocoded listings can be rescued by")
    print("address using a service we already call. If not, the fallback is a")
    print("geocoder (Census failed earlier) or Virginia Beach's own GIS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
