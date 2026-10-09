"use client";

import { useMemo, useState } from "react";
import { effectiveCost, singleBucketGrowth, solveKelly } from "@/lib/kelly";

/**
 * WHAT EACH STRATEGY ACTUALLY DOES, ON ONE MARKET YOU CAN POKE.
 *
 * The board says whether a strategy fired and what it made. It does not say
 * what the thing IS, and nine of them is more than anyone holds in their head
 * - which is how "is s3 working?" became a question nobody could answer
 * without reading Python.
 *
 * So: one worked market, eleven buckets, shared by all nine. Each strategy
 * highlights what IT would buy on it and says why, and clicking a bucket asks
 * "suppose the day lands here" and settles the position for real - cost,
 * payout, fee, profit. The same market throughout is the point: it makes the
 * nine comparable, and it shows the ones that take NOTHING here, which is the
 * half the board cannot explain.
 *
 * THE NUMBERS ARE A WORKED EXAMPLE, NOT LIVE DATA, and the panel says so where
 * it is drawn. They are chosen to match what the live board measured on
 * 21 Sep 2026 rather than to flatter anything:
 *
 *   the eleven asks sum to 1.13 against a $1.00 payout, so there is no
 *   arbitrage - live, 71 complete baskets averaged 1.2581 and the cheapest
 *   was 1.0628, and not one was under a dollar
 *
 *   the best adjacent pair is 51% likely and costs 45c, so the two-bucket
 *   cover is refused on probability and not on price - live, the cost gate
 *   passed 127 times and the probability gate zero, best pair 0.703 against
 *   its 0.72 bar
 *
 * The economics are the venue's, not an approximation: exactly one bucket
 * settles at $1.00 and the rest at zero, and the fee is shares x 0.05 x
 * p x (1 - p), which is what scripts/cost_model.py charges.
 *
 * THE ENGINE STRATEGIES (S10, S11, S12 and their model-only twins) were
 * registered on 27 Sep and 8 Oct without walkthroughs, so for 12 of the 13
 * active strategies the panel said "No walkthrough is written" (Hassan, 9 Oct:
 * "we used to have it visible when we clicked, but now no"). They have them
 * now, below the nine; what each buys on the worked market is what the
 * engine's solver buys on it (lib/kelly.ts), not a typed claim.
 */

// --------------------------------------------------------------------------
// The market.
// --------------------------------------------------------------------------
export type Bucket = {
  label: string;
  ask: number;   // executable YES ask
  prob: number;  // the model's probability this bucket wins
  tail?: "low" | "high";
};

/** Eleven buckets: nine closed, two open tails. A day in September, 30.5C forecast. */
export const MARKET: Bucket[] = [
  { label: "≤26",   ask: 0.02, prob: 0.010, tail: "low" },
  { label: "26–27", ask: 0.03, prob: 0.020 },
  { label: "27–28", ask: 0.05, prob: 0.030 },
  { label: "28–29", ask: 0.10, prob: 0.080 },
  { label: "29–30", ask: 0.21, prob: 0.200 },
  { label: "30–31", ask: 0.30, prob: 0.300 },
  { label: "31–32", ask: 0.15, prob: 0.210 },
  { label: "32–33", ask: 0.13, prob: 0.090 },
  { label: "33–34", ask: 0.07, prob: 0.040 },
  { label: "34–35", ask: 0.04, prob: 0.015 },
  { label: "≥35",   ask: 0.03, prob: 0.005, tail: "high" },
];

const CENTRE = 5;   // 30–31, where the forecast points and the model concentrates
const MISPRICED = 6; // 31–32, the one bucket the market has cheaper than the model

export const FEE_RATE = 0.05;
/** The venue's fee, per share: rate x p x (1 - p). Peaks at 50c, ~0 at the tails. */
export const fee = (price: number) => FEE_RATE * price * (1 - price);

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const money = (n: number) => `${n < 0 ? "−" : ""}$${Math.abs(n).toFixed(2)}`;
const cents = (n: number) => `${Math.round(n * 100)}c`;

// --------------------------------------------------------------------------
// What each strategy takes on this market, and why.
// --------------------------------------------------------------------------
type Leg = { i: number; side: "YES" | "NO" };
type Gate = { label: string; value: string; ok: boolean };

type Play = {
  headline: string;
  steps: string[];
  legs: Leg[];
  /** Set when the strategy takes nothing here - the interesting half. */
  refuses?: string;
  gates: Gate[];
  reads: string;
  /** s7 only: the shape of the afternoon, which is the whole signal. */
  trace?: { at: string; c: number }[];
};

const roundTrip = (price: number) => 2 * fee(price) + 0.02; // two fees plus a 2c spread, twice crossed

/**
 * EVERY ARITHMETIC CLAIM BELOW IS COMPUTED FROM `MARKET`, NOT TYPED.
 *
 * The first draft of this panel carried the totals as string literals - "$0.86
 * including fees" on a set that costs $0.89 once the fee is actually added,
 * and "beats the bar by 4c" where the bar works out at 3c. Both were wrong in
 * the direction that flatters the strategy, which is the direction a typed
 * number always drifts. A panel that exists to explain the arithmetic cannot
 * be the one place the arithmetic is asserted.
 */
