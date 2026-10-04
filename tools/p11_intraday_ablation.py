"""P1.1: what the intraday update does to the engine's ladder, component by component.

On identical city-days and checkpoints (the engine's first capture of each,
v_checkpoint_calls.first_call), every same-day ladder is recomputed with the
engine's own integrator (probability_engine.compute_band_probabilities) from
recorded inputs only, switching one component at a time:

  served       the engine's same-day centre and width, the checkpoint floor
               (recomputed: the check that the replay is the engine)
  da_carried   the day-ahead call's centre and width, no floor - the ladder
               the engine published before the day began, on today's bands
  da_floor     the day-ahead centre and width, the checkpoint floor - what
               the day-ahead forecast says once the observed floor is known
  l0c_daw      the same-day centre with the day-ahead width, the floor
  dac_l0w      the day-ahead centre with the same-day width, the floor
  s10          S10 rd1's recorded ladder at the same checkpoint (no recompute)

and the market's probability on the winner where its book was whole.

The day-ahead centre and width are v_city_hit_history's (the last pricing
before the local day began, P4.6); the same-day ones are the checkpoint row's
(prediction_checkpoints.centre_c, sigma_c). The floor is the checkpoint's
running_max_c. q (the measurement layer, cities.observation_q_down/_up) is the
value the engine read at the decision: refresh_observation_trust() refits it in
pipeline_daily between 05:05Z and 05:09Z, and data/mirror/cities/cities-<D>.csv.gz
is the table at 02:43Z on D, so a decision before 05:07Z on D read D's snapshot
and one after read D+1's. Every recomputed variant uses that same q.

Scores: log loss on the winner (p floored at 1e-6, as fec-v1) and top-1.
Intervals (fec-v1 section 5): log loss and its paired differences by the
date-clustered bootstrap (tools/fec_same_day.cluster_boot, seed 11, 1,000
resamples); top-1 by its Wilson 95% interval. The market is compared on the
rows where its book was whole, with every variant scored on those same rows
(fec-v1 section 6), as paired differences. Rows, dates, cities and the rows
left out, by reason, are reported per checkpoint.

Inputs: data/eval/p11/rows_<date>.json.gz, one per target date, each the
result of EXPORT_SQL below with :D the date, run through the Supabase SQL tool
(the tables are the service role's).

  python3 tools/p11_intraday_ablation.py score data/eval/p11/rows_*.json.gz \
      --out data/eval/p11/report.json
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
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import probability_engine as pe            # noqa: E402
from fec_same_day import cluster_boot, wilson   # noqa: E402

FLOOR = 1e-6
Q_REFIT_UTC = dt.time(5, 7)           # refresh_observation_trust, inside pipeline_daily
MIRROR = os.path.join(HERE, "..", "data", "mirror", "cities")
_snapshots = {}


def snapshots(mirror=MIRROR):
    if not _snapshots:
        for p in glob.glob(os.path.join(mirror, "cities-*.csv.gz")):
            d = os.path.basename(p)[len("cities-"):len("cities-") + 10]
            with gzip.open(p, "rt") as f:
                _snapshots[d] = {r["city_key"]: (r.get("observation_q_down"), r.get("observation_q_up"))
                                 for r in csv.DictReader(f)}
    return _snapshots


def q_at(r):
    """(q_down, q_up) as the engine read them at the decision, or None."""
    t = dt.datetime.fromisoformat(r["decided_at"].replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    day = t.date() if t.time() < Q_REFIT_UTC else t.date() + dt.timedelta(days=1)
    q = snapshots().get(day.isoformat(), {}).get(r["city"])
    if not q or q[0] in (None, "") or q[1] in (None, ""):
        return None
    return float(q[0]), float(q[1])
ORDER = ["morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h"]
VARIANTS = ["served", "da_carried", "da_floor", "l0c_daw", "dac_l0w", "s10"]
US = {"atlanta", "austin", "chicago", "dallas", "denver", "houston", "los_angeles", "miami", "nyc",
      "san_francisco", "seattle"}

EXPORT_SQL = """
with eng as (
  select p.checkpoint_id, p.city_key, p.target_date, p.checkpoint, p.decided_at, p.centre_c, p.sigma_c, p.running_max_c,
         p.probs, f.winner_band_id, f.market_prob_on_winner, f.market_hit,
         row_number() over (partition by p.city_key, p.target_date, p.checkpoint order by p.decided_at, p.checkpoint_id) as rn
    from public.prediction_checkpoints p
    join public.fact_checkpoint_outcome f using (checkpoint_id)
   where p.target_date = date ':D' and p.checkpoint <> 'd1_eve' and f.ladder_has_winner),
