#!/usr/bin/env python3
"""
find_land.py - the whole pipeline into one shared Google Sheet.

  1. listings        land_watch.fetch_city + qualify()   - the gate
  2. measurement     parcel_elevation.measure_listings() - terrain + flood
  3. drive time      drive_time.drive_times()            - one ORS Matrix call
  4. output          one live Google Sheet (+ local csv/xlsx backup)

NO HARD GATES beyond the ones in search_config.py, and those are facts only:
jurisdiction, price ceiling, acreage floor, and that it is land. Every measured
number is shown and Matt and Larissa judge. A wrong threshold silently hides
good lots, and inventory is thin enough that one hidden lot matters.

TWO RULES THIS FILE ENFORCES, both learned the hard way:

  1. The script never writes status, notes or price_verified. Those are read
     before anything else and written back untouched.

  2. A row with ANY of those three filled in survives every filter, forever.
     Dropping Suffolk must not be able to delete 7399 Crittenden, which is
     marked 'candidate' with a hand-verified price on it. Carried rows keep
     their last measurements, frozen, and say so in `warn`.

MODES
  --sweep   full current inventory -> sheet + csv + xlsx
  --check   only NEW listings since last run -> append + alert   (weekly cron)
"""

import argparse
import csv
import json
import os
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

# Column order and the human columns both come from the parameter page now.
COLS       = list(cfg.COLUMN_ORDER)
HUMAN_COLS = list(cfg.HUMAN_COLS)

# Columns that must come back as numbers when read from a sheet, where
# everything arrives as text. Sorting and the table both need real floats.
NUMERIC_COLS = {
    "price", "acres", "min_sandbridge", "canopy_pct", "parcel_acres",
    "drained_ac", "hydric_pct", "wt_depth_in", "flood_open", "flood_sfha",
    "relief_ft", "tri_ft", "std_ft", "max_ft", "min_coinjock", "n_listings",
}

STATUSES = [
    "watching",       # seen it, not ruled out. Most rows live here.
    "candidate",      # worth a visit in December
    "visited",
    "reject-price",   # the ONLY reject that can come back
    "reject-terrain", # flat, cleared, exposed
    "reject-access",  # too far, bad road, no frontage
    "reject-other",   # put the reason in notes
]

# A price rejection is about a number that changes. Everything else is about
# the land, which does not. If the price drops below what it was when you
# rejected it, the status clears back to 'watching'.
REVIVE_PRICE_DROP = 0.90

CARRIED_NOTE = "carried forward - outside current filters, measurements frozen"


# ---------------------------------------------------------------------------
# listings
# ---------------------------------------------------------------------------
def gather_land(days_on, nc=False):
    """Listings passing the gate. One gate, defined once, in land_watch."""
    cities = list(cfg.VA_ALL) + list(cfg.NC_ALL) if nc else list(cfg.JURISDICTIONS)
    rows = []
    for city in cities:
        rows.extend(lw.qualify(lw.fetch_city(city, days_on=days_on)))
        time.sleep(1.0)
    return rows


def add_drive_times(merged):
    """One ORS Matrix call for the whole set."""
    listings = [r["_listing"] for r in merged]
    if not listings:
        return
    try:
        import drive_time as dt
        dt.drive_times(listings)
    except Exception as e:
        print(f"  drive time unavailable ({type(e).__name__}: {e})")


