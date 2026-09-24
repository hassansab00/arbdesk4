"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { readAllRows } from "@/lib/readAll";
import { DataState } from "@/components/DataState";
import RefreshButton from "@/components/RefreshButton";
import { fmtPct, fmtPp, fmtPrice, pnlColor, regimeColor } from "@/lib/format";
import { fmtTemp, fmtTempDelta } from "@/lib/units";
import {
  buildCards, disagreementRecord, FORECASTS_DISAGREE_C,
  type CityCard, type CityRow, type DisagreementRecord, type ForecastRow, type HitRow, type LadderRow,
  type LiveRow, type OwnModelRow,
} from "@/lib/cityCards";
import { fmtDateTime, fmtDaysAhead, fmtResolutionDate } from "@/lib/time";

/**
 * ONE CARD PER CITY: the call, at a glance, for the market you can still trade.
 *
 * Every number is read, none is recomputed. Four sources, all fast for the
 * browser role (timed as anon on 24 Sep):
 *
 *   v_prediction_ladder        stored rows (P6.5), 0.65 s for every city and day
 *   v_forecast_convergence_all stored rows (P6.5), 0.32 s
 *   v_model_disagreement       the desk's own model, 0.55 s
 *   live_weather               one row per city
 *
 * v_city_reasoning holds much of the same per city, but took 5.3 s for all
 * cities as anon - too close to the 8 s the browser roles are allowed, which
 * is how panels end up red. It stays the per-city deep dive.
 */

const LADDER_MAX = 8000;
const FORECAST_MAX = 8000;
const MODEL_LABEL: Record<string, string> = {
  open_meteo_forecast: "Open-Meteo", open_meteo_best_match: "Open-Meteo best match", nws: "NWS",
};

function isoDay(offsetDays: number): string {
  return new Date(Date.now() + offsetDays * 86400000).toISOString().slice(0, 10);
}

