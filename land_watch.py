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

import argparse, json, math, os, re, shutil, smtplib, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request
from email.message import EmailMessage
from datetime import datetime, timezone
from pathlib import Path

HERE       = Path(__file__).resolve().parent
STATE_FILE = HERE / "land_state.json"
LOG_FILE   = HERE / "land_watch.log"
ENV_FILE   = HERE / ".env"

# --- what counts as a hit -----------------------------------------------------
# All of this now lives in search_config.py. Nothing search-related is defined
# here any more: constants in this file are exactly how the 8-acre minimum sat
# for months contradicting what Matt actually wanted, with nothing forcing it
# to surface.
import search_config as cfg

CITIES      = cfg.JURISDICTIONS
MIN_ACRES   = cfg.MIN_ACRES
MAX_PRICE   = cfg.MAX_PRICE
EXCLUDE_UNIT_ADDRESSES = getattr(cfg, "EXCLUDE_UNIT_ADDRESSES", True)

# A unit number means the address is a site INSIDE a development, so there is
# no parcel of its own to measure -- see EXCLUDE_UNIT_ADDRESSES in
# search_config.py. Matches "Unit 235", "#235", "Apt 4", "Ste 200", "Trlr 7".
# Does NOT match "Lot 12": that is ordinary for raw land. Word boundaries keep
# it off street names like "Unity Church Rd".
_UNIT_RE = re.compile(
    r"""(?:
          \b (?: unit | apt | apartment | ste | suite | trlr ) \b \.? \s* \#? \s* [A-Za-z-]* \d
        | \# \s* [A-Za-z-]* \d
        )""",
    re.I | re.X,
)


def is_unit_address(addr):
    """True if the address carries a unit designator."""
    return bool(addr and _UNIT_RE.search(str(addr)))

# kept so older calls to --nc still resolve
CITIES_VA   = cfg.VA_ALL
CITIES_NC   = cfg.NC_ALL

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


# --- geocoding fallback ------------------------------------------------------
# realtor.com does not geocode new subdivisions or new builds: every Blackwater
# Rd lot we want arrives with (None, None), while the old resale houses on the
# same road have coordinates. qualify() drops anything without a pin, so the
# lots Matt most wants were invisible for a reason that has nothing to do with
# the land.
#
# VGIN cannot help -- its statewide parcel layer is geometry and ids only
# (VGIN_QPID, FIPS, LOCALITY, PARCELID), no address field at all.
#
# Measured against three Blackwater addresses whose coordinates we already knew:
#     census      median error  94 m, 0 failures
#     nominatim   median error  93 m, 0 failures
#     photon      median error 977 m  -- pinned the street, not the address
# Census and Nominatim agreed with each other to within a few metres on every
# ungeocoded target, which is decent evidence both are right.
#
# 93 m is NOT comfortable when a parcel is 110-200 m across. That slop is only
# acceptable because parcel_elevation.check() cross-checks the measured polygon
# acreage against the listing's stated acreage and warns on a mismatch. A
# geocoded point that lands on the neighbour's lot shows up as an acreage
# disagreement rather than as quietly wrong terrain.

# Cached to DISK, not just memory. An address's coordinates do not change, and
# an in-memory cache dies with the process -- so every run was re-geocoding
# everything from scratch, which is what made a sweep take minutes before it
# printed anything at all.
_GEOCODE_FILE = HERE / "cache" / "geocode.json"
_GEOCODE_UA = "land-search/1.0 (personal property research)"


def _load_geocode_cache():
    try:
        return json.loads(_GEOCODE_FILE.read_text())
    except (OSError, ValueError):
        return {}


_GEOCODE_CACHE = _load_geocode_cache()


def _save_geocode_cache():
    try:
        _GEOCODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GEOCODE_FILE.write_text(json.dumps(_GEOCODE_CACHE, indent=1))
    except OSError:
        pass


