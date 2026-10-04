#!/usr/bin/env python3
"""Challenger C's forward test (docs/CHALLENGER_C_PREREG.md, "Forward shadow"):
rd1 against rd3 on the pairs the tick recorded live, by the rule fixed on
30 Sep before rd3's first forward row.

    python tools/fec_s10_forward.py sql --export-date 2026-10-24 [--from D --to D] > export.sql
        run it through the Supabase SQL tool (s10_shadow_checkpoints is the
        service role's, so not as anon); gzip its single text cell into
        data/eval/fec_v1/s10_forward/rows_<export date>[_<part>].csv.gz
    python tools/fec_s10_forward.py score data/eval/fec_v1/s10_forward/rows_2026-10-24*.csv.gz

THE RULE (as fixed; nothing here may loosen it)
  rows      pairs of rd1 and rd3 rows for the same city, target date and
            checkpoint, both recorded live, of the declared versions (a refit
            of either is a new pair and a new count)
  winner    the venue winner of v_checkpoint_outcome for the city-day (one
            distinct winner_band_id; fact_band_outcome's settled band is
            exported beside it and must agree)
  scores    log loss on the full ladder (p on the winner floored at 1e-6; a
            winner missing from a ladder scores the floor), top-1
  coverage  q10_c..q90_c against the label the model is trained and its
            interval calibrated on: derived_city_day_features.max_c, the whole
            day (fixed 4 Oct, before any rd3 forward score was computed: on
            602 rd1 pairs of 30 Sep - 2 Oct it differs from the settlement's
            observed_max_c by more than 0.05 C on 108; that value is exported
            and reported beside it, never used for the check)
  looks     the first 20 settled dates, and only if the pooled interval there
            spans 0, the first 40. A look is a fixed set of dates, so running
            this before, between or after the looks cannot change a verdict;
            below 20 dates it reports counts and no comparison at all
  accepted  1. pooled gain (rd1 minus rd3) 90% date-clustered interval above 0
            2. no checkpoint's 90% interval wholly below 0
            3. rd3's top-1 not below rd1's beyond rd1's Wilson 95% interval
            4. rd3's 80% coverage within 0.75-0.88
  rejected  the pooled interval wholly below 0, at either look
  otherwise look 1 with the interval spanning 0: continue to look 2; look 1
            with it above 0 but another check failing: not accepted (final);
            look 2 with it spanning 0: insufficient evidence (final)

A date is settled once it is at least SETTLE_DAYS days before the export date
(F1, 30 Sep - 2 Oct: every market on the page within 13.7 h of its local day
end). A pair on a settled date without exactly one winner is left out and
counted. The bootstrap and the Wilson interval are tools/fec_same_day.py's.

The market (v_checkpoint_outcome's, only when complete and decided within 15
minutes of rd1's row) is reported for information. It is not part of the rule.
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fec_same_day import cluster_boot, wilson  # noqa: E402

CONTRACT = "fec-v1"
INCUMBENT = "rd1:2026-09-25:f5372ebb05"
CHALLENGER = "rd3:2026-09-25:555719d4a1"
SETTLE_DAYS = 3
LOOKS = (20, 40)
FLOOR = 1e-6
MARKET_SYNC_S = 900
COLUMNS = ["city_key", "unit", "target_date", "checkpoint", "model_hour", "inc_version", "ch_version",
           "n_winners", "fb_agrees", "p_inc", "p_ch", "hit_inc", "hit_ch",
           "q10_inc", "q90_inc", "q10_ch", "q90_ch", "label_max_c", "settled_max_c",
           "market_synced", "market_p", "market_hit"]


# ---------------------------------------------------------------------------
# the export
# ---------------------------------------------------------------------------
def export_sql(export_date, inc=INCUMBENT, ch=CHALLENGER, date_from=None, date_to=None):
    """The statement whose single text cell is the rows file. Bounds are
    literals; every nullable field is coalesced, so concat_ws never drops one."""
    lo = f"and a.target_date >= date '{date_from}'" if date_from else ""
    hi = f"and a.target_date <= date '{date_to}'" if date_to else ""
    return f"""-- tools/fec_s10_forward.py sql --export-date {export_date}{' --from ' + date_from if date_from else ''}{' --to ' + date_to if date_to else ''}
