"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtPct, fmtPp, fmtPrice, pnlColor, regimeColor } from "@/lib/format";
import { fmtTemp, fmtTempDelta, type Unit } from "@/lib/units";
import { fmtCityHour, fmtDateTime, fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import {
  STATUS_WORDS, temperatureOrder,
  type CityCard, type CurrentRow, type LadderRow, type StatusRow,
} from "@/lib/cityCards";
import { cityEvidence, MOMENTS, momentLabel, type HindsightRow } from "@/lib/focus";
import {
  bucketShares, ENSEMBLE_MODELS, ensembleUrl, memberMaxima, runTimes, summarise,
  type EnsembleJson, type EnsembleModel, type EnsembleSummary,
} from "@/lib/ensemble";

/**
 * ONE CITY, EVERYTHING THE DESK KNOWS ABOUT ITS DAY (Hassan, 10 Oct: "once i
 * click on a card for the cities, i want a popup to show all details about
 * the predictions for that particular city, including the predicted max temp
 * of the day, and a temp ensemble, and all usable data that can help me
 * decide on the trade, includes the accuracy of the city based on our
 * historical data").
 *
 * Every number from the database is one the page already read: the card's
 * ladder, forecasts and live reading (CityCards), the frozen calls, the city
 * record and the per-model scorecard (the Predictive page). Two reads are new
 * and per city: the typical peak hour (derived_weather_peak, 1 ms as anon,
 * 10 Oct) and the ensemble, asked of Open-Meteo by the browser when the popup
 * opens, so it is the newest run rather than last night's record.
 */

export interface ScoreRow {
  city_key: string; model: string; lead_days: number; n_days: number;
  mae_c: number; bias_c: number; error_sd_c: number | null; worst_c: number;
  hit_rate_pct: number | null; within_1c_pct: number | null;
}
export interface HitSummaryRow {
  city_key: string; days: number; model_hits: number; model_hit_rate: number | null;
  brier_model: number | null; brier_uniform: number | null;
  mae_c: number | null; bias_c: number | null;
  first_day: string | null; last_day: string | null;
  h2h_days: number; h2h_model_hits: number; market_hits: number; market_hit_rate: number | null;
  days_we_beat_the_market: number; verdict: string;
  centre_days: number; centre_mae_c: number | null; centre_bias_c: number | null;
}
export interface PopupCity {
  city_key: string; latitude?: number | null; longitude?: number | null; timezone?: string | null;
}

const MODEL_LABEL: Record<string, string> = {
  open_meteo_forecast: "Open-Meteo", open_meteo_best_match: "Open-Meteo best match", nws: "NWS",
};

type EnsembleState = {
  loading: boolean; error: string | null;
  summary: EnsembleSummary | null; maxima: number[]; run: string | null;
};

/** One model's ensemble for the city's day, asked when the popup opens. */
function useEnsemble(city: PopupCity, day: string, model: EnsembleModel, meta: string): EnsembleState {
  const [st, setSt] = useState<EnsembleState>({ loading: true, error: null, summary: null, maxima: [], run: null });
  useEffect(() => {
    let cancelled = false;
    if (city.latitude == null || city.longitude == null || !city.timezone) {
      setSt({ loading: false, error: "the city has no coordinates or time zone", summary: null, maxima: [], run: null });
      return;
    }
    setSt((s) => ({ ...s, loading: true, error: null }));
    (async () => {
      try {
        const r = await fetch(ensembleUrl(Number(city.latitude), Number(city.longitude), model), { cache: "no-store" });
        if (!r.ok) throw new Error(`Open-Meteo answered ${r.status}`);
        const js = (await r.json()) as EnsembleJson;
        const maxima = memberMaxima(js, city.timezone as string, day);
        // The run it came from; a refused meta file costs only the label.
        let run: string | null = null;
        try {
          const m = await fetch(`https://ensemble-api.open-meteo.com/data/${meta}/static/meta.json`, { cache: "no-store" });
          if (m.ok) run = runTimes(await m.json()).init;
        } catch { run = null; }
        if (!cancelled) setSt({ loading: false, error: null, summary: summarise(maxima), maxima, run });
      } catch (e) {
        if (!cancelled) setSt({ loading: false, error: e instanceof Error ? e.message : String(e), summary: null, maxima: [], run: null });
      }
    })();
    return () => { cancelled = true; };
  }, [city.latitude, city.longitude, city.timezone, day, model, meta]);
  return st;
}

export default function CityPopup({
  c, city, ladder, current, status, hindsight, scorecard, summary, moment, onClose,
}: {
  c: CityCard; city: PopupCity; ladder: LadderRow[]; current: CurrentRow[];
  status: StatusRow | null; hindsight: HindsightRow[]; scorecard: ScoreRow[]; summary: HitSummaryRow | null;
  moment: string; onClose: () => void;
}) {
  const u: Unit = c.unit;
  const closeRef = useRef<HTMLButtonElement>(null);
  // Escape closes it, the page behind does not scroll, and the focus starts on
  // Close; set up once, so a reload of the cards behind does not redo it.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCloseRef.current(); };
    window.addEventListener("keydown", onKey);
    closeRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, []);

  const month = Number(c.for_date.slice(5, 7));
  const peakQ = useQuery<Array<{ peak_hour_local: number | null; window_width_h: number | null; n_days: number | null }>>(
    () => supabase.from("derived_weather_peak").select("peak_hour_local,window_width_h,n_days")
      .eq("city_key", c.city_key).eq("month", month).limit(1),
    [c.city_key, month]
  );
  const peak = peakQ.data?.[0] ?? null;

  const ens = {
    ecmwf_ifs025: useEnsemble(city, c.for_date, "ecmwf_ifs025", "ecmwf_ifs025_ensemble"),
    gfs025: useEnsemble(city, c.for_date, "gfs025", "ncep_gefs025"),
  };
  // The day cannot settle below what the station has already read.
  const floorC = c.live && c.live.source_kind !== "model" ? c.live.running_max_c : null;

  // THE LADDER: one line a bucket, in temperature order. The probability is
  // the newest price's (the tick's re-price when there is one); the market's
  // prices and the edges are the pricing run's.
  const rows = useMemo(() => {
    const byBand = new Map<string, { yes?: LadderRow; no?: LadderRow }>();
    for (const r of ladder) {
      const e = byBand.get(r.band_id) ?? {};
      if (r.side === "NO") e.no = r; else e.yes = r;
      byBand.set(r.band_id, e);
    }
    const bounds = Array.from(byBand.entries()).map(([band_id, e]) => ({ ...(e.yes ?? e.no)!, band_id }));
    const repriced = c.repriced ? new Map(current.map((r) => [r.band_id, r.prob])) : null;
    return temperatureOrder(bounds).map((b) => {
      const e = byBand.get(b.band_id) ?? {};
      const label = e.yes?.band_label ?? e.no?.band_label ?? b.band_id;
      return {
        band_id: b.band_id, label,
        prob: repriced?.get(b.band_id) ?? e.yes?.model_prob ?? null,
        yesPrice: e.yes?.market_price ?? null, noPrice: e.no?.market_price ?? null,
        yesEdge: e.yes?.edge_net_pp ?? null, noEdge: e.no?.edge_net_pp ?? null,
        tradeable: [e.yes, e.no].filter((x) => x?.tradeable).map((x) => x!.side).join(" / "),
        blocked: e.yes?.block_reason ?? e.no?.block_reason ?? null,
      };
    });
  }, [ladder, current, c.repriced]);
  const bounds = useMemo(() => {
    const m = new Map<string, LadderRow>();
    for (const r of ladder) if (!m.has(r.band_id)) m.set(r.band_id, r);
    return Array.from(m.values());
  }, [ladder]);
  const shares = useMemo(() => ({
    ecmwf_ifs025: bucketShares(ens.ecmwf_ifs025.maxima, u, bounds, floorC),
    gfs025: bucketShares(ens.gfs025.maxima, u, bounds, floorC),
  }), [ens.ecmwf_ifs025.maxima, ens.gfs025.maxima, u, bounds, floorC]);

  const since = useMemo(() => new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10), []);
  const recent = useMemo(() => hindsight
    .filter((r) => r.city_key === c.city_key && r.called_when === moment && r.hit !== null)
    .sort((a, b) => (a.for_date < b.for_date ? 1 : -1)).slice(0, 10), [hindsight, c.city_key, moment]);
  const scores = useMemo(() => scorecard.filter((s) => s.city_key === c.city_key)
    .sort((a, b) => a.lead_days - b.lead_days || a.mae_c - b.mae_c), [scorecard, c.city_key]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-2 sm:p-6"
      onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={`${c.name}: the day in detail`}
        className="w-full max-w-5xl rounded border border-border bg-panel p-4 text-xs shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">{c.name}</h2>
            <div className="text-muted">
              {fmtResolutionDate(c.for_date)} · {fmtDaysAhead(c.for_date)}
              {city.timezone && <> · local time {city.timezone}</>}
            </div>
          </div>
          <button ref={closeRef} onClick={onClose} aria-label="Close"
            className="rounded border border-border px-2 py-1 text-text hover:bg-panel2">Close</button>
        </div>

        {status && (
          <div className="mt-2 rounded border border-border px-2 py-1">
            <b>{STATUS_WORDS[status.status]}</b> at {momentLabel(moment)} · {status.reason}
            {(status.reasons ?? []).length > 1 && (
              <ul className="mt-0.5 list-disc pl-5 text-muted">
                {(status.reasons ?? []).map((r) => <li key={r}>{r}</li>)}
              </ul>
            )}
          </div>
        )}

        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {/* THE CALL */}
          <section className="rounded border border-border bg-panel2 p-2">
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">The call</h3>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-muted">Platform&rsquo;s pick</dt>
              <dd className="tabular-nums">
                {c.top_band ? <><b className="text-sm">{c.top_band}</b> · {fmtPct(c.top_prob, 0)} likely · market charges {fmtPrice(c.top_yes_price)}</>
                  : <span className="text-muted">not priced yet</span>}
              </dd>
              <dt className="text-muted">Market&rsquo;s pick</dt>
              <dd className="tabular-nums">
                {c.market_band ? <>{c.market_band} at {fmtPrice(c.market_price)}{c.disagrees ? <span className="text-warn"> · differs from the platform&rsquo;s</span> : ""}</>
                  : <span className="text-muted">no price</span>}
              </dd>
              <dt className="text-muted" title="The temperature the ladder was integrated on, after every correction the engine applied (band_probabilities.centre_c). The pick above is the bucket with the most probability once the width is spread around it.">
                Predicted maximum
              </dt>
              <dd className="tabular-nums">
                {c.centre_c !== null ? <b>{fmtTemp(c.centre_c, u)}</b> : <span className="text-muted">not recorded for this price</span>}
                {c.sigma_c !== null && <span className="text-muted"> ± {fmtTempDelta(c.sigma_c, u).replace("+", "")} (1 sd)</span>}
                {c.confidence !== null && <span className="text-muted"> · confidence {fmtPct(c.confidence, 0)}</span>}
                {c.regime && <span className={`ml-1 ${regimeColor(c.regime)}`}>{c.regime}</span>}
                {c.raw_forecast_c !== null && (
                  <span className="block text-[10px] text-muted">raw public input {fmtTemp(c.raw_forecast_c, u)}
                    {c.priced_from_words && <> · priced from {c.priced_from_words}</>}</span>
                )}
              </dd>
              <dt className="text-muted">Best trade</dt>
              <dd className="tabular-nums">
                {c.best
                  ? <><b className={c.best.against_market ? "text-muted" : pnlColor(c.best.edge)}>{fmtPp(c.best.edge)}</b> {c.best.side} {c.best.band} @ {fmtPrice(c.best.price)}
                      {c.best.depth !== null && <span className="text-muted"> · depth ${Math.round(c.best.depth)}</span>}
                      {c.best.against_market && <span className="block text-bad">against the market&rsquo;s favourite - not a trade the record supports</span>}</>
                  : <span className="text-muted">no positive tradeable edge{c.blocked ? ` (mostly ${c.blocked})` : ""}</span>}
              </dd>
              <dt className="text-muted">Priced</dt>
              <dd className="text-muted">
                {c.priced_at ? fmtDateTime(c.priced_at) : "not yet"}
                {c.repriced && <> · re-priced by the tick ({c.repriced.reason === "station_max" ? "the station passed the pick" : c.repriced.checkpoint ?? "hourly"})</>}
                {c.edges_at && c.edges_at !== c.priced_at && <> · edges {fmtDateTime(c.edges_at)}</>}
              </dd>
            </dl>
            {c.stale && (
              <p className="mt-1 rounded border border-bad/60 px-2 py-1 text-bad">
                Out of date: the station has reached {fmtTemp(c.stale.max_c_now, u)} since this was priced.
              </p>
            )}
          </section>

          {/* TODAY SO FAR AND THE PEAK */}
          <section className="rounded border border-border bg-panel2 p-2">
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">The station today</h3>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-muted">So far</dt>
              <dd className="tabular-nums">
                {c.live
                  ? <>max {fmtTemp(c.live.running_max_c, u)} · now {fmtTemp(c.live.temp_c, u)}
                      {c.live.day_decided ? <b className="ml-1 text-accent">day decided</b>
                        : c.live.peak_window_state ? <span className="ml-1 text-muted">{c.live.peak_window_state}</span> : null}
                      {c.live.observed_at && <span className="block text-[10px] text-muted">reading {fmtDateTime(c.live.observed_at)} · {c.live.source_kind ?? "source unknown"}</span>}</>
                  : <span className="text-muted">no reading for this day yet{fmtDaysAhead(c.for_date) === "today" ? "" : " (the day has not started)"}</span>}
              </dd>
              <dt className="text-muted" title="The month's median local hour of the day's maximum, and its 10th-90th percentile window, from three years of this station's readings (derived_weather_peak).">
                Usual peak
              </dt>
              <dd className="tabular-nums">
                {peakQ.loading ? <span className="text-muted">reading…</span>
                  : peakQ.error ? <span className="text-bad">{peakQ.error}</span>
                  : peak?.peak_hour_local != null
                    ? <>{fmtCityHour(peak.peak_hour_local, city.timezone)}
                        {peak.window_width_h != null && <span className="text-muted"> · window {Number(peak.window_width_h).toFixed(1)} h</span>}
                        {peak.n_days != null && <span className="text-muted"> · {peak.n_days} days</span>}</>
                    : <span className="text-muted">not measured for this month</span>}
              </dd>
              <dt className="text-muted">Public forecasts</dt>
              <dd className="tabular-nums">
                {c.forecasts.length === 0 ? <span className="text-muted">none for this day</span>
                  : c.forecasts.map((f) => (
                    <span key={f.model} className="mr-2" title={f.run_at ? `run ${fmtDateTime(f.run_at)}` : undefined}>
                      {MODEL_LABEL[f.model] ?? f.model} {fmtTemp(f.max_c, u)}
                    </span>))}
                {c.forecast_spread_c !== null && (
                  <span className="block text-[10px] text-muted">spread {fmtTempDelta(c.forecast_spread_c, u).replace("+", "")}</span>
                )}
              </dd>
              <dt className="text-muted" title="The desk's own weather model. Until it is promoted it is measured beside the forecasts and does not move the pick.">
                Desk model
              </dt>
              <dd className="tabular-nums">
                {c.own?.predicted_max_c != null
                  ? <>{fmtTemp(c.own.predicted_max_c, u)} <span className="text-muted">({c.own.promotion_state ?? "state unknown"})</span></>
                  : <span className="text-muted">no prediction for this day</span>}
              </dd>
              {status?.models_span_c != null && (
                <>
                  <dt className="text-muted" title="The span of the seven forecast models' day-ahead maxima (v_city_status).">Seven models</dt>
                  <dd className="tabular-nums">{status.n_models ?? "?"} models, span {fmtTempDelta(status.models_span_c, u).replace("+", "")}</dd>
                </>
              )}
            </dl>
          </section>
        </div>

        {/* THE ENSEMBLE */}
        <section className="mt-3 rounded border border-border bg-panel2 p-2">
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Temperature ensemble</h3>
          <p className="mb-1.5 text-[11px] text-muted">
            Every member&rsquo;s maximum over the city&rsquo;s own day (00-23 local), asked of Open-Meteo just now.
            Raw model output at the model&rsquo;s grid point, not corrected to the settlement station
            {floorC !== null ? <>; the bucket shares count a member below the station&rsquo;s {fmtTemp(floorC, u)} so far as {fmtTemp(floorC, u)}</> : null}.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {ENSEMBLE_MODELS.map((m) => {
              const e = ens[m.key];
              return (
                <div key={m.key} className="rounded border border-border p-2">
                  <div className="font-semibold">{m.label}</div>
                  {e.loading ? <div className="text-muted">asking Open-Meteo…</div>
                    : e.error ? <div className="text-bad">could not read it: {e.error}</div>
                    : !e.summary ? <div className="text-muted">no whole day for {fmtResolutionDate(c.for_date)} in this run</div>
                    : (
                      <div className="tabular-nums">
                        <div>{e.summary.n} members · median <b>{fmtTemp(e.summary.p50, u)}</b> · mean {fmtTemp(e.summary.mean, u)} · sd {fmtTempDelta(e.summary.sd, u).replace("+", "")}</div>
                        <div className="text-muted">
                          10-90%: {fmtTemp(e.summary.p10, u)} to {fmtTemp(e.summary.p90, u)} · 25-75%: {fmtTemp(e.summary.p25, u)} to {fmtTemp(e.summary.p75, u)} · range {fmtTemp(e.summary.min, u)} to {fmtTemp(e.summary.max, u)}
                        </div>
                        {e.run && <div className="text-[10px] text-muted">run of {fmtDateTime(e.run)}</div>}
                      </div>
                    )}
                </div>
              );
            })}
          </div>
        </section>

        {/* THE LADDER */}
        <section className="mt-3 overflow-x-auto rounded border border-border bg-panel2 p-2">
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Every bucket</h3>
          <table className="w-full tabular-nums">
            <thead className="text-left text-[10px] uppercase tracking-wide text-muted">
              <tr>
                <th className="py-0.5 pr-2">Bucket</th>
                <th className="pr-2" title="The platform's probability for the bucket (YES).">Platform</th>
                <th className="pr-2" title="Share of ECMWF ensemble members whose day the venue would settle here.">ECMWF ens.</th>
                <th className="pr-2" title="Share of GFS ensemble members whose day the venue would settle here.">GFS ens.</th>
                <th className="pr-2">YES price</th>
                <th className="pr-2">NO price</th>
                <th className="pr-2" title="Net edge after costs, from the pricing run.">YES edge</th>
                <th className="pr-2">NO edge</th>
                <th>Tradeable</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const pick = r.label === c.top_band;
                const fav = r.label === c.market_band;
                const sh = (k: EnsembleModel) => ens[k].summary ? fmtPct(shares[k].shares.get(r.band_id) ?? 0, 0) : "—";
                return (
                  <tr key={r.band_id} className={`border-t border-border ${pick ? "bg-accent/10" : ""}`}>
                    <td className="py-0.5 pr-2">
                      {r.label}{pick && <> <span className="text-accent">pick</span></>}{fav && <> <span className="text-warn">market</span></>}
                    </td>
                    <td className="pr-2">{fmtPct(r.prob, 0)}</td>
                    <td className="pr-2">{sh("ecmwf_ifs025")}</td>
                    <td className="pr-2">{sh("gfs025")}</td>
                    <td className="pr-2">{fmtPrice(r.yesPrice)}</td>
                    <td className="pr-2">{fmtPrice(r.noPrice)}</td>
                    <td className={`pr-2 ${pnlColor(r.yesEdge)}`}>{fmtPp(r.yesEdge)}</td>
                    <td className={`pr-2 ${pnlColor(r.noEdge)}`}>{fmtPp(r.noEdge)}</td>
                    <td className="text-muted">{r.tradeable || (r.blocked ?? "")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {(shares.ecmwf_ifs025.unplaced > 0 || shares.gfs025.unplaced > 0) && (
            <p className="mt-1 text-[10px] text-muted">
              Members no bucket holds: ECMWF {fmtPct(shares.ecmwf_ifs025.unplaced, 0)}, GFS {fmtPct(shares.gfs025.unplaced, 0)}.
            </p>
          )}
        </section>

        {/* THE RECORD */}
        <section className="mt-3 rounded border border-border bg-panel2 p-2">
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">How right this city has been</h3>
          {summary ? (
            <p className="mb-1.5 tabular-nums">
              Day-ahead, {summary.days} settled days{summary.first_day ? <> ({fmtResolutionDate(summary.first_day)} to {fmtResolutionDate(summary.last_day)})</> : null}:
              {" "}the platform&rsquo;s pick won <b>{fmtPct(summary.model_hit_rate, 0)}</b>
              {summary.market_hit_rate !== null && <>, the market&rsquo;s {fmtPct(summary.market_hit_rate, 0)} on the {summary.h2h_days} days both priced</>}
              {summary.centre_mae_c !== null && <>; the predicted maximum missed by {fmtTempDelta(summary.centre_mae_c, u).replace("+", "")} on average (bias {fmtTempDelta(summary.centre_bias_c ?? 0, u)}, {summary.centre_days} days)</>}
              {summary.mae_c !== null && <>, the raw public forecast by {fmtTempDelta(summary.mae_c, u).replace("+", "")}</>}
              . <span className="text-muted">{summary.verdict}</span>
            </p>
          ) : <p className="mb-1.5 text-muted">No settled day-ahead record for this city yet.</p>}

          <table className="mb-2 w-full tabular-nums">
            <thead className="text-left text-[10px] uppercase tracking-wide text-muted">
              <tr><th className="py-0.5 pr-2">Moment, last 30 days</th><th className="pr-2">Top pick right</th>
                <th className="pr-2">Claimed</th><th className="pr-2">Mean miss</th><th>Market vs platform, same days</th></tr>
            </thead>
            <tbody>
              {MOMENTS.map((m) => {
                const e = cityEvidence(hindsight, c.city_key, m.key, since);
                return (
                  <tr key={m.key} className={`border-t border-border ${m.key === moment ? "bg-accent/10" : ""}`}>
                    <td className="py-0.5 pr-2">{m.label}</td>
                    <td className="pr-2">{e.n ? <>{e.hits}/{e.n} ({fmtPct(e.rate, 0)}){e.interval && <span className="text-muted"> · 95% {Math.round(e.interval[0] * 100)}-{Math.round(e.interval[1] * 100)}%</span>}</> : <span className="text-muted">none settled</span>}</td>
                    <td className="pr-2">{e.claimed !== null ? fmtPct(e.claimed, 0) : "—"}</td>
                    <td className="pr-2">{e.mae !== null ? <>{fmtTempDelta(e.mae, u).replace("+", "")} <span className="text-muted">bias {fmtTempDelta(e.bias ?? 0, u)}</span></> : "—"}</td>
                    <td>{e.marketN ? <>market {e.marketHits}/{e.marketN} · platform {e.modelHitsOnMarketDays}/{e.marketN}</> : <span className="text-muted">—</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {scores.length > 0 && (
            <table className="mb-2 w-full tabular-nums">
              <thead className="text-left text-[10px] uppercase tracking-wide text-muted">
                <tr><th className="py-0.5 pr-2">Public forecast</th><th className="pr-2">Lead</th><th className="pr-2">Days</th>
                  <th className="pr-2">Mean miss</th><th className="pr-2">Bias</th><th className="pr-2">Right bucket</th><th>Within 1°C</th></tr>
              </thead>
              <tbody>
                {scores.map((s) => (
                  <tr key={`${s.model}|${s.lead_days}`} className="border-t border-border">
                    <td className="py-0.5 pr-2">{MODEL_LABEL[s.model] ?? s.model}</td>
                    <td className="pr-2">{s.lead_days} d</td>
                    <td className="pr-2">{s.n_days}</td>
                    <td className="pr-2">{fmtTempDelta(s.mae_c, u).replace("+", "")}</td>
                    <td className="pr-2">{fmtTempDelta(s.bias_c, u)}</td>
                    <td className="pr-2">{s.hit_rate_pct !== null ? `${Number(s.hit_rate_pct).toFixed(0)}%` : "—"}</td>
                    <td>{s.within_1c_pct !== null ? `${Number(s.within_1c_pct).toFixed(0)}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {recent.length > 0 && (
            <table className="w-full tabular-nums">
              <thead className="text-left text-[10px] uppercase tracking-wide text-muted">
                <tr><th className="py-0.5 pr-2">Day ({momentLabel(moment)})</th><th className="pr-2">Platform</th>
                  <th className="pr-2">Settled</th><th className="pr-2">Market</th><th>Miss</th></tr>
              </thead>
              <tbody>
                {recent.map((r) => (
                  <tr key={`${r.for_date}|${r.called_when}`} className="border-t border-border">
                    <td className="py-0.5 pr-2">{fmtResolutionDate(r.for_date)}</td>
                    <td className={`pr-2 ${r.hit ? "text-good" : "text-bad"}`}>
                      {r.predicted_band ?? "?"}{r.predicted_pct != null && <span className="text-muted"> {Math.round(Number(r.predicted_pct))}%</span>}
                    </td>
                    <td className="pr-2">{r.actual_band ?? "?"}{r.observed_max_c != null && <span className="text-muted"> ({fmtTemp(Number(r.observed_max_c), u)})</span>}</td>
                    <td className={`pr-2 ${r.market_hit === null ? "text-muted" : r.market_hit ? "text-good" : "text-bad"}`}>{r.market_band ?? "—"}</td>
                    <td>{r.forecast_error_c != null ? fmtTempDelta(Number(r.forecast_error_c), u) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
