"""WXPredict: every feature of one decision, from what was known at it.

One implementation of the features (plan, phase 2.3), so that the training
table and the live reader compute each one the same way. Every function here
takes what it reads as arguments - one station's reports (`Reports`), its whole
days, the daily and hourly forecasts, a ladder's price series - and reads no
file, database or network; tools/wxpredict/build_table.py loads the committed
sources into these shapes, and the live reader is to load the live sources into
the same shapes. What a row may know at its decision time is set out in
build_table.py's docstring and enforced here (REPORT_LAG_S, PUBLISH_H,
SNAPSHOT_S, MARKET_MAX_AGE_S).
"""
import bisect
import calendar
import collections
import datetime as dt
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common  # noqa: E402

# A report valid at v is used from v + REPORT_LAG_S. Measured 5 Oct 2026
# against IEM's own service, polled once a minute (docs/WXPREDICT.md, "When a
# report is known"): reports appeared 8-16 min after their valid time, most
# ~11-12 min (IEM loads them in batches). 20 min covers the slowest seen with
# a margin. The platform's own hourly ingest adds a median 38 min on top
# (weather_observations, 7 days to 5 Oct): phase 2 reads IEM at the tick.
REPORT_LAG_S = 20 * 60
PUBLISH_H = 7                  # P2.9's allowance: a run is out within 7 h of its start
SNAPSHOT_S = 60                # the decision instant: the hour's market snapshot (stamped 0-59 s past)
DAY_HOURS = tuple(range(24))   # decision hours on D
EVE_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)   # decision hours on D-1
MARKET_MAX_AGE_S = 3 * 3600    # a bucket's last price older than this is not a price
PRICE_FLOOR = 0.0005           # tools/market_vs_model.py's floor before normalising
CLIM_HALF_WINDOW = 15          # days either side of the day of year
BIAS_WINDOWS = (7, 30)         # past days of forecast error
MODELS = ("ecmwf_ifs025", "gfs_seamless", "icon_seamless", "ukmo_seamless", "jma_seamless",
          "gem_seamless", "meteofrance_seamless")
SKY_OKTAS = {"SKC": 0, "CLR": 0, "NSC": 0, "NCD": 0, "CAVOK": 0, "FEW": 2, "SCT": 4, "BKN": 6,
             "OVC": 8, "VV": 8}
WX_FLAGS = (("rain", ("RA", "DZ")), ("shower", ("SH",)), ("thunder", ("TS",)), ("snow", ("SN", "SG", "PL")),
            ("fog", ("FG", "BR", "HZ")))


# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------
def unix_of(stamp):
    """'YYYY-MM-DDTHH:MM[Z]' (UTC) as unix seconds; strptime cost 22 s a build."""
    return calendar.timegm((int(stamp[0:4]), int(stamp[5:7]), int(stamp[8:10]), int(stamp[11:13]),
                            int(stamp[14:16]), 0))


def num(x):
    if x is None or x == "" or x == "T":
        return None if x != "T" else 0.0
    try:
        v = float(x)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def f_to_c(f):
    return None if f is None else (f - 32.0) * 5.0 / 9.0


def c_to_f(c):
    return None if c is None else c * 9.0 / 5.0 + 32.0


def round_half_up(x):
    return int(math.floor(x + 0.5))


def unit_reading(temp_c, tmpf, unit):
    """A report's temperature as the venue reads it: whole degrees of the
    market's unit. F: IEM's tmpf, already whole when the report carried a T
    group (it is the T group rounded), rounded half up otherwise. C: the
    report's whole Celsius (non-US reports are whole C), from tenths half up
    when the report has a T group."""
    if unit == "F":
        if tmpf is not None:
            return round_half_up(tmpf)
        return None if temp_c is None else round_half_up(c_to_f(temp_c))
    return None if temp_c is None else round_half_up(temp_c)


def fmt(v, places=3):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    r = round(v, places)
    if r == int(r):
        return str(int(r))
    return f"{r:.{places}f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# buckets
