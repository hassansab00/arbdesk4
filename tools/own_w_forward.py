#!/usr/bin/env python3
"""The station correction learned from the forecasts it is applied to, with
skill weights, in shadow (`own_w:v1`, docs/OWN_W_PREREG.md): the learner under
Rule 11 and its forward record. Research: nothing here prices, trades or
writes to the database.

WHY. P3.9 learns each model's station bias on the Previous Runs record and
applies it to each model's latest run, then averages the corrected models
equally. tools/station_weights_study.py (15 Nov) measured that learning the
bias from the served forecasts' own errors and weighting each model by its
skill was nearer the station, and gained log loss on the served calls. Those
dates chose the recipe. This fixes it and scores it on later dates.

THE LEARNER, own_w:v1. For each night (a UTC date) and each lead 0, 1, 2:

  errors   truth - forecast for every forward row of that lead (weather_
           forecast_models, source open-meteo-models-current, the newest run
           of each run date) whose target day is 2 to 45 days before the night.
           The truth is the station's whole-day maximum, as in the study.
  target   the study's fit (station_weights_study.shrunk, which is
           station_correction.fit's shrinkage): a bias per city and model
           shrunk to the model's pool, and its mean squared error shrunk the
           same way. A model's skill at a city is its precision (1 / that
           error) over the mean precision of the models with MIN_N city-days
           there. Where only some have that many, those share out the pool
           skill of the same models and the rest keep their pool's skill: a
           city says nothing about a model it has not measured.
  Rule 11  prior: the model's pool for the bias, and the pool's skill for the
           weight. Minimum sample: MIN_N city-days before a cell's bias or
           skill leaves its pool. Bounds: |bias| <= 3.0 C; skill between REL_LO
           and REL_HI, so no model counts more than four times another. Maximum
           step a night: 0.25 C for a bias, REL_MAX_STEP for a skill, from the
           cell the night before (a cell with no night before it takes its
           target, as station_correction.fit does). Version: VERSION, the
           night and a hash of every cell.
  centre   the sum of skill x (model maximum + bias) over the sum of skill,
           with MIN_SOURCES models or more.

The nights run in order from STATE_FROM, so every cell's steps are the ones
a nightly job would have taken. A night reads only target days two or more
days before it.

THE FORWARD RECORD replays the served first calls from the mirror, as the
study did: each model's newest run made no later than the call, the bias in
force that night (derived_station_correction) for the combination as it is,
and own_w:v1's state of the call's UTC date. Both centres use the same runs,
so their difference is the bias and the weights alone.

  morning  the combination as it is against own_w:v1, each at the stored
           station width (derived_corrected_forecast.width_c)
  evening  the served ladder against the served centre moved by half of
           (own_w:v1 - the combination), at the served width: P3.9 is half of
           that centre, the station model the other half

    python tools/own_w_forward.py --design data/eval/own_w    # the study's dates, for the pre-registration
    python tools/own_w_forward.py --count                     # counts only: no score
    python tools/own_w_forward.py --look 1 --out data/eval/own_w
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import station_correction as sc  # noqa: E402
import probability_engine as pe  # noqa: E402
import station_weights_study as W  # noqa: E402  (the study's reader, fit and interval, unchanged)

VERSION = "own_w:v1"
LEADS = (0, 1, 2)
GAP_DAYS = 2
MIN_N = sc.MIN_N
BIAS_MAX_STEP = sc.MAX_STEP_C
REL_LO, REL_HI = 0.5, 2.0
REL_MAX_STEP = 0.10
STATE_FROM = "2026-09-26"            # the first night with a target day two days behind it (rows begin 24 Sep)
DEV_FROM, DEV_TO = "2026-10-14", "2026-11-14"   # the study's replayed dates
FIRST_FORWARD_DATE = "2026-11-17"    # its evening-before calls are made on 16 Nov, after this was written
LOOKS = {1: 20, 2: 40}               # valid dates
MIN_CALLS = 20                       # scored calls a checkpoint for a date to be valid
SETTLE_DAYS = 3
CHECKPOINTS = ("d1_eve", "morning")


def _clip(x, lo, hi):
    return min(hi, max(lo, x))


def target(errors):
    """Tonight's unstepped state from {(city, model): [truth - forecast]}."""
    bias, pooled, mse, pmse = W.shrunk(errors)
    n = {k: len(v) for k, v in errors.items()}
    pprec = {m: 1.0 / max(pmse[m], 1e-6) for m in pooled}
    pmean = sum(pprec.values()) / len(pprec)
    out = {"bias": {}, "rel": {}, "n": n, "pool_bias": dict(pooled),
           "pool_rel": {m: _clip(pprec[m] / pmean, REL_LO, REL_HI) for m in pooled}}
    for c in sorted({c for c, _m in errors}):
        full = [m for m in pooled if n.get((c, m), 0) >= MIN_N]
        prec = {m: 1.0 / max(mse[(c, m)], 1e-6) for m in full}
        mean = sum(prec.values()) / len(full) if full else None
        # the models measured here share out the pool skill of those same models
        scale = 1.0 if len(full) == len(pooled) else sum(pprec[m] for m in full) / max(len(full), 1) / pmean
        for m in pooled:
            out["bias"][(c, m)] = bias.get((c, m), pooled[m])
            out["rel"][(c, m)] = _clip(prec[m] / mean * scale, REL_LO, REL_HI) if m in prec else out["pool_rel"][m]
    return out


