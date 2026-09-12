#!/usr/bin/env python3
"""
soil.py - what soil is on a parcel, and WHERE.

Septic viability is a harder constraint than anything else the pipeline
measures: a lot that fails perc is not a lot. And the driveway matters too --
clay subgrade wants geotextile fabric under the base course, sand often does
not.

Validated against the cousin's lot, which Matt has walked. SSURGO returns
three map units there and they line up with his description exactly:
  Altavista fine sandy loam, moderately well drained, NOT hydric  -> the dry
      corner where the house sits
  Currituck mucky peat, very poorly drained, hydric, 90%          -> the marsh
  Roanoke fine sandy loam, poorly drained, hydric                 -> between

WHY THE GEOMETRY MATTERS
An earlier version returned only soil NAMES per parcel, which is not enough.
6664 Blackwater has Tetotum loam (moderately well drained, not hydric) AND
Tomotley loam (poorly drained, hydric). A drainfield has to go on the Tetotum.
Knowing both are present without knowing where is not actionable, so this
fetches the actual map unit polygons and clips them to the parcel.

HONEST LIMITS
SSURGO is mapped at ~1:12,000-1:24,000. The smallest polygon a mapper would
delineate is a few acres, so a 4-acre lot may be a single polygon. The Coastal
Plain is stacked Pleistocene shorelines -- beach ridge, tidal flat, old marsh
channel within a few hundred feet -- so real variation is finer than the map
can show. Where a map unit is a "complex", the survey is telling you the ground
is patchy and it cannot draw the pieces.

This is triage, not a perc test. It tells you whether to pay someone to dig,
and where to dig first.

    ./soil.py --point LAT LON
    ./soil.py --address "6664 Blackwater Rd"
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
import warnings

sys.path.insert(0, ".")
import land_watch as lw          # noqa: E402
import parcel_elevation as pe    # noqa: E402

warnings.filterwarnings("ignore")

SDA = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"
WFS = "https://SDMDataAccess.sc.egov.usda.gov/Spatial/SDMWGS84Geographic.wfs"

# Drainage classes, worst to best. Conventional septic wants the top of this
# list; anything "poorly drained" or below usually needs an engineered system
# if it is permittable at all.
DRAINAGE_ORDER = [
    "Excessively drained", "Somewhat excessively drained", "Well drained",
    "Moderately well drained", "Somewhat poorly drained",
    "Poorly drained", "Very poorly drained", "Subaqueous",
]
GOOD_DRAINAGE = {"Excessively drained", "Somewhat excessively drained",
                 "Well drained", "Moderately well drained"}


def sda(sql, timeout=90):
    body = json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode()
    req = urllib.request.Request(SDA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("Table") or []


def mu_polygons_sql(poly):
    """Map unit polygons intersecting the parcel, via SDA.

    The earlier failure was my SQL, not the service: I wrapped the geometry in
    a nested STGeomFromText/STArea conversion. Asking for the WKT directly
    works, and the intersection is then done locally in shapely -- which we
    already trust and which avoids SQL geometry quirks entirely.
    """
    w = poly.wkt
    if len(w) > 7000:
        for tol in (1e-5, 5e-5, 2e-4):
            w = poly.simplify(tol, preserve_topology=True).wkt
            if len(w) <= 7000:
                break
    rows = sda("SELECT mukey, mupolygongeo.STAsText() AS wkt FROM "
               f"SDA_Get_MuPolygon_from_intersection_with_WktWgs84('{w}')")
    out = []
    if len(rows) > 1:
        from shapely import wkt as shp_wkt
        for r in rows[1:]:
            try:
                out.append((str(r[0]), shp_wkt.loads(r[1])))
            except Exception:
                continue
    return out


def mu_polygons_wfs(poly):
    """Fallback: WFS GetFeature with a bounding-box filter, returns GML."""
    minx, miny, maxx, maxy = poly.bounds
    flt = (f"<Filter><BBOX><PropertyName>Geometry</PropertyName>"
           f"<Box srsName='EPSG:4326'><coordinates>"
           f"{minx},{miny} {maxx},{maxy}"
           f"</coordinates></Box></BBOX></Filter>")
    url = WFS + "?" + urllib.parse.urlencode({
        "SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
        "TYPENAME": "MapunitPoly", "FILTER": flt,
        "SRSNAME": "EPSG:4326", "OUTPUTFORMAT": "GML2",
    })
    with urllib.request.urlopen(url, timeout=120) as r:
        gml = r.read()
    import tempfile
    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as fh:
        fh.write(gml)
        path = fh.name
    gdf = gpd.read_file(path)
    key = next((c for c in gdf.columns if c.lower() == "mukey"), None)

    # WFS 1.1.0 returns EPSG:4326 as LATITUDE, LONGITUDE -- the axis order
    # changed from 1.0.0 and this service follows the spec. Everything else in
    # this project is lon/lat, so the polygons came back with x and y swapped
    # and landed in the Indian Ocean: parcel x started at -75.99 while soil x
    # started at 36.53. Detect and flip rather than assume, because a future
    # service version may not do this.
    from shapely.ops import transform as shp_tf
    out = []
    for _, row in gdf.iterrows():
        g = row.geometry
        if g is None or g.is_empty:
            continue
        # latitude cannot exceed 90; a US longitude always does in magnitude.
        # If x looks like a latitude and y like a longitude, swap.
        minx, miny, _, _ = g.bounds
        # In the continental US longitude is NEGATIVE and latitude POSITIVE.
        # x positive and y negative therefore means the pair is swapped.
        # (An earlier magnitude test failed: y was -75.996, and 75.996 < 90.)
        if minx > 0 and miny < 0:
            g = shp_tf(lambda x, y, z=None: (y, x), g)
        out.append((str(row[key]) if key else "?", g))
    return out


def _num(v):
    """SDA returns every value as a string, including numerics."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def components(mukeys):
    """Soil components per map unit, with the properties that decide perc."""
    keylist = ",".join(f"'{k}'" for k in mukeys)
    rows = sda(f"""
        SELECT mu.mukey, mu.muname, c.compname, c.comppct_r, c.drainagecl,
               c.hydricrating, c.taxclname,
               (SELECT TOP 1 ch.ksat_r FROM chorizon ch
                 WHERE ch.cokey = c.cokey ORDER BY ch.hzdept_r) AS ksat,
               (SELECT TOP 1 ch.claytotal_r FROM chorizon ch
                 WHERE ch.cokey = c.cokey ORDER BY ch.hzdept_r) AS clay
        FROM mapunit mu
        INNER JOIN component c ON c.mukey = mu.mukey
        WHERE mu.mukey IN ({keylist})
        ORDER BY mu.mukey, c.comppct_r DESC
    """)
    out = {}
    for r in rows[1:] if len(rows) > 1 else []:
        mukey, muname, comp, pct, drain, hyd, tax, ksat, clay = (list(r) + [None]*9)[:9]
        d = out.setdefault(str(mukey), {"muname": muname, "comps": []})
        d["comps"].append({"name": comp, "pct": pct, "drain": drain,
                           "hydric": hyd, "ksat": _num(ksat),
                           "clay": _num(clay)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--point", nargs=2, type=float, metavar=("LAT", "LON"))
    ap.add_argument("--address")
    ap.add_argument("--city", default="Virginia Beach")
    ap.add_argument("--state", default="VA")
    ap.add_argument("--zip", default=None)
    a = ap.parse_args()

    if a.address:
        lat, lon = lw.geocode(a.address, a.city, a.state, a.zip)
        if lat is None:
            sys.exit(f"could not geocode {a.address}")
    elif a.point:
        lat, lon = a.point
    else:
        sys.exit("need --point LAT LON or --address")

    poly, attrs = pe.get_parcel_polygon(lat, lon)
    if poly is None:
        sys.exit(f"no parcel at {lat:.5f}, {lon:.5f}")
    poly_utm = pe.shp_transform(pe._to_utm, poly)
    acres = poly_utm.area / pe.ACRE_M2
    print(f"\nparcel {acres:.2f} ac  {(attrs or {}).get('_address') or a.address or ''}")

    polys = []
    try:
        polys = mu_polygons_sql(poly)
        src = "SDA"
    except Exception as e:
        print(f"  SDA polygon query failed ({type(e).__name__}), trying WFS")
    if not polys:
        try:
            polys = mu_polygons_wfs(poly)
            src = "WFS"
        except Exception as e:
            print(f"  WFS also failed: {type(e).__name__}: {str(e)[:70]}")
            return 1
    print(f"  {len(polys)} soil polygon(s) from {src}")

    # clip each to the parcel and measure, locally
    shares = {}
    for mukey, g in polys:
        try:
            inter = poly.intersection(g)
        except Exception:
            continue
        if inter.is_empty:
            continue
        ac = pe.shp_transform(pe._to_utm, inter).area / pe.ACRE_M2
        shares[mukey] = shares.get(mukey, 0.0) + ac
    if not shares:
        print("  no overlap with the parcel")
        return 0

    info = components(list(shares))
    total = sum(shares.values()) or 1.0

    print(f"\n{'soil':30} {'acres':>7} {'% lot':>6} {'drainage':24} {'hyd':4} "
          f"{'ksat':>7} {'clay%':>6}")
    print("-" * 92)
    good_ac = 0.0
    for mukey, ac in sorted(shares.items(), key=lambda kv: -kv[1]):
        d = info.get(mukey, {})
        comps = d.get("comps") or [{}]
        top = comps[0]
        name = (top.get("name") or d.get("muname") or mukey)
        drain = top.get("drain") or "?"
        if drain in GOOD_DRAINAGE:
            good_ac += ac
        print(f"{str(name)[:30]:30} {ac:7.2f} {ac/total*100:6.1f} "
              f"{str(drain)[:24]:24} {str(top.get('hydric') or '?'):4} "
              f"{_num(top.get('ksat')):7.2f} {_num(top.get('clay')):6.1f}")
        for c in comps[1:]:
            print(f"   + {str(c.get('name'))[:24]:26} {str(c.get('pct') or ''):>3}% "
                  f"          {str(c.get('drain') or '?')[:24]:24} "
                  f"{str(c.get('hydric') or '?'):4}")

    print(f"\nSEPTIC   {good_ac:.2f} of {total:.2f} ac "
          f"({good_ac/total*100:.0f}%) on moderately-well-drained or better soil")
    if good_ac < 0.5:
        print("         Very little well-drained ground. A conventional")
        print("         drainfield is unlikely; expect an engineered system,")
        print("         or a lot that will not permit at all.")
    else:
        print("         Enough to site a drainfield on paper. The health")
        print("         department still wants holes dug.")

    hyd_ac = sum(ac for k, ac in shares.items()
                 if (info.get(k, {}).get("comps") or [{}])[0].get("hydric") == "Yes")
    print(f"\nWETLAND  {hyd_ac:.2f} ac ({hyd_ac/total*100:.0f}%) on hydric soil")
    print("         Hydric soil is one of the three legs of a wetland")
    print("         determination, alongside hydrology and vegetation. Compare")
    print("         against the FEMA zones and the plat's delineation.")
    print("\nksat = saturated hydraulic conductivity, um/s, top horizon -- what a")
    print("perc test measures. clay% high + poorly drained = geotextile under")
    print("any road base, and a thicker stone section.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
