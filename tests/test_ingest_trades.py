"""Trade prints for every open market (replaces n8n P0.4, plan v2 P6.2)."""
import datetime as dt

import pytest

import ingest_trades as it

NOW = dt.datetime(2026, 9, 25, 9, 0, tzinfo=dt.timezone.utc)
BAND = {"band_id": "b1", "token_yes": "tokY", "token_no": "tokN", "condition_id": "0xc1",
        "markets": {"city_key": "nyc", "resolution_date": "2026-09-25", "closed": False}}


def _trade(ts, asset="tokY", price=0.4, size=10, cond="0xc1", wallet="0xw"):
    return {"asset": asset, "conditionId": cond, "price": price, "size": size, "timestamp": ts,
            "side": "BUY", "proxyWallet": wallet}


def test_the_mapping_is_p04s():
    rows, unmatched = it.to_rows([_trade(1790325600), _trade(1790325600, asset="zzz"),
                                  _trade(1790325600, price=1.0), _trade(1790325600, size=0)],
                                 {"tokY": BAND, "tokN": BAND}, "2026-09-25T09:00:00+00:00")
    assert unmatched == 1 and len(rows) == 1
    r = rows[0]
    assert r == {"band_id": "b1", "condition_id": "0xc1", "city_key": "nyc", "token_id": "tokY",
                 "side": "BUY", "price": 0.4, "size": 10.0,
                 "traded_at": "2026-09-25T08:40:00+00:00", "proxy_wallet": "0xw",
                 "ingested_at": "2026-09-25T09:00:00+00:00"}


