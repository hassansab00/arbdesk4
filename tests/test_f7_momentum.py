"""The momentum study (WXPredict build F.7; docs/F7_MOMENTUM_2026-10-07.md).

Hassan asked for a trigger on "the market movement where the likely winner
starts climbing rapidly". tools/f7/momentum_study.py tested twelve forms of it
on pre-cutoff data by rules committed before its first run; none was adopted,
so no strategy trades on it. These tests hold the committed result, its
report and its data boundary together. The study itself takes 32 s, so CI
does not re-run it (Rule 7: Actions minutes); the committed JSON was
reproduced byte for byte twice on 7 Oct. Re-run it by hand after changing it
or season_predictability's loaders:

    python tools/f7/momentum_study.py --no-write | sha256sum
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDY = os.path.join(ROOT, "tools", "f7", "momentum_study.py")
JSON_PATH = os.path.join(ROOT, "data", "eval", "f7", "momentum_2026-10-07.json")
DOC = os.path.join(ROOT, "docs", "F7_MOMENTUM_2026-10-07.md")
sys.path.insert(0, os.path.dirname(STUDY))
sys.path.insert(0, os.path.join(ROOT, "tools", "focus"))


def _json():
    with open(JSON_PATH) as f:
        return json.load(f)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_no_rule_is_adopted_and_each_says_why():
    d = _json()
    assert d["version"] == "f7-momentum-v1"
    assert d["adopted"] == []
    assert len(d["rules"]) == 12
    for name, r in d["rules"].items():
        assert r["adopted"] is False, name
        assert r["why"] in ("too few trades or dates", "interval not wholly above zero",
                            "negative at the 75th-percentile spread"), name


def test_the_level_is_bonferroni_over_the_twelve_rules():
    import momentum_study as ms
    assert ms.N_RULES == 12 == len(_json()["rules"])
    assert abs(ms.LEVEL - (1 - 0.05 / 12)) < 1e-12
    assert _json()["level"] == ms.LEVEL


def test_the_study_reads_nothing_from_the_sealed_test():
    d = _json()
    assert d["cutoff"] == "2026-09-01"
    assert d["last_date_used"] < d["cutoff"] and d["last_date"] < d["cutoff"]
    src = _read(STUDY)
    for blind in ("rd3", "da_floor", "sd_corr", "fec_v1"):
        assert re.search(rf"\b{blind}\b", src.split('"""', 2)[2]) is None, f"{blind} is a blinded challenger"


def test_the_report_quotes_the_committed_numbers():
    d, doc = _json(), _read(DOC)

    def row(label, v):
        lo, hi = v["interval"]
        return (f"| {label} | {v['trades']:,} | {v['dates']} | {v['hit_rate'] * 100:.1f}% | "
                f"{v['mean_price'] * 100:.1f} | {v['pnl_per_share'] * 100:+.2f} | "
                f"{lo * 100:+.2f} to {hi * 100:+.2f} |")
    rows = [row("control PRE: the favourite, always", d["control"]["pre"]),
            row("control DURING", d["control"]["during"])]
    for f in "MAW":
        for ph in ("pre", "during"):
            for delta in ("0.10", "0.20"):
                rows.append(row(f"{f} {delta} {ph.upper()}", d["rules"][f"{f}_{delta}_{ph}"]))
    for r in rows:
        assert r in doc, r
    import hashlib
    digest = hashlib.sha256(open(JSON_PATH, "rb").read()).hexdigest()
    assert f"`{digest[:8]}...`" in doc