with a as (select * from public.s10_shadow_checkpoints where model_version = '{inc}'),
b as (select * from public.s10_shadow_checkpoints where model_version = '{ch}'),
pairs as (
  select a.city_key, a.target_date, a.checkpoint, a.model_hour, a.decided_at as a_at,
         a.probs as a_probs, b.probs as b_probs, a.top_band_id as a_top, b.top_band_id as b_top,
         a.q10_c as a_q10, a.q90_c as a_q90, b.q10_c as b_q10, b.q90_c as b_q90
    from a join b using (city_key, target_date, checkpoint)
   where a.target_date <= date '{export_date}' - {SETTLE_DAYS} {lo} {hi}),
w as (select city_key, target_date, count(distinct winner_band_id) as n_w, min(winner_band_id) as winner
        from public.v_checkpoint_outcome where ladder_has_winner group by 1, 2),
fb as (select city_key, for_date, min(band_id::text) filter (where settled_yes) as fb_winner,
              max(observed_max_c) as settled_max_c
         from public.fact_band_outcome group by 1, 2),
mk as (select distinct on (p.city_key, p.target_date, p.checkpoint) p.city_key, p.target_date, p.checkpoint,
              v.market_complete, v.market_prob_on_winner, v.market_hit,
              abs(extract(epoch from v.decided_at - p.a_at)) as sync_s
         from pairs p join public.v_checkpoint_outcome v using (city_key, target_date, checkpoint)
        order by p.city_key, p.target_date, p.checkpoint, abs(extract(epoch from v.decided_at - p.a_at))),
r as (
  select p.*, c.unit, coalesce(w.n_w, 0) as n_w, case when w.n_w = 1 then w.winner end as winner,
         fb.fb_winner, fb.settled_max_c, d.max_c as label_max_c, mk.market_complete, mk.market_prob_on_winner,
         mk.market_hit, mk.sync_s
    from pairs p
    join public.cities c on c.city_key = p.city_key
    left join w on w.city_key = p.city_key and w.target_date = p.target_date
    left join fb on fb.city_key = p.city_key and fb.for_date = p.target_date
    left join public.derived_city_day_features d on d.city_key = p.city_key and d.obs_date = p.target_date
    left join mk on mk.city_key = p.city_key and mk.target_date = p.target_date and mk.checkpoint = p.checkpoint)
select '{','.join(COLUMNS)}' || chr(10) ||
  coalesce(string_agg(concat_ws(',', city_key, coalesce(unit, ''), target_date, checkpoint, model_hour,
     '{inc}', '{ch}', n_w,
     coalesce(((winner is not null and fb_winner is not null and winner = fb_winner)::int)::text, ''),
     coalesce(case when winner is not null then coalesce((a_probs ->> winner)::numeric, 0)::text end, ''),
     coalesce(case when winner is not null then coalesce((b_probs ->> winner)::numeric, 0)::text end, ''),
     coalesce(case when winner is not null then ((a_top = winner)::int)::text end, ''),
     coalesce(case when winner is not null then ((b_top = winner)::int)::text end, ''),
     coalesce(a_q10::text, ''), coalesce(a_q90::text, ''), coalesce(b_q10::text, ''), coalesce(b_q90::text, ''),
     coalesce(label_max_c::text, ''), coalesce(settled_max_c::text, ''),
     ((coalesce(market_complete, false) and coalesce(sync_s, 1e9) <= {MARKET_SYNC_S})::int)::text,
     coalesce(market_prob_on_winner::text, ''), coalesce((market_hit::int)::text, '')),
   chr(10) order by target_date, city_key, checkpoint), '') as csv
