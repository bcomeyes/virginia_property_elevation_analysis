#!/usr/bin/env python3
"""
parcel_elevation.py - does a for-sale parcel have surge-safe high ground on it?

THE QUESTION (Matt's, refined):
  Not "is the listing's pin at 20 ft" and not "is it near a high-ground pocket."
  The real test: does ANY real bare ground inside the parcel boundary reach the
  threshold - because you'll dig a pond and raise a pad, so you only need one
  buildable high corner, not the whole lot high.

HOW:
  1. Take the listing's lat/lon, ask VGIN which parcel polygon contains it.
  2. Clip the cached bare-earth DEM to that polygon.
  3. Report the elevation distribution on the lot: max, and how many acres sit
     at or above the threshold. Bare-earth DEM means barns/outbuildings are
     already mostly stripped, so we're measuring dirt, not roofs.

STANDALONE by design. Prove it against real listings first (--test), THEN it
gets folded into land_watch.py's daily filter once trusted.

USAGE:
    ./parcel_elevation.py --probe 36.72 -76.24     # dump raw VGIN parcel (debug)
    ./parcel_elevation.py --point 36.72 -76.24     # full check on one coordinate
    ./parcel_elevation.py --test                   # run on today's real land listings
    ./parcel_elevation.py --test --threshold 15    # same, 15 ft bar
"""

import argparse, json, sys, time, urllib.parse, urllib.request, urllib.error, warnings
from pathlib import Path

import numpy as np
import rasterio
import rasterio.mask
from shapely.geometry import shape, Point, mapping
from shapely.ops import transform as shp_transform
from pyproj import Transformer

HERE     = Path(__file__).resolve().parent
DEM_DIR  = HERE / "data" / "dem"
WORKING_EPSG = 32618
M_TO_FT  = 3.280839895
ACRE_M2  = 4046.8564224

# Elevation is REPORTED, never a gate. Raw height above sea level turned out to
# be a bad proxy for what Matt is looking for: the Holland Rd lots are the
# highest ground in the search area (75 ft) and the flattest (relief 0.70), while
# the Knotts Island calibration lot is only 6.9 ft yet sits in FEMA Zone X and
# carries no flood insurance requirement. Flood zone answers the flood question;
# terrain texture answers the seclusion question. Height answers neither.
#
# Kept as a tunable rather than deleted so a one-off experiment is still possible
# (`--threshold 20`), but the default of 0 means nothing is ever excluded on
# height. Note 0, not some negative number: tidal-marsh DEM cells legitimately
# read slightly below sea level.
DEFAULT_THRESHOLD_FT   = 0.0
DEFAULT_MIN_HIGH_ACRES = 0.0

VGIN = ("https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
        "VA_Base_Layers/VA_Parcels/MapServer/0/query")

# NC OneMap, statewide parcels. LAYER 1 is the polygons -- layer 0 is centroids
# and a point cannot intersect a point, so querying 0 silently returns nothing.
# Field names below were read off the live service, not remembered.
NCONE = ("https://services.nconemap.gov/secure/rest/services/"
         "NC1Map_Parcels/MapServer/1/query")

# The VA/NC line runs near this latitude across our search band. It is only a
# hint for which service to ask FIRST -- if that one comes up empty we ask the
# other, so a parcel straddling the line still resolves.
STATE_LINE_LAT = 36.55

# FEMA National Flood Hazard Layer, layer 28 = Flood Hazard Zones (polygons).
# Queried with the PARCEL POLYGON, never the pin: a lot routinely spans several
# zones and a point reports only one. The Knotts Island calibration lot is 74%
# AE and 21% X -- the marsh is the AE, the buildable corner is the X. A point
# query would have returned whichever one it happened to land in and hidden the
# distinction that actually decides whether the lot is worth looking at.
NFHL = ("https://hazards.fema.gov/arcgis/rest/services/public/"
        "NFHL/MapServer/28/query")

# Zones requiring flood insurance with a federally backed mortgage. VE is
# coastal wave action and the most serious. Anything not in this set is
# treated as open ground for the purposes of the ranking column.
SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}

# USFS/NLCD Tree Canopy Cover via MRLC's GeoServer: percent tree canopy per
# 30 m pixel. Coarse -- a 4 acre lot is ~18 pixels -- but it is the thing we
# actually want to know, and it validated cleanly against parcels Matt labelled
# from imagery: 6584 Blackwater (he says completely wooded) 91.5%, the Sandpiper
# campground 6.4%, his cousin's mixed lot 58.1%.
#
# Why this matters more than it looks: on flat coastal parcels the landform
# screens nothing -- 6664 Blackwater has 2.9 ft of relief across 12 acres -- so
# trees are the ONLY source of privacy. Bare-earth lidar cannot see them.
#
# NAIP NDVI at 0.3 m was also tested and deliberately NOT used. It measures
# greenness, not trees: it scored the cousin's marsh-heavy lot at 93% against
# canopy's 58%, because marsh grass is green. We care about woods, not grass.
#
# The layer name changes with each release, so it is discovered rather than
# hardcoded. 2021 is the current vintage, meaning very recent clearing will
# not show.
MRLC_WMS = "https://www.mrlc.gov/geoserver/mrlc_display/wms"

# USDA SSURGO soil survey via Soil Data Access. Septic viability is a harder
# constraint than anything else measured here -- a lot that fails perc is not a
# lot -- and the same properties decide whether a gravel drive needs geotextile
# fabric under the base course.
#
# Validated against the cousin's lot, which Matt has walked: Altavista
# (moderately well drained, not hydric) on the dry corner where the house sits,
# Currituck mucky peat (very poorly drained) on the marsh, Roanoke between.
# 70% hydric against FEMA's 74% Zone AE -- two independent datasets agreeing
# within four points.
#
# It also found what nothing else did: 6664 Blackwater, the 12-acre Zone X lot
# that looked best in the search on every other measure, has ZERO acres of
# moderately-well-drained soil and is 67% hydric.
#
# LIMITS: mapped at ~1:12,000, smallest polygon a few acres, so a 4-acre lot may
# be one polygon. The Coastal Plain is stacked Pleistocene shorelines, so real
# variation is finer than the map can draw. Triage, not a perc test.
#
# The SDA spatial query returns HTTP 400; the WFS works. WFS 1.1.0 returns
# EPSG:4326 as LAT,LON -- the axis order changed from 1.0.0 -- so coordinates
# arrive swapped and must be flipped.
SDA_POST = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"
SDA_WFS  = "https://SDMDataAccess.sc.egov.usda.gov/Spatial/SDMWGS84Geographic.wfs"

