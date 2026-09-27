"""Plan v2 P8.2: a strategy is a view plus constraints, with no numbers of its
own. Every numeric literal in a strategy file other than 0 and 1 fails here;
what a strategy needs to decide is a parameter in strategy_params (prior,
bounds, version) or a rail in risk_rails, imported by name.

The old signal-path files (s1-s9, their base class and conflict rules) are
LEGACY until they move to scripts/strategies/legacy/ when their ids retire
(P8.2 step 5, once the consolidated strategies decide live through the engine,
P5.12 part 3). The list may only shrink: a legacy file that is gone must leave
it, and a new file is checked.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "scripts" / "strategies"
LEGACY = {
    "__init__.py", "base.py", "conflicts.py",
    "s1_buy_low_sell_signal.py", "s2_combination_arb.py", "s3_concentration.py", "s4_tail_fade.py",
    "s5_running_max_lock.py", "s6_anchor_insurance.py", "s7_pre_peak_gradient.py",
    "s8_two_bucket_cover.py", "s9_ladder_basket.py",
}


def _numbers(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool) and node.value not in (0, 1):
            out.append((node.lineno, node.value))
    return out


def test_the_legacy_list_only_shrinks():
    missing = sorted(f for f in LEGACY if not (STRATEGIES / f).exists())
    assert not missing, f"retired or moved - take them off LEGACY: {missing}"


def test_no_strategy_file_holds_a_number_of_its_own():
    checked, bad = [], {}
    for path in sorted(STRATEGIES.glob("*.py")):
        if path.name in LEGACY:
            continue
        checked.append(path.name)
        found = _numbers(path)
        if found:
            bad[path.name] = found
    assert checked, "nothing was checked"
    assert not bad, f"numbers belong in strategy_params or risk_rails: {bad}"


def test_the_consolidated_strategies_are_among_the_checked():
    for name in ("s10_max_temp_winner.py", "s11_ladder_optimiser.py", "s12_overpriced_no.py",
                 "s2_structural_arb.py", "engine_views.py", "tradeable.py"):
        assert name not in LEGACY and (STRATEGIES / name).exists(), name