def _geo_census(addr):
    url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
           f"?address={urllib.parse.quote(addr)}"
           "&benchmark=Public_AR_Current&format=json")
    req = urllib.request.Request(url, headers={"User-Agent": _GEOCODE_UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        js = json.loads(r.read().decode())
    m = js["result"]["addressMatches"]
    if not m:
        return None
    c = m[0]["coordinates"]
    return c["y"], c["x"]


def _geo_nominatim(addr):
    url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1"
           f"&q={urllib.parse.quote(addr)}")
    req = urllib.request.Request(url, headers={"User-Agent": _GEOCODE_UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        js = json.loads(r.read().decode())
    if not js:
        return None
    return float(js[0]["lat"]), float(js[0]["lon"])


def geocode(addr, city, state, zipcode=None):
    """(lat, lon) for a listing the feed did not geocode, or (None, None).

    Census first, Nominatim second -- they scored the same and either is fine,
    but hitting one service by default halves the load on both. Cached in
    memory so a repeated address inside one run costs nothing.
    """
    if not addr or not city:
        return None, None
    full = f"{addr}, {city}, {state}" + (f" {zipcode}" if zipcode else "")
    if full in _GEOCODE_CACHE:
        got = _GEOCODE_CACHE[full]
        return tuple(got) if got else (None, None)

    for fn, pause in ((_geo_census, 0.4), (_geo_nominatim, 1.2)):
        try:
            got = fn(full)
        except Exception:
            got = None
        time.sleep(pause)          # Nominatim asks for <= 1 request/second
        if got:
            _GEOCODE_CACHE[full] = list(got)
            _save_geocode_cache()
            return got
    _GEOCODE_CACHE[full] = None       # remember the failure too
    _save_geocode_cache()
    return None, None


def extract(listing):
    d   = listing.get("description") or {}
    loc = (listing.get("location") or {}).get("address") or {}
    c   = loc.get("coordinate") or {}
    flags = listing.get("flags") or {}

    # realtor.com carries ONE record per address, and where a builder has
    # listed a house-to-be-built on a lot, that is the record we get -- typed
    # single_family, priced as a package. 6664 Blackwater is "land, $325,000"
    # in the MLS and "single_family, $954,409" here, same 9.71 acres, same
    # parcel. The land is real and for sale; the house does not exist.
    #
    # So we let these through, and we DELETE THE PRICE. The package figure is
    # wrong by hundreds of thousands for our purposes, and a wrong number is
    # more dangerous than a blank one -- it would be gated on, sorted on, and
    # believed. Terrain, flood and acreage are unaffected: those come from the
    # county parcel, not from the listing.
    new_construction = bool(flags.get("is_new_construction"))
    price = listing.get("list_price")
    if new_construction:
        price = None

    return dict(
        pid=listing.get("property_id"),
        type=(d.get("type") or "").lower(),
        listed="new" if new_construction else (d.get("type") or "").lower(),
        new_construction=new_construction,
        acres=(d.get("lot_sqft") or 0) / ACRE_SQFT,
        price=price,
        lat=c.get("lat"), lon=c.get("lon"),
        addr=loc.get("line"), city=loc.get("city"),
        zip=loc.get("postal_code"),
        href=listing.get("href"),
    )


def _state_of(e):
    """VA unless the listing's city is one of the NC jurisdictions."""
    city = (e.get("city") or "").lower()
    for j in CITIES_NC:
        if city and city.split(",")[0].strip() in j.lower():
            return "NC"
    return "NC" if (e.get("zip") or "").startswith("27") else "VA"


def qualify(listings, pockets=None):
    """Apply the gate from search_config: land, acreage floor, price ceiling.

    These are the ONLY things allowed to remove a listing before Matt sees it,
    and each is a fact rather than a judgement. Terrain and flood zone are
    measured per parcel downstream and shown as columns -- never used to hide
    a lot. Inventory is thin enough that one hidden lot matters.

    `pockets` is accepted and ignored so older callers keep working.
    """
    hits = []
    for L in listings:
        e = extract(L)

        # ---- free checks first. Never pay for a network lookup on a listing
        # we were going to drop anyway. ----------------------------------
        # plain land, OR a lot carrying a proposed build (see extract())
        if "land" not in (e["type"] or "") and not e["new_construction"]:
            continue

        # Still the "is it land" check: a unit number means a site inside a
        # development, and the county parcel beneath it is the development's
        # common tract, so every measurement downstream would describe that
        # tract instead of the lot. 3665 Sandpiper Rd Unit 235 sat at the top
        # of the sheet for weeks on exactly this.
        if EXCLUDE_UNIT_ADDRESSES and is_unit_address(e["addr"]):
            continue

        # Acreage. A rural LAND listing genuinely may omit lot_sqft, and
        # dropping those would lose real parcels -- so unknown still passes for
        # plain land. But a new-construction listing with no acreage is a city
        # infill lot every time (22nd St, Terrace Ave, 0.10-0.20 ac), and
        # admitting them put ~80 parcels through three network calls each for
        # land Matt would never buy. Known acreage required for those.
        if e["acres"]:
            if e["acres"] < MIN_ACRES:
                continue
        elif e["new_construction"]:
            continue

        # new-construction rows have no usable price, so the ceiling cannot
        # judge them. They come through and Matt looks the lot price up.
        if MAX_PRICE and e["price"] and e["price"] > MAX_PRICE:
            continue

        # ---- only now is it worth geocoding ----------------------------
        if e["lat"] is None or e["lon"] is None:
            e["lat"], e["lon"] = geocode(e["addr"], e["city"], _state_of(e),
                                         e.get("zip"))
            e["geocoded"] = e["lat"] is not None
            if e["lat"] is None:
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
    if h.get("new_construction"):
        price = "LOT PRICE UNKNOWN (listed as new construction)"
    ac    = f"{h['acres']:.1f}ac" if h.get("acres") else "acreage n/a"
    return f"{h['addr']}, {h['city']} - {price}, {ac}\n  {h['href']}"


def run(days_on, label):
    price = f"<= ${MAX_PRICE:,}" if MAX_PRICE else "no ceiling"
    log(f"{label}: {', '.join(CITIES)} | >= {MIN_ACRES:g} ac | {price} | "
        f"days_on={days_on or 'all'}")
    all_hits = []
    for city in CITIES:
        raw = fetch_city(city, days_on)
        h = qualify(raw)
        log(f"  {city:24} {len(raw):4d} listings -> {len(h)} pass the gate")
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
        CITIES = list(CITIES_VA) + list(CITIES_NC)
    if a.baseline: sys.exit(baseline())
    if a.install:  sys.exit(install())
    if a.now:      sys.exit(check(days_on=DAYS_ON_NOW))
    sys.exit(check(days_on=DAYS_ON_CHECK))
