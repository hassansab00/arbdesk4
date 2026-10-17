# Primary preparation allowance — 17 Oct

Cold input loading could exhaust the former primary allowance before any
vector was built. The upper bound is now 40 seconds. The tick passes its
actual remaining allowance after reserving 30 seconds for downstream pricing,
books and freezing. If less than the 25-second preparation minimum remains,
it retains the baseline before starting expensive work. Intraday preparation
keeps the bounded upper allowance. These are operational priors, not accuracy
claims. Model weights, probability math, calibration gates and frozen calls
are unchanged. Tests cover early fallback and the tick's deadline wiring.
