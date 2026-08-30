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
OUT_XLSX   = HERE / "output" / "land_candidates.xlsx"
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


# ---------------------------------------------------------------------------
# Columns Matt owns. The script NEVER writes these -- it reads whatever is in
# the CSV and carries it forward onto the fresh measurements. Edit them in a
# spreadsheet and rerun; your edits survive.
#
# Keyed on the COUNTY PARCEL ID, not the listing id, so a verdict sticks when a
# lot relists under a new MLS number. That is the whole point: a parcel you
# ruled out stays ruled out.
HUMAN_COLS = ["status", "notes", "price_verified"]

# price_verified: the real asking price, typed in by hand.
#
# realtor.com carries ONE record per address, and where a builder listed a
# house-to-be-built, that is the record we get -- 6664 Blackwater is
# "land, $325,000" in the MLS and "single_family, $954,409" in the feed. We
# blank those prices rather than show a wrong one, which leaves a hole exactly
# where the good lots are. This column is where the number found by hand goes.
#
# NOT used by the price gate. The gate runs in land_watch.qualify() before any
# of this is read, so a verified price under the ceiling cannot rescue a lot
# the feed priced above it. It is for display, sorting and the CSV.

STATUSES = [
    "watching",       # seen it, not ruled out. Most rows live here.
    "candidate",      # worth a visit in December
    "visited",
    "reject-price",   # the ONLY reject that can come back -- see revive_price_rejects
    "reject-terrain", # flat, cleared, exposed
    "reject-access",  # too far, bad road, no frontage
    "reject-other",   # put the reason in notes
]

# A price rejection is about a number that changes. Everything else is about
# the land, which does not. So if the price drops below what it was when you
# rejected it, the status is cleared and the parcel comes back as 'watching'.
REVIVE_PRICE_DROP = 0.90          # 10% below the rejected price

COLS = (["address", "listed", "price"] + HUMAN_COLS +
        ["acres", "parcel_acres", "canopy_pct", "flood",
         "flood_open", "flood_sfha", "relief_ft", "tri_ft", "std_ft", "max_ft",
         "min_sandbridge", "min_coinjock", "source", "n_listings",
         "geocoded", "warn", "url", "parcel_key", "rejected_at_price"])


def read_existing():
    """Load the human columns from the CSV we are about to overwrite.

    Returns {parcel_key: {status, notes, rejected_at_price}}. Missing file,
    missing columns and hand-edits are all fine -- anything unreadable is just
    absent, never an error.
    """
    prior = {}

    # The xlsx is what Matt actually edits (it has the status dropdown), so it
    # wins. The csv stays as a plain-text mirror that diffs well in git.
    if OUT_XLSX.exists():
        try:
            import openpyxl
            wb = openpyxl.load_workbook(OUT_XLSX, data_only=True)
            ws = wb.active
            head = [c.value for c in ws[1]]
            idx = {n: i for i, n in enumerate(head) if n}
            for row in ws.iter_rows(min_row=2, values_only=True):
                key = str(row[idx["parcel_key"]] or "").strip() if "parcel_key" in idx else ""
                if not key:
                    continue
                prior[key] = {
                    "status": str(row[idx["status"]] or "").strip() if "status" in idx else "",
                    "notes":  str(row[idx["notes"]] or "").strip() if "notes" in idx else "",
                    "rejected_at_price": str(
                        row[idx["rejected_at_price"]] or "").strip()
                        if "rejected_at_price" in idx else "",
                    "price_verified": str(row[idx["price_verified"]] or "").strip()
                        if "price_verified" in idx else "",
                }
            return prior
        except Exception as exc:
            print(f"  could not read {OUT_XLSX.name} ({exc}) - falling back to csv")

    if not OUT_CSV.exists():
        return prior
    try:
        with OUT_CSV.open() as f:
            for row in csv.DictReader(f):
                key = (row.get("parcel_key") or "").strip()
                if not key:
                    continue
                prior[key] = {
                    "status": (row.get("status") or "").strip(),
                    "notes":  (row.get("notes") or "").strip(),
                    "rejected_at_price": (row.get("rejected_at_price") or "").strip(),
                    "price_verified": (row.get("price_verified") or "").strip(),
                }
    except (OSError, csv.Error) as exc:
        print(f"  could not read existing {OUT_CSV.name} ({exc}) - "
              f"status and notes will be blank")
    return prior


