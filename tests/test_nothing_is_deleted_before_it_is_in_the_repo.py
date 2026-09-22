"""The rows go into the repository FIRST. Then, and only then, out of Postgres.

WHAT WAS WRONG. One process exported a table, uploaded the bytes to a GitHub
Release, and deleted the rows - in a single job, in that order, with the
commit of the index happening afterwards if at all. Two consequences, both of
which actually happened:

  * the index was written onto the runner and destroyed with the runner for
    six consecutive runs, so 623,768 archived rows existed with nothing in the
    repository able to name them

  * a Release is attached to a repository, not in it. You cannot clone it,
    grep it, or open it in the tree; reading one back takes the API and a
    token. Thirteen months of observations lived only there.

WHAT IT IS NOW. Two phases with a commit between them:

    --export-only   writes data/archive/<dataset>/<range>.csv.gz, reads it
                    back off disk, records it in the index. Deletes nothing.
    (the workflow commits and pushes data/archive)
    --prune-only    re-reads each file, counts the rows IN IT, asks git
                    whether it is in HEAD, and only then tells the database
                    to delete exactly that many.

So the failure that loses data - dying between the delete and the commit -
cannot happen: the delete is on the far side of the commit. A push that fails
every retry leaves the files out of HEAD, and every prune refuses on its own
without needing the workflow to notice.
"""

import gzip
import io as _io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_observations as ao  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _gz(rows):
    buf = _io.StringIO()
    buf.write("a,b\n")
    for i in range(rows):
        buf.write(f"{i},x\n")
    return gzip.compress(buf.getvalue().encode())


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """A repo-shaped tmp dir with a real git history."""
    monkeypatch.setattr(ao, "_root", lambda: str(tmp_path))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "seed").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


def _commit_all(root, msg="archive"):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=root, check=True)


def _prune_calls(monkeypatch):
    calls = []

    def fake(fn, params=None):
        calls.append((fn, params))
        if fn == "request_reclaim":
            return {"ok": True, "job": f"ad4_reclaim_after_archive_{params['p_table']}"}
        return {"ok": True, "deleted": params["p_expected_rows"]}

    monkeypatch.setattr(ao, "_rpc", fake)
    monkeypatch.setattr(ao, "log_run", lambda *a, **k: None)
    return calls


def _pend(root, rows=100, file="data/archive/observations/observations-a-to-b.csv.gz"):
    path = root / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_gz(rows))
    ao.save_pending({"observations": {
        "file": file, "asset": Path(file).name, "rows": rows,
        "cutoff": "2026-06-01", "keep_days": 90, "exported_at": "2026-09-21T00:00:00+00:00"}})
    return path


def test_an_uncommitted_file_stops_the_prune(desk, monkeypatch):
    """THE GUARANTEE. The file exists and its row count is right, but it has
    not been committed - so the rows must stay in the database."""
    _pend(desk)
    calls = _prune_calls(monkeypatch)

    rc = ao.prune_one(ao.TABLES["observations"], "observations", object())

    assert rc == 1
    assert calls == [], "rows were deleted while the only copy was on the runner"


def test_a_committed_file_lets_the_prune_through(desk, monkeypatch):
    _pend(desk)
    _commit_all(desk)
    calls = _prune_calls(monkeypatch)

    rc = ao.prune_one(ao.TABLES["observations"], "observations", object())

    assert rc == 0
    fn, params = calls[0]
    assert fn == "prune_observations"
    assert params["p_expected_rows"] == 100
    assert params["p_dry_run"] is False
    # THEN, and only then, the reclaim that returns the freed pages to the
    # tier - scheduled behind the prune rather than on a clock that assumed
    # the prune had already happened.
    assert calls[1:] == [("request_reclaim", {"p_table": "weather_observations"})]


