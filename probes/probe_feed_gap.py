#!/usr/bin/env python3
"""
Are the Blackwater Road lots in the API feed at all?

Seven sequential Blackwater Rd listings show in OneHome (which reads the MLS
directly) and none of them come back through RapidAPI (which carries
realtor.com data, fed by brokerage syndication). Two very different causes:

  A. They ARE in realtor.com and our query is dropping them -- wrong property
     type, pagination cutting off, location string too narrow. Fixable in code.

  B. They are NOT in realtor.com. The listing broker did not syndicate, which
     is ordinary for a subdivision being drip-marketed. No amount of querying
     helps, and the answer is an MLS-sourced feed instead.

This distinguishes the two. Three passes, widening each time:

  1. Every Virginia Beach listing, no type filter, no price filter, paged to
     the end. Then grep the addresses for "Blackwater".
  2. The same for the 23457 postal code, in case the city string is the issue.
  3. Search by street address directly, if the API supports it.

Roughly 30-40 API calls. Against 6,000/month that is affordable, and it is a
question worth answering once rather than guessing at repeatedly.

    mv -f ~/Downloads/probe_feed_gap.py . && chmod +x probe_feed_gap.py && ./probe_feed_gap.py
"""

import collections
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, ".")
try:
    import land_watch as lw
except ImportError:
    sys.exit("run this from the repo root, next to land_watch.py")

# The seven from OneHome that never appear in our results.
MISSING = ["6584", "6592", "6600", "6608", "6628", "6636", "6664"]
STREET = "blackwater"

# Ones we DO get, as a control. If these vanish too, the probe is wrong.
CONTROL = ["camden ct", "head river"]


def page(params):
    url = lw.URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "x-rapidapi-key": lw.RAPIDAPI_KEY, "x-rapidapi-host": lw.HOST,
        "Content-Type": "application/json"})
    for n in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and n < 2:
                time.sleep(5 * (n + 1))
                continue
            return {"_err": f"HTTP {e.code}"}
        except Exception as e:
            if n < 2:
                time.sleep(3)
                continue
            return {"_err": type(e).__name__}
    return {"_err": "gave up"}


def sweep(label, base_params, max_pages=25):
    """Page to the end with NO type or price filtering. Return every address."""
    print(f"\n--- {label}")
    seen, offset, total, calls = [], 0, None, 0
    types = collections.Counter()
    for _ in range(max_pages):
        p = dict(base_params, offset=offset, limit=50)
        data = page(p)
        calls += 1
        if "_err" in data:
            print(f"    {data['_err']} at offset {offset}")
            break
        batch = data.get("listings") or []
        if total is None:
            total = data.get("totalResultCount", len(batch))
            print(f"    totalResultCount = {total}")
        for L in batch:
            e = lw.extract(L)
            types[e["type"] or "?"] += 1
            seen.append(((e["addr"] or "").lower(), e["type"], e["acres"], e["price"]))
        offset += 50
        if not batch or offset >= (total or 0):
            break
        time.sleep(0.8)

    print(f"    pulled {len(seen)} listings in {calls} call(s)")
    print(f"    property types: {dict(types.most_common(8))}")

    hits = [s for s in seen if STREET in s[0]]
    print(f"    addresses containing '{STREET}': {len(hits)}")
    for a, t, ac, pr in hits[:12]:
        num = a.split()[0] if a else "?"
        flag = "  <-- ONE OF THE MISSING" if num in MISSING else ""
        print(f"        {a[:40]:40} type={t!r:18} {ac:6.2f}ac "
              f"${pr or 0:,}{flag}")

    for c in CONTROL:
        n = sum(1 for s in seen if c in s[0])
        print(f"    control '{c}': {n} found")
    return seen


def main():
    if not lw.RAPIDAPI_KEY:
        sys.exit("no RAPIDAPI_KEY in .env")

    print("Looking for 7 Blackwater Rd lots that OneHome shows and we never see.")
    print("NOTE: no type filter, no price filter, no acreage filter in this probe.")

    sweep("PASS 1: all Virginia Beach listings",
          {"location": "Virginia Beach, VA", "sort": "newest"})

    sweep("PASS 2: postal code 23457 only",
          {"location": "23457", "sort": "newest"})

    print("\n--- PASS 3: direct address search")
    for num in MISSING[:3]:
        addr = f"{num} Blackwater Rd, Virginia Beach, VA 23457"
        data = page({"location": addr, "limit": 5})
        if "_err" in data:
            print(f"    {addr[:45]:45} {data['_err']}")
            continue
        n = data.get("totalResultCount", 0)
        first = ""
        if data.get("listings"):
            e = lw.extract(data["listings"][0])
            first = f"  first hit: {e['addr']}, {e['city']}"
        print(f"    {addr[:45]:45} total={n}{first}")
        time.sleep(0.8)

    print("\n" + "=" * 72)
    print("If Blackwater appears in pass 1 or 2 -> our filtering is the problem,")
    print("   and it is fixable in code.")
    print("If it appears NOWHERE -> realtor.com does not carry these listings.")
    print("   No query fixes that; the answer is an MLS-sourced feed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