export default function CityCards({ onPick }: { onPick?: (city: string) => void }) {
  const [pick, setPick] = useState("soonest");
  const since = isoDay(-1);   // a city west of UTC is still trading yesterday's UTC date

  const citiesQ = useQuery<CityRow[]>(
    () => supabase.from("cities").select("city_key,display_name,unit").eq("status", "active").order("city_key"), []
  );
  const ladderQ = useQuery<LadderRow[]>(
    () => readAllRows<LadderRow>((from, to) =>
      supabase.from("v_prediction_ladder")
        .select("city_key,for_date,band_id,band_index,band_label,side,model_prob,forecast_max_c,sigma_c,confidence,regime_label,market_price,edge_net_pp,depth_5c,tradeable,block_reason,edge_at")
        .gte("for_date", since).eq("closed", false)
        .order("for_date").order("city_key").order("band_id").order("side")
        .range(from, to), LADDER_MAX),
    [since], undefined, LADDER_MAX
  );
  const forecastQ = useQuery<ForecastRow[]>(
    () => readAllRows<ForecastRow>((from, to) =>
      supabase.from("v_forecast_convergence_all")
        .select("city_key,for_date,model,lead_days,forecast_max_c,run_at")
        .gte("for_date", since).eq("is_past", false)
        .order("city_key").order("for_date").order("model").order("lead_days")
        .range(from, to), FORECAST_MAX),
    [since], undefined, FORECAST_MAX
  );
  const ownQ = useQuery<OwnModelRow[]>(
    () => supabase.from("v_model_disagreement")
      .select("city_key,for_date,lead_days,predicted_max_c,nws_max_c,promotion_state")
      .gte("for_date", since).limit(1000),
    [since], undefined, 1000
  );
  const liveQ = useQuery<LiveRow[]>(
    () => supabase.from("live_weather")
      .select("city_key,local_date,temp_c,running_max_c,peak_window_state,day_decided,observed_at,source_kind"),
    []
  );

  // The settled record of engine vs market when their favourites differed.
  // v_city_hit_history reads stored rows (P6.5): 11 ms as anon on 24 Sep.
  const hitQ = useQuery<HitRow[]>(
    () => supabase.from("v_city_hit_history")
      .select("unit,model_call,market_call,model_hit,market_hit,head_to_head")
      .gte("for_date", isoDay(-30)).limit(1000),
    [], undefined, 1000
  );
  const record = useMemo(() => disagreementRecord(hitQ.data ?? []), [hitQ.data]);

  const reload = () => { citiesQ.refresh(); ladderQ.refresh(); forecastQ.refresh(); ownQ.refresh(); liveQ.refresh(); hitQ.refresh(); };
  const dates = useMemo(
    () => Array.from(new Set((ladderQ.data ?? []).map((r) => r.for_date))).sort(), [ladderQ.data]);
  const cards = useMemo(
    () => buildCards(citiesQ.data ?? [], ladderQ.data ?? [], forecastQ.data ?? [],
                     ownQ.data ?? [], liveQ.data ?? [], pick),
    [citiesQ.data, ladderQ.data, forecastQ.data, ownQ.data, liveQ.data, pick]);
  // The side panels are extras: a card still shows its ladder if one fails,
  // and says which one did, rather than the whole section going red.
  const sideErrors = [["forecasts", forecastQ.error], ["own model", ownQ.error], ["live weather", liveQ.error]]
    .filter(([, e]) => e) as Array<[string, string]>;

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Every city at a glance</h2>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label className="text-muted">
            Trade date{" "}
            <select value={pick} onChange={(e) => setPick(e.target.value)}
              className="rounded border border-border bg-panel2 px-1.5 py-0.5 text-text">
              <option value="soonest">soonest open, per city</option>
              {dates.map((d) => <option key={d} value={d}>{fmtResolutionDate(d)}</option>)}
            </select>
          </label>
          <button onClick={reload}
            title="Re-reads the stored rows. They are refreshed at :12 and :42 past each hour and after every pipeline run."
            className="rounded border border-border px-2.5 py-1 text-xs text-text hover:bg-panel2">
            {ladderQ.loading ? "loading…" : "Reload"}
          </button>
          <RefreshButton job="P2.1_relearn" label="Reprice now" body={{ only: "prediction" }} onDone={reload} />
        </div>
      </div>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">
        For each city, the market it can still trade: the desk&rsquo;s predicted maximum, the public
        forecasts and the desk&rsquo;s own model for that day, the bucket the model favours against what
        the market charges for it, the market&rsquo;s own favourite, and the best tradeable edge on the
        ladder. Trades that side with the market come first; a trade that bets on the engine against the
        market&rsquo;s favourite is marked and sorted after them, because on settled days the market&rsquo;s
        pick has won about twice as often when the two disagreed (the record is on each card).{" "}
        <b className="text-text">Reload</b> re-reads the stored rows (refreshed at :12 and :42 and after
        each pipeline run). <b className="text-text">Reprice now</b> runs the Intraday pipeline on
        GitHub Actions (about 2 billed minutes); press Reload once it has finished.
      </p>
      {sideErrors.length > 0 && (
        <p className="text-[11px] text-bad">
          Could not read {sideErrors.map(([n, e]) => `${n} (${e})`).join("; ")}. The cards show the rest.
        </p>
      )}
      <DataState
        relation="v_prediction_ladder"
        truncated={ladderQ.truncated}
        loading={ladderQ.loading || citiesQ.loading}
        error={ladderQ.error ?? citiesQ.error}
        isEmpty={cards.length === 0}
        emptyTitle="No open market for any city"
        emptyBody={<>Nothing is priced for an open market yet. Market discovery (n8n P0.2) and the Intraday pipeline fill this.</>}
        onRetry={reload}
      >
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {cards.map((c) => <Card key={`${c.city_key}|${c.for_date}`} c={c} onPick={onPick} record={record[c.unit]} />)}
        </div>
      </DataState>
    </section>
  );
}