from r;"""


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def num(v):
    return None if v in ("", None) else float(v)


def load(paths, inc=INCUMBENT, ch=CHALLENGER):
    """The rows of every part, each pair once. A row without every column, of
    another version, or a pair exported twice with different values is refused."""
    rows, seen = [], {}
    for path in paths:
        with gzip.open(path, "rt") as f:
            for i, r in enumerate(csv.DictReader(f)):
                if None in r or None in r.values() or set(r) != set(COLUMNS):
                    raise ValueError(f"{path}: data line {i + 1} has not every column")
                if (r["inc_version"], r["ch_version"]) != (inc, ch):
                    raise ValueError(f"{path}: data line {i + 1} is the pair {r['inc_version']} / "
                                     f"{r['ch_version']}, not {inc} / {ch}")
                key = (r["city_key"], r["target_date"], r["checkpoint"])
                if key in seen:
                    if seen[key] != r:
                        raise ValueError(f"{key} exported twice with different values")
                    continue
                seen[key] = r
                rows.append(r)
    rows.sort(key=lambda r: (r["target_date"], r["city_key"], r["checkpoint"]))
    return rows


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def ll(p):
    return -math.log(max(p, FLOOR))


def block(rows):
    """Scores of one look's scored rows."""
    n = len(rows)
    if not n:
        return {"n": 0}
    out = {"n": n, "dates": len({r["target_date"] for r in rows}), "cities": len({r["city_key"] for r in rows})}
    for m in ("inc", "ch"):
        hits = sum(int(r[f"hit_{m}"]) for r in rows)
        cov = [r for r in rows if r["label_max_c"] != "" and r[f"q10_{m}"] != "" and r[f"q90_{m}"] != ""]
        inside = sum(num(r[f"q10_{m}"]) <= num(r["label_max_c"]) <= num(r[f"q90_{m}"]) for r in cov)
        out[m] = {"logloss": round(sum(ll(num(r[f"p_{m}"])) for r in rows) / n, 4),
                  "top1": round(hits / n, 4), "top1_wilson95": wilson(hits, n),
                  "cover80": round(inside / len(cov), 4) if cov else None, "cover80_n": len(cov),
                  "width80_c": round(sum(num(r[f"q90_{m}"]) - num(r[f"q10_{m}"]) for r in cov) / len(cov), 3)
                  if cov else None}
        sc = [r for r in cov if r["settled_max_c"] != ""]
        out[m]["cover80_vs_settlement"] = (round(sum(num(r[f"q10_{m}"]) <= num(r["settled_max_c"]) <= num(r[f"q90_{m}"])
                                                     for r in sc) / len(sc), 4) if sc else None)
    out["gain_rd1_minus_rd3"] = cluster_boot([(r["target_date"], ll(num(r["p_inc"])) - ll(num(r["p_ch"])))
                                              for r in rows])
    mk = [r for r in rows if r["market_synced"] == "1" and r["market_p"] != ""]
    if mk:
        out["market_information_only"] = {
            "n": len(mk), "dates": len({r["target_date"] for r in mk}),
            "logloss": {"market": round(sum(ll(num(r["market_p"])) for r in mk) / len(mk), 4),
                        "rd1": round(sum(ll(num(r["p_inc"])) for r in mk) / len(mk), 4),
                        "rd3": round(sum(ll(num(r["p_ch"])) for r in mk) / len(mk), 4)},
            "top1": {"market": round(sum(int(r["market_hit"] or 0) for r in mk) / len(mk), 4),
                     "rd1": round(sum(int(r["hit_inc"]) for r in mk) / len(mk), 4),
                     "rd3": round(sum(int(r["hit_ch"]) for r in mk) / len(mk), 4)},
            "rd3_minus_market": cluster_boot([(r["target_date"], ll(num(r["market_p"])) - ll(num(r["p_ch"])))
                                              for r in mk])}
    return out


def checks(pooled, by_checkpoint):
    g = pooled["gain_rd1_minus_rd3"]
    return {
        "gain_ci90_above_0": g["ci90"][0] > 0,
        "no_checkpoint_ci90_below_0": all(b["gain_rd1_minus_rd3"]["ci90"][1] >= 0
                                          for b in by_checkpoint.values() if b.get("n")),
        "top1_not_lower_beyond_wilson": pooled["ch"]["top1"] >= pooled["inc"]["top1_wilson95"][0],
        "cover80_in_0.75_0.88": pooled["ch"]["cover80"] is not None and 0.75 <= pooled["ch"]["cover80"] <= 0.88,
    }


