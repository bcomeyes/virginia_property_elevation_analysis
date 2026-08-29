#!/usr/bin/env python3
"""
Where does 6584 Blackwater Rd disappear?

It is in the feed: typed 'land', $235,000, 4.35 acres, Virginia Beach 23457.
That clears every gate we have -- land type, above the 1 acre floor, under the
$400k ceiling. It should be in the table. It is not.

So something between fetch_city() and the printed row is eating it. Prime
suspects are the two dedup stages, because the Blackwater lots are adjacent
parcels carved out of one tract:

  - geocode dedup collapses listings sharing a pin to ~1 m
  - parcel-ID merge collapses listings VGIN says are the same parcel

29 listings collapsed on geocode and 15 on parcel ID in the last run. If
adjacent lots from one subdivision share a pin, or if VGIN has not yet split
the parent parcel, real properties are being merged away -- exactly the
failure Matt warned about when we discussed dedup.

This walks one listing through each stage and reports where it dies.

    mv -f ~/Downloads/trace_listing.py . && chmod +x trace_listing.py && ./trace_listing.py
"""

import sys

sys.path.insert(0, ".")
import land_watch as lw          # noqa: E402
import parcel_elevation as pe    # noqa: E402
import search_config as cfg      # noqa: E402

NEEDLE = "blackwater"


def main():
    print(f"tracing '{NEEDLE}' through the pipeline\n")

    # ---- stage 1: what the API returns -------------------------------------
    raw = []
    for city in cfg.JURISDICTIONS:
        raw.extend(lw.fetch_city(city, days_on=0))
    print(f"STAGE 1  fetch_city  -> {len(raw)} raw listings")

    hits = []
    for L in raw:
        loc = (L.get("location") or {}).get("address") or {}
        if NEEDLE in (loc.get("line") or "").lower():
            hits.append(L)
    print(f"         containing '{NEEDLE}': {len(hits)}")
    for L in hits:
        e = lw.extract(L)
        print(f"           {e['addr'][:34]:34} type={e['type']:15} "
              f"listed={e['listed']:5} {e['acres']:6.2f}ac "
              f"price={e['price']} pid={e['pid']} "
              f"({e['lat']}, {e['lon']})")
    if not hits:
        print("\n  => never arrives. The city sweep does not return it.")
        print("     (the 23457 zip probe DID find it, so compare the two)")
        return 0

    # ---- stage 2: the gate --------------------------------------------------
    passed = lw.qualify(raw)
    kept = [e for e in passed if NEEDLE in (e["addr"] or "").lower()]
    print(f"\nSTAGE 2  qualify()   -> {len(passed)} pass the gate, "
          f"{len(kept)} of them '{NEEDLE}'")
    for e in kept:
        print(f"           {e['addr'][:34]:34} {e['acres']:6.2f}ac price={e['price']}")
    if not kept:
        print("  => killed by the gate. Check type / acreage floor / price ceiling.")
        return 0

    # ---- stage 3: geocode dedup --------------------------------------------
    before = len(passed)
    deduped = pe._dedupe_listings(passed)
    kept2 = [e for e in deduped if NEEDLE in (e["addr"] or "").lower()]
    print(f"\nSTAGE 3  geocode dedup -> {before} to {len(deduped)}, "
          f"{len(kept2)} of them '{NEEDLE}'")
    lost = {e["addr"] for e in kept} - {e["addr"] for e in kept2}
    if lost:
        print(f"  => LOST HERE: {sorted(lost)}")
        print("     adjacent lots sharing one geocode. The pins are identical")
        print("     to 5 decimal places, so these listings are indistinguishable")
        print("     by location alone.")

    # ---- stage 4: parcel lookup + parcel-ID merge ---------------------------
    print(f"\nSTAGE 4  parcel lookup for each surviving '{NEEDLE}' listing")
    keys = {}
    for e in kept2:
        r = pe.check(e["lat"], e["lon"], flood=False)
        if r.get("error"):
            print(f"           {e['addr'][:30]:30} ERROR {r['error']}")
            continue
        k = pe._parcel_key(r)
        print(f"           {e['addr'][:30]:30} parcel_key={k!r} "
              f"poly={r.get('poly_acres')}ac listed={e['acres']:.2f}ac")
        keys.setdefault(k, []).append(e["addr"])

    dupes = {k: v for k, v in keys.items() if len(v) > 1}
    if dupes:
        print("\n  => MERGED HERE. VGIN returns ONE parcel id for several")
        print("     listings, so the merge folds them into a single row:")
        for k, v in dupes.items():
            print(f"       {k}: {v}")
        print("     Either the pins all land inside one parent parcel that has")
        print("     not been split in VGIN yet, or the geocoder puts them all")
        print("     on the same spot.")
    else:
        print("\n  => survives all four stages. Look at --limit truncation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