# ---------------------------------------------------------------------------
# the Google Sheet - the one place Matt and Larissa both type
# ---------------------------------------------------------------------------
def _sheet():
    """Open the configured worksheet, or return None with a plain reason.

    Never raises. A sheet that cannot be reached degrades to csv + xlsx; it
    does not take the whole sweep down after 15 minutes of measuring.
    """
    if not cfg.SHEET_ID:
        return None
    key = os.environ.get(cfg.GOOGLE_KEY_ENV, "")
    if not key:
        print(f"  sheet: ${cfg.GOOGLE_KEY_ENV} not set - skipping "
              f"(csv + xlsx still written)")
        return None
    if not Path(key).exists():
        print(f"  sheet: key file not found at {key} - skipping")
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("  sheet: pip install gspread google-auth - skipping")
        return None
    try:
        creds = Credentials.from_service_account_file(
            key, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        sh = gspread.authorize(creds).open_by_key(cfg.SHEET_ID)
        try:
            return sh.worksheet(cfg.SHEET_TAB)
        except Exception:
            return sh.add_worksheet(cfg.SHEET_TAB, rows=200, cols=len(COLS))
    except Exception as e:
        # The overwhelmingly likely cause, worth naming rather than dumping a
        # stack trace: the sheet has not been shared with the service account.
        print(f"  sheet: cannot open ({type(e).__name__}: {e})")
        print(f"         check the sheet is shared as EDITOR with the "
              f"client_email inside {key}")
        return None


def _numify(row):
    for c in NUMERIC_COLS:
        v = row.get(c)
        if isinstance(v, str):
            s = v.replace("$", "").replace(",", "").strip()
            if s:
                try:
                    row[c] = float(s)
                except ValueError:
                    pass
            else:
                row[c] = None
    return row


def read_sheet(ws):
    """Every row currently in the sheet, as dicts keyed by our column names."""
    vals = ws.get_all_values()
    if not vals:
        return []
    head = vals[0]
    out = []
    for raw in vals[1:]:
        if not any(x.strip() for x in raw):
            continue
        row = {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(head) if h}
        out.append(_numify(row))
    return out


def write_sheet(rows):
    """Rewrite the grid in place. Same sheet, same URL, forever."""
    ws = _sheet()
    if ws is None:
        return
    grid = [COLS]
    for r in rows:
        line = []
        for c in COLS:
            v = r.get(c)
            line.append("" if v is None else v)
        grid.append(line)

    try:
        need = len(grid) + 50
        if ws.row_count < need:
            ws.resize(rows=need, cols=max(ws.col_count, len(COLS)))
        ws.clear()
        ws.update(values=grid, range_name="A1",
                  value_input_option="USER_ENTERED")
        _format_sheet(ws, len(grid))
        print(f"[ok] {len(rows)} parcels -> google sheet "
              f"https://docs.google.com/spreadsheets/d/{cfg.SHEET_ID}/edit")
    except Exception as e:
        print(f"  sheet write failed ({type(e).__name__}: {e}) - "
              f"csv and xlsx are still good")


def _format_sheet(ws, n_rows):
    """Frozen bold header, yellow on the three columns that are yours, and a
    real dropdown on status so a typo cannot silently disable the price
    revive."""
    sid = ws.id
    yellow = {"backgroundColor": {"red": 1, "green": 0.95, "blue": 0.80}}

    def col_range(name, extra=None):
        i = COLS.index(name)
        r = {"sheetId": sid, "startRowIndex": 1, "endRowIndex": max(n_rows, 500),
             "startColumnIndex": i, "endColumnIndex": i + 1}
        return r

    reqs = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sid,
                           "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True,
                               "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "backgroundColor": {"red": 0.27, "green": 0.33, "blue": 0.42},
                "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(textFormat,backgroundColor,wrapStrategy)"}},
    ]
    for name in HUMAN_COLS:
        reqs.append({"repeatCell": {
            "range": col_range(name),
            "cell": {"userEnteredFormat": yellow},
            "fields": "userEnteredFormat.backgroundColor"}})
    reqs.append({"setDataValidation": {
        "range": col_range("status"),
        "rule": {"condition": {"type": "ONE_OF_LIST",
                               "values": [{"userEnteredValue": s}
                                          for s in STATUSES]},
                 "strict": False, "showCustomUi": True}}})
    try:
        ws.spreadsheet.batch_update({"requests": reqs})
    except Exception as e:
        print(f"  sheet formatting skipped ({type(e).__name__})")