# Conventional septic wants the top of this set. Below it: engineered system,
# or a lot that will not permit at all.
GOOD_DRAINAGE = {"Excessively drained", "Somewhat excessively drained",
                 "Well drained", "Moderately well drained"}
_TCC_LAYER = None

_to_utm = Transformer.from_crs(4326, WORKING_EPSG, always_xy=True).transform


# --------------------------------------------------------------------------- #
def _fetch_json(url, params, label, verbose=False, tries=3):
    """One ArcGIS query, retried. Returns parsed JSON or None; never raises.

    Retries exist because these services drop requests under load rather than
    failing cleanly -- the same behaviour 3DEP showed when whole-locality DEM
    fetches failed while small tiles succeeded. A dropped request is not a
    finding about the parcel, so treating the first failure as an answer would
    silently blank out exactly the large lots we care most about.
    """
    full = url + "?" + urllib.parse.urlencode(params)
    body = None
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(full, timeout=90) as r:
                body = r.read().decode()
            break
        except Exception as e:
            if attempt == tries:
                if verbose:
                    print(f"    {label} error after {tries} tries: "
                          f"{type(e).__name__}: {e}")
                return None
            time.sleep(2 * attempt)
    if body is None:
        return None
    try:
        js = json.loads(body)
    except ValueError:
        # ArcGIS web adaptors return an HTML error page when their backends are
        # unreachable. Treat that as "no answer", not as a crash.
        if verbose:
            print(f"    {label}: response was not JSON ({body[:120]!r})")
        return None
    if isinstance(js, dict) and "error" in js:
        if verbose:
            print(f"    {label} service error: {js['error']}")
        return None
    return js


def _query_vgin(lat, lon, verbose=False):
    """Virginia parcels (VGIN). GeoJSON out. Returns (poly_4326, attrs) or (None, None)."""
    js = _fetch_json(VGIN, {
        "f": "geojson",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326, "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
    }, "VGIN", verbose)
    if not js:
        return None, None
    feats = js.get("features") or []
    if verbose:
        print(json.dumps(js, indent=2)[:1500])
    if not feats:
        return None, None
    try:
        poly = shape(feats[0]["geometry"])
    except Exception:
        return None, None
    return poly, feats[0].get("properties", {})


