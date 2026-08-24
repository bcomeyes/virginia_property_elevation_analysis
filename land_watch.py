#!/usr/bin/env python3
"""
land_watch.py - email me when NEW land is listed across my six jurisdictions.

Sibling of revel_watch.py, same philosophy: self-contained, calibrate against a
known baseline, only alert on the delta, be honest about what the data can't do.

Lives inside the virginia_property_elevation_analysis project so it reads the
API key from .env - no copying, always current.

WHAT IT DOES
  Each run asks the US Real Estate Listings API (RapidAPI) for recently-listed
  properties across three Virginia cities and three North Carolina counties,
  keeps the ones that are land in my acreage window, and emails me only when a
  genuinely NEW one appears.

  It does NOT judge the land. Terrain and flood zone are measured per parcel by
  parcel_elevation.py and used to RANK. Nothing is discarded on quality here.

WHY IT STAYS CHEAP
  It does NOT sweep whole jurisdictions every run. days_on asks only for
  listings added in the last 8 days, so a weekly run is a handful of calls.
  A FULL sweep (--now) is far more expensive: Suffolk alone returns ~726
  listings at 50 per page. Use --now sparingly.

SETUP
    ./land_watch.py --baseline     # current land in the window; sanity check
    ./land_watch.py --baseline --nc   # same, including the three NC counties
    ./land_watch.py --install      # prints the cron + email setup
    ./land_watch.py --now          # full check right now (wider days_on)
    ./land_watch.py --check        # what cron runs (recent-only, cheap)
    tail -f land_watch.log

EMAIL: Gmail app password, set in crontab as env vars, never in this file
(exactly like revel_watch).
"""

import argparse, json, math, os, shutil, smtplib, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request
from email.message import EmailMessage
from datetime import datetime, timezone
from pathlib import Path

HERE       = Path(__file__).resolve().parent
STATE_FILE = HERE / "land_state.json"
LOG_FILE   = HERE / "land_watch.log"
ENV_FILE   = HERE / ".env"

# --- what counts as a hit ----------------------------------------------------
# Six jurisdictions straddling the state line. VA entries are independent
# cities (Virginia Beach and Chesapeake are former counties absorbed by cities,
# so there is no "Virginia Beach County"); NC entries are true counties.
#
# The word "County" is load-bearing on the NC side. Verified against the API:
#   "Currituck County, NC"  -> 675 listings across Corolla, Moyock, Shawboro...
#   "Currituck, NC"         ->  25 listings, town of Currituck only
# Dropping "County" silently narrows to the town of the same name.
CITIES_VA   = ["Chesapeake, VA", "Virginia Beach, VA", "Suffolk, VA"]
CITIES_NC   = ["Currituck County, NC", "Camden County, NC", "Gates County, NC"]

# North Carolina is OFF by default -- pass --nc to include it.
#
# Not a data problem. NC land is dramatically cheaper for the same commute:
# 54 ac in Barco at $675k against 45.8 ac on Holland Rd at $3.1M. It stays
# wired up and one flag away so that comparison can be pulled up on demand,
# rather than being decided by omission. Currituck also drags in ~70 Corolla
# and Carova beach lots with no acreage listed -- barrier island, reachable
# from the mainland only by driving south through Dare County and back up the
# beach. Another reason the default is off.
CITIES      = list(CITIES_VA)
MIN_ACRES   = 8.0
MAX_ACRES   = 60.0
# RETIRED: high-ground pocket matching.
# This used to keep only listings within 500 m of a >=20 ft pocket from
# output/regions_20ft.csv. Two reasons it is gone:
#   1. That CSV is wrong -- notebook 01 discards ground above 50 ft as artifact,
#      which deleted the real 75 ft terrain in western Suffolk.
#   2. More fundamentally, elevation turned out to be the wrong criterion. The
#      highest lots we measured (Holland Rd, 74.8 ft) are the flattest, and the
#      Knotts Island calibration lot is 6.9 ft with a buildable Zone X corner.
# Filtering on height hid good lots and promoted bad ones. Terrain and flood
# zone are now measured per parcel in parcel_elevation.py and RANKED, not gated.
# The only hard filters left here are facts: it is land, and it is in the
# acreage window.

