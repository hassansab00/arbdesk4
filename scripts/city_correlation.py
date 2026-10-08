"""derived_city_correlation from the database's recent rows and the repository's
older ones (Fresh Supabase, part 2a; Hassan, 8 Oct: "offload Supabase
completely daily to the repo, keep it fresh each run day").

recompute_correlation (sql/ad4_capacity_correlation.sql) did this in SQL, over whatever the two
weather tables held:

    obs_daily  each city's highest reading per UTC day (valid_at::date in a
               UTC session), over every reading of the last 180 days - a day
               whose readings all lack a temperature is a day with no maximum
    errs       every lead-1 forecast with a maximum, minus that day's maximum
    pairs      for each two cities (a < b in the database's order), every
               pairing of their errors on the same date: corr() over the
               pairings where both are known, n_days = every pairing, kept
               when n_days >= 20

The 30-day forecast keep made that the dates from today-30 on: weather_forecasts
held nothing older, and the readings reached 32 days back. Once the two keeps
fall to days, the SQL would quietly correlate on two days. So this is the same
computation over the same dates, read through the archive: the forecasts
through weather_history (the database's rows, the archive's below the cut),
the UTC-day maxima from v_city_utc_day_max and, below the readings' prune,
data/archive/observations. Each refuses if the newest prune's file is not in
the checkout.

corr() is float8 arithmetic: the same Youngs-Cramer accumulation Postgres uses
(float8_regr_accum), and the result stored as float8 casts to numeric, 15
significant digits. Postgres adds the pairings in whatever order its join
produces; the last digit can differ.

The window and the minimum are recompute_correlation's own; it asked for 180
days and only ever had 31. Reading more is a separate decision.
"""
import datetime as dt
import math
import os
import sys
from decimal import Decimal

import weather_history as wh

LEAD_DAYS = 1
# for_date >= today - 30: what the 30-day forecast keep left the SQL to read.
WINDOW_DAYS = 30
# `having count(*) >= 20` - the SQL's "provisional minimum sample". It counts
# pairings, not days.
MIN_PAIRINGS = 20
OBSERVATIONS = "observations"


# --------------------------------------------------------------------------
# the inputs
# --------------------------------------------------------------------------

def observation_cut(rest_all_fn):
    """{'before': the instant, 'file': path} of the newest logged prune of the
    readings, or None. Their cut is an instant, not a date."""
    rows = wh._first(None, rest_all_fn, "ingest_log", [
        ("select", "finished_at,detail"), ("job", f"eq.archive_{OBSERVATIONS}"),
        ("status", "eq.ok"), ("detail->>archived_through", "not.is.null")], "finished_at.desc")
    if not rows:
        return None
    d = rows[0]["detail"]
    return {"before": str(d["archived_through"]), "file": d.get("file")}


def _merge(out, key, value):
    """A day exists once any reading does; its maximum is over the readings
    with a temperature. Reading a day twice (a file still in the database)
    changes neither."""
    if key not in out:
        out[key] = value
    elif value is not None and (out[key] is None or value > out[key]):
        out[key] = value


def utc_day_maxima(rest_all_fn, lo, root=None):
    """{(city_key, 'YYYY-MM-DD'): highest temp_c or None} for UTC days >= lo."""
    root = root or wh.ROOT
    out = {}
    for r in rest_all_fn("v_city_utc_day_max", [
            ("select", "city_key,utc_date,max_c"), ("utc_date", f"gte.{lo}")],
            order="city_key.asc,utc_date.asc", page_size=1000):
        _merge(out, (r["city_key"], str(r["utc_date"])[:10]), r["max_c"])

    cut = observation_cut(rest_all_fn)
    if cut is None or cut["before"][:10] < lo:
        return out                       # the prune never reached the window
    wh.check_checkout(cut, root)
    before = wh._ts(cut["before"])
    for _f, to, path in wh.archive_files(OBSERVATIONS, root):
        if to < lo:
            continue
        for r in wh.file_rows(path):
            at = wh._ts(r["valid_at"]).astimezone(dt.timezone.utc)
            day = at.date().isoformat()
            if day < lo or at >= before:
                continue
            _merge(out, (r["city_key"], day), r.get("temp_c"))
    return out


