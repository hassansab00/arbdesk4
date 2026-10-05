"""A pull request that changes only documents no test reads skips the suites (WXPredict A.2).

tools/ci_paths.py writes .github/ci-paths.txt from a measured run, and
tests.yml's first step decides from the pull request's own diff; the job always
runs, so a required `pytest` check is never left pending. tests/conftest.py
fails any run in which a test, or a process it starts, reads a path the list
skips. These tests hold the rest: the list is the generator's own rendering,
every tracked file that is not a skippable document still runs the suites (so
no code, SQL, data or workflow change can skip them), every step after the
decision is bound to it, any doubt runs everything, and the node tests name no
skipped document.
"""
import importlib.util
import os
import re
import subprocess

import pytest
import yaml

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


def test_the_list_is_the_generators_rendering():
    """Only `--write` edits it: the fixed head, then the read documents, sorted."""
    text = open(ci_paths.PATTERNS, encoding="utf-8").read()
    docs = ci_paths.included_docs(ci_paths.read_block(text))
    assert text == ci_paths.render(docs), (
        ".github/ci-paths.txt is not what tools/ci_paths.py renders; "
        "run `python3 tools/ci_paths.py --write` rather than editing it")
    block = ci_paths.read_block(text)
    assert block[:3] == ["**", "!*.md", "!docs/**"]
    assert [p for p in block if p.startswith("!")] == ["!*.md", "!docs/**"], (
        "the list may skip only documents")


def test_every_document_put_back_exists():
    for d in ci_paths.included_docs():
        assert os.path.isfile(os.path.join(ROOT, d)), (
            f".github/ci-paths.txt puts back {d}, which no longer exists; run tools/ci_paths.py --write")


def test_no_code_sql_data_or_workflow_change_skips_the_suites():
    """Every tracked file outside docs/ and the root's *.md runs the suites."""
    block = ci_paths.read_block()
    files = _tracked()
    assert len(files) > 500, f"expected the repository's files, saw {len(files)}"
    wrong = [f for f in files if not ci_paths.runs_for(f, block) and not ci_paths.skippable(f)]
    assert not wrong, f"these would not run the suites and are not documents: {wrong[:20]}"
    for must in (".github/workflows/tests.yml", ".github/ci-paths.txt", "tests/conftest.py",
                 "tools/ci_paths.py", "tools/ci_child_hook/sitecustomize.py", "scripts/tick.py",
                 "sql/INSTALL_ORDER.txt", "requirements.txt", ".gitignore"):
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