const askOf = (l: Leg) => (l.side === "YES" ? MARKET[l.i].ask : 1 - MARKET[l.i].ask);
const grossOf = (legs: Leg[]) => sum(legs.map(askOf));
const feesOf = (legs: Leg[]) => sum(legs.map((l) => fee(askOf(l))));
const allInOf = (legs: Leg[]) => grossOf(legs) + feesOf(legs);
const probOf = (legs: Leg[]) =>
  sum(legs.map((l) => (l.side === "YES" ? MARKET[l.i].prob : 1 - MARKET[l.i].prob)));

const YES_ALL: Leg[] = MARKET.map((_, i) => ({ i, side: "YES" as const }));
const COVERED: Leg[] = [4, 5, 6, 7, 8].map((i) => ({ i, side: "YES" as const }));
const RUN: Leg[] = [4, 5, 6].map((i) => ({ i, side: "YES" as const }));
const PAIR: Leg[] = [5, 6].map((i) => ({ i, side: "YES" as const }));
const pct = (x: number) => `${Math.round(x * 100)}%`;

// --------------------------------------------------------------------------
// THE ENGINE STRATEGIES (plan v2 P8.1; registered 27 Sep, the model-only twins
// 8 Oct). Each is a view - the model's probability for every bucket - plus
// constraints, and the engine's own solver decides what to buy. So the legs
// are what holdings_solver buys on this market, computed by lib/kelly.ts with
// the same arithmetic.
//
// MODEL is what each does on the model's own numbers, which is how its twin
// trades (w = 1). The strategy itself starts from the market and stays there
// (w = 0) until the model earns weight, and its panel says what that means.
// --------------------------------------------------------------------------
const probs = MARKET.map((b) => b.prob);
const label = (i: number) => MARKET[i].label;
const list = (legs: Leg[]) => legs.map((l) => label(l.i)).join(", ");
/** edge_engine.DEFAULT_MIN_PRICE_YES: the platform buys no YES under 7c. */
const MIN_YES = 0.07;
const tradeableYes = MARKET.map((b) => b.ask >= MIN_YES);
/** A NO at 1 - the ask: this market quotes one price per bucket (as s4 above). */
const noPrices = MARKET.map((b) => 1 - b.ask);
/** A fraction of the ledger under 0.1% is not a leg. */
const TAKES = 1e-3;
const legsOf = (weights: number[], side: "YES" | "NO"): Leg[] =>
  weights.flatMap((w, i) => (w > TAKES ? [{ i, side }] : []));

const TOP = probs.indexOf(Math.max(...probs));
const topCost = effectiveCost(MARKET[TOP].ask)!;
const topGrowth = singleBucketGrowth(MARKET[TOP].prob, MARKET[TOP].ask);
const growths = MARKET.map((b, i) => (tradeableYes[i] ? singleBucketGrowth(b.prob, b.ask) : 0));
const BEST = growths.indexOf(Math.max(...growths));
const bestGrows = growths[BEST] > 0;
const ladderBook = solveKelly(probs, MARKET.map((b, i) => (tradeableYes[i] ? b.ask : null)), "YES");
const LADDER: Leg[] = legsOf(ladderBook.weights, "YES");
const noBook = solveKelly(probs, noPrices, "NO");
const NOS: Leg[] = legsOf(noBook.weights, "NO");
const noEdges = MARKET.filter((b) => 1 - b.prob > effectiveCost(1 - b.ask)!).length;
/** Equal shares of every bucket: the cheapest book that pays the same whatever wins. */
const LOCK_COST = allInOf(YES_ALL);
const LOCKS = LOCK_COST < 1;
const lockRefusal =
  `Holding the same number of shares of all eleven is the cheapest book that pays the same whichever bucket wins, and here it costs ${money(LOCK_COST)} with fees for $1.00 back. No book avoids a loss in every outcome, so it holds nothing.`;
const lockGates = (anchor: string): Gate[] => [
  { label: "anchor", value: anchor, ok: true },
  { label: "every bucket quoted", value: `${MARKET.length} of ${MARKET.length}`, ok: true },
  { label: "equal shares of all eleven", value: `${money(LOCK_COST)} with fees`, ok: LOCKS },
  { label: "safe in every outcome", value: LOCKS ? "yes" : `no — ${cents(LOCK_COST - 1)} more than it pays`, ok: LOCKS },
];

