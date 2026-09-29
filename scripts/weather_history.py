"""Forecast history the database no longer holds (plan v2 P1.6 phase 2, step 2).

The archive prunes `weather_forecasts` by for_date, after the rows are
exported to data/archive/forecasts, verified and committed. From then on the
database cannot answer a read that reaches back past the cut, and three
Python jobs do reach back: measure_skill.py (every verified outcome, the
width the desk prices with), station_correction.py (75 days of
`weather_forecast_models`, the centre it prices with) and regime.py (the
tick's history of forecast disagreement; the backtest runner reads through
it). Each got whatever retention left, 60 days lately, and phase 2 lowers
the keep to 30.

read() answers the read a job already makes: the database's rows, plus
the rows the archive holds for the dates the database was cut below. When the
read does not reach below the cut it IS the database call, unchanged, so
nothing moves until a job reaches past the cut.

  * The cut is the newest prune the archive logged (`ingest_log`, job
    archive_<dataset>, status ok, `archived_through` = the first for_date
    kept). No record: nothing was ever pruned, and the database is all there
    is.
  * That prune's file must be in this checkout, or the read refuses: a job
    that checked out before tonight's archive commit and read after its prune
    would otherwise price on a history with a day missing and not know it.
  * A key the database still holds (city_key, model, run_at, for_date) wins
    over the archive's copy of it.
  * v_forecast_issued's columns are computed for archived rows exactly as the
    view computes them (issued_at, issued_at_source, issued_local_date,
    issued_lead_days, same_day_issue).
  * Values come back as the REST API returns them: the archive stored each
    one as str() of what rest() returned (dicts and lists as JSON), so
    json.loads gives the same Python value back.

tools/p16_reader_proof.py proves it against the live database.
"""
import ast
import csv
import datetime as dt
import glob
import gzip
import json
import os
import re
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = ("city_key", "model", "run_at", "for_date")

# read name -> the table it comes from, the archive dataset its pruned rows
# went to (archive_observations.TABLES), and whether v_forecast_issued's
# columns are computed on top.
SOURCES = {
    "weather_forecasts": {"table": "weather_forecasts", "dataset": "forecasts", "issued": False},
    "v_forecast_issued": {"table": "weather_forecasts", "dataset": "forecasts", "issued": True},
    "weather_forecast_models": {"table": "weather_forecast_models", "dataset": "forecast_models",
                                "issued": False},
}
COLUMNS = {
    "weather_forecasts": ("forecast_id", "city_key", "model", "run_at", "observed_at", "for_date",
                          "lead_days", "forecast_max_c", "variables", "source"),
    "weather_forecast_models": ("city_key", "model", "run_at", "for_date", "lead_days",
                                "forecast_max_c", "source", "observed_at"),
}
ISSUED = ("issued_at", "issued_at_source", "issued_local_date", "issued_lead_days", "same_day_issue")
TIMESTAMPS = {"run_at", "observed_at", "issued_at"}
DATES = {"for_date", "issued_local_date"}
# The one source whose run_at is not when it was known: v_forecast_issued.
PREVIOUS_RUNS = "open-meteo-previous-runs"
ISSUED_AT_SOURCE = {"api.weather.gov": "provider_update_time", "open-meteo": "ingest_time",
                    PREVIOUS_RUNS: "ingest_time_true_issue_unverified"}

_boundary = {}
_files = {}
_zones = {}


class StaleCheckout(RuntimeError):
    """The database was pruned past what this checkout's archive holds."""


# --------------------------------------------------------------------------
# where the database was cut
# --------------------------------------------------------------------------

def _first(rest_fn, rest_all_fn, path, params, order):
    """The first row of a read, with whichever function the job reads with."""
    if rest_fn is not None:
        return rest_fn(path, list(params) + [("order", order), ("limit", "1")])[:1]
    return rest_all_fn(path, list(params), order=order, page_size=1000)[:1]


def prune_boundary(dataset, rest_fn=None, rest_all_fn=None):
    """{'before': 'YYYY-MM-DD', 'file': path} from the newest logged prune of
    `dataset`, or None if it has never been pruned (nothing deletes from
    ingest_log). Once per process: the clock starts the archive and the tick
    together at :36 and the tick is done before the prune (~02:40Z); no
    pipeline runs across it."""
    if dataset not in _boundary:
        rows = _first(rest_fn, rest_all_fn, "ingest_log", [
            ("select", "finished_at,detail"), ("job", f"eq.archive_{dataset}"),
            ("status", "eq.ok"), ("detail->>archived_through", "not.is.null")], "finished_at.desc")
        if rows:
            d = rows[0]["detail"]
            _boundary[dataset] = {"before": str(d["archived_through"])[:10], "file": d.get("file")}
        else:
            _boundary[dataset] = None
    return _boundary[dataset]


