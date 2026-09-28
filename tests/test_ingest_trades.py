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

    def fetch(ids, since):
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

        def slow(ids, since, real=real):
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

    def slow(ids, since):
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
    assert "scripts/ingest_trades.py --budget 30" in steps[start]["run"]
    assert steps[start]["run"].rstrip().endswith("&")                      # backgrounded
    assert start < names.index("Checkpoints")
    last = steps[-1]
    assert "trades.rc" in last["run"] and "steps.trades.outcome == 'success'" in last["if"]
    # The only other place the script runs is nowhere: one run a tick.
    assert sum("ingest_trades.py" in (s.get("run") or "") for s in steps) == 1


def test_thirty_seconds_fit_the_deadline_it_starts_under():
    """The step starts a few seconds after the Deadline step (checkout, python,
    the cached venv); 48 s less RESERVE_S leaves room for the 30 it asks, and
    budget() clamps it to the deadline whatever the start."""
    deadline_step = _tick_steps()[0]
    assert "+ 48 ))" in deadline_step["run"]
    assert 48 - it.RESERVE_S - 10 >= 30
    assert it.budget(30, deadline=1000, now_epoch=1000 - 48 + 10) == 30
    assert it.budget(30, deadline=1000, now_epoch=1000 - 20) == pytest.approx(14)


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
