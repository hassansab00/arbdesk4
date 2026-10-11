"""fetch_obs.py daily rebuilds station_daily.csv.gz from the stations of the
active cities. A city that left the list (Zhengzhou, ZHCC, on 11 Oct) is not
fetched, and rewriting the file from that run alone dropped its 2,470 days.
Its rows are carried over unchanged now (Hassan's rule: never delete data)."""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import common  # noqa: E402
from wxpredict import fetch_obs as fo  # noqa: E402


def _report_row(valid, tmpf):
    row = {h: "" for h in ("valid", "tmpf", "t10_c")}
    row.update(valid=valid, tmpf=tmpf)
    return row


def test_a_station_not_asked_keeps_its_days(tmp_path, monkeypatch):
    reports = tmp_path / "station_reports"
    daily = tmp_path / "station_daily.csv.gz"
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(common, "REPORTS", str(reports))
    monkeypatch.setattr(common, "STATION_DAILY", str(daily))
    monkeypatch.setattr(common, "event_stations", lambda: {"EGLC": "london"})
    monkeypatch.setattr(common, "cities", lambda: {"london": {"timezone": "UTC"}})
    head = ["valid", "tmpf", "t10_c"]
    common.write_csv(str(reports / "EGLC.csv.gz"), head,
                     [["2026-10-05T00:20Z", "50.00", ""], ["2026-10-05T12:20Z", "60.80", ""]])
    gone_city = ["ZHCC", "zhengzhou", "2026-10-04", "24", "2026-10-03T16:00Z", "2026-10-04T15:00Z",
                 "80.60", "27.00", "2026-10-04T06:00Z", "64.40", "18.00", "1.00"]
    stale_london = ["EGLC", "london", "2026-10-05", "1", "2026-10-05T00:20Z", "2026-10-05T00:20Z",
                    "50.00", "10.00", "2026-10-05T00:20Z", "50.00", "10.00", "23.33"]
    common.write_csv(str(daily), fo.DAILY_HEADER, [stale_london, gone_city])

    fo.cmd_daily(argparse.Namespace(cache=str(cache)))

    rows = [[r[h] for h in fo.DAILY_HEADER] for r in common.read_csv(str(daily))]
    assert gone_city in rows, "a station not asked keeps its days, unchanged"
    london = [r for r in rows if r[0] == "EGLC"]
    assert len(london) == 1 and london[0][3] == "2" and london[0][7] == "16.00", \
        "an asked station's days come from this run"
    assert [r[0] for r in rows] == ["EGLC", "ZHCC"], "sorted by station"
