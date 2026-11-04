import importlib.util
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('weather_learning_verification',
    Path(__file__).resolve().parents[1] / 'scripts' / 'verify_weather_learning.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def test_summary_keeps_only_counts_and_drops_fitted_values():
    raw = ('point-label eligibility: {"accepted_point_rows": 40}\n'
           '2 cell(s), 1 applied, 1 shadow\n'
           'alpha lead 1 bias +2.50 ratio 0.75 APPLIED\n')
    result = v.summarize(raw)
    assert result == dict(label_eligibility=dict(accepted_point_rows=40),
                          fit_cells=2, validated_cells=1, held_cells=1)
    assert 'alpha' not in repr(result) and '2.50' not in repr(result)


def test_verification_passes_only_dry_run_and_restores_arguments(monkeypatch, tmp_path):
    for name in ('weather_outcomes.py', 'forecast_postprocess.py'):
        (tmp_path/name).write_text('synthetic source')
    def learner():
        assert sys.argv[-1] == '--dry-run'
        print('point-label eligibility: {"accepted_point_rows": 40}')
        print('2 cell(s), 1 applied, 1 shadow')
        print('private fitted values')
        return 0
    deadlines = []
    def deadline(at):
        deadlines.append(at)
        return nullcontext()
    monkeypatch.setitem(sys.modules, 'common', SimpleNamespace(request_deadline=deadline))
    monkeypatch.setitem(sys.modules, 'forecast_postprocess', SimpleNamespace(main=learner,
        __file__=str(tmp_path/'forecast_postprocess.py')))
    original = sys.argv
    result = v.verify()
    assert sys.argv is original and len(deadlines) == 1
    assert result['read_only'] and result['fits_written'] == 0 and not result['changes_serving']
    assert 'private fitted' not in repr(result)


@pytest.mark.parametrize('kwargs', [dict(lookback=91), dict(budget_seconds=181)])
def test_bounds_are_not_optional(kwargs):
    with pytest.raises(ValueError, match='bounds'):
        v.verify(**kwargs)
