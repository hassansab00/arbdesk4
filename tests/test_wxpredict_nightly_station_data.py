"""Each day reaches WXPredict's training data (12 Oct): fetch_obs.py update
adds each night's new station reports and the days they touch as new files
beside the station files, and the readers (common.station_report_rows,
station_daily_rows; build_table's loaders) read the files and the nights
together. Until 12 Oct the station data was refreshed by hand only."""
import argparse
import datetime as dt
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import build_table as bt  # noqa: E402
from wxpredict import common  # noqa: E402
from wxpredict import fetch_obs as fo  # noqa: E402

NIGHT = "2026-10-13"


def report(valid, tmpf, t10=""):
    row = {h: "" for h in fo.REPORT_HEADER}
    row.update(valid=valid, tmpf=tmpf, t10_c=t10)
    return [row[h] for h in fo.REPORT_HEADER]


def iem_answer(rows):
    """IEM's onlycomma answer: station, valid ('YYYY-MM-DD HH:MM'), the fields, the METAR."""
    lines = [",".join(["station", "valid", *fo.FIELDS, "metar"])]
    for station, valid, tmpf, metar in rows:
        vals = {f: "" for f in fo.FIELDS}
        vals["tmpf"] = tmpf
        lines.append(",".join([station, valid, *[vals[f] for f in fo.FIELDS], metar]))
    return "\n".join(lines) + "\n"


def setup(tmp_path, monkeypatch, answer, base_reports, base_days=()):
    reports = tmp_path / "wx" / "station_reports"
    daily = tmp_path / "wx" / "station_daily.csv.gz"
    monkeypatch.setattr(common, "REPORTS", str(reports))
    monkeypatch.setattr(common, "STATION_DAILY", str(daily))
    monkeypatch.setattr(common, "event_stations", lambda: {"EGLC": "london"})
    monkeypatch.setattr(common, "cities", lambda: {"london": {"timezone": "UTC"}})
    common.write_csv(str(reports / "EGLC.csv.gz"), fo.REPORT_HEADER, base_reports)
    common.write_csv(str(daily), fo.DAILY_HEADER, list(base_days))
    asked = []

    def fake_request(stations, start, end, data, timeout=600, tries=fo.TRIES):
        asked.append((stations, start, end, timeout, tries))
        return iem_answer(answer)

    monkeypatch.setattr(fo, "request", fake_request)
    return reports, daily, asked


def run(end=NIGHT):
    return fo.cmd_update(argparse.Namespace(end=end))


def test_the_night_adds_only_reports_the_record_does_not_hold(tmp_path, monkeypatch):
    base = [report("2026-10-12T10:20Z", "60.00"), report("2026-10-12T11:20Z", "61.00")]
    answer = [("EGLC", "2026-10-12 10:20", "60.00", ""),           # held, the same
              ("EGLC", "2026-10-12 11:20", "62.00", ""),           # held, differs: counted, not applied
              ("EGLC", "2026-10-12 12:20", "63.00", "EGLC RMK T01720150"),
              ("XXXX", "2026-10-12 12:20", "50.00", "")]
    reports, _, asked = setup(tmp_path, monkeypatch, answer, base)
    before = (reports / "EGLC.csv.gz").read_bytes()

    s = run()

    assert asked[0][1:] == (dt.date(2026, 10, 10), dt.date(2026, 10, 14), fo.UPDATE_TIMEOUT_S, fo.UPDATE_TRIES), \
        "from two days before the newest report held to today, with the nightly timeout"
    assert (reports / "EGLC.csv.gz").read_bytes() == before, "the station's file is not rewritten"
    night = list(common.read_csv(str(tmp_path / "wx" / "station_reports_nightly" / f"reports-{NIGHT}.csv.gz")))
    assert [(r["station"], r["valid"], r["tmpf"], r["t10_c"]) for r in night] == \
        [("EGLC", "2026-10-12T12:20Z", "63.00", "17.2")]
    assert s["new_reports"] == 1 and s["reports_differing_from_the_record_not_applied"] == {"EGLC": 1}
    assert s["unknown_station_rows"] == {"XXXX": 1}

    rows = common.station_report_rows("EGLC")
    assert [(r["valid"], r["tmpf"]) for r in rows] == \
        [("2026-10-12T10:20Z", "60.00"), ("2026-10-12T11:20Z", "61.00"), ("2026-10-12T12:20Z", "63.00")], \
        "the files and the night together, oldest first, the record's first reading kept"
    assert bt.load_reports("EGLC").t[-1] == dt.datetime(2026, 10, 12, 12, 20, tzinfo=dt.timezone.utc).timestamp()


