"""A retention floor the prune function refuses is not a floor. It is a failure.

WHAT HAPPENED, 23 Sep 08:13. The first archive run to find the database over
its high-water mark (124% of the tier) dropped every dataset to its declared
floor, and three of those floors were below what the function that deletes
will accept:

    research     floor 1    prune_research_captures refuses under 2
    resolution   floor 1    prune_resolution_evidence refuses under 3
    trades       floor 14   prune_trades refuses under 30

Each exported its rows, had the dry run refuse them, and failed - and the
workflow then skipped the commit and the prune for ALL SEVEN datasets. Books,
edges, forecasts and observations had exported and verified cleanly: 138,152
rows landed in the repository and stayed in the database. Nothing moved.

Three things are held here. The floors are checked against the guards in the
SQL, parsed rather than restated, so a floor and its guard cannot drift apart
again. One refused dataset no longer strands the others. And an export whose
prune never ran is finished before anything new is exported, so the next run
cannot archive the same rows a second time.
"""

import gzip
import io as _io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import archive_observations as ao  # noqa: E402


# --- the floors ----------------------------------------------------------

def _guards():
    """{function name: the smallest p_keep_days it accepts}, from every SQL
    definition in the repository. A function defined in more than one file
    takes the strictest, since either may be the one applied last."""
    found = {}
    files = sorted((ROOT / "sql").glob("*.sql")) + sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"create\s+or\s+replace\s+function\s+(?:public\.)?(prune_\w+)\s*\(", text, re.I):
            body = text[m.end():]
            nxt = re.search(r"create\s+or\s+replace\s+function", body, re.I)
            body = body[:nxt.start()] if nxt else body
            g = re.search(r"p_keep_days\s*<\s*(\d+)", body)
            if g:
                found[m.group(1)] = max(found.get(m.group(1), 0), int(g.group(1)))
    return found


def test_every_prune_the_archive_calls_has_a_guard_to_check_against():
    """Otherwise the next test passes by finding nothing."""
    guards = _guards()
    missing = [s["prune_rpc"] for s in ao.TABLES.values() if s["prune_rpc"] not in guards]
    assert not missing, f"no p_keep_days guard found in the SQL for {missing}"


@pytest.mark.parametrize("name", sorted(ao.TABLES))
def test_no_window_the_archive_can_choose_is_one_the_database_refuses(name):
    spec = ao.TABLES[name]
    guard = _guards()[spec["prune_rpc"]]
    want = spec.get("keep_days", 90)
    floor = spec.get("min_keep_days", want)
    assert want >= guard, f"{name} keeps {want} days; {spec['prune_rpc']} refuses under {guard}"
    assert floor >= guard, (
        f"{name} drops to {floor} days under storage pressure and {spec['prune_rpc']} "
        f"refuses anything under {guard} - the day the database is over the tier is "
        f"the day this dataset stops archiving")


@pytest.mark.parametrize("name", sorted(ao.TABLES))
def test_where_the_database_leaves_room_the_floor_gives_something_back(name):
    """The other half. A dataset whose window is above its guard and whose
    floor is not below that window gives nothing back under pressure while
    every other dataset does."""
    spec = ao.TABLES[name]
    guard = _guards()[spec["prune_rpc"]]
    want = spec.get("keep_days", 90)
    if want > guard:
        assert spec["min_keep_days"] < want, (
            f"{name} could drop from {want} toward {guard} days under pressure and drops none")


@pytest.mark.parametrize("name", sorted(ao.TABLES))
def test_under_pressure_the_window_is_still_one_the_database_accepts(name, monkeypatch):
    """The same property through the function that chooses the window, on the
    path that failed: pressure reads over."""
    monkeypatch.setattr(ao, "_rpc", lambda fn, params=None: {"over": True, "verdict": "over"})
    spec = ao.TABLES[name]
    keep, why = ao.effective_keep_days(spec)
    assert keep >= _guards()[spec["prune_rpc"]], f"{name} chose {keep} days ({why})"


# --- one refused dataset does not strand the others ----------------------

def _steps():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "archive_observations.yml").read_text())
    return {s.get("name", ""): s for s in wf["jobs"]["archive"]["steps"]}


def test_the_commit_and_the_prune_run_after_another_dataset_refused():
    steps = _steps()
    commit = steps["Commit the data BEFORE anything is deleted"]
    prune = steps["Prune, now that the rows are in the repository"]
    assert "!cancelled()" in str(commit.get("if", "")), (
        "a refused export skips the commit of every OTHER dataset's verified file")
    assert "!cancelled()" in str(prune.get("if", "")), (
        "a refused export skips the prune of every other dataset")
    assert "github.event.inputs.commit == 'true'" in str(prune["if"]), (
        "a manual dry run must still never prune")


def test_a_refusal_still_fails_the_job():
    export = _steps()["Export cold rows into the repository"]
    assert not export.get("continue-on-error"), (
        "the export step's failure is what keeps a refused dataset red")


# --- an export whose prune never ran -------------------------------------

THROUGH = "2026-09-19T08:13:02.414887+00:00"
ASSET = "books-2026-09-15-to-2026-09-19.csv.gz"
FILE = f"data/archive/books/{ASSET}"