def check_checkout(cut, root=ROOT):
    path = cut.get("file")
    if not path or not os.path.exists(os.path.join(root, path)):
        raise StaleCheckout(
            f"the database was pruned below {cut['before']} into {path}, which this checkout "
            f"does not have. Reading on would leave those days out without saying so.")


# --------------------------------------------------------------------------
# the archive's rows
# --------------------------------------------------------------------------

_NAME = re.compile(r"-(\d{4}-\d{2}-\d{2})-to-(\d{4}-\d{2}-\d{2})\.csv\.gz$")


def _value(text):
    """A CSV cell back to the value rest() returned before it was written."""
    if text is None or text == "":
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    if text[:1] in "{[":
        # archive files written before _cell existed hold a Python repr
        return ast.literal_eval(text)
    return text


def _typed(name, text):
    if name in ("city_key", "model", "source", "run_at", "observed_at", "for_date"):
        return text if text != "" else None
    return _value(text)


def archive_files(dataset, root=ROOT):
    """[(from, to, path)] for every file of the dataset, by the for_date range
    in its name."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, "data", "archive", dataset, "*.csv.gz"))):
        m = _NAME.search(os.path.basename(path))
        if m:
            out.append((m.group(1), m.group(2), path))
    return out


def file_rows(path):
    """Every row of one archive file, typed. Cached per process."""
    if path not in _files:
        with gzip.open(path, "rt", newline="") as fh:
            _files[path] = [{k: _typed(k, v) for k, v in r.items()} for r in csv.DictReader(fh)]
    return _files[path]


def _by_city(path):
    key = ("by_city", path)
    if key not in _files:
        index = {}
        for r in file_rows(path):
            index.setdefault(r["city_key"], []).append(r)
        _files[key] = index
    return _files[key]


def archived(dataset, lo, before, root=ROOT, city=None):
    """The archive's rows with lo <= for_date < before (lo None: from the
    start), for one city when `city` is given. The tick asks once per city,
    so the rows are indexed by city rather than scanned each time."""
    out = []
    for f, t, path in archive_files(dataset, root):
        if t < (lo or "0000-00-00") or f >= before:
            continue
        rows = file_rows(path) if city is None else _by_city(path).get(city, [])
        out.extend(r for r in rows if (lo is None or r["for_date"] >= lo) and r["for_date"] < before)
    return out


# --------------------------------------------------------------------------
# v_forecast_issued, for rows the view can no longer see
# --------------------------------------------------------------------------

def _ts(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _pg_timestamp(t):
    """A UTC instant as PostgREST writes a timestamptz (trailing zeros of the
    fraction dropped)."""
    t = t.astimezone(dt.timezone.utc)
    s = t.strftime("%Y-%m-%dT%H:%M:%S")
    if t.microsecond:
        s += (".%06d" % t.microsecond).rstrip("0")
    return s + "+00:00"


def with_issued(row, timezone):
    """row plus v_forecast_issued's five columns, as the view computes them."""
    source = row.get("source")
    known = source != PREVIOUS_RUNS
    at = row.get("observed_at") if source == PREVIOUS_RUNS else row.get("run_at")
    out = dict(row)
    out["issued_at"] = _pg_timestamp(_ts(at)) if at else None
    out["issued_at_source"] = ISSUED_AT_SOURCE.get(source, "run_at_unclassified")
    local = None
    if known and at and timezone:
        local = _ts(at).astimezone(ZoneInfo(timezone)).date()
    out["issued_local_date"] = local.isoformat() if local else None
    for_date = dt.date.fromisoformat(row["for_date"])
    out["issued_lead_days"] = (for_date - local).days if local else None
    out["same_day_issue"] = (local >= for_date) if local else None
    return out


def _timezones(rest_fn, rest_all_fn):
    if not _zones:
        rows = (rest_fn("cities", [("select", "city_key,timezone")]) if rest_fn is not None else
                rest_all_fn("cities", [("select", "city_key,timezone")], order="city_key.asc", page_size=1000))
        for r in rows:
            _zones[r["city_key"]] = r.get("timezone")
    return _zones


def reset():
    """Forget what this process looked up (tests)."""
    _boundary.clear()
    _files.clear()
    _zones.clear()


# --------------------------------------------------------------------------
# the filters and orders the jobs use, applied to archived rows
# --------------------------------------------------------------------------

def _compare_value(col, v):
    if v is None:
        return None
    if col in TIMESTAMPS:
        return _ts(v) if isinstance(v, str) else v
    return v


def _literal(col, text):
    if col in TIMESTAMPS:
        return _ts(text)
    if col in DATES or col in ("city_key", "model", "source", "issued_at_source"):
        return text
    if text in ("true", "false"):
        return text == "true"
    return json.loads(text)


