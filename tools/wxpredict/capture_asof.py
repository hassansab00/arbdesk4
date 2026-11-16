"""Bounded weather receipt reads; immutable source rows remain in live_capture."""
import datetime as dt


def read(ops, cities, city_keys, first, times, *, current_receipts=True, until=None):
    """Union of the latest value of each kind/key at each decision instant.

    Match Record's stable timestamp sort, including capture_id tie breaking.
    The serving path uses the later captured/written receipt for current NWP;
    legacy shadow replays retain their existing captured-time interpretation.
    """
    from wxpredict import common
    prefixes = {city + "|" for city in city_keys}
    prefixes.update(common.iem_id(cities[city]["icao"]) + "|"
                    for city in city_keys if cities[city].get("icao"))
    times = sorted({int(t.timestamp()) for t in times})
    if not prefixes or not times:
        return []
    stamp = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
    params = dict(p_first=first.isoformat(), p_until=(until.isoformat() if until else stamp(max(times))), p_times=[stamp(t) for t in times],
                  p_prefixes=sorted(prefixes), p_current_receipts=current_receipts,
                  p_limit=1000)
    rows, after = [], 0
    while True:
        page = ops.rpc("live_capture_asof", dict(params, p_after_id=after))
        if not isinstance(page, dict) or not isinstance(page.get("rows"), list):
            raise ValueError("invalid live_capture_asof page")
        batch = page["rows"]
        ids = [int(r["capture_id"]) for r in batch]
        if ids != sorted(set(ids)) or any(i <= after for i in ids):
            raise ValueError("live_capture_asof paging did not advance")
        rows.extend(batch)
        if page.get("has_more") is False:
            # Record preserves first-seen key order when overlapping snapshots merge.
            return sorted(rows, key=lambda r: (int(r['first_capture_id']), int(r['capture_id'])))
        if page.get("has_more") is not True or not ids or page.get("next_after_id") != ids[-1]:
            raise ValueError("invalid live_capture_asof cursor")
        after = ids[-1]
