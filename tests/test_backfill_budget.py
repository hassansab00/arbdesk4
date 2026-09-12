import os
import pytest
import forecast_backfill_job as job

def test_chain_stops_before_allocating_more_compute(monkeypatch):
    monkeypatch.setenv('BACKFILL_DEPTH','3')
    monkeypatch.setenv('BACKFILL_BUDGET','75')
    with pytest.raises(ValueError,match='budget exhausted'):
        job.main()

def test_child_failure_propagates(monkeypatch):
    import subprocess
    monkeypatch.setenv('BACKFILL_DEPTH','0')
    monkeypatch.setenv('BACKFILL_BUDGET','75')
    def fail(*args,**kwargs):raise subprocess.CalledProcessError(2,args[0])
    monkeypatch.setattr(job.subprocess,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):job.main()