const MODEL: Record<string, Play> = {
  s10_winner: {
    headline: "Hold the YES of the one bucket most likely to win, and move only when the evidence says to.",
    steps: [
      "Read the model's probability for every bucket: the remaining-day ladder, through the belief layer.",
      "Drop every bucket the station's measured maximum has already ruled out, and every book the platform will not trade: a dead book, or a YES under 7c.",
      "Hold the YES of the most probable bucket left, one bucket per city-day, staked by Kelly on the price with the fee.",
      `Here that is ${label(TOP)} at ${pct(MARKET[TOP].prob)}, which costs ${cents(topCost)} with the fee. ${topGrowth > 0
        ? "It is priced under its probability, so it is held."
        : "A bucket priced at or above its probability has a Kelly stake of zero, so there is nothing to hold."}`,
      "Switch only when the growth gained beats the cost of selling what is held plus a margin (h_switch, 0.005 to start). A held bucket the station rules out is sold at the bid at once.",
    ],
    legs: topGrowth > 0 ? [{ i: TOP, side: "YES" }] : [],
    refuses: topGrowth > 0 ? undefined
      : `The most probable bucket, ${label(TOP)}, is ${pct(MARKET[TOP].prob)} likely and costs ${cents(topCost)} with the fee: no edge, so it holds nothing rather than a favourite at its fair price. The favourite is the bet only when it is cheap.`,
    gates: [
      { label: "most probable bucket", value: `${label(TOP)}, ${pct(MARKET[TOP].prob)}`, ok: true },
      { label: "tradeable (7c and up)", value: cents(MARKET[TOP].ask), ok: tradeableYes[TOP] },
      { label: "price with the fee", value: cents(topCost), ok: MARKET[TOP].prob > topCost },
      { label: "Kelly growth", value: topGrowth.toFixed(4), ok: topGrowth > 0 },
    ],
    reads: "scripts/strategies/s10_max_temp_winner.py · holdings_solver.single_bucket_growth",
  },
  s10_growth: {
    headline: "Hold the one YES that grows the ledger most - often a cheaper bucket than the favourite.",
    steps: [
      "Read the model's probability for every bucket, and drop the ones the station has ruled out and the books the platform will not trade.",
      "For each bucket left, work out how fast a Kelly stake on its YES grows the ledger, with the fee in the price.",
      "Hold the one that grows it most. Growth pays for the edge relative to the price, not for being likely, so a long shot priced well under its probability can beat the favourite.",
      bestGrows
        ? `Here that is ${label(BEST)}: ${pct(MARKET[BEST].prob)} likely at ${cents(effectiveCost(MARKET[BEST].ask)!)} with the fee. The favourite, ${label(TOP)}, grows ${topGrowth > 0 ? "less" : "nothing"} at ${cents(topCost)}.`
        : "Here no tradeable bucket is priced under its probability, so nothing grows the ledger.",
      "One bucket per city-day, and the same switching rule as s10_winner.",
    ],
    legs: bestGrows ? [{ i: BEST, side: "YES" }] : [],
    refuses: bestGrows ? undefined : "No tradeable bucket is priced under what the model makes it, so it holds nothing.",
    gates: [
      { label: "tradeable YES buckets", value: `${tradeableYes.filter(Boolean).length} of ${MARKET.length}`, ok: true },
      { label: "best growth", value: bestGrows ? `${label(BEST)}, ${growths[BEST].toFixed(4)}` : "none", ok: bestGrows },
      { label: "the favourite's growth", value: topGrowth.toFixed(4), ok: topGrowth > 0 },
      { label: "one bucket per city-day", value: "yes", ok: true },
    ],
    reads: "scripts/strategies/s10_max_temp_winner.py · holdings_solver.best_single_bucket",
  },
  s10_lock: {
    headline: "Anchored on the most likely bucket, trade only a book that cannot end below its cost whatever wins.",
    steps: [
      "Anchor on the most probable bucket, as s10_winner does.",
      "Trade only a book over the WHOLE ladder that cannot leave the ledger below what it had, whichever bucket wins - the old S6 insurance.",
      "Every bucket counts, the dead ones too: a lock is about what the venue pays, not about what the forecast rules out.",
      `The cheapest such book is the same shares of every bucket. Here it costs ${money(LOCK_COST)} with fees and pays $1.00.`,
    ],
    legs: LOCKS ? YES_ALL : [],
    refuses: LOCKS ? undefined : lockRefusal,
    gates: lockGates(label(TOP)),
    reads: "scripts/strategies/s10_max_temp_winner.py · holdings_solver.solve_book(lock=True)",
  },
  s11_ladder: {
    headline: "Buy the set of buckets that grows the ledger fastest: small on a sharp day, wider on an uncertain one.",
    steps: [
      "Read the model's probability for every bucket.",
      "Keep the YES of every bucket the platform will trade (7c and up) that the station has not ruled out.",
      "Buy the set that grows the ledger fastest - Kelly's horse race: buckets in order of probability over price, each added while it still beats what the set so far leaves in cash.",
      LADDER.length
        ? `Here that is ${list(LADDER)}: ${pct(1 - ladderBook.cash)} of the ledger, the rest kept as cash. No width rule: a sharper ladder would give a smaller set.`
        : "Here no bucket is priced under its probability, so the set is empty.",
      "The engine then sizes on the worst draws of the posterior and a fraction of Kelly, so the stake it actually buys is smaller.",
    ],
    legs: LADDER,
    refuses: LADDER.length ? undefined : "No tradeable bucket is priced under what the model makes it, so it buys nothing.",
    gates: [
      { label: "tradeable YES buckets", value: `${tradeableYes.filter(Boolean).length} of ${MARKET.length}`, ok: true },
      { label: "the set", value: LADDER.length ? list(LADDER) : "none", ok: LADDER.length > 0 },
      { label: "share of the ledger (plain Kelly)", value: pct(1 - ladderBook.cash), ok: true },
      { label: "expected log-growth", value: ladderBook.growth.toFixed(4), ok: ladderBook.growth > 0 },
    ],
    reads: "scripts/strategies/s11_ladder_optimiser.py · holdings_solver.solve_book",
  },
  s11_lock: {
    headline: "The ladder optimiser under a no-loss constraint: a book that cannot end below its cost in any outcome.",
    steps: [
      "The same view as s11_ladder.",
      "The book must not end below its cost whichever bucket wins.",
      "The whole ladder stays in, dead buckets included: a lock is about what the venue pays.",
      `The cheapest book that pays the same in every outcome is equal shares of all eleven. Here it costs ${money(LOCK_COST)} with fees and pays $1.00.`,
    ],
    legs: LOCKS ? YES_ALL : [],
    refuses: LOCKS ? undefined : lockRefusal,
    gates: lockGates("the whole ladder"),
    reads: "scripts/strategies/s11_ladder_optimiser.py · holdings_solver.solve_book(lock=True)",
  },
  s12_no: {
    headline: "Sell the buckets the market overprices by buying their NO - tails especially.",
    steps: [
      "A NO pays $1.00 whenever the day lands anywhere but its bucket, so buying a bucket's NO is selling the bucket.",
      "Its price is the book's NO ask, else 1 − the YES bid. This market quotes one price per bucket, so a NO here costs 1 − the ask.",
      "Skip a bucket the station has already ruled out - its NO has nothing left to win - and any dead book.",
      NOS.length
        ? `Buy the growth-optimal set of NOs on the model's probabilities. Here: NO on ${list(NOS)}, buckets the market prices above what the model makes them by more than the fee.`
        : "Buy the growth-optimal set of NOs on the model's probabilities. Here there is none.",
      "The engine sizes on the worst draws of the posterior and a fraction of this, so the less certain the model, the smaller the stake.",
    ],
    legs: NOS,
    refuses: NOS.length ? undefined : "No NO is priced under its probability after the fee, so it buys nothing.",
    gates: [
      { label: "NOs priced under their probability", value: `${noEdges} of ${MARKET.length}`, ok: noEdges > 0 },
      { label: "the set", value: NOS.length ? list(NOS) : "none", ok: NOS.length > 0 },
      { label: "share of the ledger (plain Kelly)", value: pct(1 - noBook.cash), ok: true },
      { label: "expected log-growth", value: noBook.growth.toFixed(4), ok: noBook.growth > 0 },
    ],
    reads: "scripts/strategies/s12_overpriced_no.py · holdings_solver.solve_book(allow=NO)",
  },
};

