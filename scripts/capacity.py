"""
AD4 capacity + correlation (Task 7), daily.

The actual computation is genuine SQL (sql/ad4_capacity_correlation.sql):
capacity is a GROUP BY over book_snapshots depth, correlation of forecast
errors uses Postgres's built-in corr() aggregate. Both belong in the
database for the same reason edge computation partly does (see
docs/architecture_deviations.md) - they're joins/aggregations over data
already there, not logic that needs Python. This script is a thin
scheduling/logging wrapper so the job shows up in `ingest_log` next to
every other daily job, consistent with how the rest of AD4 is run.
"""
import sys
from common import log_run, refresh_feature_cache, rest, rpc as _call_rpc


def _absent(exc):
    """Is this 'the database has not run that .sql file' rather than a failure?

    PostgREST answers an unknown function with 404 / PGRST202. Everything else
    - a timeout, a permission denial, a bug in the function - is the job's
    problem and must not be filed under "optional".
    """
    resp = getattr(exc, "response", None)
    if getattr(resp, "status_code", None) != 404:
        return False
    body = (getattr(resp, "text", "") or "")
    return "PGRST202" in body or "Could not find the function" in body


def optional_rpc(fn, hint, failures):
    """Call an RPC that a database may legitimately not have yet.

    A MISSING function is a note: a desk that has not run the .sql file simply
    does without. A function that EXISTS AND FAILED is recorded in `failures`,
    which fails the job.

    This is the same rule the refresh_feature_cache block below already
    follows, and it is here for the same reason it is there. Both of these
    used to swallow every failure into one line on stderr and let the job
    report green - so when refresh_weather_peak() started failing 42P10 on
    2026-09-06 (sql/ad4_56 has the story), the daily pipeline passed for a
    week while the table it was meant to write went untouched, and
    live_weather.minutes_to_peak, v_city_stats, v_trade_timing and strategy s7
    all read a stale copy without anything anywhere saying so.
    """
    try:
        return _call_rpc(fn)
    except Exception as e:
        if _absent(e):
            print(f"  note: {fn} unavailable - {hint}", file=sys.stderr)
            return None
        failures.append(f"{fn}: {e}")
        print(f"{fn} FAILED: {e}", file=sys.stderr)
        return None


def refresh_peaks(failures):
    """derived_weather_peak, ONE CITY AT A TIME.

    Same reason as common.refresh_feature_cache, which says it at length:
    statement_timeout runs from the start of the TOP-LEVEL statement and is
    never reset by what a function does internally, so a slice has to be its
    own call. Measured on the live archive, the whole-archive version sorts
    519k rows and takes between 5.0s and 17.4s for the same 609 rows -
    recompute_correlation was cancelled at about 15s, so that is a coin flip.
    Per city the slowest call is 229ms, and the output is identical: all 636
    rows compared, none changed.

    Falls back to the whole-archive function on a database that has not run
    sql/ad4_56 yet, so this script works either way.
    """
    try:
        cities = [c["city_key"] for c in
                  rest("cities", [("select", "city_key"), ("order", "city_key")])]
    except Exception as e:
        failures.append(f"cities: {e}")
        print(f"cities FAILED: {e}", file=sys.stderr)
        return None
    if not cities:
        return 0

    try:
        total = int(_call_rpc("refresh_weather_peak_city",
                              {"p_city": cities[0]}) or 0)
    except Exception as e:
        if _absent(e):
            return optional_rpc("refresh_weather_peak",
                                "run sql/ad4_56 for the per-city version", failures)
        failures.append(f"refresh_weather_peak_city[{cities[0]}]: {e}")
        print(f"refresh_weather_peak_city FAILED on {cities[0]}: {e}", file=sys.stderr)
        return None

    for city in cities[1:]:
        try:
            total += int(_call_rpc("refresh_weather_peak_city", {"p_city": city}) or 0)
        except Exception as e:
            # One city failing is still a failure. It is not a reason to
            # abandon the other fifty-three, which are independent.
            failures.append(f"refresh_weather_peak_city[{city}]: {e}")
            print(f"refresh_weather_peak_city FAILED on {city}: {e}", file=sys.stderr)
    return total


def recompute_capacity_per_city(failures):
    """derived_capacity, ONE CITY AT A TIME.

    Third time on this rake, same reasoning as refresh_peaks() above and
    common.refresh_feature_cache: statement_timeout runs from the start of the
    TOP-LEVEL statement, so the only way to buy budget is to make each unit of
    work its own statement.

    The whole-table recompute_capacity() calls capacity_side() eight times per
    row across every band's newest book snapshot. Measured on the live
    database, TWO of those eight calls over 9,012 rows already cost 8.1s of an
    8.2s query - the time is inside a per-row function, so no index touches
    it. The full eight lands near 30s against a ~15s timeout, which is how the
    Daily Pipeline died on 2026-09-14:

        recompute_capacity -> HTTP 500 {"code":"57014",
          "message":"canceling statement due to statement timeout"}

    Nothing in the aggregate crosses a city boundary - it groups by
    (city_key, hour_utc) - so per-city calls produce identical rows. Falls
    back to the whole-table function on a database that has not run
    sql/ad4_64 yet, so this script works either way.
    """
    try:
        cities = [c["city_key"] for c in
                  rest("cities", [("select", "city_key"), ("order", "city_key")])]
    except Exception as e:
        failures.append(f"cities: {e}")
        print(f"cities FAILED: {e}", file=sys.stderr)
        return None
    if not cities:
        return 0

    try:
        total = int(_call_rpc("recompute_capacity_city",
                              {"p_city": cities[0]}) or 0)
    except Exception as e:
        if _absent(e):
            return optional_rpc("recompute_capacity",
                                "run sql/ad4_64 for the per-city version", failures)
        failures.append(f"recompute_capacity_city[{cities[0]}]: {e}")
        print(f"recompute_capacity_city FAILED on {cities[0]}: {e}", file=sys.stderr)
        total = 0

    for city in cities[1:]:
        try:
            total += int(_call_rpc("recompute_capacity_city", {"p_city": city}) or 0)
        except Exception as e:
            failures.append(f"recompute_capacity_city[{city}]: {e}")
            print(f"recompute_capacity_city FAILED on {city}: {e}", file=sys.stderr)
    return total


