"use client";

import { fmtTemp, type Unit } from "@/lib/units";

/**
 * A FORECAST, WITH ITS PROVENANCE ATTACHED.
 *
 * The board said New York would reach 87°F. The day made about 76°F and the
 * market settled 74-75°F. The platform already knew: v_city_stats computes
 * `forecast_suspect` when the figure is more than 4°C above anything that city
 * has done in three days, which is the shape of a stale long-lead row.
 *
 * It was rendered on exactly one page. Every other page - the Board, the
 * Globe, Live Weather, the City Monitor, the Overview - printed the number in
 * the same type as a measured one, with nothing to say which model produced
 * it, how far ahead it was issued, or that the desk's own check had already
 * flagged it.
 *
 * So the number and its provenance are now one component, and the provenance
 * cannot be left off by forgetting to add it.
 *
 * The rules, in the order they decide:
 *   no forecast at all      -> say so, and fall back to the running maximum
 *   flagged suspect         -> amber, with a "!" and the reason
 *   issued more than a day ahead, or a stale run -> amber, saying which
 *   otherwise               -> plain, provenance in the tooltip
 */
export interface ForecastFields {
  forecast_max_c: number | null;
  running_max_c?: number | null;
  forecast_model?: string | null;
  forecast_lead_days?: number | null;
  forecast_at?: string | null;
  observed_max_3d_c?: number | null;
  forecast_suspect?: boolean | null;
}

function ageHours(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const h = (Date.now() - new Date(ts).getTime()) / 3600000;
  return Number.isFinite(h) ? h : null;
}

/** Why this number, in one sentence - the tooltip on every forecast shown. */
export function forecastProvenance(c: ForecastFields, unit: Unit | null | undefined): string {
  if (c.forecast_max_c === null || c.forecast_max_c === undefined) {
    return c.running_max_c != null
      ? "No forecast for the local day. This is the running maximum observed so far today."
      : "No forecast and no reading for the local day.";
  }
  const bits = [`${c.forecast_model ?? "unknown model"}`];
  if (c.forecast_lead_days != null) bits.push(`issued ${c.forecast_lead_days} day(s) ahead`);
  const h = ageHours(c.forecast_at);
  if (h != null) bits.push(h < 48 ? `run ${Math.round(h)}h ago` : `run ${Math.round(h / 24)} days ago`);
  if (c.observed_max_3d_c != null) {
    bits.push(`highest observed in 3 days ${fmtTemp(c.observed_max_3d_c, unit)}`);
  }
  if (c.forecast_suspect) {
    bits.push(
      "SUSPECT — more than 4°C above anything this city has done in three days. " +
      "Usually a stale long-lead row rather than weather. Run v_forecast_audit for the reason."
    );
  } else if (h != null && h > 30) {
    bits.push("STALE — nothing newer has been written for today.");
  } else if (c.forecast_lead_days != null && c.forecast_lead_days > 1) {
    bits.push("LONG LEAD — no same-day forecast has been written for this city today.");
  }
  return bits.join(" · ");
}

/** Amber when the desk's own check does not believe the number. */
function concern(c: ForecastFields): "suspect" | "stale" | "lead" | null {
  if (c.forecast_max_c === null || c.forecast_max_c === undefined) return null;
  if (c.forecast_suspect) return "suspect";
  const h = ageHours(c.forecast_at);
  if (h != null && h > 30) return "stale";
  if (c.forecast_lead_days != null && c.forecast_lead_days > 1) return "lead";
  return null;
}

export default function ForecastValue({
  city,
  unit,
  digits = 1,
  showLead = false,
  className = "",
}: {
  city: ForecastFields;
  unit: Unit | null | undefined;
  digits?: number;
  /** print "lead N" under the number - for tables with room for it */
  showLead?: boolean;
  className?: string;
}) {
  const value = city.forecast_max_c ?? city.running_max_c ?? null;
  const isFallback = city.forecast_max_c === null || city.forecast_max_c === undefined;
  const flag = concern(city);
  const tone =
    flag === "suspect" ? "text-warn" : flag ? "text-muted" : isFallback ? "text-muted" : "";

  return (
    <span className={`${tone} ${className}`} title={forecastProvenance(city, unit)}>
      {fmtTemp(value, unit, digits)}
      {flag === "suspect" && <span className="ml-0.5 text-[10px] font-bold">!</span>}
      {isFallback && <span className="ml-0.5 text-[10px]">obs</span>}
      {showLead && city.forecast_max_c != null && city.forecast_lead_days != null && (
        <span className="ml-1 text-[9px] font-normal text-muted">
          lead {city.forecast_lead_days}
        </span>
      )}
    </span>
  );
}
