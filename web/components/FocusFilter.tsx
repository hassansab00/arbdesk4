"use client";

import { MOMENTS, type Scope } from "@/lib/focus";

/**
 * THE ONE SELECTION FOR ALL OF PREDICTIVE (wave F, F.3). Which cities, and at
 * which frozen moment, every panel below shows and adds up.
 *
 * The Seasonal Focus 10 is the set recorded on 6 Oct before its outcomes
 * (focus_sets, docs/FOCUS_PREREG.md). Choosing it only filters what is shown:
 * no probability, price or trade changes with membership.
 */

export type FocusSet = {
  set_id: string; label: string; city_keys: string[];
  window_from: string; window_to: string; evaluate_from: string; recorded_at: string;
};

type City = { city_key: string; display_name: string | null };

export default function FocusFilter({
  scope, setScope, custom, setCustom, moment, setMoment, cities, focus, shown,
}: {
  scope: Scope; setScope: (s: Scope) => void;
  custom: string[]; setCustom: (c: string[]) => void;
  moment: string; setMoment: (m: string) => void;
  cities: City[]; focus: FocusSet | null; shown: number;
}) {
  const btn = (on: boolean) =>
    `rounded border px-2 py-0.5 ${on ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`;
  const toggle = (k: string) =>
    setCustom(custom.includes(k) ? custom.filter((x) => x !== k) : [...custom, k]);
  const groups = Array.from(new Set(MOMENTS.map((m) => m.group)));

  return (
    <section className="space-y-2 rounded border border-border bg-panel p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted">Cities</span>
        <button className={btn(scope === "all")} onClick={() => setScope("all")}>
          All active cities ({cities.length})
        </button>
        <button className={btn(scope === "focus")} onClick={() => setScope("focus")} disabled={!focus}
          title={focus ? focus.city_keys.join(", ") : "the focus set is not recorded in this database"}>
          {focus?.label ?? "Seasonal Focus 10"}
        </button>
        <button className={btn(scope === "custom")} onClick={() => setScope("custom")}>
          Custom selection{scope === "custom" ? ` (${custom.length})` : ""}
        </button>
        <span className="text-muted">· showing {shown} {shown === 1 ? "city" : "cities"}</span>
      </div>
      {scope === "focus" && focus && (
        <p className="max-w-3xl leading-relaxed text-muted">
          <b className="text-text">{focus.city_keys.join(", ")}</b>. Chosen on{" "}
          {focus.recorded_at.slice(0, 10)} as the cities most predictable in their current season, on
          data before the sealed test only, and recorded before any of its outcomes; judged from{" "}
          {focus.evaluate_from} against every other city (docs/FOCUS_PREREG.md). Membership filters
          what is shown. It never raises a probability.
        </p>
      )}
      {scope === "custom" && (
        <div className="flex flex-wrap gap-1">
          {cities.map((c) => (
            <button key={c.city_key} onClick={() => toggle(c.city_key)}
              className={`rounded border px-1.5 py-0.5 ${custom.includes(c.city_key)
                ? "border-accent text-accent" : "border-border text-muted"}`}>
              {c.display_name ?? c.city_key}
            </button>
          ))}
          {custom.length > 0 && (
            <button onClick={() => setCustom([])} className="rounded px-1.5 py-0.5 text-muted underline">clear</button>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-muted">Calls frozen at</span>
        {groups.map((g) => (
          <span key={g} className={`flex flex-wrap items-center gap-1 ${g === "After the peak" ? "border-l border-border pl-3" : ""}`}>
            <span className="text-[10px] uppercase tracking-wide text-muted">{g}</span>
            {MOMENTS.filter((m) => m.group === g).map((m) => (
              <button key={m.key} className={btn(moment === m.key)} onClick={() => setMoment(m.key)}>
                {m.label}
              </button>
            ))}
          </span>
        ))}
      </div>
      {MOMENTS.find((m) => m.key === moment)?.afterPeak && (
        <p className="text-warn">
          After the peak the day has mostly answered itself: these results are labelled apart and are
          not evidence about forecasting.
        </p>
      )}
    </section>
  );
}