def test_proxy_wallet_is_never_null():
    rows, _ = it.to_rows([dict(_trade(1790325600), proxyWallet=None)], {"tokY": BAND}, "x")
    assert rows[0]["proxy_wallet"] == ""


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_paging_stops_at_the_high_water_mark():
    pages = [[_trade(3000 - i) for i in range(it.PAGE)], [_trade(900 - i) for i in range(it.PAGE)],
             [_trade(1) for _ in range(it.PAGE)]]
    calls = []

    def get(url, params, headers, timeout):
        calls.append(params["offset"])
        return _Resp(pages[params["offset"] // it.PAGE])
    since = dt.datetime.fromtimestamp(1500, dt.timezone.utc)
    got = it.fetch_batch(["0xc1", "0xc2"], since, get=get)
    assert calls == [0, it.PAGE]                      # the second page reaches below the mark: stop
    assert len(got) == 2 * it.PAGE


def test_a_short_page_is_the_last():
    calls = []

    def get(url, params, headers, timeout):
        calls.append(params["market"])
        return _Resp([_trade(5)])
    assert len(it.fetch_batch(["0xa", "0xb"], None, get=get)) == 1
    assert calls == ["0xa,0xb"]                       # one request for the whole batch


class _Limited:
    """A 429 response, with Retry-After when given."""
    status_code = 429

    def __init__(self, after=None):
        self.headers = {} if after is None else {"Retry-After": str(after)}

    def raise_for_status(self):
        import requests
        raise requests.HTTPError("429 Client Error: Too Many Requests", response=self)


def test_a_429_is_waited_out_once_when_the_budget_allows():
    """R8 (Wave 2.D): 5 of 168 runs in the 7 days to 9 Oct lost a batch to a
    429. The page is asked again after Retry-After, and the wait is counted."""
    answers = [_Limited(after=3), _Resp([_trade(5)])]
    slept, stats = [], {}
    got = it.fetch_batch(["0xa"], None, get=lambda *a, **k: answers.pop(0),
                         wait_until=100.0, stats=stats, sleep=slept.append, clock=lambda: 0.0)
    assert len(got) == 1 and slept == [3.0]
    assert stats == {"waits": 1, "seconds": 3.0}


def test_a_429_without_retry_after_waits_the_default_and_never_too_long():
    answers = [_Limited(), _Resp([_trade(5)])]
    slept = []
    it.fetch_batch(["0xa"], None, get=lambda *a, **k: answers.pop(0), wait_until=100.0,
                   sleep=slept.append, clock=lambda: 0.0)
    assert slept == [it.RATE_LIMIT_WAIT_S]
    answers = [_Limited(after=600), _Resp([_trade(5)])]
    slept = []
    it.fetch_batch(["0xa"], None, get=lambda *a, **k: answers.pop(0), wait_until=100.0,
                   sleep=slept.append, clock=lambda: 0.0)
    assert slept == [it.RATE_LIMIT_MAX_WAIT_S]


def test_a_retry_after_of_zero_is_asked_again_at_once():
    """Codex on #356: `Retry-After: 0` is valid and means now; read as falsy
    it became the 5 s default, spending budget for nothing."""
    answers = [_Limited(after=0), _Resp([_trade(5)])]
    slept = []
    got = it.fetch_batch(["0xa"], None, get=lambda *a, **k: answers.pop(0), wait_until=1.0,
                         sleep=slept.append, clock=lambda: 0.0)
    assert len(got) == 1 and slept == [0.0]


def test_a_429_past_the_budget_or_twice_is_raised_as_before():
    """The batch then fails and the next run resumes it from the cursor,
    exactly as before the wait existed."""
    import requests
    slept = []
    with pytest.raises(requests.HTTPError):
        it.fetch_batch(["0xa"], None, get=lambda *a, **k: _Limited(after=10), wait_until=5.0,
                       sleep=slept.append, clock=lambda: 0.0)
    assert slept == []
    answers = [_Limited(after=1), _Limited(after=1)]
    with pytest.raises(requests.HTTPError):
        it.fetch_batch(["0xa"], None, get=lambda *a, **k: answers.pop(0), wait_until=100.0,
                       sleep=slept.append, clock=lambda: 0.0)
    assert slept == [1.0], "one wait a page, never a loop"


def test_the_run_passes_its_budget_to_the_wait():
    import inspect
    src = inspect.getsource(it.main)
    assert "fetch_batch(batch, since, wait_until=started + budget_s, stats=rate_limited)" in src
    assert '"rate_limited": rate_limited' in src


def test_the_plan_resumes_an_incomplete_cycle_with_the_older_mark():
    hw = dt.datetime(2026, 9, 25, 8, 0, tzinfo=dt.timezone.utc)
    assert it.plan(hw, {}) == (hw - it.OVERLAP, "")
    assert it.plan(hw, {"complete": True, "since": "2026-09-24T00:00:00+00:00", "cursor": None}) == (hw - it.OVERLAP, "")
    since, cursor = it.plan(hw, {"complete": False, "since": "2026-09-24T09:00:00+00:00", "cursor": "0x0099"})
    assert since == dt.datetime(2026, 9, 24, 9, tzinfo=dt.timezone.utc) and cursor == "0x0099"
    # A run logged before cursors (25 Sep 09:37Z) restarts its cycle, with its mark.
    assert it.plan(hw, {"complete": False, "since": "2026-09-24T08:42:45+00:00", "next_batch": 1}) == \
        (dt.datetime(2026, 9, 24, 8, 42, 45, tzinfo=dt.timezone.utc), "")
    assert it.plan(None, {}) == (None, "")


def _wire(monkeypatch, n_conditions, prev=None, fail_batches=(), deep_batches=(), mode="auto"):
    bands = [dict(BAND, band_id=f"b{i}", token_yes=f"y{i}", token_no=f"n{i}", condition_id=f"0x{i:04d}")
             for i in range(n_conditions)]
    logged, inserted = {}, []
    monkeypatch.setattr(it, "rest_all", lambda t, p, order: bands)

    def rest(table, params):
        if table == "settings":
            return [{"value": {"P0.4_trade_history": {"mode": mode, "every_minutes": 300}}}]
        if table == "trades_observed":
            return [{"traded_at": "2026-09-24T09:42:45+00:00"}]
        return [{"detail": prev}] if prev is not None else []
    monkeypatch.setattr(it, "rest", rest)
    seen = []

    def fetch(ids, since, **kw):
        j = int(ids[0][2:]) // it.BATCH
        seen.append(j)
        if j in fail_batches:
            raise RuntimeError("502 from the trades API")
        if j in deep_batches:                          # every page full, all newer than the mark
            return [_trade(NOW.timestamp(), asset=f"y{int(ids[0][2:])}", cond=ids[0])] * (it.MAX_PAGES * it.PAGE)
        return [_trade(NOW.timestamp(), asset=f"y{int(c[2:])}", cond=c) for c in ids]
    monkeypatch.setattr(it, "fetch_batch", fetch)
    monkeypatch.setattr(it, "insert_new", lambda rows: inserted.append(len(rows)) or len(rows))
    monkeypatch.setattr(it, "rpc", lambda fn: {"ok": True})
    monkeypatch.setattr(it, "log_run", lambda job, status, rows, detail: logged.update(job=job, status=status, rows=rows))
    return logged, seen


def test_every_open_market_is_read_and_only_new_rows_count(monkeypatch):
    n = 2 * it.BATCH + 20                                # three batches, the last one short
    logged, seen = _wire(monkeypatch, n)
    d = it.main(now=NOW)
    assert seen == [0, 1, 2] and d["complete"] and d["new"] == n
    assert logged == {"job": "P0.4_trade_history", "status": "ok", "rows": n}
    assert d["since"] == "2026-09-24T08:42:45+00:00"     # the stored high-water mark, less the overlap


def test_a_failed_batch_is_resumed_next_run(monkeypatch):
    n = 2 * it.BATCH + 20
    logged, seen = _wire(monkeypatch, n, fail_batches={1})
    d = it.main(now=NOW)
    assert seen == [0, 1, 2]                               # the batches after a failure still run
    assert logged["status"] == "attention" and not d["complete"] and d["cursor"] == f"0x{it.BATCH - 1:04d}"
    logged, seen = _wire(monkeypatch, n, prev=d)
    d2 = it.main(now=NOW)
    assert seen == [1, 2] and d2["complete"] and d2["since"] == d["since"]


def test_a_run_out_of_time_says_where_it_stopped(monkeypatch):
    logged, seen = _wire(monkeypatch, 120)
    d = it.main(budget_s=-1, now=NOW)
    assert seen == [] and not d["complete"] and d["cursor"] == "" and d["status"] == "attention"


def test_a_backlog_spread_over_runs_finishes_its_cycle(monkeypatch):
    """The bug the first live run exposed (25 Sep 09:37Z): one batch used the
    whole budget, and a cycle that had to finish inside ONE run never could."""
    n = 3 * it.BATCH
    clock = [0.0]
    monkeypatch.setattr(it.time, "monotonic", lambda: clock[0])
    prev, runs = None, []
    for _ in range(4):
        logged, seen = _wire(monkeypatch, n, prev=prev)
        real = it.fetch_batch

        def slow(ids, since, real=real, **kw):
            clock[0] += 20.0                               # a backlog batch: 20 s, measured
            return real(ids, since)
        monkeypatch.setattr(it, "fetch_batch", slow)
        prev = it.main(now=NOW)
        runs.append((seen[:], prev["complete"]))
    assert runs[:3] == [([0], False), ([1], False), ([2], True)]
    assert runs[3][0] == [0]                               # the next cycle starts at the top
    assert prev["since"] == "2026-09-24T08:42:45+00:00"     # from the stored mark, not the old one


def test_a_cycle_carries_on_after_its_cursor(monkeypatch):
    # The cursor is a condition id, not a batch number, so a market opening
    # mid-cycle cannot shift an unread market into a batch already done.
    logged, seen = _wire(monkeypatch, 2 * it.BATCH, prev={"complete": False, "since": "2026-09-24T08:00:00+00:00",
                                                        "cursor": f"0x{it.BATCH - 1:04d}"})
    d = it.main(now=NOW)
    assert seen == [1] and d["complete"]


def test_a_batch_deeper_than_the_api_pages_is_reported(monkeypatch):
    logged, seen = _wire(monkeypatch, 2 * it.BATCH, deep_batches={1})
    d = it.main(now=NOW)
    assert d["truncated"] == [f"0x{it.BATCH:04d}"] and d["status"] == "attention" and d["complete"]
    logged, seen = _wire(monkeypatch, 2 * it.BATCH)
    assert it.main(now=NOW)["truncated"] == []


@pytest.mark.parametrize("mode", ["off", "manual"])
def test_the_workflows_page_switch_is_obeyed(monkeypatch, mode):
    logged, seen = _wire(monkeypatch, 10, mode=mode)
    d = it.main(now=NOW)
    assert seen == [] and d["status"] == "skipped" and logged["status"] == "skipped"


def test_the_n8n_interval_does_not_throttle_the_hourly_run(monkeypatch):
    # every_minutes is 300 and the last run was an hour ago: it still runs.
    logged, seen = _wire(monkeypatch, 10, prev={"complete": True})
    assert it.main(now=NOW)["status"] == "ok" and seen == [0]


def test_a_batch_that_would_overrun_the_budget_is_not_started(monkeypatch):
    # 25 Sep 10:36Z: batch 1 started at 14 s of a 15 s budget and ran 13 s.
    clock = [0.0]
    monkeypatch.setattr(it.time, "monotonic", lambda: clock[0])
    logged, seen = _wire(monkeypatch, 3 * it.BATCH)
    real = it.fetch_batch

    def slow(ids, since, **kw):
        clock[0] += 9.0                                    # 9 s a batch: a second one ends at 18 s
        return real(ids, since)
    monkeypatch.setattr(it, "fetch_batch", slow)
    d = it.main(budget_s=15, now=NOW)
    assert seen == [0] and not d["complete"] and d["cursor"] == f"0x{it.BATCH - 1:04d}"


def test_the_budget_never_runs_past_the_tick_deadline():
    assert it.budget(15, None) == 15
    assert it.budget(15, deadline=1000, now_epoch=950) == 15          # 44 s left: the flag binds
    assert it.budget(15, deadline=1000, now_epoch=985) == pytest.approx(9)
    assert it.budget(15, deadline=1000, now_epoch=999) < 0           # nothing starts; resumes next hour


def test_a_run_with_no_time_left_starts_no_batch(monkeypatch):
    monkeypatch.setenv("TICK_DEADLINE", "0")
    logged, seen = _wire(monkeypatch, 2 * it.BATCH)
    reads = []
    monkeypatch.setattr(it, "rest_all", lambda *a, **k: reads.append(a) or [])
    d = it.main(now=NOW)
    # No batch, and no read either: the reads alone ran a tick past its minute
    # (27 Sep 12:36Z). Logged as skipped, which previous_run() passes over.
    assert seen == [] and reads == [] and d["status"] == "attention"
    assert logged == {"job": "P0.4_trade_history", "status": "attention", "rows": 0}
    assert not d["complete"] and d["cursor"] == ""


def test_a_run_with_no_time_left_carries_the_last_runs_place(monkeypatch):
    monkeypatch.setenv("TICK_DEADLINE", "0")
    prev = {"complete": False, "since": "2026-09-24T08:42:45+00:00", "cursor": "0x0099"}
    _wire(monkeypatch, 3 * it.BATCH, prev=prev)
    d = it.main(now=NOW)
    # the next run plans from this row exactly as it would have from the last real one
    assert it.plan(None, d) == it.plan(None, prev)


# --- BESIDE THE TICK (28 Sep) ------------------------------------------------
# Run last, the step got what the others left before TICK_DEADLINE: nothing in
# 9 of 27 ticks (27 Sep 16:36Z - 28 Sep 19:36Z) and under 5 s in 6 more. tick.yml now starts it in
# the background before the checkpoints and collects it in the last step.

def _tick_steps():
    import pathlib
    import yaml
    wf = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"
                         / "tick.yml").read_text())
    return wf["jobs"]["tick"]["steps"]


