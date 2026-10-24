"""Independent evaluation from the engineered vectors recorded at first calls.

Later history, revisions, weather replays and books never replace those
vectors. Only settled labels and the first call's frozen market are attached.
"""
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os

from wxpredict import common, capture_history, decision_inputs as inputs, live_reader as lr

CHECKPOINTS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")


def mirrored_rows(table, first, through, root=None):
    root = root or common.ROOT
    folder = os.path.join(root, "data/mirror")
    with open(os.path.join(folder, "manifest.json")) as fh:
        state = json.load(fh)["tables"][table]
    if capture_history.unix(state["through"]) < through:
        raise ValueError(table + " mirror is not complete through the evaluation")
    rows = []
    for entry in state["files"]:
        if (entry.get("from") and capture_history.unix(entry["from"]) >= through
                or capture_history.unix(entry["to"]) <= first):
            continue
        name = entry["file"]
        if os.path.basename(name) != name:
            raise ValueError("invalid mirrored input path")
        with open(os.path.join(folder, table, name), "rb") as fh:
            blob = fh.read()
        if hashlib.sha256(blob).hexdigest() != entry["sha256"]:
            raise ValueError(table + " mirror hash mismatch")
        part = list(csv.DictReader(io.StringIO(gzip.decompress(blob).decode())))
        if len(part) != entry["rows"]:
            raise ValueError(table + " mirror count mismatch")
        rows.extend(part)
    return rows


def first_calls(rows, first, last, checkpoints=CHECKPOINTS):
    selected = {}
    seen_ids = set()
    for r in sorted(rows, key=lambda r: (lr.unix(r["decided_at"]), r["checkpoint_id"])):
        if r["checkpoint_id"] in seen_ids:
            continue
        seen_ids.add(r["checkpoint_id"])
        if first <= r["target_date"] <= last and r["checkpoint"] in checkpoints:
            selected.setdefault((r["city_key"], r["target_date"], r["checkpoint"]), r)
    return list(selected.values())


def recorded(first, last, through, checkpoints=CHECKPOINTS):
    start = capture_history.unix(first + "T00:00:00Z") - 2 * 86400
    calls = first_calls(mirrored_rows("prediction_checkpoints", start, through), first, last, checkpoints)
    captured = capture_history.read(start, through, kinds=(inputs.KIND,))
    vectors = {}
    for kind, key, at, payload in sorted(captured, key=lambda r: capture_history.unix(r[2])):
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if inputs.eligible(payload, at):
            vectors.setdefault(str(payload["checkpoint_id"]), payload)
    return calls, vectors


