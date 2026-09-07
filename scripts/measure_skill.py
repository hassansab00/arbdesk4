"""
AD4 Phase 1 - forecast skill measurement.

THIS IS THE JOB THAT DECIDES THE ARCHITECTURE.

Joins what each model predicted at lead time L against what the station actually
recorded, and reports MAE per city per lead time, plus the station bias term.

Read the output like this:
  MAE at lead 1 under one band  -> concentration strategies are viable
  MAE at lead 1 over two bands  -> they are not; intraday and arbitrage carry it
  Mean signed error != 0        -> that is the station bias, and it is correctable

Band widths are 1 deg C for Celsius cities and 2 deg F (~1.11 C) for US cities,
so "bands of error" is computed per city in its own unit.
"""
import sys, json, datetime as dt
from collections import defaultdict
from common import rest, upsert, log_run, get_cities, city_local_date

def daily_max_observed(city_key, start, end, timezone):
    """Highest observed temp per LOCAL day, from our own archive.

    The day is the city's own calendar day, because that is the day
    weather_forecasts.for_date holds and the day the market settles on. It
    used to be `valid_at[:10]` - a slice of the UTC string - which for Tokyo
    put every reading after 15:00 local into the next day's bucket and for
    New York put every reading before 20:00 local into the previous one. The
    resulting error went straight into mae_c, and mae_c sets sigma.
    """
    out, offset, page = defaultdict(lambda: None), 0, 10000
    while True:
        rows = rest("weather_observations", [
            ("select", "valid_at,temp_c"),
            ("city_key", f"eq.{city_key}"),
            # PostgREST range syntax: repeat the column key for each bound
            # of the range (documented pattern), rather than an ad-hoc
            # "and" combinator wrapping a single condition.
            ("valid_at", f"gte.{start.isoformat()}"),
            ("valid_at", f"lte.{end.isoformat()}"),
            ("order", "valid_at.asc"),
            ("limit", str(page)), ("offset", str(offset)),
        ])
        if not rows:
            break
        for r in rows:
            if r.get("temp_c") is None:
                continue
            d = city_local_date(r["valid_at"], timezone)
            cur = out[d]
            if cur is None or r["temp_c"] > cur:
                out[d] = r["temp_c"]
        if len(rows) < page:
            break
        offset += page
    return {k: v for k, v in out.items() if v is not None}

MIN_SAMPLE = 10          # below this a mean absolute error is not a measurement

def skill_stats(errs, band_c):
    """The skill columns for one bag of signed errors, or None if too thin.

    Extracted so the pooled number and the per-model number are computed by
    the same arithmetic rather than by two copies of it that can drift.
    """
    n = len(errs)
    if n < MIN_SAMPLE:
        return None
    mae = sum(abs(e) for e in errs) / n
    bias = sum(errs) / n
    srt = sorted(abs(e) for e in errs)
    p90 = srt[int(0.9 * (n - 1))]
    within1 = sum(1 for e in errs if abs(e) <= band_c) / n
    return {
        "n_days": n,
        "mae_c": round(mae, 3),
        "bias_c": round(bias, 3),
        "p90_abs_err_c": round(p90, 3),
        "mae_bands": round(mae / band_c, 3),
        "pct_within_one_band": round(within1, 4),
        "band_width_c": round(band_c, 3),
    }


def one_row_per_run_key(rows):
    """Keep the NEWEST run for each (model, for_date, lead).

    The intraday job re-fetches the same date every few hours, so one model
    can hold four rows for one (date, lead) - 52% of open_meteo_forecast's
    rows are re-runs of a key it already had. Scoring all of them counts the
    same forecast day several times and silently weights the days the job
    happened to run most often. The historical archive has no duplicates at
    all, so this changes today's numbers by nothing; it stops them drifting
    as the intraday job keeps writing.
    """
    best = {}
    for r in rows:
        k = (r.get("model"), r.get("for_date"), r.get("lead_days"))
        cur = best.get(k)
        if cur is None or (r.get("run_at") or "") > (cur.get("run_at") or ""):
            best[k] = r
    return list(best.values())


