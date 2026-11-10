"""Descriptive regression watch over already-loaded original scoring evidence.

Calendar windows use the city's completed local dates. Flags request an audit;
they are not significance tests, causal attribution, or model promotion gates.
"""
import datetime as dt
import math
from collections import Counter

POLICY = 'city-checkpoint-regression-v1'
PRIORS = dict(recent_days=7, prior_days=14, min_recent_dates=5,
              min_prior_dates=10, hit_drop=.20, market_gap=.15)


def _mean(values):
    values = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return sum(values)/len(values) if values else None


def window(rows, start, end):
    calls = [r for r in rows if start <= r['target_date'] < end]
    settled = [r for r in calls if r.get('settled') and r.get('metrics')]
    pairs = [r for r in settled if r.get('market_metrics')]
    corrections = [r['early_comparison'] for r in settled
                   if r.get('verified') and r.get('early_comparison')]
    return dict(start=start, end_exclusive=end, calls=len(calls), n=len(settled),
        dates=len({r['target_date'] for r in settled}),
        pending=sum(not r.get('settled') for r in calls),
        hits=sum(r['metrics']['hit'] for r in settled),
        hit_rate=_mean(r['metrics']['hit'] for r in settled),
        log_loss=_mean(r['metrics'].get('log_loss') for r in settled),
        claimed=_mean(r.get('top_prob') for r in settled),
        verified=sum(bool(r.get('verified')) for r in settled),
        versions=dict(Counter(r.get('version') or 'unknown' for r in settled)),
        market=dict(n=len(pairs), dates=len({r['target_date'] for r in pairs}),
            model_hits=sum(r['metrics']['hit'] for r in pairs),
            market_hits=sum(r['market_metrics']['hit'] for r in pairs),
            model_hit_rate=_mean(r['metrics']['hit'] for r in pairs),
            market_hit_rate=_mean(r['market_metrics']['hit'] for r in pairs)),
        correction=dict(n=len(corrections),
            helped=sum(p['corrected']['hit'] > p['raw_centre']['hit'] for p in corrections),
            hurt=sum(p['corrected']['hit'] < p['raw_centre']['hit'] for p in corrections),
            log_loss_gain=_mean(p['raw_centre']['log_loss']-p['corrected']['log_loss'] for p in corrections)))


def audit(rows, local_date):
    """One city's one checkpoint only; caller supplies first original calls.

    Different recipes are disclosed, not silently treated as a fixed-model
    experiment. A partly settled date stays visibly incomplete.
    """
    end = dt.date.fromisoformat(str(local_date))
    split = end-dt.timedelta(days=PRIORS['recent_days'])
    start = split-dt.timedelta(days=PRIORS['prior_days'])
    recent = window(rows, str(split), str(end))
    prior = window(rows, str(start), str(split))
    enough = (recent['dates'] >= PRIORS['min_recent_dates']
              and prior['dates'] >= PRIORS['min_prior_dates'])
    delta = (recent['hit_rate']-prior['hit_rate']) if enough else None
    flags = []
    if delta is not None and delta <= -PRIORS['hit_drop']+1e-12:
        flags.append('hit_rate_drop')
    market = recent['market']
    if (market['dates'] >= PRIORS['min_recent_dates']
            and market['market_hit_rate']-market['model_hit_rate'] >= PRIORS['market_gap']-1e-12):
        flags.append('market_outperforms')
    if (recent['dates'] >= PRIORS['min_recent_dates'] and recent['correction']['n'] >= PRIORS['min_recent_dates']
            and recent['correction']['hurt'] > recent['correction']['helped']
            and recent['correction']['log_loss_gain'] < 0):
        flags.append('correction_hurt')
    return dict(policy=POLICY, priors=PRIORS, recent=recent, prior=prior,
        comparable_dates_ready=enough, hit_rate_change=delta, flags=flags,
        status='attention' if flags else 'no_trigger' if enough else 'insufficient_dates',
        interpretation='Descriptive watch for this city/checkpoint; incomplete coverage and recipe changes can affect rates. Flags do not authorize model changes.')
