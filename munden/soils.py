#!/usr/bin/env python3
"""
SSURGO map units over the parcel + 200 ft: symbol, name, drainage class,
hydric rating, annual minimum water-table depth. Clipped polygons saved to
cache/soils.geojson for the other scripts.

Same service and traps as parcel_elevation.py: the WFS returns EPSG:4326 as
LAT,LON (detect by sign), wtdepannmin lives on muaggatt, SDA returns strings.
SSURGO here is a ~1:24k survey -- a polygon is a hint about what a soil scientist
expected, not a measurement at any spot.

    source ../.venv/bin/activate && python soils.py
"""
import json
import tempfile
import urllib.parse
import urllib.request

import geopandas as gpd
from shapely.ops import transform as tf

from common import ACRE_M2, BUFFER_M, CACHE, CRS, parcel, rick

SDA_POST = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"
SDA_WFS = "https://SDMDataAccess.sc.egov.usda.gov/Spatial/SDMWGS84Geographic.wfs"


def sda(sql):
    body = json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode()
    req = urllib.request.Request(SDA_POST, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        t = json.loads(r.read().decode()).get("Table") or []
    return [dict(zip(t[0], row)) for row in t[1:]]


def main():
    feats, _ = rick()
    par, _ = parcel()
    area = par.union(feats["study"]).buffer(BUFFER_M)
    minx, miny, maxx, maxy = gpd.GeoSeries([area], crs=CRS).to_crs(4326).total_bounds
    flt = (f"<Filter><BBOX><PropertyName>Geometry</PropertyName>"
           f"<Box srsName='EPSG:4326'><coordinates>"
           f"{minx},{miny} {maxx},{maxy}</coordinates></Box></BBOX></Filter>")
    url = SDA_WFS + "?" + urllib.parse.urlencode({
        "SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
        "TYPENAME": "MapunitPoly", "FILTER": flt,
        "SRSNAME": "EPSG:4326", "OUTPUTFORMAT": "GML2"})
    with urllib.request.urlopen(url, timeout=120) as r:
        gml = r.read()
    with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as fh:
        fh.write(gml)
    g = gpd.read_file(fh.name)
    g = g.set_crs(4326, allow_override=True)
    # Continental US: lon negative, lat positive. x>0 and y<0 means swapped.
    g["geometry"] = [tf(lambda x, y, z=None: (y, x), geom)
                     if geom.bounds[0] > 0 and geom.bounds[1] < 0 else geom
                     for geom in g.geometry]
    g = g.to_crs(CRS)
    g.columns = [c.lower() for c in g.columns]

    keys = ",".join(f"'{k}'" for k in g["mukey"].astype(str).unique())
    mu = {r["mukey"]: r for r in sda(f"""
        SELECT mu.mukey, mu.musym, mu.muname, m.drclassdcd, m.hydclprs,
               m.wtdepannmin
        FROM mapunit mu LEFT JOIN muaggatt m ON m.mukey = mu.mukey
        WHERE mu.mukey IN ({keys})""")}
    comps = sda(f"""
        SELECT mukey, compname, comppct_r, drainagecl, hydricrating
        FROM component WHERE mukey IN ({keys}) ORDER BY mukey, comppct_r DESC""")

    for col, src in [("musym", "musym"), ("muname", "muname"),
                     ("drainage", "drclassdcd"), ("hydric_pct", "hydclprs"),
                     ("wt_min_cm", "wtdepannmin")]:
        g[col] = [mu.get(str(k), {}).get(src) for k in g["mukey"].astype(str)]
    for col in ("hydric_pct", "wt_min_cm"):
        g[col] = g[col].astype(float)
    g["wt_min_in"] = (g["wt_min_cm"] / 2.54).round(0)
    g["geometry"] = g.intersection(area)
    g = g[~g.is_empty][["mukey", "musym", "muname", "drainage", "hydric_pct",
                        "wt_min_in", "geometry"]]
    g.to_file(CACHE / "soils.geojson", driver="GeoJSON")

    print("== Map units (dominant condition) ==")
    for k, r in mu.items():
        print(f"  {r['musym']:>4}  {r['muname']}\n        {r['drclassdcd']}, "
              f"hydric components {r['hydclprs']}%, water table min "
              f"{r['wtdepannmin']} cm")
    print("\n== Components ==")
    for c in comps:
        print(f"  {mu[c['mukey']]['musym']:>4}  {c['compname']:<14} {c['comppct_r']:>3}%  "
              f"{c['drainagecl']}, hydric={c['hydricrating']}")

    print("\n== Acres inside each of Rick's zones ==")
    zones = {"parcel": par, "upland": feats["upland"], "wetland #2": feats["wet2"],
             "wetland #1": feats["wet1"]}
    for zn, zg in zones.items():
        parts = []
        for sym in sorted(g["musym"].unique()):
            ac = g[g.musym == sym].intersection(zg).area.sum() / ACRE_M2
            if ac >= 0.01:
                parts.append(f"{sym} {ac:.2f}")
        print(f"  {zn:<11} " + ", ".join(parts))


if __name__ == "__main__":
    main()