def step(tgt, prev):
    """Tonight's state: each cell moves from last night's by no more than its
    step. A cell with no night before it takes its target; a cell with no data
    tonight keeps last night's value."""
    if prev is None or tgt is None:
        return tgt if prev is None else prev
    out = dict(tgt, bias=dict(prev["bias"]), rel=dict(prev["rel"]), held={"bias": 0, "rel": 0})
    for key, limit in (("bias", BIAS_MAX_STEP), ("rel", REL_MAX_STEP)):
        for k, v in tgt[key].items():
            p = prev[key].get(k)
            out[key][k] = v if p is None else _clip(v, p - limit, p + limit)
            out["held"][key] += int(p is not None and out[key][k] != v)
    return out


def centre(city, v, state):
    """own_w:v1's centre from the seven maxima `v` (None where a model has no
    run), or None with fewer than MIN_SOURCES."""
    xs, ws = [], []
    for m, x in enumerate(v):
        if x is None or m not in state["pool_bias"]:
            continue
        xs.append(x + state["bias"].get((city, m), state["pool_bias"][m]))
        ws.append(state["rel"].get((city, m), state["pool_rel"][m]))
    if len(xs) < sc.MIN_SOURCES:
        return None
    return float(np.dot(xs, ws) / sum(ws))


def version_of(states, night):
    body = json.dumps({"night": night, "min_n": MIN_N, "bias_step": BIAS_MAX_STEP, "rel": [REL_LO, REL_HI, REL_MAX_STEP],
                       "cells": [[lead, c, m, round(s["bias"][(c, m)], 6), round(s["rel"][(c, m)], 6)]
                                 for lead, s in sorted(states.items()) if s for (c, m) in sorted(s["bias"])]})
    return f"{VERSION}:{night}:{hashlib.sha256(body.encode()).hexdigest()[:10]}"


class States:
    """own_w:v1's state on every night from STATE_FROM, in order."""

    def __init__(self, runs, truth):
        self.truth, self.nights = truth, {}
        self.rows = W.Fits(runs, truth).rows            # lead -> [(city, target, [7])], one per run date
        self.last = None

    def errors(self, night, lead):
        day = dt.date.fromisoformat(night)
        lo = (day - dt.timedelta(days=sc.WINDOW_DAYS)).isoformat()
        hi = (day - dt.timedelta(days=GAP_DAYS)).isoformat()
        out = collections.defaultdict(list)
        for c, d, v in self.rows[lead]:
            if lo <= d <= hi and (c, d) in self.truth:
                for m, x in enumerate(v):
                    if x is not None:
                        out[(c, m)].append(self.truth[(c, d)] - x)
        return out

    def at(self, night):
        """{lead: state} on `night`; None for a lead with no data yet."""
        if night < STATE_FROM:
            return None
        day = dt.date.fromisoformat(self.last or STATE_FROM)
        if self.last is not None:
            day += dt.timedelta(days=1)
        while day.isoformat() <= night:
            n, before = day.isoformat(), self.nights.get(self.last) or {}
            self.nights[n] = {}
            for lead in LEADS:
                e = self.errors(n, lead)
                self.nights[n][lead] = step(target(e) if e else None, before.get(lead))
            self.last = n
            day += dt.timedelta(days=1)
        return self.nights[night]