def test_the_tick_starts_the_trades_before_the_checkpoints_and_collects_them_last():
    steps = _tick_steps()
    names = [s.get("name", "") for s in steps]
    start = next(i for i, s in enumerate(steps) if s.get("id") == "trades")
    assert "scripts/ingest_trades.py --budget 60" in steps[start]["run"]
    assert steps[start]["run"].rstrip().endswith("&")                      # backgrounded
    assert start < names.index("Checkpoints")
    last = steps[-1]
    assert "trades.rc" in last["run"] and "steps.trades.outcome == 'success'" in last["if"]
    # The only other place the script runs is nowhere: one run a tick.
    assert sum("ingest_trades.py" in (s.get("run") or "") for s in steps) == 1


def test_sixty_seconds_fit_the_deadline_it_starts_under():
    """The step starts a few seconds after the Deadline step (checkout, python,
    the cached venv); the deadline less RESERVE_S leaves room for the 60 it
    asks (30 until 9 Oct, when the tick's deadline was 48 s), and budget()
    clamps it to the deadline whatever the start."""
    import re
    deadline_step = _tick_steps()[0]
    deadline_s = int(re.search(r"\+ (\d+) \)\)", deadline_step["run"]).group(1))
    asked = int(re.search(r"ingest_trades\.py --budget (\d+)", "\n".join(
        s.get("run") or "" for s in _tick_steps())).group(1))
    assert deadline_s - it.RESERVE_S - 10 >= asked
    assert it.budget(60, deadline=1000, now_epoch=1000 - deadline_s + 10) == 60
    assert it.budget(60, deadline=1000, now_epoch=1000 - 20) == pytest.approx(14)


