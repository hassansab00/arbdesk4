"""Checkpoint outcomes are banked every night (plan v2 P4.3).

The scoring itself is a database contract (tests/database/checkpoint-outcomes.cjs,
hand-worked Brier and log loss). These hold the wiring: databank calls it after
the band and signal outcomes, a failure is reported rather than fatal to the
rest of the bank, and the table is mirrored like every other fact.
"""
import pathlib

import mirror_to_repo

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_databank_banks_the_checkpoints_after_the_bands():
    src = (ROOT / "scripts" / "databank.py").read_text()
    main = src[src.index("def main("):]
    assert 'rpc("bank_checkpoint_outcomes")' in main
    assert main.index('upsert("fact_band_outcome"') < main.index('rpc("bank_checkpoint_outcomes")')
    assert '"checkpoints": n_cp' in main


def test_the_scored_checkpoints_are_mirrored():
    spec = mirror_to_repo.TABLES["fact_checkpoint_outcome"]
    assert spec["kind"] == "append"


def test_the_function_only_adds():
    sql = (ROOT / "supabase" / "migrations" / "20260924020000_checkpoint_outcomes.sql").read_text()
    body = sql[sql.index("create or replace function public.bank_checkpoint_outcomes()"):]
    assert "on conflict (checkpoint_id) do nothing" in body
    assert "update public." not in body and "delete from" not in body
