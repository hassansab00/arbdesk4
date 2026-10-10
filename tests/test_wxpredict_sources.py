"""R47 (10 Oct): the training table names its sources, so it can be rebuilt
from exactly them.

The recorded table (table_meta.json) could not be rebuilt from the repository:
it was written on #314's branch before the branch merged a newer forecast
record, and since then the nightly mirror extends that record and the cities
file follows public.cities. Every build now records the git blob of each file
it reads or lists (`sources`), and build_table.py --recorded takes exactly
those blobs from git, rebuilds, and checks the content's sha256 and that the
build read the same files. No network, and no full build here: the full
rebuild is run by hand (it takes minutes), and its result is in the PR.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import build_table as bt  # noqa: E402
from wxpredict import common  # noqa: E402

META = ROOT / "data" / "training" / "wxpredict" / "table_meta.json"


def test_a_blob_id_is_gits_own(tmp_path):
    f = tmp_path / "x.csv.gz"
    f.write_bytes(b"\x1f\x8b some bytes\n\x00 and more")
    want = subprocess.run(["git", "hash-object", str(f)], check=True, capture_output=True, text=True).stdout.strip()
    assert common.blob_sha(f.read_bytes()) == want


def test_reads_are_recorded_only_while_a_build_records(tmp_path, monkeypatch):
    import gzip
    f = tmp_path / "a.csv.gz"
    with gzip.open(f, "wt") as g:
        g.write("x\n1\n")
    listed = tmp_path / "listed.csv.gz"
    listed.write_bytes(b"listed")
    monkeypatch.setattr(common, "READ", None)
    assert list(common.read_csv(f)) == [{"x": "1"}]
    monkeypatch.setattr(common, "READ", {})
    assert list(common.read_csv(f)) == [{"x": "1"}], "the same rows when recording"
    common.note(listed)
    assert common.READ == {str(f): common.blob_sha(f.read_bytes()), str(listed): common.blob_sha(b"listed")}


def test_the_id_is_of_the_bytes_read_and_a_file_that_changes_stops_the_build(tmp_path, monkeypatch):
    """Codex on #369: the id comes from the bytes the build parsed, not from a
    later look at the file; a file seen twice with other bytes stops it."""
    import gzip
    f = tmp_path / "a.csv.gz"
    with gzip.open(f, "wt") as g:
        g.write("x\n1\n")
    first = f.read_bytes()
    monkeypatch.setattr(common, "READ", {})
    rows = common.read_csv(f)
    assert next(rows) == {"x": "1"}                  # read and recorded
    with gzip.open(f, "wt") as g:                    # replaced while the build runs
        g.write("x\n2\n")
    assert common.sources(common.READ) == {os.path.relpath(f, common.DATA_ROOT): common.blob_sha(first)}
    with pytest.raises(RuntimeError, match="changed while the build"):
        list(common.read_csv(f))


def test_every_source_path_follows_the_root_and_comes_back(tmp_path):
    try:
        common.use_root(str(tmp_path))
        for p in (common.MH, common.PR, common.WX, common.REPORTS, common.STATION_DAILY, common.CITIES_GLOB):
            assert p.startswith(str(tmp_path)), p
        assert common.DATA_ROOT == str(tmp_path)
    finally:
        common.use_root(common.ROOT)
    assert common.MH == os.path.join(common.ROOT, "data", "training", "market_history")
    assert common.DATA_ROOT == common.ROOT


def test_sources_are_named_by_path_under_the_root(tmp_path, monkeypatch):
    d = tmp_path / "data" / "training" / "previous_runs"
    d.mkdir(parents=True)
    (d / "b.csv.gz").write_bytes(b"bee")
    monkeypatch.setattr(common, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "READ", {})
    common.note(d / "b.csv.gz")
    assert common.sources(common.READ) == {"data/training/previous_runs/b.csv.gz": common.blob_sha(b"bee")}


def test_materialize_writes_each_blob_and_refuses_a_wrong_one(tmp_path):
    blobs = {common.blob_sha(b"one"): b"one", common.blob_sha(b"two"): b"two"}
    sources = {"data/mirror/cities/cities-2026-10-05.csv.gz": common.blob_sha(b"one"),
               "data/training/wxpredict/station_reports/EGLC.csv.gz": common.blob_sha(b"two")}
    bt.materialize(sources, str(tmp_path), cat=blobs.__getitem__)
    assert (tmp_path / "data" / "mirror" / "cities" / "cities-2026-10-05.csv.gz").read_bytes() == b"one"
    assert (tmp_path / "data" / "training" / "wxpredict" / "station_reports" / "EGLC.csv.gz").read_bytes() == b"two"
    with pytest.raises(ValueError):
        bt.materialize({"data/x.csv.gz": common.blob_sha(b"one")}, str(tmp_path), cat=lambda sha: b"not one")


def test_the_recorded_rebuild_needs_the_same_content_and_the_same_sources(tmp_path, monkeypatch):
    recorded = {"sha256": "abc", "rows": 3, "sources": {"data/a.csv.gz": common.blob_sha(b"a")}}
    path = tmp_path / "meta.json"
    path.write_text(json.dumps(recorded))
    seen = {}

    def fake_build(out_dir, write_meta):
        seen["root"], seen["write_meta"] = common.DATA_ROOT, write_meta
        assert (pathlib.Path(common.DATA_ROOT) / "data" / "a.csv.gz").read_bytes() == b"a"
        return dict(result)

    monkeypatch.setattr(bt, "build", fake_build)
    cat = {common.blob_sha(b"a"): b"a"}.__getitem__
    for result, want in (({"sha256": "abc", "rows": 3, "sources": recorded["sources"]}, True),
                         ({"sha256": "abd", "rows": 3, "sources": recorded["sources"]}, False),
                         ({"sha256": "abc", "rows": 3, "sources": {}}, False)):
        same, _ = bt.rebuild_recorded(str(path), cat=cat)
        assert same is want
        assert seen["write_meta"] is False and seen["root"] != common.ROOT
        assert common.DATA_ROOT == common.ROOT, "the paths come back to the repository"


def test_the_recorded_table_names_its_sources():
    meta = json.loads(META.read_text())
    src = meta["sources"]
    assert all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in src.values())
    dirs = ("data/training/market_history/", "data/training/previous_runs/", "data/training/wxpredict/",
            "data/mirror/cities/")
    assert all(p.startswith(dirs) for p in src), [p for p in src if not p.startswith(dirs)]
    for need in ("data/training/market_history/events.csv.gz", "data/training/market_history/bands.csv.gz",
                 "data/training/market_history/prices.csv.gz", "data/training/previous_runs/best_match_daily.csv.gz",
                 "data/training/previous_runs/models_daily.csv.gz",
                 "data/training/previous_runs/best_match_hourly_day1_utc.csv.gz",
                 "data/training/wxpredict/station_daily.csv.gz"):
        assert need in src, need
    assert len([p for p in src if p.startswith("data/mirror/cities/")]) == 1, "the one cities file it read"
    assert sum(p.startswith("data/training/wxpredict/station_reports/") for p in src) >= meta["cities"]