# ---------------------------------------------------------------------------
# reading what is already there
# ---------------------------------------------------------------------------
def read_existing():
    """Load prior human columns AND the full prior rows.

    Returns (prior, archive):
      prior   {parcel_key: {status, notes, rejected_at_price, price_verified}}
      archive {parcel_key: full row dict, exactly as last written}

    The archive is what lets an annotated parcel survive a filter change: if it
    is not in today's sweep we re-emit the stored row rather than losing it.

    Source order is deliberate. The sheet is where typing now happens, so it
    wins; the xlsx is the old habit and the fallback; the csv is the plain-text
    mirror of last resort. Anything unreadable is simply absent, never fatal.
    """
    rows = []

    ws = _sheet()
    if ws is not None:
        try:
            rows = read_sheet(ws)
            if rows:
                print(f"  read {len(rows)} existing row(s) from the sheet")
        except Exception as exc:
            print(f"  could not read the sheet ({exc}) - falling back to xlsx")

    if not rows and OUT_XLSX.exists():
        try:
            import openpyxl
            wb = openpyxl.load_workbook(OUT_XLSX, data_only=True)
            ws2 = wb.active
            head = [c.value for c in ws2[1]]
            for raw in ws2.iter_rows(min_row=2, values_only=True):
                row = {h: raw[i] for i, h in enumerate(head)
                       if h and i < len(raw)}
                if any(v not in (None, "") for v in row.values()):
                    rows.append(_numify(row))
        except Exception as exc:
            print(f"  could not read {OUT_XLSX.name} ({exc}) - trying csv")

    if not rows and OUT_CSV.exists():
        try:
            with OUT_CSV.open() as f:
                rows = [_numify(dict(r)) for r in csv.DictReader(f)]
        except (OSError, csv.Error) as exc:
            print(f"  could not read {OUT_CSV.name} ({exc}) - "
                  f"status and notes will be blank")

    prior, archive = {}, {}
    for row in rows:
        key = str(row.get("parcel_key") or "").strip()
        if not key:
            continue
        prior[key] = {
            "status": str(row.get("status") or "").strip(),
            "notes":  str(row.get("notes") or "").strip(),
            "rejected_at_price": str(row.get("rejected_at_price") or "").strip(),
            "price_verified": str(row.get("price_verified") or "").strip(),
        }
        archive[key] = row
    return prior, archive