def test_the_background_run_hands_its_exit_code_to_the_last_step(tmp_path):
    """Under the runner's own shell flags: the start step returns at once, and
    the last step waits for the run and fails with it (bash -e would otherwise
    end the subshell before the code is written down)."""
    import os
    import stat
    import subprocess
    import time
    steps = _tick_steps()
    start = next(s for s in steps if s.get("id") == "trades")["run"]
    collect = steps[-1]["run"]
    fake = tmp_path / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/bash\nsleep 1\necho fake run\nexit 3\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path)}
    t0 = time.monotonic()
    subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", start],
                   cwd=tmp_path, env=env, check=True, timeout=10)
    assert time.monotonic() - t0 < 0.9                                   # did not wait for the run
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", collect],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert done.returncode == 3 and "fake run" in done.stdout


# --------------------------------------------------------------------------
# Prints go in through insert_trade_prints(), which dedupes on the 16-byte key
# (WXPredict build 2.A, 8 Oct: 20261008170000)
# --------------------------------------------------------------------------
class _Posted:
    def __init__(self, body, status=200):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def test_prints_go_through_insert_trades_in_batches_and_count_what_was_new(monkeypatch):
    import json
    calls = []

    def post(url, **kw):
        calls.append((url, json.loads(kw["data"])))
        rows = json.loads(kw["data"])["p_rows"]
        return _Posted([{"trade_id": i} for i in range(len(rows) - 1)])     # one of each batch was held
    monkeypatch.setattr(it, "_post", post)
    monkeypatch.setattr(it, "_cfg", lambda: {"url": "https://x.supabase.co"})
    monkeypatch.setattr(it, "_headers", lambda: {"apikey": "k"})
    rows = [{"n": i} for i in range(1200)]
    assert it.insert_new(rows) == 1197
    assert [u for u, _b in calls] == ["https://x.supabase.co/rest/v1/rpc/insert_trade_prints"] * 3
    assert [len(b["p_rows"]) for _u, b in calls] == [500, 500, 200]
    assert calls[0][1]["p_rows"][0] == {"n": 0} and calls[2][1]["p_rows"][-1] == {"n": 1199}
    assert it.insert_new([]) == 0 and len(calls) == 3


