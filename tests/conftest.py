import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


@pytest.fixture(autouse=True)
def _weather_history_forgets():
    """weather_history keeps the newest prune and the archive's rows for the
    life of a process. A test process is many jobs' worth of fakes, so each
    test starts with nothing looked up."""
    import weather_history
    weather_history.reset()
    yield
    weather_history.reset()


# WHAT A PULL REQUEST MAY CHANGE WITHOUT THIS SUITE (WXPredict build, A.2).
#
# tests.yml skips the suites for a pull request that changes only documents no
# test reads (tools/ci_paths.py, .github/ci-paths.txt). That is safe only while
# it stays true, so every run records the documents its tests open or list,
# and the run FAILS if one of them is a path the list skips: a test that starts
# reading docs/X.md must take X.md off the list (`python3 tools/ci_paths.py
# --write`) in the same pull request. Collection counts too: a module that
# reads a document when it is imported is a test reading it.
#
# THE PROCESSES THE TESTS START COUNT TOO (Codex on #315). Python children
# inherit a sitecustomize and Node children a --require hook
# (tools/ci_child_hook/) that append what they read to one file, merged here;
# any other child's command line is checked for a skipped document's name. A
# child started with an environment of its own escapes the hooks, not the
# command-line check.
#
# With AD4_CI_PATHS_RECORD set, the run records and does not fail: that is how
# --write measures the list.
_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_doc_reads, _doc_lists, _child_cmds = set(), set(), []

import tempfile as _tempfile

_HOOK = os.path.join(_ROOT, "tools", "ci_child_hook")
_fd, _CHILD_READS = _tempfile.mkstemp(prefix="ad4-ci-child-reads-")
os.close(_fd)
os.environ["AD4_CI_CHILD_READS"] = _CHILD_READS
os.environ["AD4_CI_ROOT"] = _ROOT
os.environ["PYTHONPATH"] = os.pathsep.join(
    [_HOOK] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])
os.environ["NODE_OPTIONS"] = (os.environ.get("NODE_OPTIONS", "") + " --require "
                              + os.path.join(_HOOK, "node_hook.cjs")).strip()


def _rel(p):
    if isinstance(p, int):
        return None
    if isinstance(p, bytes):
        p = p.decode(errors="replace")
    try:
        a = os.path.realpath(os.path.join(os.getcwd(), os.fspath(p)))
    except (TypeError, ValueError):
        return None
    if not a.startswith(_ROOT + os.sep):
        return None
    return os.path.relpath(a, _ROOT).replace(os.sep, "/")


def _maybe_doc(p):
    """Cheap first look, so the hook does not resolve every file opened."""
    if isinstance(p, bytes):
        p = p.decode(errors="replace")
    return isinstance(p, (str, os.PathLike)) and ("docs" in os.fspath(p) or os.fspath(p).endswith(".md"))


def _audit(event, args):
    if event == "open":
        if not _maybe_doc(args[0]):
            return
        r = _rel(args[0])
        if r is not None and (r.startswith("docs/") or ("/" not in r and r.endswith(".md"))):
            _doc_reads.add(r)
    elif event in ("os.listdir", "os.scandir"):
        r = _rel(args[0] if args and args[0] is not None else ".")
        if r is not None and (r == "docs" or r.startswith("docs/")):
            _doc_lists.add(r)
    elif event == "glob.glob":
        pat = args[0]
        if isinstance(pat, (str, bytes, os.PathLike)):
            r = _rel(os.path.dirname(os.fspath(pat)) or ".")
            if r is not None and (r == "docs" or r.startswith("docs/")):
                _doc_lists.add(r)
    elif event == "subprocess.Popen":
        argv = args[1]
        if isinstance(argv, (str, bytes)):
            argv = [argv]
        try:
            _child_cmds.append(" ".join(os.fsdecode(a) for a in argv))
        except TypeError:
            pass
    elif event == "os.system":
        _child_cmds.append(os.fsdecode(args[0]))


sys.addaudithook(_audit)


def _merge_child_reads():
    try:
        lines = open(_CHILD_READS, encoding="utf-8").read().splitlines()
        os.remove(_CHILD_READS)
    except OSError:
        return
    for line in lines:
        kind, _, rel = line.partition("\t")
        if rel:
            (_doc_reads if kind == "read" else _doc_lists).add(rel)


def pytest_sessionfinish(session, exitstatus):
    _merge_child_reads()
    record = os.environ.get("AD4_CI_PATHS_RECORD")
    if record:
        with open(record, "w", encoding="utf-8") as f:
            f.write("".join(f"{p}\n" for p in sorted(_doc_reads)))
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("ci_paths", os.path.join(_ROOT, "tools", "ci_paths.py"))
    ci_paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci_paths)
    bad = ci_paths.violations(_doc_reads, _doc_lists, _child_cmds)
    if bad:
        sys.stderr.write(
            "\nCI PATH FILTER: these tests read paths that a pull request may change without "
            "running them:\n  " + "\n  ".join(bad) +
            "\nRun `python3 tools/ci_paths.py --write` and commit .github/ci-paths.txt with the change.\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
