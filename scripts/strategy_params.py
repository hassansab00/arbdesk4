"""The priors of every strategy-level learned parameter (plan v2 P8.2, Rule 11).

A strategy file holds no numbers of its own (tests/test_strategies_have_no_magic_numbers.py):
whatever a strategy needs to decide - a hysteresis, a buffer - is a parameter
here, with its prior, its hard bounds and the version a decision records. The
nightly loop (P5.8, scripts/strategy_learn.py) may move one off its prior only
once settings.strategy_learning is on and it has beaten the prior out of
sample; until then value() returns the prior.

The engine's own parameters (lambda, h, alpha: holdings_solver; c_wait:
timing; the belief table: belief) stay with the module that uses them.
"""

PRIORS = {
    # S10 (P7.5): leave the held bucket only when the growth gained beats the
    # exit cost plus this. The plan's prior and bounds.
    "h_switch": {"prior": 0.005, "bounds": (0.001, 0.02), "version": "prior"},
    # S2 (P8.2): a basket is an arbitrage only when its fee-inclusive YES asks
    # sum below 1 - buffer, the room for a leg failing or slipping. The plan's.
    "s2_buffer": {"prior": 0.01, "bounds": (0.002, 0.05), "version": "prior"},
}


def bounds(name):
    return PRIORS[name]["bounds"]


def prior(name):
    return PRIORS[name]["prior"]


def value(name, learned=None):
    """The value to use: `learned` if given (clipped to the bounds), else the prior."""
    lo, hi = bounds(name)
    v = prior(name) if learned is None else float(learned)
    return min(max(v, lo), hi)


def version(name, learned_version=None):
    return learned_version or PRIORS[name]["version"]