def _bands():
    out = {}
    recent = csv.DictReader(gzip.open(os.path.join(ROOT, "data", "training", "market_definitions",
                                                   "bands-recent.csv.gz"), "rt"))
    for r in list(W.read("bands")) + list(recent):
        out[r["band_id"]] = {"band_id": r["band_id"], "band_index": int(r["band_index"]),
                             "band_lo": float(r["band_lo"]) if r["band_lo"] else None,
                             "band_hi": float(r["band_hi"]) if r["band_hi"] else None,
                             "open_low": r["open_low"] in W.TRUE, "open_high": r["open_high"] in W.TRUE}
    return out


def _score(probs, win):
    p = max(probs.get(win, 0.0) / sum(probs.values()), 1e-6)
    return -math.log(p), int(sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] == win)


class Record:
    """The mirror, read once."""

    def __init__(self):
        self.cities = W.cities()
        self.truth = W.station_truth(self.cities)
        self.runs = W.forward_runs()
        self.states = States(self.runs, self.truth)
        self.study = W.Fits(self.runs, self.truth)       # the study's recipe: no step, no skill bound
        self.prod = collections.defaultdict(dict)
        for r in W.read("derived_station_correction"):
            self.prod[r["as_of"]][(r["city_key"], r["source"], int(r["lead_days"]))] = float(r["bias_c"])
        self.as_ofs = sorted(self.prod)

    def calls(self, first_date, last_date=None):
        """Every scored first call on these target dates, each priced three ways."""
        bands, corrected, first = _bands(), collections.defaultdict(list), {}
        for r in W.read("derived_corrected_forecast"):
            corrected[(r["city_key"], r["for_date"])].append(r)
        for r in W.read("prediction_checkpoints"):
            k = (r["city_key"], r["target_date"], r["checkpoint"])
            if (r["checkpoint"] in CHECKPOINTS and r["target_date"] >= first_date
                    and (last_date is None or r["target_date"] <= last_date)
                    and (k not in first or (r["decided_at"], r["checkpoint_id"])
                         < (first[k]["decided_at"], first[k]["checkpoint_id"]))):
                first[k] = r
        outcome = {r["checkpoint_id"]: r for r in W.read("fact_checkpoint_outcome")}
        out, left = [], collections.Counter()
        for (c, d, cp), r in first.items():
            f = outcome.get(r["checkpoint_id"])
            if not f or not f["settled_at"] or not f["winner_band_id"] or f["ladder_has_winner"] not in W.TRUE:
                left[f"{cp}:not_graded"] += 1
                continue
            served = json.loads(r["probs"])
            bs = sorted((bands[b] for b in served if b in bands), key=lambda b: b["band_index"])
            live = [z for z in corrected.get((c, d), []) if W.ts(z["computed_at"]) <= W.ts(r["decided_at"])]
            if len(bs) != len(served) or not live or c not in self.cities:
                left[f"{cp}:no_ladder_or_corrected_row"] += 1
                continue
            st = max(live, key=lambda z: z["computed_at"])
            at, night = W.ts(r["decided_at"]), r["decided_at"][:10]
            known, table = W.newest(self.runs.get((c, d), {}), before=at), W.in_force(self.prod, self.as_ofs, night)
            states = self.states.at(night)
            state = (states or {}).get(known[0]) if known else None
            got = W.centres(c, known[0], known[1], table, self.study.at(night, known[0])) if known and table else None
            mine = centre(c, known[1], state) if state else None
            if got is None or mine is None:
                left[f"{cp}:no_runs_or_no_fit"] += 1
                continue
            floor = float(r["running_max_c"]) if r["running_max_c"] else None
            q = ((float(self.cities[c]["observation_q_down"] or 0), float(self.cities[c]["observation_q_up"] or 0))
                 if floor is not None else (0.0, 0.0))

            def lad(mu, sigma):
                return {b: max(1e-6, x) for b, x in pe.compute_band_probabilities(
                    mu, sigma, self.cities[c]["unit"], bs, floor_c=floor, q_down=q[0], q_up=q[1])}
            win = f["winner_band_id"]
            whole = f["market_complete"] in W.TRUE and f["log_loss_market"] not in ("", None)
            row = {"city": c, "date": d, "cp": cp, "lead": known[0], "served": _score(served, win),
                   "market": (float(f["log_loss_market"]), int(f["market_hit"] in W.TRUE)) if whole else None,
                   "moved_c": mine - got[0]}
            if cp == "d1_eve":
                if not r["centre_c"] or not r["sigma_c"]:
                    left[f"{cp}:served_without_centre"] += 1
                    continue
                mu, sigma = float(r["centre_c"]), float(r["sigma_c"])
                row["base"] = row["served"]
                row["check"] = abs(_score(lad(mu, sigma), win)[0] - row["served"][0])
                row["own_w_v1"] = _score(lad(mu + 0.5 * (mine - got[0]), sigma), win)
                row["study_own_w"] = _score(lad(mu + 0.5 * (got[2] - got[0]), sigma), win)
            else:
                if not st["width_c"]:
                    left[f"{cp}:no_station_width"] += 1
                    continue
                sigma = float(st["width_c"])
                row["base"] = _score(lad(got[0], sigma), win)
                row["own_w_v1"] = _score(lad(mine, sigma), win)
                row["study_own_w"] = _score(lad(got[2], sigma), win)
            out.append(row)
        return out, dict(left)

    def station_error(self, first_run, last_run):
        """Mean absolute error against the station maximum on the forward
        rows of these run nights, by lead: the combination as it is, own_w:v1."""
        res = []
        for (c, d), by_model in self.runs.items():
            y = self.truth.get((c, d))
            for on in sorted({x[0][:10] for rs in by_model.values() for x in rs}):
                table = W.in_force(self.prod, self.as_ofs, on)
                if y is None or on < first_run or on > last_run or table is None:
                    continue
                lead, v = W.newest(by_model, on=on)
                state = (self.states.at(on) or {}).get(lead)
                got = W.centres(c, lead, v, table, self.study.at(on, lead))
                mine = centre(c, v, state) if state else None
                if got and mine is not None:
                    res.append((on, lead, abs(y - got[0]), abs(y - mine)))
        out = {}
        for lead in LEADS:
            rs = [r for r in res if r[1] == lead]
            if rs:
                out[str(lead)] = {"n": len(rs), "run_nights": len({r[0] for r in rs}),
                                  "mae_combination": round(float(np.mean([r[2] for r in rs])), 4),
                                  "mae_own_w_v1": round(float(np.mean([r[3] for r in rs])), 4),
                                  "gain": W.interval([(r[0], r[2] - r[3]) for r in rs])}
        return out

    def cells(self, first_night, last_night):
        """How often a bound or a step held a cell, over these nights."""
        self.states.at(last_night)
        n = held_b = held_r = at_bound = 0
        for night, by_lead in self.states.nights.items():
            if first_night <= night <= last_night:
                for s in by_lead.values():
                    if s:
                        n += len(s["rel"])
                        at_bound += sum(v in (REL_LO, REL_HI) for v in s["rel"].values())
                        held_b += (s.get("held") or {}).get("bias", 0)
                        held_r += (s.get("held") or {}).get("rel", 0)
        return {"cell_nights": n, "skill_at_a_bound": round(at_bound / n, 4), "bias_held_by_its_step": round(held_b / n, 4),
                "skill_held_by_its_step": round(held_r / n, 4)}