# ---------------------------------------------------------------------------
def bucket_of(value, bands):
    """Index of the half-open bucket [lo, hi) holding a whole-unit value."""
    for i, (lo, hi) in enumerate(bands):
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return i
    return None


def bucket_rep(i, bands):
    """A bucket's representative value in the unit: the middle of its whole
    degrees; an open tail one width beyond its edge."""
    lo, hi = bands[i]
    width = next((h - l for l, h in bands if l is not None and h is not None), 1)
    if lo is None:
        return hi - 1 - (width - 1) / 2 - width / 2
    if hi is None:
        return lo + (width - 1) / 2 + width / 2
    return lo + (hi - lo - 1) / 2


# ---------------------------------------------------------------------------
# station reports
# ---------------------------------------------------------------------------
def sky(codes, heights):
    """(oktas of the most covered layer, ceiling in ft or None)."""
    best, ceiling = None, None
    for c, h in zip(codes, heights):
        c = (c or "").strip().upper()
        if not c:
            continue
        o = next((v for k, v in SKY_OKTAS.items() if c.startswith(k)), None)
        if o is None:
            continue
        best = o if best is None else max(best, o)
        hv = num(h)
        if o >= 6 and hv is not None and (ceiling is None or hv < ceiling):
            ceiling = hv
    return best, ceiling


def wx_bits(codes):
    s = (codes or "").upper()
    return tuple(int(any(k in s for k in keys)) for _, keys in WX_FLAGS)


