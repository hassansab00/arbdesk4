"""Verified cold live-capture rows from the committed mirror.

The live shadow needs up to 46 hours of inputs. Shortening database retention
is safe only if those older inputs remain readable. A stale checkout refuses
instead of silently changing a prediction's history.
"""
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os

from wxpredict import common


def unix(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def read(first, last, kinds=None, prefixes=None, root=None):
    """[(kind,key,captured_at,payload)] in [first,last), hash/count checked."""
    if first >= last:
        return []
    root = root or common.ROOT
    folder = os.path.join(root, "data/mirror")
    try:
        with open(os.path.join(folder, "manifest.json")) as fh:
            state = json.load(fh)["tables"]["live_capture"]
        if unix(state["through"]) < last:
            raise ValueError("mirror has not reached the cold-input boundary")
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError("live input mirror is unavailable; refusing incomplete history") from exc
    out = []
    for entry in state["files"]:
        if entry.get("from") and unix(entry["from"]) >= last or unix(entry["to"]) <= first:
            continue
        name = entry["file"]
        if os.path.basename(name) != name:
            raise ValueError("invalid capture mirror path")
        try:
            with open(os.path.join(folder, "live_capture", name), "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            raise ValueError("capture mirror file missing; pull main before predicting") from exc
        if hashlib.sha256(blob).hexdigest() != entry["sha256"]:
            raise ValueError("capture mirror hash mismatch")
        count = 0
        for r in csv.DictReader(io.StringIO(gzip.decompress(blob).decode())):
            count += 1
            if not first <= unix(r["captured_at"]) < last:
                continue
            if kinds and r["kind"] not in kinds or prefixes and not any(r["key"].startswith(p) for p in prefixes):
                continue
            out.append((r["kind"], r["key"], r["captured_at"], r["payload"]))
        if count != entry["rows"]:
            raise ValueError("capture mirror row count mismatch")
    return out
