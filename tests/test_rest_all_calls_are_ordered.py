"""common.rest_all requires a stable `order` (keyword-only) and raises
TypeError at the call when it is missing - at run time, not at import.

On 27 Sep the nightly calibration step failed on exactly that: a
`rest_all("settings", ...)` added on 26 Sep without `order`
(calibration.py). The suite passed because nothing exercised that line. This
reads every call in scripts/ and tools/ instead of waiting for one to run."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _calls_without_order():
    bad = []
    for d in ("scripts", "tools"):
        for p in sorted((ROOT / d).rglob("*.py")):
            tree = ast.parse(p.read_text(), filename=str(p))
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name != "rest_all":
                    continue
                if any(k.arg == "order" or k.arg is None for k in n.keywords):
                    continue
                bad.append(f"{p.relative_to(ROOT)}:{n.lineno}")
    return bad


def test_every_rest_all_call_names_its_order():
    assert _calls_without_order() == []