class Reports:
    """One station's reports, oldest first, as parallel lists."""

    FIELDS = ("temp_c", "tmpf", "td_c", "rh", "wind_kt", "gust_kt", "dir_sin", "dir_cos", "precip_in",
              "mslp", "vsby", "sky_oktas", "ceiling_ft", "max6_c", "wx")

    def __init__(self, rows):
        self.t = []
        self.rec = []
        for r in rows:
            tmpf = num(r["tmpf"])
            t10 = num(r["t10_c"])
            temp_c = t10 if t10 is not None else f_to_c(tmpf)
            if temp_c is None:
                continue
            td10 = num(r["td10_c"])
            td = td10 if td10 is not None else f_to_c(num(r["dwpf"]))
            d = num(r["drct"])
            mslp = num(r["mslp"])
            if mslp is None and num(r["alti"]) is not None:
                mslp = num(r["alti"]) * 33.8639       # altimeter setting (inHg) as hPa
            oktas, ceiling = sky((r["skyc1"], r["skyc2"], r["skyc3"]), (r["skyl1"], r["skyl2"], r["skyl3"]))
            self.t.append(unix_of(r["valid"]))
            self.rec.append((temp_c, tmpf, td, num(r["relh"]), num(r["sknt"]), num(r["gust"]),
                             None if d is None else math.sin(math.radians(d)),
                             None if d is None else math.cos(math.radians(d)),
                             num(r["p01i"]), mslp, num(r["vsby"]), oktas, ceiling, num(r["max6_c"]),
                             wx_bits(r["wxcodes"])))

        minutes = collections.Counter((u // 60) % 60 for u in self.t)
        self.routine_minute = minutes.most_common(1)[0][0] if minutes else 0

    def precip_since(self, since, end):
        """Precipitation (in) over (since, the end-th report]: the report's
        one-hour amount (p01i) accumulates from the last routine report, so the
        greatest p01i in each routine-to-routine period is that period's total,
        and the periods are summed (review of #314: the maximum alone took one
        hour of a three-hour fall). The station's routine minute is its most
        common report minute."""
        per = {}
        r0 = self.routine_minute * 60
        for k in range(bisect.bisect_right(self.t, since, 0, end), end):
            x = self.rec[k][8]
            if x is None:
                continue
            period = -((r0 - self.t[k]) // 3600)          # the routine report that closes it
            per[period] = max(per.get(period, 0.0), x)
        return sum(per.values()) if per else None

    def known(self, t):
        """Index one past the newest report known at t."""
        return bisect.bisect_right(self.t, t - REPORT_LAG_S)

    def window(self, start, t):
        """(first, end) indexes of the reports valid in [start, t - lag]."""
        return bisect.bisect_left(self.t, start), self.known(t)

    def at_or_before(self, when, end):
        """Index of the newest report valid at or before `when`, among the first `end`."""
        i = bisect.bisect_right(self.t, when, 0, end) - 1
        return i if i >= 0 else None


# ---------------------------------------------------------------------------
# station days (climatology, yesterday, past forecast error)
# ---------------------------------------------------------------------------
NOT_WHOLE = "a gap over %d h in its reports" % common.WHOLE_DAY_MAX_GAP_H
UNFINISHED = "the reports stop inside the day"


def whole_day(gap_h, day_end, newest):
    """None when a station day is whole, else why not (review of #314: one
    rule wherever a day's maximum is read). Whole: its reports leave no gap
    over common.WHOLE_DAY_MAX_GAP_H, local midnight to the first report and the
    last report to the next midnight (common.max_gap_h), and the station's
    reports run past the day's end (`newest`, its newest report), so a fetch
    that stops inside the day never passes for the day's end."""
    if newest is None or newest < day_end:
        return UNFINISHED
    return None if gap_h <= common.WHOLE_DAY_MAX_GAP_H else NOT_WHOLE


def climatology(days, day, before=None):
    """(mean, sd, n, median peak hour) of the station's maximum in C on the
    whole days (`days`, from load_station_daily) within CLIM_HALF_WINDOW of
    `day`'s day of year, every year, before the
    date `before` (default `day` - 1). A row passes the newest day it may
    know (clim_for: review of #314, the D-1 00:01 decision must not count D-2,
    which ended a minute earlier)."""
    before = before or day - dt.timedelta(days=1)
    vals, peaks = [], []
    for y in range(2019, day.year + 1):
        try:
            centre = day.replace(year=y)
        except ValueError:                       # 29 Feb
            centre = day.replace(year=y, day=28)
        for k in range(-CLIM_HALF_WINDOW, CLIM_HALF_WINDOW + 1):
            d = centre + dt.timedelta(days=k)
            if d >= before:
                continue
            v = days.get(d.isoformat())
            if v is not None:
                vals.append(v[0])
                peaks.append(v[2])
    if len(vals) < 2:
        return None, None, len(vals), None
    return statistics.fmean(vals), statistics.stdev(vals), len(vals), statistics.median(peaks)


# ---------------------------------------------------------------------------
# forecasts
# ---------------------------------------------------------------------------
def hourly_known(hour_unix, t):
    """A `_previous_day1` value for hour H is known at t when H - (24 - PUBLISH_H) h <= t."""
    return hour_unix - (24 - PUBLISH_H) * 3600 <= t


def daily_known(day, lead, last_hour, t, tz):
    """A daily row for `day`, lead `lead`, whose window ends at `last_hour` local."""
    end = int(dt.datetime.combine(day, dt.time(last_hour), tz).timestamp())
    return end - (24 * lead - PUBLISH_H) * 3600 <= t


def pick_daily(city, day, t, tz, bm, md):
    """The freshest day-ahead daily forecast known at t: (source, best_match
    max, models {name: max}, best_match row). Lead 1 before lead 2; the whole
    day before the 00-17 window."""
    for lead, field, last_hour in ((1, "tmax_c", 23), (1, "tmax_00_17_c", 17), (2, "tmax_c", 23),
                                   (2, "tmax_00_17_c", 17)):
        if not daily_known(day, lead, last_hour, t, tz):
            continue
        row = bm.get((city, lead, day.isoformat()))
        if row is None:
            continue
        models = {m: v[0 if field == "tmax_c" else 1]
                  for m, v in md.get((city, lead, day.isoformat()), {}).items()}
        return f"l{lead}_{'day' if last_hour == 23 else '00_17'}", num(row[field]), models, row
    return None, None, {}, None


# ---------------------------------------------------------------------------
# the market
# ---------------------------------------------------------------------------
def ladder_at(series, t):
    """[(price, age_s)] per band: the last price at or before t, or (None, None)."""
    out = []
    for ts, ps in series:
        i = bisect.bisect_right(ts, t) - 1
        out.append((ps[i], t - ts[i]) if i >= 0 else (None, None))
    return out


def implied(ladder, bands):
    """(complete, overround, probs, mean, sd, top index, top prob, entropy) of a
    ladder; complete when every bucket has a price no older than MARKET_MAX_AGE_S."""
    if not ladder or any(p is None or a > MARKET_MAX_AGE_S for p, a in ladder):
        return False, None, None, None, None, None, None, None
    raw = [max(PRICE_FLOOR, p) for p, _ in ladder]
    s = sum(raw)
    q = [x / s for x in raw]
    reps = [bucket_rep(i, bands) for i in range(len(bands))]
    mean = sum(a * b for a, b in zip(q, reps))
    sd = math.sqrt(max(0.0, sum(a * (b - mean) ** 2 for a, b in zip(q, reps))))
    top = max(range(len(q)), key=q.__getitem__)
    ent = -sum(x * math.log(x) for x in q if x > 0)
    return True, sum(p for p, _ in ladder), q, mean, sd, top, q[top], ent


def activity(series, t, hours):
    """Bucket-hours whose price moved by more than half a cent in the `hours` before t."""
    n = 0
    for ts, ps in series:
        i = bisect.bisect_right(ts, t) - 1
        j = bisect.bisect_left(ts, t - hours * 3600)
        for k in range(max(j, 1), i + 1):
            if abs(ps[k] - ps[k - 1]) > 0.005:
                n += 1
    return n


# ---------------------------------------------------------------------------
# one row
# ---------------------------------------------------------------------------
COLUMNS = [
    # identity
    "event_id", "source", "city_key", "station", "date", "unit", "n_bands", "bands_lo", "bands_hi",
    "decision_utc", "decision_local", "day_offset", "local_hour", "weekday", "doy_sin", "doy_cos",
    # labels
    "winner", "winner_lo", "winner_hi", "station_max_unit", "station_max_c", "station_in_winner",
    "station_reports_day", "label_unit",
    # observations known at t
    "obs_age_min", "t_now_c", "td_now_c", "rh_now", "wind_now_kt", "gust_now_kt", "dir_sin", "dir_cos",
    "mslp_now", "vsby_now", "sky_oktas_now", "ceiling_now_ft", "wx_rain", "wx_shower", "wx_thunder",
    "wx_snow", "wx_fog", "dt_1h", "dt_3h", "dtd_3h", "dmslp_3h", "precip_3h_in",
    "n_reports_today", "rmax_c", "rmax_unit", "rmax_bucket", "rmax_age_min", "rmax6_c", "tmin_today_c",
    "t_0600_c", "yday_max_c", "yday_max_unit",
    # forecasts known at t
    "fc_daily_source", "fc_bm_tmax_c", "fc_models_n", "fc_models_mean_c", "fc_models_sd_c",
    "fc_models_min_c", "fc_models_max_c", *[f"fc_{m}_c" for m in MODELS],
    "fc_bm_cloud_09_17", "fc_bm_sw_06_17", "fc_bm_wind_09_17", "fc_bm_precip_00_17", "fc_bm_td_09_17",
    "fch_n_known", "fch_day_max_c", "fch_peak_local_hour", "fch_now_c", "fch_rest_max_c", "fch_next3_c",
    "fch_now_td_c", "fch_now_pmsl", "fch_rest_cloud", "fch_rest_sw", "fch_rest_wind", "fch_rest_precip",
    "anom_now_c", "anom_3h_c", "anom_td_now_c", "anom_pmsl_now",
    # past-only station and forecast history
    "clim_mean_c", "clim_sd_c", "clim_n", "clim_peak_hour", "bias7_bm_c", "bias30_bm_c", "mae30_bm_c",
    "bias30_models_c", "bias_n30",
    # the market at t
    "mkt_complete", "mkt_overround", "mkt_mean", "mkt_sd", "mkt_top", "mkt_top_p", "mkt_entropy",
    "mkt_p_rmax_bucket", "mkt_p_alive", "mkt_mean_d1h", "mkt_mean_d3h", "mkt_mean_d6h", "mkt_top_p_d3h",
    "mkt_newest_age_min", "mkt_moves_1h", "mkt_moves_6h", "mkt_prices", "mkt_ages_min",
]


def decision_instants(day, tz):
    """[(day offset, aware local datetime)]: the real instants of D-1 and D
    whose local time is on a decision hour, walked hour by hour in UTC from
    each local midnight. A spring-forward day has 23 (the missing hour is not
    invented), a fall-back day 25 (the repeated hour is there twice, as its two
    instants) - review of #314: attaching the zone to each nominal hour gave
    London's 29 Mar 2026 two rows at one instant and dropped an hour in October."""
    out = []
    for off, hours in ((-1, EVE_HOURS), (0, DAY_HOURS)):
        start, end = common.local_day_bounds(day + dt.timedelta(days=off), tz)
        for u in range(start, end, 3600):
            local = dt.datetime.fromtimestamp(u, common.UTC).astimezone(tz)
            if local.minute == 0 and local.hour in hours:
                out.append((off, local))
    return out


def venue_label(st_max_unit, winner, lo, hi):
    """The day's maximum in the market's unit as the venue read it: the
    station's maximum when it lies in the winning bucket, else the value of the
    winning bucket nearest to it (the venue's truth wins; on 176 of 9,586
    listed days, 118 of them Shenzhen's in Mar-Aug 2026, the two disagree), or
    an edge of the winning bucket when the station has no whole day. The
    station's maximum on a day the venue did not list."""
    if winner is None:
        return st_max_unit
    if st_max_unit is None:
        return lo if lo is not None else hi - 1
    v = st_max_unit
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v >= hi:
        v = hi - 1
    return v


def event_context(e, rep, sdays, tz):
    """What every row of one event shares: its day and ladder, the label, and
    the two climatologies a decision may know (before and after D-2 is whole)."""
    day = dt.date.fromisoformat(e["date"])
    bands, unit = e["bands"], e["unit"]
    d0, d1 = common.local_day_bounds(day, tz)
    # the day's station maximum: every report of D, whatever its lag (label only),
    # on a whole day only (whole_day: an outage or a fetch that stops inside D gives none)
    a, b = bisect.bisect_left(rep.t, d0), bisect.bisect_left(rep.t, d1)
    whole = whole_day(common.max_gap_h(rep.t[a:b], d0, d1), d1, rep.t[-1] if rep.t else None) is None
    day_recs = rep.rec[a:b] if whole else []
    st_max_c = max((r[0] for r in day_recs), default=None)
    st_max_unit = max((unit_reading(r[0], r[1], unit) for r in day_recs), default=None)
    st_in_win = None if st_max_unit is None or e["winner"] is None else \
        bucket_of(st_max_unit, bands) == e["winner"]
    wlo, whi = bands[e["winner"]] if e["winner"] is not None else (None, None)
    return {
        "day": day, "bands": bands, "unit": unit, "d0": d0,
        "winner_lo": wlo, "winner_hi": whi,
        "station_max_unit": st_max_unit, "station_max_c": st_max_c, "station_in_winner": st_in_win,
        "station_reports_day": len(day_recs),
        "label_unit": venue_label(st_max_unit, e["winner"], wlo, whi),
        # D-2 is whole REPORT_LAG_S after D-1 begins; every later decision may count it
        "clim_with": climatology(sdays, day),
        "clim_without": climatology(sdays, day, before=day - dt.timedelta(days=2)),
        "dm2_known_from": common.local_day_bounds(day - dt.timedelta(days=2), tz)[1] + REPORT_LAG_S,
    }


def decision_row(e, ctx, off, local, rep, sdays, bm, md, hourly, series, tz):
    """One row: the decision SNAPSHOT_S after `local` (an hour of D-1 or D on the
    city's clock, `off` -1 or 0), its identity, the event's label (from
    event_context) and every feature known at that instant."""
    day, bands, unit, d0 = ctx["day"], ctx["bands"], ctx["unit"], ctx["d0"]
    hour = local.hour
    t = int(local.timestamp()) + SNAPSHOT_S
    v = collections.OrderedDict()
    v["event_id"], v["source"], v["city_key"], v["station"], v["date"], v["unit"] = \
        e["event_id"], e["source"], e["city"], e["station"], e["date"], unit
    v["n_bands"] = len(bands)
    v["bands_lo"] = ";".join("" if lo is None else fmt(lo) for lo, _ in bands)
    v["bands_hi"] = ";".join("" if hi is None else fmt(hi) for _, hi in bands)
    # the decision instant itself, hh:01 on the city's clock (review of #314: it said hh:00)
    v["decision_utc"] = t
    v["decision_local"] = dt.datetime.fromtimestamp(t, tz).isoformat(timespec="minutes")
    v["day_offset"], v["local_hour"], v["weekday"] = off, hour, local.weekday()
    doy = day.timetuple().tm_yday
    v["doy_sin"], v["doy_cos"] = math.sin(2 * math.pi * doy / 365.25), math.cos(2 * math.pi * doy / 365.25)
    v["winner"], v["winner_lo"], v["winner_hi"] = e["winner"], ctx["winner_lo"], ctx["winner_hi"]
    v["station_max_unit"], v["station_max_c"], v["station_in_winner"] = \
        ctx["station_max_unit"], ctx["station_max_c"], ctx["station_in_winner"]
    v["station_reports_day"] = ctx["station_reports_day"]
    v["label_unit"] = ctx["label_unit"]
    obs_features(v, rep, t, d0, off, tz, unit, bands, sdays, day)
    fc_features(v, e["city"], day, t, tz, bm, md, hourly, rep)
    v["clim_mean_c"], v["clim_sd_c"], v["clim_n"], v["clim_peak_hour"] = \
        ctx["clim_with"] if t >= ctx["dm2_known_from"] else ctx["clim_without"]
    bias_features(v, e["city"], e["station"], day, t, tz, sdays, bm, md)
    market_features(v, series, bands, t)
    return v


def build_event(e, rep, sdays, bm, md, hourly, prices, tz):
    """Every row of one event."""
    ctx = event_context(e, rep, sdays, tz)
    series = prices.get(e["event_id"]) or []
    return [decision_row(e, ctx, off, local, rep, sdays, bm, md, hourly, series, tz)
            for off, local in decision_instants(ctx["day"], tz)]


def obs_features(v, rep, t, d0, off, tz, unit, bands, sdays, day):
    end = rep.known(t)
    if end == 0:
        return
    i = end - 1
    now = rep.rec[i]
    v["obs_age_min"] = (t - rep.t[i]) / 60
    (v["t_now_c"], _, v["td_now_c"], v["rh_now"], v["wind_now_kt"], v["gust_now_kt"], v["dir_sin"], v["dir_cos"],
     _, v["mslp_now"], v["vsby_now"], v["sky_oktas_now"], v["ceiling_now_ft"], _, wx) = now
    v["wx_rain"], v["wx_shower"], v["wx_thunder"], v["wx_snow"], v["wx_fog"] = wx
    for lag_h, key in ((1, "dt_1h"), (3, "dt_3h")):
        j = rep.at_or_before(rep.t[i] - lag_h * 3600, end)
        if j is not None and rep.t[i] - rep.t[j] <= (lag_h + 1) * 3600:
            v[key] = now[0] - rep.rec[j][0]
            if lag_h == 3:
                if now[2] is not None and rep.rec[j][2] is not None:
                    v["dtd_3h"] = now[2] - rep.rec[j][2]
                if now[9] is not None and rep.rec[j][9] is not None:
                    v["dmslp_3h"] = now[9] - rep.rec[j][9]
    v["precip_3h_in"] = rep.precip_since(rep.t[i] - 3 * 3600, end)
    # yesterday: the whole local day before the decision's own day, once it has ended
    dday = day + dt.timedelta(days=off)
    yday = sdays.get((dday - dt.timedelta(days=1)).isoformat())
    if yday is not None and t - REPORT_LAG_S >= common.local_day_bounds(dday, tz)[0]:
        v["yday_max_c"] = yday[0]
        v["yday_max_unit"] = round_half_up(yday[1]) if unit == "F" else round_half_up(yday[0])
    if off != 0:
        return
    a, b = rep.window(d0, t)
    if b <= a:
        v["n_reports_today"] = 0
        return
    recs = rep.rec[a:b]
    v["n_reports_today"] = len(recs)
    k = max(range(len(recs)), key=lambda x: (recs[x][0], -x))
    v["rmax_c"] = recs[k][0]
    v["rmax_unit"] = max(unit_reading(r[0], r[1], unit) for r in recs)
    v["rmax_bucket"] = bucket_of(v["rmax_unit"], bands)
    v["rmax_age_min"] = (t - rep.t[a + k]) / 60
    six = [r[13] for j, r in enumerate(recs) if r[13] is not None and rep.t[a + j] - 6 * 3600 >= d0]
    v["rmax6_c"] = max(six) if six else None
    v["tmin_today_c"] = min(r[0] for r in recs)
    at_six = int(dt.datetime.combine(day, dt.time(6), tz).timestamp())      # 06:00 on the wall clock
    if t - REPORT_LAG_S >= at_six:          # only once every report up to 06:00 is known (review of #314)
        j = rep.at_or_before(at_six, b)
        if j is not None and j >= a:
            v["t_0600_c"] = rep.rec[j][0]


def fc_features(v, city, day, t, tz, bm, md, hourly, rep):
    src, bm_max, models, row = pick_daily(city, day, t, tz, bm, md)
    v["fc_daily_source"] = src
    v["fc_bm_tmax_c"] = bm_max
    vals = [x for x in models.values() if x is not None]
    v["fc_models_n"] = len(vals)
    if vals:
        v["fc_models_mean_c"] = statistics.fmean(vals)
        v["fc_models_sd_c"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        v["fc_models_min_c"], v["fc_models_max_c"] = min(vals), max(vals)
    for m in MODELS:
        v[f"fc_{m}_c"] = models.get(m)
    if row is not None:
        v["fc_bm_cloud_09_17"] = num(row["cloud_09_17_pct"])
        v["fc_bm_sw_06_17"] = num(row["shortwave_06_17_wh_m2"])
        v["fc_bm_wind_09_17"] = num(row["wind_09_17_kmh"])
        v["fc_bm_precip_00_17"] = num(row["precip_00_17_mm"])
        v["fc_bm_td_09_17"] = num(row["td_09_17_c"])
    h = hourly.get(city)
    if not h:
        return
    ts, rows = h
    d0, d1 = common.local_day_bounds(day, tz)
    a, b = bisect.bisect_left(ts, d0), bisect.bisect_left(ts, d1)
    known = [(ts[k], rows[k]) for k in range(a, b) if hourly_known(ts[k], t) and rows[k][0] is not None]
    v["fch_n_known"] = len(known)
    if not known:
        return
    peak = max(known, key=lambda x: (x[1][0], -x[0]))
    v["fch_day_max_c"] = peak[1][0]
    v["fch_peak_local_hour"] = dt.datetime.fromtimestamp(peak[0], common.UTC).astimezone(tz).hour
    hour_floor = t - t % 3600
    now = next((r for u, r in known if u == hour_floor), None)
    if now is None:
        # a half-hour zone's hours sit at :30 UTC
        now = next((r for u, r in known if 0 <= t - u < 3600), None)
    if now is not None:
        v["fch_now_c"], v["fch_now_td_c"], v["fch_now_pmsl"] = now[0], now[1], now[6]
    rest = [r for u, r in known if u > t]
    if rest:
        v["fch_rest_max_c"] = max(r[0] for r in rest)
        nxt = [r[0] for u, r in known if t < u <= t + 3 * 3600]
        v["fch_next3_c"] = max(nxt) if nxt else None
        for key, idx, agg in (("fch_rest_cloud", 2, "mean"), ("fch_rest_sw", 3, "sum"),
                              ("fch_rest_wind", 4, "mean"), ("fch_rest_precip", 5, "sum")):
            xs = [r[idx] for r in rest if r[idx] is not None]
            if xs:
                v[key] = statistics.fmean(xs) if agg == "mean" else sum(xs)
    if now is not None and v.get("t_now_c") is not None and v.get("obs_age_min") is not None \
            and v["obs_age_min"] <= 90:
        v["anom_now_c"] = v["t_now_c"] - now[0]
        if v.get("td_now_c") is not None and now[1] is not None:
            v["anom_td_now_c"] = v["td_now_c"] - now[1]
        if v.get("mslp_now") is not None and now[6] is not None:
            v["anom_pmsl_now"] = v["mslp_now"] - now[6]
        end = rep.known(t)
        j = rep.at_or_before(t - 3 * 3600, end)
        then = next((r for u, r in known if 0 <= (t - 3 * 3600) - u < 3600), None)
        if j is not None and then is not None and (t - 3 * 3600) - rep.t[j] <= 3600:
            v["anom_3h_c"] = rep.rec[j][0] - then[0]


def bias_features(v, city, station, day, t, tz, sdays, bm, md):
    """The day-ahead forecast's recent error at this station, on whole target
    days (`sdays`, from load_station_daily) that had ended REPORT_LAG_S before t."""
    errs, merrs = [], []
    for k in range(1, max(BIAS_WINDOWS) + 2):
        d = day - dt.timedelta(days=k)
        if common.local_day_bounds(d, tz)[1] + REPORT_LAG_S > t:
            continue
        obs = sdays.get(d.isoformat())
        row = bm.get((city, 1, d.isoformat()))
        if obs is None or row is None or num(row["tmax_c"]) is None:      # obs: whole days only
            continue
        errs.append((k, obs[0] - num(row["tmax_c"])))
        ms = [x[0] for x in md.get((city, 1, d.isoformat()), {}).values() if x[0] is not None]
        if ms:
            merrs.append((k, obs[0] - statistics.fmean(ms)))
    e7 = [x for k, x in errs if k <= 7 + 1][:7]
    e30 = [x for _, x in errs][:30]
    m30 = [x for _, x in merrs][:30]
    v["bias7_bm_c"] = statistics.fmean(e7) if e7 else None
    v["bias30_bm_c"] = statistics.fmean(e30) if e30 else None
    v["mae30_bm_c"] = statistics.fmean(abs(x) for x in e30) if e30 else None
    v["bias30_models_c"] = statistics.fmean(m30) if m30 else None
    v["bias_n30"] = len(e30)


def market_features(v, series, bands, t):
    if not series:
        v["mkt_complete"] = False
        return
    lad = ladder_at(series, t)
    complete, over, q, mean, sd, top, top_p, ent = implied(lad, bands)
    v["mkt_complete"] = complete
    v["mkt_prices"] = ";".join(fmt(p, 4) for p, _ in lad)
    v["mkt_ages_min"] = ";".join("" if a is None else fmt(a / 60, 1) for _, a in lad)
    ages = [a for _, a in lad if a is not None]
    v["mkt_newest_age_min"] = min(ages) / 60 if ages else None
    v["mkt_moves_1h"], v["mkt_moves_6h"] = activity(series, t, 1), activity(series, t, 6)
    if not complete:
        return
    v["mkt_overround"], v["mkt_mean"], v["mkt_sd"] = over, mean, sd
    v["mkt_top"], v["mkt_top_p"], v["mkt_entropy"] = top, top_p, ent
    rb = v.get("rmax_bucket")
    if rb is not None:
        v["mkt_p_rmax_bucket"] = q[rb]
        v["mkt_p_alive"] = sum(q[rb:])
    for hours, key in ((1, "mkt_mean_d1h"), (3, "mkt_mean_d3h"), (6, "mkt_mean_d6h")):
        c, _, q2, m2, _, _, tp2, _ = implied(ladder_at(series, t - hours * 3600), bands)
        if c:
            v[key] = mean - m2
            if hours == 3:
                v["mkt_top_p_d3h"] = top_p - q2[top]
