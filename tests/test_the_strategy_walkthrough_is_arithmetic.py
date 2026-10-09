"""The panel that explains the strategies has to be right about them.

"i dont understand what you mean by shadow s1 s3 and s6... i want to know if
all strategy mechanisms are correct, deployable, and functional."

Nine strategies is more than anyone holds in their head, and the board could
only ever say whether one FIRED - never what it is. So the page now opens a
walkthrough under each: what it does, what it checks, and one worked market of
eleven buckets you can click to settle the day anywhere and watch the position
pay out or not.

A panel whose whole job is to explain the arithmetic is the last place the
arithmetic should be typed by hand. The first draft carried its totals as
string literals and two were already wrong - "$0.86 including fees" on a set
costing $0.89, and "beats the bar by 4c" where the bar works out at 3c. Both
wrong in the direction that flatters the strategy, which is the direction a
typed number always drifts.

So the totals are computed from the market, and this asserts:

  every strategy in REGISTRY has a walkthrough, and no walkthrough describes a
  strategy that does not exist

  the worked market is a real market - eleven buckets, two open tails, and
  probabilities that sum to exactly 1, because exactly one bucket wins

  the market says what the live board says. It is a teaching example, not a
  capture, but an example that disagreed with the measured thing would teach
  the wrong lesson: the asks must sum ABOVE a dollar (no arbitrage, as
  measured on 21 Sep across 71 complete baskets) and the best adjacent pair
  must fail the two-bucket cover's probability bar rather than its price bar,
  which is what the live board does 127 times to nothing.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PANEL = (ROOT / "web" / "components" / "StrategyExplainer.tsx").read_text()
PAGE = (ROOT / "web" / "app" / "strategies" / "page.tsx").read_text()


def _without_comments(src: str) -> str:
    """A measurement quoted in a comment is a record of what was observed.

    The rule below is that no total is typed as a VALUE the panel renders - not
    that the number may never be written down. The header of that file quotes
    what the live board measured on 21 Sep, which is exactly the sort of thing
    a comment is for, and stripping comments is what separates the two.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"^\s*//[^\n]*", " ", src, flags=re.M)


PANEL_CODE = _without_comments(PANEL)


def _market():
    """The eleven buckets, parsed out of the component."""
    block = PANEL.split("export const MARKET: Bucket[] = [")[1].split("];")[0]
    out = []
    for line in block.splitlines():
        m = re.search(r'label:\s*"([^"]+)".*?ask:\s*([\d.]+).*?prob:\s*([\d.]+)', line)
        if m:
            out.append({"label": m.group(1), "ask": float(m.group(2)),
                        "prob": float(m.group(3)), "tail": "tail:" in line})
    return out


def _fee(p):
    return 0.05 * p * (1 - p)


def _strategy_ids():
    """Every id the page can list: the signal-path REGISTRY, the retired ids
    it still shows, and the engine's (engine_shadow.STRATEGIES).

    THE ENGINE'S WERE MISSING (Hassan, 9 Oct: "we need the how it works
    explanation for each strategy, we used to have it visible when we clicked
    but now no"). S10, S11 and S12 were registered on 27 Sep and their
    model-only twins on 8 Oct, all through engine_shadow, which this test
    never read - so 12 of the 13 active strategies said "No walkthrough is
    written" while it stayed green."""
    from strategies import REGISTRY as LIVE
    from strategies.legacy import LEGACY_REGISTRY
    import engine_shadow
    return {**LIVE, **LEGACY_REGISTRY}, set(engine_shadow.ANCHORED), set(engine_shadow.MODEL_ONLY)


DESCRIBED = set(re.findall(r"^\s{2}(s\d+_\w+):\s*\{", PANEL, re.M))


def test_every_registered_strategy_has_a_walkthrough():
    registry, anchored, _twins = _strategy_ids()
    missing = sorted((set(registry) | anchored) - DESCRIBED)
    assert not missing, (
        f"no walkthrough for {missing}. The page will offer 'how it works' and then say "
        "there is nothing written, which is worse than not offering it."
    )


def test_every_model_only_twin_shows_its_base():
    """A twin decides by its base's rules at w = 1 (engine_views.MODEL_ONLY),
    so its walkthrough is the base's on the model's numbers - resolved by id,
    not six copies that could drift."""
    from strategies.engine_views import MODEL_ONLY
    _registry, _anchored, twins = _strategy_ids()
    assert twins == set(MODEL_ONLY)
    for twin, base in MODEL_ONLY.items():
        assert twin == f"{base}_model" and base in DESCRIBED, twin
    assert "PLAYS[strategyId] ?? twinPlay(strategyId)" in PANEL_CODE
    assert 'strategyId.endsWith("_model")' in PANEL_CODE


def test_no_walkthrough_describes_a_strategy_that_does_not_exist():
    registry, anchored, _twins = _strategy_ids()
    stale = sorted(DESCRIBED - set(registry) - anchored)
    assert not stale, f"explained on the page but not in REGISTRY or engine_shadow: {stale}"


def test_the_page_says_what_every_strategy_does():
    registry, anchored, twins = _strategy_ids()
    said = set(re.findall(r"^\s{2}(s\d+_\w+):\s*$", PAGE, re.M))
    missing = sorted((set(registry) | anchored) - said)
    assert not missing, f"no one-line summary on the strategies page for {missing}"
    assert "whatItDoes(r.strategy_id)" in PAGE and 'id.endsWith("_model")' in PAGE


