"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fmtTemp, type Unit } from "@/lib/units";
import type { TradePlan } from "@/lib/types";

/**
 * The three things that turn an edge into a trade, and none of which the
 * Opportunities page could say before: WHEN, WHO, and HOW OLD IS THIS PRICE.
 *
 * These live in one file because they belong to one idea. A card that shows a
 * 16pp edge and nothing else is not wrong, it is unusable: the same number is
 * a trade forty minutes before the peak with the line still climbing, and a
 * memory once the maximum is banked.
 */

/* ------------------------------------------------------------------ WHEN */

export function WindowPill({ p }: { p: TradePlan }) {
  const state = p.window_state;
  if (!state) return null;

  // ONLY s7 keys on the peak window. Every other strategy would take its band
  // at any hour, so an "ENTER NOW" over an s4 card would be claiming a
  // property of the CITY'S DAY as a property of THIS trade. The window is
  // still worth showing on those rows - it is context - but stated, not urged.
  const timed = (p.would_fire ?? []).includes("s7_pre_peak_gradient");
  const mins = Math.max(0, p.minutes_to_peak ?? 0);

  const tone =
    timed && p.in_entry_window ? "bg-good/20 text-good border-good/50"
    : state === "INSIDE" ? "bg-warn/10 text-warn border-warn/30"
    : "bg-panel2 text-muted border-border";
  const label =
    timed && p.in_entry_window ? `ENTER NOW · peak in ${mins}m`
    : state === "INSIDE" ? `peak in ${mins}m`
    : state === "BEFORE" ? `window opens in ${mins}m`
    : p.day_decided ? "day decided" : "past the peak";

  return (
    <span
      className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${tone}`}
      title={
        (p.timing_note ?? "") +
        (timed ? "" : " — this strategy does not key on the peak; the window is context, not its trigger.") +
        (p.peak_source ? ` Peak hour: ${p.peak_source}.` : "")
      }
    >
      {label}
    </span>
  );
}

/**
 * Where the day is heading, relative to THIS band - the sentence a level-only
 * desk cannot write. implied_max is the latest reading plus how much this city
 * has historically still climbed from this local hour; it is measured, not
 * forecast, which is what makes it worth holding a forecast against.
 */
export function DayPath({ p }: { p: TradePlan }) {
  const unit = (p.unit ?? "C") as Unit;
  if (p.implied_max_c == null && p.latest_temp_c == null) return null;

  const verdict = p.day_decided
    ? p.holds_running_max
      ? { tone: "text-good", text: "the banked maximum is inside this band" }
      : { tone: "text-muted", text: "the day is over and its maximum is elsewhere" }
    : p.holds_implied_max
    ? { tone: "text-good", text: "the day is heading into this band" }
    : p.out_of_reach
    ? { tone: "text-bad", text: "even this city's best day does not reach this band" }
    : { tone: "text-muted", text: "the day is heading somewhere else" };

  return (
    <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-border pt-2 font-mono text-[10px]">
      {p.latest_temp_c != null && (
        <span title={`Latest station reading, ${Math.round(p.reading_age_min ?? 0)} min old.`}>
          <span className="text-muted">now </span>{fmtTemp(p.latest_temp_c, unit, 1)}
          {p.slope_3_c_per_h != null && (
            <span className={p.slope_3_c_per_h > 0.1 ? "text-good" : p.slope_3_c_per_h < -0.1 ? "text-bad" : "text-muted"}>
              {" "}{p.slope_3_c_per_h > 0 ? "▲" : p.slope_3_c_per_h < 0 ? "▼" : "→"}
              {Math.abs(p.slope_3_c_per_h).toFixed(1)}/h
            </span>
          )}
        </span>
      )}
      {p.implied_max_c != null && (
        <span
          title={
            "Where this day ends up on observation alone: the latest reading plus how much this city has HISTORICALLY still climbed from this local hour. " +
            (p.implied_max_low_c != null && p.implied_max_high_c != null
              ? `Range on its worst and best days: ${fmtTemp(p.implied_max_low_c, unit, 1)} to ${fmtTemp(p.implied_max_high_c, unit, 1)}.`
              : "")
          }
        >
          <span className="text-muted">heading for </span>
          <span className="text-accent">{fmtTemp(p.implied_max_c, unit, 1)}</span>
          {p.implied_max_low_c != null && p.implied_max_high_c != null && (
            <span className="text-muted">
              {" "}({fmtTemp(p.implied_max_low_c, unit, 0)}–{fmtTemp(p.implied_max_high_c, unit, 0)})
            </span>
          )}
        </span>
      )}
      <span className={`${verdict.tone} ml-auto`}>{verdict.text}</span>
    </div>
  );
}

/* ------------------------------------------------------------------- WHO */

const SHORT: Record<string, string> = {
  s1_buy_low_sell_signal: "s1 buy-low",
  s2_combination_arb: "s2 arb",
  s3_concentration: "s3 concentration",
  s4_tail_fade: "s4 tail fade",
  s5_running_max_lock: "s5 max lock",
  s6_anchor_insurance: "s6 anchor",
  s7_pre_peak_gradient: "s7 pre-peak",
  s8_two_bucket_cover: "s8 two-bucket",
  s9_ladder_basket: "s9 ladder",
};

/**
 * Which strategies would take this, and which of those are switched on.
 *
 * The gap between the two lists is the entire point. Nine strategies ship
 * disabled - correctly, as a safety default - and until now that produced a
 * board full of mispriced bands and a Signals panel reading "every strategy
 * is off", with nothing connecting the two. A row that says "s7 would take
 * this and s7 is off" is a decision; an empty signals list is a shrug.
 */
export function StrategyMirror({ p }: { p: TradePlan }) {
  const all = p.would_fire ?? [];
  const on = new Set(p.would_fire_enabled ?? []);
  if (all.length === 0) return null;
  const anyOff = all.some((s) => !on.has(s));
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1 border-t border-border pt-2">
      <span className="text-[9px] uppercase tracking-wide text-muted">would fire</span>
      {all.map((s) => (
        <span
          key={s}
          className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
            on.has(s)
              ? "border-good/50 bg-good/15 text-good"
              : "border-border bg-panel2 text-muted line-through decoration-warn/60"
          }`}
          title={on.has(s)
            ? `${s} is switched on - this will reach Signals on the next engine run.`
            : `${s} passes its entry test on this row but is switched OFF, so nothing will be proposed.`}
        >
          {SHORT[s] ?? s}
        </span>
      ))}
      {anyOff && (
        <Link
          href="/strategies"
          onClick={(e) => e.stopPropagation()}
          className="ml-auto text-[10px] text-accent hover:underline"
        >
          switch on →
        </Link>
      )}
    </div>
  );
}

