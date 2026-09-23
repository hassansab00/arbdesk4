"""Rows leave Postgres only after their archive file is ON GITHUB (plan v2 P1.5).

The archive workflow's push loop could run out after four failed pushes and
still exit 0, because its last command was a `git pull --rebase` that
succeeded. The prune then asked is_committed(), which checked local HEAD - and
the file was in local HEAD, on a runner about to be discarded. Nothing had
been lost yet, but only because no push had failed four times in a row.

Also here: a prune function refusing its keep_days is reported as a refusal,
with status `error` and the reason, not as "ARCHIVE COUNT MISMATCH" (P1.3).
"""
import datetime as dt
import os
import pathlib
import subprocess
import sys
import types

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import archive_observations as ao  # noqa: E402

WORKFLOW = (ROOT / ".github" / "workflows" / "archive_observations.yml").read_text()


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def _repo(tmp_path):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    _git(work, "checkout", "-b", "main")
    (work / "seed").write_text("x")
    _git(work, "add", "seed")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "origin", "main")
    return work


def test_a_commit_that_never_left_the_runner_is_not_committed(tmp_path):
    work = _repo(tmp_path)
    (work / "data").mkdir()
    (work / "data" / "rows.csv.gz").write_bytes(b"\x1f\x8b rows")
    _git(work, "add", "data/rows.csv.gz")
    _git(work, "commit", "-m", "archive")

    ok, why = ao.is_committed("data/rows.csv.gz", branch="main", root=str(work))
    assert (ok, why) == (False, "not on origin/main"), (
        "a file in local HEAD only counted as archived - a failed push would have "
        "let the prune delete rows whose only copy dies with the runner")

    _git(work, "push", "origin", "main")
    assert ao.is_committed("data/rows.csv.gz", branch="main", root=str(work)) == (True, "on origin/main")


def test_a_file_changed_after_its_push_is_not_committed(tmp_path):
    work = _repo(tmp_path)
    (work / "f.gz").write_bytes(b"one")
    _git(work, "add", "f.gz")
    _git(work, "commit", "-m", "a")
    _git(work, "push", "origin", "main")
    (work / "f.gz").write_bytes(b"two")
    assert ao.is_committed("f.gz", branch="main", root=str(work)) == (False, "differs from origin/main")


def test_the_workflow_prunes_only_after_a_confirmed_push():
    doc = yaml.safe_load(WORKFLOW)
    steps = doc["jobs"]["archive"]["steps"]
    commit = next(s for s in steps if s.get("name", "").startswith("Commit the data"))
    prune = next(s for s in steps if s.get("name", "").startswith("Prune"))
    assert commit.get("id") == "commit"
    assert 'pushed=1' in commit["run"] and 'if [ "$pushed" != "1" ]' in commit["run"], (
        "the push loop can run out and still let the step succeed")
    assert commit["run"].count('echo "ok=1" >> "$GITHUB_OUTPUT"') == 2, (
        "ok=1 must be written for 'pushed' and for 'nothing new to push', and nowhere else")
    assert "steps.commit.outputs.ok == '1'" in prune["if"], (
        "the prune runs whether or not the push was confirmed")
    index = next(s for s in steps if s.get("name", "") == "Commit the archive index")
    assert 'if [ "$pushed" != "1" ]' in index["run"], "the index push can fail silently"


def test_a_refused_preflight_is_logged_as_an_error_with_its_reason(monkeypatch):
    logged = []
    reason = "keep_days must be at least 2 - a capture written this morning is still being read"
    monkeypatch.setattr(ao, "effective_keep_days", lambda spec, override: (1, {"over": True}))
    monkeypatch.setattr(ao, "refresh_feature_cache", lambda *a, **k: None)
    monkeypatch.setattr(ao, "finish_stranded_export", lambda *a, **k: None)
    monkeypatch.setattr(ao, "export_cold", lambda spec, cutoff: (b"blob", 19127, "2026-09-20", "2026-09-21"))
    monkeypatch.setattr(ao, "_rpc", lambda fn, params=None: {"ok": False, "error": reason})
    monkeypatch.setattr(ao, "log_run", lambda job, status, rows, detail: logged.append((job, status, rows, detail)))
    monkeypatch.setattr(ao, "write_repo_archive",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrote a file after a refusal")))

    rc = ao.export_one(ao.TABLES["research"], "research", types.SimpleNamespace(keep_days=None))
    assert rc == 1
    assert len(logged) == 1
    job, status, rows, detail = logged[0]
    assert (job, status) == ("archive_research", "error"), (
        "a refusal was logged as attention, which the watchdog does not count")
    assert detail["refused"] == reason
    assert rows == 0


def test_a_genuine_count_mismatch_is_still_called_one(monkeypatch, capsys):
    logged = []
    monkeypatch.setattr(ao, "effective_keep_days", lambda spec, override: (2, {"over": True}))
    monkeypatch.setattr(ao, "refresh_feature_cache", lambda *a, **k: None)
    monkeypatch.setattr(ao, "finish_stranded_export", lambda *a, **k: None)
    monkeypatch.setattr(ao, "export_cold", lambda spec, cutoff: (b"blob", 100, "2026-09-20", "2026-09-21"))
    monkeypatch.setattr(ao, "_rpc", lambda fn, params=None: {"ok": True, "would_delete": 99})
    monkeypatch.setattr(ao, "log_run", lambda job, status, rows, detail: logged.append(status))
    assert ao.export_one(ao.TABLES["research"], "research", types.SimpleNamespace(keep_days=None)) == 1
    assert "ARCHIVE COUNT MISMATCH" in capsys.readouterr().err
