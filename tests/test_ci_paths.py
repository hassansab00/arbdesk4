"""The path filter on tests.yml skips only what no test reads (WXPredict A.2).

tools/ci_paths.py writes the filter from a measured run; tests/conftest.py
fails any run in which a test reads a path the filter skips. These tests hold
the rest: the block is the generator's own rendering, every tracked file that
is not a skippable document still runs the suite (so no code, SQL, data or
workflow change can skip it), and the node tests, which the Python recorder
cannot see, name no skipped document.
"""
import importlib.util
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("ci_paths", os.path.join(ROOT, "tools", "ci_paths.py"))
ci_paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_paths)


def _tracked():
    try:
        out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
        files = [l for l in out.splitlines() if l]
    except (OSError, subprocess.CalledProcessError):
        files = []
    if not files:          # no git: walk the tree instead
        for d, dirs, names in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in (".git", "node_modules", ".next")]
            files += [os.path.relpath(os.path.join(d, n), ROOT).replace(os.sep, "/") for n in names]
    return files


def test_the_filter_is_the_generators_rendering():
    """Only `--write` edits the block: the fixed head, then the read documents, sorted."""
    block = ci_paths.read_block()
    docs = ci_paths.included_docs(block)
    assert block == ci_paths.patterns(docs), (
        "the paths block in tests.yml is not what tools/ci_paths.py renders; "
        "run `python3 tools/ci_paths.py --write` rather than editing it")
    assert block[:4] == ["**", ".github/**", "!*.md", "!docs/**"]
    negations = [p for p in block if p.startswith("!")]
    assert negations == ["!*.md", "!docs/**"], (
        f"the filter may skip only documents; it also skips {negations}")


def test_every_document_put_back_exists():
    for d in ci_paths.included_docs():
        assert os.path.isfile(os.path.join(ROOT, d)), (
            f"tests.yml puts back {d}, which no longer exists; run tools/ci_paths.py --write")


def test_no_code_sql_data_or_workflow_change_skips_the_suite():
    """Every tracked file outside docs/ and the root's *.md runs the suite."""
    block = ci_paths.read_block()
    files = _tracked()
    assert len(files) > 500, f"expected the repository's files, saw {len(files)}"
    skipped = [f for f in files if not ci_paths.runs_for(f, block)]
    wrong = [f for f in skipped if not ci_paths.skippable(f)]
    assert not wrong, f"these would not run the suite and are not documents: {wrong[:20]}"
    for must in (".github/workflows/tests.yml", "tests/conftest.py", "tools/ci_paths.py",
                 "scripts/tick.py", "sql/INSTALL_ORDER.txt", "requirements.txt"):
        assert ci_paths.runs_for(must, block), must
    assert all(ci_paths.runs_for(f, block) for f in files if f.startswith("supabase/migrations/"))
    for d in ci_paths.included_docs(block):
        assert ci_paths.runs_for(d, block), d


@pytest.mark.parametrize("path,runs", [
    ("scripts/probability_engine.py", True),
    ("supabase/migrations/20990101000000_x.sql", True),
    ("data/training/x.csv.gz", True),
    (".github/workflows/web.yml", True),
    ("web/app/page.tsx", True),
    ("docs/PLAN_PROGRESS.md", False),
    ("docs/handoff-2026-09-30/00_START_HERE.md", False),
    ("CLAUDE.md", False),
    ("README.md", False),
    ("web/README.md", True),           # *.md is the root only
    ("docs/SD_CORR_PREREG.md", True),  # a test reads it
])
def test_the_matcher_reads_the_patterns_as_github_does(path, runs):
    """Last match wins; `*` stays inside one directory, `**` crosses them."""
    assert ci_paths.runs_for(path) is runs


def test_the_guard_catches_a_test_that_reads_a_skipped_document():
    """What tests/conftest.py fails the run on, case by case."""
    assert ci_paths.violations({"docs/PLAN_PROGRESS.md"}, set()) == ["docs/PLAN_PROGRESS.md"]
    assert ci_paths.violations({"CLAUDE.md"}, set()) == ["CLAUDE.md"]
    assert ci_paths.violations(set(), {"docs"}) == ["docs/ (listed)"]
    assert ci_paths.violations({"docs/SD_CORR_PREREG.md", "scripts/tick.py"}, set()) == []


def test_no_node_test_names_a_skipped_document():
    """The Python recorder cannot see a node test open a file, so a node test
    that names a document outside a comment must name one that runs the suite."""
    bad = []
    for d, dirs, names in os.walk(os.path.join(ROOT, "tests")):
        dirs[:] = [x for x in dirs if x != "node_modules"]
        for n in names:
            if not n.endswith((".cjs", ".js", ".mjs", ".ts")):
                continue
            path = os.path.join(d, n)
            for i, line in enumerate(open(path, encoding="utf-8"), 1):
                code = line.split("//", 1)[0].strip()
                if not code or code.startswith("*") or code.startswith("/*"):
                    continue
                for m in re.finditer(r"docs/[A-Za-z0-9_.\-/]+\.md", code):
                    if not ci_paths.runs_for(m.group(0)):
                        bad.append(f"{os.path.relpath(path, ROOT)}:{i} {m.group(0)}")
    assert not bad, f"node tests name documents the filter skips: {bad}"
