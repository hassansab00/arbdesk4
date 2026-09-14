"use client";

import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtAge, fmtPct, fmtPp, fmtPrice } from "@/lib/format";
import { fmtBandRange, fmtTemp, fmtTempDelta, type Unit } from "@/lib/units";
import { fmtDaysAhead, fmtResolutionDate } from "@/lib/time";

/**
 * The desk's argument for one city, in the order a trader would make it.
 *
 * The board shows the ANSWER - a probability per bucket - and asks you to
 * trust it. This shows the WORKING, which is the difference between a number
 * and a reason. Seven steps, each either a fact or an honest gap:
 *
 *   1 the forecast          what the model says the max will be
 *   2 persistence           what yesterday did - the benchmark to beat
 *   3 this morning          dry and clear, or damp and grey, and what that is
 *                           worth in degrees IN THIS CITY
 *   4 AD4's own call        the same coefficients applied to the FORECAST
 *                           conditions, and where that lands against NWS
 *   5 where the day is now  how far it has climbed, is the peak still ahead
 *   6 how wrong we usually are here, at this lead
 *   7 do the models agree
 *   8 therefore             the bucket, its price, and whether to act
 *
 * A step whose input is missing says which job fills it rather than being
 * hidden. That is the point: a chain with a visible hole is still reasoning,
 * and a chain with an invisible hole is a guess.
 */

export interface Reasoning {
  city_key: string;
  display_name: string | null;
  unit: "C" | "F";
  resolution_date: string;
  forecast_max_c: number | null;
  forecast_model: string | null;
  forecast_at: string | null;
  lead_days: number | null;
  prev_max_c: number | null;
  city_persistence_mae_c: number | null;
  persistence_days: number | null;
  morning_temp_c: number | null;
  dewpoint_depression_c: number | null;
  cloud_mean: number | null;
  precip_total: number | null;
  weather_coefficients: Record<string, number> | null;
  weather_model_useful: boolean | null;
  weather_model_notes: string | null;
  now_c: number | null;
  running_max_c: number | null;
  peak_window_state: string | null;
  day_decided: boolean | null;
  observed_at: string | null;
  forecast_mae_c: number | null;
  forecast_bias_c: number | null;
  skill_days: number | null;
  model_spread_c: number | null;
  n_models: number | null;
  models: string | null;
  sigma_multiplier: number | null;
  top_band_label: string | null;
  top_band_lo: number | null;
  top_band_hi: number | null;
  top_open_low: boolean | null;
  top_open_high: boolean | null;
  top_model_prob: number | null;
  top_market_price: number | null;
  top_edge_net_pp: number | null;
  confidence: number | null;
  regime_label: string | null;
  top_tradeable: boolean | null;
  top_block_reason: string | null;
  /** Mass in the ladder's OPEN end buckets. Not a mode - see ad4_23. */
  tail_low_pct: number | null;
  tail_high_pct: number | null;
  forecast_ahead_of_book: boolean | null;
  forecast_move_c: number | null;
  hours_to_resolution: number | null;
}

/** One row of v_model_disagreement — AD4's own forward prediction for a day. */
export interface ModelView {
  city_key: string;
  for_date: string;
  lead_days: number | null;
  predicted_max_c: number | null;
  nws_max_c: number | null;
  disagreement_c: number | null;
  beats_persistence: boolean | null;
  model_mae_c: number | null;
  persistence_mae_c: number | null;
  tradeable_view: boolean | null;
  prev_source: string | null;
  contributions: Record<string, number> | null;
  inputs: Record<string, number> | null;
}

export function useReasoning(cityKey: string | null) {
  return useQuery<Reasoning[]>(
    () =>
      cityKey
        ? supabase.from("v_city_reasoning").select("*").eq("city_key", cityKey)
        : Promise.resolve({ data: [] as Reasoning[], error: null }),
    [cityKey],
    60000
  );
}

/** Fetched separately rather than joined into v_city_reasoning, because
 *  sql/ad4_25 is optional: a desk that has not run it should lose one step,
 *  not the whole panel. */
