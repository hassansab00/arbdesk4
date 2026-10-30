"""Describe existing score extracts without inventing receipt/probability proof."""
import collections
import datetime as dt
from zoneinfo import ZoneInfo

from .core import digest, finite, phase, stamp


def review_instants(reviews, timezones):
    peaks, reasons, seen = {}, collections.Counter(), set()
    for r in reviews:
        city = r.get('city', r.get('city_key'))
        day = r.get('day', r.get('target_date'))
        if (city, day) in seen:
            raise ValueError('duplicate/conflicting peak review')
        seen.add((city, day))
        value = r.get('peak', r.get('peak_local_hour'))
        if value is None:
            reasons['missing_review_peak'] += 1
            continue
        try:
            hour = float(value)
            if not 0 <= hour < 24:
                raise ValueError('invalid peak hour')
            naive = dt.datetime.combine(dt.date.fromisoformat(day), dt.time()) + dt.timedelta(hours=hour)
            zone = ZoneInfo(timezones[city])
            a, b = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
            if a.utcoffset() != b.utcoffset():
                reasons['ambiguous_or_nonexistent_local_peak'] += 1
                continue
            key = city, day
            if key in peaks:
                raise ValueError('duplicate peak review')
            peaks[key] = a.astimezone(dt.timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons['invalid_or_unusable_peak_review'] += 1
    return peaks, dict(reasons)


def audit_scores(rows, cities, reviews, evaluation_as_of, include_cities=None):
    """Accept existing fact_checkpoint_outcome rows or the compact audit export.

    Model top probabilities and winner identities are NOT reconstructed from
    hit flags or Brier scores. This mode can never approve a candidate.
    """
    asof = stamp(evaluation_as_of)
    zones = {r.get('city_key', r.get('city')): r['timezone'] for r in cities}
    peaks, peak_notes = review_instants(reviews, zones)
    seen, originals, skipped = {}, {}, collections.Counter()
    for source in rows:
        r = dict(id=source.get('checkpoint_id', source.get('id')),
                 city=source.get('city_key', source.get('city')),
                 day=source.get('target_date', source.get('day')),
                 cp=source.get('checkpoint', source.get('cp')),
                 at=source.get('decided_at', source.get('at')),
                 hit=source.get('hit', source.get('h')),
                 market_hit=source.get('market_hit', source.get('mh')),
                 settled=source.get('settled_at', source.get('settled')),
                 brier=source.get('brier_model', source.get('b')),
                 market_brier=source.get('brier_market', source.get('mb')),
                 market_complete=source.get('market_complete', source.get('mc')))
        if include_cities is not None and r['city'] not in include_cities:
            skipped['outside_declared_city_cohort'] += 1
            continue
        if not r['id'] or r['hit'] not in (True, False) or type(r['hit']) is not bool:
            raise ValueError('score extract needs an ID and boolean hit flag')
        if r['market_hit'] is not None and type(r['market_hit']) is not bool:
            raise ValueError('market hit must be boolean or null')
        if r['id'] in seen:
            raise ValueError('duplicate score ID')
        seen[r['id']] = r
        at = stamp(r['at']); dt.date.fromisoformat(r['day'])
        key = r['city'], r['day'], r['cp']
        if key not in originals or (at, r['id']) < (stamp(originals[key]['at']), originals[key]['id']):
            originals[key] = r
    scored, late_exclusions = [], collections.Counter()
    for r in originals.values():
        if not r['settled'] or not stamp(r['at']) < stamp(r['settled']) <= asof:
            late_exclusions['missing_future_or_pre_call_settlement'] += 1
            continue
        high = peaks.get((r['city'], r['day']))
        lead = (high - stamp(r['at'])).total_seconds() / 3600 if high else None
        scored.append(dict(r, lead_hours=lead, actual_lead_group=phase(lead)))

    def aggregate(a):
        paired = [r for r in a if r['market_hit'] is not None]
        bm = [float(r['brier']) for r in a if r['brier'] is not None]
        if any(not finite(x) or x < 0 for x in bm):
            raise ValueError('invalid Brier score')
        return dict(calls=len(a), target_dates=len({r['day'] for r in a}),
                    city_dates=len({(r['city'], r['day']) for r in a}),
                    model_hits=sum(r['hit'] for r in a),
                    model_hit_rate=sum(r['hit'] for r in a) / len(a) if a else None,
                    paired_calls=len(paired), market_hits=sum(r['market_hit'] for r in paired),
                    model_hits_on_paired=sum(r['hit'] for r in paired),
                    market_hit_rate=sum(r['market_hit'] for r in paired) / len(paired) if paired else None,
                    mean_stored_model_brier=sum(bm) / len(bm) if bm else None,
                    reviewed_calls=sum(r['lead_hours'] is not None for r in a),
                    calls_at_or_after_first_max=sum(r['lead_hours'] is not None and r['lead_hours'] <= 0 for r in a))
    cells = collections.defaultdict(list)
    for r in scored:
        cells[(r['city'], r['cp'])].append(r)
    return dict(mode='historical_score_audit', policy='descriptive-original-score-extract-v1',
        input_sha256=digest(rows), peak_review_sha256=digest(reviews), evaluation_as_of=evaluation_as_of,
        readiness=dict(status='not_assessable', automatic_promotion=False,
            reasons=['Summary-only scores lack full ladders, independent receipts, training/artifact and registered-plan proof.']),
        overall=aggregate(scored), by_city_checkpoint=[dict(city=c, checkpoint=cp, **aggregate(a))
                                                     for (c, cp), a in sorted(cells.items())],
        by_actual_lead={p: aggregate([r for r in scored if r['actual_lead_group'] == p])
                        for p in sorted({r['actual_lead_group'] for r in scored})},
        exclusions=dict(skipped, **late_exclusions), later_score_versions_ignored=len(seen) - len(originals),
        peak_review_notes=peak_notes,
        limitations=['First SCORED original is used; an earlier unscored prediction cannot be ruled out from this extract.',
                     'Stored score columns are described, not independently recomputed without probability ladders.',
                     'Repeated horizons are correlated; call count is not independent weather-day count.',
                     'Review peaks can include observations ineligible under a market rule; no settlement-eligibility certification.',
                     'Peak-valid time is not necessarily report availability or official market settlement.',
                     'Unknown peak times remain unknown. Actual-high groups use hindsight for diagnostics only.'])
