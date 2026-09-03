/**
 * What a signal actually means, in words.
 *
 * The panel used to print `strategy_id` as the headline and the raw reason
 * code as the body, which for the only signal type currently firing reads:
 *
 *     system
 *     critical
 *     implausible_edge_anomaly
 *
 * Three fields, none of which say which city, which band, what happened, or
 * what to do about it. The information was all there - a signal carries
 * band_id, price, probability and edge - it just was not shown.
 *
 * Every reason code the engine can emit is translated here. An unknown code
 * degrades to its own text with the underscores removed rather than being
 * hidden, so a new strategy is legible the day it ships.
 */

export interface SignalMeaning {
  /** One line: what happened. */
  headline: string;
  /** Two or three: why it fired and what it implies. */
  detail: string;
  /** What the reader should do. */
  action: string;
  /** false for alerts that are about the desk, not about a trade. */
  tradeable: boolean;
}

export function signalMeaning(reason: string, action: string): SignalMeaning {
  const job = /^job_stale:([^:]+):([\d.]+)h/.exec(reason);
  if (job) {
    return {
      headline: `${job[1]} has stopped writing`,
      detail:
        `Nothing from this job for ${Number(job[2]).toFixed(1)} hours. Every page that ` +
        `reads what it writes is showing old numbers, not wrong ones — which is worse, ` +
        `because they still look current.`,
      action: "Check GitHub → Actions and the Workflows page for its last run.",
      tradeable: false,
    };
  }

  switch (reason) {
    case "implausible_edge_anomaly":
      return {
        headline: "Edge too large to believe",
        detail:
          "The model thinks this band is mispriced by more than any real market " +
          "usually is. Nearly always a stale quote or a forecast that moved after " +
          "the book was snapshotted — not free money.",
        action: "Open the band, check the book age and the forecast run before acting.",
        tradeable: false,
      };
    case "combination_arb_fee_inclusive_guaranteed_payoff":
      return {
        headline: "Bands priced below a guaranteed payoff",
        detail:
          "The bands of this market can be bought as a set for less than the $1 " +
          "exactly one of them pays — after fees. The profit does not depend on " +
          "the weather at all.",
        action: "Size to the thinnest leg. It is only arbitrage if every leg fills.",
        tradeable: true,
      };
    case "concentration_around_forecast_centre":
      return {
        headline: "Market too flat around the forecast",
        detail:
          "The model puts far more probability on the middle bands than the book " +
          "does. This is the ordinary weather-edge trade: the desk believes the " +
          "forecast more than the market does.",
        action: "Check the forecast's own confidence before sizing — it is the whole thesis.",
        tradeable: true,
      };
    case "tail_fade_cheap_to_transact_near_extreme":
      return {
        headline: "Tail band priced above its odds",
        detail:
          "An outer band is dearer than the model's probability, and cheap enough " +
          "to transact against. Fading a tail is high hit-rate and small-payoff — " +
          "one wrong extreme costs several right ones.",
        action: "Respect the position cap. This strategy loses badly when it loses.",
        tradeable: true,
      };
    case "running_max_locked_band_still_cheap":
      return {
        headline: "Day's maximum already locked, band still cheap",
        detail:
          "The observed maximum has passed the peak window and settled inside this " +
          "band, but the band is not priced as a near-certainty yet. The weather " +
          "risk is largely spent; what remains is mostly settlement risk.",
        action: "Confirm the peak window has actually passed in the CITY's local time.",
        tradeable: true,
      };
    case "anchor_insurance_covered_basket":
      return {
        headline: "Anchor position wants its hedge",
        detail:
          "A concentrated position on the centre band, with the neighbouring bands " +
          "bought as insurance. The basket caps the loss if the day moves.",
        action: "Both legs or neither — an uninsured anchor is a different trade.",
        tradeable: true,
      };
    case "regime_degraded":
      return {
        headline: "Exit: the forecast regime turned",
        detail:
          "Conditions that justified this position no longer hold — the model's " +
          "confidence for this city-day has dropped since entry.",
        action: "Exit while the book is still there.",
        tradeable: true,
      };
    case "regime_or_liquidity_degraded":
      return {
        headline: "Exit: regime or liquidity turned",
        detail:
          "Either the forecast confidence dropped or the book thinned to where " +
          "exiting later would cost more than the remaining edge is worth.",
        action: "Exit while the book is still there.",
        tradeable: true,
      };
    default:
      return {
        headline: reason.replace(/_/g, " "),
        detail:
          "This reason code has no description yet. The numbers below are still " +
          "the engine's own, so they can be read directly.",
        action: action === "ENTER" ? "Review before approving." : "Review.",
        tradeable: action !== "ALERT",
      };
  }
}
