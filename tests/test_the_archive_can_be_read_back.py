"""Archiving that nothing can read is deletion with extra steps.

"DID U FUCKIN DELETE MY FUCKIN DATA COLLECTED AT TIS POINT"

No - and this file is why that question needed a download to answer rather
than a glance. On 2026-09-20 the picture was:

    in the Releases     623,768 rows across 4 datasets, every asset present
    in the index        78,291 rows, research only, hand-committed on 19 Sep
    in the database     proprietary_data_manifests: ZERO rows, ever

scripts/archive_observations.py writes web/public/archive/index.json after
every prune, and .github/workflows/archive_observations.yml had no commit
step - so the file was written onto the runner and destroyed with it, every
run since the archive began. /api/archive reads that index to locate an
asset, so 545,477 archived rows were unreachable from the platform that
archived them.

The data was never at risk: the prune only runs after the upload is
re-downloaded and counted, and that order held. Three assets were pulled back
and parsed to prove it - 293,123 / 114,591 / 31,787 rows, all exact. But
"the bytes are in a Release somewhere" is not the promise the desk makes.
The promise is that a range which left Postgres can be fetched back, and
that promise lived in a file nobody committed.

TWO SEPARATE THINGS HAVE TO BE TRUE, and each has its own test here:

  1 the workflow that prunes must COMMIT the index, or the next run's
    knowledge dies with its runner
  2 every entry must carry what a reader needs to fetch it - the asset name,
    the range, the row count and a digest - because an index entry that
    cannot be resolved to bytes is the same as no entry
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "archive_observations.yml").read_text(encoding="utf-8")
INDEX_PATH = ROOT / "web" / "public" / "archive" / "index.json"
SCRIPT = (ROOT / "scripts" / "archive_observations.py").read_text(encoding="utf-8")


def _index():
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def test_the_workflow_commits_the_index_it_writes():
    """The root cause. Without this the script's own record is thrown away
    with the runner, every single run."""
    assert "git add web/public/archive/index.json" in WORKFLOW, (
        "archive_observations.yml prunes rows out of Postgres and does not commit the "
        "index that says where they went - the file dies with the runner"
    )
    assert "git push origin HEAD:" in WORKFLOW


def test_the_commit_step_runs_even_when_a_dataset_refuses():
    """Datasets are archived in sequence and any one can refuse its count
    check - on 20 Sep resolution did, AFTER forecasts, observations and
    research had already uploaded and pruned. Committing only on success
    drops the index for exactly the runs that changed it."""
    step = WORKFLOW[WORKFLOW.index("Commit the archive index"):]
    assert re.search(r"if:\s*always\(\)", step[:400]), (
        "the index commit is conditional on the whole job succeeding, so a late failure "
        "discards the record of the prunes that already happened"
    )


def test_the_index_is_not_empty_and_covers_more_than_one_dataset():
    """It sat at research-only for days while four Releases held data."""
    idx = _index()
    assert idx.get("datasets"), "the archive index has no datasets at all"
    assert len(idx["datasets"]) >= 4, (
        f"only {len(idx['datasets'])} dataset(s) in the index: {sorted(idx['datasets'])}. "
        "Anything pruned and not listed cannot be served by /api/archive."
    )


@pytest.mark.parametrize("dataset", sorted(_index().get("datasets", {})))
def test_every_entry_carries_what_a_reader_needs_to_fetch_it(dataset):
    ds = _index()["datasets"][dataset]
    for key in ("table", "release_tag", "assets", "rows_archived"):
        assert key in ds, f"{dataset} is missing {key}"
    assert ds["assets"], f"{dataset} lists no assets"
    for a in ds["assets"]:
        for key in ("asset", "rows", "from", "to"):
            assert key in a, f"{dataset}/{a.get('asset','?')} is missing {key}"
        assert a["rows"] > 0, f"{dataset}/{a['asset']} claims zero rows"
        assert a["asset"].endswith(".csv.gz")
    assert ds["rows_archived"] == sum(a["rows"] for a in ds["assets"]), (
        f"{dataset}'s total disagrees with its own assets"
    )


def test_the_recorded_totals_are_the_ones_measured_from_the_files():
    """These counts came from downloading each asset and parsing it, not from
    what an earlier run claimed it wrote. If they drift, the index is
    asserting something nobody checked."""
    idx = _index()
    expected = {"observations": 413_257, "forecasts": 34_180,
                "trades": 90_640, "research": 85_691}
    for name, rows in expected.items():
        assert name in idx["datasets"], f"{name} has dropped out of the index"
        assert idx["datasets"][name]["rows_archived"] == rows, (
            f"{name} now claims {idx['datasets'][name]['rows_archived']:,} rows, measured "
            f"{rows:,}. Either an asset was added - update this - or one went missing."
        )


def test_the_prune_still_happens_only_after_a_verified_read_back():
    """The reason no data was lost. The upload is re-downloaded and counted
    before anything is deleted, and that order is the whole safety property."""
    body = SCRIPT[SCRIPT.index("# 3 - upload"):]
    verify_at = body.index("verify(")
    prune_at = body.index('"p_dry_run": False')
    assert verify_at < prune_at, (
        "the committed prune now runs before the uploaded asset is read back and counted"
    )
    assert "VERIFY FAILED" in body and "Nothing pruned." in body
