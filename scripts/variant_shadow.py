"""Engine variants in shadow, observe only: `da_floor` (P1.1's candidate) and
`sd_corr` (the engine's station-corrected path on the day itself).

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

THE SECOND CANDIDATE, sd_corr:v1 (docs/SD_CORR_PREREG.md; the design replay is
tools/sd_corrected_replay.py), beside each of the same rows:
  - the derived_corrected_forecast row of the city and target date as the
    tick reads it now: computed no later than now and younger than
    settings.station_correction_pricing.max_age_hours (36 when absent), the
    age the engine uses. Its combined_c is the centre (P3.9) and its width_c
    the width (P3.9 part 3); a row without a width is no candidate. It is
    read whatever the pricing switches say: the test is of the path, not of
    the switch;
  - the same floor, q, integrator, clamp and rounding as da_floor.
Nothing is fitted here: the corrections and the width are the nightly fit's,
under Rule 11, and every row records their versions.

BOUNDED AND NEVER RAISES: it runs inside the tick's minute, after the
engine's decisions (it observes; they act). Each day-ahead lookup is one
indexed read (36 ms in the database for an 11-bucket market, 4 Oct), one
attempt with a timeout no longer than the time left; they run on a small pool
and stop at the deadline. sd_corr's two reads (the corrected rows, their max
age) are one attempt each, and are not started with under 2 s left. Each
variant's write is one request the same way, da_floor's first, so a refused
sd_corr row never costs da_floor its rows; none is started with under a second
left (common.upsert waits up to 120 s and retries four times; Codex on #303).
A row it cannot build or write is counted by reason in the tick's log.
"""
import datetime as dt
import json
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
        "station": served.get("station"),
        "day_ahead_centre_c": float(centre), "day_ahead_sigma_c": float(sigma),
        "day_ahead_priced_at": call["computed_at"],
        "day_ahead_lead_days": call.get("lead_days"),
        "floor_c": None if floor is None else float(floor),
        "q_down": None if floor is None else q_down,
        "q_up": None if floor is None else q_up,
        "served_centre_c": served.get("centre_c"), "served_sigma_c": served.get("sigma_c"),
        "served_calibrated": bool(calibrated),
        "unit": unit, "probs": probs, "top_band_id": top[0], "top_prob": top[1],
        **{k: None for k in CORRECTED_INPUTS},
    }, None


# ---------------------------------------------------------------------------
# sd_corr:v1 - the engine's station-corrected path on the day itself
# ---------------------------------------------------------------------------
SD_VARIANT = "sd_corr"
SD_VERSION = "sd_corr:v1"
SD_MAX_AGE_H = 36.0     # settings.station_correction_pricing.max_age_hours when absent, as the engine
CORRECTED_SELECT = "city_key,for_date,lead_days,combined_c,width_c,version,width_version,computed_at,n_sources"
# Each variant names the other's inputs as null, as the table's check holds it
# to (20261005090000).
DAY_AHEAD_INPUTS = ("day_ahead_centre_c", "day_ahead_sigma_c", "day_ahead_priced_at", "day_ahead_lead_days")
CORRECTED_INPUTS = ("corrected_centre_c", "corrected_width_c", "corrected_version", "corrected_width_version",
                    "corrected_computed_at", "corrected_lead_days", "corrected_n_sources")