def test_only_a_clean_docs_only_pull_request_skips():
    assert ci_paths.decide("pull_request", ["docs/PLAN_PROGRESS.md", "CLAUDE.md"])[0] is False
    assert ci_paths.decide("pull_request", ["docs/PLAN_PROGRESS.md", "scripts/tick.py"])[0] is True
    assert ci_paths.decide("pull_request", ["docs/SD_CORR_PREREG.md"])[0] is True
    # Any doubt runs everything.
    assert ci_paths.decide("pull_request", None)[0] is True, "an unreadable diff skipped the suites"
    assert ci_paths.decide("pull_request", [])[0] is True
    assert ci_paths.decide("workflow_dispatch", ["docs/PLAN_PROGRESS.md"])[0] is True
    assert ci_paths.decide("", ["docs/PLAN_PROGRESS.md"])[0] is True


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def test_the_diff_is_the_merge_against_its_base(tmp_path):
    """What GitHub checks out for a pull request: a merge of the head into the
    base. The diff against the first parent is the pull request's change."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    # (Not under docs/: the command-line check would rightly flag a git
    # command naming a document, even in a scratch repository.)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "NOTE.txt").write_text("n")
    _git(tmp_path, "add", "notes/NOTE.txt")
    _git(tmp_path, "commit", "-qm", "doc")
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "b.py").write_text("y")       # the base moved on meanwhile
    _git(tmp_path, "add", "b.py")
    _git(tmp_path, "commit", "-qm", "base moves")
    _git(tmp_path, "merge", "-q", "--no-ff", "--no-edit", "pr")
    assert ci_paths.pr_diff(str(tmp_path)) == ["notes/NOTE.txt"]
    # A commit with one parent is not a pull request's merge: no answer, so
    # everything runs.
    _git(tmp_path, "checkout", "-q", "HEAD^1")
    assert ci_paths.pr_diff(str(tmp_path)) is None
    assert ci_paths.pr_diff(str(tmp_path / "not-a-repo")) is None


def test_every_step_after_the_decision_is_bound_to_it():
    """Nothing but the checkout and the decision itself runs when it says skip."""
    doc = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "tests.yml")))
    on = doc.get("on", doc.get(True))
    assert "paths" not in (on.get("pull_request") or {}) and "paths-ignore" not in (on.get("pull_request") or {}), (
        "a workflow-level path filter leaves a required check pending on a docs-only pull request")
    steps = doc["jobs"]["pytest"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@") and steps[0]["with"]["fetch-depth"] == 2
    assert steps[1]["id"] == "scope" and "tools/ci_paths.py --decide" in steps[1]["run"]
    assert "if" not in steps[1]
    for st in steps[2:]:
        assert st.get("if") == "steps.scope.outputs.run == 'true'", st


def test_the_guard_catches_a_test_that_reads_a_skipped_document():
    """What tests/conftest.py fails the run on, case by case."""
    assert ci_paths.violations({"docs/PLAN_PROGRESS.md"}, set()) == ["docs/PLAN_PROGRESS.md"]
    assert ci_paths.violations({"CLAUDE.md"}, set()) == ["CLAUDE.md"]
    assert ci_paths.violations(set(), {"docs"}) == ["docs/ (listed)"]
    assert ci_paths.violations({"docs/SD_CORR_PREREG.md", "scripts/tick.py"}, set()) == []
    named = ci_paths.violations(set(), set(), ["bash -c cat docs/PLAN_PROGRESS.md", "git add -A",
                                               "node x.cjs docs/SD_CORR_PREREG.md",
                                               "cat web/README.md tests/fixtures/a.md"])
    assert len(named) == 1 and named[0].startswith("docs/PLAN_PROGRESS.md (named by a child process")
    assert ci_paths.violations(set(), set(), ["cat CLAUDE.md"])[0].startswith("CLAUDE.md (named")


def test_a_child_process_reading_a_document_is_recorded(tmp_path):
    """The hooks the tests' children inherit (tools/ci_child_hook) see what a
    Python or Node child reads, through the environment conftest set."""
    out = os.environ.get("AD4_CI_CHILD_READS")
    assert out and os.environ.get("AD4_CI_ROOT") == os.path.realpath(ROOT)
    probe = tmp_path / "reads.txt"
    env = dict(os.environ, AD4_CI_CHILD_READS=str(probe))
    # The code goes in files, so the command lines name no document (the
    # command-line check would rightly flag them).
    (tmp_path / "child.py").write_text("open('docs/PLAN_PROGRESS.md').read()\n")
    (tmp_path / "child.cjs").write_text("require('fs').readFileSync('docs/OPEN_ITEMS.md');\n")
    subprocess.run(["python3", str(tmp_path / "child.py")], cwd=ROOT, env=env, check=True)
    subprocess.run(["node", str(tmp_path / "child.cjs")], cwd=ROOT, env=env, check=True)
    assert sorted(probe.read_text().splitlines()) == ["read\tdocs/OPEN_ITEMS.md", "read\tdocs/PLAN_PROGRESS.md"]


def test_no_node_test_names_a_skipped_document():
    """A node test that names a document outside a comment must name one that
    runs the suites."""
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
    assert not bad, f"node tests name documents the list skips: {bad}"