def test_a_refused_insert_fails_its_batch(monkeypatch):
    monkeypatch.setattr(it, "_post", lambda url, **kw: _Posted({"message": "no"}, 400))
    monkeypatch.setattr(it, "_cfg", lambda: {"url": "https://x.supabase.co"})
    monkeypatch.setattr(it, "_headers", lambda: {})
    with pytest.raises(it.requests.HTTPError, match="insert_trade_prints -> HTTP 400"):
        it.insert_new([{"n": 1}])


# ---------------------------------------------------------------------------
# THE TRADES BEFORE WE KNEW THE MARKET (R46, 10 Oct): each market first seen in
# the last BACKFILL_HOURS is read up to OVERLAP after it was first seen.
SEEN = dt.datetime(2026, 10, 10, 3, 20, 57, tzinfo=dt.timezone.utc)


class _Paged:
    """The data API over a fixed list: start/end inclusive, newest first, capped at offset 10,000."""

    def __init__(self, trades):
        self.trades = sorted(trades, key=lambda t: -t["timestamp"])
        self.asked = []

    def __call__(self, url, params, headers, timeout):
        self.asked.append(dict(params))
        assert params["offset"] <= it.MAX_PAGES * it.PAGE, "asked past the API's offset cap"
        rows = [t for t in self.trades if params["start"] <= t["timestamp"] <= params["end"]]
        return _Resp(rows[params["offset"]:params["offset"] + params["limit"]])


