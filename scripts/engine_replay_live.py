"""Replay a day of the live engine's decisions and compare (plan v2 P5.12
acceptance).

The acceptance: the replay's decisions match the live `decisions` rows on
>= 95% of rows, for the same params version. The live engine decides in the
hourly tick (engine_shadow.decide_all) on:

  the ladder and the book   the tick's own prediction_checkpoints row, which
                            each decision names (checkpoint_id)
  S10's ladder              s10_shadow_checkpoints, the remaining-day model
                            the tick ran (its version in the tick's detail)
  the floor                 per city (date, floor, basis, newest reading) and
  the ledgers               each strategy's ledger as read - both recorded in
                            the tick's ingest_log detail, engine.inputs, from
                            27 Sep (#235): v_city_running_max is a view of now
                            and the ledgers change
  learned parameters        the versions each row records; "prior" is none

This calls the same decide_all on those inputs, per run, and compares each
row with live on its action and reason, among rows decided on the same
parameters (same_params; a row where they differ is counted apart). A run without recorded inputs is skipped and counted.

Nightly in pipeline_daily over the previous UTC day; the result is the
ingest_log row of job engine_replay. Reads only; never raises into the job.
"""
import argparse
import datetime as dt
import json
import sys

import engine_shadow as es

ACCEPT = 0.95                 # the plan's acceptance share
MISMATCHES_KEPT = 30


def ledgers_from(snapshot):
    """{strategy_id: f(city, target) -> ledger} from a recorded snapshot
    (engine_shadow.read_ledgers' `snapshot`), through the same es.ledger."""
    out = {}
    for sid, s in (snapshot or {}).items():
        city_day_of, pos, live = {}, [], []
        for band, side, shares, cost, cd in s.get("positions") or []:
            pos.append({"band_id": band, "side": side, "shares": shares, "cost_basis": cost})
            if cd:
                city_day_of[band] = tuple(cd)
        for band, ceiling, cd in s.get("orders") or []:
            live.append({"band_id": band, "cash_ceiling": ceiling})
            if cd:
                city_day_of[band] = tuple(cd)
        acct = {"cash": s.get("cash"), "reserved_cash": s.get("reserved_cash")}
        out[sid] = (lambda c, t, acct=acct, pos=pos, cdo=city_day_of, live=live,
                    hw=s.get("high_water"), pnl=s.get("pnl_today") or 0.0:
                    es.ledger(acct, pos, cdo, live, c, t, hw, pnl))
    return out


def canonical(params_version):
    """A params_version as one comparable string (key order aside)."""
    if not params_version:
        return None
    try:
        return json.dumps(json.loads(params_version), sort_keys=True)
    except (TypeError, ValueError):
        return str(params_version)


def same_params(a, b):
    """Whether two rows were decided on the same parameters: no key both
    record disagrees. Which keys a row records depends on how far it got - an
    S10 row its own rule declined records the anchor and S10's own, one that
    reached decide() also the engine's - so a different decision must not
    read as different parameters."""
    pa, pb = (json.loads(x) if x else {} for x in (a, b))
    return all(pa[k] == pb[k] for k in pa.keys() & pb.keys())


def learned_tables(live_rows, tables):
    """(clusters, anchor table) the live rows were decided on. `tables` maps
    (param, version) to a strategy_params value; "prior" is None, as
    city_clusters.load and market_anchor.load return while learning is off.
    Returns (None, None, why) when a version cannot be found."""
    clusters = anchor = None
    for r in live_rows:
        pv = json.loads(r["params_version"]) if r.get("params_version") else {}
        cv = pv.get("clusters")
        av = (pv.get("market_anchor") or {}).get("version")
        if cv and cv != "prior":
            if ("city_clusters", cv) not in tables:
                return None, None, f"city_clusters {cv} not found"
            clusters = tables[("city_clusters", cv)]
        if av and av != "prior":
            if ("market_weight", av) not in tables:
                return None, None, f"market_weight {av} not found"
            anchor = tables[("market_weight", av)]
    return clusters, anchor, None


