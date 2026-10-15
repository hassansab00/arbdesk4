"""Append-only IEM response versions, including corrections and same-minute reports.

The legacy station files keep their first answers for frozen experiments.
This separate journal records when a response was actually received. It does
not pretend a historical report was received at its meteorological valid time.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import os

from wxpredict import common

HEADER = ["station", "valid", "received_at", "response_sha256", "payload"]


def paths():
    root = os.path.join(os.path.dirname(common.REPORTS), "station_report_versions")
    return root, common.nightly_files(root, "versions")


def stamp(value):
    value = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("report receipt must include its timezone")
    return value.astimezone(common.UTC)


def versions():
    for path in paths()[1]:
        yield from common.read_csv(path)


def append_response(text, stations, fields, received_at=None):
    received = stamp(received_at) if received_at else dt.datetime.now(common.UTC)
    icao = {common.iem_id(s): s for s in stations}
    grouped = {}
    for row in csv.DictReader(io.StringIO(text)):
        station = icao.get((row.get("station") or "").strip().upper())
        if station is None:
            continue
        valid = (row.get("valid") or "").strip().replace(" ", "T") + "Z"
        if stamp(valid) > received + dt.timedelta(minutes=5):
            raise ValueError("station report is dated after its receipt")
        values = [(row.get(f) or "").strip() for f in fields]
        held = grouped.setdefault((station, valid), [])
        if values not in held:
            held.append(values)
    latest = {}
    for row in versions():
        key = row["station"], row["valid"]
        old = latest.get(key)
        if old is None or stamp(row["received_at"]) > stamp(old["received_at"]):
            latest[key] = row
    root, _ = paths()
    path = os.path.join(root, "versions-" + received.date().isoformat() + ".csv.gz")
    kept = list(common.read_csv(path)) if os.path.exists(path) else []
    new = []
    for key, rows in sorted(grouped.items()):
        payload = json.dumps({"schema": "iem-report-versions-v1", "fields": list(fields),
                              "rows": rows, "selected": len(rows) - 1},
                             sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        previous = latest.get(key)
        if previous and previous["response_sha256"] == digest:
            continue
        if previous and stamp(previous["received_at"]) >= received:
            raise ValueError("conflicting report versions at the same or earlier receipt")
        new.append(dict(zip(HEADER, [*key, received.isoformat(), digest, payload])))
    if new:
        rows = sorted(kept + new, key=lambda r: (r["station"], r["valid"], stamp(r["received_at"])))
        common.write_csv(path, HEADER, ([r[h] for h in HEADER] for r in rows))
    return {"versions_added": len(new), "same_minute_report_sets": sum(len(v) > 1 for v in grouped.values()),
            "received_at": received.isoformat()}


def asof(station, decision, report_lag_s=480):
    """Raw selected reports actually received by a decision, without hindsight.

    Earlier records with no receipt are deliberately absent, not assigned a
    fabricated arrival. Every distinct raw report remains in the journal.
    """
    decision = stamp(decision)
    latest = {}
    for row in versions():
        if row["station"] != station or stamp(row["received_at"]) > decision:
            continue
        if stamp(row["valid"]) + dt.timedelta(seconds=report_lag_s) > decision:
            continue
        old = latest.get(row["valid"])
        if old is None or stamp(row["received_at"]) > stamp(old["received_at"]):
            latest[row["valid"]] = row
    out = []
    for valid, row in sorted(latest.items()):
        if hashlib.sha256(row["payload"].encode()).hexdigest() != row["response_sha256"]:
            raise ValueError("station report journal hash mismatch")
        payload = json.loads(row["payload"])
        if payload.get("schema") != "iem-report-versions-v1":
            raise ValueError("unknown report journal schema")
        raw = dict(zip(payload["fields"], payload["rows"][payload["selected"]]))
        raw.update(station=station, valid=valid.removesuffix("Z").replace("T", " "))
        out.append(raw)
    return out