def test_a_partial_day_becomes_whole_and_an_unchanged_day_is_not_written(tmp_path, monkeypatch):
    def hourly(day, temps):
        return [(f"{day}T{h:02d}:20Z", f"{t:.2f}") for h, t in enumerate(temps)]

    d10 = hourly("2026-10-10", [50 + h % 5 for h in range(24)])
    d11 = hourly("2026-10-11", [55 + h % 7 for h in range(24)])
    d12 = hourly("2026-10-12", [52 + h % 3 for h in range(24)])
    base = [report(v, t) for v, t in d10 + d11 + d12[:6]]       # 12 Oct held to 05:20 only
    tz = {"EGLC": dt.timezone.utc}

    def days_of(rows):
        readings = [("EGLC", fo.parse_valid(v), float(t), None) for v, t in rows]
        return [fo.day_row("EGLC", "london", day, d) for (_, day), d in sorted(fo.reduce_daily(readings, tz).items())]

    held_days = days_of(d10 + d11 + d12[:6])
    answer = [("EGLC", v.replace("T", " ").rstrip("Z"), t, "") for v, t in d11 + d12]
    _, daily, _ = setup(tmp_path, monkeypatch, answer, base, held_days)

    s = run()

    night = list(common.read_csv(str(tmp_path / "wx" / "station_daily_nightly" / f"days-{NIGHT}.csv.gz")))
    assert [r["local_date"] for r in night] == ["2026-10-12"], "10 and 11 Oct are recomputed unchanged, so not written"
    assert night[0]["n_reports"] == "24" and night[0]["last_valid"] == "2026-10-12T23:20Z"
    assert s["days_written"] == 1
    merged = {(r["station"], r["local_date"]): r for r in common.station_daily_rows()}
    assert merged[("EGLC", "2026-10-12")]["n_reports"] == "24", "the night's row stands for the day"
    assert merged[("EGLC", "2026-10-11")]["n_reports"] == "24"
    assert len(list(common.read_csv(str(daily)))) == 3, "the file keeps its rows, the partial day's too"
    sdays, refused = bt.load_station_daily()
    assert "2026-10-12" in refused["EGLC"], "the day ends at midnight; the newest report is 23:20"


def test_a_second_run_the_same_night_keeps_the_first_runs_rows(tmp_path, monkeypatch):
    base = [report("2026-10-12T10:20Z", "60.00")]
    setup(tmp_path, monkeypatch, [("EGLC", "2026-10-12 11:20", "61.00", "")], base)
    run()
    monkeypatch.setattr(fo, "request", lambda *a, **k: iem_answer([("EGLC", "2026-10-12 11:20", "61.00", ""),
                                                                   ("EGLC", "2026-10-12 12:20", "62.00", "")]))
    s = run()
    night = list(common.read_csv(str(tmp_path / "wx" / "station_reports_nightly" / f"reports-{NIGHT}.csv.gz")))
    assert [r["valid"] for r in night] == ["2026-10-12T11:20Z", "2026-10-12T12:20Z"]
    assert s["new_reports"] == 1


def test_a_night_asks_for_two_weeks_at_most(tmp_path, monkeypatch):
    _, _, asked = setup(tmp_path, monkeypatch, [], [report("2026-08-01T10:20Z", "60.00")])
    run()
    assert asked[0][1] == dt.date(2026, 10, 13) - dt.timedelta(days=fo.UPDATE_MAX_DAYS)


def test_the_files_alone_read_as_before(tmp_path, monkeypatch):
    """With no night yet, the readers return the file's rows in its order."""
    base = [report("2026-10-12T10:20Z", "60.00"), report("2026-10-12T11:20Z", "61.00")]
    reports, daily, _ = setup(tmp_path, monkeypatch, [], base)
    assert [[r[h] for h in fo.REPORT_HEADER] for r in common.station_report_rows("EGLC")] == base
    assert common.station_daily_rows() == []


def test_the_nightly_workflow_runs_it_before_the_commit():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "archive_observations.yml").read_text())
    steps = wf["jobs"]["archive"]["steps"]
    names = [s.get("name") for s in steps]
    i = next(k for k, s in enumerate(steps) if "fetch_obs.py update" in str(s.get("run", "")))
    assert i < names.index("Commit the mirror")
    assert steps[i]["if"] == "always()" and steps[i]["timeout-minutes"] <= 5


def test_the_run_is_logged_partial_when_a_station_had_no_new_report(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [("EGLC", "2026-10-12 11:20", "61.00", "")], [report("2026-10-12T10:20Z", "60.00")])
    logged = []
    monkeypatch.setattr(fo, "ops_log", lambda: lambda *a: logged.append(a))
    assert fo.main(["update", "--end", NIGHT]) == 0
    assert logged[0][:3] == ("P2.13_station_record", "ok", 1)
    monkeypatch.setattr(common, "event_stations", lambda: {"EGLC": "london", "LFPG": "london"})
    fo.main(["update", "--end", NIGHT])
    assert logged[1][1] == "partial" and logged[1][3]["stations_without_a_new_report"] == ["EGLC", "LFPG"]


def test_without_the_database_keys_nothing_is_logged(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert fo.ops_log() is None
