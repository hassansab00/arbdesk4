"""Forecast history the database no longer holds (plan v2 P1.6 phase 2, step 2).

scripts/weather_history.py answers a job's forecast read from the database
plus data/archive for the dates the archive pruned. tools/p16_reader_proof.py
proved it against the live database on 29 Sep (skill's, regime's and station
correction's reads at a 30-day cut: 36,705, 45,231 and 38,913 rows, equal city
by city in the job's order; v_forecast_issued's columns on 49,050 rows). These
pin the rules that proof rests on.
"""
import csv
import datetime as dt
import gzip
import io
import pathlib

import pytest

import weather_history as wh

ROOT = pathlib.Path(__file__).resolve().parents[1]
COLS = ["city_key", "model", "run_at", "observed_at", "for_date", "lead_days", "forecast_max_c", "variables", "source"]
CUT = "2026-07-31"
PRUNE = f"data/archive/forecasts/forecasts-2026-07-30-to-2026-07-30.csv.gz"


def _archive(root, name, rows):
    path = root / "data" / "archive" / "forecasts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in COLS})
    path.write_bytes(gzip.compress(buf.getvalue().encode()))


def _row(city, for_date, run_at, value, model="open_meteo_best_match", lead=1, source="open-meteo",
         observed_at=None):
    return {"city_key": city, "model": model, "run_at": run_at, "observed_at": observed_at or run_at,
            "for_date": for_date, "lead_days": lead, "forecast_max_c": value, "source": source}


@pytest.fixture
def world(tmp_path):
    """An archive of 28-30 Jul, a database that keeps 31 Jul on, a prune log."""
    _archive(tmp_path, "forecasts-2026-07-28-to-2026-07-29.csv.gz", [
        _row("nyc", "2026-07-28", "2026-07-27T00:00:00+00:00", 30.1),
        _row("nyc", "2026-07-29", "2026-07-28T00:00:00+00:00", 31.0),
        _row("tokyo", "2026-07-29", "2026-07-28T00:00:00+00:00", 29.5),
    ])
    _archive(tmp_path, "forecasts-2026-07-30-to-2026-07-30.csv.gz", [
        _row("nyc", "2026-07-30", "2026-07-29T00:00:00+00:00", 32.0),
        _row("nyc", "2026-07-30", "2026-07-29T12:00:00+00:00", 33.0, lead=0),
    ])
    db = [_row("nyc", "2026-07-31", "2026-07-30T00:00:00+00:00", 28.0),
          _row("nyc", "2026-08-01", "2026-07-31T00:00:00+00:00", 27.0)]
    calls = []
    state = {"prunes": [{"finished_at": "2026-09-29T02:40:55+00:00",
                         "detail": {"archived_through": CUT, "file": PRUNE}}]}

    def _db_rows(path, params):
        select, filters, _, _ = wh.parse_filters(params)
        rows = [dict(r) for r in state.get("db", db) if wh.matches(r, filters)]
        return [{c: r.get(c) for c in select} for r in rows] if select else rows

    def rest(path, params=None):
        calls.append(("rest", path, list(params)))
        if path == "ingest_log":
            return state["prunes"][:1]
        if path == "cities":
            return [{"city_key": "nyc", "timezone": "America/New_York"}, {"city_key": "tokyo", "timezone": "Asia/Tokyo"}]
        return _db_rows(path, params)

    def rest_all(path, params=None, *, order, page_size=500):
        calls.append(("rest_all", path, list(params), order, page_size))
        if path == "ingest_log":
            return state["prunes"]
        return wh.sort_rows(_db_rows(path, params), order)

    return {"root": tmp_path, "rest": rest, "rest_all": rest_all, "calls": calls, "state": state, "db": db}


def _read(world, name, params, **kw):
    return wh.read(name, params, rest_fn=world["rest"], rest_all_fn=world["rest_all"],
                   order="for_date.asc,lead_days.asc,model.asc,run_at.asc", page_size=1000,
                   root=str(world["root"]), **kw)


# --------------------------------------------------------------------------
# nothing moves until a read reaches past the cut
# --------------------------------------------------------------------------

def test_never_pruned_is_exactly_the_database_read(world):
    world["state"]["prunes"] = []
    params = [("select", "for_date,forecast_max_c"), ("city_key", "eq.nyc"), ("for_date", "gte.2026-07-01")]
    got = _read(world, "weather_forecasts", params)
    db_calls = [c for c in world["calls"] if c[1] == "weather_forecasts"]
    assert db_calls == [("rest_all", "weather_forecasts", params, "for_date.asc,lead_days.asc,model.asc,run_at.asc", 1000)]
    assert [r["for_date"] for r in got] == ["2026-07-31", "2026-08-01"]


