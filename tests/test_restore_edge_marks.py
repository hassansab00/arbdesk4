"""The marks the edges prune took come back from data/archive/edges (plan v2
P1.6 phase 3, step 3.1b, 29 Sep).

tests/database/edge-marks.cjs proves restore_edge_marks over the real
migration: a pre-3.1 prune's lost marks return exactly, once, never over a
mark edges still holds. This holds the tool that feeds it and the workflow
that runs it: every archived YES edge of a band goes in ONE call (its mark is
the newest of all of them by the cutoff), a run writes only when asked, and
checks what it wrote against the files before it calls the job done.
"""
import csv
import gzip
import os
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import restore_edge_marks as rm  # noqa: E402

HEADER = ["band_id", "computed_at", "side", "model_prob", "market_price", "quoted_price", "edge_pp",
          "edge_net_pp", "edge_per_dollar", "fillable_usd_2c", "fillable_usd_5c", "fillable_usd_10c",
          "est_fee", "est_slippage", "book_snapshot_id", "prob_id", "confidence", "regime_label",
          "tradeable", "block_reason"]


def _write(folder, name, rows):
    with gzip.open(os.path.join(folder, name), "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for band, at, side, price in rows:
            w.writerow([band, at, side, "", price] + [""] * (len(HEADER) - 5))


@pytest.fixture
def archive(tmp_path):
    _write(tmp_path, "edges-2026-09-12-to-2026-09-16.csv.gz", [
        ("a", "2026-09-13T21:00:00+00:00", "YES", "0.25"),
        ("a", "2026-09-13T21:00:00+00:00", "NO", ""),
        ("b", "2026-09-13T18:00:00+00:00", "YES", ""),
    ])
    _write(tmp_path, "edges-2026-09-16-to-2026-09-18.csv.gz", [
        ("a", "2026-09-16T03:00:00+00:00", "YES", "0.5"),
    ])
    return rm.read_archive(str(tmp_path))


def test_only_yes_rows_every_file_and_an_empty_price_is_null(archive):
    assert archive == {
        "a": [("2026-09-13T21:00:00+00:00", "0.25", "edges-2026-09-12-to-2026-09-16.csv.gz"),
              ("2026-09-16T03:00:00+00:00", "0.5", "edges-2026-09-16-to-2026-09-18.csv.gz")],
        "b": [("2026-09-13T18:00:00+00:00", None, "edges-2026-09-12-to-2026-09-16.csv.gz")],
    }


def test_a_band_is_never_split_across_two_calls():
    bands = {f"b{i}": [(f"2026-09-13T0{j}:00:00+00:00", "0.1", "f") for j in range(3)] for i in range(5)}
    parts = rm.batches(bands, limit=7)
    assert [len(p) for p in parts] == [6, 6, 3]
    seen = {}
    for n, part in enumerate(parts):
        for r in part:
            assert seen.setdefault(r["band_id"], n) == n, "a band's rows went to two calls"
    assert all(r["source"] == "archive:f" for p in parts for r in p)
    # a band larger than the limit goes alone, whole
    assert rm.batches({"big": [(f"t{i}", None, "f") for i in range(9)]}, limit=4) == [
        [{"band_id": "big", "computed_at": f"t{i}", "market_price": None, "source": "archive:f"} for i in range(9)]]


def test_the_mark_is_each_readers_own_cutoff():
    rows = [("2026-09-13T21:00:00+00:00", "0.1", "f"), ("2026-09-13T22:00:00+00:00", "0.2", "f"),
            ("2026-09-14T04:00:00+00:00", "0.3", "f")]
    # eve: <= 18:00 local (22:00Z in New York); day: < local midnight (04:00Z)
    assert rm.expected_mark(rows, "2026-09-13T22:00:00+00:00", "eve")[1] == "0.2"
    assert rm.expected_mark(rows, "2026-09-14T04:00:00+00:00", "day")[1] == "0.2"
    assert rm.expected_mark(rows, "2026-09-13T20:00:00Z", "eve") is None


class _Db:
    """restore_edge_marks and derived_edge_marks as the tool sees them."""

    def __init__(self, to_write, restored, again=0):
        self.to_write, self.restored, self.again, self.calls = to_write, restored, again, []

    def rpc(self, fn, params=None, timeout=120):
        self.calls.append((fn, params.get("p_dry_run") if params else None))
        if fn == "refresh_page_cache":
            return {"mv_city_hit_history": 1}
        written_before = any(f == fn and d is False for f, d in self.calls[:-1])
        if params["p_dry_run"]:
            return {"to_write": self.again if written_before else self.to_write, "rows": len(params["p_rows"])}
        return {"written": self.to_write, "rows": len(params["p_rows"])}

    def rest_all(self, path, params=None, *, order, page_size=500):
        assert path == "derived_edge_marks" and params["source"] == "like.archive:*"
        return self.restored


BANDS = {"a": [("2026-09-13T21:00:00+00:00", "0.25", "f1.csv.gz"), ("2026-09-16T03:00:00+00:00", "0.5", "f2.csv.gz")]}
GOOD = [{"band_id": "a", "mark": "eve", "cutoff_at": "2026-09-13T22:00:00+00:00",
         "computed_at": "2026-09-13T21:00:00+00:00", "market_price": 0.25, "source": "archive:f1.csv.gz"}]


def test_without_commit_nothing_is_written():
    db = _Db(1, GOOD)
    rm.run(BANDS, False, db.rpc, db.rest_all)
    assert [c for c in db.calls if c[1] is False] == []


def test_a_commit_is_checked_against_the_files_then_the_page_cache_refreshes():
    db = _Db(1, GOOD)
    assert rm.run(BANDS, True, db.rpc, db.rest_all)["written"] == 1
    assert [c[0] for c in db.calls] == ["restore_edge_marks", "restore_edge_marks", "refresh_page_cache",
                                        "restore_edge_marks"]


@pytest.mark.parametrize("bad", [
    {"computed_at": "2026-09-16T03:00:00+00:00"},       # not the newest by the cutoff
    {"market_price": 0.5},                               # another price
    {"source": "archive:f2.csv.gz"},                     # another file
])
def test_a_restored_mark_that_disagrees_with_the_files_fails_the_run(bad):
    db = _Db(1, [{**GOOD[0], **bad}])
    with pytest.raises(SystemExit):
        rm.run(BANDS, True, db.rpc, db.rest_all)


def test_a_count_that_does_not_match_the_dry_run_fails_the_run():
    db = _Db(1, GOOD)
    db.rpc_orig = db.rpc

    def rpc(fn, params=None, timeout=120):
        r = db.rpc_orig(fn, params, timeout)
        if fn == "restore_edge_marks" and params["p_dry_run"] is False:
            r["written"] = 0
        return r
    with pytest.raises(SystemExit):
        rm.run(BANDS, True, rpc, db.rest_all)


def test_a_second_pass_that_would_still_write_fails_the_run():
    db = _Db(1, GOOD, again=1)
    with pytest.raises(SystemExit):
        rm.run(BANDS, True, db.rpc, db.rest_all)


def test_the_workflow_is_manual_main_only_bounded_and_a_dry_run_by_default():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "restore_edge_marks.yml").read_text())
    on = doc.get("on", doc.get(True))
    assert list(on) == ["workflow_dispatch"], "no schedule, no push trigger: it is run once, by hand"
    mode = on["workflow_dispatch"]["inputs"]["mode"]
    assert mode["default"] == "dry-run" and mode["options"] == ["dry-run", "commit"]
    job = doc["jobs"]["restore"]
    assert job["if"] == "github.ref_name == 'main'"
    assert job["timeout-minutes"] <= 10
    run = job["steps"][-1]["run"]
    assert 'if [ "${{ github.event.inputs.mode }}" = "commit" ]' in run
    assert run.count("--commit") == 1
    assert doc["permissions"] == {"contents": "read"}, "it writes to the database, never to the repository"


def test_the_migration_is_the_files_own_text():
    sql = (ROOT / "sql" / "ad4_97_evidence_cache.sql").read_text()
    fn = sql[sql.index("create or replace function public.restore_edge_marks"):
             sql.index("grant execute on function public.restore_edge_marks(jsonb, boolean) to service_role;")]
    mig = (ROOT / "supabase" / "migrations" / "20260929230000_the_marks_the_prune_took_come_back.sql").read_text()
    assert fn in mig