def preflight(first, last, through, minimum_cities=20, checkpoints=CHECKPOINTS):
    """No ML dependencies or historical table build until real vectors exist."""
    calls, vectors = recorded(first, last, through, checkpoints)
    counts = collections.defaultdict(set)
    for call in calls:
        payload = vectors.get(str(call["checkpoint_id"]))
        if payload and payload["checkpoint"] == call["checkpoint"]:
            counts[(call["target_date"], call["checkpoint"])].add(call["city_key"])
    dates = [(dt.date.fromisoformat(first) + dt.timedelta(days=i)).isoformat()
             for i in range((dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1)]
    sparse = [day + ":" + cp for day in dates for cp in checkpoints if len(counts[(day, cp)]) < minimum_cities]
    return {"ready": not sparse, "first_calls": len(calls), "recorded_first_calls": sum(map(len, counts.values())),
            "sparse_checkpoint_dates": sparse, "feature_policy": inputs.POLICY,
            "checkpoints":list(checkpoints)}


def attach(call, payload, event, label, include_current=False):
    """Strict contract identity, no reconstruction, the same decision's book."""
    r = payload["row"]
    if (str(payload["checkpoint_id"]) != str(call["checkpoint_id"])
            or payload["decided_at"] != call["decided_at"] and inputs.unix(payload["decided_at"]) != inputs.unix(call["decided_at"])
            or payload["checkpoint"] != call["checkpoint"] or payload["city_key"] != event["city"]
            or payload["target_date"] != event["date"] or payload["band_ids"] != event["band_ids"]
            or r["station"] != event["station"] or r["unit"] != event["unit"]
            or r["n_bands"] != len(event["bands"])):
        raise ValueError("recorded decision inputs differ from the first settled contract")
    def bounds(values):
        return [None if v == "" else float(v) for v in values.split(";")]
    if list(zip(bounds(r["bands_lo"]), bounds(r["bands_hi"]))) != event["bands"]:
        raise ValueError("recorded decision ladder geometry differs")
    market = json.loads(call["market"]) if isinstance(call["market"], str) else call["market"]
    prices = []
    for band in event["band_ids"]:
        quote = (market or {}).get(band) or {}
        bid, ask = quote.get("bid"), quote.get("ask")
        if any(v is not None and (not isinstance(v, (int, float)) or not 0 <= v <= 1) for v in (bid, ask)):
            raise ValueError("invalid first-call market quote")
        price = lr.live_price(bid, ask)
        if price is None:
            return None
        prices.append(price)
    if not prices or not sum(prices) or r["local_hour"] < 9 or r.get("rmax_unit") is None:
        return None
    out = {**r, **label, "event_id": event["event_id"], "checkpoint": call["checkpoint"],
           "live_mode": "arrival", "mkt_complete": True, "mkt_prices": ";".join(map(str, prices))}
    if include_current:
        from wxpredict import current_forecasts
        current = inputs.current_context(payload)
        out.update(current or dict.fromkeys(current_forecasts.FIELDS))
        out.update(current_context_available=int(current is not None),
                   recorded_checkpoint_id=str(call["checkpoint_id"]),
                   recorded_row_sha256=payload["row_sha256"],
                   recorded_current_sha256=payload.get("current_forecasts_sha256"))
    return out


def build(first, last, table, through, include_current=False, checkpoints=CHECKPOINTS):
    from wxpredict import build_table as bt, parity
    labels = {}
    with gzip.open(table, "rt") as fh:
        for r in csv.DictReader(fh):
            if r["source"] == "venue" and first <= r["date"] <= last:
                labels.setdefault(r["event_id"], {k: r[k] for k in parity.LABELS})
    cities = common.cities()
    events, _, _ = bt.load_events(bt.report_stations(common.nightly_reports()), first, last)
    by_event = {e["event_id"]: e for e in events}
    markets, bands = lr.read_definitions()
    mapping = parity.event_crosswalk(markets, list(common.market_rows("events")), bands, list(common.market_rows("bands")))
    matched = []
    for e in lr.live_events(markets, bands, cities, first, last):
        event = by_event.get(mapping.get(e["event_id"]))
        if event is None:
            continue
        if event["bands"] != e["bands"] or event["station"] != e["station"] or event["unit"] != e["unit"]:
            raise ValueError("live and labeled venue ladders differ")
        matched.append({**event, "band_ids": e["band_ids"]})
    calls, vectors = recorded(first, last, through, checkpoints)
    rows, excluded = [], collections.Counter()
    for call in calls:
        payload = vectors.get(str(call["checkpoint_id"]))
        if payload is None:
            excluded["missing_on_time_recorded_vector"] += 1
            continue
        chosen = [e for e in matched if e["city"] == call["city_key"] and e["date"] == call["target_date"]
                  and call["top_band_id"] in e["band_ids"]]
        if len(chosen) != 1 or chosen[0]["event_id"] not in labels:
            excluded["unsettled_or_missing_exact_identity"] += 1
            continue
        r = attach(call, payload, chosen[0], labels[chosen[0]["event_id"]], include_current=include_current)
        if r is None:
            excluded["ineligible_or_missing_first_call_market"] += 1
            continue
        rows.append(r)
    return rows, {"first_calls": len(calls), "paired_rows": len(rows), "excluded": dict(excluded),
                  "feature_policy": inputs.POLICY, "uses_reconstructed_features": False}
