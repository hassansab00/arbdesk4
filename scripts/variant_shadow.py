"""Engine variants in shadow, observe only: `da_floor` (P1.1's candidate).

WHY. P1.1 (docs/P11_INTRADAY_DIAGNOSIS_2026-10-04.md) found that switching at
local midnight to the same-day centre and its narrower width cost the engine
0.110 - 0.297 in log loss before the peak, against keeping the day-ahead call's
centre and width with the observed floor. Seven dates chose that candidate, so
they cannot judge it. docs/P11_DA_FLOOR_PREREG.md fixes the forward test; this
records the candidate beside every same-day call the tick writes, for that test
to grade. NOTHING HERE PRICES OR TRADES.

THE CANDIDATE, da_floor:v1, for each same-day checkpoint row the tick wrote
this run (first captures only: the tick writes a checkpoint no version holds):
  - the day-ahead call: centre_c and sigma_c of the last band_probabilities row
    of the market's buckets computed before the city's local midnight that
    begins the target day (newest computed_at, then prob_id) - the call
    v_city_hit_history grades;
  - the floor the served ladder used (its running_max_c) and the q the engine
    read for the city in this run (cities.observation_q_down/_up, else the
    pooled default);
  - probability_engine.compute_band_probabilities over the market's buckets,
    each bucket clamped and rounded as the served ladder is, no calibration.
One row per city, day, checkpoint and variant, append-only.

BOUNDED AND NEVER RAISES: it runs inside the tick's minute. Each day-ahead
lookup is one indexed read (36 ms in the database for an 11-bucket market,
4 Oct), one attempt with a timeout no longer than the time left; they run on a
small pool and stop at the deadline. A row it cannot build is counted by
reason in the tick's log.
"""
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, wait
from zoneinfo import ZoneInfo

VARIANT = "da_floor"
VERSION = "da_floor:v1"
TABLE = "variant_shadow_checkpoints"
ON_CONFLICT = "city_key,target_date,checkpoint,variant"
CHECKPOINTS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
WORKERS = 4
SUM_TOLERANCE = 1e-4


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
def day_starts_at(target_date, timezone):
    """The city's own midnight at the start of target_date, in UTC."""
    day = dt.date.fromisoformat(str(target_date))
    return dt.datetime.combine(day, dt.time(0), ZoneInfo(timezone)).astimezone(dt.timezone.utc)


def ladder(centre_c, sigma_c, unit, bands, floor_c, q_down, q_up):
    """{band_id: p}: the engine's integrator, clamped and rounded as served."""
    import probability_engine as pe
    out = pe.compute_band_probabilities(float(centre_c), float(sigma_c), unit, bands,
                                        floor_c=None if floor_c is None else float(floor_c),
                                        q_down=q_down, q_up=q_up)
    return {str(b): round(pe.clamp_prob(p), 6) for b, p in out}


