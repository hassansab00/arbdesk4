"""--pull-releases copies what is only in a Release into data/archive.

IT SHIPPED WITH NO TEST AND DIED ON ITS FIRST RUN, at line one of its own
loop:

    AttributeError: 'Response' object has no attribute 'get'

gh() returns a raw requests.Response. The Release-upload code of the time
checked .status_code and called .json(); this called .get() straight on the
Response.
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


def test_a_short_local_file_is_reported_rather_than_silently_replaced(desk, monkeypatch):
    """This used to re-download on any size mismatch, to repair a truncated
    local copy. That rule is what let a Release asset overwrite a verified
    export, so the repo now wins unconditionally - and a short file is
    handled where it can be handled safely instead:

      * prune_one re-reads the file and REFUSES if the count disagrees with
        the export record, so no rows leave Postgres against it
      * reconcile_index rewrites the index to the file's true count, so the
        shortfall is visible rather than papered over by a stale number

    Degrading to "nothing is deleted and the index tells the truth" is the
    right failure. Degrading to "restore from whatever a Release holds" is
    how a verified 51,504-row export became a 55,203-row one nobody chose.
    """
    full = _gz(9)
    path = desk / "data" / "archive" / "observations" / "observations-a-to-b.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(_gz(4))                      # short

    tag = ao.TABLES["observations"]["tag"]
    _releases(monkeypatch, {tag: [
        {"name": "observations-a-to-b.csv.gz", "size": len(full), "url": "u1"}]})
    fetched = []
    monkeypatch.setattr(ao.requests, "get",
                        lambda url, headers=None, timeout=None:
                        fetched.append(url) or FakeResponse(content=full))

    idx = desk / "web" / "public" / "archive" / "index.json"
    idx.parent.mkdir(parents=True)
    idx.write_text(json.dumps({"datasets": {"observations": {
        "assets": [{"asset": "observations-a-to-b.csv.gz", "rows": 9,
                    "gzip_bytes": len(full), "from": "a", "to": "b"}],
        "rows_archived": 9}}}))

    assert ao.pull_releases(Args()) == 0
    assert fetched == [], "the Release was pulled over a file the repo owns"
    assert ao.count_rows(path.read_bytes()) == 4, "the local file was replaced"
    assert json.loads(idx.read_text())["datasets"]["observations"]["rows_archived"] == 4, (
        "the index still claims 9 rows for a file holding 4 - the shortfall is invisible"
    )


def test_no_token_is_refused_rather_than_half_done(desk, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert ao.pull_releases(Args()) == 1


# --- the repository wins, and the index says what the files say ------------
#
# The first version of --pull-releases re-downloaded whenever the sizes
# differed, treating the Release as the authority. It is not. The export
# phase writes the repo copy, reads it back off disk, counts it, and the
# prune deletes exactly those rows against it; a Release asset of the same
# name is whatever some older run uploaded.
#
# On 21 Sep that replaced a verified 51,504-row books export with a
# 55,203-row Release asset, and the index - written by the export - stopped
# describing the file next to it. Nothing was lost, because the replacement
# happened to be the larger set. That was luck.


def test_an_existing_file_is_never_overwritten_by_a_release(desk, monkeypatch):
    """THE BUG. The repo file and the Release asset differ; the repo wins."""
    mine = _gz(51504)
    theirs = _gz(55203)
    path = desk / "data" / "archive" / "books" / "books-a-to-b.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(mine)

    tag = ao.TABLES["books"]["tag"]
    _releases(monkeypatch, {tag: [
        {"name": "books-a-to-b.csv.gz", "size": len(theirs), "url": "u1"}]})
    fetched = []

    def fake_get(url, headers=None, timeout=None):
        fetched.append(url)
        return FakeResponse(content=theirs)
    monkeypatch.setattr(ao.requests, "get", fake_get)

    assert ao.pull_releases(Args()) == 0
    assert fetched == [], "a Release asset was fetched over an export the prune had verified"
    assert ao.count_rows(path.read_bytes()) == 51504, "the repo copy was replaced"


def test_the_index_is_corrected_to_match_the_files(desk, monkeypatch):
    """The drift that mismatch left behind, healed on the next run."""
    blob = _gz(55203)
    path = desk / "data" / "archive" / "books" / "books-a-to-b.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)

    idx = desk / "web" / "public" / "archive" / "index.json"
    idx.parent.mkdir(parents=True)
    idx.write_text(json.dumps({"datasets": {"books": {
        "table": "book_snapshots", "release_tag": "books-archive",
        "rows_archived": 51504,
        "assets": [{"asset": "books-a-to-b.csv.gz", "rows": 51504,
                    "gzip_bytes": 123, "from": "a", "to": "b"}]}}}))

    changed = ao.reconcile_index()

    assert changed == [("books-a-to-b.csv.gz", 51504, 55203)]
    after = json.loads(idx.read_text())["datasets"]["books"]
    assert after["assets"][0]["rows"] == 55203
    assert after["assets"][0]["gzip_bytes"] == len(blob)
    assert after["rows_archived"] == 55203, "the dataset total still quotes the old number"


def test_an_index_that_already_matches_is_left_alone(desk):
    blob = _gz(7)
    path = desk / "data" / "archive" / "books" / "books-a-to-b.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)
    idx = desk / "web" / "public" / "archive" / "index.json"
    idx.parent.mkdir(parents=True)
    idx.write_text(json.dumps({"datasets": {"books": {
        "assets": [{"asset": "books-a-to-b.csv.gz", "rows": 7,
                    "gzip_bytes": len(blob), "from": "a", "to": "b"}],
        "rows_archived": 7}}}))
    before = idx.read_text()

    assert ao.reconcile_index() == []
    assert idx.read_text() == before, "an index that already agreed was rewritten"


def test_an_entry_whose_file_is_not_here_yet_is_not_zeroed(desk):
    """Most assets live only in a Release until the pull brings them in. An
    entry with no local file must keep its recorded count, not be rewritten
    to nothing."""
    idx = desk / "web" / "public" / "archive" / "index.json"
    idx.parent.mkdir(parents=True)
    idx.write_text(json.dumps({"datasets": {"books": {
        "assets": [{"asset": "not-here.csv.gz", "rows": 999,
                    "gzip_bytes": 1, "from": "a", "to": "b"}],
        "rows_archived": 999}}}))

    assert ao.reconcile_index() == []
    assert json.loads(idx.read_text())["datasets"]["books"]["rows_archived"] == 999
