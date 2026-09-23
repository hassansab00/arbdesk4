"""A fitted verdict that the database refuses to overwrite is frozen forever.

MEASURED IN PRODUCTION, 2026-09-22. scripts/forecast_postprocess.py wrote
derived_forecast_postprocess at 17:27:16 and again at 17:50:12, against a day
more settled evidence the second time. The table's computed_at never moved off
17:27:16, because both writes went through common.upsert(), which sends
`Prefer: resolution=ignore-duplicates` - ON CONFLICT DO NOTHING. The second
run computed 399 correct rows and the database discarded every one of them.

That is the identical failure common.upsert_replace()'s own docstring records
for derived_model_promotion the day before: 392 rows written once, 384 rows
recomputed daily and thrown away, and seven leads reading `stale` on every
city forever. A bias correction that cannot be re-fitted is not a fitted
layer, and an hour that cannot lose the right to price from the day so far is
not a gate.

TWO THINGS HAVE TO BE TRUE and they are one word apart:

  - the write is upsert_replace, so the row can be rewritten at all;
  - computed_at travels ON THE ROW, because merge-duplicates updates only the
    columns the payload carries. A column defaulted to now() is stamped on
    INSERT and never again, and sql/ad4_39_freshness.sql reads exactly that
    column to decide whether the layer is current.

So these tests run each fitter's main() against a fake `common` and assert
what reached the database, rather than reading the source and hoping.
"""

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import forecast_postprocess as fp          # noqa: E402
import trajectory as tj                    # noqa: E402


