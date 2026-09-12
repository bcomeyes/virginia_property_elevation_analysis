#!/usr/bin/env python3
"""
Probe USDA Soil Data Access: can we get perc-relevant soil per parcel?

Why this matters more than anything else we measure: a lot that fails a perc
test is not a lot, it is a campsite. And the road matters too -- clay subgrade
wants geotextile under the base course, sand often does not.

What we want per parcel:
  drainagecl     drainage class. The best single predictor of passing perc.
  hydricrating   formed under saturation. One of the three legs of a wetland
                 determination, so this also speaks to the Corps question.
  comppct_r      each soil's share of its map unit -- the mosaic Matt asked
                 about. A "complex" map unit is the survey saying the ground
                 is patchy and to expect surprises.
  wtdepannmin    shallowest annual water table. The usual reason coastal lots
                 fail.
  ksat_r         how fast water actually moves. What a perc test measures.
  claytotal_r    clay content, for the geotextile question.

HONEST LIMITS, stated up front:
  SSURGO is mapped at ~1:12,000-1:24,000. The smallest polygon a mapper would
  draw is a few acres, so a 4-acre lot may be ONE polygon. Between-parcel
  differences: yes. A 50-foot sand ridge inside a small lot: no. The Coastal
  Plain is stacked Pleistocene shorelines -- beach ridge, tidal flat, old marsh
  channel within a few hundred feet -- so real variation is finer than the map.
  This is triage, not diagnosis. It tells you whether to pay someone to dig.

GROUND TRUTH: the cousin's lot is ~74% Zone AE marsh with a dry corner where
the house sits. Its soils should come back mostly poorly drained and hydric,
with something better on the dry end. If they do not, the query is wrong.

    mv -f ~/Downloads/probe_soil.py . && chmod +x probe_soil.py && ./probe_soil.py
"""

import json
import sys
import urllib.request
import warnings

sys.path.insert(0, ".")
import land_watch as lw          # noqa: E402
import parcel_elevation as pe    # noqa: E402

warnings.filterwarnings("ignore")

SDA = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"

SITES = [
    ("Knotts Island (cousin's)", 36.53916, -75.99679,
     "74% AE marsh, dry corner with the house - expect poorly drained + hydric"),
    ("6584 Blackwater Rd", None, None, "wooded, X 78 / AE 22"),
    ("6664 Blackwater Rd", None, None, "12 ac, X 100"),
]


def sda(sql, timeout=90):
    """POST a T-SQL query. Returns list of rows (first row is column names)."""
    body = json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode()
    req = urllib.request.Request(SDA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        js = json.loads(r.read().decode())
    return js.get("Table") or []


def wkt(poly):
    """shapely -> WKT, simplified if huge. SDA has a query size limit."""
    p = poly
    if len(p.wkt) > 8000:
        for tol in (1e-5, 5e-5, 2e-4):
            p = poly.simplify(tol, preserve_topology=True)
            if len(p.wkt) <= 8000:
                break
    return p.wkt


def main():
    print("=" * 78)
    print("1. is SDA up?")
    print("=" * 78)
    try:
        rows = sda("SELECT areasymbol, saverest FROM sacatalog "
                   "WHERE areasymbol IN ('VA810','NC053')")
        for r in rows:
            print("   ", r)
    except Exception as e:
        print(f"   FAILED {type(e).__name__}: {str(e)[:120]}")
        print("   (VA810 = Virginia Beach, NC053 = Currituck)")
        return 1

    sites = []
    for name, lat, lon, note in SITES:
        if lat is None:
            lat, lon = lw.geocode(name, "Virginia Beach", "VA", "23457")
        if lat is None:
            print(f"could not locate {name}")
            continue
        sites.append((name, lat, lon, note))

    for name, lat, lon, note in sites:
        print("\n" + "=" * 78)
        print(f"{name}")
        print(f"  {note}")
        print("=" * 78)

        poly, _ = pe.get_parcel_polygon(lat, lon)
        if poly is None:
            print("  no parcel")
            continue

        w = wkt(poly)
        print(f"  parcel WKT {len(w)} chars")

        # --- which map units intersect the parcel, and how much of it? -----
        try:
            rows = sda(f"""
                SELECT mukey, mupolygonkey,
                       Geometry::STGeomFromText(mupolygongeo.STAsText(),4326).STArea()
                FROM SDA_Get_MuPolygon_from_intersection_with_WktWgs84('{w}')
            """)
            print(f"  spatial query returned {max(len(rows)-1,0)} polygon(s)")
        except Exception as e:
            print(f"  MuPolygon query failed: {type(e).__name__}: {str(e)[:90]}")
            rows = []

        # simpler fallback: just the mukeys
        if len(rows) <= 1:
            try:
                rows = sda("SELECT mukey FROM "
                           f"SDA_Get_Mukey_from_intersection_with_WktWgs84('{w}')")
                print(f"  mukey query returned {max(len(rows)-1,0)} map unit(s)")
            except Exception as e:
                print(f"  mukey query failed: {type(e).__name__}: {str(e)[:90]}")
                continue

        if len(rows) <= 1:
            print("  no soil map units found for this parcel")
            continue

        header, data = rows[0], rows[1:]
        mukeys = sorted({str(r[0]) for r in data})
        print(f"  map units: {', '.join(mukeys)}")

        # --- the soil properties we actually care about ---------------------
        keylist = ",".join(f"'{k}'" for k in mukeys)
        sql = f"""
            SELECT mu.mukey, mu.muname, c.compname, c.comppct_r,
                   c.drainagecl, c.hydricrating, c.taxclname
            FROM mapunit mu
            INNER JOIN component c ON c.mukey = mu.mukey
            WHERE mu.mukey IN ({keylist})
            ORDER BY mu.mukey, c.comppct_r DESC
        """
        try:
            rows = sda(sql)
        except Exception as e:
            print(f"  component query failed: {type(e).__name__}: {str(e)[:90]}")
            continue
        if len(rows) <= 1:
            print("  no components returned")
            continue

        print(f"\n  {'component':22} {'%':>4} {'drainage':26} {'hydric':7}")
        print("  " + "-" * 66)
        last_mu = None
        for r in rows[1:]:
            mukey, muname, comp, pct, drain, hyd, tax = (list(r) + [None]*7)[:7]
            if mukey != last_mu:
                print(f"\n  [{mukey}] {str(muname)[:62]}")
                last_mu = mukey
            print(f"  {str(comp)[:22]:22} {str(pct or ''):>4} "
                  f"{str(drain or '?')[:26]:26} {str(hyd or '?'):7}")

    print("\n" + "=" * 78)
    print("Read the cousin's lot first -- if its soils are not mostly poorly")
    print("drained and hydric, the query is wrong, not the land.")
    print("'Poorly drained' or 'very poorly drained' means conventional septic")
    print("is unlikely. Hydric = wetland indicator. A map unit named as a")
    print("'complex' means the survey itself is telling you the ground varies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
