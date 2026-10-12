"""The focus-station poller (WXPredict WP4; Hassan, 11 Oct: "yes add the
poller"): its schedule covers every seeded station's local window, it is in
the database suite, and the nightly mirror copies what it records."""
import datetime as dt
import json
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mirror_to_repo as mirror  # noqa: E402

MIG = (ROOT / "supabase" / "migrations" / "20261011180000_reports_the_minute_they_arrive.sql").read_text()


def _cron_hours():
    m = re.search(r"cron\.schedule\('ad4_focus_obs', '\* (\d+)-(\d+) \* \* \*', 'select public\.focus_obs_poll\(\)'\)", MIG)
    assert m, "the poll runs every minute within a range of UTC hours"
    return set(range(int(m.group(1)), int(m.group(2)) + 1))


def test_the_schedule_covers_every_seeded_station_and_reads_the_last_answers():
    hours = _cron_hours()
    seeds = re.findall(r"\('([A-Z]{4})', '(\w+)', '([\w/]+)', (\d+), (\d+), '", MIG)
    assert {s[0] for s in seeds} == {"OEJN", "LTFM"}
    for icao, _city, tz, lo, hi in seeds:
        for day in (dt.date(2026, 1, 15), dt.date(2026, 7, 15)):          # either side of any clock change
            for local_h in range(int(lo), int(hi)):
                utc_h = dt.datetime.combine(day, dt.time(local_h), ZoneInfo(tz)).astimezone(dt.timezone.utc).hour
                assert utc_h in hours, f"{icao} {local_h}:00 local is {utc_h}:00 UTC, not polled"
            last = dt.datetime.combine(day, dt.time(int(hi) - 1, 59), ZoneInfo(tz)).astimezone(dt.timezone.utc)
            assert (last + dt.timedelta(minutes=1)).hour in hours, \
                "a run after the window closes reads the last minute's answer (pg_net keeps answers 6 hours)"
    assert "cron.schedule('ad4_focus_obs_day', '55 23 * * *', 'select public.focus_obs_day()')" in MIG


def test_it_is_in_the_database_suite():
    pkg = json.loads((ROOT / "tests" / "database" / "package.json").read_text())
    assert "node focus-obs.cjs" in pkg["scripts"]["test"]


def test_the_mirror_copies_its_records():
    assert mirror.TABLES["focus_obs_reports"]["kind"] == "append"
    assert mirror.TABLES["focus_obs_reports"]["time"] == "first_seen_at"
    assert mirror.TABLES["focus_obs_polls"]["time"] == "asked_at"
    assert mirror.TABLES["focus_obs_stations"]["kind"] == "snapshot"


BOOKS = (ROOT / "supabase" / "migrations" / "20261012170000_books_the_minute_reports_arrive.sql").read_text()
BOOKS2 = (ROOT / "supabase" / "migrations" / "20261012180000_a_books_poll_that_fails_is_counted.sql").read_text()


def test_the_books_ride_the_reports_minute_and_add_no_schedule():
    """WP5: the focus cities' books are asked in the same minute as the
    reports, by the same poll; no cron job and no workflow is added (rule 7,
    and the poll's switch stops both). The same in the poll's newest form
    (Codex on #389)."""
    for mig in (BOOKS, BOOKS2):
        assert "cron.schedule" not in mig
        poll = mig[mig.index("create or replace function public.focus_obs_poll"):]
        poll = poll[:poll.index("$fo$;")]
        assert poll.index("aviationweather.gov") < poll.index("focus_books_ask(v_ids)"), "the reports first"
        assert "switched ' || v_mode" in poll, "the Workflows switch stops both"


def test_the_books_are_in_the_database_suite():
    pkg = json.loads((ROOT / "tests" / "database" / "package.json").read_text())
    assert "node focus-books.cjs" in pkg["scripts"]["test"]