def summary(rows):
    """Each checkpoint's score, and own_w:v1's gain over its base."""
    out = {}
    for cp in CHECKPOINTS:
        rs = [r for r in rows if r["cp"] == cp]
        if not rs:
            continue
        m = [r for r in rs if r["market"]]
        s = {"n": len(rs), "dates": len({r["date"] for r in rs}),
             "first_date": min(r["date"] for r in rs), "last_date": max(r["date"] for r in rs),
             "market": {"n": len(m), "log_loss": round(float(np.mean([r["market"][0] for r in m])), 4),
                        "top1": round(float(np.mean([r["market"][1] for r in m])), 4)} if m else None,
             "mean_absolute_move_c": round(float(np.mean([abs(r["moved_c"]) for r in rs])), 4)}
        for k in ("served", "base", "own_w_v1", "study_own_w"):
            s[k] = {"log_loss": round(float(np.mean([r[k][0] for r in rs])), 4),
                    "top1": round(float(np.mean([r[k][1] for r in rs])), 4)}
        s["gain"] = W.interval([(r["date"], r["base"][0] - r["own_w_v1"][0]) for r in rs])
        s["study_own_w_gain"] = W.interval([(r["date"], r["base"][0] - r["study_own_w"][0]) for r in rs])
        by_city = collections.defaultdict(list)
        for r in rs:
            by_city[r["city"]].append(r["base"][0] - r["own_w_v1"][0])
        s["cities_better"], s["cities"] = int(sum(np.mean(v) > 0 for v in by_city.values())), len(by_city)
        if cp == "d1_eve":
            s["replay_of_served_max_log_loss_difference"] = round(max(r["check"] for r in rs), 6)
        out[cp] = s
    return out


