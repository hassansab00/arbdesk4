"""Compare frozen forecast artifacts at original checkpoint decision times.

Retries read the same as-of input history and preserve existing shadow rows.
This runner writes diagnostics; it does not price or trade.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wxpredict import common, live_reader as lr  # noqa: E402

ROOT = common.ROOT
VARIANT = "wxrd"
MODEL_JSON = os.path.join(ROOT, "data", "models", "wxrd", "wxrd-v2_remaining.json")
# Each shadow variant, its frozen model and its log row. wxrd3 (13 Oct): the
# same model fitted with five seeds, their ladders averaged
# (data/eval/wxpredict/stability_2026-10-13.json); skipped until its file exists.
VARIANTS = {
    "wxrd": (MODEL_JSON, "P7.6_wxrd_shadow"),
    "wxrd3": (os.path.join(ROOT, "data", "models", "wxrd", "wxrd-v3_remaining.json"), "P7.6_wxrd3_shadow"),
}
CHECKPOINTS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
SERVED_WITHIN_MIN = 30      # a row decided further back than this was written by a retry
RETRY_HOURS = 6             # the record the row reads reaches RETRY_HOURS + RECORD_HOURS back, joined from the database and committed mirror
MAX_DUE = 30                # bounded per-run work
ROW_BUDGET_S = 90           # rows stop being built here and what is built is written, inside the step's 3 min;
                            # bounds, not measurements: the first ticks' seconds are in the log rows
RECORD_HOURS = 40           # the day's hourly forecasts reach the record up to 17 h ahead; older hours come from the committed mirror
KINDS = ("report", "hourly", "hourly2", "models1", "models2", "hourly0", "models0", "awc_metar")
TABLE = "variant_shadow_checkpoints"
ON_CONFLICT = "city_key,target_date,checkpoint,variant"
JOB = "P7.6_wxrd_shadow"


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
def variant_version(meta, variant=VARIANT):
    """'<variant>:<model version>:<sha256 12>', e.g. 'wxrd:v2:62b5de3eace3'."""
    return f"{variant}:{meta['version'].rsplit('-', 1)[-1]}:{meta['model_sha256'][:12]}"


def due(served, held, limit=MAX_DUE):
    """The served rows at a checkpoint of CHECKPOINTS without a wxrd row,
    oldest decision first, at most `limit`: held is {(city, target_date,
    checkpoint)} of the wxrd rows written. Returns (due, deferred count)."""
    ordered = sorted((r for r in served if r["checkpoint"] in CHECKPOINTS
                   and (r["city_key"], str(r["target_date"]), r["checkpoint"]) not in held),
                  key=lambda r: (str(r["decided_at"]), r["city_key"], r["checkpoint"], str(r.get("checkpoint_id", ""))))
    first = {}
    for r in ordered:
        first.setdefault((r["city_key"], str(r["target_date"]), r["checkpoint"]), r)
    todo = list(first.values())
    return todo[:limit], max(0, len(todo) - limit)


def probs_of(band_ids, p):
    """{band_id: p} rounded to 6 places, as the variant table holds them."""
    return {str(b): round(float(x), 6) for b, x in zip(band_ids, p)}


def variant_row(served, unit, band_ids, p, version, calibrated, variant=VARIANT):
    """calibrated: the served call's calibration as the tick recorded it on
    the other variants' rows of the same checkpoint (variant_shadow.py reads
    it from the engine's reasons; a separate step cannot)."""
    probs = probs_of(band_ids, p)
    top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return {
        "city_key": served["city_key"], "target_date": str(served["target_date"]),
        "checkpoint": served["checkpoint"], "variant": variant, "variant_version": version,
        "decided_at": served["decided_at"], "engine_version": served["engine_version"],
        "station": served.get("station"), "served_centre_c": served.get("centre_c"),
        "served_sigma_c": served.get("sigma_c"),
        "served_calibrated": bool(calibrated),
        "unit": unit, "probs": probs, "top_band_id": top[0], "top_prob": top[1],
        "floor_c": None, "q_down": None, "q_up": None,
    }


def oldest(todo):
    """The earliest decision among the due rows, as a UTC datetime."""
    return min(dt.datetime.fromisoformat(str(r["decided_at"]).replace("Z", "+00:00")) for r in todo)


def event_for(events, served):
    """The served call's ladder among the city-day's events: the one holding
    its top bucket (a city-day can have a twin listing)."""
    for e in events:
        if e["city"] == served["city_key"] and e["date"] == str(served["target_date"]) \
                and str(served["top_band_id"]) in e["band_ids"]:
            return e
    return None


