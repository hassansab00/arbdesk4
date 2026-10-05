"""Record the documents a child Python process opens (WXPredict build A.2).

tests/conftest.py puts this directory first on the PYTHONPATH its tests'
children inherit and names a file in AD4_CI_CHILD_READS. A child that opens or
lists a file under docs/ (or a root *.md) of AD4_CI_ROOT appends it there, and
the parent's run fails if one is a document tools/ci_paths.py lets a pull
request skip. Nothing happens without AD4_CI_CHILD_READS. Any sitecustomize
this one shadows still runs.
"""
import importlib.machinery
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = os.environ.get("AD4_CI_CHILD_READS")
_ROOT = os.environ.get("AD4_CI_ROOT")

if _OUT and _ROOT:
    import atexit
    _seen = set()

    def _rel(p):
        try:
            if isinstance(p, bytes):
                p = p.decode(errors="replace")
            a = os.path.realpath(os.path.join(os.getcwd(), os.fspath(p)))
        except (TypeError, ValueError):
            return None
        if not a.startswith(_ROOT + os.sep):
            return None
        r = os.path.relpath(a, _ROOT).replace(os.sep, "/")
        return r if (r == "docs" or r.startswith("docs/") or ("/" not in r and r.endswith(".md"))) else None

    def _audit(event, args):
        if event in ("open", "os.listdir", "os.scandir") and args and not isinstance(args[0], int):
            r = _rel(args[0] if args[0] is not None else ".")
            if r:
                _seen.add(("list\t" if event != "open" else "read\t") + r)
        elif event == "glob.glob" and isinstance(args[0], (str, bytes, os.PathLike)):
            r = _rel(os.path.dirname(os.fspath(args[0])) or ".")
            if r:
                _seen.add("list\t" + r)

    sys.addaudithook(_audit)

    @atexit.register
    def _flush():
        if _seen:
            with open(_OUT, "a", encoding="utf-8") as f:
                f.write("".join(f"{s}\n" for s in sorted(_seen)))

# Chain to the sitecustomize this one shadows, if there is one.
_spec = importlib.machinery.PathFinder.find_spec(
    "sitecustomize", [p for p in sys.path if os.path.abspath(p or ".") != _HERE])
if _spec is not None and _spec.origin and os.path.dirname(os.path.abspath(_spec.origin)) != _HERE:
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
