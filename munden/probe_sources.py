#!/usr/bin/env python3
"""
What data actually exists at 1832 Munden Point Rd? Asked before anything is built.

  1. Parcel polygon from VGIN (statewide) -- compare to Rick's traced study area.
  2. Every 3DEP 1 m DEM tile and lidar point-cloud tile covering the site, from
     the USGS TNM products API, with project name and date. Rick used "Norfolk
     VA 2013"; the question is whether anything newer was flown.

    source .venv/bin/activate && python munden/probe_sources.py
"""
import json
import urllib.parse
import urllib.request

LAT, LON = 36.5807, -76.0210        # middle of Rick's study area
VGIN = ("https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
        "VA_Base_Layers/VA_Parcels/MapServer/0/query")
TNM = "https://tnmaccess.nationalmap.gov/api/v1/products"


def get(url, params, timeout=60):
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params),
                                timeout=timeout) as r:
        return json.loads(r.read().decode())


print("== VGIN parcel ==")
js = get(VGIN, {"f": "geojson", "geometry": f"{LON},{LAT}",
                "geometryType": "esriGeometryPoint", "inSR": 4326,
                "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*", "returnGeometry": "true"})
for f in js.get("features", []):
    print(json.dumps(f["properties"]))
    print("  vertices:", len(f["geometry"]["coordinates"][0]))

bbox = f"{LON-0.004},{LAT-0.004},{LON+0.004},{LAT+0.004}"
for ds in ("Digital Elevation Model (DEM) 1 meter", "Lidar Point Cloud (LPC)"):
    print(f"\n== TNM: {ds} ==")
    js = get(TNM, {"datasets": ds, "bbox": bbox, "max": 50,
                   "outputFormat": "JSON"})
    for it in js.get("items", []):
        print(f"  {it.get('publicationDate','')[:10]}  {it['title']}")
        print(f"      {it.get('downloadURL')}  {it.get('sizeInBytes')}")
