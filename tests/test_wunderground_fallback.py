"""Contract-gated fallback; synthetic source pages exercise refusals as well as capture."""
import base64
import datetime as dt
import gzip
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
import weather_outcomes as w

UTC = dt.timezone.utc
DAY = dt.date(2026, 10, 20)
AFTER = dt.datetime(2026, 10, 22, 4, tzinfo=UTC)
RULES = ('highest temperature recorded by NOAA in degrees Celsius. '
         'https://www.weather.gov/wrh/timeseries?site=oejn '
         'If NOAA data for the observation date is unavailable by 11:59 PM ET '
         'on the day following the observation date, the Weather Underground '
         'Daily Observations table will be used as the resolution source.')


def page(*, station='OEJN', date='2026-10-20', zone='Asia/Riyadh', unit='C',
         table_temp='35', chart_temp=35, timestamp=None):
    stamp = timestamp or int(dt.datetime(2026, 10, 20, 10, tzinfo=UTC).timestamp() * 1000)
    return (f'<script>const apiKey="unrelated-client-key";</script>'
            f'<airport-body data-icao-code="{station}" data-location-id="{station}:9:SA" '
            f'data-mode="daily" data-date="{date}" data-time-zone="{zone}">'
            '<h2>Daily Observations</h2><table class="observations-table">'
            '<thead><tr><th>Time</th><th>Temperature</th></tr></thead>'
            f'<tbody><tr><td>1:00 PM</td><td class="temp">{table_temp} °{unit}</td></tr></tbody></table>'
            '<script type="application/json" class="chart-data">'
            + json.dumps([{'ts': stamp, 'temp': chart_temp}])
            + '</script></airport-body>').encode()


def parse(raw=None, *, day=DAY, now=AFTER, spec=None, zone='Asia/Riyadh'):
    return w.parse_wu_daily(raw or page(), spec or w.parse_rule_spec(RULES), day, zone, now)


def test_fallback_requires_the_saved_conditional_clause_not_just_a_mention():
    assert w.parse_rule_spec(RULES).wu_fallback
    assert w.parse_rule_spec(RULES.replace(' ', '\n')).wu_fallback
    assert not w.parse_rule_spec(RULES.split('If NOAA')[0] + 'Weather Underground exists.').wu_fallback
    assert not w.parse_rule_spec(RULES.replace('11:59 PM ET', 'noon')).wu_fallback


def test_deadline_is_following_day_in_eastern_time_including_dst():
    assert w.wu_fallback_cutoff(DAY).astimezone(UTC) == dt.datetime(2026, 10, 22, 3, 59, tzinfo=UTC)
    assert w.wu_fallback_cutoff(dt.date(2026, 10, 31)).astimezone(UTC) == dt.datetime(2026, 11, 2, 4, 59, tzinfo=UTC)
    with pytest.raises(w.OutcomeError) as exc:
        parse(now=dt.datetime(2026, 10, 22, 3, 58, 59, tzinfo=UTC))
    assert exc.value.status == 'not_final'
    parse(now=dt.datetime(2026, 10, 22, 3, 59, tzinfo=UTC))


def test_daily_table_provenance_is_canonical_and_contains_no_page_configuration():
    result = parse()
    assert result.observed_max_c == Decimal('35.000000')
    assert result.observed_at == '2026-10-20T13:00:00+03:00'
    assert result.detail['source_authority'] == 'Weather Underground Daily Observations'
    assert result.detail['source_url'].endswith('/OEJN/date/2026-10-20')
    canonical = gzip.decompress(base64.b64decode(result.raw_payload['data']))
    assert b'unrelated-client-key' not in canonical and b'apiKey' not in canonical
    assert w._sha(canonical) == result.payload_sha256
    assert result.detail['source_response_sha256'] == w._sha(page())


@pytest.mark.parametrize('kwargs', [
    {'station': 'IJEDDA41'}, {'date': '2026-10-21'}, {'zone': 'UTC'},
    {'zone': 'not/a/timezone'}, {'unit': 'F'}, {'table_temp': '34'},
    {'chart_temp': True}, {'chart_temp': '35'}, {'chart_temp': float('nan')},
    {'table_temp': '90', 'chart_temp': 90},
    {'timestamp': int(dt.datetime(2026, 10, 21, 10, tzinfo=UTC).timestamp() * 1000)},
])
def test_bad_station_date_clock_unit_or_table_never_produces_evidence(kwargs):
    with pytest.raises(w.OutcomeError) as exc:
        parse(page(**kwargs))
    assert exc.value.status == 'parse_error'


@pytest.mark.parametrize('raw', [b'<html>Access denied</html>', b'<airport-body></airport-body>',
                               page() + page(), page().replace(b'Temperature', b'Dew Point')])
def test_empty_blocked_changed_or_ambiguous_pages_are_refused(raw):
    with pytest.raises(w.OutcomeError):
        parse(raw)


