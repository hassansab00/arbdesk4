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
    assert "git add data/archive web/public/archive/index.json" in WORKFLOW, (
        "archive_observations.yml prunes rows out of Postgres and does not commit the "
        "data and the index that say where they went - they die with the runner"
    )
    assert "git push origin HEAD:" in WORKFLOW

    # AND IT COMMITS BEFORE IT PRUNES. The question in this file's header -
    # "did you delete my data" - took a download to answer because the only
    # copy was in a Release. Now the delete is on the far side of a commit of
    # the data itself, so the answer is a glance at the tree.
    assert (WORKFLOW.index("--export-only")
            < WORKFLOW.index("Commit the data BEFORE anything is deleted")
            < WORKFLOW.index("--prune-only")), (
        "the workflow deletes rows before committing the files that hold them"
    )


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
    """The index must not claim a number no file backs.

    THIS USED TO BE FOUR HARD-CODED TOTALS, measured by hand on the day it was
    written, with a message telling the next person to update them. That made
    it a test of whether someone had remembered to edit a constant - and it
    went red the first time the archive ran daily, which is the one thing it
    should have been relaxed about.

    Now that the archive files live in data/archive it can do what its name
    says: decompress each one that is present and count. Files not yet pulled
    out of the old Releases are skipped rather than failed, so the migration
    can land incrementally.
    """
    idx = _index()
    root = ROOT / "data" / "archive"

    for name, ds in idx["datasets"].items():
        assert ds["rows_archived"] == sum(a.get("rows", 0) for a in ds["assets"]), (
            f"{name}: rows_archived disagrees with the assets it lists, so the "
            "total was written rather than counted"
        )

    checked = 0
    for name, ds in idx["datasets"].items():
        for asset in ds["assets"]:
            path = root / name / asset["asset"]
            if not path.exists():
                continue            # still only in the Release; --pull-releases brings it in
            import gzip as _gz
            import csv as _csv
            import io as _sio
            text = _gz.decompress(path.read_bytes()).decode()
            rows = max(0, sum(1 for _ in _csv.reader(_sio.StringIO(text))) - 1)
            assert rows == asset["rows"], (
                f"{name}/{asset['asset']} holds {rows:,} rows, the index claims "
                f"{asset['rows']:,}"
            )
            checked += 1
    print(f"verified {checked} archive file(s) against the index")


def test_the_prune_still_happens_only_after_a_verified_read_back():
    """The reason no data was lost, now with the repository in the middle.

    It used to be: upload to a Release, download it back, count, delete. The
    read-back is still there and still before the delete - but the thing read
    back is the file in data/archive, and between writing it and deleting
    anything the workflow commits and pushes it. So the delete is not merely
    after a verified copy exists; it is after that copy is in git.
    """
    prune = SCRIPT[SCRIPT.index("def prune_one("):SCRIPT.index("def is_committed(")]
    read_at = prune.index("verify_repo_archive(")
    commit_at = prune.index("is_committed(")
    delete_at = prune.index('"p_dry_run": False')
    assert read_at < delete_at, (
        "the committed prune now runs before the archive file is read back and counted"
    )
    assert commit_at < delete_at, (
        "the prune no longer confirms the file is in HEAD before deleting the rows"
    )
    assert "REFUSING TO PRUNE" in prune and "not committed" in prune

    export = SCRIPT[SCRIPT.index("def export_one("):SCRIPT.index("def prune_one(")]
    assert "VERIFY FAILED" in export and "Nothing will be pruned." in export
    assert '"p_dry_run": False' not in export, "the export phase can delete rows"


# --- and a page has to offer it ------------------------------------------
#
# /api/archive worked for days and NOTHING CALLED IT. Every page, component
# and lib was searched: the only references anywhere were the route file and
# comments about it. So 623,768 rows sat in four Releases, retrievable by
# anyone who knew the endpoint and invisible to everyone else - which is the
# same condition the archive exists to prevent.
#
# An endpoint with no caller is not a feature. These hold the other end.

WEB = ROOT / "web"


def _web_sources():
    for sub in ("app", "components", "lib"):
        for path in (WEB / sub).rglob("*.ts*"):
            if "node_modules" in str(path) or ".next" in str(path):
                continue
            yield path


def test_something_in_the_ui_actually_calls_the_archive():
    callers = [p.name for p in _web_sources()
               if "/api/archive" in p.read_text(encoding="utf-8")
               and "api/archive/route" not in str(p)]
    assert callers, (
        "no page or component fetches /api/archive. The route can serve 623,768 rows and "
        "nothing asks it to, which is an archive nobody can read."
    )


def test_the_browser_is_rendered_on_a_page():
    """A component nobody renders is the same as no component - the lesson
    from the calibration indicator earlier today."""
    rendered = [p.name for p in _web_sources()
                if "<ArchiveBrowser" in p.read_text(encoding="utf-8")]
    assert rendered, "ArchiveBrowser exists but no page renders it"


def test_it_distinguishes_unreachable_from_deleted():
    """The assets are on a private repo, so a deployment without a token can
    list ranges but not fetch rows. Showing an empty table there would read as
    'the data is gone', which is the exact misreading this all guards against."""
    src = (WEB / "components" / "ArchiveBrowser.tsx").read_text(encoding="utf-8")
    assert "can_fetch_rows" in src, (
        "the browser does not check whether this deployment can reach the assets"
    )
    assert "not lost" in src or "not deleted" in src, (
        "when rows cannot be fetched the page must say the data still exists"
    )