def replay_run(live_rows, inputs, checkpoints, s10_ladders, bands_of, units, tables):
    """(replayed rows, None) for one run, or (None, why)."""
    ids = []
    for r in live_rows:
        cid = r.get("checkpoint_id")
        if cid and cid not in ids:
            ids.append(cid)
    missing = [c for c in ids if c not in checkpoints]
    if missing:
        return None, f"{len(missing)} checkpoint rows not found"
    clusters, anchor, why = learned_tables(live_rows, tables)
    if why:
        return None, why
    floors = {c: tuple(v) for c, v in (inputs.get("floors") or {}).items()}
    rows, _detail = es.decide_all([(c, checkpoints[c]) for c in ids], s10_ladders, bands_of, units, floors,
                                  ledgers_from(inputs.get("ledgers")), {"clusters": clusters}, anchor,
                                  float("inf"), live_rows[0]["run_id"], inputs["decided_at"])
    return rows, None


def compare(live_rows, replayed):
    """Row by row on (strategy, city, date). Returns the counts."""
    key = lambda r: (r["strategy_id"], r["city_key"], str(r["resolution_date"]))
    got = {key(r): r for r in replayed}
    out = {"rows": 0, "same_version": 0, "matched": 0, "other_version": 0, "not_replayed": 0,
           "by_strategy": {}, "mismatches": []}
    for r in live_rows:
        out["rows"] += 1
        s = out["by_strategy"].setdefault(r["strategy_id"], {"rows": 0, "matched": 0})
        s["rows"] += 1
        g = got.get(key(r))
        if g is None:
            out["not_replayed"] += 1
            continue
        if not same_params(g.get("params_version"), r.get("params_version")):
            out["other_version"] += 1
            continue
        out["same_version"] += 1
        if (g["action"], g["reason_code"]) == (r["action"], r["reason_code"]):
            out["matched"] += 1
            s["matched"] += 1
        elif len(out["mismatches"]) < MISMATCHES_KEPT:
            out["mismatches"].append({"decision_id": r.get("decision_id"), "strategy_id": r["strategy_id"],
                                      "city_key": r["city_key"], "live": [r["action"], r["reason_code"]],
                                      "replay": [g["action"], g["reason_code"]]})
    return out


def verdict(totals):
    """The acceptance share and whether it passes (None without rows)."""
    n = totals["same_version"]
    share = totals["matched"] / n if n else None
    return share, (None if share is None else share >= ACCEPT)


# ---------------------------------------------------------------------------
# reading the live day (service key), and the nightly run
# ---------------------------------------------------------------------------
def read_day(day, rest, rest_all):
    """Everything replay_run needs for the ticks of one UTC day."""
    lo, hi = day.isoformat(), (day + dt.timedelta(days=1)).isoformat()
    runs = {}
    for r in rest_all("ingest_log", [("select", "finished_at,detail"), ("job", "eq.tick"),
                                     ("finished_at", f"gte.{lo}"), ("finished_at", f"lt.{hi}")],
                      order="finished_at.asc"):
        eng = (r.get("detail") or {}).get("engine") or {}
        if eng.get("run_id"):
            runs[eng["run_id"]] = {"inputs": eng.get("inputs"),
                                   "s10_version": (((r.get("detail") or {}).get("s10") or {}).get("shadow")
                                                   or {}).get("version")}
    decisions = {}
    for r in rest_all("decisions", [("select", "decision_id,run_id,decided_at,checkpoint_id,strategy_id,city_key,"
                                               "resolution_date,action,reason_code,params_version"),
                                    ("decided_at", f"gte.{lo}"), ("decided_at", f"lt.{hi}"),
                                    ("checkpoint_id", "not.is.null")], order="decision_id.asc"):
        if str(r["run_id"]) in runs:
            decisions.setdefault(str(r["run_id"]), []).append(r)
    cids = sorted({str(r["checkpoint_id"]) for rs in decisions.values() for r in rs})
    checkpoints = {}
    for i in range(0, len(cids), 100):
        for r in rest_all("prediction_checkpoints", [
                ("select", "checkpoint_id,city_key,target_date,checkpoint,local_decision_time,probs,market"),
                ("checkpoint_id", f"in.({','.join(cids[i:i + 100])})")], order="checkpoint_id.asc"):
            checkpoints[str(r["checkpoint_id"])] = r
    days = sorted({(r["city_key"], str(r["target_date"])) for r in checkpoints.values()})
    cities = sorted({c for c, _ in days})
    dates = sorted({d for _, d in days})
    s10 = {}
    if cities:
        for r in rest_all("s10_shadow_checkpoints", [
                ("select", "city_key,target_date,checkpoint,model_version,probs"),
                ("city_key", f"in.({','.join(cities)})"), ("target_date", f"in.({','.join(dates)})")],
                order="city_key.asc,target_date.asc"):
            s10[(r["city_key"], str(r["target_date"]), r["checkpoint"], r.get("model_version"))] = r["probs"]
    markets = {}
    if cities:
        for m in rest_all("v_canonical_markets", [("select", "market_id,city_key,resolution_date"),
                                      ("city_key", f"in.({','.join(cities)})"),
                                      ("resolution_date", f"in.({','.join(dates)})")], order="market_id.asc"):
            markets[(m["city_key"], str(m["resolution_date"]))] = str(m["market_id"])
    import probability_engine as pe
    bands_by_market = {}
    for b in pe._bands_for_markets(sorted(set(markets.values()))):
        bands_by_market.setdefault(str(b["market_id"]), []).append(b)
    bands_of = {k: sorted(bands_by_market.get(mid, []),
                          key=lambda b: (not b.get("open_low"), b["band_lo"] if b.get("band_lo") is not None else -1e9))
                for k, mid in markets.items()}
    from common import get_cities
    units = {c["city_key"]: (c.get("unit") or "C") for c in get_cities(require_coords=False)}
    tables = {}
    versions = set()
    for rs in decisions.values():
        for r in rs:
            pv = json.loads(r["params_version"]) if r.get("params_version") else {}
            if pv.get("clusters") not in (None, "prior"):
                versions.add(("city_clusters", pv["clusters"]))
            av = (pv.get("market_anchor") or {}).get("version")
            if av not in (None, "prior"):
                versions.add(("market_weight", av))
    for param, version in sorted(versions):
        rows = rest("strategy_params", [("select", "value,version"), ("param", f"eq.{param}"),
                                        ("version", f"eq.{version}"), ("limit", "1")]) or []
        if rows:
            table = rows[0].get("value") or {}
            table.setdefault("version", version)
            tables[(param, version)] = table
    return runs, decisions, checkpoints, s10, bands_of, units, tables


