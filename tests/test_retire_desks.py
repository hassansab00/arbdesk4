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


# ---- the targeted mode (WXPredict build P.2; D5, Hassan 5 Oct) --------------
# Retire the desks of s1 and s3-s9, which have been switched off since 29 Sep,
# and nothing else: never the desks of s2, s10, s11, s12 or the Portfolio desk.

import pytest

EIGHT = ["s1_buy_low_sell_signal", "s3_concentration", "s4_tail_fade", "s5_running_max_lock",
         "s6_anchor_insurance", "s7_pre_peak_gradient", "s8_two_bucket_cover", "s9_ladder_basket"]
RUNNING = ["s2_combination_arb", "s10_growth", "s10_lock", "s10_winner", "s11_ladder", "s11_lock", "s12_no"]


class FakeDesks:
    """The three tables retire_listed reads and the one RPC it calls."""

    def __init__(self, enabled=None):
        self.strategies = {s: False for s in EIGHT}
        self.strategies.update({s: True for s in RUNNING})
        self.strategies.update(enabled or {})
        self.desks = [{"account_id": "portfolio", "name": "Portfolio", "status": "suspended", "strategy_id": None},
                      {"account_id": "old", "name": "Wide edge, all US", "status": "retired", "strategy_id": None}]
        self.desks += [{"account_id": f"d-{s}", "name": f"Shadow: {s}", "status": "active", "strategy_id": s}
                       for s in EIGHT + RUNNING]
        self.calls = []

    def rest(self, table, params):
        if table == "strategies":
            return [{"strategy_id": k, "enabled": v} for k, v in self.strategies.items()]
        if table == "paper_accounts":
            return [dict(d) for d in self.desks]
        if table == "paper_orders":
            return []
        raise AssertionError(f"unexpected read of {table}")

    def rpc(self, name, args):
        self.calls.append((name, args))
        if name == "paper_desk_retire":
            for d in self.desks:
                if d["account_id"] == args["p_account_id"]:
                    already = d["status"] == "retired"
                    d["status"] = "retired"
                    return {"already": already}
        raise AssertionError(f"unexpected rpc {name}")


def test_only_the_listed_desks_are_retired():
    db = FakeDesks()
    rd.retire_listed(EIGHT, "WXPredict build P.2", db.rest, db.rpc)
    retired = sorted(a["p_account_id"] for n, a in db.calls if n == "paper_desk_retire")
    assert retired == sorted(f"d-{s}" for s in EIGHT)
    status = {d["account_id"]: d["status"] for d in db.desks}
    assert all(status[f"d-{s}"] == "retired" for s in EIGHT)
    assert all(status[f"d-{s}"] == "active" for s in RUNNING), "a running strategy's desk was touched"
    assert status["portfolio"] == "suspended", "the Portfolio desk was touched"
    assert all(a["p_reason"] == "WXPredict build P.2" for _, a in db.calls)


def test_the_strategy_switch_is_not_called_in_this_mode():
    db = FakeDesks()
    rd.retire_listed(EIGHT, "r", db.rest, db.rpc)
    assert {n for n, _ in db.calls} == {"paper_desk_retire"}, "set_strategies_enabled was called"


def test_a_listed_strategy_that_is_switched_on_is_refused_and_nothing_retires():
    db = FakeDesks()
    with pytest.raises(SystemExit, match="switched on \\['s2_combination_arb'\\]"):
        rd.retire_listed(EIGHT + ["s2_combination_arb"], "r", db.rest, db.rpc)
    assert db.calls == [], "a desk was retired before the refusal"


def test_an_unknown_strategy_or_one_without_a_desk_is_refused():
    db = FakeDesks()
    with pytest.raises(SystemExit, match="no such strategy \\['s99_nope'\\]"):
        rd.retire_listed(["s1_buy_low_sell_signal", "s99_nope"], "r", db.rest, db.rpc)
    db.strategies["s13_new"] = False
    with pytest.raises(SystemExit, match="no desk for \\['s13_new'\\]"):
        rd.retire_listed(["s1_buy_low_sell_signal", "s13_new"], "r", db.rest, db.rpc)
    assert db.calls == []


def test_another_desk_changing_fails_the_acceptance():
    db = FakeDesks()
    real_rpc = db.rpc

    def rpc(name, args):          # a retire that also hits the Portfolio desk
        out = real_rpc(name, args)
        db.desks[0]["status"] = "retired"
        return out

    with pytest.raises(SystemExit, match="other desks changed \\['Portfolio'\\]"):
        rd.retire_listed(["s1_buy_low_sell_signal"], "r", db.rest, rpc)


def test_the_list_is_parsed_strictly():
    assert rd.parse_strategies(" s1_buy_low_sell_signal, s3_concentration ") == [
        "s1_buy_low_sell_signal", "s3_concentration"]
    for bad in ("", " , ", "s1_x;rm -rf", "S1_upper", "s1_x,s1_x", "all"):
        with pytest.raises(SystemExit):
            rd.parse_strategies(bad)


def test_with_no_list_today_s_retirement_is_unchanged(monkeypatch):
    """Every desk retired, then the old nine switched off: P0.3 as it ran on 23 Sep."""
    import common
    db = FakeDesks()
    switched = []

    def rpc(name, args):
        if name == "set_strategies_enabled":
            switched.append(args)
            return len(args["p_ids"])
        return db.rpc(name, args)

    monkeypatch.setattr(common, "rest", db.rest)
    monkeypatch.setattr(common, "rpc", rpc)
    monkeypatch.setattr(rd, "check_manifest", lambda d: ({"files": {}}, []))
    monkeypatch.setattr(rd, "on_remote", lambda paths: True)
    rd.retire("/tmp/export", "plan v2: engine rebuild")
    assert {a["p_account_id"] for n, a in db.calls if n == "paper_desk_retire"} == {d["account_id"] for d in db.desks}
    assert switched and switched[0]["p_enabled"] is False
    assert switched[0]["p_ids"] == ["s2_combination_arb"], "only an ENABLED s1-s9 strategy is switched off"


def test_the_targeted_mode_still_waits_for_the_export_on_origin(monkeypatch):
    import common
    db = FakeDesks()
    monkeypatch.setattr(common, "rest", db.rest)
    monkeypatch.setattr(common, "rpc", db.rpc)
    monkeypatch.setattr(rd, "check_manifest", lambda d: ({"files": {}}, []))
    monkeypatch.setattr(rd, "on_remote", lambda paths: False)
    with pytest.raises(SystemExit, match="not on origin/main"):
        rd.retire("/tmp/export", "r", EIGHT)
    assert db.calls == []


def test_retire_with_a_list_touches_only_the_listed_desks(monkeypatch):
    """Through retire() itself, not only retire_listed: the targeted run never
    falls through to the whole-desk retirement or the strategy switch."""
    import common
    db = FakeDesks()
    monkeypatch.setattr(common, "rest", db.rest)
    monkeypatch.setattr(common, "rpc", db.rpc)        # set_strategies_enabled would raise here
    monkeypatch.setattr(rd, "check_manifest", lambda d: ({"files": {}}, []))
    monkeypatch.setattr(rd, "on_remote", lambda paths: True)
    rd.retire("/tmp/export", "WXPredict build P.2", EIGHT)
    assert sorted(a["p_account_id"] for _, a in db.calls) == sorted(f"d-{s}" for s in EIGHT)
    assert {d["account_id"]: d["status"] for d in db.desks}["portfolio"] == "suspended"
