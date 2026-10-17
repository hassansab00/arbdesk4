"""Horizon-specific primary prediction, authorized by Hassan on 17 Oct.

The corrected multi-model path keeps day-ahead/evening and morning calls.
The frozen five-seed wxrd3 supplies same-day noon and peak-relative calls,
with fresh station/forecast inputs. Its *whole bucket distribution* replaces
the baseline; no normal width or old calibration is applied to it. Every
failure keeps the baseline and records why. No capital setting changes.
"""
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from zoneinfo import ZoneInfo

POLICY = "wxrd3-horizons-v1"
MAX_OBS_AGE_MIN = 90
# Allow cold input/history loading before building the first vector, while
# leaving sixty seconds of the nominal tick budget for pricing and capture.
MAX_PREPARE_S = 40
PEAK_CALLS = {"prepeak_2h", "prepeak_1h", "postpeak_1h"}
_predictions = {}
_eligible = set()
_detail = {}
_lineage = {}


def enabled():
    return os.environ.get("PREDICTIVE_PRIMARY") == POLICY


def eligible(target, timezone, now, checkpoints=()):
    """The city's own day, never tomorrow or yesterday. Morning keeps its
    incumbent even if a delayed tick also catches a peak checkpoint."""
    if not timezone:
        return False
    local = now.astimezone(ZoneInfo(timezone))
    if str(target) != local.date().isoformat() or local.hour < 9:
        return False
    if "morning" in checkpoints:
        return False
    return local.hour >= 12 or bool(set(checkpoints) & PEAK_CALLS)


def input_problem(row):
    if row is None or row.get("rmax_c") is None:
        return "no same-day running maximum"
    age = row.get("obs_age_min")
    if age is None or not math.isfinite(float(age)) or not 0 <= float(age) <= MAX_OBS_AGE_MIN:
        return "station report stale or missing"
    if int(row.get("fc_models_n") or 0) < 4 or int(row.get("fch_n_known") or 0) < 20:
        return "incomplete weather-model inputs"
    return None


def valid_distribution(ids, p):
    return (p is not None and len(ids) == len(p) and len(set(ids)) == len(ids)
            and all(math.isfinite(float(v)) and 0 <= float(v) <= 1 for v in p)
            and abs(sum(map(float, p)) - 1) <= 1e-4)