def _ts(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def corrected_live(row, now, max_age_h=SD_MAX_AGE_H):
    """(row, None) when `row` is the corrected forecast the tick may read at
    `now`, else (None, why): computed no later than now, younger than the
    engine's max age, with a centre and a width. The design replay's rule
    (tools/sd_corrected_replay.live_row) on the one row the table holds."""
    if row is None or row.get("combined_c") is None or row.get("computed_at") is None:
        return None, "no_live_corrected_row"
    at = _ts(row["computed_at"])
    if at > now:
        return None, "no_live_corrected_row"
    if (now - at).total_seconds() / 3600 > float(max_age_h):
        return None, "corrected_row_older_than_max_age"
    if row.get("width_c") is None or float(row["width_c"]) <= 0 or not row.get("width_version"):
        return None, "live_row_without_width"
    return row, None


def build_sd_row(served, live, bands, unit, q, calibrated):
    """One sd_corr:v1 row, or (None, why): the live corrected centre and width,
    the served floor and the engine's q."""
    if not bands:
        return None, "no_bands"
    floor = served.get("running_max_c")
    q_down, q_up = q
    probs = ladder(live["combined_c"], live["width_c"], unit, bands, floor, q_down, q_up)
    if abs(sum(probs.values()) - 1) > SUM_TOLERANCE:
        return None, "ladder_does_not_sum_to_one"
    top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    row = {
        "city_key": served["city_key"], "target_date": str(served["target_date"]),
        "checkpoint": served["checkpoint"], "variant": SD_VARIANT, "variant_version": SD_VERSION,
        "engine_version": served["engine_version"],
        "station": served.get("station"),
        "corrected_centre_c": float(live["combined_c"]), "corrected_width_c": float(live["width_c"]),
        "corrected_version": live.get("version"), "corrected_width_version": live.get("width_version"),
        "corrected_computed_at": live["computed_at"], "corrected_lead_days": live.get("lead_days"),
        "corrected_n_sources": live.get("n_sources"),
        "floor_c": None if floor is None else float(floor),
        "q_down": None if floor is None else q_down,
        "q_up": None if floor is None else q_up,
        "served_centre_c": served.get("centre_c"), "served_sigma_c": served.get("sigma_c"),
        "served_calibrated": bool(calibrated),
        "unit": unit, "probs": probs, "top_band_id": top[0], "top_prob": top[1],
    }
    row.update({k: None for k in DAY_AHEAD_INPUTS})
    return row, None


# Skips that lose a row the test should have (a failure, the clock), as
# against the exclusions the pre-registration counts (no day-ahead call, ...).
LOST = ("error", "out_of_time")


def lost(counts):
    """Why this run lost capture rows of either variant, or None: the tick's
    status reads it."""
    counts = counts or {}
    if counts.get("error"):
        return counts["error"]
    sd = counts.get("sd_corr") or {}
    if sd.get("error"):
        return f"{SD_VERSION}: {sd['error']}"
    n = {k: v for k, v in (counts.get("skipped") or {}).items() if k in LOST and v}
    n.update({f"{SD_VARIANT}:{k}": v for k, v in (sd.get("skipped") or {}).items() if k in LOST and v})
    return f"rows not captured: {n}" if n else None


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def write(rows, timeout_s):
    """One insert of every row, duplicates ignored (first capture wins), one
    attempt, never longer than timeout_s."""
    import common
    headers = common._headers()
    headers["Prefer"] = "resolution=ignore-duplicates,return=minimal"
    r = common._post(f"{common._cfg()['url']}/rest/v1/{TABLE}", headers=headers,
                     params={"on_conflict": ON_CONFLICT}, data=json.dumps(rows),
                     timeout=max(1.0, timeout_s))
    r.raise_for_status()
    return len(rows)


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


def corrected_rows(keys, timeout_s=8.0):
    """{(city, for_date): derived_corrected_forecast row} for these city-days:
    one read, one attempt, never longer than timeout_s (the table holds one row
    per city and date, the newest computation)."""
    import common
    cities = sorted({c for c, _d in keys})
    dates = sorted({d for _c, d in keys})
    r = common._get(f"{common._cfg()['url']}/rest/v1/derived_corrected_forecast",
                    headers=common._headers(), timeout=max(1.0, timeout_s), params={
                        "select": CORRECTED_SELECT,
                        "city_key": f"in.({','.join(cities)})",
                        "for_date": f"in.({','.join(dates)})"})
    r.raise_for_status()
    return {(x["city_key"], str(x["for_date"])): x for x in r.json()}


def corrected_max_age(timeout_s=8.0):
    """settings.station_correction_pricing.max_age_hours, 36 when absent: one
    read, one attempt. A read that fails raises (the rows are lost, counted),
    never a guess."""
    import common
    r = common._get(f"{common._cfg()['url']}/rest/v1/settings", headers=common._headers(),
                    timeout=max(1.0, timeout_s),
                    params={"select": "value", "key": "eq.station_correction_pricing"})
    r.raise_for_status()
    rows = r.json()
    value = (rows[0].get("value") if rows else None) or {}
    try:
        return float(value.get("max_age_hours", SD_MAX_AGE_H))
    except (TypeError, ValueError, AttributeError):
        return SD_MAX_AGE_H


def record(out, results, market_of, bands_by_market, tz_of, unit_of, dry_run=False,
           deadline=None):
    """Write the da_floor and sd_corr rows for each same-day checkpoint row in
    `out` (the rows the tick is writing). results: {(city, target): (rows, reg, reasons)}
    from process_city_day. Returns the counts for the tick's log."""
    t0 = time.monotonic()
    counts = {"version": VERSION, "due": 0, "written": 0, "skipped": {}}
    sd = {"version": SD_VERSION, "due": 0, "written": 0, "skipped": {}}
    counts["sd_corr"] = sd

    def skip(why, n=1):
        counts["skipped"][why] = counts["skipped"].get(why, 0) + n

    def sd_skip(why, n=1):
        sd["skipped"][why] = sd["skipped"].get(why, 0) + n

    try:
        import probability_engine as pe
        todo = [r for r in out if r.get("checkpoint") in CHECKPOINTS]
        counts["due"] = sd["due"] = len(todo)
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
        def inputs(r, key):
            """(unit, q, calibrated) the engine used for this call, for both variants."""
            fitted = pe._measurement_layer_for(r["city_key"])
            q = fitted if fitted is not None else (pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP)
            reasons = (results.get(key) or (None, None, []))[2] or []
            calibrated = any(str(x).startswith("calibrated:") for x in reasons)
            unit = (market_of.get(key) or {}).get("unit") or unit_of.get(r["city_key"], "C")
            return unit, q, calibrated

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
            row, why = build_row(r, call, bands, *inputs(r, key))
            if row is None:
                skip(why)
                continue
            rows.append(row)

        # sd_corr:v1 - two reads (the corrected rows and their max age), each one
        # attempt inside the time left; a failed read loses every row, counted.
        sd_rows = []
        left = (deadline - time.monotonic()) if deadline else 10.0
        if left < 2.0:
            sd_skip("out_of_time", len(todo))
        else:
            try:
                live_by = corrected_rows(set(days), min(8.0, left - 1.0)) if days else {}
                left = (deadline - time.monotonic()) if deadline else 10.0
                max_age = corrected_max_age(min(8.0, max(1.0, left - 1.0)))
            except Exception as e:
                sd["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            else:
                now = dt.datetime.now(dt.timezone.utc)
                for r in todo:
                    key = (r["city_key"], str(r["target_date"]))
                    if key not in days:
                        sd_skip("no_market_or_timezone")
                        continue
                    bands, _ = days[key]
                    if not bands:
                        sd_skip("no_bands")
                        continue
                    live, why = corrected_live(live_by.get(key), now, max_age)
                    if live is None:
                        sd_skip(why)
                        continue
                    row, why = build_sd_row(r, live, bands, *inputs(r, key))
                    if row is None:
                        sd_skip(why)
                        continue
                    sd_rows.append(row)

        # One insert per variant, da_floor's first: a refused sd_corr row can
        # never cost da_floor's test its rows. Each starts only with a second left.
        if dry_run:
            if rows or sd_rows:
                counts["would_write"] = len(rows)
                sd["would_write"] = len(sd_rows)
        elif rows:
            left = (deadline - time.monotonic()) if deadline else 10.0
            if left < 1.0:
                skip("out_of_time", len(rows))
            else:
                try:
                    counts["written"] = write(rows, min(8.0, left))
                except Exception as e:
                    counts["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        if sd_rows and not dry_run:
            left = (deadline - time.monotonic()) if deadline else 10.0
            if left < 1.0:
                sd_skip("out_of_time", len(sd_rows))
            else:
                try:
                    sd["written"] = write(sd_rows, min(8.0, left))
                except Exception as e:
                    sd["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    except Exception as e:
        counts["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    counts["seconds"] = round(time.monotonic() - t0, 2)
    return counts
