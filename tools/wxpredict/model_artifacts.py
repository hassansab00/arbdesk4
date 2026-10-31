"""Immutable candidate weights in repository Releases; hash-checked local cache."""
import gzip
import hashlib
import io
import os
import re
import time
import urllib.parse

import requests

from wxpredict import common

REPO = "hassansab00/arbdesk4"
MAX_COMPRESSED = 32 * 1024 * 1024
MAX_RAW = 256 * 1024 * 1024


def validate_blob(blob, expected):
    if len(blob) > MAX_COMPRESSED:
        raise ValueError("candidate weight download exceeds its bound")
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as fh:
        raw = fh.read(MAX_RAW + 1)
    if len(raw) > MAX_RAW or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("candidate weight hash mismatch or oversized model")
    return raw


def ensure(meta, root=None, force_remote=False, budget_s=60):
    if not 0 < budget_s <= 60:
        raise ValueError('invalid candidate download time budget')
    url = meta["asset_url"]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith("/" + REPO + "/releases/download/"):
        raise ValueError("candidate weights must come from this repository's Releases")
    sha = meta["model_sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise ValueError("invalid candidate model hash")
    path = os.path.join(root or common.ROOT, ".cache/wxpredict-models", sha + ".txt.gz")
    if os.path.exists(path) and not force_remote:
        with open(path, "rb") as fh:
            validate_blob(fh.read(), sha)
        return path
    started = time.monotonic()
    with requests.get(url, stream=True, timeout=(min(10, budget_s/2), min(20, budget_s/2))) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            if time.monotonic() - started > budget_s:
                raise TimeoutError("candidate weight download exceeded its time budget")
            size += len(chunk)
            if size > MAX_COMPRESSED:
                raise ValueError("candidate weight download exceeds its bound")
            chunks.append(chunk)
    blob = b"".join(chunks)
    validate_blob(blob, sha)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as fh:
        fh.write(blob)
    os.replace(path + ".tmp", path)
    return path


def publish(path, model_id, sha, *, validated=True):
    """Upload once, then read the public asset back before registering a model."""
    if not re.fullmatch(r"wxrd-auto-[0-9]{8}-[a-f0-9]{12}", model_id):
        raise ValueError("invalid candidate artifact id")
    with open(path, "rb") as fh:
        blob = fh.read()
    validate_blob(blob, sha)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is needed to publish an accepted shadow candidate")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}
    base = "https://api.github.com/repos/" + REPO
    tag = "wxpredict-" + model_id
    response = requests.get(base + "/releases/tags/" + tag, headers=headers, timeout=30)
    if response.status_code == 404:
        response = requests.post(base + "/releases", headers=headers,
            json={"tag_name": tag, "name": model_id, "prerelease": True,
                  "body": ("Validated shadow candidate weights. " if validated else "Research challenger awaiting future validation. ")+"Raw model SHA256: "+sha}, timeout=30)
    response.raise_for_status()
    release = response.json()
    name = model_id + ".txt.gz"
    existing = [a for a in release.get("assets", []) if a["name"] == name]
    if not existing:
        response = requests.post("https://uploads.github.com/repos/" + REPO + "/releases/" + str(release["id"]) + "/assets",
            params={"name": name}, headers={**headers, "Content-Type": "application/gzip"}, data=blob, timeout=90)
        response.raise_for_status()
    url = "https://github.com/" + REPO + "/releases/download/" + tag + "/" + name
    ensure({"asset_url": url, "model_sha256": sha}, force_remote=True)
    return url