def main():
    # common.rpc, not raise_for_status: the body is where Postgres puts the
    # reason. Six consecutive daily runs died here reporting only
    # "500 Server Error ... /rpc/recompute_correlation", while the Postgres
    # log recorded "canceling statement due to statement timeout" at the very
    # same second. One of those two messages says what to fix.
    failures = []
    capacity_rows = recompute_capacity_per_city(failures)
    correlation_rows = _call_rpc("recompute_correlation")
    print(f"derived_capacity: {capacity_rows} rows")
    print(f"derived_city_correlation: {correlation_rows} rows")

    # The seasonal normal is a full pass over weather_observations for a figure
    # that changes once a day. Computing it per page load is what made
    # v_city_stats time out in the browser; it belongs here, with the other
    # daily derivations. Optional: a database without ad4_19 simply has no such
    # function, and nothing else in this job depends on it.
    # THE PEAK HOUR NOTHING WAS COMPUTING.
    #
    # derived_weather_peak holds each city's measured peak hour and window
    # width per calendar month. live_weather.minutes_to_peak, v_city_stats,
    # v_trade_timing and strategy s7 all read it - and nothing in this repo
    # ever wrote it, so every one of them has been silently running on a
    # fallback since the schema was created. sql/ad4_37_peak_hour.sql adds the
    # function; this is where it belongs, next to the other daily passes over
    # the archive.
    #
    # Optional in the same way refresh_city_climate is: a database without
    # ad4_37 has no such function, and nothing else in this job depends on it.
    peaks = refresh_peaks(failures)
    if peaks is not None:
        print(f"derived_weather_peak: {peaks} city-month row(s)")

    climate = optional_rpc("refresh_city_climate",
                           "run sql/ad4_19_stats_cache.sql", failures)
    if climate is not None:
        print(f"derived_city_climate: {climate}")

    # CALIBRATION FEEDBACK - the loop sql/ad4_45 closes.
    #
    # mae_c already makes the centre right and sets a starting width. Nothing
    # measured whether the resulting distribution turned out HONEST: a model
    # can have excellent mae_c and still be systematically overconfident,
    # because the centre is right and the spread is too narrow. Every edge
    # computed from a too-narrow distribution is overstated, so the desk sizes
    # UP on exactly the trades it should size down.
    #
    # Here rather than in databank.py because it reads the frozen evidence
    # databank writes, and this job is what runs after it. Optional in the
    # same way as the two above: a desk that has not run ad4_45 prices exactly
    # as it did before, and says so once rather than failing the run.
    calibration = optional_rpc(
        "refresh_calibration_adjustment",
        "run sql/ad4_45_calibration_feedback.sql. Sigma stays at measured skill alone.",
        failures)
    if calibration is not None:
        print(f"calibration feedback: {calibration}")

    # Same reasoning, same place: v_city_day_features and the climb profile are
    # passes over the whole archive for figures that change once a day. Left in
    # the browser they cost 1-3 seconds each and Supabase cancels the statement.
    #
    # Per city, because statement_timeout is measured from the TOP-LEVEL
    # statement and never reset by what a function runs inside itself - so the
    # whole-archive call was one ~6.5 second statement that Supabase cancels,
    # reaching here as a bare HTTP 500. See common.refresh_feature_cache.
    #
    # AND IT IS NO LONGER A NOTE. This used to swallow every failure into a
    # one-line "unavailable" on stderr and report the job green - so when the
    # refresh started failing, this job kept passing and the cache silently
    # stopped being updated. The UI, the analytics views and the model all read
    # that cache. A missing ad4_28 is still just a note; anything else fails the
    # job, which is the only way anyone finds out.
    features, features_error = None, None
    try:
        features = refresh_feature_cache()
    except RuntimeError as e:                     # signature missing: ad4_28/29 not run
        print(f"  note: {e}", file=sys.stderr)
    except Exception as e:
        features_error = str(e)
        failures.append(f"refresh_feature_cache: {e}")
        print(f"refresh_feature_cache FAILED: {e}", file=sys.stderr)

    # `peaks` was computed here and never logged, so the one table that WAS
    # failing left no trace in ingest_log either. Every derivation this job
    # performs now appears in the row it writes.
    log_run("capacity", "attention" if failures else "ok",
            (capacity_rows or 0) + (correlation_rows or 0),
            {"capacity_rows": capacity_rows, "correlation_rows": correlation_rows,
             "peaks": peaks, "climate": climate, "calibration": calibration,
             "features": features, "features_error": features_error,
             "failures": failures or None})

    if failures:
        print(f"capacity: {len(failures)} derivation(s) failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