class _Recorder:
    """Stands in for scripts/common, remembering how the rows were written."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []          # (kind, table, rows, on_conflict)
        self.logged = []

    # -- the reader ------------------------------------------------------
    def rest_all(self, table, params, order=None, page_size=None):
        return list(self._rows)

    # -- the two writers, one of which must never be used here -----------
    def upsert(self, table, rows, on_conflict, chunk=500):
        self.calls.append(("ignore-duplicates", table, rows, on_conflict))
        return len(rows)

    def upsert_replace(self, table, rows, on_conflict, chunk=500):
        self.calls.append(("merge-duplicates", table, rows, on_conflict))
        return len(rows)

    def log_run(self, job, status, rows, detail):
        self.logged.append((job, status, rows, detail))

    # the city's unit and measurement layer, which the P3.4 bucket gate reads
    def get_cities(self, *a, **k):
        return [{"city_key": "alpha", "unit": "C",
                 "observation_q_down": 0.02, "observation_q_up": 0.05}]


def _run(module, recorder, argv):
    sys.modules["common"] = recorder
    old_argv = sys.argv
    sys.argv = argv
    try:
        assert module.main() == 0
    finally:
        sys.argv = old_argv
        sys.modules.pop("common", None)


# ---------------------------------------------------------------------------
# The evidence each fitter reads, in the shape its loader expects.
# ---------------------------------------------------------------------------
def _postprocess_rows(n_days=14, city="alpha", lead=1):
    """A cell with a real, learnable bias: the forecast runs 2 C warm."""
    start = dt.date(2026, 6, 1)
    out = []
    for i in range(n_days):
        day = (start + dt.timedelta(days=i)).isoformat()
        observed = 18.0 + (i % 5)
        out.append({
            "city_key": city, "for_date": day, "lead_days": lead,
            "model": "ecmwf", "run_at": f"{day}T00:00:00+00:00",
            "forecast_max_c": observed + 2.0, "observed_max_c": observed,
            "obs_source": "station_hourly",
        })
    return out


def _trajectory_rows(n_days=14, city="alpha", hour=14):
    start = dt.date(2026, 6, 1)
    out = []
    for i in range(n_days):
        day = (start + dt.timedelta(days=i)).isoformat()
        final = 24.0 + (i % 4)
        out.append({
            "city_key": city, "local_date": day, "local_hour": hour,
            "temp_c": final - 1.5, "running_max_c": final - 1.0,
            "final_max_c": final, "final_is_verified": True,
            # the climb profile as of the day before (plan v2 P3.4)
            "climb_left_asof_c": 1.4, "climb_sd_asof_c": 0.6, "climb_n_asof": 60,
            "forecast_c": final + 1.0, "forecast_sigma_c": 2.0,
        })
    return out


# ---------------------------------------------------------------------------
# derived_forecast_postprocess
# ---------------------------------------------------------------------------
def test_the_station_bias_is_written_so_a_later_fit_can_overwrite_it():
    rec = _Recorder(_postprocess_rows())
    _run(fp, rec, ["forecast_postprocess.py"])

    assert len(rec.calls) == 1, rec.calls
    kind, table, rows, on_conflict = rec.calls[0]
    assert table == "derived_forecast_postprocess"
    assert kind == "merge-duplicates", (
        "ignore-duplicates freezes the fit at its first run: every later run "
        "computes correct rows and the database discards them")
    assert on_conflict == "city_key,lead_days", (
        "the conflict target must be the table's primary key or the replace "
        "silently inserts a duplicate instead of rewriting the verdict")
    assert rows, "the fit produced no rows to write"


def test_every_station_bias_row_carries_the_hour_it_was_decided():
    """merge-duplicates updates only the columns the payload carries."""
    rec = _Recorder(_postprocess_rows())
    _run(fp, rec, ["forecast_postprocess.py"])
    _, _, rows, _ = rec.calls[0]

    stamps = {r.get("computed_at") for r in rows}
    assert None not in stamps, (
        "a row without computed_at leaves the old timestamp in place, and "
        "ad4_39_freshness then reports the age of the FIRST fit forever")
    assert len(stamps) == 1, "one run, one timestamp"
    stamped = dt.datetime.fromisoformat(stamps.pop())
    assert stamped.tzinfo is not None, "a naive timestamp is not a moment"
    age = (dt.datetime.now(dt.timezone.utc) - stamped).total_seconds()
    assert 0 <= age < 300, f"stamped {age:.0f}s from now"


def test_a_dry_run_writes_nothing_at_all():
    rec = _Recorder(_postprocess_rows())
    _run(fp, rec, ["forecast_postprocess.py", "--dry-run"])
    assert rec.calls == [], "--dry-run must not reach the database"


# ---------------------------------------------------------------------------
# derived_trajectory
# ---------------------------------------------------------------------------
def test_the_trajectory_verdict_is_written_so_a_later_fit_can_overwrite_it():
    rec = _Recorder(_trajectory_rows())
    _run(tj, rec, ["trajectory.py"])

    assert len(rec.calls) == 1, rec.calls
    kind, table, rows, on_conflict = rec.calls[0]
    assert table == "derived_trajectory"
    assert kind == "merge-duplicates", (
        "whether an hour may price from the day so far is a verdict, and a "
        "gate that cannot change its mind is not a gate")
    assert on_conflict == "city_key,local_hour"
    assert rows


def test_every_trajectory_row_carries_the_hour_it_was_decided():
    rec = _Recorder(_trajectory_rows())
    _run(tj, rec, ["trajectory.py"])
    _, _, rows, _ = rec.calls[0]

    stamps = {r.get("computed_at") for r in rows}
    assert None not in stamps
    assert len(stamps) == 1
    stamped = dt.datetime.fromisoformat(stamps.pop())
    assert stamped.tzinfo is not None
    assert 0 <= (dt.datetime.now(dt.timezone.utc)
                 - stamped).total_seconds() < 300


def test_the_trajectory_dry_run_writes_nothing_at_all():
    rec = _Recorder(_trajectory_rows())
    _run(tj, rec, ["trajectory.py", "--dry-run"])
    assert rec.calls == []


# ---------------------------------------------------------------------------
# The column has to exist, or the write is rejected for every row.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sql_file,table", [
    ("sql/ad4_83_forecast_postprocess.sql", "derived_forecast_postprocess"),
    ("sql/ad4_86_trajectory.sql", "derived_trajectory"),
])
def test_the_table_actually_has_the_column_the_payload_stamps(sql_file, table):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    body = open(os.path.join(root, sql_file), encoding="utf-8").read()
    start = body.index(f"create table if not exists {table}")
    ddl = body[start:body.index(");", start)]
    assert "computed_at" in ddl, f"{table} has no computed_at to stamp"
