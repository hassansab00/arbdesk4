"""Recovery must still bank data, without running unrelated model enrichment."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / '.github/workflows/archive_observations.yml').read_text())
STEPS = {s['name']: s for s in DOC['jobs']['archive']['steps'] if 'name' in s}
ENRICHMENT = {
    'Append the honest training record', 'Append the ensemble record',
    'Append the station reports and days (WXPredict)',
    'Append settled venue labels and prices (WXPredict)',
    'Audit the candidate against serving on settled first calls',
    'Why each day hit or missed (WXPredict)', 'Score the original frozen city calls',
}


def test_recovery_is_opt_in_and_adds_no_schedule():
    trigger = DOC.get('on', DOC.get(True))
    assert set(trigger) == {'workflow_dispatch'}
    assert trigger['workflow_dispatch']['inputs']['archive_only']['default'] is False


@pytest.mark.parametrize('value,expected', [(None, True), ('false', True), ('true', False)])
@pytest.mark.parametrize('cancelled', [False, True])
def test_scheduled_and_normal_manual_runs_keep_enrichment(value, expected, cancelled):
    for name in ENRICHMENT:
        condition = STEPS[name]['if']
        assert "github.event.inputs.archive_only != 'true'" in condition
        assert ('always()' in condition or '!cancelled()' in condition)
        # Evaluate this workflow's small condition against missing clock inputs,
        # explicit recovery inputs and cancellation; no status success() gate.
        expression = condition.removeprefix('${{').removesuffix('}}').strip()
        expression = expression.replace("github.event.inputs.archive_only", repr(value))
        expression = expression.replace('!cancelled()', repr(not cancelled))
        expression = expression.replace('always()', 'True').replace('&&', 'and')
        result = eval(expression, {'__builtins__': {}}, {})
        assert result is (expected and (not cancelled if 'Score the original' in name else True))


def test_recovery_keeps_every_durability_and_retirement_gate():
    for name, step in STEPS.items():
        if name not in ENRICHMENT:
            assert 'archive_only' not in step.get('if', '')
    prune = STEPS['Prune, now that the rows are in the repository']
    assert "steps.commit.outputs.ok == '1'" in prune['if']
    assert "github.event.inputs.commit == 'true'" in prune['if']
    assert '--prune-only' in prune['run']
    assert '--export-only' in STEPS['Export cold rows into the repository']['run']
    assert STEPS['Mirror the proprietary record into the repository']['if'] == 'always()'
    assert STEPS['Commit the mirror']['if'] == 'always()'


def test_failed_maintenance_never_promises_a_later_shrink(monkeypatch, capsys):
    import archive_observations as archive

    def unavailable(*args, **kwargs):
        raise TimeoutError('maintenance unavailable')

    monkeypatch.setattr(archive, '_rpc', unavailable)
    assert archive.request_reclaim('book_snapshots')['ok'] is False
    output = capsys.readouterr().err
    assert 'headroom remains unverified' in output
    assert 'schedule will return the space' not in output
