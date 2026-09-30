#!/usr/bin/env python3
"""The day-ahead lane of fec-v1 (docs/FORECAST_EVALUATION_CONTRACT.md), from a
frozen export of v_city_hit_history, read as anon, with the settled winner's
bucket from fact_band_outcome and each forecast's bucket by the database's own
venue_round(). The query is in the file's header comment; the export is
committed beside the results so the report reproduces without the database.

    python tools/fec_day_ahead.py data/eval/fec_v1/da_rows_2026-09-13_2026-09-29.csv.gz

Compared on identical rows, with a date-clustered bootstrap (fec-v1 §5):
  the raw public forecast   top-1 only: its bucket is venue_round(forecast_max_c)
  the priced centre         top-1 only: venue_round(centre_c), where recorded (from 22 Sep 18:32Z)
  the engine's ladder       log loss (p on the winner, floored at 1e-6), top-1, Brier
  the market                on head_to_head rows only: the same buckets both priced before
                            the day, each renormalised over that set (v_city_hit_history)

The export statement, run 30 Sep 2026 10:51Z through the Supabase SQL tool as
anon (bounds are literals; its single text cell, gzipped, is the rows file; all
707 lines have the 18 columns):

  begin; set local role anon;
  with h as (
    select h.*, w.band_lo w_lo, w.band_hi w_hi, w.open_low w_ol, w.open_high w_oh
    from v_city_hit_history h
    join lateral (select f.band_lo, f.band_hi, f.open_low, f.open_high from fact_band_outcome f
                  where f.city_key = h.city_key and f.for_date = h.for_date and f.settled_yes limit 1) w on true
    where h.for_date between date '2026-09-13' and date '2026-09-29'),
  r as (
    select h.*, venue_round(forecast_max_c, unit) raw_r,
           case when centre_c is null then null else venue_round(centre_c, unit) end centre_r
    from h)
  select '<the header line of the rows file>' || chr(10) ||
    string_agg(concat_ws(',', city_key, unit, for_date, round(hours_before_day::numeric,2), model_hit::int,
       round(model_prob_on_winner::numeric,6), round(brier_model::numeric,6), round(brier_uniform::numeric,6),
       head_to_head::int, coalesce(market_hit::int::text,''), coalesce(round(market_prob_on_winner::numeric,6)::text,''),
       coalesce(round(brier_model_common::numeric,6)::text,''), coalesce(round(brier_market::numeric,6)::text,''),
       observed_max_c, forecast_max_c, coalesce(centre_c::text,''),
       (case when w_ol then raw_r < w_hi when w_oh then raw_r >= w_lo else raw_r >= w_lo and raw_r < w_hi end)::int,
       coalesce((case when centre_r is null then null when w_ol then centre_r < w_hi when w_oh then centre_r >= w_lo
                      else centre_r >= w_lo and centre_r < w_hi end)::int::text, '')),
     chr(10) order by for_date, city_key) csv
  from r;
  rollback;

concat_ws skips a NULL, so a NULL in an uncoalesced column would shift a row's
fields; load() refuses any row without all 18.
"""
import csv
import gzip
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fec_same_day import cluster_boot, wilson  # noqa: E402

FLOOR = 1e-6


def load(path):
    with gzip.open(path, "rt") as f:
        rows = list(csv.DictReader(f))
    bad = [i for i, r in enumerate(rows) if None in r or None in r.values()]
    if bad:
        raise ValueError(f"{len(bad)} rows without every column (first at data line {bad[0] + 1})")
    return rows


def num(v):
    return None if v in ("", None) else float(v)


