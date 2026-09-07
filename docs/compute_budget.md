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

Three rounds of cutting, each finding a different kind of waste.

**Round 1 — the pollers.** Two workflows were 89% of the bill and both were
polling for work that usually was not there:

| | before | after |
|---|---|---|
| `backtest.yml` | every 10 min - 4,320 runs, ~6,480 min | on demand - 0 scheduled |
| `live_weather.yml` | every 15 min - 2,880 runs, ~4,320 min | manual only - 0 |
| `observations.yml` | every 2 h - 360 runs | every 6 h - 120 runs |
| **total** | **~12,100 min** | **~970 min** |

**Round 2 — the setup tax.** ~970 minutes still ran out the 2,000-minute
allowance in early September, because the remaining cost was not the work. A
typical job here spends 20-30 seconds on checkout, `setup-python` and
`pip install` and then 16-41 seconds doing anything - and GitHub bills each
job rounded UP to a whole minute. **More than half of every billed minute was
setup**, paid again for each workflow.

Two chains were running as nine separate workflows:

| chain | was | now |
|---|---|---|
| model forecast -> probabilities -> edges -> signals | 3 workflows, 15 min apart, 360 runs/mo | `pipeline_intraday.yml`, one runner, **120 runs/mo** |
| forecasts -> settlement -> skill -> databank -> calibration -> derived -> backtest sweep | 6 workflows spread over 5 hours, 180 runs/mo | `pipeline_daily.yml`, one runner, **30 runs/mo** |

The spacing between them was never a dependency - it was a guess that the
previous one had finished. As steps in one job the order is real, and the
setup is paid once instead of nine times.

**Round 3 — the archive feed.** `observations.yml` re-fetches the **last two
days** on every run, so a 6-hourly cadence covered every hour eight times
over. At 12-hourly it still covers it four times over, and nothing reads that
table for a live number - `live_weather` comes from n8n's P1.5 every 3 hours
and P1.2's NWS observations every 2. 120 runs becomes 60.

| | runs/mo |
|---|---|
| `pipeline_intraday.yml` | 120 |
| `observations.yml` | 60 |
| `pipeline_daily.yml` | 30 |
| `weather_model.yml` | 4 |
| `archive_observations.yml` | 1 |
| **total** | **215** (from 665) |

At ~2 billed minutes a run that is roughly **520 minutes a month**, from ~970.
`tests/test_github_actions.py` fails if the count drifts past 260.

**CI was the other half.** `tests.yml` ran on every push with no branch filter
*and* on pull requests, so one commit was billed two or three times - the branch
push, the pull request on that same sha, and the push to main on merge. Both CI
workflows now run on pull requests and on main only, with a concurrency group
that cancels a superseded run rather than paying for it.

A backtest is something you *ask for*, so it no longer polls or runs on a cron
of its own. Queue it in AD4 -> Backtest, then either press **Run workflow**, or
fire it instantly from anything holding a token:

```bash
curl -X POST -H "Authorization: Bearer $GH_PAT" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/OWNER/REPO/dispatches \
     -d '{"event_type":"backtest-queued"}'
```

The last step of the daily pipeline still sweeps anything left queued, on a
runner that job has already paid for, so nothing is forgotten.

## Is every Action necessary? One line each

Nine workflow files. Five run on a schedule, four do not. GitHub bills a
**minimum of one minute per job**, rounded up, so for the short ones runs and
minutes are close to the same number.

| Action | Runs/mo | Necessary? |
|---|---|---|
| **Intraday Pipeline** | 120 | **Yes.** Model forecast, band probabilities, edges, signals - the whole pricing chain, four times a day. Nothing downstream exists without it. |
| **Observations** (IEM METAR) | 60 | **Yes.** The second temperature source. Without it there is nothing to check the settlement source against, and `docs/settlement_verification.md` has no evidence base. |
| **Daily Pipeline** | 30 | **Yes.** Forecast ingest, settlement, skill, databank, calibration, derived caches, queued backtests. Every one writes history that cannot be reconstructed later, or a cache the UI times out without. |
| **Weather Model** | 4 | **Yes,** and weekly is right - a regression on 120+ days barely moves day to day. |
| **Archive Observations** | 1 | **Yes, monthly.** This is what keeps the database inside the free tier. |
| **Forecasts** (backfill) | 0 | **On demand.** The daily ingest is step 1 of the daily pipeline; this file is the multi-hour backfill over a date range, which has no business on a cron. Bounded to 8 self-triggered links. |
| **Backtest** | 0 | **On demand.** Repository dispatch or Run workflow. The daily sweep catches anything left queued. |
| **Live Weather Monitor** | 0 | **Superseded, kept as a fallback.** n8n P1.2 does this better and far cheaper. Use it if n8n is down. |
| **Verify Resolution Source** | 0 | An audit you run when you want it, not a schedule. |
| **Tests** | PRs + main | Not a data job. What stops a broken script reaching a schedule. |
| **Web build** | PRs + main, `web/**` only | Not a data job. Reproduces Vercel's build conditions. |

If you need to cut further, the honest order is: the intraday pipeline from
4x/day to 3x (-30 runs) or 2x (-60), then observations to daily (-30). Do not
cut settlement or the databank freeze - those write history that cannot be
recovered afterwards, and a day missed is a day gone.

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
