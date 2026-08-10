#!/usr/bin/env python3
"""
drive_time.py - real road drive time from parcels to the two access anchors.

Step 3 of the pipeline. By the time we get here the list is already tiny (only
parcels that passed acreage + elevation), so we spend the accurate method -
actual driving time - not a straight-line guess. Around Back Bay, the Dismal
Swamp, and the sounds, straight-line distance is actively misleading, so it is
not used anywhere here.

ANCHORS
  Sandbridge  36.7357, -75.9527   weekday drivable ocean beach   (rule: <=30 min)
  Coinjock    36.3495, -75.9478   ICW marina / weekend boat       (rule: <=60 min)

A parcel is access-viable if it's within 30 min of Sandbridge OR 60 min of
Coinjock. This module REPORTS both times (no hard gate) so you judge by eye;
the OR-rule is applied only as a convenience flag.

Uses OpenRouteService Matrix V2: all parcels x both anchors in ONE call.
Needs ORS_API_KEY in .env.
"""

import json, os, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV  = HERE / ".env"

ANCHORS = {
    "Sandbridge": (36.7357, -75.9527, 30),   # (lat, lon, minutes limit)
    "Coinjock":   (36.3495, -75.9478, 60),
}
ORS_MATRIX = "https://api.openrouteservice.org/v2/matrix/driving-car"


def _load_key():
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith("ORS_API_KEY=") and "=" in line:
                return line.split("=", 1)[1].strip()
    return os.environ.get("ORS_API_KEY", "")


def drive_times(parcels, verbose=False):
    """
    parcels: list of dicts each with 'lat','lon' (and whatever else).
    Returns the same list with 'min_sandbridge' and 'min_coinjock' added
    (minutes, rounded) plus 'access_ok' bool. One ORS Matrix call total.
    """
    key = _load_key()
    if not key:
        print("  no ORS_API_KEY in .env"); return parcels
    if not parcels:
        return parcels

    # ORS wants [lon, lat]. Sources = parcels, destinations = the two anchors.
    src = [[p["lon"], p["lat"]] for p in parcels]
    dst = [[ANCHORS["Sandbridge"][1], ANCHORS["Sandbridge"][0]],
           [ANCHORS["Coinjock"][1],   ANCHORS["Coinjock"][0]]]
    locations = src + dst
    n = len(parcels)
    body = {
        "locations": locations,
        "sources":       list(range(n)),
        "destinations":  [n, n + 1],           # the two anchors
        "metrics": ["duration"],
    }
    req = urllib.request.Request(
        ORS_MATRIX, data=json.dumps(body).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            js = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  ORS HTTP {e.code}: {e.read().decode()[:200]}"); return parcels
    except Exception as e:
        print(f"  ORS error: {type(e).__name__}: {e}"); return parcels

    if verbose:
        print(json.dumps(js, indent=2)[:1200])

    durations = js.get("durations")            # seconds, [parcel][anchor]
    if not durations:
        print("  ORS returned no durations"); return parcels

    for i, p in enumerate(parcels):
        row = durations[i]
        sb = row[0] / 60.0 if row[0] is not None else None
        cj = row[1] / 60.0 if row[1] is not None else None
        p["min_sandbridge"] = round(sb) if sb is not None else None
        p["min_coinjock"]   = round(cj) if cj is not None else None
        ok_sb = sb is not None and sb <= ANCHORS["Sandbridge"][2]
        ok_cj = cj is not None and cj <= ANCHORS["Coinjock"][2]
        p["access_ok"] = bool(ok_sb or ok_cj)
    return parcels


# quick standalone test: feed it the Kings Fork coordinate area
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--point", nargs=2, type=float, metavar=("LAT", "LON"))
    a = ap.parse_args()
    pts = [{"lat": a.point[0], "lon": a.point[1], "addr": "test point"}] if a.point \
          else [{"lat": 36.70, "lon": -76.60, "addr": "western Suffolk (Kings Fork area)"}]
    out = drive_times(pts, verbose=True)
    for p in out:
        print(f"\n{p['addr']}: "
              f"Sandbridge {p.get('min_sandbridge')} min, "
              f"Coinjock {p.get('min_coinjock')} min, "
              f"access_ok={p.get('access_ok')}")