export function useModelView(cityKey: string | null, forDate: string | null) {
  return useQuery<ModelView[]>(
    () =>
      cityKey && forDate
        ? supabase
            .from("v_model_disagreement")
            .select("*")
            .eq("city_key", cityKey)
            .eq("for_date", forDate)
        : Promise.resolve({ data: [] as ModelView[], error: null }),
    [cityKey, forDate],
    60000
  );
}

/** Coefficient keys are column names; a reader is owed words. */
const DRIVER: Record<string, string> = {
  prev_max_c: "yesterday",
  morning_temp_c: "morning",
  dewpoint_depression_c: "dryness",
  cloud_mean: "cloud",
  wind_mean: "wind",
  precip_total: "rain",
};

export default function ReasoningPanel({ r }: { r: Reasoning }) {
  const u = r.unit as Unit;
  const mv = useModelView(r.city_key, r.resolution_date);
  const m = mv.data?.[0] ?? null;

  // What this morning's conditions are worth, in degrees, using THIS city's
  // own fitted coefficients. Not a rule of thumb - the numbers the model
  // learned from this city's history.
  const c = r.weather_coefficients ?? null;
  const cloudEffect = c && r.cloud_mean !== null ? (c.cloud_mean ?? 0) * r.cloud_mean : null;
  const dryEffect =
    c && r.dewpoint_depression_c !== null ? (c.dewpoint_depression_c ?? 0) * r.dewpoint_depression_c : null;
  const rainEffect = c && r.precip_total ? (c.precip_total ?? 0) * r.precip_total : null;

  const toGo =
    r.forecast_max_c !== null && r.running_max_c !== null ? r.forecast_max_c - r.running_max_c : null;

  return (
    <div className="rounded border border-border bg-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border px-3 py-2">
        <h3 className="text-sm font-semibold">
          Why {r.display_name ?? r.city_key}, {fmtResolutionDate(r.resolution_date)}
        </h3>
        <span className="font-mono text-[10px] text-muted">
          {fmtDaysAhead(r.resolution_date)}
          {r.hours_to_resolution !== null && ` · ${r.hours_to_resolution.toFixed(0)}h to settle`}
        </span>
      </div>

      <ol className="divide-y divide-border text-xs">
        <Step n={1} title="The forecast">
          {r.forecast_max_c === null ? (
            <Gap>
              No forecast for this day. <b>Actions → Forecasts</b> writes them; without one every
              probability below is unanchored.
            </Gap>
          ) : (
            <>
              <b>{r.forecast_model ?? "model"}</b> says the day peaks at{" "}
              <b className="text-accent">{fmtTemp(r.forecast_max_c, u)}</b>
              {r.lead_days !== null && <> at {r.lead_days}-day lead</>}
              {r.forecast_at && <span className="text-muted"> · run {fmtAge(r.forecast_at)}</span>}
              {r.forecast_bias_c !== null && Math.abs(r.forecast_bias_c) > 0.15 && (
                <div className="mt-0.5 text-muted">
                  This model runs {fmtTempDelta(r.forecast_bias_c, u)} {r.forecast_bias_c > 0 ? "hot" : "cold"} here
                  on average, and the engine already subtracts that before pricing.
                </div>
              )}
            </>
          )}
        </Step>

        <Step n={2} title="Yesterday, and the bar it sets">
          {r.prev_max_c === null ? (
            <Gap>No observation history yet — <b>Actions → Station Observations</b>.</Gap>
          ) : (
            <>
              Yesterday peaked at <b>{fmtTemp(r.prev_max_c, u)}</b>.
              {r.city_persistence_mae_c !== null && (
                <>
                  {" "}Simply repeating yesterday is wrong by{" "}
                  <b>{fmtTempDelta(r.city_persistence_mae_c, u).replace("+", "")}</b> on average here
                  {r.persistence_days ? ` over ${r.persistence_days} days` : ""} — that is the bar any
                  forecast has to clear to be worth anything.
                </>
              )}
              {r.forecast_max_c !== null && r.prev_max_c !== null && (
                <div className="mt-0.5 text-muted">
                  The forecast calls for {fmtTempDelta(r.forecast_max_c - r.prev_max_c, u)} on
                  yesterday.
                </div>
              )}
            </>
          )}
        </Step>

        <Step n={3} title="What this morning implies">
          {r.dewpoint_depression_c === null && r.cloud_mean === null ? (
            <Gap>
              No morning conditions for today. Needs <code>sql/ad4_21</code> and observations with
              dewpoint and cloud.
            </Gap>
          ) : (
            <>
              <div className="flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px]">
                {r.dewpoint_depression_c !== null && (
                  <span title="Temperature minus dewpoint at 08:00. Dry air heats faster - the sun's energy raises temperature instead of evaporating water.">
                    <span className="text-muted">dryness </span>
                    {fmtTempDelta(r.dewpoint_depression_c, u).replace("+", "")}
                    {dryEffect !== null && (
                      <span className={dryEffect > 0 ? "text-good" : "text-bad"}>
                        {" "}→ {fmtTempDelta(dryEffect, u)}
                      </span>
                    )}
                  </span>
                )}
                {r.cloud_mean !== null && (
                  <span title="Mean daytime cloud in oktas. Sunlight that never lands cannot heat the ground.">
                    <span className="text-muted">cloud </span>
                    {r.cloud_mean.toFixed(1)}/8
                    {cloudEffect !== null && (
                      <span className={cloudEffect > 0 ? "text-good" : "text-bad"}>
                        {" "}→ {fmtTempDelta(cloudEffect, u)}
                      </span>
                    )}
                  </span>
                )}
                {(r.precip_total ?? 0) > 0 && (
                  <span title="A wet surface spends the afternoon evaporating rather than warming.">
                    <span className="text-muted">rain </span>
                    {r.precip_total?.toFixed(2)}
                    {rainEffect !== null && <span className="text-bad"> → {fmtTempDelta(rainEffect, u)}</span>}
                  </span>
                )}
              </div>
              {c === null ? (
                <div className="mt-1 text-muted">
                  No fitted model for this city yet, so these are conditions without a number
                  attached. <b>Actions → Weather Model</b> fits them once there is a season of
                  history.
                </div>
              ) : r.weather_model_useful === false ? (
                <div className="mt-1 text-warn">
                  This city&apos;s fitted model does <b>not</b> beat simply repeating yesterday, so
                  the effects above are shown but not trusted. That is a finding, not a gap.
                </div>
              ) : (
                <div className="mt-1 text-muted">{r.weather_model_notes}</div>
              )}
            </>
          )}
        </Step>

        <Step n={4} title="AD4's own call">
          {m === null ? (
            <Gap>
              {mv.error
                ? <>Own-model view unavailable: <code>{mv.error}</code>. Run <code>sql/ad4_25</code>.</>
                : <>No forward prediction for this day. Needs <b>P1.4 NWS Gridpoint</b> for the
                   forecast conditions and <b>Actions → Weather Model</b> to apply the fit to
                   them.</>}
            </Gap>
          ) : (
            <>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span>
                  AD4 makes it{" "}
                  <b className="text-accent">{fmtTemp(m.predicted_max_c, u)}</b>
                </span>
                {m.nws_max_c !== null && (
                  <span className="text-muted">
                    NWS {fmtTemp(m.nws_max_c, u)}
                    {m.disagreement_c !== null && (
                      <b className={m.tradeable_view ? " text-accent" : ""}>
                        {" "}({fmtTempDelta(m.disagreement_c, u)})
                      </b>
                    )}
                  </span>
                )}
              </div>

              {m.contributions && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px]">
                  {Object.entries(m.contributions)
                    .filter(([k, v]) => k !== "intercept" && Math.abs(v) >= 0.05)
                    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                    .map(([k, v]) => (
                      <span key={k} title={`${k} = ${m.inputs?.[k] ?? "?"}`}>
                        <span className="text-muted">{DRIVER[k] ?? k} </span>
                        <span className={v > 0 ? "text-good" : "text-bad"}>{fmtTempDelta(v, u)}</span>
                      </span>
                    ))}
                </div>
              )}

              <div className="mt-1 text-muted">
                {m.prev_source === "chained" ? (
                  <>
                    Built on AD4&apos;s own prediction for the day before, not an observation —
                    error compounds with each day out, and this is {m.lead_days ?? "?"} day(s)
                    ahead.
                  </>
                ) : (
                  <>Anchored on an observed maximum, not on another prediction.</>
                )}
              </div>

              {m.beats_persistence === false ? (
                <div className="mt-1 text-warn">
                  This city&apos;s fit does <b>not</b> beat repeating yesterday, so this number is
                  shown and not traded. A finding, not a gap.
                </div>
              ) : m.tradeable_view ? (
                <div className="mt-1 rounded bg-accent/10 px-2 py-1 text-accent">
                  The gap to NWS is larger than this model&apos;s own average error
                  {m.model_mae_c !== null && <> ({fmtTempDelta(m.model_mae_c, u).replace("+", "")})</>}
                  , so it is a view rather than noise.
                </div>
              ) : (
                <div className="mt-1 text-muted">
                  The gap is smaller than the model&apos;s own average error
                  {m.model_mae_c !== null && <> ({fmtTempDelta(m.model_mae_c, u).replace("+", "")})</>}
                  , so it is noise, not a view.
                </div>
              )}
            </>
          )}
        </Step>

        <Step n={5} title="Where the day has got to">
          {r.running_max_c === null ? (
            <Gap>
              No live reading. <b>P1.2 NWS Monitor</b> or the Live Weather action fills this — until
              one runs, the desk is trading a forecast with no idea where the day actually is.
            </Gap>
          ) : (
            <>
              Now <b>{fmtTemp(r.now_c, u)}</b>, peaked so far at{" "}
              <b>{fmtTemp(r.running_max_c, u)}</b>
              {toGo !== null && (
                <>
                  {" "}— {toGo > 0 ? <>still <b>{fmtTempDelta(toGo, u)}</b> to go</> : <b>already at or past the forecast</b>}
                </>
              )}
              .
              <div className="mt-0.5 text-muted">
                Peak window {r.peak_window_state ?? "unknown"}
                {r.day_decided && " · the day is decided — the weather risk is spent"}
                {r.observed_at && (
                  <span className={Date.now() - new Date(r.observed_at).getTime() > 3 * 3600_000 ? " text-bad" : ""}>
                    {" "}· reading {fmtAge(r.observed_at)}
                  </span>
                )}
              </div>
            </>
          )}
        </Step>

        <Step n={6} title="How wrong we usually are here">
          {r.forecast_mae_c === null ? (
            <Gap>Forecast error not measured yet — <b>Actions → Skill</b>.</Gap>
          ) : (
            <>
              At this lead the forecast misses by{" "}
              <b>{fmtTempDelta(r.forecast_mae_c, u).replace("+", "")}</b> on average
              {r.skill_days ? ` over ${r.skill_days} days` : ""}. That error <b>is</b> the width of
              every probability below — a city the model knows well produces sharp buckets, a city it
              does not produces flat ones.
              {r.skill_days !== null && r.skill_days < 200 && (
                <div className="mt-0.5 text-warn">
                  Only {r.skill_days} days measured, so the error figure is itself uncertain and the
                  engine has already widened confidence for it.
                </div>
              )}
            </>
          )}
        </Step>

        <Step n={7} title="Do the models agree">
          {r.n_models === null || r.n_models < 2 ? (
            <Gap>
              Only one forecast model. Run <b>P1.3 NWS Forecast</b> for a second — disagreement
              between two is the only live evidence the desk has about how uncertain today is.
            </Gap>
          ) : (
            <>
              {r.n_models} models ({r.models}) differ by{" "}
              <b>{fmtTempDelta(r.model_spread_c ?? 0, u).replace("+", "")}</b>.
              {(r.sigma_multiplier ?? 1) > 1 ? (
                <> The engine widened its range by ×{(r.sigma_multiplier ?? 1).toFixed(2)} for it.</>
              ) : (
                <> They agree closely, so nothing was widened.</>
              )}
            </>
          )}
        </Step>

        <Step n={8} title="Therefore" last>
          {r.top_band_label === null && r.top_model_prob === null ? (
            <Gap>
              No priced bucket yet. <b>Actions → Probabilities</b> after a book snapshot from P0.3.
            </Gap>
          ) : (
            <>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span>
                  Most likely bucket{" "}
                  <b className="font-mono text-accent">
                    {r.top_band_label ?? fmtBandRange(r.top_band_lo, r.top_band_hi, u, r.top_open_low, r.top_open_high)}
                  </b>{" "}
                  at <b>{fmtPct(r.top_model_prob, 0)}</b>
                </span>
                {r.top_market_price !== null && (
                  <span className="text-muted">
                    market {fmtPrice(r.top_market_price)}
                    {r.top_edge_net_pp !== null && (
                      <span className={r.top_edge_net_pp > 0 ? " text-good" : " text-bad"}>
                        {" "}({fmtPp(r.top_edge_net_pp)})
                      </span>
                    )}
                  </span>
                )}
                {r.confidence !== null && (
                  <span className="text-muted">confidence {fmtPct(r.confidence, 0)}</span>
                )}
              </div>

              {/* The open ends of the ladder, which the mode deliberately
                  excludes. "27C or higher" used to WIN this line purely for
                  being unbounded - it is not a bucket anyone can be most
                  likely to land in, but the mass is real and worth printing
                  where it is large. Off the board is a way to lose. */}
              {((r.tail_high_pct ?? 0) >= 10 || (r.tail_low_pct ?? 0) >= 10) && (
                <div className="mt-1 text-muted">
                  Off the board:{" "}
                  {(r.tail_low_pct ?? 0) >= 10 && (
                    <span>below the lowest bucket <b>{(r.tail_low_pct ?? 0).toFixed(0)}%</b></span>
                  )}
                  {(r.tail_low_pct ?? 0) >= 10 && (r.tail_high_pct ?? 0) >= 10 && " · "}
                  {(r.tail_high_pct ?? 0) >= 10 && (
                    <span>above the highest bucket <b>{(r.tail_high_pct ?? 0).toFixed(0)}%</b></span>
                  )}
                  . Open-ended buckets are unbounded, so they collect mass without being likely.
                </div>
              )}

              {r.forecast_ahead_of_book && (
                <div className="mt-1 rounded bg-accent/10 px-2 py-1 text-accent">
                  The forecast has moved {r.forecast_move_c !== null && <b>{fmtTempDelta(r.forecast_move_c, u)}</b>}{" "}
                  since the market last repriced. This price was set on older information.
                </div>
              )}
              {r.top_tradeable === false && (
                <div className="mt-1 text-warn">
                  Not tradeable: <code>{r.top_block_reason ?? "blocked"}</code>. The reasoning holds;
                  the fill does not.
                </div>
              )}
            </>
          )}
        </Step>
      </ol>
    </div>
  );
}

function Step({ n, title, children, last }: { n: number; title: string; children: React.ReactNode; last?: boolean }) {
  return (
    <li className={`flex gap-3 px-3 py-2 ${last ? "bg-panel2" : ""}`}>
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border font-mono text-[10px] text-muted">
        {n}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-muted">{title}</div>
        <div className="leading-relaxed">{children}</div>
      </div>
    </li>
  );
}

/** A missing input names the job that fills it. A chain with a visible hole is
 *  still reasoning; one with an invisible hole is a guess. */
function Gap({ children }: { children: React.ReactNode }) {
  return <span className="text-muted">{children}</span>;
}
