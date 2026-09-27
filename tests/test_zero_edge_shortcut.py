"""P5.12 part 3: the engine skips the solve when buying nothing is optimal
(holdings_solver.no_book_grows), and decides exactly as if it had solved."""
import random

import decision_engine as de
import holdings_solver as hs


def _case(rng):
    n = rng.randint(3, 11)
    raw = [rng.random() ** 2 for _ in range(n)]
    probs = {f"b{i}": v / sum(raw) for i, v in enumerate(raw)}
    # Half the books price every bucket near the view (the anchored case:
    # nothing to buy); the rest misprice buckets by up to 30% (sometimes an edge).
    noise = 0.02 if rng.random() < 0.5 else 0.3
    over = rng.uniform(-0.02, 0.10)
    book = {}
    for b, p in probs.items():
        mkt = min(max(p * rng.uniform(1 - noise, 1 + noise), 0.002), 0.97)
        ask = round(min(mkt + over / n + 0.005, 0.99), 3)
        bid = round(max(ask - rng.uniform(0.005, 0.05), 0.001), 3)
        book[b] = {"ask": ask, "bid": bid, "no_ask": round(min(1 - bid + 0.005, 0.99), 3),
                   "no_bid": round(1 - ask, 3), "depth_usd": 200.0}
    held = {}
    if rng.random() < 0.3:
        b = rng.choice(list(probs))
        held[b] = (round(rng.uniform(1, 40), 2), 0.0)
    view = {"strategy_id": "t", "city_key": "c", "resolution_date": "2026-09-28", "probs": probs,
            "allow": rng.choice([("YES",), ("YES", "NO"), ("NO",)]), "lock": rng.random() < 0.3}
    elsewhere = rng.choice([0.0, 0.0, 120.0])        # money on other ladders (the lock's floor)
    cost = sum(y * 0.3 for y, _ in held.values())
    ledger = {"equity_usd": 1000.0, "cash_usd": 1000.0 - cost - elsewhere, "held": held, "held_usd": cost}
    return view, book, ledger


def test_skipping_the_solve_never_changes_a_decision(monkeypatch):
    rng = random.Random(7)
    params = {"against_market_gate_on": False}
    fired = locks_fired = 0
    for _ in range(32):
        view, book, ledger = _case(rng)
        fast = de.decide(view, book=book, ledger=ledger, params=params)
        with monkeypatch.context() as m:
            m.setattr(hs, "no_book_grows", lambda *a, **k: False)
            slow = de.decide(view, book=book, ledger=ledger, params=params)
        fired += bool(fast.get("no_edge"))
        locks_fired += bool(fast.get("no_edge") and view["lock"])
        assert (fast["action"], fast["reason_code"], fast["orders"]) == \
               (slow["action"], slow["reason_code"], slow["orders"])
        if fast.get("no_edge"):
            assert slow["g_target"] <= slow["g_now"] + 1e-12     # the solve found nothing better either
    assert 5 <= fired < 32, fired     # both paths exercised
    assert locks_fired >= 1, locks_fired          # the test must exercise the shortcut, not only the solve


def test_an_edge_is_never_skipped():
    ladder = [{"id": "a", "p": 0.6, "yes_price": 0.40, "no_price": 0.62},
              {"id": "b", "p": 0.4, "yes_price": 0.62, "no_price": 0.40}]
    assert not hs.no_book_grows(ladder, allow=("YES",))
    fair = [dict(b, yes_price=b["p"] + 0.02, no_price=1 - b["p"] + 0.02) for b in ladder]
    assert hs.no_book_grows(fair, allow=("YES", "NO"))
    # capped to zero, or above the price rail, an edge is not buyable
    assert hs.no_book_grows(ladder, allow=("YES",), caps={"a:YES": 0.0})
    assert hs.no_book_grows(ladder, allow=("YES",), max_price=0.35)


def test_what_is_held_moves_the_test():
    """The rate is taken at the current book: a bucket already held pays in
    its outcome, so buying more of it is worth less than on an empty ledger."""
    ladder = [{"id": "a", "p": 0.5, "yes_price": 0.46, "no_price": None},
              {"id": "b", "p": 0.5, "yes_price": 0.56, "no_price": None}]
    only_a = {"b:YES": 0.0}
    assert not hs.no_book_grows(ladder, allow=("YES",), caps=only_a)
    assert hs.no_book_grows(ladder, allow=("YES",), caps=only_a, held={"a": (400.0, 0.0)},
                            total_usd=1000.0, cash_usd=800.0)
    # and it can make the other bucket worth buying, as a hedge
    assert not hs.no_book_grows(ladder, allow=("YES",), held={"a": (400.0, 0.0)}, total_usd=1000.0, cash_usd=800.0)
