"""The live record (WXPredict WP3; Hassan, 11 Oct: "go ahead with live
capture"): scripts/live_capture.py keeps every input the hourly tick had, as it
arrived, and records the training's own definitions (the fields, the forecast
variables, the rule's horizon) rather than copies of them; the tick shares its
one IEM answer; tick.yml runs it beside the checkpoints and collects it; one
part failing stops nothing; off or manual stores nothing."""
import datetime as dt
import json
import pathlib
import re
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

import honest_record  # noqa: E402
import ingest_observations as io_obs  # noqa: E402
import live_capture as lc  # noqa: E402
import tick  # noqa: E402
from wxpredict import features, fetch_obs  # noqa: E402

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# the training's definitions, not copies of them
# ---------------------------------------------------------------------------
def test_the_reports_carry_every_field_the_training_reads():
    assert lc.FULL_FIELDS == fetch_obs.FIELDS + ["metar"], "the T group and 6-hour extremes come from the text"
    assert set(io_obs.DATA.split(",")) <= set(lc.FULL_FIELDS), "the tick's own fields are still asked"


def test_the_horizon_is_the_training_rule():
    assert lc.HORIZON_H == 24 - features.PUBLISH_H
    hour = dt.datetime(2026, 10, 12, 9, tzinfo=UTC)
    known_at = int(hour.timestamp()) - lc.HORIZON_H * 3600
    assert features.hourly_known(int(hour.timestamp()), known_at)
    assert not features.hourly_known(int(hour.timestamp()), known_at - 1)


def test_the_wider_answer_parses_to_the_same_observation_rows():
    """The tick now asks for every field; parse() reads only its own columns
    (measured 11 Oct over 47 stations: 405 rows from each answer, 0 differ)."""
    narrow = ("station,valid,tmpf,dwpf,relh,drct,sknt,p01i,skyc1,mslp\n"
              "LGA,2026-10-11 20:51,60.0,50.0,70.1,180.00,5.00,0.00,FEW,1015.0\n"
              "EGLC,2026-10-11 20:50,55.4,48.2,76.0,230.00,8.00,,BKN,\n")
    wide = ("station,valid,tmpf,dwpf,relh,drct,sknt,gust,p01i,alti,mslp,vsby,skyc1,skyc2,skyc3,"
            "skyl1,skyl2,skyl3,wxcodes,metar\n"
            "LGA,2026-10-11 20:51,60.0,50.0,70.1,180.00,5.00,,0.00,30.01,1015.0,10.00,FEW,,,4000.00,,,,"
            "KLGA 112051Z 18005KT 10SM FEW040 16/10 A3001 RMK AO2 SLP150 T01560100\n"
            "EGLC,2026-10-11 20:50,55.4,48.2,76.0,230.00,8.00,,,29.95,,6.00,BKN,,,1800.00,,,,"
            "EGLC 112050Z 23008KT 9999 BKN018 13/09 Q1014\n")
    by = {"LGA": "nyc", "EGLC": "london"}
    assert io_obs.parse(narrow, by_station=by) == io_obs.parse(wide, by_station=by)


# ---------------------------------------------------------------------------
# books
# ---------------------------------------------------------------------------
def test_a_book_top_is_found_by_price_not_position():
    book = {"asset_id": "t1", "timestamp": "1760216000123", "last_trade_price": "0.420",
            "min_order_size": "5", "tick_size": "0.01",
            "bids": [{"price": "0.30", "size": "100"}, {"price": "0.41", "size": "50"},
                     {"price": "0.36", "size": "10"}, {"price": "0.40", "size": "0"}],
            "asks": [{"price": "0.99", "size": "1000"}, {"price": "0.48", "size": "20"},
                     {"price": "0.43", "size": "30"}]}
    bid, ask, bid_sz, ask_sz, bid_usd, ask_usd, last, ts, mn, tk = lc.book_top(book)
    assert (bid, ask, bid_sz, ask_sz) == (0.41, 0.43, 50, 30)
    # within 5 cents of the touch, inclusive, as the 2-hourly snapshot counts
    assert bid_usd == pytest.approx(0.41 * 50 + 0.36 * 10)
    assert ask_usd == pytest.approx(0.43 * 30 + 0.48 * 20)
    assert (last, ts, mn, tk) == (0.42, 1760216000, 5.0, 0.01)


def test_an_empty_side_is_empty_not_zero():
    top = lc.book_top({"bids": [], "asks": [{"price": "0.02", "size": "100"}]})
    assert top[0] is None and top[2] is None and top[4] is None
    assert top[1] == 0.02


