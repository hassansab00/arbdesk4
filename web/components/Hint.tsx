"use client";

/**
 * A one-line explanation attached to an input, so a number can be typed by
 * someone who is not the person who named it.
 *
 * WHY A COMPONENT AND NOT JUST title=. Half this desk's inputs had a label and
 * nothing else - "Maximum plan cost (USD)" tells you the units and not the
 * consequence, and "Minimum net edge per share" is a sentence only its author
 * could act on. A bare `title` attribute is also invisible: there is nothing
 * on screen to say an explanation exists, so nobody hovers.
 *
 * This renders a small marker that SAYS there is more, carries the text for a
 * mouse (title) and for a screen reader (aria-label), and keeps every hint in
 * one place so they can be checked for existence rather than hoped for.
 */
export default function Hint({ text }: { text: string }) {
  return (
    <span
      className="ml-1 inline-flex h-3.5 w-3.5 shrink-0 cursor-help select-none items-center
                 justify-center rounded-full border border-border align-middle text-[9px]
                 leading-none text-muted hover:border-accent hover:text-accent"
      title={text}
      aria-label={text}
      role="note"
    >
      ?
    </span>
  );
}

/**
 * Every hint on the paper desk, in one object.
 *
 * Keeping them together is what makes "does each input explain itself" a
 * question with an answer: the test asserts that each key here is used, and
 * that each input that needs one has one.
 */
export const PAPER_HINTS = {
  // ---- creating a desk --------------------------------------------------
  name:
    "Just a label for you. Name it after what makes it different - " +
    "'Austin only, tight edge' beats 'Desk 2' when you come back in a month.",
  starting_cash:
    "The paper bankroll this desk starts with. It is not real money and not " +
    "shared: every desk has its own, so one desk running out does not stop " +
    "another. Used as the baseline for P&L, so changing it later would " +
    "rewrite the scoreboard - Reset does that deliberately, editing does not.",
  mode:
    "Manual: it only does what you type on a ticket. Assisted: strategies " +
    "propose trades and you approve each one. Automatic: it places them " +
    "itself, inside the limits below. Every desk is created PAUSED whatever " +
    "you pick here, so nothing trades until you press Start.",

  // ---- the policy -------------------------------------------------------
  max_plan_usd:
    "The most this desk may commit to any ONE proposal, fees included. It " +
    "caps a single mistake. A proposal that would cost more is blocked and " +
    "says so, rather than being trimmed to fit.",
  max_exposure_usd:
    "The most this desk may have at risk ACROSS everything at once - open " +
    "positions plus cash reserved by queued orders. It caps a bad day. New " +
    "entries stop when this is reached; exits always stay available.",
  min_edge:
    "How mispriced a band has to be before this desk will touch it, per " +
    "share, after fees. 0.03 means it needs three cents of edge on a " +
    "one-dollar contract. Higher means fewer and better trades; too high " +
    "means none at all.",
  strategies:
    "Which strategies may propose trades on THIS desk. A strategy disabled " +
    "globally cannot fire here however it is ticked - the tick is this " +
    "desk's permission, not the strategy's on switch.",
  cities:
    "Which cities this desk may trade. 'All cities' follows the roster, so a " +
    "retired city drops out on its own. Naming cities individually is how " +
    "you test one market without the rest.",
  paused:
    "Stops this desk OPENING anything new. It does not close what is already " +
    "open and never blocks an exit. This is the switch to reach for first if " +
    "something looks wrong.",

  // ---- the manual ticket ------------------------------------------------
  band:
    "The contract you are buying: one temperature band, for one city, on one " +
    "day. Only bands with a market currently open are listed.",
  side:
    "YES pays $1 if the day's maximum lands inside this band. NO pays $1 if " +
    "it does not. You are buying one of them at the price shown.",
  shares:
    "How many contracts. Each settles at $1 or $0, so shares are also your " +
    "maximum payout in dollars.",
  limit:
    "The worst price per share you will accept. The order walks the real ask " +
    "ladder and stops here - it will fill partially rather than pay more.",
  ceiling:
    "A hard stop on the total, fees included. Belt and braces with the limit " +
    "price: this one is what actually leaves the account.",
  reason:
    "Why you are taking this trade. Stored with the fill, so when it settles " +
    "you can tell a good decision from a lucky one.",

  // ---- lifecycle --------------------------------------------------------
  archive:
    "Takes this desk off the list and stops it trading. NOTHING IS DELETED - " +
    "its trades, orders and history stay exactly where they are, and " +
    "Restore brings it back with all of it. Use it to keep the switcher " +
    "readable, not to throw anything away.",
  restore:
    "Puts this desk back on the list, still paused, with all of its history.",
  reset:
    "Returns the cash to its starting figure and clears positions. This one " +
    "DOES rewrite the scoreboard, so it asks first.",
} as const;