s10 as (
  select distinct on (city_key, target_date, checkpoint) city_key, target_date, checkpoint, probs as s10_probs
    from public.s10_shadow_checkpoints
   where target_date = date ':D' and model_version like 'rd1:%'
   order by city_key, target_date, checkpoint, decided_at)
select json_agg(json_build_object(
         'city', e.city_key, 'date', e.target_date, 'cp', e.checkpoint, 'decided_at', e.decided_at,
         'unit', c.unit, 'q_down', c.observation_q_down, 'q_up', c.observation_q_up,
         'centre', e.centre_c, 'sigma', e.sigma_c, 'floor', e.running_max_c, 'probs', e.probs,
         'winner', e.winner_band_id, 'mkt_p', e.market_prob_on_winner, 'mkt_hit', e.market_hit,
         'da_centre', h.centre_c, 'da_sigma', h.sigma_c, 'da_at', h.called_at, 'obs', h.observed_max_c,
         's10', s.s10_probs,
         'bands', (select json_agg(json_build_object('band_id', b.band_id::text, 'band_lo', b.band_lo, 'band_hi', b.band_hi,
                                                     'open_low', b.open_low, 'open_high', b.open_high) order by b.band_lo nulls first)
                     from public.bands b where b.band_id::text in (select jsonb_object_keys(e.probs))))
         order by e.city_key, e.checkpoint) as rows
  from eng e
  join public.cities c on c.city_key = e.city_key
  left join public.v_city_hit_history h on h.city_key = e.city_key and h.for_date = e.target_date
  left join s10 s on s.city_key = e.city_key and s.target_date = e.target_date and s.checkpoint = e.checkpoint
 where e.rn = 1