# --- API ---------------------------------------------------------------------
HOST = "us-real-estate-listings.p.rapidapi.com"
URL  = f"https://{HOST}/for-sale"
PAGE = 50               # API page size
DAYS_ON_CHECK = 8       # cron runs weekly; 8 days gives a day of overlap
                        # so nothing slips through between runs
DAYS_ON_NOW   = 0       # --now: 0 = no recency filter (full current inventory)
MAX_PAGES     = 100       # hard stop so one run can't run away with your quota


def load_env():
    """Minimal .env reader so we don't need python-dotenv for a standalone run."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
GMAIL_USER   = os.environ.get("GMAIL_USER", "")
GMAIL_PASS   = os.environ.get("GMAIL_APP_PASSWORD", "")
MAIL_TO      = os.environ.get("MAIL_TO", "") or GMAIL_USER
NTFY         = os.environ.get("LAND_NTFY_TOPIC", "")
ACRE_SQFT    = 43560.0


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0; p = math.radians
    dlat = p(lat2 - lat1); dlon = p(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(p(lat1))*math.cos(p(lat2))*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def fetch_city(city, days_on, tries=3):
    """Page through /for-sale for one city. Returns list of raw listings."""
    if not RAPIDAPI_KEY:
        log("  no RAPIDAPI_KEY in .env"); return []
    out, offset = [], 0
    for _ in range(MAX_PAGES):
        params = {"location": city, "offset": offset, "limit": PAGE,
                  "sort": "newest"}
        if days_on and days_on > 0:
            params["days_on"] = days_on
        url = URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": HOST,
            "Content-Type": "application/json"})
        data = None
        for n in range(tries):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode()); break
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and n < tries - 1:
                    time.sleep(5 * (n + 1)); continue
                log(f"  {city}: HTTP {e.code}"); break
            except Exception as e:
                if n < tries - 1:
                    time.sleep(3); continue
                log(f"  {city}: {type(e).__name__}"); break
        if not data:
            break
        batch = data.get("listings") or []
        out.extend(batch)
        total = data.get("totalResultCount", len(out))
        offset += PAGE
        if offset >= total or not batch:
            break
        time.sleep(1.0)
    return out


def extract(listing):
    d   = listing.get("description") or {}
    loc = (listing.get("location") or {}).get("address") or {}
    c   = loc.get("coordinate") or {}
    return dict(
        pid=listing.get("property_id"),
        type=(d.get("type") or "").lower(),
        acres=(d.get("lot_sqft") or 0) / ACRE_SQFT,
        price=listing.get("list_price"),
        lat=c.get("lat"), lon=c.get("lon"),
        addr=loc.get("line"), city=loc.get("city"),
        zip=loc.get("postal_code"),
        href=listing.get("href"),
    )


def qualify(listings, pockets=None):
    """Keep land in the acreage window. Nothing else is filtered here.

    `pockets` is accepted and ignored, so older callers keep working. Terrain
    and flood zone are measured per parcel downstream by parcel_elevation.py
    and used for RANKING -- a lot never disappears before Matt has seen it.
    """
    hits = []
    for L in listings:
        e = extract(L)
        if "land" not in (e["type"] or ""):
            continue
        if e["lat"] is None or e["lon"] is None:
            continue
        # acreage: allow unknown (0) through - land often omits lot_sqft
        if e["acres"] and not (MIN_ACRES <= e["acres"] <= MAX_ACRES):
            continue
        hits.append(e)
    return hits


def notify(title, body):
    sent = []
    if GMAIL_USER and GMAIL_PASS and MAIL_TO:
        try:
            msg = EmailMessage()
            msg["Subject"] = title; msg["From"] = GMAIL_USER; msg["To"] = MAIL_TO
            msg.set_content(body)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
                s.login(GMAIL_USER, GMAIL_PASS); s.send_message(msg)
            sent.append(f"email->{MAIL_TO}")
        except Exception as e:
            log(f"  email FAILED: {type(e).__name__}: {e}")
    else:
        log("  email skipped (GMAIL_USER/APP_PASSWORD not set)")
    if NTFY:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{NTFY}", data=body.encode(),
                headers={"Title": title, "Priority": "high"}, method="POST"),
                timeout=15).read()
            sent.append("phone")
        except Exception as e:
            log(f"  ntfy FAILED: {e}")
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-u", "normal", "-t", "0", title, body[:400]],
                       check=False, timeout=10)
        sent.append("desktop")
    log(f"  ALERT via: {', '.join(sent) if sent else 'NOTHING (set email or LAND_NTFY_TOPIC)'}")


def fmt(h):
    price = f"${h['price']:,}" if h.get("price") else "price n/a"
    ac    = f"{h['acres']:.1f}ac" if h.get("acres") else "acreage n/a"
    return f"{h['addr']}, {h['city']} - {price}, {ac}\n  {h['href']}"


def run(days_on, label):
    log(f"{label}: {len(CITIES)} jurisdiction(s) "
        f"({'VA+NC' if len(CITIES) > 3 else 'VA only'}), "
        f"days_on={days_on or 'all'}")
    all_hits = []
    for city in CITIES:
        raw = fetch_city(city, days_on)
        h = qualify(raw)
        log(f"  {city:24} {len(raw):4d} listings -> {len(h)} land in window")
        all_hits.extend(h)
        time.sleep(1.0)
    return all_hits


def check(days_on=DAYS_ON_CHECK):
    hits = run(days_on, "check")
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        state = {}
    seen = set(state.get("seen", []))
    now  = {h["pid"] for h in hits}
    fresh = [h for h in hits if h["pid"] not in seen]

    if not hits:
        log("  no land in the acreage window this run")
    else:
        log(f"  {len(hits)} land listing(s) ({len(fresh)} new)")
    if fresh:
        body = (f"{len(fresh)} NEW land listing(s) across the six jurisdictions:\n\n"
                + "\n\n".join(fmt(h) for h in fresh))
        notify(f"LAND ALERT: {len(fresh)} new listing(s)", body)

    state["seen"] = sorted(seen | now)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return 0


def baseline():
    log("=== BASELINE: all current land in the acreage window ===")
    hits = run(DAYS_ON_NOW, "baseline")
    if not hits:
        log("RESULT: zero land in the acreage window right now.")
        log("        Check the acreage bounds before assuming the API is wrong.")
    else:
        log(f"RESULT: {len(hits)} land listing(s) in the window right now:")
        for h in sorted(hits, key=lambda x: -(x.get("acres") or 0)):
            log("   " + fmt(h).replace("\n  ", "  |  "))
        log("        If these look right, the filter works. Cron will alert on NEW ones.")
    # seed state so the first cron run doesn't email the whole current set
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        state = {}
    state["seen"] = sorted({h["pid"] for h in hits})
    state["baseline_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))
    log(f"   (seeded {len(state['seen'])} current listings as 'seen' - "
        f"you'll only be emailed about NEW ones)")
    return 0


def install():
    py = shutil.which("python3") or "/usr/bin/python3"
    me = Path(__file__).resolve()
    print("\n" + "="*68)
    print("STEP 1 - Gmail app password (one time)")
    print("="*68)
    print("  https://myaccount.google.com/apppasswords  -> make one, copy it\n")
    print("="*68); print("STEP 2 - run:  crontab -e   and add:"); print("="*68)
    print(f"""