def _clean_status(raw, addr):
    """Normalise a hand-typed status, and complain loudly about a typo.

    A dropdown in the xlsx prevents most of this, but the csv is plain text and
    nothing stops 'reject-terrian' or a trailing space. Silently ignoring an
    unrecognised value is the dangerous failure: a mistyped 'reject-price'
    would quietly stop reviving on a price drop.
    """
    v = (raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not v or v in STATUSES:
        return v
    import difflib
    near = difflib.get_close_matches(v, STATUSES, n=1, cutoff=0.7)
    if near:
        print(f"  status {raw!r} on {addr} -> read as {near[0]!r}")
        return near[0]
    print(f"  WARNING: status {raw!r} on {addr} is not a known value "
          f"and will be ignored. Valid: {', '.join(STATUSES)}")
    return ""


def merge_human(r, prior):
    """Carry Matt's status and notes forward onto a fresh measurement."""
    key = pe._parcel_key(r) or ""
    r["_key"] = key
    old = prior.get(key, {})
    status = _clean_status(old.get("status", ""),
                           r["_listing"].get("addr", "?"))
    notes = old.get("notes", "")
    at_price = old.get("rejected_at_price", "")

    # Revive a price rejection if the asking price has actually come down.
    if status == "reject-price":
        now = r["_listing"].get("price")
        try:
            was = float(at_price) if at_price else None
        except ValueError:
            was = None
        if now and was and now <= was * REVIVE_PRICE_DROP:
            print(f"  PRICE DROP: {r['_listing'].get('addr')} "
                  f"${was:,.0f} -> ${now:,.0f} - status cleared to 'watching'")
            status, at_price = "watching", ""
            notes = (notes + " | was rejected at "
                     f"${was:,.0f}").strip(" |")
    elif status == "reject-price" or (status == "" and at_price):
        pass

    # Record the price at the moment of rejection, so the revive has a baseline.
    if status == "reject-price" and not at_price:
        pr = r["_listing"].get("price")
        at_price = str(pr) if pr else ""

    r["_status"] = status
    r["_notes"] = notes
    r["_at_price"] = at_price
    r["_price_verified"] = old.get("price_verified", "")
    return r


def to_row(r):
    e = r["_listing"]
    return {
        "status": r.get("_status", ""),
        "notes":  r.get("_notes", ""),
        "parcel_key": r.get("_key", ""),
        "price_verified": r.get("_price_verified", ""),
        "rejected_at_price": r.get("_at_price", ""),
        "address":      f'{e.get("addr","")}, {e.get("city","")}',
        "listed":       e.get("listed"),
        # blank for new construction: that price includes a house that does not
        # exist. A wrong number would be sorted on and believed.
        "price":        e.get("price"),
        "acres":        round(e["acres"], 2) if e.get("acres") else None,
        "parcel_acres": r.get("parcel_acres"),
        "canopy_pct":   r.get("canopy_pct"),
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
    hdr = (f"{'src':4} {'status':14} {'n':>2} {'listed':7} {'lot_ac':>6} "
           f"{'tree%':>6} {'relief':>6} {'tri':>5} {'openac':>6} "
           f"{'SB':>4} {'CJ':>4} {'price':>10}  {'flood':18}  address")
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
        pr, verified = e.get("price"), False
        pv = (r.get("_price_verified") or "").replace("$", "").replace(",", "").strip()
        if pv:
            try:
                pr, verified = float(pv), True
            except ValueError:
                pass
        cnt = r.get("_n", 1)
        warn = ""
        if r.get("warn"):
            warn += "  !verify"
        if r.get("acres_warning"):
            warn += "  !acreage"
        print(f" {src:4} {(r.get('_status') or ''):14} "
              f"{cnt if cnt > 1 else '':>2} {listed:7} "
              f"{n('parcel_acres')} {n('canopy_pct',6,0)} "
              f"{n('relief_ft')} {n('tri_ft',5)} "
              f"{n('flood_open',6)} "
              f"{sb if sb is not None else '  -':>4} "
              f"{cj if cj is not None else '  -':>4} "
              f"{(('$' + format(int(pr), ',') + ('*' if verified else '')) if pr else '   --'):>10}  "
              f"{str(r.get('flood',''))[:18]:18}  "
              f"{e.get('addr','')}, {e.get('city','')}{warn}")
    print(f"\n{len(merged)} parcels, sorted by {key}. "
          f"'~' = we geocoded it, '--' price = new construction (lot "
          f"price not in the feed; type it into price_verified). "
          f"'*' = verified by hand. SB/CJ = minutes to Sandbridge / Coinjock.\n"
          f"tree% = NLCD tree canopy cover. On flat ground it is the ONLY "
          f"source of privacy -- landform screens nothing under ~8 ft relief.")


def write_csv(merged):
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    n_kept = sum(1 for r in merged if r.get("_status"))
    if n_kept:
        print(f"  carried {n_kept} status/notes entr(ies) forward")
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in merged:
            w.writerow(to_row(r))
    print(f"[ok] {len(merged)} parcels -> {OUT_CSV}")


def write_xlsx(merged):
    """The file Matt edits. Status is a real dropdown; measured columns locked.

    Data validation is why this exists -- a csv cannot stop a typo, and a
    mistyped status is silently ignored rather than loudly wrong.
    """
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        print("  openpyxl not installed - skipping xlsx (pip install openpyxl)")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "candidates"

    ws.append(COLS)
    for r in merged:
        row = to_row(r)
        ws.append([row.get(c) for c in COLS])

    hdr_font = Font(name="Arial", bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", start_color="44546A")
    for cell in ws[1]:
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial")

    # Yours to edit -> yellow, the convention for input cells.
    mine = PatternFill("solid", start_color="FFF2CC")
    for name in HUMAN_COLS:
        col = COLS.index(name) + 1
        for row in range(2, ws.max_row + 1):
            ws.cell(row=row, column=col).fill = mine

    # The dropdown.
    dv = DataValidation(type="list",
                        formula1='"' + ",".join(STATUSES) + '"',
                        allow_blank=True, showDropDown=False)
    dv.error = "Pick one of the listed statuses."
    dv.errorTitle = "Not a valid status"
    dv.prompt = "blank = not looked at yet"
    ws.add_data_validation(dv)
    scol = get_column_letter(COLS.index("status") + 1)
    dv.add(f"{scol}2:{scol}{max(ws.max_row, 500)}")

    widths = {"address": 42, "notes": 46, "flood": 24, "url": 12,
              "canopy_pct": 10,
              "price_verified": 14,
              "parcel_key": 26, "status": 15}
    for i, name in enumerate(COLS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 11)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_XLSX)
    print(f"[ok] {len(merged)} parcels -> {OUT_XLSX}   (edit status/notes here)")


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
    prior = read_existing()
    merged = [merge_human(r, prior) for r in merged]
    add_drive_times(merged)
    key, _ = pe.sort_results(merged)
    print_table(merged, key)
    write_csv(merged)
    write_xlsx(merged)
    save_seen({r["_listing"]["pid"] for r in merged if r["_listing"].get("pid")})
    return 0


def check(nc=False, flood=True):
    rows = gather_land(days_on=lw.DAYS_ON_CHECK, nc=nc)
    seen = load_seen()
    fresh = [e for e in rows if e.get("pid") not in seen]
    print(f"{len(rows)} recent listings, {len(fresh)} new")
    if fresh:
        merged = pe.measure_listings(fresh, flood=flood)
        prior = read_existing()
        merged = [merge_human(r, prior) for r in merged]
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