def test_a_read_above_the_cut_is_the_same_read_and_opens_no_file(world, monkeypatch):
    monkeypatch.setattr(wh, "file_rows", lambda path: pytest.fail("a file was read"))
    params = [("select", "for_date,forecast_max_c"), ("city_key", "eq.nyc"), ("for_date", f"gte.{CUT}")]
    got = _read(world, "weather_forecasts", params)
    assert [c[2] for c in world["calls"] if c[1] == "weather_forecasts"] == [params]
    assert [r["forecast_max_c"] for r in got] == [28.0, 27.0]


def test_the_tick_reads_no_file_while_the_keep_covers_the_window(world, monkeypatch):
    """regime's 60 days against a 60-day keep: the history starts on the cut."""
    import regime
    monkeypatch.setattr(wh, "file_rows", lambda path: pytest.fail("a file was read"))
    before = dt.date.fromisoformat(CUT) + dt.timedelta(days=regime.LOOKBACK_DAYS)
    monkeypatch.setattr(regime, "rest", world["rest"])
    monkeypatch.setattr(regime, "rest_all", world["rest_all"])
    monkeypatch.setattr(wh, "ROOT", str(world["root"]))
    regime._history_disagreement("nyc", before)
    assert [c for c in world["calls"] if c[1] == "weather_forecasts"][0][2][2] == ("for_date", f"gte.{CUT}")


# --------------------------------------------------------------------------
# below the cut
# --------------------------------------------------------------------------

def test_a_read_below_the_cut_adds_the_archives_rows_for_that_city(world):
    params = [("select", "for_date,lead_days,forecast_max_c,model"), ("city_key", "eq.nyc"),
              ("for_date", "gte.2026-07-29"), ("for_date", "lte.2026-08-01")]
    got = _read(world, "weather_forecasts", params)
    assert got == [
        {"for_date": "2026-07-29", "lead_days": 1, "forecast_max_c": 31.0, "model": "open_meteo_best_match"},
        {"for_date": "2026-07-30", "lead_days": 0, "forecast_max_c": 33.0, "model": "open_meteo_best_match"},
        {"for_date": "2026-07-30", "lead_days": 1, "forecast_max_c": 32.0, "model": "open_meteo_best_match"},
        {"for_date": "2026-07-31", "lead_days": 1, "forecast_max_c": 28.0, "model": "open_meteo_best_match"},
        {"for_date": "2026-08-01", "lead_days": 1, "forecast_max_c": 27.0, "model": "open_meteo_best_match"},
    ]
    # the database was asked for the key as well, so a row it holds can win
    db_select = dict([c for c in world["calls"] if c[1] == "weather_forecasts"][0][2])["select"].split(",")
    assert set(wh.KEY) <= set(db_select)


def test_a_key_the_database_still_holds_wins_over_the_archives_copy(world):
    """A pruned day re-inserted by an ingest (step 3's gap check) is the
    database's row, once."""
    world["state"]["db"] = world["db"] + [_row("nyc", "2026-07-30", "2026-07-29T00:00:00+00:00", 32.4)]
    got = _read(world, "weather_forecasts", [("select", "for_date,lead_days,forecast_max_c"), ("city_key", "eq.nyc"),
                                             ("for_date", "eq.2026-07-30")])
    assert sorted((r["lead_days"], r["forecast_max_c"]) for r in got) == [(0, 33.0), (1, 32.4)]


def test_a_checkout_without_the_newest_prunes_file_refuses(world):
    (world["root"] / PRUNE).unlink()
    with pytest.raises(wh.StaleCheckout):
        _read(world, "weather_forecasts", [("city_key", "eq.nyc"), ("for_date", "gte.2026-07-01")])


def test_the_prune_is_looked_up_once_per_process(world):
    for _ in range(3):
        _read(world, "weather_forecasts", [("city_key", "eq.nyc"), ("for_date", "gte.2026-07-29")])
    assert sum(1 for c in world["calls"] if c[1] == "ingest_log") == 1


def test_the_single_request_read_and_its_order_and_limit(world):
    """regime._forecasts_for_date's shape: rest(), order and limit in params."""
    got = wh.read("weather_forecasts", [("select", "lead_days,forecast_max_c"), ("city_key", "eq.nyc"),
                                        ("for_date", "eq.2026-07-30"), ("order", "lead_days.asc,run_at.desc"),
                                        ("limit", "1")], rest_fn=world["rest"], root=str(world["root"]))
    assert got == [{"lead_days": 0, "forecast_max_c": 33.0}]


# --------------------------------------------------------------------------
# v_forecast_issued, for rows the view can no longer see
# --------------------------------------------------------------------------