"""


def load(paths):
    rows = []
    for p in paths:
        with gzip.open(p, "rt") as f:
            rows += json.load(f)
    return rows


def _f(v):
    return None if v is None else float(v)


def ladder(r, centre, sigma, floor):
    q = q_at(r)
    if q is None:
        q = (_f(r["q_down"]), _f(r["q_up"])) if r.get("q_down") is not None and r.get("q_up") is not None \
            else (pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP)
    q_down, q_up = q
    if floor is None:
        q_down = q_up = 0.0
    out = pe.compute_band_probabilities(centre, sigma, r["unit"], r["bands"], floor, q_down, q_up)
    return {b: pe.clamp_prob(p) for b, p in out}


def variants(r):
    """{variant: {band_id: p}} for the variants this row has inputs for."""
    c, s, fl = _f(r["centre"]), _f(r["sigma"]), _f(r["floor"])
    dc, ds = _f(r.get("da_centre")), _f(r.get("da_sigma"))
    out = {"served": ladder(r, c, s, fl)}
    if dc is not None and ds is not None:
        out["da_carried"] = ladder(r, dc, ds, None)
        out["da_floor"] = ladder(r, dc, ds, fl)
        out["l0c_daw"] = ladder(r, c, ds, fl)
        out["dac_l0w"] = ladder(r, dc, s, fl)
    if r.get("s10"):
        out["s10"] = {b: float(p) for b, p in r["s10"].items()}
    return out


def score(probs, winner):
    p = probs.get(winner, 0.0)
    top = max(probs.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return -math.log(max(p, FLOOR)), 1.0 if top == winner else 0.0


def replay_check(rows):
    worst, off = 0.0, 0
    for r in rows:
        got = ladder(r, _f(r["centre"]), _f(r["sigma"]), _f(r["floor"]))
        d = max(abs(round(got[b], 6) - float(r["probs"][b])) for b in got)
        worst = max(worst, d)
        off += d > 1e-3
    return {"rows": len(rows), "max_abs_diff": round(worst, 6), "rows_off_by_1e-3": off}


def near_zero_causes(r, probs):
    """Why the served ladder put almost nothing on the winner."""
    fl = _f(r["floor"])
    order = [b["band_id"] for b in r["bands"]]
    w = order.index(r["winner"]) if r["winner"] in order else None
    if fl is not None and w is not None:
        lad, i = pe.floor_bucket(fl, r["unit"], r["bands"])
        if i is not None:
            fb = lad[i]["band_id"]
            if order.index(fb) > w:
                return "floor_above_winner"
    obs, c, s = _f(r.get("obs")), _f(r["centre"]), _f(r["sigma"])
    if obs is not None and s:
        z = abs(obs - c) / s
        if z >= 2.5:
            return "centre_off_by_2.5_sigma_or_more"
        return "centre_within_2.5_sigma"
    return "unknown"


def run(rows):
    report = {"replay": replay_check(rows), "by_checkpoint": {}, "near_zero_us": {}}
    per = defaultdict(list)
    for r in rows:
        per[r["cp"]].append(r)
    for cp in ORDER:
        rs = per.get(cp, [])
        no_da = [r for r in rs if r.get("da_centre") is None or r.get("da_sigma") is None]
        no_s10 = [r for r in rs if r not in no_da and not r.get("s10")]
        full = [r for r in rs if r not in no_da and r.get("s10")]
        sc = defaultdict(list)          # variant -> [(date, ll, hit, has_market)]
        mkt = []                        # [(index into full, date, ll, hit)]
        for idx, r in enumerate(full):
            v = variants(r)
            for name in VARIANTS:
                ll, hit = score(v[name], r["winner"])
                sc[name].append((r["date"], ll, hit))
            if r.get("mkt_p") is not None:
                mkt.append((idx, r["date"], -math.log(max(float(r["mkt_p"]), FLOOR)),
                            1.0 if r.get("mkt_hit") else 0.0))

        def top1(hits):
            k, n = int(sum(hits)), len(hits)
            return {"rate": round(k / n, 4) if n else None, "hits": k, "n": n, "wilson95": wilson(k, n)}

        out = {"rows": len(rs), "matched": len(full), "dates": len({r["date"] for r in full}),
               "cities": len({r["city"] for r in full}),
               "left_out": {"no_day_ahead_centre_or_width": len(no_da), "no_s10_ladder": len(no_s10)},
               "variants": {}}
        for name in VARIANTS:
            out["variants"][name] = {
                "log_loss": cluster_boot([(d, ll) for d, ll, _ in sc[name]]),
                "top1": top1([h for _, _, h in sc[name]]),
            }
        if mkt:
            idxs = [i for i, *_ in mkt]
            out["market"] = {
                "rows": len(mkt), "left_out_no_whole_book": len(full) - len(mkt),
                "log_loss": cluster_boot([(d, ll) for _, d, ll, _ in mkt]),
                "top1": top1([h for *_, h in mkt]),
                # every variant on the market's rows, and market minus variant, paired
                "variants_on_market_rows": {
                    name: {"log_loss": cluster_boot([(sc[name][i][0], sc[name][i][1]) for i in idxs]),
                           "top1": top1([sc[name][i][2] for i in idxs])} for name in VARIANTS},
                "market_minus_variant_log_loss": {
                    name: cluster_boot([(d, ll - sc[name][i][1]) for i, d, ll, _ in mkt]) for name in VARIANTS},
            }
        # Differences on the same rows, positive = the first is WORSE (higher log loss).
        def diff(a, b):
            return cluster_boot([(sa[0], sa[1] - sb[1]) for sa, sb in zip(sc[a], sc[b])])
        out["differences_log_loss"] = {
            "served_minus_da_carried (the whole update)": diff("served", "da_carried"),
            "served_minus_da_floor (switching centre and width, floor held)": diff("served", "da_floor"),
            "da_floor_minus_da_carried (adding the floor to the day-ahead ladder)": diff("da_floor", "da_carried"),
            "l0c_daw_minus_da_floor (centre switch, day-ahead width)": diff("l0c_daw", "da_floor"),
            "served_minus_l0c_daw (width switch, same-day centre)": diff("served", "l0c_daw"),
            "dac_l0w_minus_da_floor (width switch, day-ahead centre)": diff("dac_l0w", "da_floor"),
            "served_minus_dac_l0w (centre switch, same-day width)": diff("served", "dac_l0w"),
            "served_minus_s10": diff("served", "s10"),
        }
        report["by_checkpoint"][cp] = out
    # The near-zero US calls, as served (24 Sep - 2 Oct was where 58 were counted).
    causes = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["city"] not in US:
            continue
        served = ladder(r, _f(r["centre"]), _f(r["sigma"]), _f(r["floor"]))
        if served.get(r["winner"], 0.0) < 0.01:
            causes[r["cp"]][near_zero_causes(r, served)] += 1
    report["near_zero_us"] = {cp: dict(v) for cp, v in causes.items()}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sql", help="print the export statement for one date")
    s.add_argument("--date", required=True)
    s2 = sub.add_parser("score", help="score the exported rows")
    s2.add_argument("paths", nargs="+")
    s2.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "sql":
        print(EXPORT_SQL.replace(":D", a.date))
        return 0
    rep = run(load(sorted(a.paths)))
    text = json.dumps(rep, indent=1, sort_keys=True)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
