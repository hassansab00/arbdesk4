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
from common import _cfg, _headers, log_run
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
    features = None
    try:
        features = _call_rpc("refresh_feature_cache")
        print(f"feature cache: {features}")
    except Exception as e:
        print(f"  note: refresh_feature_cache unavailable ({e}) - run sql/ad4_28_feature_cache.sql",
              file=sys.stderr)

    log_run("capacity", "ok", (capacity_rows or 0) + (correlation_rows or 0),
            {"capacity_rows": capacity_rows, "correlation_rows": correlation_rows,
             "climate": climate, "features": features})


if __name__ == "__main__":
    main()