def _rings_to_polygon(geom):
    """Esri rings -> shapely. Outer rings are clockwise, holes counter-clockwise."""
    rings = geom.get("rings") or []
    if not rings:
        return None
    from shapely.geometry import Polygon, MultiPolygon

    def signed_area(r):
        return sum((r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1])
                   for i in range(len(r) - 1)) / 2.0

    outers, holes = [], []
    for r in rings:
        (holes if signed_area(r) > 0 else outers).append(r)
    if not outers:                       # all one direction; treat each as outer
        outers, holes = rings, []
    polys = []
    for o in outers:
        shell = Polygon(o)
        inner = [h for h in holes if shell.contains(Polygon(h).representative_point())]
        polys.append(Polygon(o, inner))
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def _query_ncone(lat, lon, verbose=False):
    """North Carolina parcels (NC OneMap). Esri JSON out -- geojson support is
    not guaranteed on this service, and plain json is what we verified live."""
    js = _fetch_json(NCONE, {
        "f": "json",
        "geometry": json.dumps({"x": lon, "y": lat,
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "inSR": 4326, "outSR": 4326,      # service is natively NC State Plane feet;
        "spatialRel": "esriSpatialRelIntersects",   # without these the lon/lat is
        "outFields": "*",                           # read as feet and matches nothing
        "returnGeometry": "true",
    }, "NCOneMap", verbose)
    if not js:
        return None, None
    feats = js.get("features") or []
    if verbose:
        print(json.dumps(js, indent=2)[:1500])
    if not feats:
        return None, None
    poly = _rings_to_polygon(feats[0].get("geometry", {}))
    if poly is None or poly.is_empty:
        return None, None
    return poly, feats[0].get("attributes", {})


# Reported-acreage field, per source. Used for the pin-landed-on-the-wrong-parcel
# cross-check; NC publishes gisacres, VGIN's naming varies by contributing county.
_ACRE_FIELDS = ("gisacres", "GIS_Acres", "ACRES", "Acres", "acres",
                "CALC_ACRES", "LEGAL_ACRE", "deeded_acres")


def _normalise(attrs, source):
    """Add source-independent keys so callers never branch on which state it is."""
    if attrs is None:
        return None
    out = dict(attrs)
    out["_source"] = source
    for k in _ACRE_FIELDS:
        if k in attrs and attrs[k] not in (None, "", 0):
            try:
                out["_acres_reported"] = float(attrs[k])
                break
            except (TypeError, ValueError):
                pass
    for k in ("siteadd", "SITEADDRESS", "Address", "situs_addr", "ADDRESS"):
        if attrs.get(k):
            out["_address"] = str(attrs[k]).strip()
            break
    for k in ("ownname", "OWNER", "Owner", "OWNERNAME", "owner_name"):
        if attrs.get(k):
            out["_owner"] = str(attrs[k]).strip()
            break
    return out


# --------------------------------------------------------------------------- #
# On-disk cache for the two slow network lookups.
#
# Parcel boundaries have not moved in decades and FEMA remaps on a scale of
# years, but every sweep was re-querying both for every parcel. That is what
# made a run take minutes: three round trips per parcel, serially, with retries
# and rate-limit pauses. Same precompute-then-tune pattern as the DEM tiles and
# cache/geocode.json.
#
# One file per parcel rather than one big JSON, so a lookup never rewrites
# megabytes and a bad entry can be deleted by hand.
#
# Delete cache/parcels/ or cache/flood/ to force a refetch.
CACHE_DIR = HERE / "cache"
FLOOD_MAX_AGE_DAYS = 180        # FEMA does remap; parcels effectively never do


def _cache_key(lat, lon):
    """~1 m precision. Two listings this close are the same lookup."""
    return f"{lat:.5f}_{lon:.5f}".replace("-", "m")


def _cache_read(kind, key, max_age_days=None):
    fp = CACHE_DIR / kind / f"{key}.json"
    try:
        obj = json.loads(fp.read_text())
    except (OSError, ValueError):
        return None
    if max_age_days is not None and (time.time() - obj.get("_at", 0)) > max_age_days * 86400:
        return None
    return obj


def _cache_write(kind, key, obj):
    fp = CACHE_DIR / kind / f"{key}.json"
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        obj = dict(obj)
        obj["_at"] = time.time()
        fp.write_text(json.dumps(obj))
    except OSError:
        pass


def get_parcel_polygon(lat, lon, verbose=False):
    """Parcel polygon containing (lat, lon), from whichever state service has it.

    Returns (shapely polygon in 4326, attrs dict) or (None, None).

    Routing: latitude picks which service to ask first, then we ask the other if
    the first has nothing. That means the STATE_LINE_LAT constant only needs to be
    roughly right -- a parcel just north or south of the line still resolves, and
    so does one in a county whose data is missing from its own state's service.
    """
    key = _cache_key(lat, lon)
    hit = _cache_read("parcels", key)
    if hit is not None:
        if not hit.get("geometry"):
            return None, None            # remembered miss; do not re-ask
        return shape(hit["geometry"]), hit.get("attrs")

    order = ((_query_vgin, "VGIN"), (_query_ncone, "NCOneMap"))
    if lat < STATE_LINE_LAT:
        order = order[::-1]

    for fn, name in order:
        poly, attrs = fn(lat, lon, verbose)
        if poly is not None:
            if verbose:
                print(f"    parcel from {name}")
            attrs = _normalise(attrs, name)
            _cache_write("parcels", key,
                         {"geometry": mapping(poly), "attrs": attrs})
            return poly, attrs
    _cache_write("parcels", key, {"geometry": None, "attrs": None})
    return None, None


def _esri_polygon(poly, max_vertices=250):
    """shapely -> Esri rings JSON, for use as a query geometry.

    Big rural parcels can carry hundreds of vertices, and the whole geometry
    rides in the URL query string. That is what made the 36-acre Murphys Mill
    and 21-acre Backwoods lookups fail while small lots went through. We
    simplify the outline until it fits: a slightly generalised boundary changes
    which flood zones a parcel touches essentially never, and a request that
    completes beats an exact one that gets dropped.
    """
    from shapely.geometry import mapping

    def n_verts(g):
        gj_ = mapping(g)
        if gj_["type"] == "Polygon":
            return sum(len(r) for r in gj_["coordinates"])
        return sum(len(r) for part in gj_["coordinates"] for r in part)

    if n_verts(poly) > max_vertices:
        # tolerance in degrees; ~1e-5 is roughly a metre at this latitude
        for tol in (1e-5, 3e-5, 1e-4, 3e-4, 1e-3):
            simple = poly.simplify(tol, preserve_topology=True)
            if not simple.is_empty and n_verts(simple) <= max_vertices:
                poly = simple
                break
        else:
            poly = poly.convex_hull      # last resort, still the right locale

    gj = mapping(poly)
    if gj["type"] == "Polygon":
        rings = [list(map(list, r)) for r in gj["coordinates"]]
    else:
        rings = [list(map(list, r)) for part in gj["coordinates"] for r in part]
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def _short_zone(zone, subty):
    """Compact label. 'X' + '0.2 PCT ANNUAL CHANCE...' -> 'X0.2'."""
    z = (zone or "?").strip()
    sub = (subty or "").strip().upper()
    if z == "X" and "0.2 PCT" in sub:
        return "X0.2"
    if "FLOODWAY" in sub:
        return z + "-FW"
    return z


def _flood_zones_uncached(poly_4326, verbose=False):
    """FEMA flood picture for one parcel.

    Returns a dict with:
      flood        compact breakdown string, e.g. "AE 74 / X 21 / X0.2 5"
      flood_open   acres in the LARGEST CONTIGUOUS non-SFHA piece of the lot
      flood_sfha   fraction of the lot inside a Special Flood Hazard Area
      flood_bfe    base flood elevation (ft) if the service publishes one

    flood_open is the ranking number, and it is deliberately not an average.
    A lot that is three-quarters AE marsh with a dry buildable corner is still
    buyable -- Matt's cousin bought exactly that. What matters is the size of
    the best usable piece, not the mean condition of the whole parcel.
    """
    from shapely.ops import unary_union

    js = _fetch_json(NFHL, {
        "f": "json",
        "geometry": json.dumps(_esri_polygon(poly_4326)),
        "geometryType": "esriGeometryPolygon",
        "inSR": 4326, "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
        "returnGeometry": "true",
    }, "NFHL", verbose)

    if js is None:
        return {"flood": "lookup failed"}

    feats = js.get("features") or []
    if not feats:
        # Unmapped is NOT the same as safe. Say so rather than implying Zone X.
        return {"flood": "not mapped"}

    parcel_utm = shp_transform(_to_utm, poly_4326)
    total = parcel_utm.area
    if total <= 0:
        return {"flood": "no parcel area"}

    shares, open_parts, bfes = {}, [], []
    for f in feats:
        a = f.get("attributes", {})
        zpoly = _rings_to_polygon(f.get("geometry", {}))
        if zpoly is None or zpoly.is_empty:
            continue
        try:
            inter = parcel_utm.intersection(shp_transform(_to_utm, zpoly))
        except Exception:
            continue
        if inter.is_empty:
            continue

        label = _short_zone(a.get("FLD_ZONE"), a.get("ZONE_SUBTY"))
        shares[label] = shares.get(label, 0.0) + inter.area

        base = (a.get("FLD_ZONE") or "").strip().upper()
        is_sfha = (a.get("SFHA_TF") == "T") or (base in SFHA_ZONES)
        if is_sfha:
            bfe = a.get("STATIC_BFE")
            if bfe not in (None, -9999, "-9999", ""):
                try:
                    bfes.append(float(bfe))
                except (TypeError, ValueError):
                    pass
        else:
            open_parts.append(inter)

    if not shares:
        return {"flood": "no overlap"}

    ordered = sorted(shares.items(), key=lambda kv: -kv[1])
    out = {"flood": " / ".join(f"{k} {v/total*100:.0f}" for k, v in ordered)}

    sfha_area = sum(v for k, v in shares.items()
                    if k.split("-")[0].split("0.2")[0] in SFHA_ZONES)
    out["flood_sfha"] = round(sfha_area / total, 3)

    # Largest CONTIGUOUS non-SFHA piece -- one buildable corner beats the same
    # acreage scattered in slivers.
    if open_parts:
        merged = unary_union(open_parts)
        pieces = list(getattr(merged, "geoms", [merged]))
        out["flood_open"] = round(max(pc.area for pc in pieces) / ACRE_M2, 2)
    else:
        out["flood_open"] = 0.0

    if bfes:
        out["flood_bfe"] = round(max(bfes), 1)
    return out


def _tcc_layer(verbose=False):
    """Current tree-canopy layer name from GetCapabilities, cached per run."""
    global _TCC_LAYER
    if _TCC_LAYER is not None:
        return _TCC_LAYER or None
    try:
        full = MRLC_WMS + "?" + urllib.parse.urlencode(
            {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"})
        with urllib.request.urlopen(full, timeout=60) as r:
            xml = r.read().decode()
    except Exception as e:
        if verbose:
            print(f"    MRLC capabilities failed: {type(e).__name__}")
        _TCC_LAYER = ""
        return None
    import re
    names = re.findall(r"<Name>([^<]+)</Name>", xml)
    cands = [n for n in names
             if ("tcc" in n.lower() or "canopy" in n.lower())
             and "change" not in n.lower()]
    cands.sort(key=lambda n: (("conus" in n.lower() or "l48" in n.lower()), n),
               reverse=True)
    _TCC_LAYER = cands[0] if cands else ""
    return _TCC_LAYER or None


def canopy_pct(poly_4326, verbose=False):
    """Mean percent tree canopy over the parcel. Cached to disk.

    Returns {"canopy_pct": float} or {} if unavailable. Never raises -- a
    missing canopy figure should not stop a parcel being measured.
    """
    c = poly_4326.centroid
    key = _cache_key(c.y, c.x)
    hit = _cache_read("canopy", key, 400)      # NLCD updates annually
    if hit is not None:
        hit.pop("_at", None)
        return hit

    layer = _tcc_layer(verbose)
    if not layer:
        return {}

    minx, miny, maxx, maxy = poly_4326.bounds
    # a small parcel still needs enough pixels to average sensibly
    px = 256
    full = MRLC_WMS + "?" + urllib.parse.urlencode({
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": layer, "styles": "", "srs": "EPSG:4326",
        "bbox": f"{minx},{miny},{maxx},{maxy}",
        "width": px, "height": px, "format": "image/geotiff",
    })
    try:
        with urllib.request.urlopen(full, timeout=90) as r:
            img = r.read()
        import rasterio
        from rasterio.io import MemoryFile
        with MemoryFile(img) as mf, mf.open() as ds:
            a = ds.read(1).astype("float32")
        a[a > 100] = np.nan
        if not np.isfinite(a).any():
            return {}
        out = {"canopy_pct": round(float(np.nanmean(a)), 1)}
    except Exception as e:
        if verbose:
            print(f"    canopy lookup failed: {type(e).__name__}: {str(e)[:60]}")
        return {}

    _cache_write("canopy", key, out)
    return out


def _sda(sql, timeout=90):
    body = json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode()
    req = urllib.request.Request(SDA_POST, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("Table") or []


def _sda_num(v):
    """SDA returns every value as a string, including numerics."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _soil_polygons(poly_4326, timeout=120):
    """Soil map unit polygons over the parcel bbox, axis order corrected."""
    minx, miny, maxx, maxy = poly_4326.bounds
    flt = (f"<Filter><BBOX><PropertyName>Geometry</PropertyName>"
           f"<Box srsName='EPSG:4326'><coordinates>"
           f"{minx},{miny} {maxx},{maxy}</coordinates></Box></BBOX></Filter>")
    url = SDA_WFS + "?" + urllib.parse.urlencode({
        "SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
        "TYPENAME": "MapunitPoly", "FILTER": flt,
        "SRSNAME": "EPSG:4326", "OUTPUTFORMAT": "GML2"})
    with urllib.request.urlopen(url, timeout=timeout) as r:
        gml = r.read()

    import tempfile
    import geopandas as gpd
    from shapely.ops import transform as _tf
    with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as fh:
        fh.write(gml)
        path = fh.name
    gdf = gpd.read_file(path)
    key = next((c for c in gdf.columns if c.lower() == "mukey"), None)
    out = []
    for _, row in gdf.iterrows():
        g = row.geometry
        if g is None or g.is_empty:
            continue
        # In the continental US longitude is NEGATIVE and latitude POSITIVE, so
        # x positive with y negative means the pair is swapped. A magnitude test
        # fails here: y is -75.996 and 75.996 < 90.
        if g.bounds[0] > 0 and g.bounds[1] < 0:
            g = _tf(lambda x, y, z=None: (y, x), g)
        out.append((str(row[key]) if key else "?", g))
    return out


def soil_summary(poly_4326, verbose=False):
    """Four soil numbers per parcel. Cached to disk.

      drained_ac   acres of moderately-well-drained or better. The septic
                   number -- 6664 Blackwater returns 0.00 on 12 acres.
      hydric_pct   percent on wetland-indicator soil.
      wt_depth_in  shallowest annual water table, inches. Drives both septic
                   and whether the driveway needs fabric under the stone.
      soils        dominant soil names, e.g. "Tomotley / Augusta".

    The full breakdown -- every component with its own drainage class, Ksat and
    clay -- is nine lines for one parcel and does not fit a spreadsheet row.
    Run soil.py on a single parcel for that.

    Never raises: a missing soil figure must not stop a parcel being measured.
    """
    c = poly_4326.centroid
    key = _cache_key(c.y, c.x)
    hit = _cache_read("soil", key)          # soil surveys change very rarely
    if hit is not None:
        hit.pop("_at", None)
        return hit

    try:
        polys = _soil_polygons(poly_4326)
    except Exception as e:
        if verbose:
            print(f"    soil polygons failed: {type(e).__name__}: {str(e)[:60]}")
        return {}
    if not polys:
        return {}

    shares = {}
    for mukey, g in polys:
        try:
            inter = poly_4326.intersection(g)
        except Exception:
            continue
        if inter.is_empty:
            continue
        shares[mukey] = shares.get(mukey, 0.0) + (
            shp_transform(_to_utm, inter).area / ACRE_M2)
    if not shares:
        return {}

    keylist = ",".join(f"'{k}'" for k in shares)
    try:
        rows = _sda(f"""
            SELECT c.mukey, c.compname, c.comppct_r, c.drainagecl,
                   c.hydricrating, m.wtdepannmin
            FROM component c
            LEFT JOIN muaggatt m ON m.mukey = c.mukey
            WHERE c.mukey IN ({keylist})
            ORDER BY c.mukey, c.comppct_r DESC
        """)
    except Exception as e:
        if verbose:
            print(f"    soil components failed: {type(e).__name__}")
        return {}
    if len(rows) <= 1:
        return {}

    dominant = {}
    for r in rows[1:]:
        mukey, name, pct, drain, hyd, wt = (list(r) + [None] * 6)[:6]
        mukey = str(mukey)
        if mukey not in dominant:       # ordered by comppct_r desc
            dominant[mukey] = {"name": name, "drain": drain, "hydric": hyd,
                               "wt": _sda_num(wt)}

    total = sum(shares.values()) or 1.0
    drained = sum(ac for k, ac in shares.items()
                  if dominant.get(k, {}).get("drain") in GOOD_DRAINAGE)
    hydric = sum(ac for k, ac in shares.items()
                 if dominant.get(k, {}).get("hydric") == "Yes")
    wts = [dominant[k]["wt"] for k in shares
           if dominant.get(k, {}).get("wt") is not None]
    names = [str(dominant.get(k, {}).get("name") or k)
             for k, _ in sorted(shares.items(), key=lambda kv: -kv[1])[:3]]

    out = {"drained_ac": round(drained, 2),
           "hydric_pct": round(hydric / total * 100, 0),
           "soils": " / ".join(names)}
    if wts:
        out["wt_depth_in"] = round(min(wts), 0)

    _cache_write("soil", key, out)
    return out


def flood_zones(poly_4326, verbose=False):
    """Cached wrapper. See _flood_zones_uncached for the real work."""
    c = poly_4326.centroid
    key = _cache_key(c.y, c.x)
    hit = _cache_read("flood", key, FLOOD_MAX_AGE_DAYS)
    if hit is not None:
        hit.pop("_at", None)
        return hit

    out = _flood_zones_uncached(poly_4326, verbose)
    # Do not cache a transient failure as if it were an answer.
    if out.get("flood") not in ("lookup failed",):
        _cache_write("flood", key, out)
    return out


def pick_dem(poly_utm):
    """Every cached DEM whose bounding box could cover this parcel, best-first.

    Returns a LIST, not one tile, and that matters. Our localities are adjacent
    and each county DEM is a rectangle with nodata everywhere outside the county
    line, so their bounding boxes overlap heavily. The old version returned the
    first alphabetical bbox hit - which meant a Virginia Beach parcel sitting
    inside Chesapeake's bounding box got clipped against Chesapeake's tile, found
    nothing but nodata, and reported "no valid ground cells in parcel". Five of
    twelve listings failed that way.

    Sorted by distance from the parcel to the tile centre, so the tile the parcel
    is genuinely inside gets tried first; the caller falls through the rest until
    one yields real cells.
    """
    if not DEM_DIR.exists():
        return []
    cx, cy = poly_utm.centroid.x, poly_utm.centroid.y
    hits = []
    for tif in sorted(DEM_DIR.glob("*.tif")):
        with rasterio.open(tif) as src:
            b = src.bounds
            if b.left <= cx <= b.right and b.bottom <= cy <= b.top:
                mx, my = (b.left + b.right) / 2, (b.bottom + b.top) / 2
                hits.append((((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5, tif))
    return [t for _, t in sorted(hits, key=lambda h: h[0])]


def _clip_to_dem(tif, poly_utm):
    """Clip one DEM to the parcel. Returns (ft_array, cell_m) or (None, None)."""
    with rasterio.open(tif) as src:
        try:
            arr, _ = rasterio.mask.mask(src, [poly_utm.__geo_interface__],
                                        crop=True, filled=True,
                                        nodata=src.nodata)
        except ValueError:
            return None, None
        cell_m = abs(src.transform.a)
        nod = src.nodata

    z = arr[0].astype("float32")
    if nod is not None:
        z[z == nod] = np.nan
    z[z < -100] = np.nan
    ft = z * M_TO_FT
    ft[ft < -2] = np.nan                       # water / nodata leak
    return ft, cell_m


def terrain_metrics(ft, cell_m):
    """Shape-of-the-ground metrics from an already-clipped, already-masked
    elevation array (feet, NaN outside the parcel).

    Four numbers, deliberately simple, all from the same bare-earth array we
    already have. No new data, no new API call.

      relief_ft  p95 - p5 spread. Robust version of max-minus-min: one spike
                 from a leftover barn roof or a DEM artifact can't inflate it.
                 This is the "does the ground move" number.
      std_ft     standard deviation of elevation. Cheap, and it separates a
                 pancake from a rolling lot better than you'd expect.
      slope_deg  mean slope. Marsh and cleared farm field both sit near zero;
                 anything with a fold in it climbs.
      tri_ft     terrain ruggedness index - mean absolute difference between a
                 cell and its 8 neighbours. Catches SMALL texture (swales,
                 hummocks) that relief and std both average away.

    NOTE ON MARSH: marsh is flat by definition, so it scores near zero on all
    four without any wetland layer, any mask, or any special-case rule. That is
    the whole reason we don't need one.

    These are REPORTED, not thresholded. Nothing here disqualifies a parcel.
    """
    out = {}
    valid = ft[~np.isnan(ft)]
    if valid.size < 9:
        out["terrain_note"] = f"only {valid.size} cells - too small to measure"
        return out

    p5, p95 = np.nanpercentile(ft, [5, 95])
    out["relief_ft"] = round(float(p95 - p5), 2)
    out["std_ft"]    = round(float(np.nanstd(ft)), 2)

    # slope: gradient of the ft surface over cell size in ft
    cell_ft = cell_m * M_TO_FT
    gy, gx = np.gradient(ft, cell_ft)
    grad = np.sqrt(gx ** 2 + gy ** 2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN tiny parcels
        out["slope_deg"] = round(float(np.degrees(np.arctan(np.nanmean(grad)))), 2)

    # TRI: mean |centre - neighbour| over the 8 shifts. np.roll wraps at the
    # edges, so blank the wrapped row/column before differencing.
    diffs = []
    for dy, dx in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
        sh = np.roll(np.roll(ft, dy, axis=0), dx, axis=1)
        if dy == 1:   sh[0, :]  = np.nan
        if dy == -1:  sh[-1, :] = np.nan
        if dx == 1:   sh[:, 0]  = np.nan
        if dx == -1:  sh[:, -1] = np.nan
        diffs.append(np.abs(ft - sh))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        tri = np.nanmean(np.dstack(diffs))
    out["tri_ft"] = round(float(tri), 3) if np.isfinite(tri) else None
    return out


def sample_parcel_elevation(poly_4326, threshold_ft=DEFAULT_THRESHOLD_FT, verbose=False):
    """Clip the bare-earth DEM to the parcel and summarize its elevations."""
    poly_utm = shp_transform(_to_utm, poly_4326)
    cands = pick_dem(poly_utm)
    if not cands:
        return {"error": "no cached DEM covers this parcel"}

    # Try each candidate and keep whichever actually yields ground. A bbox hit
    # is not a data hit: overlapping county rectangles mean the nearest tile can
    # still be all nodata over this parcel. Keep the best, not the first.
    ft = cell_m = tif = None
    best = 0
    for cand in cands:
        f, c = _clip_to_dem(cand, poly_utm)
        if f is None:
            continue
        n = int(np.count_nonzero(~np.isnan(f)))
        if n > best:
            ft, cell_m, tif, best = f, c, cand, n
    if ft is None:
        return {"error": "parcel does not overlap DEM"}
    if best == 0:
        return {"error": f"no valid ground cells in parcel "
                         f"(tried {len(cands)} DEM{'s' if len(cands) > 1 else ''})"}

    valid = ft[~np.isnan(ft)]

    cell_acres = (cell_m * cell_m) / ACRE_M2
    high = valid >= threshold_ft

    # Cross-check: acreage the POLYGON says vs acreage the DEM actually gave us.
    # These should agree closely. When they don't it means either the parcel
    # straddles two DEM tiles (we clip against a single tile, so the far half
    # gets silently dropped) or a big chunk masked out as water/nodata.
    # Either way every stat below is describing less land than you think.
    poly_acres = poly_utm.area / ACRE_M2
    dem_acres  = valid.size * cell_acres
    coverage   = dem_acres / poly_acres if poly_acres > 0 else 0.0

    res = {
        "parcel_acres": round(dem_acres, 2),
        "poly_acres":   round(poly_acres, 2),
        "dem_coverage": round(coverage, 3),
        "max_ft":  round(float(valid.max()), 1),
        "mean_ft": round(float(valid.mean()), 1),
        "min_ft":  round(float(valid.min()), 1),
        "high_acres": round(int(high.sum()) * cell_acres, 2),
        "high_frac": round(float(high.mean()), 3),
        "cell_m": round(cell_m, 1),
        "dem": tif.stem,
    }
    res.update(terrain_metrics(ft, cell_m))

    warns = []
    # Suffolk genuinely reaches 70+ ft, so 60 was firing on real terrain. This
    # only flags a spike that towers over its own lot - the barn-roof signature.
    if res["max_ft"] > 90 or (res.get("relief_ft") and
                              res["max_ft"] - res["mean_ft"] > 40):
        warns.append("tall spike vs lot mean - possible structure, verify by eye")
    if coverage < 0.85:
        warns.append(f"DEM covers only {coverage:.0%} of the polygon - "
                     "tile straddle or water mask, stats are partial")
    if warns:
        res["warn"] = "; ".join(warns)
    return res


def check(lat, lon, threshold_ft=DEFAULT_THRESHOLD_FT,
          min_high_acres=DEFAULT_MIN_HIGH_ACRES, verbose=False, flood=True,
          listed_acres=None):
    """Full test for one coordinate.

    `qualifies` is NOT a verdict on the parcel. It only says the lot has at
    least `min_high_acres` of ground above `threshold_ft`. Both default to 0,
    so by default nothing is disqualified on elevation and every parcel comes
    back with its full set of measurements to be ranked on later."""
    poly, attrs = get_parcel_polygon(lat, lon, verbose=verbose)
    if poly is None:
        return {"qualifies": None, "error": "no parcel found at point"}
    stats = sample_parcel_elevation(poly, threshold_ft, verbose=verbose)
    if "error" in stats:
        return {"qualifies": None, **stats}
    stats["qualifies"] = stats["high_acres"] >= min_high_acres
    stats["threshold_ft"] = threshold_ft

    # source-independent fields the router normalised (works for VA and NC alike)
    for k in ("_source", "_owner", "_address", "_acres_reported"):
        if attrs and k in attrs:
            stats[k.lstrip("_")] = attrs[k]

    # a couple of raw id fields if the service returned them
    for k in ("PARCELID", "LOCALITY", "OWNERNAME", "GPIN", "parno", "cntyname"):
        if attrs and k in attrs:
            stats[k.lower()] = attrs[k]

    # Cross-check: does the polygon we measured match the acreage the county
    # itself publishes? A large gap means the pin landed on a neighbouring
    # parcel, which is silent and would otherwise poison every terrain metric.
    # Compare against poly_acres (pure geometry), NOT parcel_acres (DEM-derived).
    # A marsh or waterfront lot can legitimately have far fewer valid DEM cells
    # than acres, and comparing that to the county figure would cry wolf on
    # exactly the parcels we most want to look at.
    # FEMA flood picture. One extra HTTP call per parcel, so it can be turned
    # off for bulk runs where only terrain matters.
    if flood:
        stats.update(flood_zones(poly, verbose=verbose))
        stats.update(canopy_pct(poly, verbose=verbose))
        stats.update(soil_summary(poly, verbose=verbose))

    # What do we compare the measured polygon against?
    #
    # NC OneMap publishes gisacres, so on that side the county's own figure is
    # the reference. VGIN publishes NO acreage at all -- its statewide parcel
    # layer is geometry and ids only -- so every Virginia parcel had nothing to
    # check against and the cross-check silently did nothing.
    #
    # 6664 Blackwater is why this matters: the recorded plat says Lot 11 is
    # 423,081 SF / 9.713 ac, the listing agrees at 9.71, and we measured 12.13.
    # 25% over, no warning. The VGIN polygon most likely swallows part of the
    # adjacent 7.118 ac Open Space parcel.
    #
    # So: county figure first, listing acreage second. The listing is weaker
    # evidence -- agents round, and lot_sqft is sometimes the parent tract --
    # but it is far better than no check at all.
    rep = attrs.get("_acres_reported") if attrs else None
    rep_src = "county"
    if not rep and listed_acres:
        rep, rep_src = float(listed_acres), "listing"
    got = stats.get("poly_acres")
    if rep and got:
        stats["acres_ratio"] = round(got / rep, 3)
        stats["acres_ref"] = rep_src
        if not 0.85 <= stats["acres_ratio"] <= 1.15:
            stats["acres_warning"] = (
                f"measured {got:.2f} ac vs {rep_src}-reported {rep:.2f} ac "
                f"({stats['acres_ratio']:.2f}x) - polygon may include a "
                f"neighbouring parcel, or the pin is on the wrong lot")
    return stats


# --------------------------------------------------------------------------- #
# Fields a county uses to identify a parcel, best first. NC OneMap publishes
# parno; VGIN's naming varies by contributing locality.
_PARCEL_ID_FIELDS = ("parno", "PARCELID", "GPIN", "gpin", "parcel_id", "PIN")


def _parcel_key(stats):
    """Stable identity for a parcel, or None if the county gave us nothing.

    NOT used for merging rows in --test. A duplicate row costs a glance; a
    wrongly merged row loses a property, and that trade is not worth it.
    This exists for the rejection list, where identity DOES matter: a lot Matt
    has ruled out should stay ruled out when it relists under a new MLS id.
    """
    for k in _PARCEL_ID_FIELDS:
        v = stats.get(k.lower()) or stats.get(k)
        if v not in (None, "", 0, "0"):
            county = stats.get("cntyname") or stats.get("locality") or ""
            return f"{county}|{v}".strip("|")
    return None


def _dedupe_listings(rows):
    """Collapse listings that share a geocode to within about a metre.

    The same lot routinely appears twice: relisted under a new MLS id, or two
    agents on one parcel. Today's run showed 2281 Kings Fork Rd twice, and the
    NC baseline showed Tulls Creek Rd and Caratoke Hwy doubled.

    Deliberately strict. Matching on "nearby pin + same acreage" would be
    wrong: E Ridge Rd lots 9 through 12 in Shawboro are four DIFFERENT parcels,
    all exactly 10.0 acres, side by side. Only an identical geocode counts here.
    The authoritative merge happens after the county tells us the parcel id.
    """
    out, seen = [], {}
    for e in rows:
        key = (round(e["lat"], 5), round(e["lon"], 5))
        if key in seen:
            prior = seen[key]
            prior.setdefault("dupes", []).append(e.get("href"))
            continue
        seen[key] = e
        out.append(e)
    return out


def measure_listings(rows, threshold_ft=DEFAULT_THRESHOLD_FT,
                     min_high_acres=DEFAULT_MIN_HIGH_ACRES,
                     flood=True, limit=None, verbose=True):
    """Listings in -> measured parcels out. THE single measurement path.

    Deduplicates, looks up each parcel, measures terrain and flood, then
    collapses listings that share a county parcel id.

    Exists because find_land.py and --test each grew their own copy of this and
    drifted: find_land still gated on 8 ft of elevation and still called
    lw.MAX_ACRES, deleted hours ago. One function, both callers.

    Returns a list of stats dicts, each with "_listing" attached.
    """
    n_raw = len(rows)
    rows = _dedupe_listings(rows)
    if verbose and n_raw != len(rows):
        print(f"{n_raw - len(rows)} duplicate listing(s) collapsed on identical geocode")

    results = []
    for e in (rows[:limit] if limit else rows):
        r = check(e["lat"], e["lon"], threshold_ft, min_high_acres, flood=flood,
                  listed_acres=e.get("acres"))
        if r.get("error"):
            if verbose:
                print(f"  ?? {r['error'][:44]:44}  {e['addr']}, {e['city']}")
            continue
        r["_listing"] = e
        results.append(r)

    # Collapse listings sitting on the SAME COUNTY PARCEL. 3665 Sandpiper Rd
    # returned 13 listings all reporting the parent tract's 61.46 acres. Safe
    # because the key comes from the county, not the listing: if VGIN says two
    # pins are one parcel, they are one parcel. Cheapest listing's details are
    # kept and the count is shown, so nothing vanishes silently.
    merged, by_parcel = [], {}
    for r in results:
        key = _parcel_key(r)
        if key is None:
            r["_n"] = 1
            merged.append(r)
            continue
        if key in by_parcel:
            prior = by_parcel[key]
            prior["_n"] = prior.get("_n", 1) + 1
            if (r["_listing"].get("price") or 9e12) < \
               (prior["_listing"].get("price") or 9e12):
                prior["_listing"] = r["_listing"]
            continue
        r["_n"] = 1
        by_parcel[key] = r
        merged.append(r)

    if verbose and len(results) != len(merged):
        print(f"{len(results) - len(merged)} listing(s) collapsed onto "
              f"shared county parcels\n")
    return merged


def sort_results(merged, key=None, desc=None):
    """Sort measured parcels by a config-named column."""
    import search_config as cfg
    key = key if key is not None else getattr(cfg, "SORT_BY", "tri")
    desc = desc if desc is not None else getattr(cfg, "SORT_DESC", True)
    field = {"tri": "tri_ft", "relief": "relief_ft", "flood_open": "flood_open",
             "acres": "parcel_acres", "price": None}.get(key, "tri_ft")
    if field:
        merged.sort(key=lambda r: r.get(field)
                    if isinstance(r.get(field), (int, float)) else -1, reverse=desc)
    else:
        merged.sort(key=lambda r: r["_listing"].get("price") or 0, reverse=desc)
    return key, desc


def run_test(threshold_ft, min_high_acres, limit, flood=True, nc=False):
    """End-to-end against today's real land listings (needs land_watch.py)."""
    try:
        import land_watch as lw
    except ImportError:
        print("land_watch.py not found next to this script - needed for --test")
        return 1
    import search_config as cfg
    cities = list(cfg.VA_ALL) + list(cfg.NC_ALL) if nc else list(cfg.JURISDICTIONS)
    rows = []
    for city in cities:
        # one gate, defined once, in search_config -- do not re-implement it here
        rows.extend(lw.qualify(lw.fetch_city(city, days_on=0)))
    merged = measure_listings(rows, threshold_ft, min_high_acres,
                              flood=flood, limit=limit)
    key, _ = sort_results(merged)

    hdr = (f"{'src':4} {'n':>2} {'listed':6} {'max':>5} {'lot_ac':>6} "
           f"{'relief':>6} {'tri':>5} {'openac':>6} {'price':>9}  "
           f"{'flood':22}  address")
    print(hdr)
    print("-" * len(hdr))
    for r in merged:
        e = r["_listing"]
        q = {"VGIN": "VA", "NCOneMap": "NC"}.get(r.get("source"), "??")
        w = "  !" + r["warn"] if r.get("warn") else ""
        if r.get("acres_warning"):
            w += "  !acreage"

        def n(k, w_=6, d=2):
            v = r.get(k)
            return f"{v:{w_}.{d}f}" if isinstance(v, (int, float)) else " " * w_

        cnt = r.get("_n", 1)
        pr = e.get("price")
        # "new" = the feed gave us a builder's house-package listing on this
        # lot. Acreage and terrain are the real parcel; the price is not the
        # land price and has been removed rather than shown wrong.
        listed = e.get("listed") or e.get("type") or "?"
        # "~" marks a row whose coordinates we geocoded ourselves because the
        # feed had none. Median geocoder error is ~93 m against parcels
        # 110-200 m across, so the acreage cross-check below is what makes
        # these trustworthy -- watch for !acreage on these rows especially.
        if e.get("geocoded"):
            listed = "~" + listed[:5]
        print(f" {q:4} {cnt if cnt > 1 else '':>2} {listed:6} {r['max_ft']:5.1f} "
              f"{r['parcel_acres']:6.2f} "
              f"{n('relief_ft')} {n('tri_ft',5)} {n('flood_open',6)} "
              f"{('$' + format(pr, ',')) if pr else ('  --' if listed == 'new' else ''):>9}  "
              f"{str(r.get('flood','')):22}  "
              f"{e['addr']}, {e['city']}{w}")
    print(f"\nsorted by {key} "
          f"({'desc' if getattr(cfg, 'SORT_DESC', True) else 'asc'}) - "
          f"change SORT_BY in search_config.py")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--probe", nargs=2, metavar=("LAT", "LON"), type=float)
    g.add_argument("--point", nargs=2, metavar=("LAT", "LON"), type=float)
    g.add_argument("--test", action="store_true")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_FT,
               help="ft; ground above this counts as 'high'. Default 0 "
                    "= no elevation filtering, which is the intended mode.")
    p.add_argument("--min-high-acres", type=float, default=DEFAULT_MIN_HIGH_ACRES)
    p.add_argument("--no-flood", action="store_true",
                   help="skip the FEMA lookup (one HTTP call per parcel)")
    p.add_argument("--nc", action="store_true",
                   help="include the three NC counties in --test (off by default)")
    p.add_argument("--limit", type=int, default=40)
    a = p.parse_args()

    if a.probe:
        get_parcel_polygon(a.probe[0], a.probe[1], verbose=True)
    elif a.point:
        print(json.dumps(check(a.point[0], a.point[1], a.threshold,
                               a.min_high_acres, verbose=True), indent=2))
    else:
        sys.exit(run_test(a.threshold, a.min_high_acres, a.limit,
                          flood=not a.no_flood, nc=a.nc))