const sideList = (legs: Leg[]) => legs.map((l) => `${l.side} ${label(l.i)}`).join(", ");

/**
 * An engine strategy as it trades: anchored on the market until the model
 * earns weight. The 16,182 decisions and one buy are the twins' migration's
 * count (20261008100000: decisions, live and data/archive/decisions).
 */
function anchored(base: string): Play {
  const m = MODEL[base];
  return {
    headline: m.headline,
    steps: [
      "Start from the market, not the model: p = p_market + w × (p_model − p_market), where p_market is each bucket's mid normalised to sum to one. w is learned per view and checkpoint, and stays 0 until the model has beaten the market on settled days (at least 40).",
      ...m.steps,
    ],
    legs: [],
    refuses:
      `At w = 0 the view is the market's own price, and an ask sits above its mid with the fee on top, so it almost never has an edge: from 27 Sep 16:36Z to 8 Oct 06:36Z the six engine strategies decided 16,182 times and bought once. Its model-only twin, ${base}_model, trades these rules at w = 1 on its own $1,000 paper ledger; on this market it would ${m.legs.length ? `buy ${sideList(m.legs)}` : "take nothing too"}.`,
    gates: [
      { label: "anchor weight w", value: "0 — the market's numbers until the model earns weight", ok: false },
      { label: "on the model's numbers (w = 1)", value: m.legs.length ? sideList(m.legs) : "nothing", ok: m.legs.length > 0 },
    ],
    reads: `${m.reads} · market_anchor.py`,
  };
}

/** A model-only twin: its base's rules at w = 1 (engine_views.MODEL_ONLY). */
export function twinPlay(strategyId: string): Play | undefined {
  if (!strategyId.endsWith("_model")) return undefined;
  const base = strategyId.slice(0, -"_model".length);
  const m = MODEL[base];
  if (!m) return undefined;
  return {
    ...m,
    steps: [
      `The same rules as ${base}, on the model's own numbers: the market anchor at w = 1, recorded on every decision as model-only:w1. It trades its own $1,000 paper ledger only.`,
      ...m.steps,
    ],
    gates: [{ label: "anchor weight w", value: "1 — the model's own numbers", ok: true }, ...m.gates],
    reads: `${m.reads} · engine_views.MODEL_ONLY`,
  };
}