def build_row(served, call, bands, unit, q, calibrated):
    """One variant_shadow_checkpoints row, or (None, why).

    served: the prediction_checkpoints row the tick wrote; call: the day-ahead
    band_probabilities row; q: (q_down, q_up) the engine read for the city."""
    if not bands:
        return None, "no_bands"
    centre, sigma = call.get("centre_c"), call.get("sigma_c")
    if centre is None or sigma is None or float(sigma) <= 0:
        return None, "day_ahead_call_without_centre"
    floor = served.get("running_max_c")
    q_down, q_up = q
    probs = ladder(centre, sigma, unit, bands, floor, q_down, q_up)
    if abs(sum(probs.values()) - 1) > SUM_TOLERANCE:
        return None, "ladder_does_not_sum_to_one"
    top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return {
        "city_key": served["city_key"], "target_date": str(served["target_date"]),
        "checkpoint": served["checkpoint"], "variant": VARIANT, "variant_version": VERSION,
        "engine_version": served["engine_version"],
        "day_ahead_centre_c": float(centre), "day_ahead_sigma_c": float(sigma),
        "day_ahead_priced_at": call["computed_at"],
        "day_ahead_lead_days": call.get("lead_days"),
        "floor_c": None if floor is None else float(floor),
        "q_down": None if floor is None else q_down,
        "q_up": None if floor is None else q_up,
        "served_centre_c": served.get("centre_c"), "served_sigma_c": served.get("sigma_c"),
        "served_calibrated": bool(calibrated),
        "unit": unit, "probs": probs, "top_band_id": top[0], "top_prob": top[1],
    }, None


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def day_ahead_call(band_ids, starts_at, timeout_s=8.0):
    """The last pricing of these buckets before starts_at, or None.

    ONE attempt, with a timeout no longer than the time left: common.rest()
    waits up to 90 s and retries four times, and a lookup thread still running
    at the end would hold the tick's process open past its billed minute. A
    lookup that fails is counted and the row is skipped."""
    import common
    r = common._get(f"{common._cfg()['url']}/rest/v1/band_probabilities",
                    headers=common._headers(), timeout=max(1.0, timeout_s), params={
                        "select": "band_id,centre_c,sigma_c,computed_at,lead_days",
                        "band_id": f"in.({','.join(str(b) for b in band_ids)})",
                        "computed_at": f"lt.{starts_at.isoformat()}",
                        "order": "computed_at.desc,prob_id.desc",
                        "limit": "1"})
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def record(out, results, market_of, bands_by_market, tz_of, unit_of, dry_run=False,
           deadline=None):
    """Write the da_floor row for each same-day checkpoint row in `out` (the
    rows the tick is writing). results: {(city, target): (rows, reg, reasons)}
    from process_city_day. Returns the counts for the tick's log."""
    t0 = time.monotonic()
    counts = {"version": VERSION, "due": 0, "written": 0, "skipped": {}}

    def skip(why):
        counts["skipped"][why] = counts["skipped"].get(why, 0) + 1

    try:
        from common import upsert
        import probability_engine as pe
        todo = [r for r in out if r.get("checkpoint") in CHECKPOINTS]
        counts["due"] = len(todo)
        if not todo:
            return counts
        days = {}
        for r in todo:
            key = (r["city_key"], str(r["target_date"]))
            m = market_of.get(key)
            tz = tz_of.get(r["city_key"])
            if not m or not tz:
                continue
            bands = bands_by_market.get(m["market_id"]) or []
            days[key] = (bands, day_starts_at(key[1], tz))
        calls = {}
        left = (deadline - time.monotonic()) if deadline else 10.0
        if left <= 0:
            calls = {key: "out_of_time" for key in days}
        elif days:
            pool = ThreadPoolExecutor(max_workers=WORKERS)
            futures = {pool.submit(day_ahead_call, [b["band_id"] for b in bands], starts,
                                   min(8.0, left)): key
                       for key, (bands, starts) in days.items() if bands}
            done, not_done = wait(futures, timeout=max(0.0, left))
            for f in not_done:
                f.cancel()
                calls[futures[f]] = "out_of_time"
            for f in done:
                try:
                    calls[futures[f]] = f.result()
                except Exception as e:
                    calls[futures[f]] = f"error:{type(e).__name__}"
            pool.shutdown(wait=False, cancel_futures=True)
        rows = []
        for r in todo:
            key = (r["city_key"], str(r["target_date"]))
            if key not in days:
                skip("no_market_or_timezone")
                continue
            bands, _ = days[key]
            call = calls.get(key) if bands else None
            if not bands:
                skip("no_bands")
                continue
            if isinstance(call, str):
                skip(call.split(":")[0] if call.startswith("error") else call)
                continue
            if call is None:
                skip("no_day_ahead_call")
                continue
            fitted = pe._measurement_layer_for(r["city_key"])
            q = fitted if fitted is not None else (pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP)
            reasons = (results.get(key) or (None, None, []))[2] or []
            calibrated = any(str(x).startswith("calibrated:") for x in reasons)
            unit = (market_of.get(key) or {}).get("unit") or unit_of.get(r["city_key"], "C")
            row, why = build_row(r, call, bands, unit, q, calibrated)
            if row is None:
                skip(why)
                continue
            rows.append(row)
        if rows and not dry_run:
            counts["written"] = upsert(TABLE, rows, ON_CONFLICT)
        elif rows:
            counts["would_write"] = len(rows)
    except Exception as e:
        counts["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    counts["seconds"] = round(time.monotonic() - t0, 2)
    return counts