def _clean_status(raw, addr):
    """Normalise a hand-typed status, and complain loudly about a typo.

    Silently ignoring an unrecognised value is the dangerous failure: a
    mistyped 'reject-price' would quietly stop reviving on a price drop.
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
    """Carry status, notes and the verified price onto a fresh measurement."""
    key = pe._parcel_key(r) or ""
    r["_key"] = key
    old = prior.get(key, {})
    status = _clean_status(old.get("status", ""),
                           r["_listing"].get("addr", "?"))
    notes = old.get("notes", "")
    at_price = old.get("rejected_at_price", "")

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
            notes = (notes + f" | was rejected at ${was:,.0f}").strip(" |")
        elif not at_price:
            pr = r["_listing"].get("price")
            at_price = str(pr) if pr else ""

    r["_status"] = status
    r["_notes"] = notes
    r["_at_price"] = at_price
    r["_price_verified"] = old.get("price_verified", "")
    return r


def shared_parcel_warning(r):
    """Several listings resolving to ONE county parcel means the parcel is a
    shared tract -- a campground, a marina, a subdivision still sitting on its
    parent deed. Every measurement on the row then describes that whole tract,
    not the lot being sold.

    This is the only cross-check available when the listing publishes no
    acreage. VGIN carries no acreage field, so a blank listing acreage leaves
    acres_ratio with nothing to compare and it stays silent -- which is exactly
    how 3665 Sandpiper Rd Unit 235 reached the TOP of the sheet (9 min to
    Sandbridge, the best drive time in the set) describing 61.46 ac of
    campground common ground, 100% SFHA, water table at 0 in.

    A flag, not a gate. The row still shows; it just stops pretending the
    numbers are about a buildable lot.
    """
    n = r.get("_n", 1) or 1
    if float(n) < 2:
        return ""
    ac = r.get("parcel_acres")
    tract = f"{ac:.2f} ac tract" if isinstance(ac, (int, float)) else "whole tract"
    return (f"{int(float(n))} listings share this county parcel - every "
            f"measurement describes the {tract}, not the lot being sold")


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
        # blank for new construction: that price includes a house that does
        # not exist. A wrong number would be sorted on and believed.
        "price":        e.get("price"),
        "acres":        round(e["acres"], 2) if e.get("acres") else None,
        "parcel_acres": r.get("parcel_acres"),
        "canopy_pct":   r.get("canopy_pct"),
        # soil: drained_ac is the septic number, hydric_pct the wetland
        # indicator, wt_depth_in drives both septic and whether the driveway
        # needs fabric. Run soil.py on a single lot for full detail.
        "drained_ac":   r.get("drained_ac"),
        "hydric_pct":   r.get("hydric_pct"),
        "wt_depth_in":  r.get("wt_depth_in"),
        "soils":        r.get("soils"),
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
                                              r.get("acres_warning"),
                                              shared_parcel_warning(r)) if x),
        "url":          e.get("href", ""),
    }


# ---------------------------------------------------------------------------
# the rule that protects hand-entered work
# ---------------------------------------------------------------------------
def annotated(row):
    """Has a human typed anything on this row?"""
    return any(str(row.get(c) or "").strip() for c in HUMAN_COLS)


def in_jurisdiction(row):
    """Is this row in a place we are still looking?

    Cheap string match on the city in the address, because that is all an
    archived row carries. NC rows are matched by source instead, since their
    addresses give a town ('Moyock') and JURISDICTIONS gives a county
    ('Currituck County, NC').

    Unknown city -> True. Never delete somebody's work on a guess.
    """
    cities = {j.split(",")[0].strip().lower() for j in cfg.JURISDICTIONS}
    if str(row.get("source") or "") == "NCOneMap":
        return any(j.strip().upper().endswith(", NC") for j in cfg.JURISDICTIONS)
    city = str(row.get("address") or "").rsplit(",", 1)[-1].strip().lower()
    return city in cities if city else True


def enforce_measured_acres(rows):
    """Drop rows whose MEASURED parcel is under the floor.

    The gate could only check the acreage the listing claimed, and plenty of
    land listings publish none. This is the number we computed ourselves, so
    it is the one that settles it. Annotated rows are exempt.
    """
    if not getattr(cfg, "ENFORCE_MEASURED_ACRES", False) or not cfg.MIN_ACRES:
        return rows
    keep, dropped = [], []
    for r in rows:
        a = r.get("parcel_acres")
        if isinstance(a, (int, float)) and a < cfg.MIN_ACRES and not annotated(r):
            dropped.append(r)
        else:
            keep.append(r)
    if dropped:
        print(f"  dropped {len(dropped)} row(s): measured under "
              f"{cfg.MIN_ACRES:g} acres despite passing the listing gate")
        for r in sorted(dropped, key=lambda x: x.get("parcel_acres") or 0):
            print(f"    {r.get('parcel_acres'):>6.2f} ac  {r.get('address','?')}")
    return keep


def carry_forward(out, archive):
    """Re-emit annotated rows that a FILTER TWEAK would have dropped.

    Price and acreage are tweaks: nudge the number, see what shakes loose.
    A tweak must never delete work done by hand, so an annotated row survives
    one, measurements frozen and flagged in `warn`.

    Jurisdiction is not a tweak. Dropping Suffolk was a decision with a reason
    behind it, and 7399 Crittenden being marked 'candidate' does not make the
    drive to Sandbridge any shorter. Those rows go - but they are named on the
    way out, so it is never silent.
    """
    if not cfg.ANNOTATED_ROWS_BYPASS_FILTERS:
        return out
    here = {str(r.get("parcel_key") or "") for r in out}
    carried, released = [], []
    for key, row in archive.items():
        if key in here or not annotated(row):
            continue
        if not in_jurisdiction(row):
            released.append(row)
            continue
        row = dict(row)
        w = str(row.get("warn") or "")
        if CARRIED_NOTE not in w:
            row["warn"] = "; ".join(x for x in (w, CARRIED_NOTE) if x)
        carried.append(row)
    if carried:
        print(f"  carried {len(carried)} annotated row(s) past the filters:")
        for row in carried:
            print(f"    {row.get('address','?')}  "
                  f"[{row.get('status') or 'notes only'}]")
    if released:
        print(f"  released {len(released)} annotated row(s) - jurisdiction "
              f"no longer searched:")
        for row in released:
            print(f"    {row.get('address','?')}  "
                  f"[{row.get('status') or 'notes only'}]")
    return out + carried


def sort_rows(rows):
    """Multi-key sort from cfg.SORT. Blanks always sink to the bottom."""
    def key(r):
        out = []
        for col, desc in cfg.SORT:
            v = r.get(col)
            try:
                v = float(str(v).replace("$", "").replace(",", ""))
            except (TypeError, ValueError):
                v = None
            out.append((1, 0.0) if v is None else (0, -v if desc else v))
        return tuple(out)
    return sorted(rows, key=key)


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
def print_table(rows):
    hdr = (f"{'src':4} {'status':14} {'n':>2} {'listed':7} {'lot_ac':>6} "
           f"{'tree%':>6} {'dry_ac':>6} {'hyd%':>5} {'wt_in':>5} "
           f"{'tri':>5} {'openac':>6} "
           f"{'SB':>4} {'CJ':>4} {'price':>10}  {'flood':18}  address")
    print("\n" + hdr)
    print("-" * len(hdr))
    for row in rows:
        src = {"VGIN": "VA", "NCOneMap": "NC"}.get(row.get("source"), "??")
        listed = str(row.get("listed") or "?")
        if row.get("geocoded"):
            listed = "~" + listed[:6]

        def n(k, w=6, d=2):
            v = row.get(k)
            return f"{v:{w}.{d}f}" if isinstance(v, (int, float)) else " " * w

        sb, cj = row.get("min_sandbridge"), row.get("min_coinjock")
        pr, verified = row.get("price"), False
        pv = str(row.get("price_verified") or "").replace("$", "").replace(",", "").strip()
        if pv:
            try:
                pr, verified = float(pv), True
            except ValueError:
                pass
        cnt = row.get("n_listings") or 1
        w = str(row.get("warn") or "")
        flag = ""
        if CARRIED_NOTE in w:
            flag += "  !carried"
        if "verify" in w or "structure" in w:
            flag += "  !verify"
        if "acre" in w and "carried" not in w:
            flag += "  !acreage"
        if "share this county parcel" in w:
            flag += "  !shared"
        print(f" {src:4} {str(row.get('status') or ''):14} "
              f"{int(cnt) if cnt and float(cnt) > 1 else '':>2} {listed:7} "
              f"{n('parcel_acres')} {n('canopy_pct',6,0)} "
              f"{n('drained_ac',6)} {n('hydric_pct',5,0)} "
              f"{n('wt_depth_in',5,0)} {n('tri_ft',5)} "
              f"{n('flood_open',6)} "
              f"{int(sb) if isinstance(sb,(int,float)) else '  -':>4} "
              f"{int(cj) if isinstance(cj,(int,float)) else '  -':>4} "
              f"{(('$' + format(int(pr), ',') + ('*' if verified else '')) if pr else '   --'):>10}  "
              f"{str(row.get('flood') or '')[:18]:18}  "
              f"{str(row.get('address') or '')}{flag}")
    order = " then ".join(f"{c} {'desc' if d else 'asc'}" for c, d in cfg.SORT)
    print(f"\n{len(rows)} parcels, sorted by {order}. "
          f"'~' = we geocoded it, '--' price = new construction (lot price not "
          f"in the feed; type it into price_verified). '*' = verified by hand. "
          f"'!carried' = annotated row kept past the filters, numbers frozen. "
          f"'!shared' = several listings on one county parcel, so the "
          f"measurements are the whole tract, not the lot.\n"
          f"SB/CJ = minutes to Sandbridge / Coinjock.\n"
          f"tree% = NLCD tree canopy cover. On flat ground it is the ONLY "
          f"source of privacy -- landform screens nothing under ~8 ft relief.\n"
          f"dry_ac = acres of moderately-well-drained or better soil (the "
          f"septic number). hyd% = on wetland-indicator soil. wt_in = "
          f"shallowest seasonal water table, inches.")


def write_csv(rows):
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    n_kept = sum(1 for r in rows if str(r.get("status") or "").strip())
    if n_kept:
        print(f"  carried {n_kept} status entr(ies) forward")
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, restval="",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"[ok] {len(rows)} parcels -> {OUT_CSV}")


def write_xlsx(rows):
    """Local backup of the sheet. Same columns, same order, same dropdown."""
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
    ws.title = cfg.SHEET_TAB

    ws.append(COLS)
    for r in rows:
        ws.append([r.get(c) for c in COLS])

    hdr_font = Font(name="Arial", bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", start_color="44546A")
    for cell in ws[1]:
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial")

    mine = PatternFill("solid", start_color="FFF2CC")
    for name in HUMAN_COLS:
        col = COLS.index(name) + 1
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=col).fill = mine

    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"',
                        allow_blank=True, showDropDown=False)
    dv.error = "Pick one of the listed statuses."
    dv.errorTitle = "Not a valid status"
    dv.prompt = "blank = not looked at yet"
    ws.add_data_validation(dv)
    scol = get_column_letter(COLS.index("status") + 1)
    dv.add(f"{scol}2:{scol}{max(ws.max_row, 500)}")

    widths = {"address": 42, "notes": 46, "flood": 24, "url": 12,
              "canopy_pct": 10, "soils": 34, "drained_ac": 11,
              "hydric_pct": 10, "wt_depth_in": 11, "price_verified": 14,
              "min_sandbridge": 13, "parcel_key": 26, "status": 15}
    for i, name in enumerate(COLS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 11)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(OUT_XLSX)
    except PermissionError:
        print(f"  {OUT_XLSX.name} is open in another program - xlsx not written")
        return
    print(f"[ok] {len(rows)} parcels -> {OUT_XLSX}")


def emit(merged, archive):
    """Measurements -> rows -> carry-forward -> sort -> every output."""
    rows = [to_row(r) for r in merged]
    missing = set(rows[0]) - set(COLS) if rows else set()
    if missing:
        print(f"  WARNING: {sorted(missing)} produced but not in "
              f"COLUMN_ORDER - they will not appear anywhere")
    rows = sort_rows(carry_forward(enforce_measured_acres(rows), archive))
    print_table(rows)
    write_sheet(rows)
    write_csv(rows)
    write_xlsx(rows)
    return rows


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
    prior, archive = read_existing()       # BEFORE measuring: if the sweep
                                           # dies halfway, nothing was lost
    rows = gather_land(days_on=0, nc=nc)
    print(f"{len(rows)} listings pass the gate; measuring...")
    merged = pe.measure_listings(rows, flood=flood)
    merged = [merge_human(r, prior) for r in merged]
    add_drive_times(merged)
    emit(merged, archive)
    save_seen({r["_listing"]["pid"] for r in merged if r["_listing"].get("pid")})
    return 0


def check(nc=False, flood=True):
    prior, archive = read_existing()
    rows = gather_land(days_on=lw.DAYS_ON_CHECK, nc=nc)
    seen = load_seen()
    fresh = [e for e in rows if e.get("pid") not in seen]
    print(f"{len(rows)} recent listings, {len(fresh)} new")
    if fresh:
        merged = pe.measure_listings(fresh, flood=flood)
        merged = [merge_human(r, prior) for r in merged]
        add_drive_times(merged)
        emit(merged, archive)
        if merged:
            body = "\n".join(
                f"{r['_listing'].get('addr')}, {r['_listing'].get('city')} - "
                f"{r.get('parcel_acres')}ac, {r.get('flood')}, "
                f"{r['_listing'].get('min_sandbridge')} min to Sandbridge\n"
                f"  {r['_listing'].get('href')}"
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