export const PLAYS: Record<string, Play> = {
  s1_buy_low_sell_signal: {
    headline: "One bucket, bought because the model and the market disagree by more than the round trip costs.",
    steps: [
      "Look at every bucket the venue will let you trade.",
      "Take the model's probability and subtract what the bucket costs. That difference is the edge.",
      "Charge the edge for the whole round trip — a fee and a spread on the way in, and again on the way out.",
      "What survives that is the trade. Here only 31–32 does: the model makes it 21% and it is priced at 15c.",
      "Sell when the model's number and the price converge, or when the day decides it.",
    ],
    legs: [{ i: MISPRICED, side: "YES" }],
    gates: [
      { label: "edge after fees", value: `+${cents(MARKET[MISPRICED].prob - MARKET[MISPRICED].ask)}`, ok: true },
      { label: "round-trip bar", value: cents(roundTrip(MARKET[MISPRICED].ask)), ok: true },
      { label: "beats the bar", value: `yes, by ${cents(MARKET[MISPRICED].prob - MARKET[MISPRICED].ask - roundTrip(MARKET[MISPRICED].ask))}`, ok: true },
      { label: "book is tradeable", value: "yes", ok: true },
    ],
    reads: "scripts/strategies/s1_buy_low_sell_signal.py · cost_model.round_trip_cost",
  },

  s2_combination_arb: {
    headline: "Buy every bucket at once. Exactly one pays $1.00, so if the set costs less than a dollar the profit is arithmetic.",
    steps: [
      "Add up the asking price of all eleven buckets, fees included.",
      "Exactly one of them settles at $1.00 and the other ten at zero — that is guaranteed, not forecast.",
      "If the whole set costs less than $1.00, buying it all is riskless. It needs no weather model at all.",
      `Here the set costs ${money(allInOf(YES_ALL))} with fees. That is ${cents(allInOf(YES_ALL) - 1)} above the payout, so there is nothing to take.`,
      "A bucket the day has walked away from is not an obstacle — at 2c it is the part that makes the set cheap.",
    ],
    legs: [],
    refuses:
      `The eleven buckets cost ${money(allInOf(YES_ALL))} together, fees included, and pay $1.00. No arbitrage. That is the ordinary answer on a venue-listed ladder, and it is the answer this strategy is for — it is the only one that can say so with certainty rather than opinion.`,
    gates: [
      { label: "basket complete", value: "11 of 11 priced", ok: true },
      { label: "cost including fees", value: money(allInOf(YES_ALL)), ok: false },
      { label: "guaranteed payout", value: "$1.00", ok: true },
      { label: "riskless", value: `no — costs ${cents(allInOf(YES_ALL) - 1)} more than it pays`, ok: false },
    ],
    reads: "scripts/strategies/s2_combination_arb.py · needs a book on every leg, not an edge on every leg",
  },

  s3_concentration: {
    headline: "When this city's forecast is measurably accurate, buy the cluster of buckets around where it points.",
    steps: [
      "First check the city, not the market: is this forecast historically accurate to under one bucket width?",
      "Find the bucket holding the most probability — here 30–31, at 30%.",
      "Take the window two either side of it and buy the ones that are actually cheap.",
      "Only 31–32 clears: the rest of the window is priced at or above what the model makes it.",
      "Exit if the regime degrades — a forecast that stops being sharp stops justifying the concentration.",
    ],
    legs: [{ i: MISPRICED, side: "YES" }],
    gates: [
      { label: "city forecast error", value: "under 1 bucket", ok: true },
      { label: "regime", value: "SHARP", ok: true },
      { label: "window", value: "28–29 through 32–33", ok: true },
      { label: "priced under the model", value: "1 of 5 in the window", ok: true },
    ],
    reads: "scripts/strategies/s3_concentration.py · gated on derived_forecast_skill.mae_bands",
  },

  s4_tail_fade: {
    headline: "Sell the outer buckets. Not because they rarely win — because the fee there is nearly zero.",
    steps: [
      "The venue charges 5% x p x (1 − p) a share. That peaks near 50c and collapses at both ends.",
      "Selling the ≥35 bucket means buying its NO at 97c, where the fee is a seventh of a cent.",
      "The only sourced claim is that cost advantage. It is NOT a claim about how often the tails lose.",
      "So the shape is small frequent gains and rare large losses, and it is capped hard for that reason.",
      "It never runs in an UNCERTAIN or BLOCKED regime, where the distribution is already widened.",
    ],
    legs: [
      { i: 0, side: "NO" },
      { i: MARKET.length - 1, side: "NO" },
    ],
    gates: [
      { label: "outer buckets", value: "the two open tails", ok: true },
      { label: "fee at 97c", value: `${(fee(0.97) * 100).toFixed(2)}c a share`, ok: true },
      { label: "fee at the centre", value: `${(fee(0.30) * 100).toFixed(2)}c a share`, ok: true },
      { label: "regime", value: "not UNCERTAIN, not BLOCKED", ok: true },
    ],
    reads: "scripts/strategies/s4_tail_fade.py · the fee curve in cost_model.taker_fee",
  },

  s5_running_max_lock: {
    headline: "Wait until the day is provably over, then buy the bucket holding the maximum while it is still cheap.",
    steps: [
      "This one does not forecast. It waits.",
      "The day is decided when the temperature has fallen far enough, for long enough, that the remaining daylight cannot recover it.",
      "At that moment the bucket containing the day's running maximum is the winner — the outcome is settled, not predicted.",
      "If the market has not caught up, buy it. Here the maximum landed in 30–31 and it is still priced at 30c.",
      "There is no exit rule and that is deliberate: the outcome is already known, so it is held to settlement.",
    ],
    legs: [{ i: CENTRE, side: "YES" }],
    gates: [
      { label: "day decided", value: "yes", ok: true },
      { label: "maximum rests on", value: "a series of readings, not one point", ok: true },
      { label: "seasonal window", value: "under 12h — a peak exists to be past", ok: true },
      { label: "price", value: "30c, under the 90c ceiling", ok: true },
    ],
    reads: "scripts/strategies/s5_running_max_lock.py · day_decided from sql/ad4_26_temp_trend.sql",
  },

  s6_anchor_insurance: {
    headline: "Buy the likely bucket and its neighbours together, sized so that whichever of them wins, the payout covers all of it.",
    steps: [
      "Pick the anchor — the bucket with the best edge. Here 31–32.",
      "Take the window two either side and add up what the whole covered set costs.",
      "Exactly one bucket wins and pays $1.00, and the cost is the same whichever of the covered ones it is.",
      "So the rule is one inequality: if the covered set costs under $1.00 after fees, every covered outcome profits.",
      `Here the five cost ${money(allInOf(COVERED))} together with fees. Any of them winning returns $1.00. Miss the set entirely and that stake is gone.`,
    ],
    legs: COVERED,
    gates: [
      { label: "covered set", value: "29–30 through 33–34", ok: true },
      { label: "cost including fees", value: money(allInOf(COVERED)), ok: allInOf(COVERED) < 1 },
      { label: "payout if any of them wins", value: "$1.00", ok: true },
      { label: "covered probability", value: pct(probOf(COVERED)), ok: true },
    ],
    reads: "scripts/strategies/s6_anchor_insurance.py · the hard invariant is sum(ask) < 1 after fees",
  },

  s7_pre_peak_gradient: {
    headline: "In the hour before the peak, trade the DIRECTION the temperature is moving, not the level it has reached.",
    steps: [
      "30.2C an hour before peak means nothing on its own.",
      "After 28.9 / 29.6 / 30.2 it is still climbing, and the bucket above is live and usually still cheap.",
      "After 31.1 / 30.8 / 30.2 it has rolled over, and the buckets above are dead and the market has not always noticed.",
      "The slope is fitted over the real timestamps of the last three readings, because METAR is hourly and SPECIs are not.",
      "Then add how much this city HISTORICALLY still climbs from this hour — +0.9C/h at 13:00 is ordinary in Phoenix and remarkable in Seattle.",
    ],
    legs: [{ i: MISPRICED, side: "YES" }],
    trace: [
      { at: "−3h", c: 27.4 },
      { at: "−2h", c: 28.9 },
      { at: "−1h", c: 29.6 },
      { at: "now", c: 30.2 },
    ],
    gates: [
      { label: "inside the entry window", value: "40 min to peak", ok: true },
      { label: "reading age", value: "12 min — the hard gate is 90", ok: true },
      { label: "slope over 3 readings", value: "+0.62 C/h, still climbing", ok: true },
      { label: "implied maximum", value: "31.4C — inside 31–32", ok: true },
    ],
    reads: "scripts/strategies/s7_pre_peak_gradient.py · slope and implied max from sql/ad4_26_temp_trend.sql",
  },

  s8_two_bucket_cover: {
    headline: "Buy the two most likely ADJACENT buckets when the pair is cheap enough that either one landing pays.",
    steps: [
      `Find the best adjacent pair. Here 30–31 and 31–32, which together cost ${cents(allInOf(PAIR))} and carry ${pct(probOf(PAIR))} of the probability.`,
      `${cents(allInOf(PAIR))} returning $1.00 is ${pct(1 / allInOf(PAIR) - 1)} on the stake, and it lands whenever the day finishes anywhere in a two-bucket span.`,
      "But cheap is a trap: two buckets are cheap usually because the market thinks the day lands elsewhere.",
      "So the pair's probability has to beat 72% — comfortably above what it costs, with room for the fee.",
      `${pct(probOf(PAIR))} does not. Paying ${cents(allInOf(PAIR))} for a ${pct(probOf(PAIR))} pair is a losing trade executed tidily, and it is refused.`,
    ],
    legs: [],
    refuses:
      `The price gate passes and the probability gate does not — ${pct(probOf(PAIR))} against a 72% bar. That is the gate doing its job. On the live board on 21 Sep the cost gate passed 127 times and the probability gate zero times, best pair 70.3%.`,
    gates: [
      { label: "pair cost including fees", value: `${cents(allInOf(PAIR))} — under the 70c cap`, ok: allInOf(PAIR) < 0.7 },
      { label: "adjacent", value: "yes", ok: true },
      { label: "contains where the day is heading", value: "yes", ok: true },
      { label: "pair probability", value: `${pct(probOf(PAIR))} — the bar is 72%`, ok: probOf(PAIR) >= 0.72 },
    ],
    reads: "scripts/strategies/s8_two_bucket_cover.py · four gates, and cost is only the first",
  },

  s9_ladder_basket: {
    headline: "Buy the contiguous run of buckets with the best expected return per dollar, once it clears the floor.",
    steps: [
      "Try every unbroken run of buckets — pairs, triples, and longer.",
      "For each, the cost is the sum of the asks and the payout is $1.00 if the day lands anywhere inside it.",
      "Score them by expected return per dollar, not by probability: a run that is 90% likely and costs 95c is a bad trade.",
      `Here 29–30 through 31–32 costs ${cents(allInOf(RUN))} and carries ${pct(probOf(RUN))}, which is the best on the board.`,
      "The run must also contain where the day is actually heading, or it is a bet on the model alone.",
    ],
    legs: RUN,
    gates: [
      { label: "run", value: "29–30 through 31–32", ok: true },
      { label: "cost including fees", value: money(allInOf(RUN)), ok: true },
      { label: "probability it lands inside", value: pct(probOf(RUN)), ok: true },
      { label: "expected return per dollar",
        value: `${cents((probOf(RUN) - allInOf(RUN)) / allInOf(RUN))}`,
        ok: probOf(RUN) > allInOf(RUN) },
    ],
    reads: "scripts/strategies/s9_ladder_basket.py · scored on EV per dollar, not on probability",
  },

  s10_winner: { ...anchored("s10_winner") },
  s10_growth: { ...anchored("s10_growth") },
  s10_lock: { ...anchored("s10_lock") },
  s11_ladder: { ...anchored("s11_ladder") },
  s11_lock: { ...anchored("s11_lock") },
  s12_no: { ...anchored("s12_no") },
};