def lead_forecasts(rest_all_fn, lo, root=None):
    """Every lead-1 forecast with a maximum, for_date >= lo: the database's,
    and the archive's below the forecasts' cut."""
    rows = wh.read("weather_forecasts", [
        ("select", "city_key,for_date,forecast_max_c"),
        ("lead_days", f"eq.{LEAD_DAYS}"),
        ("for_date", f"gte.{lo}"),
    ], rest_all_fn=rest_all_fn, order="city_key.asc,for_date.asc,model.asc,run_at.asc", root=root)
    return [r for r in rows if r.get("forecast_max_c") is not None]


# --------------------------------------------------------------------------
# the computation
# --------------------------------------------------------------------------

class _Corr:
    """Postgres's corr(Y, X): float8_regr_accum, then float8_corr."""
    __slots__ = ("n", "sx", "sy", "sxx", "syy", "sxy", "pairings")

    def __init__(self):
        self.n = self.sx = self.sy = self.sxx = self.syy = self.sxy = 0.0
        self.pairings = 0

    def add(self, y, x):
        self.pairings += 1               # count(*): every pairing
        if y is None or x is None:       # corr() skips a null
            return
        old = self.n
        self.n += 1.0
        self.sx += x
        self.sy += y
        if old > 0.0:
            tx = x * self.n - self.sx
            ty = y * self.n - self.sy
            scale = 1.0 / (self.n * old)
            self.sxx += tx * tx * scale
            self.syy += ty * ty * scale
            self.sxy += tx * ty * scale

    def value(self):
        if self.n < 1.0 or self.sxx == 0.0 or self.syy == 0.0:
            return None
        return self.sxy / math.sqrt(self.sxx * self.syy)


def errors_by_date(forecasts, maxima):
    """{for_date: {city: [error or None, ...]}}: each forecast minus its UTC
    day's maximum, where the readings hold that day at all."""
    out = {}
    for f in forecasts:
        key = (f["city_key"], str(f["for_date"])[:10])
        if key not in maxima:
            continue
        observed = maxima[key]
        err = (None if observed is None
               else float(Decimal(str(f["forecast_max_c"])) - Decimal(str(observed))))
        out.setdefault(key[1], {}).setdefault(key[0], []).append(err)
    return out


def pairs(errors):
    """[(city_a, city_b, n_days, err_corr float or None)] with n_days >= 20."""
    acc = {}
    for day in sorted(errors):
        by_city = errors[day]
        cities = sorted(by_city, key=wh.text_key)
        for i, a in enumerate(cities):
            for b in cities[i + 1:]:
                c = acc.setdefault((a, b), _Corr())
                for ea in by_city[a]:
                    for eb in by_city[b]:
                        c.add(ea, eb)            # corr(a.err, b.err)
    return [(a, b, c.pairings, c.value())
            for (a, b), c in sorted(acc.items(), key=lambda kv: (wh.text_key(kv[0][0]), wh.text_key(kv[0][1])))
            if c.pairings >= MIN_PAIRINGS]


def as_numeric(v):
    """A float8 as Postgres casts it to numeric: 15 significant digits."""
    return None if v is None else str(Decimal(format(v, ".15g")))


def compute(rest_all_fn, today=None, root=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    lo = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    maxima = utc_day_maxima(rest_all_fn, lo, root)
    errors = errors_by_date(lead_forecasts(rest_all_fn, lo, root), maxima)
    return pairs(errors)


def recompute(rest_all_fn=None, insert_fn=None, today=None, root=None, now=None):
    """Compute and append one set at one instant, as one request: the readers
    take the rows at the newest computed_at, so a part-written set must not
    exist. Returns the rows written, as recompute_correlation did."""
    if rest_all_fn is None or insert_fn is None:
        from common import insert, rest_all
        rest_all_fn = rest_all_fn or rest_all
        insert_fn = insert_fn or insert
    rows = compute(rest_all_fn, today, root)
    at = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    payload = [{"city_a": a, "city_b": b, "computed_at": at, "n_days": n, "err_corr": as_numeric(v)}
               for a, b, n, v in rows]
    if payload:
        insert_fn("derived_city_correlation", payload, chunk=len(payload))
    return len(payload)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    print(f"derived_city_correlation: {recompute()} rows")
