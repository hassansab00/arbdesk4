"""The solver's inner loop was rewritten for speed on 27 Sep (plan v2 P5.12:
one engine decision cost 0.5-0.8 s, the tick has one billed minute). The
rewrite adds the same terms in the same order and sorts the same way, so every
output must be BIT-IDENTICAL to the code before it: the golden file holds that
code's outputs on 30 cases (robust and not, YES/NO, the lock, caps, the
city-day cap)."""
import json
import pathlib

import holdings_solver as hs

GOLDEN = json.loads((pathlib.Path(__file__).parent / "fixtures" / "solver_golden_27sep.json").read_text())


def test_the_rewrite_is_bit_identical_to_the_code_before_it():
    assert len(GOLDEN["cases"]) == 30
    for c in GOLDEN["cases"]:
        kw = dict(c["kwargs"], allow=tuple(c["kwargs"]["allow"]))
        got = json.loads(json.dumps(hs.solve_book(c["ladder"], iters=300, **kw), sort_keys=True))
        assert got == c["expected"], kw