// --------------------------------------------------------------------------
// Settling the position for real.
// --------------------------------------------------------------------------
function settle(legs: Leg[], landed: number) {
  let cost = 0, payout = 0, fees = 0;
  for (const leg of legs) {
    const b = MARKET[leg.i];
    const price = leg.side === "YES" ? b.ask : 1 - b.ask;
    cost += price;
    fees += fee(price);
    const won = leg.side === "YES" ? leg.i === landed : leg.i !== landed;
    if (won) payout += 1;
  }
  return { cost, payout, fees, profit: payout - cost - fees };
}

// --------------------------------------------------------------------------
// The ladder.
// --------------------------------------------------------------------------
function Ladder({
  legs, landed, onLand,
}: { legs: Leg[]; landed: number | null; onLand: (i: number) => void }) {
  const bySide = new Map(legs.map((l) => [l.i, l.side]));
  const widest = Math.max(...MARKET.map((b) => b.ask));
  return (
    <div className="space-y-px" role="group" aria-label="The eleven buckets of one market">
      {MARKET.map((b, i) => {
        const side = bySide.get(i);
        const isLanded = landed === i;
        const won = side === "YES" ? isLanded : side === "NO" ? landed !== null && !isLanded : false;
        return (
          <button
            key={b.label}
            onClick={() => onLand(i)}
            aria-pressed={isLanded}
            title={`Suppose the day lands in ${b.label}`}
            className={`flex w-full items-center gap-2 rounded px-1.5 py-0.5 text-left transition ${
              isLanded ? "bg-accent/15 ring-1 ring-accent" : "hover:bg-panel2"
            }`}
          >
            <span className={`w-14 shrink-0 font-mono text-[10px] ${isLanded ? "text-text" : "text-muted"}`}>
              {b.label}
            </span>

            {/* the ask, as a bar. One hue, length is the magnitude. */}
            <span className="relative h-3 flex-1 overflow-hidden rounded-sm bg-panel2">
              <span
                className={`absolute inset-y-0 left-0 rounded-sm ${side ? "bg-accent" : "bg-border"}`}
                style={{ width: `${(b.ask / widest) * 100}%` }}
              />
              {/* the model's probability, as a tick - the thing the price is judged against */}
              <span
                className="absolute inset-y-0 w-px bg-text/70"
                style={{ left: `${(b.prob / widest) * 100}%` }}
                aria-hidden
              />
            </span>

            <span className="w-8 shrink-0 text-right font-mono text-[10px] text-muted">{cents(b.ask)}</span>

            {/* identity is never colour alone */}
            <span className="w-16 shrink-0 text-right font-mono text-[10px]">
              {side ? (
                <span className="text-accent">{side === "YES" ? "buys" : "sells"}</span>
              ) : (
                <span className="text-muted/40">—</span>
              )}
            </span>
            <span className="w-10 shrink-0 text-right font-mono text-[10px]">
              {isLanded && <span className="text-text">lands</span>}
              {!isLanded && side && landed !== null && (
                <span className={won ? "text-good" : "text-bad"}>{won ? "pays" : "0"}</span>
              )}
            </span>
          </button>
        );
      })}
      <div className="flex items-center gap-2 px-1.5 pt-1 text-[10px] text-muted">
        <span className="w-14 shrink-0">bar</span>
        <span>what the bucket costs · the thin tick is what the model says it is worth · click any row to settle the day there</span>
      </div>
    </div>
  );
}

