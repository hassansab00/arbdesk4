import React from 'react';

/** A reading guide only. Original calls, scoring and model selection are unchanged. */
export default function PredictiveAccuracyGuide() {
  return <section aria-label="Accuracy source of truth" className="mt-3 rounded border border-accent/40 bg-panel p-3 text-xs">
    <h2 className="font-semibold">Accuracy · start here</h2>
    <p className="mt-1 text-muted">
      Use <a href="#predictive-accuracy" className="text-accent underline">City accuracy</a>,
      {' '}<b>All served predictors</b> and one checkpoint for the platform&rsquo;s winning-bucket record.
      Inspect the original pick and confirmed winner in <a href="#predictive-results" className="text-accent underline">Was it right?</a>.
      Hit rate is correct / scored calls; pending results are excluded. Later versioned captures do not replace the first call.
    </p>
    <p className="mt-1 text-muted">
      <b>Likely / claimed</b> is forecast confidence, not historical accuracy.
      Compare the platform and market on the <b>same days and checkpoint</b> using the paired counts.
      Reports show their scoring time; recent-window, full-history and paired samples can have different denominators.
    </p>
    <details className="mt-2">
      <summary className="cursor-pointer text-accent">Which model and which percentage am I seeing?</summary>
      <div className="mt-2 overflow-x-auto"><table className="w-full text-left">
        <thead className="text-muted"><tr><th className="py-1 pr-3">Display</th><th>Meaning</th></tr></thead>
        <tbody>
          <tr className="border-t border-border"><th className="py-1 pr-3 font-normal">Platform / served engine</th><td>The forecast actually selected for that city and checkpoint, including any baseline fallback.</td></tr>
          <tr className="border-t border-border"><th className="py-1 pr-3 font-normal">WXPredict v3 / wxrd3</th><td>One predictor with five seed fits. Served v3 counts calls that used it; raw paired v3 is its saved uncalibrated comparison. Its calls can also appear in the platform record.</td></tr>
          <tr className="border-t border-border"><th className="py-1 pr-3 font-normal">WXPredict v2 / wxrd-v2; S10</th><td>Separate evaluation predictors. Their scores do not replace the platform&rsquo;s served-call score. Check <a href="#predictive-predictors" className="text-accent underline">Predictors</a> for each version&rsquo;s current role.</td></tr>
          <tr className="border-t border-border"><th className="py-1 pr-3 font-normal">Desk model · station comparison</th><td>A separate per-city weather model, with its own promotion state shown beside it.</td></tr>
          <tr className="border-t border-border"><th className="py-1 pr-3 font-normal">Market</th><td>The favourite from the saved book marks at the call, not the closing price or a second desk model.</td></tr>
          <tr className="border-t border-border"><th className="py-1 pr-3 font-normal">Raw weather scorecard</th><td>Temperature-error diagnostics for weather inputs. Same whole °C compares integer-degree Celsius intervals; it is not settlement-bucket accuracy. Verified actuals shows how many stored observations match verified weather outcomes.</td></tr>
        </tbody>
      </table></div>
      <p className="mt-2 text-muted">Scheduled checkpoint names do not prove a capture preceded the actual maximum.
        {' '}City accuracy&rsquo;s <b>Before expected peak · verified</b> filter requires the saved timing evidence;
        legacy calls without receipts and calls without a saved peak remain excluded. The day-ahead archive has no server freeze receipts.
        {' '}Overall pools checkpoints on complete dates; it is not one independent prediction per city-day.</p>
      <p className="mt-1 text-muted">Brier and log loss score the full probability distribution; lower is better.
        {' '}Reliability error is the weighted absolute confidence error; the signed calibration gap is actual hit rate minus claimed confidence.
        {' '}Temperature-error signs are labelled by panel: frozen-call errors use actual − predicted; raw forecast bias uses forecast − actual.</p>
    </details>
  </section>;
}
