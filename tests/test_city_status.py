"""Each city's daily status (WXPredict build, wave F, F.2; Hassan, 6 Oct).

The Watch rule comes from tools/focus/status_conditions.py, run on data before
the sealed test only, by a rule fixed in its docstring before any number was
computed. The migration records its thresholds as version status-v1 with the
study's sha256. These tests hold the three together: the committed JSON, what
the study produces now, and what the migration installs. And they hold that a
status, like focus membership, never reaches a price.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDY = os.path.join(ROOT, "tools", "focus", "status_conditions.py")
JSON_PATH = os.path.join(ROOT, "data", "eval", "focus", "status_conditions_2026-10-06.json")
MIGRATION = os.path.join(ROOT, "supabase", "migrations", "20261006180000_each_city_has_a_daily_status.sql")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _study():
    return json.loads(_read(JSON_PATH))


def test_the_migration_records_the_study_it_comes_from():
    mig = _read(MIGRATION)
    digest = hashlib.sha256(open(JSON_PATH, "rb").read()).hexdigest()
    assert f"'{digest}'" in mig, "method_sha256 is the committed study's sha256"
    assert "'data/eval/focus/status_conditions_2026-10-06.json'" in mig


def test_the_watch_threshold_is_the_adopted_signal_and_only_that():
    s = _study()
    assert s["adopted"] == ["S1_disagreement_c"]
    thr = s["signals"]["S1_disagreement_c"]["threshold"]
    mig = _read(MIGRATION)
    assert f"'disagreement_c', {thr}" in mig
    for name in ("S2_cloud_uncertainty", "S3_pressure_change_hpa", "S4_wind_change_kmh"):
        assert s["signals"][name]["adopted"] is False
    assert "'tested_not_adopted', jsonb_build_array('cloud_uncertainty', 'pressure_change', 'wind_change')" in mig
    # the rule: lower on Watch days, interval wholly below zero
    a = s["signals"]["S1_disagreement_c"]
    assert a["hit_rate_watch"] < a["hit_rate_other"] and a["interval"][1] < 0
    assert a["interval_level"] == 1 - 0.05 / 4


def test_the_study_reads_nothing_from_the_sealed_test():
    s = _study()
    assert s["cutoff"] == "2026-09-01"
    assert s["last_day_used"] < s["cutoff"]
    src = _read(STUDY)
    for blind in ("rd3", "da_floor", "sd_corr", "fec_v1"):
        assert blind not in src, f"{blind} is a blinded challenger"


def test_the_study_reproduces_its_committed_output():
    out = subprocess.run([sys.executable, STUDY, "--no-write"], capture_output=True, text=True,
                         cwd=ROOT, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout == _read(JSON_PATH), "the study's output and the committed JSON differ"


def test_status_and_membership_never_reach_a_price():
    """The status says how far a card's numbers can be relied on; it does not
    change them. Nothing under scripts/ reads it (F.7 will add the paper
    strategies' read deliberately, with its own test), and the view does not
    read the focus set."""
    mig = _read(MIGRATION)
    view = mig[mig.index("create or replace view public.v_city_status"):]
    view = view[:view.index("$v$;")]                # the definition, not its comment
    assert "focus_sets" not in view
    hits = []
    for dirpath, _, files in os.walk(os.path.join(ROOT, "scripts")):
        for f in files:
            if f.endswith(".py"):
                text = _read(os.path.join(dirpath, f))
                if re.search(r"\b(v_city_status|city_status_rules)\b", text):
                    hits.append(f)
    hits = [f for f in hits if f != "mirror_to_repo.py"]   # it lists the table as not mirrored
    assert hits == [], f"read by {hits}"


def test_the_page_reads_one_selection_for_every_panel():
    page = _read(os.path.join(ROOT, "web", "app", "predictive", "page.tsx"))
    assert 'supabase.from("focus_sets")' in page
    assert 'supabase.from("v_city_status")' in page
    assert "selectedKeys(scope, activeKeys" in page
    for prop in ("<CityCards onPick={setCity} cities={selected}", "cities={selected} active={activeKeys}",
                 "<PredictionLineup cities={selected} />"):
        assert prop in page
    assert '.in("city_key", selected)' in page and '.in("city_key", activeKeys)' not in page
    assert "prob_on_winner" in page