def test_a_window_is_asked_with_start_and_end_and_a_short_page_ends_it():
    api = _Paged([_trade(1000 + i) for i in range(1500)] + [_trade(5000)])
    got, whole = it.fetch_window(["0xa", "0xb"], 1000, 2600, get=api)
    assert whole and len(got) == 1500, "the trade after `end` is not read"
    assert [a["offset"] for a in api.asked] == [0, it.PAGE]
    assert all(a["market"] == "0xa,0xb" and a["start"] == 1000 and a["end"] == 2600 for a in api.asked)


def test_a_window_over_the_cap_is_read_in_halves_and_comes_back_whole():
    n = 25_000
    api = _Paged([_trade(100_000 + i, size=1 + i % 7) for i in range(n)])
    got, whole = it.fetch_window(["0xa"], 100_000, 100_000 + n, get=api)
    assert whole and len(got) == n
    assert len({t["timestamp"] for t in got}) == n, "the halves meet with no gap and no overlap"


def test_a_window_still_over_the_cap_at_the_last_halving_is_not_called_whole(monkeypatch):
    monkeypatch.setattr(it, "BACKFILL_MAX_DEPTH", 0)
    api = _Paged([_trade(100_000 + i) for i in range(12_000)])
    got, whole = it.fetch_window(["0xa"], 100_000, 112_000, get=api)
    assert not whole and len(got) == (it.MAX_PAGES + 1) * it.PAGE


def test_a_429_in_a_window_is_waited_out_once():
    answers = [_Limited(after=2), _Resp([_trade(5)])]
    slept = []
    got, whole = it.fetch_window(["0xa"], 0, 10, get=lambda *a, **k: answers.pop(0), sleep=slept.append)
    assert whole and len(got) == 1 and slept == [2.0]


def _wire_backfill(monkeypatch, markets, ok_before=True, fail=()):
    """markets: {market_id: (first_seen, n_bands)}"""
    bands = []
    for mid, (seen, nb) in markets.items():
        for i in range(nb):
            bands.append({"band_id": f"{mid}-{i}", "token_yes": f"{mid}-y{i}", "token_no": f"{mid}-n{i}",
                          "condition_id": f"0x{mid}{i:02d}",
                          "markets": {"market_id": mid, "city_key": "tokyo", "resolution_date": "2026-10-11",
                                      "closed": False, "first_seen_at": seen.isoformat()}})
    asked_since, windows, logged, inserted = [], [], {}, []

    def rest_all(table, params, order):
        assert table == "bands"
        since = dt.datetime.fromisoformat(params["markets.first_seen_at"][4:])
        asked_since.append(since)
        return [b for b in bands if dt.datetime.fromisoformat(b["markets"]["first_seen_at"]) >= since]
    monkeypatch.setattr(it, "rest_all", rest_all)
    monkeypatch.setattr(it, "rest", lambda table, params: [{"logged_at": "x"}] if ok_before else [])

    def fetch(ids, start, end, get=None, **kw):
        mid = ids[0][2:-2]
        windows.append((mid, start, end))
        if mid in fail:
            raise RuntimeError("502 from the trades API")
        return [_trade(end - 60, asset=f"{mid}-y0", cond=ids[0])], True
    monkeypatch.setattr(it, "fetch_window", fetch)
    monkeypatch.setattr(it, "insert_new", lambda rows: inserted.append(len(rows)) or len(rows))
    monkeypatch.setattr(it, "rpc", lambda fn: {"ok": True})
    monkeypatch.setattr(it, "log_run", lambda job, status, rows, detail: logged.update(job=job, status=status, rows=rows))
    return asked_since, windows, logged, inserted


