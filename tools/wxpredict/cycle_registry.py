"""Candidate lineage and an atomic shadow pointer; no serving or capital writes."""
import datetime as dt
import hashlib
import json
import os

from wxpredict import common

REGISTRY = "data/models/wxrd/auto_registry.json"
POINTER = "data/models/wxrd/shadow-current.json"


def read(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = (json.dumps(value, indent=1, sort_keys=True) + "\n").encode()
    with open(path + ".tmp", "wb") as fh:
        fh.write(raw)
    os.replace(path + ".tmp", path)
    return hashlib.sha256(raw).hexdigest()


def registry(root=None):
    return read(os.path.join(root or common.ROOT, REGISTRY), {"schema": 1, "paused": False, "history": []})


def selected(root=None):
    root = root or common.ROOT
    # The registry is canonical and atomic. The separate pointer is only a
    # cache-key hint; a crash before writing it cannot expose partial lineage.
    pointer = registry(root).get("current")
    if pointer is None:
        return None
    if pointer.get("schema") != 1 or pointer.get("variant") != "wxrd_auto":
        raise ValueError("invalid automatic shadow pointer")
    rel = pointer["metadata"]
    if not rel.startswith("data/models/wxrd/candidates/") or ".." in rel.split("/"):
        raise ValueError("candidate metadata is outside its registry")
    path = os.path.join(root, rel)
    with open(path, "rb") as fh:
        raw = fh.read()
    if hashlib.sha256(raw).hexdigest() != pointer["metadata_sha256"]:
        raise ValueError("candidate metadata hash mismatch")
    meta = json.loads(raw)
    if meta["model_sha256"] != pointer["model_sha256"]:
        raise ValueError("candidate pointer names different weights")
    return path, meta


def record(report, meta=None, root=None):
    root = root or common.ROOT
    state = registry(root)
    if any(r["night"] == report["night"] and r["action"] == "cycle" for r in state["history"]):
        raise ValueError("this night's candidate cycle is already recorded")
    entry = {"night": report["night"], "action": "cycle", "decision": report["decision"],
             "fitted": bool(report.get('training_rows')),
             "report": "data/eval/wxpredict/cycles/" + report["night"] + ".json"}
    if meta is not None:
        if report["decision"] != "accept shadow" or state["paused"]:
            raise ValueError("an unaccepted or paused model cannot become shadow")
        rel = "data/models/wxrd/candidates/" + meta["version"] + ".json"
        path = os.path.join(root, rel)
        if os.path.exists(path):
            if read(path) != meta:
                raise ValueError("candidate metadata already exists; refusing to rewrite it")
            with open(path, "rb") as fh:
                metadata_sha = hashlib.sha256(fh.read()).hexdigest()
        else:
            metadata_sha = write(path, meta)
        entry.update(model_id=meta["version"], metadata=rel, metadata_sha256=metadata_sha,
                     model_sha256=meta["model_sha256"])
        state["current"] = {"schema": 1, "variant": "wxrd_auto", **{
            k: entry[k] for k in ("metadata", "metadata_sha256", "model_sha256")}}
        state['current']['available_at']=dt.datetime.now(common.UTC).isoformat()
        state.pop('forward_validated',None)
    if report['decision']=='forward validated':
        state['forward_validated']=dict(model_sha256=report['model_sha256'],
            report=entry['report'],forward_last=report['forward_last'])
    if report['decision']=='forward rejected':
        entry['retired_candidate']=state.pop('current',None)
        state.pop('forward_validated',None)
    write(os.path.join(root, entry["report"]), report)
    state["history"].append(entry)
    write(os.path.join(root, REGISTRY), state)
    if meta is not None:
        write(os.path.join(root, POINTER), state["current"])


def rollback(model_id, root=None):
    root = root or common.ROOT
    state = registry(root)
    if model_id == "baseline":
        previous = state.pop("current", None)
        state["paused"] = True
        state["history"].append({"action": "rollback", "night": dt.datetime.now(common.UTC).isoformat(),
                                 "model_id": "baseline", "previous": previous})
        write(os.path.join(root, REGISTRY), state)
        write(os.path.join(root, POINTER), {"schema": 1, "variant": "wxrd_auto", "disabled": True})
        return
    entries = [r for r in state["history"] if r.get("model_id") == model_id and r.get("decision") == "accept shadow"]
    if not entries:
        raise ValueError("rollback target was never an accepted candidate")
    entry = entries[-1]
    pointer = {"schema": 1, "variant": "wxrd_auto", **{
        k: entry[k] for k in ("metadata", "metadata_sha256", "model_sha256")}}
    # Validate the target in isolation before touching the live pointer.
    with open(os.path.join(root, entry["metadata"]), "rb") as fh:
        raw = fh.read()
    if hashlib.sha256(raw).hexdigest() != entry["metadata_sha256"]:
        raise ValueError("rollback metadata hash mismatch")
    from wxpredict import model_artifacts
    meta=json.loads(raw)
    for model in meta.get('models') or [meta]:
        model_artifacts.ensure(dict(asset_url=model['asset_url'],
                                    model_sha256=model.get('sha256') or model['model_sha256']),root)
    state["paused"] = True
    state["current"] = pointer
    state["history"].append({"action": "rollback", "night": dt.datetime.now(common.UTC).isoformat(), "model_id": model_id})
    write(os.path.join(root, REGISTRY), state)
    write(os.path.join(root, POINTER), pointer)