/* ------------------------------------------------- HOW OLD IS THIS PRICE */

/**
 * A live-price indicator that is honest about two different clocks: how long
 * since the browser last asked, and how old the BOOK is that came back. They
 * are not the same, and only the second one decides whether a price is real.
 * Polling every 30 seconds against a book that P0.3 last wrote three hours ago
 * produces a page that looks live and is not.
 */
export function LivePrices({
  ageMin, everyMs, onRefresh, loading,
}: {
  ageMin: number | null;
  everyMs: number;
  onRefresh: () => void;
  loading?: boolean;
}) {
  const [since, setSince] = useState(0);

  useEffect(() => {
    if (loading) setSince(0);
  }, [loading]);

  useEffect(() => {
    const t = setInterval(() => setSince((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const stale = ageMin != null && ageMin > 30;
  const next = Math.max(0, Math.round(everyMs / 1000) - since);

  return (
    <div className="flex items-center gap-2 font-mono text-[10px]">
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${stale ? "bg-warn" : "bg-good"} ${loading ? "animate-pulse" : ""}`} />
      <span className={stale ? "text-warn" : "text-muted"}>
        {ageMin == null
          ? "no book snapshot"
          : ageMin < 1
          ? "prices under a minute old"
          : `prices ${Math.round(ageMin)} min old`}
      </span>
      <button
        onClick={onRefresh}
        className="text-muted hover:text-text"
        title={`Re-reads the database now. The next automatic read is in ${next}s. This does not fetch new prices FROM Polymarket - n8n P0.3 does that; use "Refresh books" for a new snapshot.`}
      >
        re-read ({next}s)
      </button>
      {stale && (
        <span className="text-warn" title="Every edge on this page is computed against this book. Older than half an hour and the quotes have almost certainly moved.">
          run P0.3 — these edges are priced against a stale book
        </span>
      )}
    </div>
  );
}
