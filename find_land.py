#!/usr/bin/env python3
"""
find_land.py - the whole pipeline in one table.

Ties the three proven pieces together:
  1. land listings          (land_watch.fetch_city)      - acreage filter
  2. parcel elevation       (parcel_elevation.check)     - high ground ON the lot
  3. drive time             (drive_time.drive_times)     - one ORS Matrix call

Output: ONE ranked table + CSV. No hard gates - every real number is shown and
you judge. Each parcel is measured individually, so eastern-Suffolk (close) and
western-Suffolk (far) sort themselves out by their own drive time, not by county.

MODES
  --sweep   full current inventory -> table + CSV, seed 'seen' state   (run once)
  --check   only NEW listings since last run -> append + alert          (daily cron)

Cheap by design: elevation cuts the list to a handful BEFORE any drive-time call,
so ORS sees ~6 parcels, not 1200. Listings use days_on in --check so a daily run
is a few API calls.
"""

import argparse, csv, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

import land_watch as lw
import parcel_elevation as pe
import drive_time as dt

HERE       = Path(__file__).resolve().parent
OUT_CSV    = HERE / "output" / "land_candidates.csv"
STATE_FILE = HERE / "land_find_state.json"

THRESHOLD_FT   = 8.0      # elevation bar (reported, not a hard gate)
MIN_HIGH_ACRES = 0.25      # how much ground >= threshold to count as "has high ground"


def gather_land(days_on):
    """Full inventory (days_on=0) or recent-only. Returns land in acreage window."""
    rows = []
    for city in lw.CITIES:
        raw = lw.fetch_city(city, days_on=days_on)
        for L in raw:
            e = lw.extract(L)
            if "land" not in e["type"] or not e["lat"]:
                continue
            if e["acres"] and not (lw.MIN_ACRES <= e["acres"] <= lw.MAX_ACRES):
                continue
            rows.append(e)
        time.sleep(1.0)
    return rows


def enrich(rows):
    """Add elevation stats, then drive times (one Matrix call for survivors)."""
    kept = []
    for e in rows:
        stats = pe.check(e["lat"], e["lon"], THRESHOLD_FT, MIN_HIGH_ACRES)
        if stats.get("error") or stats.get("qualifies") is None:
            e["elev_note"] = stats.get("error", "no parcel")
            e["max_ft"] = e["high_acres"] = None
            continue
        e["max_ft"]      = stats["max_ft"]
        e["high_acres"]  = stats["high_acres"]
        e["parcel_acres"] = stats["parcel_acres"]
        e["elev_warn"]   = stats.get("warn", "")
        e["has_high"]    = stats["qualifies"]
        kept.append(e)

    # drive time only on parcels that actually have high ground
    survivors = [e for e in kept if e.get("has_high")]
    if survivors:
        dt.drive_times(survivors)
    return kept


def to_row(e):
    return {
        "address": f'{e.get("addr","")}, {e.get("city","")}',
        "price": e.get("price"),
        "acres": round(e["acres"], 1) if e.get("acres") else None,
        "max_ft": e.get("max_ft"),
        "high_acres": e.get("high_acres"),
        "min_sandbridge": e.get("min_sandbridge"),
        "min_coinjock": e.get("min_coinjock"),
        "warn": e.get("elev_warn", ""),
        "url": e.get("href", ""),
    }


def print_table(rows):
    hi = [r for r in rows if r.get("has_high")]
    hi.sort(key=lambda e: (min(e.get("min_sandbridge") or 9e9,
                               e.get("min_coinjock") or 9e9)))
    print(f"\n{'max':>5} {'hi_ac':>6} {'SB':>4} {'CJ':>4}  {'price':>9}  address")
    print("-" * 78)
    for e in hi:
        sb = e.get("min_sandbridge"); cj = e.get("min_coinjock")
        price = f"${e['price']:,}" if e.get("price") else "     n/a"
        warn = "  !verify" if e.get("elev_warn") else ""
        print(f"{e['max_ft']:5.0f} {e['high_acres']:6.1f} "
              f"{sb if sb is not None else '  -':>4} {cj if cj is not None else '  -':>4}  "
              f"{price:>9}  {e.get('addr','')}, {e.get('city','')}{warn}")
    print(f"\n{len(hi)} parcels with high ground (of {len(rows)} land listings checked)")
    print("SB=min to Sandbridge (rule <=30)   CJ=min to Coinjock (rule <=60)")


def write_csv(rows):
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    hi = [to_row(e) for e in rows if e.get("has_high")]
    cols = ["address","price","acres","max_ft","high_acres",
            "min_sandbridge","min_coinjock","warn","url"]
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in hi:
            w.writerow(r)
    print(f"[ok] {len(hi)} candidates -> {OUT_CSV}")


def load_seen():
    try:
        return set(json.loads(STATE_FILE.read_text()).get("seen", []))
    except (OSError, ValueError):
        return set()


def save_seen(ids):
    STATE_FILE.write_text(json.dumps(
        {"seen": sorted(ids), "at": datetime.now(timezone.utc).isoformat()}, indent=2))


def sweep():
    print("=== FULL SWEEP: all current land on high ground ===")
    rows = gather_land(days_on=0)
    print(f"{len(rows)} land listings in window; checking elevation + drive time...")
    rows = enrich(rows)
    print_table(rows)
    write_csv(rows)
    save_seen({e["pid"] for e in rows if e.get("pid")})
    return 0


def check():
    rows = gather_land(days_on=lw.DAYS_ON_CHECK)
    seen = load_seen()
    fresh = [e for e in rows if e.get("pid") not in seen]
    print(f"{len(rows)} recent land listings, {len(fresh)} new")
    if fresh:
        fresh = enrich(fresh)
        hi = [e for e in fresh if e.get("has_high")]
        if hi:
            body = "\n".join(
                f"{e['max_ft']:.0f}ft, {e['high_acres']:.1f}ac high, "
                f"SB {e.get('min_sandbridge')}min CJ {e.get('min_coinjock')}min - "
                f"{e.get('addr')}, {e.get('city')}\n  {e.get('href')}" for e in hi)
            lw.notify(f"NEW high-ground land: {len(hi)}", body)
        print_table(fresh)
    save_seen(seen | {e["pid"] for e in rows if e.get("pid")})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sweep", action="store_true", help="full inventory (run once)")
    g.add_argument("--check", action="store_true", help="new listings only (daily cron)")
    a = ap.parse_args()
    sys.exit(sweep() if a.sweep else check())
