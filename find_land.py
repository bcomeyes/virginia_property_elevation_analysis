#!/usr/bin/env python3
"""
find_land.py - the whole pipeline in one table + CSV.

  1. listings        land_watch.fetch_city + qualify()   - the gate
  2. measurement     parcel_elevation.measure_listings() - terrain + flood
  3. drive time      drive_time.drive_times()            - one ORS Matrix call

NO HARD GATES beyond the ones in search_config.py, and those are facts only:
jurisdiction, price ceiling, acreage floor, and that it is land. Every measured
number is shown and Matt judges. A wrong threshold silently hides good lots,
and inventory is thin enough that one hidden lot matters.

WHAT CHANGED, and why it is worth knowing:
  This script used to gate on elevation -- 8 ft, quarter acre of it -- and only
  tabled, CSV'd and drive-timed parcels that passed. That rule was wrong in both
  directions. The highest ground in the search area (Holland Rd, 74.8 ft) is the
  flattest and most exposed; the Knotts Island calibration lot is 6.9 ft with a
  buildable Zone X corner and no flood insurance required. Elevation predicted
  neither flood risk nor seclusion. FEMA answers the first, terrain texture the
  second.

  It also carried its own copy of the listing filter, which drifted: it still
  referenced lw.MAX_ACRES after that was deleted, and never picked up geocoding
  or new-construction handling. Both are now gone -- the gate lives in
  land_watch.qualify(), the measurement in parcel_elevation.measure_listings().

MODES
  --sweep   full current inventory -> table + CSV, seed 'seen' state
  --check   only NEW listings since last run -> append + alert   (weekly cron)
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import land_watch as lw
import parcel_elevation as pe
import search_config as cfg

HERE       = Path(__file__).resolve().parent
OUT_CSV    = HERE / "output" / "land_candidates.csv"
STATE_FILE = HERE / "land_find_state.json"


def gather_land(days_on, nc=False):
    """Listings passing the gate. One gate, defined once, in land_watch."""
    cities = list(cfg.VA_ALL) + list(cfg.NC_ALL) if nc else list(cfg.JURISDICTIONS)
    rows = []
    for city in cities:
        rows.extend(lw.qualify(lw.fetch_city(city, days_on=days_on)))
        time.sleep(1.0)
    return rows


def add_drive_times(merged):
    """One ORS Matrix call for the whole set.

    Affordable now because the price and jurisdiction gate already cut the list
    to a couple dozen. It used to be elevation that made this cheap, which is
    also what made it wrong.
    """
    listings = [r["_listing"] for r in merged]
    if not listings:
        return
    try:
        import drive_time as dt
        dt.drive_times(listings)
    except Exception as e:
        print(f"  drive time unavailable ({type(e).__name__}: {e})")


COLS = ["address", "listed", "price", "acres", "parcel_acres", "flood",
        "flood_open", "flood_sfha", "relief_ft", "tri_ft", "std_ft", "max_ft",
        "min_sandbridge", "min_coinjock", "source", "n_listings",
        "geocoded", "warn", "url"]


def to_row(r):
    e = r["_listing"]
    return {
        "address":      f'{e.get("addr","")}, {e.get("city","")}',
        "listed":       e.get("listed"),
        # blank for new construction: that price includes a house that does not
        # exist. A wrong number would be sorted on and believed.
        "price":        e.get("price"),
        "acres":        round(e["acres"], 2) if e.get("acres") else None,
        "parcel_acres": r.get("parcel_acres"),
        "flood":        r.get("flood"),
        "flood_open":   r.get("flood_open"),
        "flood_sfha":   r.get("flood_sfha"),
        "relief_ft":    r.get("relief_ft"),
        "tri_ft":       r.get("tri_ft"),
        "std_ft":       r.get("std_ft"),
        "max_ft":       r.get("max_ft"),
        "min_sandbridge": e.get("min_sandbridge"),
        "min_coinjock":   e.get("min_coinjock"),
        "source":       r.get("source"),
        "n_listings":   r.get("_n", 1),
        "geocoded":     "yes" if e.get("geocoded") else "",
        "warn":         "; ".join(x for x in (r.get("warn"),
                                              r.get("acres_warning")) if x),
        "url":          e.get("href", ""),
    }


def print_table(merged, key):
    hdr = (f"{'src':4} {'n':>2} {'listed':7} {'lot_ac':>6} {'relief':>6} "
           f"{'tri':>5} {'openac':>6} {'SB':>4} {'CJ':>4} {'price':>9}  "
           f"{'flood':20}  address")
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in merged:
        e = r["_listing"]
        src = {"VGIN": "VA", "NCOneMap": "NC"}.get(r.get("source"), "??")
        listed = e.get("listed") or "?"
        if e.get("geocoded"):
            listed = "~" + listed[:6]

        def n(k, w=6, d=2):
            v = r.get(k)
            return f"{v:{w}.{d}f}" if isinstance(v, (int, float)) else " " * w

        sb, cj = e.get("min_sandbridge"), e.get("min_coinjock")
        pr = e.get("price")
        cnt = r.get("_n", 1)
        warn = ""
        if r.get("warn"):
            warn += "  !verify"
        if r.get("acres_warning"):
            warn += "  !acreage"
        print(f" {src:4} {cnt if cnt > 1 else '':>2} {listed:7} "
              f"{n('parcel_acres')} {n('relief_ft')} {n('tri_ft',5)} "
              f"{n('flood_open',6)} "
              f"{sb if sb is not None else '  -':>4} "
              f"{cj if cj is not None else '  -':>4} "
              f"{('$' + format(pr, ',')) if pr else '   --':>9}  "
              f"{str(r.get('flood',''))[:20]:20}  "
              f"{e.get('addr','')}, {e.get('city','')}{warn}")
    print(f"\n{len(merged)} parcels, sorted by {key}. "
          f"'~' = we geocoded it, '--' price = new construction (lot price "
          f"not in the feed). SB/CJ = minutes to Sandbridge / Coinjock.")


def write_csv(merged):
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in merged:
            w.writerow(to_row(r))
    print(f"[ok] {len(merged)} parcels -> {OUT_CSV}")


def load_seen():
    try:
        return set(json.loads(STATE_FILE.read_text()).get("seen", []))
    except (OSError, ValueError):
        return set()


def save_seen(ids):
    STATE_FILE.write_text(json.dumps(
        {"seen": sorted(ids), "at": datetime.now(timezone.utc).isoformat()},
        indent=2))


def sweep(nc=False, flood=True):
    cfg.show()
    rows = gather_land(days_on=0, nc=nc)
    print(f"{len(rows)} listings pass the gate; measuring...")
    merged = pe.measure_listings(rows, flood=flood)
    add_drive_times(merged)
    key, _ = pe.sort_results(merged)
    print_table(merged, key)
    write_csv(merged)
    save_seen({r["_listing"]["pid"] for r in merged if r["_listing"].get("pid")})
    return 0


def check(nc=False, flood=True):
    rows = gather_land(days_on=lw.DAYS_ON_CHECK, nc=nc)
    seen = load_seen()
    fresh = [e for e in rows if e.get("pid") not in seen]
    print(f"{len(rows)} recent listings, {len(fresh)} new")
    if fresh:
        merged = pe.measure_listings(fresh, flood=flood)
        add_drive_times(merged)
        key, _ = pe.sort_results(merged)
        print_table(merged, key)
        if merged:
            body = "\n".join(
                f"{r['_listing'].get('addr')}, {r['_listing'].get('city')} - "
                f"{r.get('parcel_acres')}ac, {r.get('flood')}, "
                f"tri {r.get('tri_ft')}\n  {r['_listing'].get('href')}"
                for r in merged)
            lw.notify(f"NEW land: {len(merged)} parcel(s)", body)
    save_seen(seen | {e["pid"] for e in rows if e.get("pid")})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sweep", action="store_true", help="full inventory")
    g.add_argument("--check", action="store_true", help="new listings only (cron)")
    ap.add_argument("--nc", action="store_true", help="include the NC counties")
    ap.add_argument("--no-flood", action="store_true", help="skip FEMA lookups")
    a = ap.parse_args()
    sys.exit(sweep(a.nc, not a.no_flood) if a.sweep
             else check(a.nc, not a.no_flood))
