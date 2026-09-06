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
    assert result["passed"] >= 40, "assertions went missing from the harness"


def test_ts_cost_model_agrees_with_the_python_one():
    """web/lib/costs.ts is a PORT of scripts/cost_model.py.

    A port is worth nothing unless it agrees with the original, and this pair
    is what tells a trader what a trade returns. Before the port existed the
    Board used a flat 2% of notional, which at 34c overstated the profit on a
    $100 stake by about $3.50 and had the wrong shape everywhere: the real fee
    peaks at 1.25% near 50c and falls to nothing at both extremes.

    Both are run on the same inputs and compared exactly.
    """
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from cost_model import taker_fee

    cases = [(100, 0.34), (100, 0.5), (250, 0.9), (50, 0.05), (500, 0.72), (1000, 0.5)]
    src = (
        'import * as C from "%s";\n'
        "const cases = %s;\n"
        "console.log(JSON.stringify(cases.map(([usd, p]) => {\n"
        "  const pos = C.buy(usd, p);\n"
        "  return { usd, p, shares: pos.shares, fee: pos.fee, profit: pos.profit };\n"
        "})));\n"
    ) % (os.path.join(ROOT, "web", "lib", "costs.ts"), json.dumps(cases))

    script = os.path.join(ROOT, "tests", "web", ".xcheck.generated.mjs")
    try:
        with open(script, "w") as fh:
            fh.write(src)
        r = subprocess.run([NODE, "--experimental-strip-types", script],
                           capture_output=True, text=True, timeout=60, cwd=ROOT)
        assert r.returncode == 0, r.stderr
        rows = json.loads([l for l in r.stdout.splitlines() if l.startswith("[")][-1])
    finally:
        if os.path.exists(script):
            os.remove(script)

    assert len(rows) == len(cases)
    for row in rows:
        expected = taker_fee(row["shares"], row["p"])
        assert abs(expected - row["fee"]) < 1e-9, (
            f'fee mismatch at stake {row["usd"]} price {row["p"]}: '
            f'ts {row["fee"]!r} vs py {expected!r}'
        )
        # The stake is what leaves the account: shares x price + fee == stake.
        spent = row["shares"] * row["p"] + row["fee"]
        assert abs(spent - row["usd"]) < 1e-6, (
            f'stake does not reconstitute at price {row["p"]}: {spent} != {row["usd"]}'
        )



def test_execution_layer():
    """web/lib/execution.ts - the size you can actually place.

    Every position figure on this desk used to be computed as if any quantity
    could be bought at the quoted price. Both halves were false: Polymarket
    refuses an order under its minimum notional, and a book has a depth, so
    the second hundred dollars fills worse than the first and past the end of
    the ladder it does not fill at all.

    These assertions are the specific wrong answers that were on screen: a
    sub-minimum leg quoted as a small trade, and a $500 ticket quoted at the
    profit of $500 of shares against a $25 book.
    """
    script = os.path.join(ROOT, "tests", "web", "check_execution.mjs")
    r = subprocess.run([NODE, "--experimental-strip-types", script],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.strip().splitlines() if l.startswith("{")]
    assert lines, r.stdout + r.stderr
    result = json.loads(lines[-1])
    detail = "\n".join(l for l in r.stdout.splitlines() if "FAIL" in l)
    assert result["ok"], f"{result['failed']} execution assertion(s) failed:\n{detail}"
    assert result["passed"] >= 13, "assertions went missing from the harness"