def replay_day(runs, decisions, checkpoints, s10, bands_of, units, tables):
    totals = {"runs": 0, "runs_skipped": {}, "rows": 0, "same_version": 0, "matched": 0, "other_version": 0,
              "not_replayed": 0, "by_strategy": {}, "mismatches": []}
    for run_id, live_rows in sorted(decisions.items()):
        run = runs.get(run_id) or {}
        why = None if run.get("inputs") else "no recorded inputs"
        replayed = None
        if why is None:
            ladders = {(c, d, n): p for (c, d, n, v), p in s10.items() if v == run.get("s10_version")}
            replayed, why = replay_run(live_rows, run["inputs"], checkpoints, ladders, bands_of, units, tables)
        if why:
            totals["runs_skipped"][why] = totals["runs_skipped"].get(why, 0) + 1
            continue
        totals["runs"] += 1
        c = compare(live_rows, replayed)
        for k in ("rows", "same_version", "matched", "other_version", "not_replayed"):
            totals[k] += c[k]
        for sid, s in c["by_strategy"].items():
            t = totals["by_strategy"].setdefault(sid, {"rows": 0, "matched": 0})
            t["rows"] += s["rows"]
            t["matched"] += s["matched"]
        totals["mismatches"] += c["mismatches"][:MISMATCHES_KEPT - len(totals["mismatches"])]
    share, ok = verdict(totals)
    totals["share"] = None if share is None else round(share, 4)
    totals["accepted"] = ok
    return totals


def main(argv=None):
    from common import rest, rest_all, log_run
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--day", help="UTC day to replay (default: yesterday)")
    a = ap.parse_args(argv)
    day = dt.date.fromisoformat(a.day) if a.day else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    try:
        totals = replay_day(*read_day(day, rest, rest_all))
        totals["day"] = day.isoformat()
        status = "ok" if totals["accepted"] is not False else "attention"
    except Exception as e:                        # noqa: BLE001 - a check, never the job
        totals = {"day": day.isoformat(), "error": f"{type(e).__name__}: {str(e)[:200]}"}
        status = "attention"
    print(json.dumps({k: v for k, v in totals.items() if k != "mismatches"}, default=str))
    log_run("engine_replay", status, totals.get("rows", 0), totals)
    return totals


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