def block(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    ll = [-math.log(max(num(r["model_prob_on_winner"]), FLOOR)) for r in rows]
    out = {"n": n, "dates": len({r["for_date"] for r in rows}), "cities": len({r["city_key"] for r in rows}),
           "engine": {"logloss": round(sum(ll) / n, 4),
                      "top1": round(sum(int(r["model_hit"]) for r in rows) / n, 4),
                      "top1_wilson95": wilson(sum(int(r["model_hit"]) for r in rows), n),
                      "brier": round(sum(num(r["brier_model"]) for r in rows) / n, 4)},
           "uniform_brier": round(sum(num(r["brier_uniform"]) for r in rows) / n, 4),
           "raw_forecast": {"top1": round(sum(int(r["raw_hit"]) for r in rows) / n, 4),
                            "top1_wilson95": wilson(sum(int(r["raw_hit"]) for r in rows), n),
                            "mae_c": round(sum(abs(num(r["forecast_max_c"]) - num(r["observed_max_c"])) for r in rows) / n, 3),
                            "bias_c": round(sum(num(r["forecast_max_c"]) - num(r["observed_max_c"]) for r in rows) / n, 3)}}
    # engine top-1 minus the raw forecast's bucket, paired by row
    out["engine_minus_raw_top1"] = cluster_boot([(r["for_date"], int(r["model_hit"]) - int(r["raw_hit"])) for r in rows])
    c = [r for r in rows if r["centre_c"] not in ("", None)]
    if c:
        out["priced_centre_subset"] = {
            "n": len(c), "dates": len({r["for_date"] for r in c}),
            "centre_top1": round(sum(int(r["centre_hit"]) for r in c) / len(c), 4),
            "raw_top1": round(sum(int(r["raw_hit"]) for r in c) / len(c), 4),
            "engine_top1": round(sum(int(r["model_hit"]) for r in c) / len(c), 4),
            "centre_mae_c": round(sum(abs(num(r["centre_c"]) - num(r["observed_max_c"])) for r in c) / len(c), 3),
            "raw_mae_c": round(sum(abs(num(r["forecast_max_c"]) - num(r["observed_max_c"])) for r in c) / len(c), 3),
            "centre_bias_c": round(sum(num(r["centre_c"]) - num(r["observed_max_c"]) for r in c) / len(c), 3),
            "raw_minus_centre_abs_error": cluster_boot(
                [(r["for_date"], abs(num(r["forecast_max_c"]) - num(r["observed_max_c"]))
                  - abs(num(r["centre_c"]) - num(r["observed_max_c"]))) for r in c])}
    h = [r for r in rows if r["head_to_head"] == "1" and r["market_prob_on_winner"] not in ("", None)]
    if h:
        # on the shared buckets both sides use the renormalised p (v_city_hit_history);
        # the engine's shared-set p is not exported, so log loss vs the market is on
        # the Brier over the shared set, which the view computes for both
        out["head_to_head"] = {
            "n": len(h), "dates": len({r["for_date"] for r in h}),
            "engine_brier_shared": round(sum(num(r["brier_model_common"]) for r in h) / len(h), 4),
            "market_brier_shared": round(sum(num(r["brier_market"]) for r in h) / len(h), 4),
            "market_logloss": round(sum(-math.log(max(num(r["market_prob_on_winner"]), FLOOR)) for r in h) / len(h), 4),
            "engine_logloss_full_ladder": round(sum(-math.log(max(num(r["model_prob_on_winner"]), FLOOR)) for r in h) / len(h), 4),
            "engine_top1": round(sum(int(r["model_hit"]) for r in h) / len(h), 4),
            "market_top1": round(sum(int(r["market_hit"]) for r in h) / len(h), 4),
            "brier_engine_minus_market": cluster_boot(
                [(r["for_date"], num(r["brier_model_common"]) - num(r["brier_market"])) for r in h])}
    return out


def main(argv=None):
    path = (argv or sys.argv[1:])[0]
    rows = load(path)
    rep = {"contract": "fec-v1", "lane": "DA", "source": os.path.basename(path),
           "all": block(rows), "C": block([r for r in rows if r["unit"] == "C"]),
           "F": block([r for r in rows if r["unit"] == "F"])}
    out = path.replace("_rows_", "_report_").replace(".csv.gz", ".json")
    with open(out, "w") as f:
        json.dump(rep, f, indent=1, sort_keys=True)
    print(json.dumps(rep, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
