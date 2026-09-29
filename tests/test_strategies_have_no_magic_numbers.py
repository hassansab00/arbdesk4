"""Plan v2 P8.2: a strategy is a view plus constraints, with no numbers of its
own. Every numeric literal in a strategy file other than 0 and 1 fails here;
what a strategy needs to decide is a parameter in strategy_params (prior,
bounds, version) or a rail in risk_rails, imported by name.

The old signal-path files are LEGACY. s1 and s3-s9 retired on 29 Sep (P8.2
step 5, Hassan) and moved to scripts/strategies/legacy/, which this scan does
not enter; what is left here is s2 and the base class and conflict rules they
share. The list may only shrink: a legacy file that is gone must leave it,
and a new file is checked.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "scripts" / "strategies"
LEGACY = {"__init__.py", "base.py", "conflicts.py", "s2_combination_arb.py"}


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


def test_the_retired_strategies_are_in_legacy_and_not_registered():
    """P8.2 step 5: the retired ids' code is kept for replay comparisons, in
    legacy/, and the live registry does not carry them."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from strategies import REGISTRY
    from strategies.legacy import LEGACY_REGISTRY
    retired = {"s1_buy_low_sell_signal", "s3_concentration", "s4_tail_fade", "s5_running_max_lock",
               "s6_anchor_insurance", "s7_pre_peak_gradient", "s8_two_bucket_cover", "s9_ladder_basket"}
    assert set(LEGACY_REGISTRY) == retired
    assert not retired & set(REGISTRY)
    for sid in retired:
        assert (STRATEGIES / "legacy" / f"{sid}.py").exists() and not (STRATEGIES / f"{sid}.py").exists(), sid