def test_each_new_market_is_read_up_to_an_hour_after_it_was_first_seen(monkeypatch):
    now = SEEN + dt.timedelta(hours=5, minutes=15)
    asked_since, windows, logged, inserted = _wire_backfill(
        monkeypatch, {"m1": (SEEN, 11), "m2": (SEEN, 9)})
    d = it.backfill(now=now)
    assert asked_since == [now - dt.timedelta(hours=it.BACKFILL_HOURS)]
    assert [w[0] for w in windows] == ["m1", "m2"]
    for _, start, end in windows:
        assert end == (SEEN + it.OVERLAP).timestamp(), "exactly the window the hourly cycle never read"
        assert start == (SEEN - it.BACKFILL_LOOKBACK).timestamp()
    assert d["status"] == "ok" and d["new"] == 2 and d["read"] == 2 and not d["first_run"]
    assert logged == {"job": it.BACKFILL_JOB, "status": "ok", "rows": 2}


def test_until_a_backfill_has_finished_whole_it_reaches_further_back(monkeypatch):
    now = SEEN + dt.timedelta(hours=5)
    asked_since, *_ = _wire_backfill(monkeypatch, {"m1": (SEEN, 3)}, ok_before=False)
    d = it.backfill(now=now)
    assert d["first_run"] and d["hours"] == it.BACKFILL_FIRST_HOURS
    assert asked_since == [now - dt.timedelta(hours=it.BACKFILL_FIRST_HOURS)]


def test_thirteen_hours_cover_each_discovery_twice():
    """P0.2 first sees next-day markets at 03:20Z (all 94 of 9-10 Oct); the
    intraday runs at 02:36, 08:36, 14:36, 20:36Z. A market seen at 03:20Z must
    fall in two runs' windows, so one failed run is made good by the next."""
    runs = [dt.datetime(2026, 10, 10, h, 36, tzinfo=dt.timezone.utc) for h in (8, 14, 20)] + \
           [dt.datetime(2026, 10, 11, 2, 36, tzinfo=dt.timezone.utc)]
    covered = [r for r in runs if r - dt.timedelta(hours=it.BACKFILL_HOURS) <= SEEN <= r]
    assert len(covered) == 2


def test_a_failed_market_does_not_stop_the_others_and_is_reported(monkeypatch):
    _, windows, logged, _ = _wire_backfill(monkeypatch, {"m1": (SEEN, 2), "m2": (SEEN, 2)}, fail={"m1"})
    d = it.backfill(now=SEEN + dt.timedelta(hours=5))
    assert [w[0] for w in windows] == ["m1", "m2"] and d["read"] == 1
    assert d["status"] == "attention" and d["errors"]


def test_a_backfill_out_of_time_says_how_many_markets_are_left(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(it.time, "monotonic", lambda: clock[0])
    _, windows, logged, _ = _wire_backfill(monkeypatch, {f"m{i}": (SEEN, 2) for i in range(5)})
    real = it.fetch_window

    def slow(*a, **k):
        clock[0] += 40.0
        return real(*a, **k)
    monkeypatch.setattr(it, "fetch_window", slow)
    d = it.backfill(budget_s=100, now=SEEN + dt.timedelta(hours=5))
    assert d["read"] == 2 and d["left"] == 3 and d["status"] == "attention"


def test_the_backfill_runs_in_the_intraday_pipeline_not_the_tick():
    import pathlib
    wf = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"
    intraday = (wf / "pipeline_intraday.yml").read_text()
    assert "python scripts/ingest_trades.py --backfill --budget 300" in intraday
    assert "--backfill" not in (wf / "tick.yml").read_text(), "the tick's minute is untouched"