def test_whole_degree_precision_uses_the_contract_unit_before_rounding():
    result = parse(page(table_temp='35.4', chart_temp=35.4))
    assert result.observed_max_c == Decimal('35.000000')
    spec = w.RuleSpec('weather_gov_wrh', 'NOAA', 'OEJN', 'source', 'F', False, True)
    result = parse(page(unit='F', table_temp='95', chart_temp=95), spec=spec)
    assert result.observed_max_c == Decimal('35.000000')


def test_duplicate_timestamps_and_chart_table_clock_mismatch_are_refused():
    raw = page().replace(b'1:00 PM', b'12:00 PM')
    with pytest.raises(w.OutcomeError):
        parse(raw)
    raw = page().replace(b'"temp": 35}]', b'"temp": 35}, {"ts": 1792490400000, "temp": 35}]')
    with pytest.raises(w.OutcomeError):
        parse(raw)


def test_primary_success_never_requests_fallback(monkeypatch):
    primary = w.Outcome(Decimal('35'), None, 'primary', {}, {})
    monkeypatch.setattr(w, 'collect_one', lambda *a: primary)
    monkeypatch.setattr(w, '_get', lambda *a, **kw: pytest.fail('primary already published'))
    assert w.collect_with_fallback(None, w.parse_rule_spec(RULES), DAY, 'Asia/Riyadh', AFTER) is primary


@pytest.mark.parametrize('status', ['source_unavailable', 'parse_error', 'not_final'])
def test_local_failure_does_not_prove_primary_unavailability(monkeypatch, status):
    def primary(*args):
        raise w.OutcomeError(status, 'upstream failure')
    monkeypatch.setattr(w, 'collect_one', primary)
    monkeypatch.setattr(w, '_get', lambda *a, **kw: pytest.fail('no authorized fallback trigger'))
    with pytest.raises(w.OutcomeError) as exc:
        w.collect_with_fallback(None, w.parse_rule_spec(RULES), DAY, 'Asia/Riyadh', AFTER)
    assert exc.value.status == status


def test_publication_restriction_uses_fallback_only_after_deadline(monkeypatch):
    def primary(*args):
        raise w.OutcomeError('unsupported_source', 'named interface withholds international observations')
    monkeypatch.setattr(w, 'collect_one', primary)
    calls = []
    monkeypatch.setattr(w, '_get', lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(content=page()))
    with pytest.raises(w.OutcomeError) as exc:
        w.collect_with_fallback(None, w.parse_rule_spec(RULES), DAY, 'Asia/Riyadh', AFTER - dt.timedelta(minutes=2))
    assert exc.value.status == 'not_final' and calls == []
    result = w.collect_with_fallback(None, w.parse_rule_spec(RULES), DAY, 'Asia/Riyadh', AFTER)
    assert result.observed_max_c == Decimal('35.000000')
    assert calls[0][1]['headers'] == {'Cookie': 'wu_units=m'}
    assert calls[0][0][1].endswith('/OEJN/date/2026-10-20')
    calls.clear()
    with pytest.raises(w.OutcomeError):
        w.collect_with_fallback(None, w.parse_rule_spec(RULES.split('If NOAA')[0]), DAY, 'Asia/Riyadh', AFTER)
    assert calls == []


@pytest.mark.parametrize('valid', [True, False])
def test_run_records_fallback_under_its_actual_authority_without_prediction_writes(monkeypatch, valid):
    monkeypatch.setattr(w, 'rest_all', lambda *a, **kw: [{'city_key': 'jeddah', 'timezone': 'Asia/Riyadh'}])
    monkeypatch.setattr(w, '_existing_evidence', lambda _: {})
    monkeypatch.setattr(w, '_unparseable', lambda _: {})
    monkeypatch.setattr(w, '_load_targets', lambda *a: [dict(city_key='jeddah', market_id='j', resolution_date=DAY.isoformat(), unit='C', rules_text=RULES)])
    def primary(*args):
        raise w.OutcomeError('unsupported_source', 'named interface withholds international observations')
    monkeypatch.setattr(w, 'collect_one', primary)
    monkeypatch.setattr(w, '_get', lambda *a, **kw: SimpleNamespace(content=page() if valid else b'<html>blocked</html>'))
    writes = []
    monkeypatch.setattr(w, 'upsert', lambda table, rows, key: writes.extend((table, r) for r in rows))
    monkeypatch.setattr(w, 'log_run', lambda *a: None)
    result = w.run(now=AFTER)
    assert result['captured'] == int(valid) and result['failures'] == int(not valid)
    assert [t for t, _ in writes] == ['weather_resolution_attempts'] + (['weather_resolution_evidence'] if valid else [])
    for _, row in writes:
        assert row['source_authority'] == 'Weather Underground Daily Observations'
        assert row['source_url'].endswith('/OEJN/date/2026-10-20')
    if valid:
        assert writes[-1][1]['supersedes_evidence_id'] is None
    else:
        assert writes[0][1]['outcome_status'] == 'parse_error'
