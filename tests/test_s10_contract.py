"""The S10 contract (docs/S10_MAX_TEMP_WINNER.md, plan v2 P7.1) is frozen
before any evaluation. These tests hold it to the code it names, so a change on
either side shows up here instead of quietly changing what a result means."""
import re
from pathlib import Path

import model_promotion
import tick
import walk_forward

DOC = (Path(__file__).resolve().parents[1] / "docs" / "S10_MAX_TEMP_WINNER.md").read_text()


def test_the_contract_carries_its_version():
    assert re.search(r"\*\*Version `s10-contract-v\d+`, frozen \d+ \w+ 20\d\d", DOC)


def test_the_checkpoints_are_the_ticks():
    table = re.findall(r"^\| `([a-z0-9_]+)` \|", DOC, flags=re.M)
    assert tuple(table) == tuple(c for c in tick.CHECKPOINTS if c != "day_ahead")
    assert "Prospective day-ahead addition" in DOC


def test_the_fixed_checkpoints_are_at_the_stated_local_times():
    assert {k:v for k,v in tick.FIXED_LOCAL.items() if k != "day_ahead"} == {"morning": (0, 9, 0), "noon": (0, 12, 0), "d1_eve": (-1, 18, 0)}
    assert "| `d1_eve` | 18:00 the day before |" in DOC
    assert "| `morning` | 09:00 |" in DOC and "| `noon` | 12:00 |" in DOC
    assert tick.PEAK_OFFSETS_H == {"prepeak_2h": -2, "prepeak_1h": -1, "postpeak_1h": 1}


def test_better_means_the_p34_rule():
    assert model_promotion.BOOT_INTERVAL == 0.90 and "`BOOT_INTERVAL = 0.90`" in DOC
    assert walk_forward.MIN_GATE_DAYS == 20 and "at least 20 scored days" in DOC


def test_truth_is_the_venue_winner_only():
    assert "**The venue winner only:**" in DOC and "`label_source = 'station'`" in DOC


def test_prospective_day_ahead_does_not_rewrite_the_frozen_replay_contract():
    assert tick.FIXED_LOCAL["day_ahead"] == (-1, 9, 0)
    assert "09:00 on D-1" in DOC