def prepare(markets, bands_by_market, cities, now=None, checkpoints=None, dry_run=False):
    """Prepare one immutable batch BEFORE pricing workers start. Read only
    inputs received by `now`; persist the exact vectors before they can price.
    All expensive dependencies are optional outside the two enabled jobs."""
    global _predictions, _eligible, _detail, _lineage
    _predictions, _eligible, _lineage = {}, set(), {}
    _detail = {"policy": POLICY, "selected": 0, "fallbacks": {}}
    if not enabled():
        return _detail
    t0 = time.monotonic()
    now = now or dt.datetime.now(dt.timezone.utc)
    now = now.replace(microsecond=0)
    cities = {c["city_key"]: c for c in cities}
    checkpoints = checkpoints or {}
    selected = [m for m in markets if eligible(m["resolution_date"],
        (cities.get(m["city_key"]) or {}).get("timezone"), now,
        checkpoints.get((m["city_key"], str(m["resolution_date"])), ()))]
    _eligible = {(m["city_key"], str(m["resolution_date"])) for m in selected}
    if not selected:
        return _detail
    skipped = Counter()
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(root, "tools"))
        from wxpredict import common, live_reader as lr, wxrd_live as wl
        from wxpredict import build_table, capture_history, decision_inputs
        import sdyes_forward
        ops = common.ops()
        meta_path = wl.VARIANTS["wxrd3"][0]
        with open(meta_path) as fh:
            meta = json.load(fh)
        version = wl.variant_version(meta, "wxrd3")
        from wx_city_calibration import phase, transform, usable
        try:
            cells = ops.rest_all("derived_wx_city_calibration", [
                ("select", "city_key,phase,state"), ("model_version", "eq."+version),
                ("computed_at", "lte."+now.isoformat())], order="city_key.asc,phase.asc", page_size=1000)
        except Exception:
            cells = []  # A missing/unavailable fit keeps the raw distribution.
        calibration = {(c["city_key"],c["phase"]): c["state"] for c in cells}
        model = wl.load_model(meta, meta_path)
        city_keys = {m["city_key"] for m in selected}
        first = now - dt.timedelta(hours=wl.RECORD_HOURS)
        rows = ops.rest_all("live_capture", [
            ("select", "kind,key,captured_at,payload"),
            ("kind", f"in.({','.join(wl.KINDS)})"),
            ("captured_at", f"gte.{first.isoformat()}"),
            ("captured_at", f"lte.{now.isoformat()}"),
            ("or", wl.key_filter(cities, city_keys))], order="capture_id.asc", page_size=1000)
        boundary = now.replace(hour=0, minute=0, second=0) - dt.timedelta(days=1)
        prefixes = {c + "|" for c in city_keys}
        prefixes.update(common.iem_id(cities[c]["icao"]) + "|" for c in city_keys if cities[c].get("icao"))
        cold = capture_history.read(first.timestamp(), boundary.timestamp(), kinds=wl.KINDS, prefixes=prefixes)
        record = lr.Record([*cold, *[(r["kind"], r["key"], r["captured_at"], r["payload"]) for r in rows]])
        all_bands = [b for m in selected for b in bands_by_market.get(m["market_id"], [])]
        dates = [str(m["resolution_date"]) for m in selected]
        events = lr.live_events(selected, all_bands, cities, min(dates), max(dates))
        sdays, _ = build_table.load_station_daily()
        bm, md = build_table.load_daily_forecasts()
        vectors, pairs = [], []
        for e in events:
            if not meta.get("fit_last") or str(meta["fit_last"])[:10] >= e["date"]:
                skipped["model trained on or after target"] += 1
                continue
            if time.monotonic() - t0 > MAX_PREPARE_S:
                skipped["primary preparation budget"] += 1
                continue
            row = lr.row_at(record, e, cities, sdays, bm, md, int(now.timestamp()),
                            report_lag_s=meta["report_lag_s"])
            why = input_problem(row)
            if why:
                skipped[why] += 1
                continue
            vectors.append(row)
            pairs.append((e, row))
        probs = sdyes_forward.model_probs(vectors, model)
        commit, committed_at = decision_inputs.history_identity()
        if committed_at > now.timestamp():
            raise ValueError("history vintage is newer than the prediction")
        packed, accepted = [], []
        for e, row in pairs:
            p = probs.get((str(e["event_id"]), int(row["decision_utc"])))
            if not valid_distribution(e["band_ids"], p):
                skipped["invalid primary distribution"] += 1
                continue
            vector = {k: None if k in decision_inputs.LABELS else row.get(k) for k in lr.features.COLUMNS}
            raw = json.dumps(vector, sort_keys=True, separators=(",", ":"), allow_nan=False)
            sha = hashlib.sha256(raw.encode()).hexdigest()
            key = "|".join((POLICY, str(e["event_id"]), str(int(now.timestamp()))))
            payload = {"policy": POLICY, "version": version, "as_of": now.isoformat(),
                       "history_commit": commit, "history_committed_at": committed_at,
                       "model_fit_through": str(meta["fit_last"])[:10],
                       "band_ids": list(e["band_ids"]), "row": vector, "row_sha256": sha}
            packed.append({"key": key, "payload": payload})
            accepted.append((e, row, p, sha, key))
        if packed and not dry_run:
            ops.rpc("live_capture_put", {"p_kind": decision_inputs.KIND,
                "p_captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "p_rows": packed})
        for e, row, p, sha, input_key in accepted:
            # Market identity includes the ladder; a twin market cannot borrow
            # another listing's probabilities just because its city/day match.
            key = (e["city"], e["date"], frozenset(map(str, e["band_ids"])))
            raw_probs = dict(zip(map(str, e["band_ids"]), map(float, p)))
            context = {k: row.get(k) for k in lr.features.COLUMNS if k not in decision_inputs.LABELS}
            context.update(band_ids=list(map(str,e["band_ids"])), model_fit_through=str(meta["fit_last"])[:10])
            state = calibration.get((e["city"], phase(context)))
            served = raw_probs
            if state and usable(state, e["date"], version):
                try:
                    served = transform(raw_probs, context["band_ids"], state["params"])
                except (KeyError, TypeError, ValueError):
                    state = None
            else:
                state = None
            _predictions[key] = {"version": version, "as_of": now.isoformat(),
                "probs": served, "raw_probs": raw_probs, "input_key": input_key,
                "calibration": state, "predictive_context": context,
                "floor_c": float(row["rmax_c"]), "unit": e["unit"],
                "floor_unit": float(row["rmax_unit"]), "input_sha256": sha,
                "history_commit": commit}
        _detail.update(selected=len(_predictions), version=version)
        if len(events) < len(selected):
            skipped["no complete canonical ladder"] += len(selected) - len(events)
    except (Exception, SystemExit) as exc:
        _predictions = {}
        _detail["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    _detail.update(fallbacks=dict(skipped), seconds=round(time.monotonic() - t0, 2))
    print("predictive primary " + json.dumps(_detail), flush=True)
    return _detail


def apply(city, target, result):
    """Replace all probabilities together, preserving existing eligibility
    gates. A missing primary keeps the baseline."""
    if not enabled() or result is None:
        return result
    rows, reg, reasons = result
    key = (city, str(target), frozenset(str(r["band_id"]) for r in rows))
    primary = _predictions.get(key)
    if primary is None:
        if (city, str(target)) in _eligible:
            return rows, reg, [*reasons, f"primary_fallback:{POLICY}:baseline_kept"]
        return result
    # Capture may still contain an older report after the engine has ingested
    # a new maximum. Compare settlement degrees, including Fahrenheit rounding,
    # before replacing the baseline; never reshape the frozen distribution.
    from venue import venue_round
    if any(venue_round(float(r["observed_floor_c"]), primary["unit"]) > primary["floor_unit"]
           for r in rows if r.get("observed_floor_c") is not None
           and math.isfinite(float(r["observed_floor_c"]))):
        return rows, reg, [*reasons, f"primary_fallback:{POLICY}:newer_station_maximum"]
    from common import model_version_id
    version = model_version_id("forecast", primary["version"], config={
        "policy": POLICY, "version": primary["version"], "distribution": "direct buckets",
        "authorization": "Hassan 2026-10-17: best predictive model should take the heavy lifting",
        "max_obs_age_min": MAX_OBS_AGE_MIN}, structural=False)
    if not version:
        return rows, reg, [*reasons, f"primary_fallback:{POLICY}:version_not_registered"]
    state = primary.get("calibration")
    cal_id = None
    served = primary["probs"]
    if state:
        cal_id = model_version_id("calibration", state["version"], config=state, structural=False)
        if not cal_id:
            state, served = None, primary["raw_probs"]
    baseline_probs = {str(r["band_id"]): float(r["calibrated_prob"]) for r in rows}
    out = []
    for baseline in rows:
        r = dict(baseline)
        p = round(served[str(r["band_id"])], 6)
        r.update(raw_prob=round(primary.get("raw_probs", primary["probs"])[str(r["band_id"])], 6), calibrated_prob=p, computed_at=primary["as_of"],
                 forecast_version=version, calibration_version=cal_id,
                 forecast_max_c=None, input_forecast_run=None,
                 centre_c=None, sigma_c=None, forecast_sigma_c=None,
                 station_width_c=None, observed_floor_c=primary["floor_c"],
                 bias_applied_c=0, lattice_applied=False)
        out.append(r)
    # Remove provenance for corrections that did NOT price this distribution.
    keep = [r for r in reasons if not r.startswith(("priced_from:", "calibrated:", "station_width:",
        "station_correction:", "measurement_layer:", "trajectory:", "postprocessed:"))]
    keep.extend([f"primary_policy:{POLICY}", f"primary_inputs:{primary['input_sha256']}",
                 f"priced_from:{primary['version']}"])
    if primary.get("input_key"):
        _lineage[key] = dict(input_as_of=primary["as_of"], input_key=primary["input_key"],
            input_sha256=primary["input_sha256"], raw_probs=primary["raw_probs"],
            baseline_probs=baseline_probs, baseline_model=next((r for r in reasons if r.startswith("priced_from:")), "unknown"),
            calibration=state, predictive_context=primary["predictive_context"],
            served_probs={str(r["band_id"]): r["calibrated_prob"] for r in out})
    return out, reg, keep


def checkpoint_lineage(city, target, probs):
    """Only attach metadata for the exact ladder that was actually served."""
    record = _lineage.get((city,str(target),frozenset(probs)))
    if record is None or record["served_probs"] != probs:
        return {}
    return {k:v for k,v in record.items() if k != "served_probs"}
