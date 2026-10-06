"""Shared paths and readers for the WXPredict data tools.

Every source the training table reads is a file in the repository:

  data/training/market_history/   the venue's record (tools/market_history.py)
  data/training/previous_runs/    what the public forecasts said before the day
                                  (scripts/honest_record.py)
  data/training/wxpredict/        the settlement stations' own reports, every
                                  routine and special report with every field
                                  (tools/wxpredict/fetch_obs.py)
  data/mirror/cities/             public.cities as mirrored nightly
"""
import csv
import datetime as dt
import glob
import io
import gzip
import os
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MH = os.path.join(ROOT, "data", "training", "market_history")
PR = os.path.join(ROOT, "data", "training", "previous_runs")
WX = os.path.join(ROOT, "data", "training", "wxpredict")
REPORTS = os.path.join(WX, "station_reports")
STATION_DAILY = os.path.join(WX, "station_daily.csv.gz")
CITIES_GLOB = os.path.join(ROOT, "data", "mirror", "cities", "cities-*.csv.gz")

UTC = dt.timezone.utc

# A station day is whole when its reports leave no gap over this many hours,
# counting local midnight to the first report and the last report to the next
# midnight (review of #314: one rule wherever a day's maximum is read).
WHOLE_DAY_MAX_GAP_H = 3


def read_csv(path):
    with gzip.open(path, "rt", newline="") as f:
        yield from csv.DictReader(f)


def write_csv(path, header, rows):
    """Write rows to a gzip CSV deterministically (mtime 0), so rebuilding the
    same rows gives the same bytes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    n = 0
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
            text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
            w = csv.writer(text, lineterminator="\n")
            w.writerow(header)
            for r in rows:
                w.writerow(r)
                n += 1
            text.flush()
            text.detach()
    os.replace(tmp, path)
    return n


def cities():
    """{city_key: row} from the newest mirror of public.cities."""
    path = sorted(glob.glob(CITIES_GLOB))[-1]
    return {r["city_key"]: r for r in read_csv(path)}


def active_cities():
    return {k: c for k, c in cities().items() if (c.get("status") or "active") == "active"}


def zone(city):
    return ZoneInfo(city["timezone"] or "UTC")


def iem_id(icao):
    """IEM answers a US station under its three-letter id (KLGA -> LGA);
    scripts/ingest_observations.station_map documents the trap."""
    icao = icao.upper()
    return icao[1:] if len(icao) == 4 and icao.startswith("K") else icao


def event_stations():
    """{icao: city_key} for every settlement station an active city's events
    name, plus each active city's station today."""
    act = active_cities()
    out = {}
    for r in read_csv(os.path.join(MH, "events.csv.gz")):
        if r["city_key"] in act and r["station_icao"]:
            out[r["station_icao"].upper()] = r["city_key"]
    for k, c in act.items():
        if c.get("icao"):
            out[c["icao"].upper()] = k
    return dict(sorted(out.items()))


def local_day_bounds(day, tz):
    """[start, end) of a local calendar day, as unix seconds."""
    start = dt.datetime.combine(day, dt.time(0), tz)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tz)
    return int(start.timestamp()), int(end.timestamp())


def max_gap_h(times, d0, d1):
    """The longest stretch of the local day [d0, d1) (unix seconds) with no
    report, in hours: local midnight to the first report, report to report,
    and the last report to the next midnight. A day with no report is one gap
    as long as the day. `times` are the day's report instants, any order."""
    edges = [d0, *sorted(times), d1]
    return max(b - a for a, b in zip(edges, edges[1:])) / 3600
