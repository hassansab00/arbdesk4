"""Shared paths and readers for the WXPredict data tools.

Every source the training table reads is a file in the repository:

  data/training/market_history/   the venue's record (tools/market_history.py)
  data/training/previous_runs/    what the public forecasts said before the day
                                  (scripts/honest_record.py)
  data/training/wxpredict/        the settlement stations' own reports, every
                                  routine and special report with every field
                                  (tools/wxpredict/fetch_obs.py), and each
                                  night's new reports and recomputed station
                                  days beside them (fetch_obs.py update)
  data/mirror/cities/             public.cities as mirrored nightly
"""
import csv
import datetime as dt
import glob
import hashlib
import io
import gzip
import os
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def use_root(root):
    """Point every source path at `root`, a tree laid out like the repository's
    (the repository itself by default; a directory of recorded sources for
    build_table.py --recorded, R47)."""
    global DATA_ROOT, MH, PR, WX, REPORTS, STATION_DAILY, CITIES_GLOB
    DATA_ROOT = root
    MH = os.path.join(root, "data", "training", "market_history")
    PR = os.path.join(root, "data", "training", "previous_runs")
    WX = os.path.join(root, "data", "training", "wxpredict")
    REPORTS = os.path.join(WX, "station_reports")
    STATION_DAILY = os.path.join(WX, "station_daily.csv.gz")
    CITIES_GLOB = os.path.join(root, "data", "mirror", "cities", "cities-*.csv.gz")


use_root(ROOT)

UTC = dt.timezone.utc


def market_nights(kind):
    return sorted(glob.glob(os.path.join(os.path.dirname(MH), "market_history_nightly", kind + "-*.csv.gz")))


def market_rows(kind):
    """The original venue record plus nightly additions; no closed label changes.

    Each read goes through read_csv, so recorded builds name the exact blobs.
    Prices are new series only; market_update never replaces a fetched series.
    """
    paths = [os.path.join(MH, kind + ".csv.gz"), *market_nights(kind)]
    if kind == "prices":
        for path in paths:
            yield from read_csv(path)
        return
    if kind not in ("events", "bands"):
        raise ValueError("unknown market record " + kind)
    rows = {}
    for path in paths:
        for r in read_csv(path):
            key = r["event_id"] if kind == "events" else (r["event_id"], r["band_index"])
            old = rows.get(key)
            if old is not None and old != r:
                mutable = "closed" if kind == "events" else "winner"
                if any(old.get(k) != r.get(k) for k in set(old) | set(r) if k != mutable):
                    raise ValueError("market identity changed in " + path)
                if old[mutable] not in ("", "0" if kind == "events" else ""):
                    raise ValueError("a settled market label changed in " + path)
            rows[key] = r
    yield from (rows[k] for k in sorted(rows))

# A station day is whole when its reports leave no gap over this many hours,
# counting local midnight to the first report and the last report to the next
# midnight (review of #314: one rule wherever a day's maximum is read).
WHOLE_DAY_MAX_GAP_H = 3


# Every source file a build reads, when a build is recording them (R47: the
# table names its inputs by git blob, so it can be rebuilt from exactly them):
# {absolute path: git blob id of the bytes the build itself read}. The id is
# taken from those bytes, never from a later look at the file, and a file whose
# bytes differ the second time a build sees it stops the build (Codex on #369).
READ = None


def blob_sha(data):
    """The git blob id of these bytes, as `git hash-object` gives it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _record(path, data):
    if READ is None:
        return
    p = os.path.abspath(path)
    sha = blob_sha(data)
    if READ.setdefault(p, sha) != sha:
        raise RuntimeError(f"{p} changed while the build was reading its sources")


def note(path):
    """Record a file the build lists without parsing (its bytes, now)."""
    if READ is not None:
        with open(path, "rb") as f:
            _record(path, f.read())


def read_csv(path):
    if READ is None:
        with gzip.open(path, "rt", newline="") as f:
            yield from csv.DictReader(f)
        return
    with open(path, "rb") as f:
        data = f.read()
    _record(path, data)
    with gzip.open(io.BytesIO(data), "rt", newline="") as f:
        yield from csv.DictReader(f)


def sources(recorded):
    """{path relative to the data root: git blob id} from a recording build's READ."""
    return {os.path.relpath(p, DATA_ROOT).replace(os.sep, "/"): sha for p, sha in sorted(recorded.items())}


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


# ---------------------------------------------------------------------------
# the station record: the files plus each night's increments
# ---------------------------------------------------------------------------
# EACH NIGHT ADDS TO IT (12 Oct). Until then the station reports and days were
# refreshed by hand (6 and 11 Oct), so no new day reached the training data on
# its own. fetch_obs.py update writes each night's new reports and its
# recomputed days into a new file of their own; the files below are never
# rewritten by it (the repository keeps every version of a rewritten file).
# The increments sit beside the files they add to (station_reports_nightly
# beside station_reports, station_daily_nightly beside station_daily.csv.gz),
# so pointing REPORTS or STATION_DAILY elsewhere moves them too.
def reports_nightly_dir():
    return os.path.join(os.path.dirname(REPORTS), "station_reports_nightly")


def station_daily_nightly_dir():
    return os.path.join(os.path.dirname(STATION_DAILY), "station_daily_nightly")


def nightly_files(directory, prefix):
    return sorted(glob.glob(os.path.join(directory, f"{prefix}-*.csv.gz")))


def nightly_reports():
    """{station: [report row]} from every night's increment, oldest night first."""
    out = {}
    for path in nightly_files(reports_nightly_dir(), "reports"):
        for r in read_csv(path):
            out.setdefault(r["station"], []).append(r)
    return out


def station_report_rows(station, nightly=None):
    """The station's reports, oldest first: its file in REPORTS, then the
    nights' new reports. A report time already read is kept as first read (the
    nightly fetch adds only times the record does not hold)."""
    nightly = nightly_reports() if nightly is None else nightly
    rows = {}
    path = os.path.join(REPORTS, f"{station}.csv.gz")
    if os.path.exists(path):
        for r in read_csv(path):
            rows.setdefault(r["valid"], r)
    for r in nightly.get(station, []):
        rows.setdefault(r["valid"], r)
    return [rows[k] for k in sorted(rows)]


def station_daily_rows():
    """Every station day, by station and date: STATION_DAILY's row, or the
    newest night's row for the same station and local date (a night recomputes
    a day its new reports touch: a day that was partial becomes whole)."""
    rows = {}
    if os.path.exists(STATION_DAILY):
        for r in read_csv(STATION_DAILY):
            rows[(r["station"], r["local_date"])] = r
    for path in nightly_files(station_daily_nightly_dir(), "days"):
        for r in read_csv(path):
            rows[(r["station"], r["local_date"])] = r
    return [rows[k] for k in sorted(rows)]


_OPS = None


def ops():
    """scripts/common.py, the database helpers (rest_all, upsert, log_run),
    loaded once by path: a script run from tools/wxpredict has this directory's
    common.py first on its path. Loading it also makes a crash leave its row
    (crash:<script>)."""
    global _OPS
    if _OPS is None:
        import importlib.util
        import sys
        canonical = sys.modules.get('common')
        if canonical is not None and os.path.realpath(getattr(canonical, '__file__', '')) == os.path.realpath(os.path.join(ROOT, 'scripts', 'common.py')):
            _OPS = canonical
            return _OPS
        spec = importlib.util.spec_from_file_location("ops_common", os.path.join(ROOT, "scripts", "common.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _OPS = mod
    return _OPS


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
    for r in market_rows("events"):
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