def test_the_engine_walkthroughs_take_what_the_solver_takes():
    """web/lib/kelly.ts ports holdings_solver; web/tests/kelly.test.cjs pins
    its answers on the worked market to these. If the solver changes, both
    sides must be looked at again."""
    import holdings_solver as hs
    market = _market()
    ladder = [{"id": b["label"], "p": b["prob"], "yes_price": b["ask"] if b["ask"] >= 0.07 else None,
               "no_price": 1 - b["ask"]} for b in market]
    yes = hs.solve(ladder, allow=("YES",))
    assert {k.split(":")[0] for k, w in yes["weights"].items() if w > 1e-3} == {"30–31", "31–32"}
    assert abs(yes["cash"] - 0.9191090269636576) < 1e-6
    no = hs.solve(ladder, allow=("NO",))
    assert {k.split(":")[0] for k, w in no["weights"].items() if w > 1e-3} == {"32–33", "33–34", "34–35", "≥35"}
    assert abs(no["growth"] - 0.02407555097182852) < 1e-9
    assert hs.best_single_bucket([{"id": b["id"], "p": b["p"], "yes_price": b["yes_price"]} for b in ladder])[0] == "31–32"
    assert hs.single_bucket_growth(0.30, 0.30) == 0.0, "the favourite is priced at its probability"


def test_the_worked_market_is_a_real_market():
    market = _market()
    assert len(market) == 11, (
        f"{len(market)} buckets. The venue lists eleven - nine closed and two open tails - "
        "and a strategy that reasons about the whole ladder reasons about eleven of them."
    )
    assert sum(1 for b in market if b["tail"]) == 2, "a ladder has exactly two open tails"
    total = sum(b["prob"] for b in market)
    assert abs(total - 1.0) < 1e-9, (
        f"the probabilities sum to {total}, not 1. Exactly one bucket wins, so anything else "
        "is not a distribution and every payout computed from it is wrong."
    )
    assert all(0 < b["ask"] < 1 for b in market), "a price outside (0,1) is not a price"


def test_the_worked_market_agrees_with_what_the_live_board_measured():
    """Measured 21 Sep 2026: 71 complete baskets, cheapest 1.0628, average
    1.2581, none under a dollar. An example where the ladder summed UNDER a
    dollar would teach that arbitrage is ordinary here. It is not."""
    market = _market()
    asks = sum(b["ask"] for b in market)
    fees = sum(_fee(b["ask"]) for b in market)
    assert asks > 1.0, (
        f"the eleven asks sum to {asks:.4f}, so the worked example contains a riskless "
        "arbitrage. No complete basket measured on this venue ever has."
    )
    assert 1.0 < asks + fees < 1.35, (
        f"the ladder costs {asks + fees:.4f} with fees, outside the range the live board "
        "measured (1.06 to 1.26). An example that far from the real book is a different game."
    )


def test_the_two_bucket_cover_fails_on_probability_and_not_on_price():
    """The live board's own shape: on 21 Sep the cost gate passed 127 times and
    the probability gate zero, best pair 0.703 against its 0.72 bar. An example
    where the pair were simply too expensive would blame the wrong gate, and
    the wrong gate is the one somebody would then go and loosen."""
    market = _market()
    pairs = [(market[i], market[i + 1]) for i in range(len(market) - 1)]
    best = max(pairs, key=lambda pr: pr[0]["prob"] + pr[1]["prob"])
    cost = sum(b["ask"] for b in best) + sum(_fee(b["ask"]) for b in best)
    prob = sum(b["prob"] for b in best)
    assert cost < 0.70, f"the best pair costs {cost:.4f}, so it is refused on PRICE, not probability"
    assert prob < 0.72, f"the best pair is {prob:.3f} likely, which passes the bar it should fail"


def test_the_covered_basket_is_the_one_case_that_does_clear_its_invariant():
    """s6's whole mechanism is `sum(ask) < 1 after fees`. If the worked example
    failed that too, nine panels would say the same thing - nothing ever fires -
    and the one strategy whose structure actually works here would be invisible."""
    market = _market()
    covered = market[4:9]
    cost = sum(b["ask"] for b in covered) + sum(_fee(b["ask"]) for b in covered)
    assert cost < 1.0, (
        f"the covered set costs {cost:.4f}, so s6's invariant fails on the worked market and "
        "its walkthrough claims a structure the example does not support"
    )
    assert sum(b["prob"] for b in covered) > 0.5, "a covered set nobody expects to win is not insurance"


@pytest.mark.parametrize("literal", ["$1.13", "$0.86", "$0.66", "45c", "51%", "84%", "71%"])
def test_no_total_is_typed_into_the_panel(literal):
    """Each of these was once a string in this file. Every one is a sum over
    MARKET, so every one must be computed - a literal here is a number that
    stops moving when the market does."""
    assert literal not in PANEL_CODE, (
        f"{literal!r} is typed into the walkthrough's code. Compute it from MARKET: that is "
        "the difference between a panel that explains the arithmetic and one that asserts it."
    )


def test_the_page_actually_opens_the_walkthrough():
    assert "StrategyExplainer" in PAGE, "the component exists and nothing renders it"
    assert "how it works" in PAGE, "the control that opens it is gone"
