import datetime as dt
import gzip

import archive_observations as a


def test_newly_superseded_rows_cannot_replace_a_prior_date_range():
    spec = a.TABLES['model_skill']
    day = '2026-01-01'
    first = a.archive_asset_name('model_skill', spec, day, day, gzip.compress(b'first', mtime=1))
    later = a.archive_asset_name('model_skill', spec, day, day, gzip.compress(b'later', mtime=1))
    same = a.archive_asset_name('model_skill', spec, day, day, gzip.compress(b'first', mtime=2))
    assert first != later and first == same
    assert a.archive_asset_name('trades', a.TABLES['trades'], day, day, b'') == 'trades-2026-01-01-to-2026-01-01.csv.gz'


def test_model_skill_export_uses_every_composite_key_and_date_alias(monkeypatch):
    spec = a.TABLES['model_skill']
    seen = []
    pages = iter([[dict(skill_key='["a", "gfs", 1, "2026-01-01T00:00:00.000001"]',
                       city_key='a', model='gfs', lead_days=1, computed_at='2026-01-01T00:00:00Z',
                       captured_at='2026-01-01T00:00:00Z', mae_c='1.25', evidence_scope=None)], []])
    def rest(source, params):
        seen.append((source, dict(params)))
        return next(pages)
    monkeypatch.setattr(a, 'rest', rest)
    blob, count, lo, hi = a.export_cold(spec, dt.datetime(2026,2,1,tzinfo=dt.timezone.utc))
    assert count == 1 and a.count_rows(blob) == 1
    assert all(source == 'v_prunable_model_skill' for source, _ in seen)
    assert seen[1][1]['skill_key'].startswith('gt.["a"')
    text = gzip.decompress(blob).decode()
    assert 'captured_at' in text and '1.25' in text
