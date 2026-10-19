"""A controlled early-forecast comparison, constructed before the call freezes.

The alternative changes only the temperature centre to the saved raw input.
It is not an independently fitted model, and never replaces the served call.
No reconstruction of an old alternative is admitted as forward evidence.
"""
import math

POLICY = 'early-raw-centre-comparison-v1'


def freeze(rows, bands, unit):
    """Return paired distributions only if the served forecast reproduces.

    All inputs are already in the pricing worker's memory. No extra source
    request, fit, database write or modification of the priced rows occurs.
    """
    if not rows or not bands or unit not in ('C', 'F'):
        return None
    import probability_engine as pe
    head = rows[0]
    if head.get('observed_floor_c') is not None:
        return None
    try:
        raw, centre, sigma = (float(head[k]) for k in ('forecast_max_c', 'centre_c', 'sigma_c'))
        if not all(math.isfinite(v) for v in (raw, centre, sigma)) or sigma <= 0:
            return None
        served = {str(r['band_id']): float(r['calibrated_prob']) for r in rows}
        if (len(served) != len(rows) or len(bands) != len(rows)
                or set(served) != {str(b['band_id']) for b in bands}
                or any(any(r.get(k) != head.get(k) for k in ('forecast_max_c','centre_c','sigma_c','input_forecast_run','forecast_version')) for r in rows)):
            return None

        def distribution(value):
            q = {str(k): round(pe.clamp_prob(pe._calibrate(p)), 6)
                 for k, p in pe.compute_band_probabilities(value, sigma, unit, bands)}
            total = sum(q.values())
            return {k: round(pe.clamp_prob(p/total), 6) for k, p in q.items()}

        reproduced, alternative = distribution(centre), distribution(raw)
        if (any(abs(reproduced[k]-served[k]) > 1e-4 for k in served)
                or abs(sum(served.values())-1) > 1e-4
                or abs(sum(alternative.values())-1) > 1e-4):
            return None
        return dict(policy=POLICY, raw_centre_c=raw, corrected_centre_c=centre,
                    sigma_c=sigma, unit=unit, raw_centre_probs=alternative,
                    corrected_probs=served,
                    input_forecast_run=head.get('input_forecast_run'),
                    forecast_version=head.get('forecast_version'),
                    interpretation='Only the centre differs; width and probability calibration are shared. Diagnostic, not a promoted predictor.')
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def score(packet, served, winner):
    """A saved pair must name this policy and reproduce the exact frozen call."""
    from wx_city_calibration import metrics, transform, IDENTITY
    try:
        if (packet['policy'] != POLICY or packet['corrected_probs'] != served
                or not winner):
            return None
        q = packet['raw_centre_probs']
        transform(q, list(served), IDENTITY)  # validates the exact ladder
        transform(served, list(served), IDENTITY)
        return dict(raw_centre=metrics(q, winner), corrected=metrics(served, winner))
    except (KeyError, TypeError, ValueError):
        return None


def summarize(rows):
    pairs = [r for r in rows if r.get('verified') and r.get('early_comparison')]
    return dict(policy=POLICY, calls=len(pairs), dates=len({r['target_date'] for r in pairs}),
                raw_centre_hits=sum(r['early_comparison']['raw_centre']['hit'] for r in pairs),
                corrected_hits=sum(r['early_comparison']['corrected']['hit'] for r in pairs),
                correction_helped=sum(r['early_comparison']['corrected']['hit'] > r['early_comparison']['raw_centre']['hit'] for r in pairs),
                correction_hurt=sum(r['early_comparison']['corrected']['hit'] < r['early_comparison']['raw_centre']['hit'] for r in pairs),
                mean_log_loss_gain=sum(r['early_comparison']['raw_centre']['log_loss']-r['early_comparison']['corrected']['log_loss'] for r in pairs)/len(pairs) if pairs else None,
                interpretation='Forward frozen evening pairs only. Positive log-loss gain favours the correction. No automatic model promotion from this diagnostic.')
