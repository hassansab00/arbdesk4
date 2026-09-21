"""--pull-releases copies what is only in a Release into data/archive.

IT SHIPPED WITH NO TEST AND DIED ON ITS FIRST RUN, at line one of its own
loop:

    AttributeError: 'Response' object has no attribute 'get'

gh() returns a raw requests.Response. ensure_release, right above it, checks
.status_code and calls .json(); this called .get() straight on the Response.
A missing release came back as a 404 Response rather than an exception, so
the try/except around it never fired either - it was guarding against the
wrong thing, and the run ended before a single byte was pulled.

Nothing here talks to GitHub. The point is the shape of what gh() hands back
and what this function does with it, which is exactly what went wrong.
"""

import gzip
import io as _io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_observations as ao  # noqa: E402


def _gz(rows):
    buf = _io.StringIO()
    buf.write("a,b\n")
    for i in range(rows):
        buf.write(f"{i},x\n")
    return gzip.compress(buf.getvalue().encode())


class FakeResponse:
    """What requests hands back - NOT a dict."""

    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class Args:
    repo = "o/r"


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr(ao, "_root", lambda: str(tmp_path))
    monkeypatch.setattr(ao, "log_run", lambda *a, **k: None)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    return tmp_path


def _releases(monkeypatch, by_tag):
    """gh() returns a Response; a missing tag is a 404 Response, not a raise."""
    def fake_gh(repo, token, method, path, **kw):
        tag = path.rsplit("/", 1)[-1]
        if tag not in by_tag:
            return FakeResponse(status=404)
        return FakeResponse(payload={"assets": by_tag[tag]})
    monkeypatch.setattr(ao, "gh", fake_gh)


def _downloads(monkeypatch, blobs):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(content=blobs[url])
    monkeypatch.setattr(ao.requests, "get", fake_get)


def test_a_release_is_read_through_json_not_indexed_as_a_dict(desk, monkeypatch):
    """The exact crash. gh() gives a Response and this must call .json()."""
    blob = _gz(7)
    tag = ao.TABLES["observations"]["tag"]
    _releases(monkeypatch, {tag: [
        {"name": "observations-a-to-b.csv.gz", "size": len(blob), "url": "u1"}]})
    _downloads(monkeypatch, {"u1": blob})

    assert ao.pull_releases(Args()) == 0

    out = desk / "data" / "archive" / "observations" / "observations-a-to-b.csv.gz"
    assert out.exists(), "the asset was not written into the repository"
    assert ao.count_rows(out.read_bytes()) == 7


def test_a_missing_release_is_skipped_not_fatal(desk, monkeypatch):
    """Six of the seven datasets had no release for most of this desk's life.
    A 404 must not end the run - the other six still have history to bring in."""
    blob = _gz(3)
    tag = ao.TABLES["edges"]["tag"]
    _releases(monkeypatch, {tag: [
        {"name": "edges-a-to-b.csv.gz", "size": len(blob), "url": "u1"}]})
    _downloads(monkeypatch, {"u1": blob})

    assert ao.pull_releases(Args()) == 0
    assert (desk / "data" / "archive" / "edges" / "edges-a-to-b.csv.gz").exists()


def test_it_skips_what_is_already_on_disk(desk, monkeypatch):
    """Idempotent, because it runs on every archive run."""
    blob = _gz(5)
    path = desk / "data" / "archive" / "observations" / "observations-a-to-b.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)

    tag = ao.TABLES["observations"]["tag"]
    _releases(monkeypatch, {tag: [
        {"name": "observations-a-to-b.csv.gz", "size": len(blob), "url": "u1"}]})
    fetched = []

    def fake_get(url, headers=None, timeout=None):
        fetched.append(url)
        return FakeResponse(content=blob)
    monkeypatch.setattr(ao.requests, "get", fake_get)

    assert ao.pull_releases(Args()) == 0
    assert fetched == [], "an asset already on disk at the right size was re-downloaded"


def test_a_file_that_changed_size_is_fetched_again(desk, monkeypatch):
    """The skip is on size, so a truncated local copy must not be kept."""
    blob = _gz(9)
    path = desk / "data" / "archive" / "observations" / "observations-a-to-b.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"short")

    tag = ao.TABLES["observations"]["tag"]
    _releases(monkeypatch, {tag: [
        {"name": "observations-a-to-b.csv.gz", "size": len(blob), "url": "u1"}]})
    _downloads(monkeypatch, {"u1": blob})

    assert ao.pull_releases(Args()) == 0
    assert ao.count_rows(path.read_bytes()) == 9


def test_no_token_is_refused_rather_than_half_done(desk, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert ao.pull_releases(Args()) == 1
