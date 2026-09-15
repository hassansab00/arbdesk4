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
table for a live number - `live_weather` comes from n8n's P1.5 (documented at 3 hours; the live trigger
was 6 hours until the 15 Sep audit)
and P1.2's NWS observations every 2. 120 runs becomes 60.

| | runs/mo |
|---|---|
| `pipeline_intraday.yml` | 180 |
| `observations.yml` | 120 |
| `pipeline_daily.yml` | 30 |
| `forecasts.yml` | 30 |
| `weather_model.yml` | 4 |
| `archive_observations.yml` | 1 |
| **total** | **365** (from 665) |

The current schedule restores six intraday runs and four observation runs per day.
The thirty against `forecasts.yml` is the archive feed finally having a cron of its
own: it was dispatch-only, which is precisely why the forecast archive was never
being extended.
The table counts scheduled starts in a 30-day month; it is not a bill estimate.
Runtime, whole-minute rounding per job, retries, CI and manual runs also affect the bill.
`tests/test_github_actions.py` fails if scheduled starts drift past **380**.

The paper-trading changes add no scheduled Actions runs. Research captures,
proposals and the recovery sweep share the intraday runner, gated by
`PAPER_TRADES_ENABLED=true` after database setup. They can add runtime to that
job. The n8n worker workflow is event/manual driven and ships inactive; monitor
its executions separately. Do not add minute-by-minute Actions polling.

Manual forecast backfills now run in 25-minute jobs, with a default 75-minute
chain budget. Continuation requires new fully covered dates and no failed API
chunks; a no-progress chain stops with a resumable failure instead of paying
for repeated runner setup. Coverage requires all seven requested lead times.
The web build also omits its unused Python setup step; CI test gates remain.
Runtime jobs install `requirements.runtime.txt` (the tested requests version)
instead of installing pytest and its development dependencies on every data
run. Tests still install the full `requirements.txt`; their coverage is unchanged.

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

Eleven workflow files. Six run on a schedule, five do not. GitHub bills a
**minimum of one minute per job**, rounded up, so for the short ones runs and
minutes are close to the same number.

| Action | Runs/mo | Necessary? |
|---|---|---|
| **Intraday Pipeline** | 180 | **Yes.** Model forecast, band probabilities, edges, signals - the whole pricing chain, six times a day. Nothing downstream exists without it. |
| **Observations** (IEM METAR) | 120 | **Yes.** The second temperature source. Without it there is nothing to check the settlement source against, and `docs/settlement_verification.md` has no evidence base. |
| **Daily Pipeline** | 30 | **Yes.** Forecast ingest, settlement, skill, databank, calibration, derived caches, queued backtests. Every one writes history that cannot be reconstructed later, or a cache the UI times out without. |
| **Weather Model** | 4 | **Yes,** and weekly is right - a regression on 120+ days barely moves day to day. |
| **Archive Observations** | 1 | **Yes, monthly.** This is what keeps the database inside the free tier. |
| **Forecasts** (archive) | 30 | **Yes, nightly at 03:10 UTC** - ahead of the 04:00 daily pipeline, so the steps that read this archive see today's rows and not yesterday's. Blank dates mean "the last ten days", so the cron needs no parameters and simply closes the recent gap. Dispatched *with* a date range it is the multi-hour backfill instead: 25 minutes per link, bounded chain. **This file is the entire Actions bill - see the section below.** |
| **Backtest** | 0 | **On demand.** Repository dispatch or Run workflow. The daily sweep catches anything left queued. |
| **Live Weather Monitor** | 0 | **Superseded, kept as a fallback.** n8n P1.2 does this better and far cheaper. Use it if n8n is down. |
| **Verify Resolution Source** | 0 | An audit you run when you want it, not a schedule. |
| **Tests** | PRs + main | Not a data job. What stops a broken script reaching a schedule. |
| **Web build** | PRs + main, `web/**` only | Not a data job. Reproduces Vercel's build conditions. |

If you need to cut further, the honest order is: the intraday pipeline from
6x/day to 4x (-60 runs) or 3x (-90), then observations from 6-hourly to
12-hourly (-60). Do not cut settlement or the databank freeze - those write
history that cannot be recovered afterwards, and a day missed is a day gone.

**Measure before you cut, though.** The next section is what happened the one
time this desk actually ran out of minutes, and cutting every cadence in the
table above would not have prevented it.

## What it actually cost, measured

Everything above counts scheduled *starts*. This is the bill.

Measured on 2026-09-14 across this repository's whole run history - all 694
runs since it was created on 2026-08-23, every event type, billed the way
GitHub bills: per job, rounded up to the whole minute.

| | |
|---|---|
| Total billed | **2,445 minutes in 23 days** |
| Scheduled runs | 356 |
| Push (CI) runs | 289 |
| Manual dispatches | 49 |

**Five runs are 1,241 of those 2,445 minutes - 51% of everything this desk has
ever spent on Actions.** All five are `forecasts.yml`, started by hand:

| minutes | when | outcome |
|---|---|---|
| 341 | 2026-08-27 | success |
| 341 | 2026-08-27 | success |
| 332 | 2026-08-28 | failure |
| 121 | 2026-08-26 | cancelled |
| 106 | 2026-08-26 | cancelled |

341 minutes is a job running until something else stops it. The
`timeout-minutes` on that job was **350**. That is the whole story, and it is
what `eb7b8d4` - "Actions: stop the minute burn that took the whole account
down", 7 September - was written about.

**The schedule was never the cause.** Excluding those five, the other 689 runs
cost 1,204 minutes over the same 23 days. Measured over the week after the
consolidation landed, on the cadence that is running now:

| 7-13 September | measured | per 30 days |
|---|---|---|
| Scheduled only | 180 min / 7 days | **~770 min** |
| Everything, CI included | 305 min / 7 days | **~1,300 min** |

So 365 scheduled starts really do cost about 2.1 billed minutes each and about
770 of the 2,000 - the estimate this document already carried was right. CI
takes it to roughly 1,300 at a heavy development pace, and well below that when
nobody is pushing. **Cutting a cadence would have saved tens of minutes against
a 1,241-minute problem.** Do not trade freshness for it.

Two guards now stand where that hole was:

- `forecasts.yml` runs 25 minutes per link with a bounded continuation chain,
  and a link that makes no progress stops with a resumable failure instead of
  paying for another runner's setup.
- `test_no_single_job_can_burn_a_fifth_of_the_month` caps every job at **120**
  minutes. Note that `test_every_job_has_a_timeout` passed right through the
  burn: a timeout that *exists* is not a timeout that *bounds*, and 350 was
  not a bound.

**What to watch instead of the cadence: the failures.** Over those same seven
days, **40 of 66 scheduled runs failed**, and for the pipelines the failures
are also the expensive ones - `pipeline_daily` has a median healthy run of 3.2
minutes and a worst failure of 23.1; `pipeline_intraday` 0.8 against 19.6. A
red pipeline costs several times what a green one costs and produces nothing.
(`observations.yml` is the exception: its failures are fast and cheap.) That is
a correctness bug first and a cost bug second, but it is the larger of the two
remaining numbers.

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
