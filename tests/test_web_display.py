"""Run the web display-layer assertions (web/lib/units.ts, web/lib/time.ts).

Those two modules decide what every temperature and every timestamp in the UI
says, and their failure mode is silent: a Fahrenheit market printed in Celsius
is a plausible-looking number, and a delta converted with the +32 offset is a
plausible-looking number too. Nobody spots either by reading the page.

Executed through Node's type-stripping so the web app gains no test-runner
dependency. Skipped where node is absent or too old to strip types.
"""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests", "web", "check_display.mjs")
NODE = shutil.which("node")


def _strips_types() -> bool:
    if NODE is None:
        return False
    r = subprocess.run([NODE, "--experimental-strip-types", "-e", "0"],
                       capture_output=True, text=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(
    not _strips_types(), reason="node with --experimental-strip-types not available"
)


def test_display_layer():
    r = subprocess.run([NODE, "--experimental-strip-types", SCRIPT],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.strip().splitlines() if l.startswith("{")]
    assert lines, r.stdout + r.stderr
    result = json.loads(lines[-1])
    detail = "\n".join(l for l in r.stdout.splitlines() if "FAIL" in l)
    assert result["ok"], f"{result['failed']} display assertion(s) failed:\n{detail}"
    assert result["passed"] >= 15