def _gz(rows):
    buf = _io.StringIO()
    buf.write("a,b\n")
    for i in range(rows):
        buf.write(f"{i},x\n")
    return gzip.compress(buf.getvalue().encode())


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """A repo-shaped tmp dir holding 23 Sep's state: a books file exported,
    committed and listed in the index, whose prune never ran."""
    monkeypatch.setattr(ao, "_root", lambda: str(tmp_path))
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp_path, check=True)
    (tmp_path / FILE).parent.mkdir(parents=True)
    (tmp_path / FILE).write_bytes(_gz(40))
    index = tmp_path / "web" / "public" / "archive" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"datasets": {"books": {"assets": [
        {"asset": "books-2026-09-14-to-2026-09-15.csv.gz", "rows": 8979,
         "archived_through": "2026-09-15T08:10:48+00:00"},
        {"asset": ASSET, "rows": 40, "archived_through": THROUGH,
         "archived_at": "2026-09-23T08:13:40+00:00"},
    ]}}}))
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "archive"], cwd=tmp_path, check=True)
    return tmp_path


class Args:
    keep_days = 7
    commit = True


def _wire(monkeypatch, *, recorded=False, still_there=40, probe_ok=True):
    """Stub the database. Returns (rpc calls, exports attempted, log lines)."""
    calls, exports, logged = [], [], []

    def fake_rpc(fn, params=None):
        calls.append((fn, dict(params or {})))
        if fn == "request_reclaim":
            return {"ok": True}
        if params.get("p_dry_run"):
            if params.get("p_before") != THROUGH:
                # today's export asking the database to agree with it
                return {"ok": True, "dry_run": True, "would_delete": params["p_expected_rows"]}
            if not probe_ok:
                return {"ok": False, "error": "statement timeout"}
            return {"ok": True, "dry_run": True, "would_delete": still_there}
        return {"ok": True, "deleted": params["p_expected_rows"]}

    def fake_export(spec, cutoff):
        exports.append(cutoff)
        return _gz(5), 5, "2026-09-19", "2026-09-20"

    monkeypatch.setattr(ao, "_rpc", fake_rpc)
    monkeypatch.setattr(ao, "export_cold", fake_export)
    monkeypatch.setattr(ao, "rest", lambda path, params=None: [{"log_id": 1}] if recorded else [])
    monkeypatch.setattr(ao, "log_run", lambda job, status, rows, detail: logged.append((status, detail)))
    return calls, exports, logged


def test_the_stranded_file_is_pruned_before_anything_new_is_exported(desk, monkeypatch):
    calls, exports, logged = _wire(monkeypatch)

    rc = ao.export_one(ao.TABLES["books"], "books", Args())

    assert rc == 0
    assert exports == [], "a new export would repeat the 40 rows the committed file already holds"
    pending = ao.load_pending()["books"]
    assert (pending["file"], pending["rows"], pending["cutoff"]) == (FILE, 40, THROUGH), (
        "the prune must be told the STRANDED file's cutoff and count, not today's")
    assert not any(p.get("p_dry_run") is False for _, p in calls), "the export phase deleted rows"

    rc = ao.prune_one(ao.TABLES["books"], "books", Args())
    assert rc == 0
    committed = [p for fn, p in calls if fn == "prune_book_redundancy" and p.get("p_dry_run") is False]
    assert committed == [{"p_keep_days": 7, "p_before": THROUGH,
                          "p_expected_rows": 40, "p_dry_run": False}]
    ok = [d for status, d in logged if status == "ok"]
    assert ok and ok[-1]["asset"] == ASSET and ok[-1]["resumed"] is True, (
        "the record must say this prune finished an earlier run's export")


def test_a_recorded_prune_means_there_is_nothing_to_finish(desk, monkeypatch):
    calls, exports, _ = _wire(monkeypatch, recorded=True, still_there=5)

    assert ao.export_one(ao.TABLES["books"], "books", Args()) == 0
    assert len(exports) == 1, "the normal export did not run"
    assert not any(p.get("p_before") == THROUGH for _, p in calls), (
        "a range whose prune is on record was probed again")


def test_a_database_that_no_longer_holds_the_range_means_carry_on(desk, monkeypatch):
    _, exports, _ = _wire(monkeypatch, still_there=0)
    assert ao.export_one(ao.TABLES["books"], "books", Args()) == 0
    assert len(exports) == 1


def test_rows_the_file_does_not_hold_stop_the_dataset(desk, monkeypatch):
    """45 rows older than the cutoff against a 40-row file: exporting repeats
    40 of them, pruning deletes 5 nobody archived. Neither is allowed."""
    calls, exports, logged = _wire(monkeypatch, still_there=45)

    assert ao.export_one(ao.TABLES["books"], "books", Args()) == 1
    assert exports == [] and "books" not in ao.load_pending()
    assert logged[-1][0] == "attention"
    assert (logged[-1][1]["still_in_database"], logged[-1][1]["file_rows"]) == (45, 40)


def test_a_file_that_is_not_in_head_is_not_proof(desk, monkeypatch):
    subprocess.run(["git", "rm", "-q", "--cached", FILE], cwd=desk, check=True)
    subprocess.run(["git", "commit", "-qm", "untrack"], cwd=desk, check=True)
    _, exports, _ = _wire(monkeypatch)

    assert ao.export_one(ao.TABLES["books"], "books", Args()) == 1
    assert exports == [] and "books" not in ao.load_pending()


def test_a_probe_that_fails_stops_the_dataset_rather_than_guessing(desk, monkeypatch):
    _, exports, logged = _wire(monkeypatch, probe_ok=False)

    assert ao.export_one(ao.TABLES["books"], "books", Args()) == 1
    assert exports == []
    assert logged[-1][0] == "attention"
