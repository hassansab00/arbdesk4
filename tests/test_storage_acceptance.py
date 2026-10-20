import pytest
import storage_acceptance as storage


def receipt(size):
    return dict(db_mb=size, tier_mb=500, high_water_pct=92, target_pct=85)


@pytest.mark.parametrize('size,ok', [(400, True), (425, False), (450, False), (610.4, False)])
def test_success_requires_actual_target_headroom(size, ok):
    report = storage.assess(receipt(size))
    assert report['capacity_ok'] is ok
    assert report['headroom_mb'] == pytest.approx(500-size)


@pytest.mark.parametrize('value', [None, {}, [], [receipt(400), receipt(610)],
                                  receipt(float('nan')), receipt(float('inf')),
                                  dict(receipt(400), tier_mb=0),
                                  dict(receipt(400), target_pct=95),
                                  dict(receipt(400), db_mb=True)])
def test_unmeasured_or_invalid_capacity_cannot_be_healthy(value):
    with pytest.raises((ValueError, TypeError)):
        storage.assess(value)


def test_pressure_is_recorded_without_blocking_the_archive(monkeypatch, capsys):
    import common
    calls = []
    monkeypatch.setattr(common, 'rpc', lambda *a, **k: receipt(610.4))
    monkeypatch.setattr(common, 'log_run', lambda *args: calls.append(args))
    assert storage.main() == 0
    assert calls[0][0:2] == ('storage_acceptance', 'attention')
    assert '::warning::' in capsys.readouterr().out


def test_unreadable_capacity_is_an_error_not_a_success(monkeypatch):
    import common
    calls = []
    def fail(*a, **k):
        raise TimeoutError('unavailable')
    monkeypatch.setattr(common, 'rpc', fail)
    monkeypatch.setattr(common, 'log_run', lambda *args: calls.append(args))
    with pytest.raises(TimeoutError):
        storage.main()
    assert calls[0][1] == 'error' and calls[0][3]['capacity_ok'] is None
