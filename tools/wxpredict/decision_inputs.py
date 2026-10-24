"""The engineered row actually used at a first decision, not a later replay.

Receipt time and the checkout's commit time accompany each feature vector.
A retry using a newer history vintage cannot masquerade as an on-time call.
"""
import datetime as dt
import hashlib
import json
import subprocess

from wxpredict import common, features

KIND = "wx_features"
POLICY = "wx-arrival-480-v1"
MAX_DELAY_S = 30 * 60
LABELS = {"winner", "winner_lo", "winner_hi", "station_max_unit", "station_max_c", "station_in_winner",
          "station_reports_day", "label_unit"}


def unix(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def history_identity():
    out = subprocess.check_output(["git", "show", "-s", "--format=%H%n%ct", "HEAD"],
                                  cwd=common.ROOT, text=True).splitlines()
    return out[0], int(out[1])


def pack(served, event, row, commit, committed_at, current=None):
    vector = {k: None if k in LABELS else row.get(k) for k in features.COLUMNS}
    if row.get('observation_contract'):
        vector.update(observation_contract=row['observation_contract'],rules_sha256=event.get('rules_sha256'),
                      authority_input=row.get('authority_input'))
    raw = json.dumps(vector, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload = {"policy": POLICY, "checkpoint_id": served["checkpoint_id"], "city_key": served["city_key"],
               "target_date": str(served["target_date"]), "checkpoint": served["checkpoint"],
               "decided_at": served["decided_at"], "band_ids": list(event["band_ids"]),
               "history_commit": commit, "history_committed_at": committed_at,
               "row": vector, "row_sha256": hashlib.sha256(raw.encode()).hexdigest()}
    if current is not None:
        from wxpredict import current_forecasts
        if set(current) != set(current_forecasts.FIELDS):
            raise ValueError("unexpected current-forecast predictors")
        encoded = json.dumps(current, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload.update(current_forecasts=current,
                       current_forecasts_sha256=hashlib.sha256(encoded.encode()).hexdigest())
    return {"key": "|".join((POLICY, served["city_key"], str(served["target_date"]), served["checkpoint"])), "payload": payload}


def eligible(payload, captured_at):
    if payload.get("policy") != POLICY:
        return False
    decided = unix(payload["decided_at"])
    captured = unix(captured_at)
    if not decided <= captured <= decided + MAX_DELAY_S or payload["history_committed_at"] > decided:
        return False
    row = payload["row"]
    if int(row["decision_utc"]) != int(decided) or row["city_key"] != payload["city_key"] or row["date"] != payload["target_date"]:
        raise ValueError("recorded decision inputs name a different call")
    if any(row.get(k) is not None for k in LABELS):
        raise ValueError("recorded predictive inputs contain an outcome")
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if hashlib.sha256(raw.encode()).hexdigest() != payload["row_sha256"]:
        raise ValueError("recorded decision input hash mismatch")
    current_context(payload)
    return True


def current_context(payload):
    """The recorded context only, verified independently of the frozen row."""
    if "current_forecasts" not in payload:
        return None
    from wxpredict import current_forecasts
    context = payload["current_forecasts"]
    if not isinstance(context, dict) or set(context) != set(current_forecasts.FIELDS):
        raise ValueError("unexpected current-forecast predictors")
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if hashlib.sha256(encoded.encode()).hexdigest() != payload.get("current_forecasts_sha256"):
        raise ValueError("current-forecast input hash mismatch")
    return dict(context)
