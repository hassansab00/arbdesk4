"""Challenger C's forward test (tools/fec_s10_forward.py) holds the rule fixed in
docs/CHALLENGER_C_PREREG.md: looks at fixed date sets (20, then 40 only if the
first spans 0), no comparison before 20 dates, the four checks, a refused
export rather than a quietly wrong one."""
import csv
import datetime as dt
import gzip
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import fec_s10_forward as F  # noqa: E402

CPS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")

# Five rows exactly as the export statement returned them (1 Oct, rd1 against
# itself: the plumbing check run 4 Oct before any rd3 date had settled).
REAL = """city_key,unit,target_date,checkpoint,model_hour,inc_version,ch_version,n_winners,fb_agrees,p_inc,p_ch,hit_inc,hit_ch,q10_inc,q90_inc,q10_ch,q90_ch,label_max_c,settled_max_c,market_synced,market_p,market_hit
amsterdam,C,2026-10-01,morning,9,rd1:2026-09-25:f5372ebb05,rd1:2026-09-25:f5372ebb05,1,1,0.351813,0.351813,1,1,20.052,22.566,20.052,22.566,21.0,21.0,1,0.236337,0
atlanta,F,2026-10-01,morning,9,rd1:2026-09-25:f5372ebb05,rd1:2026-09-25:f5372ebb05,1,1,0.304945,0.304945,1,1,27.812,31.351,27.812,31.351,30,29.444444,1,0.263494,0
austin,F,2026-10-01,noon,12,rd1:2026-09-25:f5372ebb05,rd1:2026-09-25:f5372ebb05,1,1,0.013272,0.013272,0,0,32.153,35.521,32.153,35.521,31,30.56,1,0.929886,1
houston,F,2026-10-01,morning,9,rd1:2026-09-25:f5372ebb05,rd1:2026-09-25:f5372ebb05,1,1,0.106798,0.106798,0,0,31.803,35.591,31.803,35.591,33,31.67,1,0.144092,0
london,C,2026-10-01,postpeak_1h,14,rd1:2026-09-25:f5372ebb05,rd1:2026-09-25:f5372ebb05,1,1,0.052979,0.052979,0,0,20.0,21.296,20.0,21.296,22.0,22.0,1,0.988066,1
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    with gzip.open(p, "wt") as f:
        f.write(text)
    return str(p)


def _rows(n_dates, p_inc, p_ch, cities=8, seed=3, start=dt.date(2026, 10, 3), half=1.4, cp_override=None):
    """Synthetic scored pairs. p_inc/p_ch: callables (rng, checkpoint) -> p on the winner.
    Labels ~ N(20, 1) and the 80% interval 20 +/- half (1.4 covers about 84%)."""
    rng = random.Random(seed)
    out = []
    for k in range(n_dates):
        d = (start + dt.timedelta(days=k)).isoformat()
        for c in range(cities):
            for cp in CPS:
                pi = p_inc(rng, cp)
                pc = (cp_override or {}).get(cp, p_ch)(rng, cp)
                label = 20.0 + rng.gauss(0, 1)
                out.append({"city_key": f"c{c}", "unit": "C", "target_date": d, "checkpoint": cp, "model_hour": "12",
                            "inc_version": F.INCUMBENT, "ch_version": F.CHALLENGER, "n_winners": "1", "fb_agrees": "1",
                            "p_inc": str(pi), "p_ch": str(pc), "hit_inc": str(int(pi > 0.45)), "hit_ch": str(int(pc > 0.45)),
                            "q10_inc": str(20 - half), "q90_inc": str(20 + half), "q10_ch": str(20 - half), "q90_ch": str(20 + half),
                            "label_max_c": str(label), "settled_max_c": str(label), "market_synced": "1",
                            "market_p": "0.6", "market_hit": "1"})
    return out


def _to_file(tmp_path, rows, name="rows_2026-11-30.csv.gz"):
    text = ",".join(F.COLUMNS) + "\n" + "\n".join(",".join(r[c] for c in F.COLUMNS) for r in rows) + "\n"
    return _write(tmp_path, name, text)


def test_the_real_export_parses_and_rd1_against_itself_gains_nothing(tmp_path):
    rows = F.load([_write(tmp_path, "rows_2026-10-04.csv.gz", REAL)],
                  inc=F.INCUMBENT, ch=F.INCUMBENT)
    assert len(rows) == 5
    b = F.block(rows)
    assert b["gain_rd1_minus_rd3"]["mean"] == 0 and b["inc"]["logloss"] == b["ch"]["logloss"]
    # houston: label 33 (the station's whole-degree METAR maximum) lies inside
    # 31.803..35.591; the settlement's 31.67 does not - the check uses the label
    assert b["inc"]["cover80_n"] == 5
    assert b["inc"]["cover80"] == 0.6 and b["inc"]["cover80_vs_settlement"] == 0.4
    assert b["market_information_only"]["n"] == 5


def test_no_comparison_before_twenty_dates(tmp_path):
    rows = _rows(19, lambda r, c: 0.3, lambda r, c: 0.6)
    rep = F.score(F.load([_to_file(tmp_path, rows)]))
    assert rep["verdict"].startswith("no look yet: 19 of 20")
    assert "look_1" not in rep and rep["counts"]["settled_dates"] == 19
    assert not any(k in str(rep) for k in ("logloss", "gain_rd1_minus_rd3", "top1"))


def test_a_clearly_better_challenger_is_accepted_and_the_look_is_a_fixed_date_set(tmp_path):
    better = _rows(20, lambda r, c: 0.25 + 0.1 * r.random(), lambda r, c: 0.45 + 0.1 * r.random())
    rep = F.score(F.load([_to_file(tmp_path, better)]))
    assert rep["verdict"] == "accepted" and rep["look_1"]["final"] and all(rep["look_1"]["checks"].values())
    # more dates afterwards, whatever they hold, cannot change look 1
    later = better + _rows(10, lambda r, c: 0.9, lambda r, c: 0.01, start=dt.date(2026, 10, 23), seed=9)
    rep2 = F.score(F.load([_to_file(tmp_path, later, "rows_2026-12-31.csv.gz")]))
    assert rep2["look_1"] == rep["look_1"] and rep2["verdict"] == "accepted" and "look_2" not in rep2


def test_a_worse_challenger_is_rejected_at_the_first_look(tmp_path):
    worse = _rows(20, lambda r, c: 0.45 + 0.1 * r.random(), lambda r, c: 0.25 + 0.1 * r.random())
    rep = F.score(F.load([_to_file(tmp_path, worse)]))
    assert rep["verdict"] == "rejected" and rep["look_1"]["final"]


def test_a_tie_continues_to_forty_and_then_stops(tmp_path):
    same = lambda r, c: 0.3 + 0.2 * r.random()  # noqa: E731
    rep = F.score(F.load([_to_file(tmp_path, _rows(25, same, same))]))
    assert rep["look_1"]["verdict"] == "continue to look 2" and not rep["look_1"]["final"]
    assert rep["verdict"].startswith("look 1: continue; look 2 at 40")
    rep = F.score(F.load([_to_file(tmp_path, _rows(45, same, same), "rows_2027-01-31.csv.gz")]))
    assert rep["look_2"]["dates"] == sorted({r["target_date"] for r in _rows(45, same, same)})[:40]
    assert rep["verdict"] == "insufficient evidence" and rep["look_2"]["final"]
    assert rep["look_2"]["pooled"]["gain_rd1_minus_rd3"]["dates"] == 40


def test_a_pooled_gain_with_one_checkpoint_worse_is_not_accepted_and_final(tmp_path):
    good = lambda r, c: 0.5 + 0.05 * r.random()  # noqa: E731
    rows = _rows(20, lambda r, c: 0.3 + 0.05 * r.random(), good,
                 cp_override={"postpeak_1h": lambda r, c: 0.05 + 0.02 * r.random()})
    # postpeak alone: rd1 0.3 vs rd3 0.05 - its interval is wholly below 0
    rep = F.score(F.load([_to_file(tmp_path, rows)]))
    assert rep["look_1"]["checks"]["gain_ci90_above_0"]
    assert not rep["look_1"]["checks"]["no_checkpoint_ci90_below_0"]
    assert rep["verdict"] == "not accepted" and rep["look_1"]["final"] and "look_2" not in rep


def test_coverage_outside_the_band_fails_check_four(tmp_path):
    rows = _rows(20, lambda r, c: 0.25 + 0.1 * r.random(), lambda r, c: 0.45 + 0.1 * r.random(), half=3.0)
    rep = F.score(F.load([_to_file(tmp_path, rows)]))
    assert rep["look_1"]["pooled"]["ch"]["cover80"] > 0.88
    assert not rep["look_1"]["checks"]["cover80_in_0.75_0.88"] and rep["verdict"] == "not accepted"


def test_a_winner_disagreeing_with_the_banked_ladder_stops_everything(tmp_path):
    rows = _rows(20, lambda r, c: 0.3, lambda r, c: 0.6)
    rows[7]["fb_agrees"] = "0"
    rep = F.score(F.load([_to_file(tmp_path, rows)]))
    assert rep["verdict"].startswith("stopped") and "look_1" not in rep


def test_pairs_without_one_winner_are_left_out_and_counted(tmp_path):
    rows = _rows(20, lambda r, c: 0.25 + 0.1 * r.random(), lambda r, c: 0.45 + 0.1 * r.random())
    for r in rows[:3]:
        r.update(n_winners="0", fb_agrees="", p_inc="", p_ch="", hit_inc="", hit_ch="")
    rep = F.score(F.load([_to_file(tmp_path, rows)]))
    assert rep["counts"]["left_out_not_one_winner"] == 3 and rep["counts"]["scored"] == len(rows) - 3


def test_the_export_is_refused_when_it_is_not_what_the_rule_scores(tmp_path):
    rows = _rows(1, lambda r, c: 0.3, lambda r, c: 0.6)
    bad = dict(rows[0], ch_version="rd3:2026-10-20:0000000000")
    with pytest.raises(ValueError, match="not rd1"):
        F.load([_to_file(tmp_path, [bad])])
    short = ",".join(F.COLUMNS) + "\nc0,C,2026-10-03\n"
    with pytest.raises(ValueError, match="not every column"):
        F.load([_write(tmp_path, "rows_x.csv.gz", short)])
    a = _to_file(tmp_path, rows, "rows_2026-11-30_a.csv.gz")
    b = _to_file(tmp_path, rows, "rows_2026-11-30_b.csv.gz")
    assert len(F.load([a, b])) == len(rows), "a pair exported in two parts counts once"
    changed = [dict(rows[0], p_ch="0.99")] + rows[1:]
    c = _to_file(tmp_path, changed, "rows_2026-11-30_c.csv.gz")
    with pytest.raises(ValueError, match="exported twice"):
        F.load([a, c])


def test_the_export_statement_is_bounded_and_drops_no_field():
    sql = F.export_sql("2026-10-24", date_from="2026-10-03", date_to="2026-10-12")
    assert f"date '2026-10-24' - {F.SETTLE_DAYS}" in sql
    assert "date '2026-10-03'" in sql and "date '2026-10-12'" in sql
    assert f"model_version = '{F.INCUMBENT}'" in sql and f"model_version = '{F.CHALLENGER}'" in sql
    assert "'" + ",".join(F.COLUMNS) + "'" in sql
    assert "now()" not in sql and "current_date" not in sql, "bounds are literals"
    # every concat_ws argument after the always-present keys is coalesced
    body = sql[sql.index("concat_ws(',',"):sql.index("chr(10) order by")]
    assert body.count("coalesce(") >= 14
    assert "from public.v_checkpoint_outcome where ladder_has_winner" in sql
    assert "public.derived_city_day_features" in sql and "observed_max_c" in sql


def test_the_rule_constants_are_the_preregistered_ones():
    assert F.LOOKS == (20, 40) and F.FLOOR == 1e-6 and F.MARKET_SYNC_S == 900
    assert F.INCUMBENT == "rd1:2026-09-25:f5372ebb05" and F.CHALLENGER == "rd3:2026-09-25:555719d4a1"
    prereg = open(os.path.join(ROOT, "docs", "CHALLENGER_C_PREREG.md")).read()
    assert F.INCUMBENT in prereg and F.CHALLENGER in prereg
    assert "first look at 20 settled dates" in prereg.lower() or "the first at 20 settled dates" in prereg