function Trace({ points }: { points: { at: string; c: number }[] }) {
  const lo = Math.min(...points.map((p) => p.c)) - 0.4;
  const hi = Math.max(...points.map((p) => p.c)) + 1.6;
  const x = (i: number) => 6 + (i * 88) / (points.length - 1);
  const y = (c: number) => 34 - ((c - lo) / (hi - lo)) * 28;
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.c)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <div className="rounded border border-border bg-panel2/40 p-2">
      <svg viewBox="0 0 100 40" className="h-16 w-full" role="img"
           aria-label={`Temperature climbing to ${last.c} degrees, implied maximum 31.4`}>
        <line x1="6" y1={y(31.4)} x2="94" y2={y(31.4)} className="stroke-muted" strokeDasharray="2 2" strokeWidth="0.5" />
        <path d={d} fill="none" className="stroke-accent" strokeWidth="1.5" strokeLinejoin="round" />
        {points.map((p, i) => (
          <circle key={p.at} cx={x(i)} cy={y(p.c)} r="1.4" className="fill-accent" />
        ))}
        <text x="94" y={y(31.4) - 1.5} textAnchor="end" className="fill-muted" fontSize="4">
          implied max 31.4
        </text>
      </svg>
      <div className="flex justify-between font-mono text-[9px] text-muted">
        {points.map((p) => (
          <span key={p.at}>{p.at} · {p.c.toFixed(1)}</span>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
export function StrategyExplainer({ strategyId }: { strategyId: string }) {
  const play = PLAYS[strategyId] ?? twinPlay(strategyId);
  const [landed, setLanded] = useState<number | null>(null);
  const outcome = useMemo(
    () => (play && landed !== null ? settle(play.legs, landed) : null),
    [play, landed]
  );

  if (!play) {
    return (
      <p className="px-3 py-2 text-[11px] text-muted">
        No walkthrough is written for <code>{strategyId}</code> yet. Add one to{" "}
        <code>web/components/StrategyExplainer.tsx</code> beside the others.
      </p>
    );
  }

  const stake = sum(play.legs.map((l) => (l.side === "YES" ? MARKET[l.i].ask : 1 - MARKET[l.i].ask)));

  return (
    <div className="space-y-3 border-t border-border bg-panel2/20 px-3 py-3 text-[11px]">
      <p className="max-w-3xl leading-relaxed text-text">{play.headline}</p>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* ---- how it works ------------------------------------------- */}
        <div className="space-y-2">
          <h4 className="text-[10px] uppercase tracking-wide text-muted">How it works</h4>
          <ol className="space-y-1.5">
            {play.steps.map((s, i) => (
              <li key={i} className="flex gap-2 leading-relaxed text-muted">
                <span className="mt-px w-3 shrink-0 text-right font-mono text-[10px] text-accent">{i + 1}</span>
                <span>{s}</span>
              </li>
            ))}
          </ol>

          <h4 className="pt-1 text-[10px] uppercase tracking-wide text-muted">What it checks</h4>
          <ul className="space-y-0.5">
            {play.gates.map((g) => (
              <li key={g.label} className="flex items-baseline justify-between gap-3">
                <span className="text-muted">
                  <span className={g.ok ? "text-good" : "text-bad"} aria-hidden>{g.ok ? "✓" : "✕"}</span>
                  <span className="sr-only">{g.ok ? "passes" : "fails"}</span> {g.label}
                </span>
                <span className={`shrink-0 font-mono ${g.ok ? "text-muted" : "text-bad"}`}>{g.value}</span>
              </li>
            ))}
          </ul>
          <p className="pt-1 font-mono text-[10px] leading-relaxed text-muted/70">{play.reads}</p>
        </div>

        {/* ---- the market, and what happens on it --------------------- */}
        <div className="space-y-2">
          <h4 className="flex items-baseline justify-between text-[10px] uppercase tracking-wide text-muted">
            <span>One market, eleven buckets</span>
            <span className="normal-case tracking-normal text-muted/70">a worked example, not live</span>
          </h4>

          {play.trace && <Trace points={play.trace} />}

          <Ladder legs={play.legs} landed={landed} onLand={(i) => setLanded(landed === i ? null : i)} />

          {play.refuses ? (
            <div className="rounded border border-warn/40 bg-warn/5 p-2 leading-relaxed text-warn">
              <strong className="font-semibold">It takes nothing here.</strong> {play.refuses}
            </div>
          ) : (
            <div className="rounded border border-border bg-panel p-2">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 font-mono text-[10px]">
                <span className="text-muted">
                  stake <span className="text-text">{money(stake)}</span> a share
                </span>
                <span className="text-muted">
                  {play.legs.length} leg{play.legs.length === 1 ? "" : "s"}
                </span>
                {outcome ? (
                  <>
                    <span className="text-muted">
                      pays <span className="text-text">{money(outcome.payout)}</span>
                    </span>
                    <span className="text-muted">
                      fee <span className="text-text">{money(outcome.fees)}</span>
                    </span>
                    <span className={outcome.profit >= 0 ? "text-good" : "text-bad"}>
                      {outcome.profit >= 0 ? "profit" : "loss"} {money(Math.abs(outcome.profit))} a share
                    </span>
                  </>
                ) : (
                  <span className="text-muted/70">click a bucket to settle the day there</span>
                )}
              </div>
              {outcome && (
                <p className="mt-1 leading-relaxed text-muted">
                  The day finished in <span className="font-mono text-text">{MARKET[landed!].label}</span>.{" "}
                  {outcome.payout > 0
                    ? `${outcome.payout === 1 ? "One leg" : `${outcome.payout} legs`} settled at $1.00 against ${money(stake)} staked.`
                    : "No leg settled in the money, so the whole stake is lost — this is the case the gates exist to make rare, not impossible."}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