def test_the_books_are_asked_in_requests_the_venue_takes(monkeypatch):
    """869 tokens in one request were refused ("Payload exceeds the limit"),
    500 answered (11 Oct): chunks never pass that, a failed chunk is recorded
    and the others kept, and an answer is matched by asset_id."""
    assert lc.BOOKS_PER_REQUEST <= 500
    band_of = {f"tok{i:04d}": f"band{i}" for i in range(lc.BOOKS_PER_REQUEST * 2 + 69)}
    sent = []

    class R:
        def __init__(self, body, fail=False):
            self.body, self.fail = body, fail

        def raise_for_status(self):
            if self.fail:
                raise RuntimeError("502")

        def json(self):
            return self.body

    def post(url, headers=None, data=None, timeout=None):
        toks = [x["token_id"] for x in json.loads(data)]
        sent.append(len(toks))
        if len(sent) == 2:
            return R(None, fail=True)
        return R([{"asset_id": t, "bids": [{"price": "0.1", "size": "1"}], "asks": []} for t in reversed(toks)
                  if t != "tok0001"])                     # one bucket answered without a book
    monkeypatch.setattr(lc, "_post", post)
    payload, d = lc.fetch_books(band_of, lambda: 60)
    assert max(sent) <= lc.BOOKS_PER_REQUEST and sum(sent) == len(band_of) and len(sent) == 3
    assert d["books"] == len(band_of) - lc.BOOKS_PER_REQUEST - 1 and len(d["errors"]) == 1
    assert payload["tops"]["band0"][0] == 0.1, "matched by asset_id, not by position"
    # THE ROW SAYS WHAT IT LACKS (Codex on #381): every open bucket is in exactly
    # one of tops, failed and no_book, and a failed request makes it incomplete
    assert payload["complete"] is False and payload["buckets"] == len(band_of)
    assert len(payload["failed"]) == lc.BOOKS_PER_REQUEST and payload["no_book"] == ["band1"]
    parts = [set(payload["tops"]), set(payload["failed"]), set(payload["no_book"])]
    assert set().union(*parts) == set(band_of.values()) and sum(len(p) for p in parts) == len(band_of)


def test_buckets_never_asked_for_lack_of_time_are_named(monkeypatch):
    band_of = {f"tok{i:04d}": f"band{i}" for i in range(lc.BOOKS_PER_REQUEST + 5)}
    monkeypatch.setattr(lc, "_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    payload, d = lc.fetch_books(band_of, lambda: lc.RESERVE_S - 1)
    assert payload["complete"] is False and len(payload["failed"]) == len(band_of) and payload["tops"] == {}
    assert d["asked"] == 0


# ---------------------------------------------------------------------------
# the hourly forecast
# ---------------------------------------------------------------------------
def test_the_hourly_values_kept_are_the_training_variables_in_its_order():
    now = dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC)
    first, last = lc.hourly_window(now)
    assert first == dt.datetime(2026, 10, 10) and last == dt.datetime(2026, 10, 12, 14)
    times = [(first + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(-2, 48)]
    answer = {"hourly": {"time": times, **{f"{v}_previous_day1": [float(i) for i in range(len(times))]
                                           for v in honest_record.HEATING}}}
    answer["hourly"]["temperature_2m_previous_day1"][5] = None
    rows = lc.hourly_rows("jeddah", answer, first, last)
    keys = [r["key"] for r in rows]
    assert keys[0] == "jeddah|2026-10-10T00:00" and keys[-1] == "jeddah|2026-10-11T23:00"
    assert "jeddah|2026-10-10T03:00" not in keys, "an hour without a temperature is left out"
    assert all(len(r["payload"]) == len(honest_record.HEATING) for r in rows)
    # the key's stamp is the hourly record's own (honest_record.HOURLY_HEADER "utc")
    assert re.fullmatch(r"jeddah\|\d{4}-\d\d-\d\dT\d\d:\d\d", keys[0])


def test_the_forecast_is_asked_a_chunk_at_a_time_in_gmt(monkeypatch):
    cities = [{"city_key": f"c{i}", "latitude": i, "longitude": -i} for i in range(lc.CITIES_PER_REQUEST + 3)]
    asked = []

    class R:
        def __init__(self, js):
            self.js = js

        def raise_for_status(self):
            pass

        def json(self):
            return self.js

    def get(url, params=None, timeout=None):
        n = len(params["latitude"].split(","))
        asked.append((url, params))
        if len(asked) == 2:
            return R([{"hourly": {}}])            # one answer for three places: refused, recorded
        return R([{"hourly": {"time": ["2026-10-11T12:00"],
                              **{f"{v}_previous_day1": [1.0] for v in honest_record.HEATING}}}] * n)
    monkeypatch.setattr(lc, "_get", get)
    rows, d = lc.fetch_hourly(cities, dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC), lambda: 60)
    url, params = asked[0]
    assert url == honest_record.PREVIOUS_API and params["timezone"] == "GMT"
    assert params["hourly"] == ",".join(f"{v}_previous_day1" for v in honest_record.HEATING)
    assert d["answered"] == lc.CITIES_PER_REQUEST and len(d["errors"]) == 1 and len(rows) == lc.CITIES_PER_REQUEST


