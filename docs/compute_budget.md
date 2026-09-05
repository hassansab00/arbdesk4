# Two schedulers, one desk

AD4 runs jobs in two places and the split is not arbitrary. It is also where the
waste came from, so it is worth stating plainly.

## The rule

| | n8n | GitHub Actions |
|---|---|---|
| **Shape of work** | call an API, write rows | read rows, compute, write rows |
| **Costs** | one execution, ~2,000/month | **one billed minute, minimum**, 2,000/month private |
| **Runtime** | HTTP + a little JavaScript | Python, pandas-free but real |
| **Good at** | polling something external, on a schedule | anything with arithmetic in it |
| **Bad at** | a regression, a Monte Carlo, a backtest | being run often |

The dividing line: **fetching is n8n's, thinking is Actions'.**

The billing difference is what makes it matter. An n8n execution that fetches
54 cities and writes 250 rows costs *one execution*. A GitHub Action that does
the same thing costs **a full billed minute** — GitHub rounds up and charges a
minimum of one minute per job, so a five-second job and a fifty-second job cost
exactly the same. That is why a job which polls and usually finds nothing is the
most expensive possible thing to run there.

## Who owns what

| Job | Where | Why there |
|---|---|---|
| Market discovery, book, trades, rules (P0.2–P0.5) | n8n | Polymarket HTTP → rows |
| Weather alerts (P1.1) | n8n | webhook → email |
| NWS observations, forecast, gridpoint (P1.2–P1.4) | n8n | weather.gov HTTP → rows |
| Digests, watchdog (P3.1, P4.1) | n8n | read a view → email |
| IEM observations | Actions | a parser, not a fetch |
| Open-Meteo forecasts | Actions | reshapes 7 lead-times per city |
| Forecast skill | Actions | scored against outcomes |
| Probability + edge | Actions | the model |
| Signal engine | Actions | eight strategies, conflict resolution |
| Settlement | Actions | reads a page and adjudicates |
| Derived recompute | Actions | full-archive aggregates |
| Weather model / model forecast | Actions | a regression, and applying it |
| Backtest | Actions | replays months of book history |
| Archive observations | Actions | dumps, uploads, verifies, prunes |

## Where they genuinely overlap, and where they only looked like it

**Genuine duplication, now fixed.** `live_weather.yml` ran every 15 minutes
writing `live_weather`, and n8n **P1.2** writes the same table every 2 hours.
Two jobs writing one table on different cadences makes "which number is
current?" unanswerable, and P1.2 is strictly better — it reads api.weather.gov,
which is what these markets settle on, and since the series change it archives
the whole day rather than one reading. The Action keeps only its manual trigger,
as the fallback for when n8n is down or not yet imported.

**Not duplication, deliberate.** Two pairs look redundant and are not:

- IEM observations (Actions) and NWS observations (n8n P1.2) are *different
  sources for the same instant*. Having both is the entire evidence base for
  `docs/settlement_verification.md` — you cannot check whether the archive
  agrees with the settlement source using one feed.
- Open-Meteo forecasts (Actions) and NWS forecasts (n8n P1.3) are *different
  models for the same day*. Their disagreement is what widens sigma. One model
  produces a spread of zero and the mechanism is dormant.

## What the schedules cost now

Two workflows were 89% of the bill, and both were pollers:

| | before | after |
|---|---|---|
| `backtest.yml` | every 10 min — 4,320 runs, ~6,480 min | daily + on demand — 30 runs |
| `live_weather.yml` | every 15 min — 2,880 runs, ~4,320 min | manual only — 0 |
| `observations.yml` | every 2 h — 360 runs | every 6 h — 120 runs |
| everything else | unchanged | unchanged, with pip cached |
| **total** | **~12,100 min** | **~800 min** |

`pip install` now restores from cache on every workflow, which is worth about
25 seconds a run — around 300 minutes a month on its own.