function Card({ c, onPick, record }: { c: CityCard; onPick?: (city: string) => void; record?: DisagreementRecord }) {
  const u = c.unit;
  const trusted = c.best && !c.best.against_market;
  return (
    <div className={`rounded border bg-panel p-3 text-xs ${trusted ? "border-accent/60" : c.disagrees ? "border-bad/40" : "border-border"}`}>
      <div className="flex items-baseline justify-between gap-2">
        <button className="truncate text-sm font-semibold text-accent hover:underline"
          onClick={() => onPick?.(c.city_key)} title="Show this city in the panels below">
          {c.name}
        </button>
        <span className="shrink-0 text-muted">
          {fmtResolutionDate(c.for_date)} · {fmtDaysAhead(c.for_date)}
        </span>
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular-nums">{fmtTemp(c.predicted_c, u)}</span>
        {c.sigma_c !== null && <span className="text-muted tabular-nums">± {fmtTempDelta(c.sigma_c, u).replace("+", "")}</span>}
        <span className="text-muted" title="The centre the engine priced the ladder from: its forecast input (Open-Meteo) after bias correction. Not a blend of the forecasts listed below.">
          engine&rsquo;s predicted max
        </span>
      </div>
      {c.centre_outside_forecasts && (
        <div className="mt-0.5 text-[11px] text-bad">
          outside every public forecast for this day - treat the engine&rsquo;s call with suspicion
        </div>
      )}
      {c.forecast_spread_c !== null && c.forecast_spread_c >= FORECASTS_DISAGREE_C && (
        <div className="mt-0.5 text-[11px] text-bad">
          the public forecasts disagree by {fmtTempDelta(c.forecast_spread_c, u).replace("+", "")} - an uncertain day
        </div>
      )}
      {/* The predicted maximum is the forecast centre the ladder was priced
          from. Once the day's own reading is above it, the ladder is priced
          from that floor instead (P2.7; a station reading only, never a model
          value), so say so rather than leave the two
          numbers looking like a contradiction. */}
      {c.live?.running_max_c != null && c.live.source_kind !== "model" && c.predicted_c !== null
        && c.live.running_max_c > c.predicted_c && (
        <div className="mt-0.5 text-[11px] text-accent">
          already {fmtTemp(c.live.running_max_c, u)} today, above the forecast; priced from that floor
        </div>
      )}
      <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-muted">
        {c.confidence !== null && <span>confidence {fmtPct(c.confidence, 0)}</span>}
        {c.regime && <span className={regimeColor(c.regime)}>{c.regime}</span>}
      </div>

      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        <dt className="text-muted">Forecasts</dt>
        <dd className="tabular-nums">
          {c.forecasts.length === 0 ? <span className="text-muted">none for this day</span>
            : c.forecasts.map((f) => (
              <span key={f.model} className="mr-2" title={f.run_at ? `run ${fmtDateTime(f.run_at)}` : undefined}>
                {MODEL_LABEL[f.model] ?? f.model} {fmtTemp(f.max_c, u)}
              </span>))}
        </dd>
        <dt className="text-muted">Desk model</dt>
        <dd className="tabular-nums">
          {c.own?.predicted_max_c != null
            ? <>{fmtTemp(c.own.predicted_max_c, u)}{" "}
                <span className="text-muted">({c.own.promotion_state ?? "state unknown"})</span></>
            : <span className="text-muted">no prediction for this day</span>}
        </dd>
        {c.live && (
          <>
            <dt className="text-muted">Today so far</dt>
            <dd className="tabular-nums">
              max {fmtTemp(c.live.running_max_c, u)} · now {fmtTemp(c.live.temp_c, u)}
              {c.live.day_decided ? <b className="ml-1 text-accent">day decided</b>
                : c.live.peak_window_state ? <span className="ml-1 text-muted">{c.live.peak_window_state}</span> : null}
            </dd>
          </>
        )}
        <dt className="text-muted">Engine favours</dt>
        <dd className="tabular-nums">
          {c.top_band ? <>{c.top_band} <b>{fmtPct(c.top_prob, 0)}</b>
            <span className="text-muted"> · market {fmtPrice(c.top_yes_price)}</span>
            {c.favourite_dead && <span className="text-bad"> · the market treats it as dead</span>}</>
            : <span className="text-muted">not priced</span>}
        </dd>
        <dt className="text-muted">Market favours</dt>
        <dd className="tabular-nums">
          {c.market_band ? <>{c.market_band} <b>{fmtPrice(c.market_price)}</b></>
            : <span className="text-muted">no price</span>}
        </dd>
        <dt className="text-muted">Best trade</dt>
        <dd className="tabular-nums">
          {c.best
            ? <><b className={c.best.against_market ? "text-muted" : pnlColor(c.best.edge)}>{fmtPp(c.best.edge)}</b>{" "}
                {c.best.side} {c.best.band} @ {fmtPrice(c.best.price)}
                {c.best.depth !== null && <span className="text-muted"> · depth ${Math.round(c.best.depth)}</span>}
                {c.best.against_market && <span className="block text-bad">against the market&rsquo;s favourite - not a trade the record supports</span>}</>
            : <span className="text-muted">no positive tradeable edge{c.blocked ? ` (mostly ${c.blocked})` : ""}</span>}
        </dd>
      </dl>

      {c.disagrees && (
        <div className="mt-2 rounded border border-bad/40 px-2 py-1 text-[11px] text-bad">
          The engine and the market favour different buckets.
          {record && record.days > 0 && (
            <> Over the last 30 settled days ({c.unit === "F" ? "°F" : "°C"} cities) they disagreed on {record.days}:
              the market&rsquo;s pick won {record.market_won}, the engine&rsquo;s {record.engine_won}.</>
          )}
        </div>
      )}
      <div className="mt-2 text-[10px] text-muted">
        {c.priced_at ? <>priced {fmtDateTime(c.priced_at)}</> : "not priced yet"}
      </div>
    </div>
  );
}
