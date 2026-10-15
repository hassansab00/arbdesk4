"""Current forecasts actually received by a decision, beside frozen inputs."""
import datetime as dt
import statistics

from wxpredict import common, live_reader as lr

FIELDS = ("current_hours", "expected_hours", "current_max_c", "current_rest_max_c",
          "current_models_n", "current_models_mean_c", "current_models_sd_c")


def context(record, event, decision, tz):
    first, last = common.local_day_bounds(dt.date.fromisoformat(event["date"]), tz)
    required = int((last - first) / 3600)
    hours = lr._by_city(record, "hourly0", decision).get(event["city"], {})
    temperatures = [(lr.features.unix_of(t), lr.features.num(v[0])) for t, v in hours.items()
                    if v and first <= lr.features.unix_of(t) < last]
    temperatures = [(t, v) for t, v in temperatures if v is not None]
    complete = len(temperatures) == required
    rest = [v for t, v in temperatures if t > decision]
    models = lr._by_city(record, "models0", decision).get(event["city"], {})
    maxima = []
    for i in range(len(lr.honest_record.MODELS)):
        values = [lr.features.num(v[i]) for stamp, v in models.items()
                  if first <= lr.features.unix_of(stamp) < last and len(v) > i]
        values = [v for v in values if v is not None]
        if len(values) == required:
            maxima.append(max(values))
    return dict(zip(FIELDS, [len(temperatures), required,
        max(v for _, v in temperatures) if complete else None,
        max(rest) if complete and rest else None,
        len(maxima), statistics.fmean(maxima) if maxima else None,
        statistics.pstdev(maxima) if len(maxima) > 1 else None]))