# ---------------------------------------------------------------------------
# the station reports, from the tick's own answer
# ---------------------------------------------------------------------------
def test_the_reports_are_kept_as_iem_wrote_them():
    text = ("station,valid," + ",".join(lc.FULL_FIELDS) + "\n" +
            "OEJN,2026-10-11 20:00," + ",".join(["31.0" if f == "tmpf" else "" for f in lc.FULL_FIELDS[:-1]]) +
            ",OEJN 112000Z 30004KT CAVOK 31/24 Q1008 NOSIG\n")
    (row,) = lc.report_rows(text)
    assert row["key"] == "OEJN|2026-10-11 20:00"
    assert row["payload"][0] == "31.0" and row["payload"][-1].startswith("OEJN 112000Z")
    assert len(row["payload"]) == len(lc.FULL_FIELDS)


def test_the_tick_leaves_its_answer_whole_and_never_raises(tmp_path):
    path = tmp_path / "iem_answer.txt"
    at = dt.datetime(2026, 10, 11, 21, 36, 4, tzinfo=UTC)
    assert tick.share_with_capture("station,valid\nOEJN,x\n", at, str(path))
    got = lc.read_iem(str(path), 1, lambda: 60)
    assert got == (at, "station,valid\nOEJN,x\n")
    assert not (tmp_path / "iem_answer.txt.tmp").exists(), "renamed into place, never half-written"
    assert tick.share_with_capture("x", at, str(tmp_path / "no" / "such" / "dir" / "f")) is False
    assert lc.read_iem(str(tmp_path / "never"), 0.6, lambda: 60) is None


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
@pytest.fixture
def desk(monkeypatch, tmp_path):
    calls = {"put": [], "log": []}
    monkeypatch.setattr(lc, "mode", lambda: "auto")
    monkeypatch.setattr(lc, "open_bands", lambda now: {"t1": "b1"})
    monkeypatch.setattr(lc, "fetch_books", lambda band_of, left: (
        {"buckets": 1, "complete": True, "failed": [], "no_book": [], "tops": {"b1": [0.4] * 10}},
        {"buckets": 1, "asked": 1, "books": 1, "failed": 0, "no_book": 0, "errors": []}))
    monkeypatch.setattr(lc, "get_cities", lambda: [{"city_key": "jeddah", "latitude": 21.7, "longitude": 39.2}])
    monkeypatch.setattr(lc, "fetch_hourly", lambda cities, now, left: (
        [{"key": "jeddah|2026-10-12T09:00", "payload": [31.0]}], {"cities": 1, "answered": 1, "values": 1, "errors": []}))
    monkeypatch.setattr(lc, "rpc", lambda fn, params, timeout=None: calls["put"].append((fn, params)) or {"new": 1})
    monkeypatch.setattr(lc, "log_run", lambda job, status, rows, detail: calls["log"].append((job, status, rows, detail)))
    iem = tmp_path / "iem.txt"
    tick.share_with_capture("station,valid," + ",".join(lc.FULL_FIELDS) + "\nOEJN,2026-10-11 20:00" +
                            "," * len(lc.FULL_FIELDS) + "\n", dt.datetime(2026, 10, 11, 21, 36, 2, tzinfo=UTC), str(iem))
    calls["iem"] = str(iem)
    return calls


def test_a_run_keeps_all_three_and_writes_one_row(desk):
    status, detail = lc.run(now=dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC), iem_path=desk["iem"])
    assert status == "ok"
    kinds = [p["p_kind"] for fn, p in desk["put"]]
    assert kinds == ["books", "hourly", "report"] and all(fn == "live_capture_put" for fn, _ in desk["put"])
    report = desk["put"][2][1]
    assert report["p_captured_at"] == "2026-10-11T21:36:02+00:00", "a report's time is when the tick received it"
    (books,) = desk["put"][0][1]["p_rows"]
    assert books["key"] == "all" and books["payload"]["complete"] is True and "b1" in books["payload"]["tops"]
    (job, st, rows, d), = desk["log"]
    assert (job, st, rows) == (lc.JOB, "ok", 3) and d["reports"]["stations"] == 1