def test_the_views_columns_are_computed_for_archived_rows(world):
    got = _read(world, "v_forecast_issued", [("select", "for_date,lead_days,issued_at,issued_local_date,same_day_issue"),
                                              ("city_key", "eq.nyc"), ("for_date", "eq.2026-07-30")])
    # issued 29 Jul 12:00Z = 08:00 in New York, the day before: not same-day
    assert {r["lead_days"]: (r["issued_local_date"], r["same_day_issue"]) for r in got} == {
        0: ("2026-07-29", False), 1: ("2026-07-28", False)}


def test_a_backtest_sees_only_what_was_issued_by_its_moment(world):
    got = _read(world, "v_forecast_issued", [("select", "for_date,lead_days"), ("city_key", "eq.nyc"),
                                              ("for_date", "eq.2026-07-30"), ("issued_at", "lte.2026-07-29T06:00:00+00:00")])
    assert [r["lead_days"] for r in got] == [1]


def test_previous_runs_are_issued_when_ingested_and_their_day_is_unknown():
    r = wh.with_issued(_row("nyc", "2026-07-30", "2026-07-29T00:00:00+00:00", 1.0, source=wh.PREVIOUS_RUNS,
                            observed_at="2026-08-26T22:27:48.130729+00:00"), "America/New_York")
    assert (r["issued_at"], r["issued_at_source"]) == ("2026-08-26T22:27:48.130729+00:00", "ingest_time_true_issue_unverified")
    assert (r["issued_local_date"], r["issued_lead_days"], r["same_day_issue"]) == (None, None, None)


def test_an_unknown_source_is_unclassified_and_timed_by_run_at():
    r = wh.with_issued(_row("nyc", "2026-07-30", "2026-07-30T13:00:00+00:00", 1.0, source="nws"), "America/New_York")
    assert r["issued_at_source"] == "run_at_unclassified" and r["same_day_issue"] is True


# --------------------------------------------------------------------------
# values, filters and order as the REST API gives them
# --------------------------------------------------------------------------

def test_one_city_is_typed_as_the_whole_file_is_and_only_that_city(world, monkeypatch):
    """Fresh Supabase, part 2b: the tick's 60-day history is almost all
    archive at a three-day keep, so a city read types that city's rows only.
    They must be exactly the rows, and the types, the whole file gives."""
    wh.reset()
    path = str(world["root"] / "data" / "archive" / "forecasts" / "forecasts-2026-07-28-to-2026-07-29.csv.gz")
    typed = []
    real = wh._typed
    monkeypatch.setattr(wh, "_typed", lambda k, v: typed.append(k) or real(k, v))
    tokyo = wh.archived("forecasts", None, CUT, root=str(world["root"]), city="tokyo")
    assert typed.count("city_key") == 1, "rows of other cities were typed"
    assert wh.archived("forecasts", None, CUT, root=str(world["root"]), city="tokyo") == tokyo
    assert wh.archived("forecasts", None, CUT, root=str(world["root"]), city="paris") == []
    whole = [r for r in wh.file_rows(path) if r["city_key"] == "tokyo"]
    assert tokyo == whole and [type(v) for v in tokyo[0].values()] == [type(v) for v in whole[0].values()]
    assert typed.count("city_key") == 1 + 3, "the city read was typed again, or the file read did not type every row"
    wh.reset()


def test_values_come_back_as_rest_returned_them():
    assert wh._value("15.8") == 15.8 and wh._value("33") == 33 and isinstance(wh._value("33"), int)
    assert wh._value("33.0") == 33.0 and isinstance(wh._value("33.0"), float)
    assert wh._value("") is None
    assert wh._value('{"min_c":13.3,"n_hours":24}') == {"min_c": 13.3, "n_hours": 24}
    assert wh._value("{'min_c': 13.3, 'ok': True, 'x': None}") == {"min_c": 13.3, "ok": True, "x": None}
    assert wh._typed("run_at", "2026-07-29T00:00:00+00:00") == "2026-07-29T00:00:00+00:00"


def test_filters_the_jobs_use():
    rows = [{"for_date": d, "lead_days": l, "forecast_max_c": v, "source": s}
            for d, l, v, s in (("2026-07-01", 1, 1.0, "a"), ("2026-07-02", 3, None, "b"), ("2026-07-03", 4, 2.0, "a"))]
    keep = lambda p: [r["for_date"][-2:] for r in rows if wh.matches(r, wh.parse_filters(p)[1])]  # noqa: E731
    assert keep([("lead_days", "lte.3")]) == ["01", "02"]
    assert keep([("for_date", "gt.2026-07-01"), ("for_date", "lt.2026-07-03")]) == ["02"]
    assert keep([("forecast_max_c", "not.is.null")]) == ["01", "03"]
    assert keep([("forecast_max_c", "is.null")]) == ["02"]
    assert keep([("source", "in.(b,c)")]) == ["02"]
    with pytest.raises(ValueError):
        wh.parse_filters([("model", "like.open*")])
    with pytest.raises(ValueError):
        wh.parse_filters([("offset", "10")])


