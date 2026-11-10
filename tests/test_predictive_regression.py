import datetime as dt
import predictive_regression as watch

END = dt.date(2026, 11, 10)


def row(offset, hit, market=None, **extra):
    return dict(target_date=str(END-dt.timedelta(days=offset)), settled=True, verified=True,
                metrics=dict(hit=hit, log_loss=.5), top_prob=.7, version='original',
                market_metrics=dict(hit=market) if market is not None else None, **extra)


def test_drop_compares_disjoint_calendar_windows_and_excludes_pending_and_current_day():
    rows = [row(i, 0, 1) for i in range(1, 8)] + [row(i, 1, 0) for i in range(8, 22)]
    rows += [row(0, 1), row(-1, 1), row(22, 0)]
    rows.append(dict(target_date=str(END-dt.timedelta(days=2)), settled=False))
    r = watch.audit(rows, END)
    assert r['recent']['n'] == 7 and r['prior']['n'] == 14
    assert r['recent']['pending'] == 1 and r['hit_rate_change'] == -1
    assert r['flags'] == ['hit_rate_drop', 'market_outperforms']


def test_missing_dates_are_not_replaced_with_older_successful_dates_or_losses():
    rows = [row(1, 0)] + [row(i, 1) for i in range(8, 29)]
    r = watch.audit(rows, END)
    assert r['status'] == 'insufficient_dates' and r['hit_rate_change'] is None
    assert r['recent']['n'] == 1 and not r['flags']


def test_market_comparison_uses_identical_calls_and_missing_quote_is_unknown():
    rows = [row(i, 0, 1) for i in range(1, 6)] + [row(6, 1), row(7, 1)]
    r = watch.audit(rows, END)
    assert r['recent']['hits'] == 2 and r['recent']['market']['n'] == 5
    assert r['recent']['market']['model_hit_rate'] == 0
    assert r['recent']['market']['market_hit_rate'] == 1
    assert r['flags'] == ['market_outperforms']


def test_correction_hurt_needs_original_paired_receipts_and_proper_score_decline():
    pair = dict(raw_centre=dict(hit=1, log_loss=.1), corrected=dict(hit=0, log_loss=2))
    rows = [row(i, 0, early_comparison=pair) for i in range(1, 8)]
    r = watch.audit(rows, END)
    assert r['recent']['correction']['hurt'] == 7 and 'correction_hurt' in r['flags']
    for r in rows: r['verified'] = False
    assert not watch.audit(rows, END)['flags']


def test_versions_and_legacy_provenance_are_visible_without_granting_training_approval():
    rows = [row(1, 1), row(2, 0)]
    rows[1].update(version='replacement-recipe', verified=False)
    r = watch.audit(rows, END)
    assert r['recent']['versions'] == {'original': 1, 'replacement-recipe': 1}
    assert r['recent']['verified'] == 1 and r['status'] == 'insufficient_dates'