def parse_filters(params):
    """(select, [(col, op, value)], order, limit) from a PostgREST param list."""
    select, filters, order, limit = None, [], None, None
    for k, v in (params.items() if isinstance(params, dict) else params):
        if k == "select":
            select = [c.strip() for c in v.split(",")]
        elif k == "order":
            order = v
        elif k == "limit":
            limit = int(v)
        elif k == "offset":
            raise ValueError("weather_history: offset is not supported")
        else:
            if v in ("not.is.null", "is.null"):
                filters.append((k, v, None))
                continue
            op, _, arg = v.partition(".")
            if op == "in":
                vals = arg.strip("()").split(",")
                filters.append((k, "in", [_literal(k, x.strip('"')) for x in vals]))
            elif op in ("eq", "gt", "gte", "lt", "lte"):
                filters.append((k, op, _literal(k, arg)))
            else:
                raise ValueError(f"weather_history: filter {k}={v} is not supported")
    return select, filters, order, limit


def matches(row, filters):
    for col, op, want in filters:
        v = _compare_value(col, row.get(col))
        if op == "not.is.null":
            ok = v is not None
        elif op == "is.null":
            ok = v is None
        elif v is None:
            ok = False
        elif op == "eq":
            ok = v == want
        elif op == "in":
            ok = v in want
        elif op == "gt":
            ok = v > want
        elif op == "gte":
            ok = v >= want
        elif op == "lt":
            ok = v < want
        else:
            ok = v <= want
        if not ok:
            return False
    return True


def lowest_date(filters):
    """The earliest for_date the read can return, or None when unbounded."""
    lows = []
    for col, op, want in filters:
        if col != "for_date":
            continue
        if op in ("eq", "gte"):
            lows.append(want)
        elif op == "gt":
            lows.append((dt.date.fromisoformat(want) + dt.timedelta(days=1)).isoformat())
        elif op == "in":
            lows.append(min(want))
    return max(lows) if lows else None


def text_key(s):
    """Postgres en_US.UTF-8 order for the identifiers these tables hold:
    punctuation is ignored first and only breaks ties (tests pin it against
    the database's own order)."""
    return (re.sub(r"[^0-9a-z]", "", s.lower()), s)


def _sort_key(col, v):
    if v is None:
        return None
    if col in TIMESTAMPS:
        return _ts(v)
    if isinstance(v, str) and col not in DATES:
        return text_key(v)
    return v


def sort_rows(rows, order):
    """Postgres order: ascending puts NULLs last, descending first."""
    for term in reversed([t.strip() for t in (order or "").split(",") if t.strip()]):
        col, _, direction = term.partition(".")
        desc = direction.startswith("desc")
        present = [r for r in rows if r.get(col) is not None]
        absent = [r for r in rows if r.get(col) is None]
        present.sort(key=lambda r: _sort_key(col, r[col]), reverse=desc)
        rows = absent + present if desc else present + absent
    return rows


# --------------------------------------------------------------------------
# the read
# --------------------------------------------------------------------------

def read(name, params, *, rest_fn=None, rest_all_fn=None, order=None, page_size=1000, root=ROOT):
    """What `rest_all_fn(name, params, order=, page_size=)` returns - or, with
    rest_all_fn None, `rest_fn(name, params)` - as if the database had never
    been pruned."""
    if rest_fn is None and rest_all_fn is None:
        raise ValueError("weather_history.read needs rest_fn or rest_all_fn")
    spec = SOURCES[name]
    params = list(params.items()) if isinstance(params, dict) else list(params)

    def db(p):
        if rest_all_fn is not None:
            return rest_all_fn(name, p, order=order, page_size=page_size)
        return rest_fn(name, p)

    select, filters, order_in_params, limit = parse_filters(params)
    cut = prune_boundary(spec["dataset"], rest_fn, rest_all_fn)
    lo = lowest_date(filters)
    if cut is None or (lo is not None and lo >= cut["before"]):
        return db(params)                                 # the read it replaces

    check_checkout(cut, root)
    # The database's rows, carrying the key so a row it still holds wins.
    missing = [c for c in KEY if select is not None and c not in select]
    db_params = ([(k, v) for k, v in params if k != "select"] + [("select", ",".join(select + missing))]
                 if missing else params)
    rows = db(db_params)
    have = {(r["city_key"], r["model"], _ts(r["run_at"]), r["for_date"]) for r in rows}

    zones = _timezones(rest_fn, rest_all_fn) if spec["issued"] else None
    city = next((w for c, op, w in filters if c == "city_key" and op == "eq"), None)
    for r in archived(spec["dataset"], lo, cut["before"], root, city=city):
        if (r["city_key"], r["model"], _ts(r["run_at"]), r["for_date"]) in have:
            continue
        full = {c: r.get(c) for c in COLUMNS[spec["table"]]}
        if spec["issued"]:
            full = with_issued(full, zones.get(r["city_key"]))
        if matches(full, filters):
            rows.append(full)

    rows = sort_rows(rows, order or order_in_params)
    if limit is not None:
        rows = rows[:limit]
    if select is not None:
        rows = [{c: r.get(c) for c in select} for r in rows]
    return rows