def test_a_file_edited_after_the_commit_stops_the_prune(desk, monkeypatch):
    """In HEAD is not enough - it has to be the SAME file.

    The replacement here holds the SAME 100 rows and only differs in bytes
    (a different gzip level). So the row-count re-read passes, and the ONLY
    thing that can catch it is asking git whether the working tree still
    matches HEAD. Written that way on purpose: an earlier version of this
    test swapped in a corrupt file, which the row count rejected by itself,
    and it went on passing with the git check deleted.
    """
    path = _pend(desk)
    _commit_all(desk)

    payload = gzip.decompress(path.read_bytes())
    path.write_bytes(gzip.compress(payload, compresslevel=1))
    assert ao.count_rows(path.read_bytes()) == 100, "the fixture no longer isolates the git check"
    assert ao.verify_repo_archive(str(path), 100)[1], "the row count alone would have caught this"

    calls = _prune_calls(monkeypatch)
    assert ao.prune_one(ao.TABLES["observations"], "observations", object()) == 1
    assert calls == [], "a file changed after it was committed still authorised a delete"


def test_the_count_comes_from_the_file_not_from_the_exporter(desk, monkeypatch):
    """The exporter says 100; the file holds 60. The database must be told
    60 - or, since they disagree, nothing at all."""
    path = _pend(desk, rows=100)
    path.write_bytes(_gz(60))
    _commit_all(desk)
    calls = _prune_calls(monkeypatch)

    assert ao.prune_one(ao.TABLES["observations"], "observations", object()) == 1
    assert calls == [], "the prune trusted the exporter over the committed file"


def test_a_missing_file_stops_the_prune(desk, monkeypatch):
    _pend(desk).unlink()
    calls = _prune_calls(monkeypatch)
    assert ao.prune_one(ao.TABLES["observations"], "observations", object()) == 1
    assert calls == []


def test_nothing_pending_is_not_a_failure(desk, monkeypatch):
    ao.save_pending({})
    calls = _prune_calls(monkeypatch)
    assert ao.prune_one(ao.TABLES["observations"], "observations", object()) == 0
    assert calls == []


def test_export_writes_into_the_repo_and_deletes_nothing(desk, monkeypatch):
    monkeypatch.setattr(ao, "refresh_feature_cache", lambda: None)
    monkeypatch.setattr(ao, "export_cold",
                        lambda spec, cutoff: (_gz(42), 42, "2026-06-01", "2026-06-02"))
    monkeypatch.setattr(ao, "log_run", lambda *a, **k: None)
    calls = []

    def fake_rpc(fn, params=None):
        calls.append((fn, params))
        return {"ok": True, "would_delete": 42}
    monkeypatch.setattr(ao, "_rpc", fake_rpc)

    class Args:
        keep_days = 90
        commit = True
    rc = ao.export_one(ao.TABLES["observations"], "observations", Args())

    assert rc == 0
    # The export DOES call the database - once, to ask whether it agrees the
    # exported range is the range it would delete. That call must be a dry
    # run: the export phase is not allowed to remove anything.
    assert [fn for fn, _ in calls] == ["prune_observations"]
    assert calls[0][1]["p_dry_run"] is True, "the export phase issued a committed prune"
    written = list((desk / "data" / "archive" / "observations").glob("*.csv.gz"))
    assert len(written) == 1, f"nothing landed in data/archive: {written}"
    assert ao.count_rows(written[0].read_bytes()) == 42
    assert ao.load_pending()["observations"]["rows"] == 42


def test_the_workflow_commits_before_it_prunes():
    """The ordering lives in the workflow, so assert it there too."""
    wf = (ROOT / ".github" / "workflows" / "archive_observations.yml").read_text()
    export = wf.index("--export-only")
    commit = wf.index("Commit the data BEFORE anything is deleted")
    prune = wf.index("--prune-only")
    assert export < commit < prune, (
        "the workflow no longer commits the archive between exporting and pruning"
    )
    assert "git add data/archive" in wf, "data/archive is not committed by the workflow"
