"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { operatorRpc } from "@/lib/operator";
import { paperRead } from "@/lib/paperSupabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { StrategyExplainer } from "@/components/StrategyExplainer";
import { FreshnessRow } from "@/components/Provenance";
import { fmtAge, fmtUsd } from "@/lib/format";
import type { StrategyBoardRow, TradePlan } from "@/lib/types";

/**
 * The main switch.
 *
 * WHY THIS PAGE EXISTS. Nine trading strategies ship `enabled = false`. That
 * is the right default - a desk that starts trading the moment it is deployed
 * is a bug - but the only way to change it was an UPDATE typed into the
 * Supabase SQL editor, and that has been the answer given every single time
 * the question came up. A desk whose main switch lives in a SQL console is not
 * a product.
 *
 * WHAT SITS BESIDE THE SWITCH. Its record: how often it fired, how often that
 * filled, what the filled ones came to, and a verdict that separates "has
 * never fired" from "loses money" - which a green/red toggle cannot. Plus the
 * thing that makes the decision concrete: how many bands on the board RIGHT
 * NOW would pass this strategy's entry test if it were on.
 *
 * Reads sql/ad4_33_control.sql (v_strategy_board, set_strategy_enabled) and
 * sql/ad4_34_trade_plan.sql (v_trade_plan.would_fire).
 */

/**
 * One line each, from the strategy's own docstring in scripts/strategies/.
 * Kept here rather than in the database because it is documentation of the
 * code, and it should go stale in the same commit the code does.
 */
const WHAT_IT_DOES: Record<string, string> = {
  s1_buy_low_sell_signal:
    "Buys a bucket while it is cheap and sells when the signal arrives. Must beat two spreads and two fees, not merely beat the market — it pays the round trip twice.",
  s2_combination_arb:
    "Pure arithmetic: when a set of mutually exclusive buckets can be bought for less than they must collectively pay. No forecast dependency, so it runs in regimes the directional strategies sit out.",
  s3_concentration:
    "When this city's forecast is measurably accurate to under one band width, buys the buckets clustered around the model's centre.",
  s4_tail_fade:
    "Sells the outer buckets. The only sourced support is the fee curve: p(1−p) → 0 at the extremes, so the tails are cheap to transact.",
  s5_running_max_lock:
    "Waits for the day to be provably over, then buys the bucket holding the locked maximum while it is still cheap. The outcome is already decided; the only question is the price.",
  s6_anchor_insurance:
    "An anchor position in the likely bucket plus a cheap hedge in its neighbour, sized so being wrong costs less than being right pays.",
  s7_pre_peak_gradient:
    "Enters under an hour before the peak on the DIRECTION of travel, not the level. Still climbing buys the band above; rolling over sells the bands the day can no longer reach.",
  s8_two_bucket_cover:
    "Buys the two most likely ADJACENT buckets when the pair costs under 70c including fees and one of them holds where the day is actually heading.",
  s9_ladder_basket:
    "Buys the contiguous window of buckets with the best expected return per dollar after fees, when it clears the floor and contains where the day is heading.",
};

const ORIGIN_NOTE: Record<string, string> = {
  hassan: "traded by hand before it was code",
  candidate: "candidate — never validated against this desk's own data",
};

/**
 * ONE ROW PER STRATEGY FOR THE DESK IN FRONT OF YOU.
 *
 * Everything above this line is global: v_strategy_board aggregates signals
 * and fact_signal_outcome, neither of which carries an account. That answers
 * "has this ever worked anywhere", which is the wrong question when four
 * desks are running four different policies - on 20 Sep "Wide edge, all US"
 * allowed all nine strategies while "All Cities" allowed three and "Main
 * paper account" allowed none, and the global board showed them identically.
 *
 * v_strategy_desk_board reads paper_trade_plans and paper_orders, which do
 * carry account_id. It comes through /api/paper-desk rather than the browser
 * client because it sits on paper_accounts and a view runs as its owner.
 */
type DeskStrategy = {
  strategy_id: string;
  allowed_here: boolean;
  proposed: number;
  blocked: number;
  took: number;
  working: number;
  orders_filled: number;
  last_proposed_at: string | null;
  last_block_reason: string | null;
  verdict: string;
};
type Desk = { account_id: string; name: string; mode: string; entries_paused: boolean };

type BoardConditions = {
  cities: number; day_decided: number; station_backed: number;
  running_max_series: number; lock_eligible: number; inside_peak_window: number;
};

