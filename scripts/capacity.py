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
from common import _cfg, _headers, log_run, refresh_feature_cache
import requests


def _call_rpc(fn, params=None):
    r = requests.post(f"{_cfg()['url']}/rest/v1/rpc/{fn}", headers=_headers(),
                      json=params or {}, timeout=120)
    r.raise_for_status()
    return r.json()


def main():
    capacity_rows = _call_rpc("recompute_capacity")
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
    peaks = None
    try:
        peaks = _call_rpc("refresh_weather_peak")
        print(f"derived_weather_peak: {peaks} city-month row(s)")
    except Exception as e:
        print(f"  note: refresh_weather_peak unavailable ({e}) - run sql/ad4_37_peak_hour.sql",
              file=sys.stderr)

    climate = None
    try:
        climate = _call_rpc("refresh_city_climate")
        print(f"derived_city_climate: {climate}")
    except Exception as e:
        print(f"  note: refresh_city_climate unavailable ({e}) - run sql/ad4_19_stats_cache.sql",
              file=sys.stderr)

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
        print(f"refresh_feature_cache FAILED: {e}", file=sys.stderr)

    log_run("capacity", "attention" if features_error else "ok",
            (capacity_rows or 0) + (correlation_rows or 0),
            {"capacity_rows": capacity_rows, "correlation_rows": correlation_rows,
             "climate": climate, "features": features, "features_error": features_error})
    if features_error:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