A backtest is something you *ask for*, so it no longer polls for one. Queue it
in AD4 → Backtest, then either press **Run workflow**, or fire it instantly from
anything holding a token:

```bash
curl -X POST -H "Authorization: Bearer $GH_PAT" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/OWNER/REPO/dispatches \
     -d '{"event_type":"backtest-queued"}'
```

A daily sweep still picks up anything left queued, so nothing is forgotten.

## Is every Action necessary? One line each

Sixteen workflow files. Thirteen run on a schedule, three do not. Counts are
runs per month; GitHub bills a **minimum of one minute per job**, rounded up, so
runs and minutes are close to the same number for the short ones.

| Action | Runs/mo | Necessary? |
|---|---|---|
| **Observations** (IEM METAR) | 120 | **Yes.** The second temperature source. Without it there is nothing to check the settlement source against, and `docs/settlement_verification.md` has no evidence base. |
| **Forecasts** (Open-Meteo) | 30 | **Yes.** The second forecast model. Its disagreement with NWS is what widens sigma — with one model the spread is zero and the whole mechanism is dormant. |
| **Measure Forecast Skill** | 30 | **Yes.** Scores every model against what happened. This is the number that decides whether AD4's forecast is worth trading on. |
| **Probability + Edge Pipeline** | 120 | **Yes.** The model. Nothing downstream exists without it. |
| **Signal Engine** | 120 | **Yes.** Turns edge into signals. Cheap while every strategy is disabled, and it is what you turn on first. |
| **Settlement Sweep** | 30 | **Yes.** A day that never settles never enters the record, and the record is the only durable asset here. |
| **Data Bank** | 30 | **Yes.** Freezes what was predicted against what happened. Every improvement is measured against it, and it cannot be reconstructed later. |
| **Derived Recompute** | 30 | **Yes.** Refreshes the caches every page load reads. Skip it and the UI goes back to timing out. |
| **Weather Model** | 4 | **Yes,** and weekly is right — a regression on 120+ days barely moves day to day. |
| **Model Forecast** | 120 | **Yes.** Applies the fitted model forward. Separate from the fit because predicting is cheap and fitting is not. |
| **Archive Observations** | 1 | **Yes, monthly.** This is what keeps the database inside the free tier. |
| **Backtest** | 30 | **Kept, but it no longer polls.** It used to run every 10 minutes looking for queued work — 4,320 runs, and 54% of the entire bill. A backtest is something you ask for. |
| **Live Weather Monitor** | 0 | **No — superseded, kept as a fallback.** n8n P1.2 does this better and far cheaper. Manual trigger only. Use it if n8n is down. |
| **Tests** | on push | Not a data job. Free-ish and it is what stops a broken script reaching a schedule. |
| **Web build** | on push | Not a data job. Vercel's build conditions. |
| **Verify Resolution Source** | manual | An audit you run when you want it, not a schedule. |

So: **twelve are load-bearing, one is monthly, one is on demand, one is a
fallback, three are not data jobs at all.** Nothing here is redundant with n8n —
see "Not duplication, deliberate" above for the two pairs that look like they
are.

If you need to cut further, the honest order is: Signal Engine and Model
Forecast from 4×/day to 2×/day (−120 runs), then Backtest to weekly (−26). Do
not cut Observations, Settlement or Data Bank — those three write history that
cannot be recovered afterwards.

## The option that removes the limit entirely

**GitHub Actions minutes are free and unlimited on public repositories.** The
2,000-minute allowance only applies to private ones.

Nothing secret is committed to this repo — every Supabase key lives in GitHub
Secrets (which stay private on a public repo), every n8n `Config` node ships
blank, and `scripts/sanitise_n8n_export.py` exists to keep it that way. A scan
for JWTs, `sb_secret_` keys and API tokens across the tree returns only
documentation describing their shape.

So making the repo public costs nothing operationally and removes the compute
ceiling. What it costs is that the strategies, the model and the reasoning
become readable by anyone. That is a real trade and it is yours to make — the
budget above is designed so you do not have to.
