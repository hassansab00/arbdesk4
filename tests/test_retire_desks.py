"""tools/retire_desks.py: the export has to prove itself before any desk retires.

Retiring the desks (plan v2 P0.3) deletes nothing, but it is the one moment
their whole record is copied into the repository, so the copy has to be
checkable: the same rows always give the same bytes, a file that does not match
its manifest is caught, and "committed" means on the remote, not on the runner.
"""
import importlib.util
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("retire_desks", ROOT / "tools" / "retire_desks.py")
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)


ROWS = [{"account_id": "a1", "cash": 100, "name": "Desk"},
        {"account_id": "a2", "cash": 4470.24443, "name": "Other"}]


def _write_export(tmp, tables=None, rows=ROWS):
    files = {}
    for table, order in (tables or rd.TABLES):
        blob = rd.encode(rows if table == "paper_accounts" else [])
        (tmp / f"{table}.jsonl.gz").write_bytes(blob)
        files[table] = {"file": f"{table}.jsonl.gz", "rows": len(rows) if table == "paper_accounts" else 0,
                        "order": order, "sha256": rd.sha256(blob), "gzip_bytes": len(blob)}
    (tmp / rd.MANIFEST).write_text(json.dumps({"files": files}))
    return files


def test_the_same_rows_always_give_the_same_bytes():
    assert rd.encode(ROWS) == rd.encode(ROWS), "gzip carried a timestamp, so a re-run cannot be compared"
    assert rd.decode(rd.encode(ROWS)) == ROWS


def test_a_clean_export_verifies(tmp_path):
    _write_export(tmp_path)
    manifest, problems = rd.check_manifest(str(tmp_path))
    assert problems == []
    assert manifest["files"]["paper_accounts"]["rows"] == 2


def test_a_changed_file_is_caught_by_its_sha256(tmp_path):
    _write_export(tmp_path)
    (tmp_path / "paper_accounts.jsonl.gz").write_bytes(rd.encode(ROWS[:1]))
    _, problems = rd.check_manifest(str(tmp_path))
    assert any("sha256" in p for p in problems)
    assert any("1 rows, the manifest says 2" in p for p in problems)


def test_a_duplicated_row_is_caught(tmp_path):
    files = _write_export(tmp_path, rows=ROWS + ROWS[:1])
    assert files["paper_accounts"]["rows"] == 3
    _, problems = rd.check_manifest(str(tmp_path))
    assert any("duplicate key" in p for p in problems)


def test_a_missing_table_is_caught(tmp_path):
    _write_export(tmp_path, tables=rd.TABLES[:-1])
    _, problems = rd.check_manifest(str(tmp_path))
    assert any("paper_activity" in p for p in problems), "an export without the cash history verified"


def test_every_table_that_holds_a_desk_is_exported():
    assert {t for t, _ in rd.TABLES} == {
        "paper_accounts", "paper_trade_plans", "paper_orders", "paper_positions",
        "paper_position_settlements", "paper_trades", "paper_activity"}


def test_only_the_old_nine_are_switched_off():
    ids = ["s1_buy_low_sell_signal", "s2_combination_arb", "s9_ladder_basket",
           "s10_winner", "s11_ladder", "system"]
    assert [i for i in ids if rd.OLD_STRATEGY.match(i)] == [
        "s1_buy_low_sell_signal", "s2_combination_arb", "s9_ladder_basket"]


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def test_a_local_commit_is_not_an_archive(tmp_path):
    """The file has to be on origin, as of a fresh fetch, before anything retires."""
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    _git(work, "checkout", "-b", "main")
    (work / "seed").write_text("x")
    _git(work, "add", "seed")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "origin", "main")

    (work / "export.json").write_text("{}")
    _git(work, "add", "export.json")
    _git(work, "commit", "-m", "export")
    assert rd.on_remote(["export.json"], root=str(work)) is False, \
        "a commit that never left the runner counted as archived"

    _git(work, "push", "origin", "main")
    assert rd.on_remote(["export.json"], root=str(work)) is True