/**
 * WHAT EACH ONE IS WAITING FOR, in one line.
 *
 * "nothing has met its conditions" names no condition, so there is nothing to
 * do about it. These do. Like WHAT_IT_DOES this is documentation of the code
 * and belongs beside it, not in the database - but where a condition is
 * COUNTABLE the count comes from v_board_conditions, measured, not asserted.
 *
 * s5 and s7 are the two whose gate is a property of the board rather than of
 * a price, and they are also two of the five that have never fired. s5 needs
 * all three of: the day decided, a station reading rather than a model grid,
 * and a running maximum resting on a series. On 20 Sep that was ONE city of
 * 54 - not a misconfiguration, but worth knowing before waiting on it.
 */
const NEEDS: Record<string, { text: string; count?: keyof BoardConditions }> = {
  s1_buy_low_sell_signal: { text: "a tradeable band priced under what the model says it is worth" },
  s2_combination_arb: { text: "every band in one market priced at once — an incomplete basket is not an arb" },
  s3_concentration: { text: "a band the model concentrates enough probability into" },
  s4_tail_fade: { text: "an outer band the market is paying too much for" },
  s5_running_max_lock: {
    text: "the day decided, a station reading behind the maximum, and that maximum resting on a series",
    count: "lock_eligible",
  },
  s6_anchor_insurance: { text: "an anchor band plus a cheap neighbour worth insuring it with" },
  s7_pre_peak_gradient: { text: "a city inside its pre-peak entry window", count: "inside_peak_window" },
  s8_two_bucket_cover: { text: "two buckets that together cover where the day is heading" },
  s9_ladder_basket: { text: "a contiguous run of buckets clearing the floor together" },
};

