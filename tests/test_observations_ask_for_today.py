"""The observation ingest asked for every day except the one being traded.

IEM's ASOS service treats `day2` as EXCLUSIVE. The job computed
`end = today`, so it requested [start, today) - yesterday and before, never
now.

This is the most expensive kind of bug to find, because it self-heals. The
next morning's run asks for a window that ends after today, so today's rows
do land; every history anyone inspects afterwards is complete and correct.
It is only wrong in the present, which is the only time the desk trades.

MEASURED ON THE LIVE TABLE, 21 Sep, over seven days of rows:

    IEM    7,830 rows - every single one written the day AFTER it describes
    NWS   22,482 rows written the same day, +1,217 across midnight

Zero IEM rows at a write lag of zero, ever. Upstream latency would give a
spread; a window that stops at midnight gives exactly this. The two runs that
day settle it: 04:45 returned readings up to 23:58 the previous night, 4.8
hours behind real time, and the 12:40 run eight hours later still stopped at
that same wall. IEM had the data. It was never asked for it.

The cost fell entirely on the 37 cities with no api.weather.gov feed:
observations averaging 14.7 hours old, worst 25.7, at 10 readings a day
against 291 for the 11 NWS cities - on DAILY HIGH markets, where not seeing
the intraday peak is not seeing the thing being traded.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_observations as io_obs  # noqa: E402


TODAY = dt.date(2026, 9, 21)


def test_the_window_covers_today():
    """The whole bug, in one assertion: `day2` is exclusive, so it has to be
    strictly after today or today's readings are outside the request."""
    start, end = io_obs.window(2, today=TODAY)
    assert end > TODAY, (
        f"the window ends at {end} and day2 is exclusive, so the {TODAY} readings "
        "are never requested - which is the bug this file is about"
    )


def test_the_window_still_reaches_back_the_days_it_was_asked_for():
    start, end = io_obs.window(2, today=TODAY)
    assert start == dt.date(2026, 9, 19)
    assert (end - start).days == 3, "two days back plus today is three days of coverage"


def test_a_backfill_ends_today_not_yesterday():
    """`ingest_observations.py 365` is the backfill path. It must not stop one
    day short of the present either."""
    start, end = io_obs.window(365, today=TODAY)
    assert start == TODAY - dt.timedelta(days=365)
    assert end == TODAY + dt.timedelta(days=1)


def test_the_request_sent_to_iem_carries_that_window(monkeypatch):
    """Not the computed dates - the ones that actually go on the wire."""
    sent = {}

    class FakeResponse:
        text = ""

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, timeout=None):
        sent.update(params)
        return FakeResponse()

    monkeypatch.setattr(io_obs.requests, "get", fake_get)
    start, end = io_obs.window(2, today=TODAY)
    io_obs.fetch_station("EGLC", start, end)

    asked_end = dt.date(sent["year2"], sent["month2"], sent["day2"])
    assert asked_end > TODAY, (
        f"the request sent day2={asked_end}, so IEM returns nothing for {TODAY}"
    )
    assert dt.date(sent["year1"], sent["month1"], sent["day1"]) == dt.date(2026, 9, 19)


def test_the_window_rolls_over_a_month_end():
    start, end = io_obs.window(2, today=dt.date(2026, 9, 30))
    assert end == dt.date(2026, 10, 1), "the day after the 30th is not the 31st"


def test_the_window_rolls_over_a_year_end():
    start, end = io_obs.window(1, today=dt.date(2026, 12, 31))
    assert end == dt.date(2027, 1, 1)
    assert start == dt.date(2026, 12, 30)