def test_the_lowest_date_a_read_can_return():
    lo = lambda p: wh.lowest_date(wh.parse_filters(p)[1])  # noqa: E731
    assert lo([("for_date", "gte.2026-07-01"), ("for_date", "lte.2026-08-01")]) == "2026-07-01"
    assert lo([("for_date", "gt.2026-07-01")]) == "2026-07-02"
    assert lo([("for_date", "eq.2026-07-05")]) == "2026-07-05"
    assert lo([("for_date", "lt.2026-07-05")]) is None


def test_order_puts_nulls_where_postgres_does_and_sorts_text_as_en_us():
    rows = [{"k": "new_york"}, {"k": None}, {"k": "newark"}]
    assert [r["k"] for r in wh.sort_rows(rows, "k.asc")] == ["newark", "new_york", None]
    assert [r["k"] for r in wh.sort_rows(rows, "k.desc")] == [None, "new_york", "newark"]
    ts = [{"run_at": "2026-07-29T00:00:00.5+00:00"}, {"run_at": "2026-07-29T00:00:00+00:00"}]
    assert [r["run_at"] for r in wh.sort_rows(ts, "run_at.asc")] == ["2026-07-29T00:00:00+00:00",
                                                                     "2026-07-29T00:00:00.5+00:00"]


# --------------------------------------------------------------------------
# the repository and the jobs
# --------------------------------------------------------------------------

def test_every_committed_forecast_file_holds_the_dates_its_name_says():
    """archived() picks files by the for_date range in their names."""
    files = wh.archive_files("forecasts")
    assert files
    for lo, hi, path in files:
        dates = {r["for_date"] for r in wh.file_rows(path)}
        assert dates and lo <= min(dates) and max(dates) <= hi, path


def test_each_source_names_the_dataset_the_archive_prunes_it_into():
    """weather_forecast_models has no archive dataset yet (phase 2 step 4);
    when it gets one, it must be the one the reader looks in."""
    import archive_observations as ao
    for name, spec in wh.SOURCES.items():
        # A dataset that keeps the rows and moves only a payload (rows_stay:
        # forecast_variables, WXPredict build 2.A) takes nothing this reader reads.
        datasets = [k for k, v in ao.TABLES.items() if v["table"] == spec["table"] and not v.get("rows_stay")]
        assert datasets in ([], [spec["dataset"]]), (name, datasets)
        if datasets:
            assert ao.TABLES[spec["dataset"]]["cutoff_col"] == "for_date"
    assert wh.SOURCES["weather_forecasts"]["dataset"] in ao.TABLES


def test_a_payload_dataset_keeps_the_rows_weather_history_reads():
    """forecast_variables strips weather_forecasts.variables and deletes no row,
    and no reader through weather_history selects the payload."""
    import archive_observations as ao
    spec = ao.TABLES["forecast_variables"]
    assert spec["rows_stay"] is True and spec["table"] == "weather_forecasts"
    sql = (ROOT / "sql" / "ad4_prune_forecast_payloads.sql").read_text()
    body = sql[sql.index("create or replace function public.prune_forecast_variables"):]
    body = body[:body.index("$function$;")]
    assert "delete from" not in body.lower()
    assert "set variables = jsonb_build_object('variables_in_repo', true)" in body


def test_the_jobs_read_through_it():
    src = lambda f: (ROOT / "scripts" / f).read_text()  # noqa: E731
    assert 'weather_history.read("v_forecast_issued"' in src("measure_skill.py")
    regime_src = src("regime.py")
    assert regime_src.count("weather_history.read(") == 3
    assert "weather_history.read(\n        \"weather_forecast_models\"" in src("station_correction.py")


def test_regime_reads_the_sixty_days_it_has_been_built_on():
    import regime
    assert regime.LOOKBACK_DAYS == 60


def test_the_archive_names_its_files_as_the_reader_reads_them():
    """archive_files() picks a file by the for_date range in its name; the
    archive writes `<dataset>-<first>-to-<last>.csv.gz` from the cutoff
    column's values (for_date for both forecast datasets). A new name format
    would hide every file from station correction and regime."""
    import archive_observations as ao
    src = (ROOT / "scripts" / "archive_observations.py").read_text()
    assert 'asset_name = f"{name}-{str(lo)[:10]}-to-{str(hi)[:10]}.csv.gz"' in src
    for name in {s["dataset"] for s in wh.SOURCES.values()}:
        assert ao.TABLES[name]["cutoff_col"] == "for_date"
        m = wh._NAME.search(f"{name}-2026-08-20-to-2026-08-29.csv.gz")
        assert m and m.groups() == ("2026-08-20", "2026-08-29"), name