GMAIL_USER={GMAIL_USER or 'you@gmail.com'}
GMAIL_APP_PASSWORD={'*'*16 if GMAIL_PASS else 'abcdefghijklmnop'}
MAIL_TO={MAIL_TO or 'you@gmail.com'}
# Mondays at 7:05am - only listings added in the last 8 days:
5 7 * * 1 cd {me.parent} && {py} {me} --check >/dev/null 2>&1
""")
    print("="*68); print("STEP 3 - seed the baseline first so you aren't spammed:"); print("="*68)
    print(f"  cd {me.parent} && ./land_watch.py --baseline\n")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline", action="store_true", help="current land on high ground; seed state")
    g.add_argument("--install",  action="store_true", help="print cron + email setup")
    g.add_argument("--check",    action="store_true", help="cron: recent-only, email new")
    g.add_argument("--now",      action="store_true", help="full current check, email new")
    p.add_argument("--nc", action="store_true",
                   help="also search Currituck, Camden and Gates counties, NC "
                        "(off by default; cheaper land, different state)")
    a = p.parse_args()
    if a.nc:
        CITIES = CITIES_VA + CITIES_NC
    if a.baseline: sys.exit(baseline())
    if a.install:  sys.exit(install())
    if a.now:      sys.exit(check(days_on=DAYS_ON_NOW))
    sys.exit(check(days_on=DAYS_ON_CHECK))