export default function StrategiesPage() {
  const boardQ = useQuery<StrategyBoardRow[]>(
    () => supabase.from("v_strategy_board").select("*"),
    [],
    60000
  );
  // What each strategy would take right now. This is the number that makes
  // the toggle a decision rather than a guess.
  const planQ = useQuery<Pick<TradePlan, "would_fire" | "city_key" | "band_label" | "side" | "in_entry_window">[]>(
    () => supabase.from("v_trade_plan").select("would_fire,city_key,band_label,side,in_entry_window").limit(600),
    [],
    60000
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ id: string; text: string; bad: boolean } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  // The desk dimension. Same default rule as /paper-trades: open on a desk
  // that CAN act rather than the one created first, because the oldest desk
  // here is manual, paused and empty.
  const [desk, setDesk] = useState("");
  const desksQ = useQuery<Desk[]>(() => paperRead<Desk[]>("accounts"), [], 60000);
  useEffect(() => {
    if (desk || !desksQ.data?.length) return;
    const live = (a: Desk) => (a.mode === "automatic" || a.mode === "assisted") && !a.entries_paused;
    setDesk([...desksQ.data].sort((a, b) => Number(live(b)) - Number(live(a)))[0].account_id);
  }, [desksQ.data, desk]);
  const deskQ = useQuery<DeskStrategy[]>(
    async () => (desk ? paperRead<DeskStrategy[]>("strategies", desk) : { data: [], error: null }),
    [desk],
    60000
  );
  const here = useMemo(
    () => new Map((deskQ.data ?? []).map((d) => [d.strategy_id, d])),
    [deskQ.data]
  );
  const condQ = useQuery<BoardConditions[]>(
    () => supabase.from("v_board_conditions").select("*"),
    [],
    60000
  );
  const cond = condQ.data?.[0];
  const selectedDesk = desksQ.data?.find((d) => d.account_id === desk);

  const rows = (boardQ.data ?? []).filter((r) => r.strategy_id !== "system");

  /** band count per strategy, and how many of those are inside an entry window */
  const wouldTake = useMemo(() => {
    const m = new Map<string, { n: number; now: number }>();
    for (const p of planQ.data ?? []) {
      for (const s of p.would_fire ?? []) {
        const e = m.get(s) ?? { n: 0, now: 0 };
        e.n += 1;
        // Only s7 keys on the peak window. Counting it for the others would
        // dress a property of the city's day as a property of the strategy.
        if (p.in_entry_window && s === "s7_pre_peak_gradient") e.now += 1;
        m.set(s, e);
      }
    }
    return m;
  }, [planQ.data]);

  async function toggle(r: StrategyBoardRow) {
    setBusy(r.strategy_id);
    setMsg(null);
    const { data, error } = await operatorRpc("set_strategy_enabled", {
      p_strategy_id: r.strategy_id,
      p_enabled: !r.enabled,
    });
    setBusy(null);
    if (error) {
      setMsg({ id: r.strategy_id, text: error.message, bad: true });
      return;
    }
    const res = data as { ok?: boolean; error?: string } | null;
    if (res && res.ok === false) {
      setMsg({ id: r.strategy_id, text: res.error ?? "refused", bad: true });
      return;
    }
    setMsg({
      id: r.strategy_id,
      text: r.enabled
        ? "off — it will propose nothing on the next engine run"
        : "on — it will propose from the next engine run",
      bad: false,
    });
    boardQ.refresh();
  }

  const onCount = rows.filter((r) => r.enabled).length;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Strategies</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          Nothing here proposes a trade until it is switched on, and everything ships off. Turning
          one on does not place an order — it lets that strategy propose one to the paper desk.
          The verdict beside each one distinguishes{" "}
          <b>has never fired</b> from <b>loses money</b>; a coloured toggle cannot.
        </p>
        {/* WHAT THIS PAGE STANDS ON. A thin page and an unfed page look
            identical, and only one of them is worth investigating. */}
        <div className="mt-2">
          <FreshnessRow relations={["derived_forecast_skill", "bands", "book_snapshots", "cities", "markets", "paper_trades"]} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded border border-border bg-panel px-3 py-2 text-xs">
        <span>
          <b className={onCount === 0 ? "text-warn" : "text-good"}>{onCount}</b> of {rows.length} on
        </span>
        {onCount === 0 && (
          <span className="text-warn">
            Nothing is running. That is why the paper desk is idle — it is a safety default, not a fault.
          </span>
        )}
        <Link href="/opportunities" className="ml-auto text-accent hover:underline">
          see what they would take →
        </Link>
      </div>

      {/* THE DESK THIS IS BEING READ FOR. Without it every number on this page
          is summed across desks that do not share a policy, which is how a
          strategy that was never allowed here reads the same as one that ran
          here and lost. */}
      <div className="flex flex-wrap items-center gap-3 rounded border border-border bg-panel px-3 py-2 text-xs">
        <label className="flex items-center gap-2">
          <span className="text-muted">On desk</span>
          <select
            className="input max-w-xs text-xs"
            aria-label="Paper desk"
            value={desk}
            onChange={(e) => setDesk(e.target.value)}
          >
            {desksQ.data?.map((d) => (
              <option key={d.account_id} value={d.account_id}>
                {d.name} — {d.mode}
                {d.entries_paused ? " · paused" : ""}
              </option>
            ))}
          </select>
        </label>
        {selectedDesk && (
          <span className="text-muted">
            {(deskQ.data ?? []).filter((d) => d.allowed_here).length} of {rows.length} allowed here
            {" · "}
            {(deskQ.data ?? []).filter((d) => d.took > 0).length} have traded here
          </span>
        )}
        <Link href="/paper-trades#automation" className="ml-auto text-accent hover:underline">
          change what this desk allows →
        </Link>
      </div>

      {/* CONDITIONS ON THE BOARD, MEASURED. Five of the nine have never
          proposed anything on any desk, and "nothing has met its conditions"
          does not say whether that is the strategy, the settings or the
          weather. These are the preconditions a strategy can depend on,
          counted across the city-days actually on the board. */}
      {cond && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded border border-border bg-panel px-3 py-2 text-xs">
          <span className="text-muted">On the board now</span>
          <span className="font-mono">{cond.cities} cities</span>
          <span className="font-mono text-muted">{cond.day_decided} day decided</span>
          <span className="font-mono text-muted">{cond.station_backed} station-backed</span>
          <span className={`font-mono ${cond.inside_peak_window > 0 ? "text-good" : "text-muted"}`}>
            {cond.inside_peak_window} inside a peak window
          </span>
          <span className={`font-mono ${cond.lock_eligible > 0 ? "text-good" : "text-warn"}`}>
            {cond.lock_eligible} lockable
          </span>
        </div>
      )}

      <DataState
          relation="v_strategy_board"
        loading={boardQ.loading}
        error={boardQ.error}
        isEmpty={rows.length === 0}
        emptyTitle="No strategies registered"
        emptyBody={
          <>
            Run <code className="rounded bg-panel2 px-1">sql/ad4_strategies_seed.sql</code>, then{" "}
            <code className="rounded bg-panel2 px-1">sql/ad4_33_control.sql</code>.
          </>
        }
        onRetry={boardQ.refresh}
      >
        <div className="space-y-2">
          {rows.map((r) => {
            const take = wouldTake.get(r.strategy_id);
            const open = expanded === r.strategy_id;
            const origin = r.origin ?? "";
            return (
              <div
                key={r.strategy_id}
                className={`rounded border bg-panel ${r.enabled ? "border-good/40" : "border-border"}`}
              >
                <div className="flex flex-wrap items-start gap-3 p-3">
                  {/* ---- the switch ------------------------------------- */}
                  <button
                    onClick={() => toggle(r)}
                    disabled={busy === r.strategy_id}
                    aria-pressed={r.enabled}
                    title={r.enabled ? "Switch off — it stops proposing" : "Switch on — it starts proposing on the next engine run"}
                    className={`mt-0.5 flex h-5 w-9 shrink-0 items-center rounded-full border px-0.5 transition disabled:opacity-40 ${
                      r.enabled ? "justify-end border-good bg-good/30" : "justify-start border-border bg-panel2"
                    }`}
                  >
                    <span className={`h-3.5 w-3.5 rounded-full ${r.enabled ? "bg-good" : "bg-muted"}`} />
                  </button>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-semibold">{r.name ?? r.strategy_id}</span>
                      <code className="text-[10px] text-muted">{r.strategy_id}</code>
                      {r.side && (
                        <span className="rounded bg-panel2 px-1 font-mono text-[10px] text-muted">{r.side}</span>
                      )}
                      {origin && ORIGIN_NOTE[origin] && (
                        <span
                          className={`text-[10px] ${origin === "candidate" ? "text-warn" : "text-muted"}`}
                          title={
                            origin === "candidate"
                              ? "No hit-rate claim survives from before this build. Treat its record here as the only evidence."
                              : "Traded by hand before it was written down."
                          }
                        >
                          {ORIGIN_NOTE[origin]}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 max-w-3xl text-[11px] leading-relaxed text-muted">
                      {WHAT_IT_DOES[r.strategy_id] ?? "—"}
                    </p>

                    {/* ---- ON THIS DESK ---------------------------------
                        The record above is every desk at once. This line is
                        the one Hassan is actually looking at, and the block
                        reason is the sentence that was missing entirely: of
                        360 plans on this database 284 are blocked, and the
                        commonest cause is the venue's minimum order size,
                        which says nothing about the strategy. */}
                    {(() => {
                      const d = here.get(r.strategy_id);
                      if (!d) return null;
                      return (
                        <div className="mt-1.5 rounded border border-border/60 bg-panel2/40 px-2 py-1 text-[11px]">
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                            <span
                              className={
                                d.allowed_here ? "text-good" : "text-muted"
                              }
                            >
                              {d.allowed_here ? "allowed here" : "not allowed here"}
                            </span>
                            {d.proposed > 0 ? (
                              <span className="font-mono text-muted">
                                {d.proposed} proposed · {d.took} through · {d.blocked} blocked
                                {d.working > 0 && ` · ${d.working} working`}
                              </span>
                            ) : (
                              d.allowed_here && <span className="text-muted">nothing proposed here yet</span>
                            )}
                            {d.last_proposed_at && (
                              <span className="text-muted">last {fmtAge(d.last_proposed_at)}</span>
                            )}
                          </div>
                          <div className={d.took > 0 ? "text-muted" : "text-warn"}>{d.verdict}</div>
                          {d.took === 0 && d.last_block_reason && (
                            <div className="mt-0.5 text-muted">
                              last stopped by: <span className="font-mono">{d.last_block_reason}</span>
                            </div>
                          )}
                          {/* When it has proposed nothing at all, the verdict
                              above says so and stops. This says what it is
                              waiting FOR, which is the part that can be acted
                              on - and counts it where the board can be
                              counted. */}
                          {d.allowed_here && d.proposed === 0 && NEEDS[r.strategy_id] && (
                            <div className="mt-0.5 text-muted">
                              needs {NEEDS[r.strategy_id].text}
                              {cond && NEEDS[r.strategy_id].count && (
                                <span
                                  className={
                                    cond[NEEDS[r.strategy_id].count!] > 0 ? " text-good" : " text-warn"
                                  }
                                >
                                  {" "}
                                  — {cond[NEEDS[r.strategy_id].count!]} of {cond.cities} qualify right now
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {/* ---- what it would take right now ----------------- */}
                    {take && take.n > 0 && (
                      <div className="mt-1.5 text-[11px]">
                        <span className={r.enabled ? "text-good" : "text-accent"}>
                          {take.n} band{take.n === 1 ? "" : "s"} on the board pass{take.n === 1 ? "es" : ""} its
                          entry test right now
                        </span>
                        {take.now > 0 && (
                          <span className="text-warn"> · {take.now} inside a peak entry window</span>
                        )}
                        {!r.enabled && <span className="text-muted"> — and it is off</span>}
                      </div>
                    )}

                    {msg?.id === r.strategy_id && (
                      <div className={`mt-1.5 text-[11px] ${msg.bad ? "text-bad" : "text-good"}`}>{msg.text}</div>
                    )}
                  </div>

                  {/* ---- its record ------------------------------------- */}
                  <div className="grid shrink-0 grid-cols-4 gap-x-4 text-right font-mono text-xs">
                    <Stat label="fired 30d" v={r.fired_30d} sub={r.last_fired_at ? fmtAge(r.last_fired_at) : "never"} />
                    <Stat label="filled" v={r.filled_all_time} />
                    <Stat
                      label="win rate"
                      v={r.win_rate_pct == null ? "—" : `${r.win_rate_pct}%`}
                      tone={r.win_rate_pct == null ? "" : r.win_rate_pct >= 50 ? "text-good" : "text-bad"}
                    />
                    <Stat
                      label="net P&L"
                      v={r.net_pnl == null ? "—" : fmtUsd(r.net_pnl, { signed: true })}
                      tone={r.net_pnl == null ? "" : r.net_pnl > 0 ? "text-good" : "text-bad"}
                    />
                  </div>
                </div>

                {/* ---- the verdict, in words --------------------------- */}
                <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-1.5 text-[11px]">
                  <span className={verdictTone(r.verdict)}>{r.verdict}</span>
                  {r.waiting > 0 && (
                    <span className="text-warn">{r.waiting} waiting for approval</span>
                  )}
                  <button
                    onClick={() => setExpanded(open ? null : r.strategy_id)}
                    aria-expanded={open}
                    className={`ml-auto rounded border px-2 py-0.5 transition ${
                      open ? "border-accent/60 text-accent" : "border-border text-muted hover:border-accent/40 hover:text-text"
                    }`}
                  >
                    {open ? "hide" : "how it works"}
                  </button>
                </div>

                {/* THE STRATEGY ITSELF, not its record. One worked market,
                    eleven buckets, shared by all nine so they can be compared
                    - and clickable, so "what happens if the day lands there"
                    is answered by settling the position rather than by a
                    sentence about it. */}
                {open && <StrategyExplainer strategyId={r.strategy_id} />}

                {open && (
                  <div className="grid gap-x-6 gap-y-1 border-t border-border px-3 py-2 text-[11px] sm:grid-cols-2 lg:grid-cols-4">
                    <Limit
                      label="capital cap"
                      v={r.capital_cap_pct == null ? "—" : `${r.capital_cap_pct}% of bankroll`}
                      why="The most this one strategy may have at risk at once."
                    />
                    <Limit
                      label="max concurrent"
                      v={r.max_concurrent == null ? "—" : String(r.max_concurrent)}
                      why="How many open positions it may hold. Correlated cities make ten positions behave like three."
                    />
                    <Limit
                      label="conflict class"
                      v={r.conflict_class ?? "—"}
                      why="Strategies in the same class cannot both take the same band; the conflict layer picks one."
                    />
                    <Limit
                      label="regime filter"
                      v={Array.isArray(r.regime_filter) ? (r.regime_filter as string[]).join(", ") : "—"}
                      why="The forecast regimes it is allowed to run in. UNCERTAIN means sigma is already widened."
                    />
                    <p className="col-span-full mt-1 text-[10px] leading-relaxed text-muted">
                      These are risk limits, not preferences, so they are deliberately not editable
                      from the browser — the switch RPC flips one boolean and touches nothing else.
                      Change them in <code>strategies</code> directly.
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </DataState>
    </div>
  );
}

function verdictTone(v: string) {
  if (v.startsWith("off")) return "text-muted";
  if (v.startsWith("profitable")) return "text-good";
  if (v.startsWith("losing")) return "text-bad";
  return "text-warn";
}

function Stat({ label, v, sub, tone = "" }: { label: string; v: number | string; sub?: string; tone?: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide text-muted">{label}</div>
      <div className={tone}>{v}</div>
      {sub && <div className="text-[9px] text-muted">{sub}</div>}
    </div>
  );
}

function Limit({ label, v, why }: { label: string; v: string; why: string }) {
  return (
    <div title={why}>
      <span className="text-muted">{label} </span>
      <span className="font-mono">{v}</span>
    </div>
  );
}
