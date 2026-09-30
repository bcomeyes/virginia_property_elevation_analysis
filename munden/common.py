"""
Shared geometry and paths for the Munden Point upland screen.

Everything is worked in EPSG:26918 (UTM 18N, metres) because that is the native
grid of all three 1 m DEMs -- no resampling anywhere. Elevations are NAVD88
metres in the files and converted to feet only for display.

Rick's lines come from site_docs/munden/munden_point.kml, traced from his
Exhibit 2 to about 20 ft. The KML has no Wetland #1 polygon; it is defined here
as study area minus upland minus Wetland #2 minus a ditch strip, which is how
Rick's own acreages add up (18.64 + 2.51 + 0.23 + ~4.0 = 25.39).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DOCS = REPO / "site_docs" / "munden"
CACHE = ROOT / "cache"
OUT = ROOT / "out"
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

CRS = 26918
M_TO_FT = 3.280839895
ACRE_M2 = 4046.8564224
BUFFER_M = 61.0                      # 200 ft around the parcel

# Rick's ditch is 10,016 sq ft over 1,358 ft: ~7.4 ft wide on average.
DITCH_HALF_WIDTH_M = 10016 / 1358 / 2 / M_TO_FT

# Ground truth from Rick's delineation: which polygon each data point is in.
UPLAND_PTS = [1, 2, 3, 5, 6]
WETLAND_PTS = [4, 7, 8, 9]

DEMS = {
    # key: (label, url). All on the same USGS 1 m tile grid, x40y405.
    "2013": ("Norfolk 2013 (Rick's source)",
             "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
             "VA_Norfolk_2013/TIFF/USGS_one_meter_x40y405_VA_Norfolk_2013.tif"),
    "2020": ("NC Hurricane Florence 2020",
             "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
             "NC_HurricaneFlorence_2020_D20/TIFF/"
             "USGS_1M_18_x40y405_NC_HurricaneFlorence_2020_D20.tif"),
    "2023": ("VA Hampton Roads 2023",
             "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
             "VA_HamptonRoads_B23/TIFF/USGS_1M_18_x40y405_VA_HamptonRoads_B23.tif"),
}

VGIN = ("https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
        "VA_Base_Layers/VA_Parcels/MapServer/0/query")
PARCEL_PIN = (36.5807, -76.0210)


def rick():
    """Rick's features in UTM: dict of name -> geometry, plus points GeoDataFrame."""
    g = gpd.read_file(DOCS / "munden_point.kml").to_crs(CRS)
    feats = {}
    pts = []
    for name, geom in zip(g["Name"], g.geometry):
        n = name.lower()
        if n.startswith("rick point"):
            num = int(n.split()[2])
            pts.append({"point": num,
                        "rick": "upland" if num in UPLAND_PTS else "wetland",
                        "geometry": Point(geom.x, geom.y)})
        elif n.startswith("study"):
            feats["study"] = geom
        elif n.startswith("upland"):
            feats["upland"] = geom
        elif n.startswith("wetland 2"):
            feats["wet2"] = geom
        elif n.startswith("ditch"):
            feats["ditch"] = geom
    ditch_strip = feats["ditch"].buffer(DITCH_HALF_WIDTH_M)
    feats["ditch_strip"] = ditch_strip
    feats["wet1"] = (feats["study"].difference(feats["upland"])
                     .difference(feats["wet2"]).difference(ditch_strip))
    feats["wetland"] = feats["wet1"].union(feats["wet2"])
    points = gpd.GeoDataFrame(pts, crs=CRS).sort_values("point").reset_index(drop=True)
    return feats, points


def parcel():
    """Authoritative parcel polygon from VGIN, cached, in UTM."""
    f = CACHE / "parcel_vgin.geojson"
    if not f.exists():
        lat, lon = PARCEL_PIN
        q = urllib.parse.urlencode({
            "f": "geojson", "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint", "inSR": 4326, "outSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
            "returnGeometry": "true"})
        for attempt in range(4):        # VGIN throws intermittent 500s
            try:
                with urllib.request.urlopen(VGIN + "?" + q, timeout=60) as r:
                    f.write_bytes(r.read())
                break
            except urllib.error.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
    js = json.loads(f.read_text())
    feat = js["features"][0]
    g = gpd.GeoDataFrame([feat["properties"]], geometry=[shape(feat["geometry"])],
                         crs=4326).to_crs(CRS)
    return g.geometry.iloc[0], feat["properties"]


def dem_path(key):
    return CACHE / f"dem_{key}.tif"