def look(rows, dates):
    rs = [r for r in rows if r["target_date"] in dates]
    by_cp = {cp: block([r for r in rs if r["checkpoint"] == cp]) for cp in sorted({r["checkpoint"] for r in rs})}
    pooled = block(rs)
    return {"dates": sorted(dates), "pooled": pooled, "by_checkpoint": by_cp, "checks": checks(pooled, by_cp)}


def decide(result, which):
    """(verdict, final) for look 1 or 2 under the rule."""
    c, g = result["checks"], result["pooled"]["gain_rd1_minus_rd3"]
    if all(c.values()):
        return "accepted", True
    if g["ci90"][1] < 0:
        return "rejected", True
    if not c["gain_ci90_above_0"]:
        return ("continue to look 2", False) if which == 1 else ("insufficient evidence", True)
    return "not accepted", True


def score(rows, export_date=None):
    settled = [r for r in rows]
    scored = [r for r in settled if r["n_winners"] == "1" and r["p_inc"] != "" and r["p_ch"] != ""]
    dates = sorted({r["target_date"] for r in scored})
    rep = {"contract": CONTRACT, "rule": "docs/CHALLENGER_C_PREREG.md, Forward shadow",
           "pair": {"incumbent": INCUMBENT, "challenger": CHALLENGER}, "export_date": export_date,
           "settle_days": SETTLE_DAYS, "looks_at_dates": list(LOOKS),
           "counts": {"pairs_on_settled_dates": len(settled), "scored": len(scored),
                      "left_out_not_one_winner": len(settled) - len(scored),
                      "winner_disagrees_with_fact_band_outcome": sum(1 for r in scored if r["fb_agrees"] == "0"),
                      "settled_dates": len(dates), "first_date": dates[0] if dates else None,
                      "last_date": dates[-1] if dates else None,
                      "pairs_by_date": {d: sum(1 for r in scored if r["target_date"] == d) for d in dates}}}
    if rep["counts"]["winner_disagrees_with_fact_band_outcome"]:
        rep["verdict"] = "stopped: a winner disagrees with fact_band_outcome; investigate before any look"
        return rep
    if len(dates) < LOOKS[0]:
        rep["verdict"] = f"no look yet: {len(dates)} of {LOOKS[0]} settled dates (no comparison is computed before)"
        return rep
    one = look(scored, set(dates[:LOOKS[0]]))
    v1, final = decide(one, 1)
    rep["look_1"] = dict(one, verdict=v1, final=final)
    if final:
        rep["verdict"] = v1
        return rep
    if len(dates) < LOOKS[1]:
        rep["verdict"] = f"look 1: continue; look 2 at {LOOKS[1]} settled dates ({len(dates)} now)"
        return rep
    two = look(scored, set(dates[:LOOKS[1]]))
    v2, _ = decide(two, 2)
    rep["look_2"] = dict(two, verdict=v2, final=True)
    rep["verdict"] = v2
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sql")
    s.add_argument("--export-date", required=True)
    s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to")
    c = sub.add_parser("score")
    c.add_argument("paths", nargs="+")
    c.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "sql":
        dt.date.fromisoformat(a.export_date)
        print(export_sql(a.export_date, date_from=a.date_from, date_to=a.date_to))
        return
    export_date = os.path.basename(a.paths[0]).split("_")[1].split(".")[0] if "_" in os.path.basename(a.paths[0]) else None
    rep = score(load(a.paths), export_date)
    out = a.out or os.path.join(os.path.dirname(a.paths[0]), f"report_{export_date}.json")
    with open(out, "w") as f:
        json.dump(rep, f, indent=1, sort_keys=True)
    print(json.dumps({k: rep[k] for k in ("verdict", "counts")}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
