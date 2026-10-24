"""Research feature adapter for recorded current context; no fit or promotion."""
from wxpredict import current_forecasts, features as contract

RECIPE = "remaining-current-context-v1"


def features(df):
    import numpy as np
    import pandas as pd
    from wxpredict import remaining, decision_inputs
    # Explicit source columns prevent provenance, checkpoint IDs, labels from
    # other recipes and future dataset additions becoming model predictors.
    base = df[list(contract.COLUMNS)].copy()
    identifiers = {'event_id','source','city_key','station','date','unit','n_bands',
                   'bands_lo','bands_hi','decision_utc','decision_local','fc_daily_source'}
    weather = base[[k for k in contract.COLUMNS if k not in identifiers | decision_inputs.LABELS
                    and not k.startswith('mkt_')]].copy()
    weather['city'] = base.city_key.astype('category')
    weather['is_f'] = (base.unit=='F').astype(int)
    out = remaining.features(base, weather)
    for name in current_forecasts.FIELDS:
        out[name] = pd.to_numeric(df[name], errors="raise")
    per_step = np.where(base.unit.values == "F", 10 / 9, 1.0)
    for suffix in ("max_c", "rest_max_c", "models_mean_c"):
        out["current_gap_" + suffix] = (out["current_" + suffix] - base.rmax_c) / per_step
    out["current_model_sd_steps"] = out.current_models_sd_c / per_step
    out["current_hour_coverage"] = out.current_hours / out.expected_hours.where(out.expected_hours > 0)
    return out