def test_one_part_failing_stops_nothing(desk, monkeypatch):
    def boom(cities, now, left):
        raise TimeoutError("Open-Meteo hung")
    monkeypatch.setattr(lc, "fetch_hourly", boom)
    status, detail = lc.run(now=dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC), iem_path=desk["iem"])
    assert status == "partial" and "Open-Meteo hung" in detail["hourly"]["error"]
    assert [p["p_kind"] for _, p in desk["put"]] == ["books", "report"]


def test_an_incomplete_snapshot_is_kept_and_marked(desk, monkeypatch):
    monkeypatch.setattr(lc, "fetch_books", lambda band_of, left: (
        {"buckets": 2, "complete": False, "failed": ["b2"], "no_book": [], "tops": {"b1": [0.4] * 10}},
        {"buckets": 2, "asked": 2, "books": 1, "failed": 1, "no_book": 0, "errors": ["tokens 1-2: HTTPError"]}))
    status, detail = lc.run(now=dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC), iem_path=desk["iem"])
    assert status == "partial" and detail["books"]["failed"] == 1
    (books,) = desk["put"][0][1]["p_rows"]
    assert books["payload"]["complete"] is False and books["payload"]["failed"] == ["b2"]


def test_no_answer_from_the_tick_is_said(desk, monkeypatch, tmp_path):
    monkeypatch.setattr(lc, "IEM_WAIT_S", 0.6)
    status, detail = lc.run(now=dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC), iem_path=str(tmp_path / "none"))
    assert status == "partial" and detail["reports"]["error"].startswith("no IEM answer")


@pytest.mark.parametrize("mode", ["off", "manual"])
def test_off_or_manual_stores_nothing(desk, monkeypatch, mode):
    monkeypatch.setattr(lc, "mode", lambda: mode)
    status, _ = lc.run(now=dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC), iem_path=desk["iem"])
    assert status == "skipped" and desk["put"] == [] and desk["log"][0][1] == "skipped"


def test_a_rehearsal_writes_nothing(desk):
    status, _ = lc.run(now=dt.datetime(2026, 10, 11, 21, 36, tzinfo=UTC), dry_run=True, iem_path=desk["iem"])
    assert desk["put"] == [] and desk["log"] == []


def test_a_run_its_timeout_ended_leaves_a_row(monkeypatch):
    rows = []
    monkeypatch.setattr(lc, "log_run", lambda *a: rows.append(a))
    assert lc.main(["--killed", "124"]) == 0
    (job, status, n, detail), = rows
    assert (job, status, detail["exit_code"]) == (lc.JOB, "error", "124")


# ---------------------------------------------------------------------------
# the tick's workflow
# ---------------------------------------------------------------------------
def _steps():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "tick.yml").read_text())
    return next(iter(doc["jobs"].values()))["steps"]


def test_it_runs_beside_the_checkpoints_and_is_collected():
    steps = _steps()
    names = [s.get("name") for s in steps]
    start = names.index("The live record, started beside the other steps (WXPredict WP3)")
    collect = names.index("The live record, collected (WXPredict WP3)")
    ticks = names.index("Checkpoints")
    assert start < ticks < collect
    s, c, t = steps[start], steps[collect], steps[ticks]
    assert "dry_run != 'true'" in s["if"], "a rehearsal stores nothing"
    assert s["env"]["LIVE_CAPTURE_IEM"] == t["env"]["LIVE_CAPTURE_IEM"], "the tick leaves its answer where the capture reads"
    backstop = int(re.search(r"timeout (\d+) \.venv/bin/python scripts/live_capture\.py", s["run"]).group(1))
    assert backstop > lc.BUDGET_S + lc.RESERVE_S, "the backstop only ends a hang"
    assert f"started + {backstop} + 5" in c["run"], "collected once its backstop has passed"
    assert "--killed" in c["run"] and '"124"' in c["run"]
    assert ") < /dev/null > /dev/null 2>&1 &" in s["run"], "in the background"


def test_it_is_mirrored_and_archived():
    import archive_observations as ao
    import mirror_to_repo as mirror
    assert mirror.TABLES["live_capture"]["kind"] == "append" and mirror.TABLES["live_capture"]["time"] == "captured_at"
    spec = ao.TABLES["live_capture"]
    assert spec["table"] == "live_capture" and spec["keep_days"] == 2 and spec["prune_rpc"] == "prune_live_capture"
    assert "capture_id" not in spec["columns"]


def test_it_is_in_the_database_suite():
    pkg = json.loads((ROOT / "tests" / "database" / "package.json").read_text())
    assert "node live-capture.cjs" in pkg["scripts"]["test"]