def key_filter(cities, city_keys):
    """PostgREST or-filter for the live record's keys of these cities: their
    forecasts ('city|hour') and their stations' reports ('IEM id|time')."""
    parts = []
    for k in sorted(city_keys):
        parts.append(f"key.like.{k}|*")
        icao = (cities.get(k) or {}).get("icao")
        if icao:
            parts.append(f"key.like.{common.iem_id(icao)}|*")
    return "(" + ",".join(parts) + ")"


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def load_model(meta, model_json=MODEL_JSON):
    """The frozen model, or for a several-seed version the list of its models,
    each one's sha256 checked, and the version's id checked against them -
    every check before lightgbm is imported, so a refusal needs no model."""
    def one(name, want, asset_url=None):
        path = os.path.join(os.path.dirname(model_json), name)
        if asset_url:
            from wxpredict import model_artifacts
            path = model_artifacts.ensure({"asset_url": asset_url, "model_sha256": want})
        with open(path, "rb") as fh:
            text = gzip.decompress(fh.read())
        sha = hashlib.sha256(text).hexdigest()
        if sha != want:
            raise SystemExit(f"{meta['version']}'s {name} sha256 is {sha}, not {want}: refusing to predict")
        import lightgbm as lgb
        return lgb.Booster(model_str=text.decode())
    if "models" not in meta:
        return one(meta["model_file"], meta["model_sha256"], meta.get("asset_url"))
    joined = hashlib.sha256("".join(m["sha256"] for m in meta["models"]).encode()).hexdigest()
    if joined != meta["model_sha256"]:
        raise SystemExit(f"{meta['version']}'s models do not make its id {meta['model_sha256']}: refusing to predict")
    return [one(m["file"], m["sha256"]) for m in meta["models"]]