def forecasts(city_key, start, end):
    rows, offset, page = [], 0, 10000
    while True:
        r = rest("weather_forecasts", [
            ("select", "for_date,lead_days,forecast_max_c,model,run_at"),
            ("city_key", f"eq.{city_key}"),
            ("for_date", f"gte.{start.isoformat()}"),
            ("for_date", f"lte.{end.isoformat()}"),
            ("order", "for_date.asc"),
            ("limit", str(page)), ("offset", str(offset)),
        ])
        if not r:
            break
        rows.extend(r)
        if len(r) < page:
            break
        offset += page
    return rows

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()

    cities = get_cities(require_coords=False)
    unit_of = {c["city_key"]: (c.get("unit") or "C") for c in cities}
    print(f"measuring {len(cities)} cities, {start} -> {end}\n")

    out_rows, model_rows, summary = [], [], []
    for c in cities:
        ck = c["city_key"]
        obs = daily_max_observed(ck, start, end, c.get("timezone"))
        if not obs:
            continue
        fc = forecasts(ck, start, end)
        if not fc:
            continue

        # band width expressed in degrees C
        band_c = 1.0 if unit_of[ck] == "C" else 2.0 * 5.0 / 9.0

        fc = one_row_per_run_key(fc)

        # Two grains, one pass. by_lead is the number the desk prices with;
        # by_model_lead is the breakdown behind it, because error is generated
        # per model - NWS and Open-Meteo miss in different directions, and an
        # average over whichever models happened to write rows is not a
        # property of either of them.
        by_lead = defaultdict(list)
        by_model_lead = defaultdict(list)
        for f in fc:
            a = obs.get(f["for_date"])
            if a is None or f.get("forecast_max_c") is None:
                continue
            err = f["forecast_max_c"] - a                             # signed error
            by_lead[f["lead_days"]].append(err)
            if f.get("model"):
                by_model_lead[(f["model"], f["lead_days"])].append(err)

        for lead, errs in sorted(by_lead.items()):
            stats = skill_stats(errs, band_c)
            if stats is None:
                continue
            out_rows.append({"city_key": ck, "computed_at": computed_at,
                             "lead_days": lead, **stats})
            if lead == 1:
                summary.append((ck, stats["n_days"], stats["mae_c"],
                                stats["bias_c"], stats["mae_bands"],
                                stats["pct_within_one_band"]))

        for (model, lead), errs in sorted(by_model_lead.items()):
            stats = skill_stats(errs, band_c)
            if stats is None:
                continue
            model_rows.append({"city_key": ck, "model": model,
                               "computed_at": computed_at, "lead_days": lead,
                               **stats})

    if model_rows:
        upsert("derived_forecast_skill_model", model_rows,
               "city_key,model,computed_at,lead_days")
        by_model = defaultdict(int)
        for r in model_rows:
            by_model[r["model"]] += 1
        print("PER MODEL rows written: " +
              ", ".join(f"{m}={n}" for m, n in sorted(by_model.items())) + "\n")

    if out_rows:
        upsert("derived_forecast_skill", out_rows, "city_key,computed_at,lead_days")

    print(f"{'CITY':<16}{'n':>5}{'MAE C':>8}{'bias':>8}{'bands':>7}{'within1':>9}")
    for ck, n, mae, bias, mb, w1 in sorted(summary, key=lambda x: x[2]):
        print(f"{ck[:15]:<16}{n:>5}{mae:>8.2f}{bias:>+8.2f}{mb:>7.2f}{w1:>9.1%}")

    if summary:
        maes = sorted(s[2] for s in summary)
        bands = sorted(s[4] for s in summary)
        print(f"\nLEAD 1 across {len(summary)} cities:")
        print(f"  median MAE      {maes[len(maes)//2]:.2f} C")
        print(f"  median in bands {bands[len(bands)//2]:.2f}")
        print(f"  cities under 1 band: {sum(1 for b in bands if b < 1)}/{len(bands)}")
        print("\n  -> under 1 band favours concentration strategies")
        print("  -> over 2 bands favours intraday and arbitrage instead")

    # Mirrors the verify query in AD4 spec Task 1: aggregate stats for the
    # rows just written under this run's single computed_at.
    if out_rows:
        n_rows = len(out_rows)
        n_cities = len({r["city_key"] for r in out_rows})
        worst_sample = min(r["n_days"] for r in out_rows)
        avg_mae = sum(r["mae_c"] for r in out_rows) / n_rows
        avg_bands = sum(r["mae_bands"] for r in out_rows) / n_rows
        print(f"\nVERIFY  rows={n_rows} cities={n_cities} worst_sample={worst_sample} "
              f"avg_mae={avg_mae:.2f} avg_bands={avg_bands:.2f}")
        thin = sorted({r["city_key"] for r in out_rows if r["n_days"] < 200})
        if thin:
            print(f"  UNDER-200-DAYS (untrusted, downgrade confidence in Task 4): {thin}")
        else:
            print("  all cities >= 200 days sample")

    log_run("measure_skill", "ok", len(out_rows) + len(model_rows),
            {"cities": len(summary), "window_days": days,
             "pooled_rows": len(out_rows), "model_rows": len(model_rows),
             "models": sorted({r["model"] for r in model_rows})})

if __name__ == "__main__":
    main()