def valid_dates(rows):
    """Target dates with MIN_CALLS scored calls at each checkpoint, settled
    SETTLE_DAYS before the record's last date."""
    n = collections.Counter((r["date"], r["cp"]) for r in rows)
    if not rows:
        return []
    last = dt.date.fromisoformat(max(r["date"] for r in rows))
    return sorted(d for d in {r["date"] for r in rows}
                  if all(n[(d, cp)] >= MIN_CALLS for cp in CHECKPOINTS)
                  and (last - dt.date.fromisoformat(d)).days >= SETTLE_DAYS)


def verdict(k, s):
    """The pre-registered reading: both calls must gain."""
    g = [s[cp]["gain"]["ci90"] for cp in CHECKPOINTS]
    if all(lo > 0 for lo, _hi in g):
        return "accepted as a proposal"
    if any(hi < 0 for _lo, hi in g):
        return "rejected"
    return "insufficient evidence" if k < max(LOOKS) else "insufficient evidence: the test ends"


def design(rec=None):
    """own_w:v1 on the study's dates (they chose the recipe: not evidence)."""
    rec = rec or Record()
    rows, left = rec.calls(DEV_FROM, DEV_TO)
    return {"version": VERSION, "from": DEV_FROM, "to": DEV_TO,
            "rule_11": {"min_n": MIN_N, "k": sc.K, "bias_bound_c": sc.BOUND_C, "bias_max_step_c": BIAS_MAX_STEP,
                        "skill_bounds": [REL_LO, REL_HI], "skill_max_step": REL_MAX_STEP,
                        "window_days": sc.WINDOW_DAYS, "gap_days": GAP_DAYS, "state_from": STATE_FROM},
            "left_out": left, "calls": summary(rows),
            "station_error_by_lead": rec.station_error(W.FORWARD_FROM, DEV_TO),
            "cells": rec.cells(DEV_FROM, DEV_TO),
            "version_on_the_last_night": version_of(rec.states.at(DEV_TO), DEV_TO)}


def count(rec=None):
    """Counts only: no score."""
    rec = rec or Record()
    rows, left = rec.calls(FIRST_FORWARD_DATE)
    dates = valid_dates(rows)
    return {"version": VERSION, "counted_only": "no score", "from": FIRST_FORWARD_DATE,
            "to": max((r["date"] for r in rows), default=None),
            "scored_calls": dict(collections.Counter(r["cp"] for r in rows)), "left_out": left,
            "valid_dates": len(dates),
            "looks": {str(k): {"needs": n, "due": len(dates) >= n} for k, n in LOOKS.items()}}


def look(k, rec=None):
    rec = rec or Record()
    rows, left = rec.calls(FIRST_FORWARD_DATE)
    dates = valid_dates(rows)
    if len(dates) < LOOKS[k]:
        raise SystemExit(f"look {k} is not due: {len(dates)} valid dates of {LOOKS[k]}")
    dates = dates[:LOOKS[k]]
    rows = [r for r in rows if r["date"] in set(dates)]
    s = summary(rows)
    return {"version": VERSION, "look": k, "from": dates[0], "to": dates[-1], "valid_dates": len(dates),
            "left_out": left, "calls": s, "verdict": verdict(k, s),
            "station_error_by_lead": rec.station_error(FIRST_FORWARD_DATE, dates[-1]),
            "cells": rec.cells(FIRST_FORWARD_DATE, dates[-1]),
            "version_on_the_last_night": version_of(rec.states.at(dates[-1]), dates[-1])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--design")
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--look", type=int, choices=sorted(LOOKS))
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.design:
        out, path = design(), os.path.join(args.design, "design_2026-11-15.json")
    elif args.look:
        if not args.out:
            ap.error("--look needs --out")
        out, path = look(args.look), os.path.join(args.out, f"look_{args.look}.json")
    else:
        out, path = count(), (os.path.join(args.out, "counts.json") if args.out else None)
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
