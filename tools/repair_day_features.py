"""Recompute derived_city_day_features from the repository's raw readings
(plan v2 P1.6, 28 Sep).

prune_observations cut every city at one instant, part-way through most
cities' local day, and refresh_feature_cache then overwrote that day's cached
features from the readings the cut left (fixed in
20260929010000_a_cut_day_keeps_its_cached_values.sql). The raw readings those
days were computed from are in the repository: data/archive/observations
(what each prune deleted) and data/mirror/weather_observations (everything
held on 24 Sep and every day since). This recomputes a city's local day from
them exactly as v_city_day_features (sql/ad4_21) does:

  obs      every reading with a temperature, local date and hour in the
           city's timezone
  daily    max, min, count; cloud and wind means/maxima over local 09-17;
           precipitation summed over all readings; the wind vector over
           09-17 readings with a direction and a speed
  morning  the reading at local 06-10 with a dewpoint nearest 08:00
  day-over-day terms from the previous day (here: the previous day as the
           repaired cache holds it, which is what the fixed refresh uses)

Numeric rounding follows Postgres (half away from zero), so the output can
be compared with the cache field by field.

  python tools/repair_day_features.py --days 2026-07-28,2026-07-29 --cities nyc
  python tools/repair_day_features.py --around-cuts cuts.txt --out rows.json

It only reads the repository and writes a file; it changes nothing in the
database.
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import json
import math
import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = (sorted(glob.glob(os.path.join(ROOT, "data", "archive", "observations", "*.csv.gz")))
           + sorted(glob.glob(os.path.join(ROOT, "data", "mirror", "weather_observations", "*.csv.gz"))))


def num(v):
    return None if v in (None, "") else Decimal(v)


def pg_round(x, places):
    """Postgres round(numeric, n): half away from zero."""
    if x is None:
        return None
    q = Decimal(1).scaleb(-places)
    return Decimal(x).quantize(q, rounding=ROUND_HALF_UP) if x >= 0 else \
        -((-Decimal(x)).quantize(q, rounding=ROUND_HALF_UP))


def load(cities_tz, want=None):
    """{(city, local_date): [reading,...]} from every repository file, one row
    per (city_key, valid_at, source) - the table's unique key. The archive
    file wins over the mirror for the same key: it holds the row as it was
    when the prune deleted it. `want` limits to a set of (city, local_date)."""
    seen = {}
    conflicts = 0
    for path in SOURCES:
        from_archive = os.sep + "archive" + os.sep in path
        with gzip.open(path, "rt") as fh:
            for r in csv.DictReader(fh):
                c = r["city_key"]
                if c not in cities_tz or not r.get("temp_c"):
                    continue
                at = dt.datetime.fromisoformat(r["valid_at"].replace("Z", "+00:00"))
                local = at.astimezone(cities_tz[c])
                if want is not None and (c, local.date()) not in want:
                    continue
                key = (c, at, r.get("source"))
                row = {
                    "local_date": local.date(), "local_hour": local.hour, "valid_at": at,
                    "temp_c": num(r["temp_c"]), "dewpoint_c": num(r.get("dewpoint_c")),
                    "humidity": num(r.get("humidity")), "wind_speed": num(r.get("wind_speed")),
                    "wind_dir_deg": num(r.get("wind_dir_deg")), "precip": num(r.get("precip")),
                    "cloud_cover": num(r.get("cloud_cover")), "pressure_hpa": num(r.get("pressure_hpa")),
                    "archive": from_archive,
                }
                old = seen.get(key)
                if old is not None:
                    if any(old[k] != row[k] for k in ("temp_c", "dewpoint_c", "pressure_hpa")):
                        conflicts += 1
                    if old["archive"] and not from_archive:
                        continue
                seen[key] = row
    days = {}
    for (c, _, _), row in seen.items():
        days.setdefault((c, row["local_date"]), []).append(row)
    return days, conflicts


def features(rows):
    """One city-day as v_city_day_features computes it, before the
    day-over-day terms. Also returns how many morning candidates tied."""
    mx = max(r["temp_c"] for r in rows)
    mn = min(r["temp_c"] for r in rows)
    day = [r for r in rows if 9 <= r["local_hour"] <= 17]
    clouds = [r["cloud_cover"] for r in day if r["cloud_cover"] is not None]
    winds = [r["wind_speed"] for r in day if r["wind_speed"] is not None]
    vec = [r for r in day if r["wind_dir_deg"] is not None and r["wind_speed"] is not None]
    su = sum(-float(r["wind_speed"]) * math.sin(math.radians(float(r["wind_dir_deg"]))) for r in vec)
    sv = sum(-float(r["wind_speed"]) * math.cos(math.radians(float(r["wind_dir_deg"]))) for r in vec)
    scalar = sum((r["wind_speed"] for r in vec), Decimal(0))
    morning = [r for r in rows if 6 <= r["local_hour"] <= 10 and r["dewpoint_c"] is not None]
    best = min((abs(r["local_hour"] - 8) for r in morning), default=None)
    tied = [r for r in morning if abs(r["local_hour"] - 8) == best] if morning else []
    m = sorted(tied, key=lambda r: r["valid_at"])[0] if tied else None
    avg = lambda xs: (sum(xs, Decimal(0)) / len(xs)) if xs else None
    out = {
        "max_c": mx, "min_c": mn, "diurnal_range_c": mx - mn, "n_obs": len(rows),
        "morning_temp_c": m["temp_c"] if m else None,
        "morning_dewpoint_c": m["dewpoint_c"] if m else None,
        "dewpoint_depression_c": (m["temp_c"] - m["dewpoint_c"]) if m else None,
        "morning_humidity": m["humidity"] if m else None,
        "morning_pressure_hpa": m["pressure_hpa"] if m else None,
        "morning_to_max_c": (mx - m["temp_c"]) if m else None,
        "cloud_mean": pg_round(avg(clouds), 2), "cloud_max": max(clouds) if clouds else None,
        "wind_mean": pg_round(avg(winds), 2),
        "precip_total": pg_round(sum((r["precip"] or Decimal(0) for r in rows), Decimal(0)), 3),
        "wind_max": pg_round(max(winds), 2) if winds else None,
        "wind_u_mean": pg_round(Decimal(repr(su / float(scalar))), 4) if len(vec) >= 3 and scalar > 0 else None,
        "wind_v_mean": pg_round(Decimal(repr(sv / float(scalar))), 4) if len(vec) >= 3 and scalar > 0 else None,
    }
    return out, (len(tied) > 1)


def jsonable(v):
    if isinstance(v, Decimal):
        return str(v.normalize()) if v == v.to_integral() else str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


# ---------------------------------------------------------------------------
# plan: what to change, the audit, and the one transaction that changes it.
#
# The cache is dumped with DUMP_SQL (every column, one line per city-day,
# fields joined by '|', NULL as '~') and its md5 checked against the file.
# A city-day is repaired only when the cache holds FEWER readings than the
# repository: it was recomputed from what a cut left. Where the cache holds
# more, the repository is missing readings and the cache stays. Where the
# counts match but a value differs, the cause is not a cut, and it stays too
# (the plan lists those rows and why).
# ---------------------------------------------------------------------------
DUMP_COLS = ["city_key", "obs_date", "n_obs", "max_c", "min_c", "diurnal_range_c", "prev_max_c",
             "delta_max_c", "morning_temp_c", "morning_dewpoint_c", "dewpoint_depression_c",
             "morning_humidity", "morning_pressure_hpa", "morning_to_max_c", "cloud_mean", "cloud_max",
             "wind_mean", "wind_max", "precip_total", "pressure_change_24h_hpa", "wind_u_mean",
             "wind_v_mean", "computed_at"]
BASE = ["n_obs", "max_c", "min_c", "diurnal_range_c", "morning_temp_c", "morning_dewpoint_c",
        "dewpoint_depression_c", "morning_humidity", "morning_pressure_hpa", "morning_to_max_c",
        "cloud_mean", "cloud_max", "wind_mean", "wind_max", "precip_total", "wind_u_mean", "wind_v_mean"]
MORNING = {"morning_temp_c", "morning_dewpoint_c", "dewpoint_depression_c", "morning_humidity",
           "morning_pressure_hpa", "morning_to_max_c"}


def _line_sql(alias):
    """The dump expression for one cached row: the same text on both sides of
    every md5 comparison."""
    parts = []
    for c in DUMP_COLS:
        if c in ("city_key", "obs_date", "n_obs", "max_c", "min_c", "diurnal_range_c"):
            parts.append(f"{alias}.{c}")
        elif c == "computed_at":
            parts.append(f"""to_char({alias}.computed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')""")
        else:
            parts.append(f"coalesce({alias}.{c}::text, '~')")
    return "concat_ws('|', " + ", ".join(parts) + ")"


def dump_sql(cuts, city_filter):
    """The query that dumps the cached rows around the cuts (-1, 0, +1 local
    days per city), one '|'-joined line each, with the md5 of the lines."""
    values = ",".join(f"('{c}'::timestamptz)" for c in cuts)
    return (f"with cuts(t) as (values {values}),\n"
            "pairs as (select distinct c.city_key, (t at time zone c.timezone)::date + k as d\n"
            "            from public.cities c, cuts, generate_series(-1, 1) k where c.timezone is not null),\n"
            f"r as (select {_line_sql('f')} as line, f.city_key, f.obs_date\n"
            "        from public.derived_city_day_features f join pairs p on f.city_key = p.city_key and f.obs_date = p.d\n"
            f"       where {city_filter})\n"
            "select count(*) as n, md5(string_agg(line, E'\\n' order by city_key, obs_date)) as md5,\n"
            "       string_agg(line, E'\\n' order by city_key, obs_date) as body from r")


def _dec(v):
    return None if v in (None, "~") else Decimal(str(v))


def _plain(v):
    """A value as SQL numeric text: 30 rather than 3E+1."""
    return "~" if v is None else format(Decimal(str(v)), "f")


def plan(cache_lines, recomputed):
    """Split the cached rows into repaired / equal / kept, with the reason."""
    cache = {}
    for line in cache_lines:
        v = line.split("|")
        if len(v) != len(DUMP_COLS):
            raise SystemExit(f"a dump line has {len(v)} fields, not {len(DUMP_COLS)}: {line[:80]}")
        d = dict(zip(DUMP_COLS, v))
        cache[(d["city_key"], d["obs_date"])] = (line, d)
    repo = {(r["city_key"], r["obs_date"]): r for r in recomputed["rows"]}
    if set(cache) != set(repo):
        raise SystemExit(f"the dump and the recompute cover different city-days: "
                         f"{len(set(cache) - set(repo))} only cached, {len(set(repo) - set(cache))} only recomputed")
    out = {"repair": [], "equal": [], "equal_but_morning_tie": [], "cache_has_more": [], "same_count_differs": []}
    for key in sorted(cache):
        line, c = cache[key]
        r = repo[key]
        changed = [f for f in BASE if _dec(c[f]) != _dec(r[f])]
        n_cache, n_repo = int(c["n_obs"]), int(r["n_obs"])
        row = {"city_key": key[0], "obs_date": key[1], "n_cached": n_cache, "n_repository": n_repo,
               "changed": changed}
        if n_cache > n_repo:
            out["cache_has_more"].append(row)
        elif not changed:
            out["equal"].append(row)
        elif n_cache == n_repo:
            if r["morning_tied"] and set(changed) <= MORNING:
                out["equal_but_morning_tie"].append(row)
            else:
                out["same_count_differs"].append(row)
        else:
            row["before_line"] = line
            row["before"] = {k: (None if c[k] == "~" else c[k]) for k in DUMP_COLS[2:]}
            row["after"] = {f: r[f] for f in BASE}
            row["morning_tied"] = r["morning_tied"]
            out["repair"].append(row)
    return out


CHUNK = 60


def repair_chunks(rows, audit_file):
    """The base-value repair, as DO blocks of CHUNK rows. Each block is all or
    nothing and independent of the others: it refuses unless its own values
    hash to what the audit file records AND its rows still hold exactly the
    values the audit recorded as "before" - so a copy that is not exact, a
    block run twice, or a row that changed since the audit, changes nothing.
    Then it writes the repository's values."""
    import hashlib
    cols = ",\n         ".join(f"{f} = r.{f}" for f in BASE)
    fields = ",\n         ".join(
        f"nullif(split_part(l, '|', {i + 3}), '~')::{'int' if f == 'n_obs' else 'numeric'} as {f}"
        for i, f in enumerate(BASE))
    out = []
    for k in range(0, len(rows), CHUNK):
        part = rows[k:k + CHUNK]
        n = len(part)
        blob = "\n".join("|".join([r["city_key"], r["obs_date"]] + [_plain(r["after"][f]) for f in BASE])
                         for r in part)
        before = "\n".join(r["before_line"] for r in part)
        out.append(f"""-- derived_city_day_features repair, block {k // CHUNK + 1}: {n} city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: {audit_file}).
do $repair$
declare
  v_blob text := $blob${blob}$blob$;
  v_n int;
begin
  if md5(v_blob) <> '{hashlib.md5(blob.encode()).hexdigest()}' then
    raise exception 'the repair values are not the ones the audit file records';
  end if;
  create temp table _repair on commit drop as
  select split_part(l, '|', 1) as city_key, split_part(l, '|', 2)::date as obs_date,
         {fields}
    from unnest(string_to_array(v_blob, E'\\n')) l;
  select count(*) into v_n from _repair;
  if v_n <> {n} then raise exception 'expected {n} repair rows, parsed %', v_n; end if;
  -- The rows still hold exactly what the audit recorded as "before".
  if (select md5(string_agg({_line_sql('f')}, E'\\n' order by f.city_key, f.obs_date))
        from public.derived_city_day_features f join _repair r using (city_key, obs_date))
     is distinct from '{hashlib.md5(before.encode()).hexdigest()}' then
    raise exception 'these rows do not hold what the audit recorded (already repaired, or changed since)';
  end if;
  update public.derived_city_day_features f
     set {cols},
         computed_at = now()
    from _repair r
   where f.city_key = r.city_key and f.obs_date = r.obs_date;
  get diagnostics v_n = row_count;
  if v_n <> {n} then raise exception 'expected to repair {n} rows, updated %', v_n; end if;
end $repair$;
""")
    return out


def day_over_day_sql(cuts, repaired_since):
    """After the blocks: the day-over-day terms of every repaired day (a row in
    the cut pairs rewritten since `repaired_since`) and of the cached day after
    it, exactly as the view defines them - lag over the city's cached days.
    Only rows whose terms differ are written; running it again changes
    nothing."""
    values = ",".join(f"('{c}'::timestamptz)" for c in cuts)
    return f"""-- derived_city_day_features repair, last step: the day-over-day terms.
with cuts(t) as (values {values}),
pairs as (select distinct c.city_key, (t at time zone c.timezone)::date + k as d
            from public.cities c, cuts, generate_series(-1, 1) k where c.timezone is not null),
repaired as (select f.city_key, f.obs_date from public.derived_city_day_features f
               join pairs p on f.city_key = p.city_key and f.obs_date = p.d
              where f.computed_at >= timestamptz '{repaired_since}'),
nxt as (select r.city_key, (select min(q.obs_date) from public.derived_city_day_features q
                             where q.city_key = r.city_key and q.obs_date > r.obs_date) as obs_date
          from repaired r),
affected as (select city_key, obs_date from repaired
             union select city_key, obs_date from nxt where obs_date is not null),
lagged as (select city_key, obs_date, lag(max_c) over w as p_max, lag(morning_pressure_hpa) over w as p_pressure
             from public.derived_city_day_features
           window w as (partition by city_key order by obs_date)),
done as (
  update public.derived_city_day_features f
     set prev_max_c = l.p_max,
         delta_max_c = f.max_c - l.p_max,
         pressure_change_24h_hpa = round(f.morning_pressure_hpa - l.p_pressure, 2),
         computed_at = now()
    from lagged l join affected a using (city_key, obs_date)
   where f.city_key = l.city_key and f.obs_date = l.obs_date
     and (f.prev_max_c is distinct from l.p_max
          or f.delta_max_c is distinct from f.max_c - l.p_max
          or f.pressure_change_24h_hpa is distinct from round(f.morning_pressure_hpa - l.p_pressure, 2))
  returning f.city_key)
select (select count(*) from repaired) as repaired_rows, (select count(*) from affected) as affected_rows,
       (select count(*) from done) as day_over_day_written
"""


def main_plan(argv):
    ap = argparse.ArgumentParser(prog="repair_day_features.py plan")
    ap.add_argument("--cache", nargs="+", required=True, help="dump files (DUMP_SQL's body, md5-checked)")
    ap.add_argument("--recomputed", required=True, help="the recompute step's output")
    ap.add_argument("--cuts", required=True, help="JSON list of the prune cut instants")
    ap.add_argument("--audit", required=True)
    ap.add_argument("--sql-dir", required=True, help="where the repair blocks and the last step are written")
    ap.add_argument("--repaired-since", required=True,
                    help="an instant after the last refresh and before the first block runs")
    a = ap.parse_args(argv)
    lines = [l for f in a.cache for l in open(f).read().split("\n") if l]
    recomputed = json.load(open(a.recomputed))
    p = plan(lines, recomputed)
    counts = {k: len(v) for k, v in p.items()}
    audit = {
        "what": "derived_city_day_features rows recomputed from what a prune's cut left (plan v2 P1.6); "
                "repaired from the repository's raw readings",
        "cuts": json.load(open(a.cuts)),
        "counts": counts,
        "missing_in_repository": recomputed["missing"],
        "rows": p,
    }
    with open(a.audit, "w") as fh:          # one row per line: readable and diffable
        fh.write(json.dumps({k: v for k, v in audit.items() if k != "rows"}) + "\n")
        for kind, rows in p.items():
            for r in rows:
                fh.write(json.dumps({"kind": kind, **r}) + "\n")
    os.makedirs(a.sql_dir, exist_ok=True)
    blocks = repair_chunks(p["repair"], a.audit)
    for i, b in enumerate(blocks, 1):
        open(os.path.join(a.sql_dir, f"repair_{i:02d}.sql"), "w").write(b)
    open(os.path.join(a.sql_dir, "repair_last_day_over_day.sql"), "w").write(
        day_over_day_sql(audit["cuts"], a.repaired_since))
    print(json.dumps({**counts, "blocks": len(blocks)}))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "plan":
        return main_plan(sys.argv[2:])
    ap = argparse.ArgumentParser()
    ap.add_argument("--tz", required=True, help="JSON file {city_key: IANA timezone}")
    ap.add_argument("--pairs", required=True, help="JSON file [[city_key, 'YYYY-MM-DD'], ...] to recompute")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tz = {k: ZoneInfo(v) for k, v in json.load(open(a.tz)).items()}
    want = {(c, dt.date.fromisoformat(d)) for c, d in json.load(open(a.pairs))}
    days, conflicts = load(tz, want)
    out, ties, missing = [], 0, []
    for (c, d) in sorted(want):
        rows = days.get((c, d))
        if not rows:
            missing.append([c, d.isoformat()])
            continue
        f, tie = features(rows)
        ties += tie
        out.append({"city_key": c, "obs_date": d.isoformat(), "morning_tied": tie,
                    **{k: jsonable(v) for k, v in f.items()}})
    json.dump({"rows": out, "missing": missing, "conflicts": conflicts, "morning_ties": ties},
              open(a.out, "w"), indent=0)
    print(f"{len(out)} city-days recomputed, {len(missing)} with no readings in the repository, "
          f"{conflicts} key conflicts between files, {ties} with tied morning readings -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