def run(now=None, dry_run=False, variant=VARIANT, deadline=None):
    """deadline: when rows stop being built (unix seconds); ROW_BUDGET_S after
    the start unless given, and main() gives every variant the same one."""
    t0 = time.time()
    deadline = t0 + ROW_BUDGET_S if deadline is None else deadline
    ops = common.ops()
    now = now or dt.datetime.now(dt.timezone.utc)
    model_json, job = VARIANTS[variant]
    with open(model_json) as fh:
        meta = json.load(fh)
    version = variant_version(meta, variant)
    since = (now - dt.timedelta(hours=RETRY_HOURS)).isoformat()
    served = ops.rest_all("prediction_checkpoints", [
        ("select", "checkpoint_id,city_key,target_date,checkpoint,decided_at,engine_version,model_path,station,centre_c,"
                   "sigma_c,top_band_id"),
        ("decided_at", f"gte.{since}"), ("decided_at", f"lte.{now.isoformat()}"),
        ("checkpoint", f"in.({','.join(CHECKPOINTS)})")], order="checkpoint_id.asc")
    variant_rows = ops.rest_all(TABLE, [("select", "city_key,target_date,checkpoint,variant,served_calibrated"),
                                        ("decided_at", f"gte.{since}")], order="shadow_id.asc")
    key = lambda r: (r["city_key"], str(r["target_date"]), r["checkpoint"])  # noqa: E731
    todo, deferred = due(served, {key(r) for r in variant_rows if r["variant"] == variant})
    calibrated = {key(r): r["served_calibrated"] for r in variant_rows if r["variant"] not in VARIANTS}
    late = (now - dt.timedelta(minutes=SERVED_WITHIN_MIN)).isoformat()
    detail = {"version": version, "served": len(served), "due": len(todo), "deferred": deferred,
              "retried": sum(str(r["decided_at"]) < late for r in todo), "written": 0, "skipped": {}}
    if not todo:
        detail["seconds"] = round(time.time() - t0, 1)
        if not dry_run:
            ops.log_run(job, "ok", 0, detail)
        return detail

    cities = common.cities()
    city_keys = {r["city_key"] for r in todo}
    record_rows = ops.rest_all("live_capture", [
        ("select", "kind,key,captured_at,payload"), ("kind", f"in.({','.join(KINDS)})"),
        ("captured_at", f"gte.{(oldest(todo) - dt.timedelta(hours=RECORD_HOURS)).isoformat()}"),
        ("captured_at", f"lte.{now.isoformat()}"), ("or", key_filter(cities, city_keys))],
        order="capture_id.asc", page_size=1000)
    from wxpredict import capture_history
    boundary = (now.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                - dt.timedelta(days=1)).timestamp()
    prefixes = {c + "|" for c in city_keys}
    prefixes.update(common.iem_id(cities[c]["icao"]) + "|" for c in city_keys if cities[c].get("icao"))
    cold = capture_history.read((oldest(todo) - dt.timedelta(hours=RECORD_HOURS)).timestamp(),
                                boundary, kinds=KINDS, prefixes=prefixes)
    record = lr.Record([*cold, *[(r["kind"], r["key"], r["captured_at"], r["payload"]) for r in record_rows]])
    detail["record_rows_from_repo"] = len(cold)
    dates = sorted({str(r["target_date"]) for r in todo})
    markets = ops.rest_all("markets", [("select", "market_id,city_key,resolution_date,unit"),
                                       ("city_key", f"in.({','.join(sorted(city_keys))})"),
                                       ("resolution_date", f"in.({','.join(dates)})")], order="market_id.asc")
    bands = ops.rest_all("bands", [("select", "band_id,market_id,band_index,band_lo,band_hi,open_low,open_high"),
                                   ("market_id", f"in.({','.join(m['market_id'] for m in markets)})")],
                         order="band_id.asc") if markets else []
    events = lr.live_events(markets, bands, cities, dates[0], dates[-1])
    from wxpredict import build_table
    sdays, _ = build_table.load_station_daily()
    bm, md = build_table.load_daily_forecasts()

    skipped = {}
    rows, pairs = [], []
    for i, s in enumerate(todo):
        if time.time() > deadline:
            detail["deferred"] += len(todo) - i
            break
        if key(s) not in calibrated:
            skipped["no sibling variant row (calibration unknown)"] = \
                skipped.get("no sibling variant row (calibration unknown)", 0) + 1
            continue
        e = event_for(events, s)
        if e is None:
            skipped["no whole ladder"] = skipped.get("no whole ladder", 0) + 1
            continue
        t = int(dt.datetime.fromisoformat(str(s["decided_at"]).replace("Z", "+00:00")).timestamp())
        row = lr.row_at(record, e, cities, sdays, bm, md, t, report_lag_s=meta["report_lag_s"])
        if row is None:
            skipped["not on the day"] = skipped.get("not on the day", 0) + 1
            continue
        rows.append(row)
        pairs.append((s, e, row))
    out = []
    if rows:
        if not dry_run:
            from wxpredict import decision_inputs, current_forecasts
            try:
                commit, committed_at = decision_inputs.history_identity()
                packed = [decision_inputs.pack(s, e, r, commit, committed_at,
                    current=current_forecasts.context(record, e, int(r["decision_utc"]), common.zone(cities[s["city_key"]])))
                    for s, e, r in pairs]
                detail["recorded_decision_inputs"] = ops.rpc("live_capture_put", {
                    "p_kind": decision_inputs.KIND, "p_captured_at": dt.datetime.now(common.UTC).isoformat(), "p_rows": packed})
            except Exception as exc:
                detail["decision_input_capture_error"] = type(exc).__name__ + ": " + str(exc)[:200]
        # A shadow row marks the checkpoint held on the next tick. Leave it
        # due when its exact inputs were not saved, so a transient capture
        # failure can be retried instead of permanently losing evaluation data.
    if rows and not detail.get("decision_input_capture_error"):
        import sdyes_forward
        probs = sdyes_forward.model_probs(rows, load_model(meta, model_json))
        for s, e, row in pairs:
            p = probs.get((str(row["event_id"]), int(row["decision_utc"])))
            if p is None:
                why = "before 09h local" if int(row["local_hour"]) < 9 else "no running maximum"
                skipped[why] = skipped.get(why, 0) + 1
                continue
            out.append(variant_row(s, e["unit"], e["band_ids"], p, version, calibrated[key(s)], variant))
    written = len(out)
    if out and not dry_run:
        from frozen_calls import freeze_insert
        written = freeze_insert(TABLE, out, ON_CONFLICT, writer=ops.upsert,
            refused=detail.setdefault("freeze_refused", []),
            deadline=time.monotonic()+max(0,deadline-time.time()))
    detail.update(written=written, skipped=skipped, record_rows=len(record_rows),
                  seconds=round(time.time() - t0, 1))
    if dry_run:
        detail["rows"] = out
    else:
        lost = sum(v for k, v in skipped.items() if k in ("no whole ladder", "not on the day"))
        ops.log_run(job, "ok" if not lost and not detail.get("decision_input_capture_error") and not detail.get("freeze_refused") else "partial", written, detail)
    return detail


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--at", default="", help="rehearse as if it were this UTC time")
    args = ap.parse_args(argv)
    now = dt.datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else None
    failed = []
    # Frozen wxrd/wxrd3 keep their original files. An accepted automatic
    # challenger gets its own variant and an immutable, checked lineage.
    from wxpredict import cycle_registry
    VARIANTS.pop("wxrd_auto", None)
    try:
        selected = cycle_registry.selected()
        if selected:
            VARIANTS["wxrd_auto"] = (selected[0], "P7.6_wxrd_auto_shadow")
    except Exception as exc:
        failed.append("wxrd_auto")
        print(f"automatic shadow metadata refused: {exc!r}", flush=True)
    # one row budget for all the variants together (Codex on #398): the
    # step's 3 minutes hold every variant, so the second gets what the first
    # left and defers the rest to the next tick
    deadline = time.time() + ROW_BUDGET_S
    for variant, (model_json, _) in VARIANTS.items():
        if not os.path.exists(model_json):
            print(f"{variant}: no model at {os.path.relpath(model_json, ROOT)}, skipped")
            continue
        try:                                   # one variant failing does not stop the next
            print(variant, json.dumps(run(now, args.dry_run, variant, deadline), indent=1, default=str), flush=True)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - a refused model too (Codex on #398)
            failed.append(variant)
            print(f"{variant} failed: {exc!r}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
