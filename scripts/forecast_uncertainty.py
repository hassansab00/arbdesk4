"""A whole-day forecast cannot borrow the final revision's apparent skill.

The nearest measured advance lead is a conservative width PRIOR, not a new
fit or a claim of improvement. It can widen a same-day public forecast only;
it never moves its centre or touches a direct WXPredict distribution.
"""
import math

POLICY = 'same-day-advance-width-prior-v1'
MIN_ADVANCE_DATES = 10  # existing measure_skill.MIN_SAMPLE, not a fitted threshold


def guard(sigma, lead, advance_skill, mae_to_sigma):
    packet = dict(policy=POLICY, applied=False, original_sigma_c=sigma,
                  sigma_c=sigma, reason='not a same-day forecast')
    if lead != 0:
        return sigma, packet
    packet['reason'] = 'no verified advance-lead width available'
    try:
        if (not advance_skill or advance_skill.get('verified') is not True
                or int(advance_skill['lead_days']) < 1
                or int(advance_skill['n_days']) < MIN_ADVANCE_DATES):
            return sigma, packet
        width = float(advance_skill['mae_c'])*mae_to_sigma
        if not all(math.isfinite(v) and v > 0 for v in (float(sigma), width)):
            return sigma, packet
        result = max(float(sigma), width)
        packet.update(applied=result > sigma, sigma_c=result,
                      advance_lead_days=int(advance_skill['lead_days']),
                      advance_mae_c=float(advance_skill['mae_c']),
                      n_days=int(advance_skill['n_days']),
                      skill_computed_at=advance_skill.get('computed_at'),
                      reason='advance-lead error prior; final same-day revisions do not measure morning uncertainty')
        return result, packet
    except (KeyError, TypeError, ValueError, OverflowError):
        return sigma, packet
