"""
Move cold rows out of Postgres and into a GitHub Release.

THREE TABLES NOW. The archive covered the two weather tables and grew stale
against its own database: at 436 MB of a 500 MB tier, the 180-day window it
defaulted to had nothing left to take, and the table that had overtaken both
was not in the set at all.

    trades_observed         91 MB   156,008 rows    90,640 older than 90 days
    weather_observations    57 MB   239,617 rows   114,283 older than 90 days
    weather_forecasts       33 MB    81,693 rows    31,787 older than 90 days

    (at 180 days those same three give 0, 2,522 and 700 - which is why the
     default is ninety. The window has to be shorter than the history.)

trades_observed is the one that matters most and was the last to be covered.
All 91 MB of it is LIVE rows - n_dead_tup is 0, nothing ever updates or
deletes a trade - and 55 MB of that is indexes. So there is no bloat to
reclaim there, only rows nothing reads: v_band_volume and v_city_volume, its
only two consumers, both cut at settings.volume_thresholds.lookback_hours,
which is 24.

WHY IT SHRINKS SO FAR. A Postgres row carries a 24-byte header, per-column
length bytes, and an entry in every index on the table. Indexes are 78 MB of
weather_forecasts' 124 and 59 MB of weather_observations' 121, so removing 79%
of the rows returns far more than the heap figure alone suggests. The same
data as gzipped CSV is a few megabytes.

  python scripts/archive_observations.py --keep-days 90 --dry-run
  python scripts/archive_observations.py --keep-days 90 --table trades --commit

WHERE IT GOES. A GitHub Release asset on this repo: free, 2 GB per asset,
unlimited assets, durable, versioned, and already inside the pipeline that
produced the data. No new account, no new credential, no new bill.

(The idea of hiding data in YouTube uploads is real and people do it. It is
also against their terms, unqueryable, and takes minutes per megabyte. The
instinct - cold storage somewhere free - is right; this is the version that
works.)

WHAT MAKES IT SAFE, in order and without exception:

  1 refresh the feature cache, so every city-day about to lose its raw rows
    exists as a derived row first
  2 export the cold rows to gzipped CSV
  3 upload, then RE-DOWNLOAD and count the rows back
  4 only then prune

Step 3 is the one that matters. An upload that returns 201 and stores a
truncated file would otherwise be discovered months later, by a model with a
hole in it. prune_observations() independently refuses if step 1 did not cover
the range, so the guard exists on both sides.

Loading an archive back into local PostgreSQL: docs/local_archive.md.
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import sys
import traceback

import requests

from common import rest, log_run, rpc, refresh_feature_cache, _cfg, _headers

API = "https://api.github.com"
PAGE = 50000

# --- WHAT CAN BE ARCHIVED, AND HOW EACH ONE IS PAGED ----------------------
#
# weather_forecasts is now the LARGER of the two - 124 MB against 121 MB, and
# 79% of it is older than six months - so archiving only observations left the
# bigger half behind. Both follow the identical four steps; only the key, the
# cutoff column and the prune guard differ, so they are data rather than a
# second copy of the script.
#
# `pk` must be the PRIMARY KEY. Keyset paging on it is what makes the export
# safe: the cutoff columns are nowhere near unique (37 cities x 2 sources
# share a timestamp), Postgres gives no order among ties, and OFFSET paging
# over a non-unique order silently skips rows the prune then deletes anyway.
TABLES = {
    "observations": {
        "table": "weather_observations",
        "pk": "obs_id",
        "cutoff_col": "valid_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_observations",
        "min_keep_days": 60,   # prune_observations refuses under 30; the fit trains on months
        "tag": "observations-archive",
        "columns": ["city_key", "station", "valid_at", "temp_c", "temp_f",
                    "dewpoint_c", "humidity", "wind_speed", "wind_dir_deg",
                    "precip", "cloud_cover", "pressure_hpa", "source"],
        "bytes_per_row": 272,
    },
    "forecasts": {
        "table": "weather_forecasts",
        "pk": "forecast_id",
        "cutoff_col": "for_date",
        # for_date is a DATE, so the cutoff is sent as one. Passing a
        # timestamp would compare a date to a timestamp and shift the boundary
        # by the time of day the job happened to run.
        "cutoff_is_date": True,
        "prune_rpc": "prune_forecasts",
        "min_keep_days": 60,   # same shape, same fitter
        "tag": "forecasts-archive",
        "columns": ["city_key", "model", "run_at", "observed_at", "for_date",
                    "lead_days", "forecast_max_c", "variables", "source"],
        "bytes_per_row": 140,
    },
    # THE LARGEST TABLE IN THE DATABASE, and until now the archive did not
    # know it existed. 91 MB of 436, 156,008 rows, 90,640 of them older than
    # ninety days, and never vacuumed once - not by hand, not by autovacuum.
    #
    # Nothing live reads a trade older than a day. v_band_volume and
    # v_city_volume both cut at settings.volume_thresholds.lookback_hours,
    # which is 24. What is permanent is archive_daily_city_presence, written
    # by a trigger on every insert, and prune_trades refuses unless it already
    # covers every city-day being removed.
    "trades": {
        "table": "trades_observed",
        "pk": "trade_id",
        "cutoff_col": "traded_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_trades",
        # ONE DAY, WANTED AND FLOOR ALIKE (plan v2 P1.6 phase 1, 28 Sep;
        # Hassan approved it on condition that no collected trade is lost -
        # every one is exported, read back and committed first). Two from
        # earlier that day, fourteen from 24 Sep. Since the trade prints moved
        # into the tick (P6.2, 24-25 Sep) the table takes 23,000-56,500 a day,
        # and its readers look back 24 hours; prune_trades refuses any window
        # shorter than that, and any cutoff past the trade ingest's own mark.
        # keep_days is stated, not left to the 90-day default: below the
        # high-water mark every dataset gets what it WANTS, and 90 days of
        # trades would refill the tier within days. The floor is parsed from
        # the SQL by a test: on 23 Sep the function said 30 while this said
        # 14, and the run exported 36,945 rows, had them refused, and failed.
        "keep_days": 1,
        "min_keep_days": 1,
        "tag": "trades-archive",
        "columns": ["band_id", "condition_id", "traded_at", "price", "size",
                    "side", "proxy_wallet", "ingested_at", "city_key",
                    "token_id", "observed_at"],
        "bytes_per_row": 583,
    },
    # THE FASTEST-GROWING TABLE, and the one the 90-day default cannot touch.
    #
    # 78,291 rows and 92 MB in FIVE DAYS - about 18 MB a day, on a 500 MB tier
    # with 46 MB left. Every other table here has months of history and a
    # ninety-day window takes a third of it; research_captures has no row
    # older than the 12th, so ninety days, or thirty, or even seven, archives
    # exactly nothing. That is why keep_days is per table now.
    #
    # It is not duplication - payload_hash already dedupes, and all 78,291
    # payloads are distinct. It is one capture per row of each source
    # relation, six times a day: band_probabilities alone is 30,914 of them.
    # The evidence is real, it just does not need to sit in Postgres to be
    # evidence. It goes to the Release like everything else here and is read
    # back from there.
    #
    # THE GUARD IS DIFFERENT because nothing derived survives this one. There
    # is no feature cache and no presence rollup to check against - what makes
    # a prune safe is the verified round-trip upload, enforced by
    # p_expected_rows, plus a refusal to delete any row a stored integrity
    # manifest has already hashed. See prune_research_captures.
    "research": {
        "table": "research_captures",
        "pk": "capture_id",
        "cutoff_col": "captured_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_research_captures",
        "tag": "research-archive",
        "columns": ["command_key", "captured_at", "engine_version", "provenance",
                    "source_relation", "source_key", "payload", "payload_hash"],
        "bytes_per_row": 1230,
        # TWO DAYS, which is the floor prune_research_captures allows, and the
        # arithmetic is why. The recent rate is ~25,000 rows a day, about
        # 29 MB: 12,259 on the 12th, then 25,808 on the 15th and 24,510 on the
        # 16th as the board grew. Four days of that is 116 MB steady state
        # against 46 MB of headroom; two days is ~58 MB and holds.
        #
        # Two days is also the shortest window that is still honest - a
        # capture written this morning is still being read - so there is
        # deliberately no room left to tighten it further. If this table needs
        # to be smaller than two days hold, the answer is capturing less, not
        # keeping less: band_probabilities is 30,914 of the 78,291 rows
        # because every band is captured on every one of the six cycles a day,
        # and that is the number to change.
        "keep_days": 2,
        # NO ROOM BELOW TWO, as the paragraph above says - and until 23 Sep
        # the floor under it said 1, which prune_research_captures refuses.
        # Under storage pressure that turned a working two-day archive into a
        # refused one-day export: 19,127 rows read, nothing archived.
        "min_keep_days": 2,
        # The other three exist so a model has history to train on, and
        # refresh_feature_cache is what preserves it. This one has no derived
        # form - the capture IS the artefact - so the cache step is not a
        # precondition for it.
        "needs_feature_cache": False,
    },
    # 31 MB TO 75 MB IN THREE DAYS. 11,123 rows at about 7 KB each, because
    # every row carries the full Gamma and CLOB payloads that prove a band's
    # winner. At ~2,200 rows a day that is 15 MB a day - enough on its own to
    # fill a 500 MB tier inside a month.
    #
    # It is also the most valuable table here: those payloads are what make an
    # outcome admissible, and the whole of Phase 2A was about a running
    # maximum not being a settlement. So the read is from a VIEW, not the
    # table. v_prunable_resolution_evidence is only the proofs whose band
    # outcome is ALREADY FROZEN in fact_band_outcome - a proof for a band
    # nobody has banked is the only copy of that answer and is never offered,
    # at any age.
    #
    # Reading the same view the prune deletes from is what makes the count
    # contract hold: the rows uploaded ARE the rows removed, by construction,
    # rather than by two hand-written predicates that would drift apart.
    "resolution": {
        "table": "paper_resolution_evidence",
        "read_from": "v_prunable_resolution_evidence",
        "pk": "proof_id",
        "cutoff_col": "captured_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_resolution_evidence",
        "tag": "resolution-archive",
        # proof_id pages the export and is deliberately NOT exported: a
        # surrogate key means nothing outside the database that issued it. The
        # row is identified by condition_id plus the two token ids, which are
        # the venue's own identifiers and survive anywhere.
        "columns": ["condition_id", "token_yes", "token_no",
                    "winning_token", "captured_at", "gamma", "clob", "source_urls"],
        "bytes_per_row": 7000,
        # ONE DAY, the floor prune_resolution_evidence allows since 24 Sep
        # (plan v2 P1.6). It was three while databank read this table; since
        # P4.5 the verdict lives in resolution_verdicts from the moment of
        # insert, databank reads that, and the payload here is evidence at
        # rest. At ~2,200 proofs a day of ~6 KB, three days held ~40 MB.
        "keep_days": 1,
        "min_keep_days": 1,
        "needs_feature_cache": False,
    },
    # THE LARGEST TABLE ON THE DESK, and the only big one the archive never
    # covered: 204,322 rows, 78 MB, growing 2.76 MB a day. It could not be
    # pruned by AGE like the other five, because scripts/backtest/runner.py
    # asks for the newest book per band at an arbitrary as_of and
    # v_backtest_window bounds every backtest by min(observed_at) - so an age
    # window would silently shorten what can be backtested.
    #
    # What IS redundant is intra-day. The collector writes ~7.7 snapshots per
    # band-day; the backtest reads ONE. So the read is from a view of exactly
    # the rows neither reader touches: not their band-day's closing book, not
    # their band's newest. Verified against the live table before anything was
    # removed - 40,986 rows prunable, band-days 26,513 before and after, and
    # min(observed_at) identical to the microsecond.
    # edges is an append-only log: one row per band, per side, per intraday
    # run, four times a day. 121,644 of its 136,184 rows have already been
    # superseded, and nothing reads the table directly - every consumer goes
    # through v_latest_edge, which takes the newest row per band and side.
    # So what leaves is a pricing that a later pricing replaced.
    "edges": {
        "table": "edges",
        "read_from": "v_prunable_edge_history",
        "pk": "edge_id",
        "cutoff_col": "computed_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_edge_history",
        "tag": "edges-archive",
        "columns": [
            "band_id", "computed_at", "side", "model_prob", "market_price",
            "quoted_price", "edge_pp", "edge_net_pp", "edge_per_dollar",
            "fillable_usd_2c", "fillable_usd_5c", "fillable_usd_10c",
            "est_fee", "est_slippage", "book_snapshot_id", "prob_id",
            "confidence", "regime_label", "tradeable", "block_reason",
        ],
        "bytes_per_row": 290,
        "keep_days": 14,
        "min_keep_days": 7,   # halves the window AND unpins 11,726 book snapshots held only by an edge older than a week
        "needs_feature_cache": False,
    },
    # BOOK PROOF (plan v2 P1.6 phase 1, step 2, 28 Sep; Hassan approved it on
    # condition that no collected data is lost). The order book behind every
    # paper fill, ~6.4 KB a row, ~340 a day, 13 MB and never archived. Its
    # readers - complete_paper_order and queue_automatic_paper_exit - take a
    # proof no older than 900 s (120 by default), so one day is evidence at
    # rest. The table is append-only; prune_book_evidence claims the same
    # single-table archive exemption prune_research_captures does.
    "book_evidence": {
        "table": "paper_book_evidence",
        "pk": "snapshot_id",
        "cutoff_col": "captured_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_book_evidence",
        "tag": "book-evidence-archive",
        # snapshot_id IS EXPORTED: it is not a surrogate but a sha256 of the
        # book itself, and paper_orders cite their proof by it
        # (result->>snapshot_id), so it is what joins an order to its book.
        "pk_is_exported_because": "paper_orders cite their proof by it (result->>snapshot_id), and it is a hash of the book, not a surrogate",
        "columns": ["snapshot_id", "token_id", "observed_at", "captured_at", "payload"],
        "bytes_per_row": 6400,
        "keep_days": 1,
        "min_keep_days": 1,
        "needs_feature_cache": False,
    },
    # PAST FORECAST FEATURES (plan v2 P1.6 phase 1, step 3, 28 Sep; approved
    # on condition that no collected data is lost). Each forecast run's
    # conditions for a city-day, ~3,300 rows a day, 14 MB, never archived.
    # Readers: v_forecast_features for for_date >= today (weather_model's
    # forward predictions; its fit reads derived_city_day_features), plus the
    # inventory and freshness views. The table's key is (city_key, for_date,
    # run_at), so the export reads v_forecast_features_export, which joins
    # them into one fixed-width text key to page on; the key itself is not
    # exported. Two days, not one: scripts/mirror_to_repo.py copies this table
    # by capture day AFTER the prune, and a for_date can be a day before its
    # capture day (588 rows, measured 28 Sep), so a row leaves only once the
    # mirror has it too. The prune refuses any row captured since yesterday.
    "forecast_features": {
        "table": "weather_forecast_features",
        "read_from": "v_forecast_features_export",
        "pk": "feature_key",
        # the table's composite primary key, which the view joins into `pk`
        "pk_joins": ["city_key", "for_date", "run_at"],
        "cutoff_col": "for_date",
        "cutoff_is_date": True,
        "prune_rpc": "prune_forecast_features",
        "tag": "forecast-features-archive",
        "columns": ["city_key", "for_date", "run_at", "source", "lead_days",
                    "forecast_max_c", "forecast_min_c", "apparent_max_c",
                    "morning_temp_c", "morning_dewpoint_c", "dewpoint_depression_c",
                    "morning_humidity", "cloud_mean", "cloud_max", "wind_mean",
                    "wind_max", "precip_total", "precip_probability", "n_hours",
                    "captured_at", "morning_pressure_hpa", "pressure_change_24h_hpa",
                    "wind_u_mean", "wind_v_mean"],
        "bytes_per_row": 230,
        "keep_days": 2,
        "min_keep_days": 2,
        "needs_feature_cache": False,
    },
    # THE DECISION LOG (plan v2 P5.11): one row per run, strategy and
    # city-day, about 2,600 a day at 48 city-days, 9 strategies and six runs
    # (measured 25 Sep). Nothing reads a decision older than 30 days from
    # Postgres; the Release keeps every one. prune_decisions refuses under 14.
    "decisions": {
        "table": "decisions",
        "pk": "decision_id",
        "cutoff_col": "decided_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_decisions",
        "tag": "decisions-archive",
        "columns": ["run_id", "decided_at", "tick_id", "checkpoint_id", "strategy_id",
                    "city_key", "resolution_date", "action", "reason_code", "g_now", "g_wait",
                    "binding", "target_usd", "held_usd", "n_signals", "params_version"],
        "bytes_per_row": 150,
        "keep_days": 30,
        "min_keep_days": 14,
        "needs_feature_cache": False,
    },
    # THE LADDERS, BEFORE THE HOURLY PRUNE EMPTIES THEM (plan v2 P5.13).
    # prune_dead_book_detail nulls raw_book / no_book at 6 h (decided bands)
    # or 48 h (trading ones), so "books" above, at 7 days, has only ever
    # archived empty ladders: 0 raw_book values in its 7 files (27 Sep). The
    # replay's taker fills (P5.12) walk exactly those ladders. This exports
    # every tradeable snapshot whose ladder the archive does not hold yet
    # (a DEAD_LOSER / DEAD_WINNER book keeps its 6-hour prune: ~500 an hour,
    # a wall at 0.001 or 0.999, and too many MB to hold a day), nightly, and
    # its "prune" is mark_ladders_archived: a stamp, not a delete. The hourly
    # prune now nulls only stamped ladders (20260927100000). One day: the
    # nightly run takes every ladder older than a day, so a ladder waits at
    # most about two days in Postgres. About 2-4 MB of ladder JSON a day
    # before gzip (26 Sep: 3.8 MB LIVE, 0.4 MB WIDE and ONE_SIDED).
    "ladders": {
        "table": "book_snapshots",
        "read_from": "v_unarchived_ladders",
        "pk": "snapshot_id",
        "cutoff_col": "observed_at",
        "cutoff_is_date": False,
        "prune_rpc": "mark_ladders_archived",
        "tag": "ladders-archive",
        "columns": ["snapshot_id", "band_id", "observed_at", "market_state", "best_bid", "best_ask",
                    "no_best_bid", "no_best_ask", "raw_book", "no_book"],
        "bytes_per_row": 1100,
        "keep_days": 1,
        "min_keep_days": 1,
        # THE ONE DATASET THAT EXPORTS ITS KEY, because P5.13 asks for it: the
        # edges archive carries book_snapshot_id, and a ladder is joined to the
        # pricing that used it through exactly this id.
        "pk_is_exported_because": "edges.book_snapshot_id refers to it (plan v2 P5.13)",
        "needs_feature_cache": False,
        # A stamp frees no space, so there is nothing to VACUUM for; the space
        # comes back when the hourly prune nulls the ladders.
        "reclaim": False,
    },
    "books": {
        "table": "book_snapshots",
        "read_from": "v_prunable_book_redundancy",
        "pk": "snapshot_id",
        "cutoff_col": "observed_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_book_redundancy",
        "tag": "books-archive",
        "columns": ["band_id", "observed_at", "snapshot_hour_utc", "market_state",
                    "tradeable", "best_bid", "best_ask", "mid", "spread",
                    "bid_levels", "ask_levels",
                    "ask_usd_1c", "bid_usd_1c", "ask_usd_2c", "bid_usd_2c",
                    "ask_usd_5c", "bid_usd_5c", "ask_usd_10c", "bid_usd_10c",
                    "ask_usd_25c", "bid_usd_25c", "ask_total_usd", "bid_total_usd",
                    "band_volume", "band_volume_24hr", "raw_book",
                    "no_best_bid", "no_best_ask", "no_book"],
        "bytes_per_row": 400,
        # SEVEN DAYS AT FULL RESOLUTION, because v_band_price_history draws the
        # monitor's chart from intra-day rows and a shorter window makes that
        # chart sparse for storage not worth having.
        "keep_days": 7,
        # The floor costs that chart some resolution, which is exactly why it
        # is a floor and not the default: it applies only while the database
        # is over its high-water mark.
        "min_keep_days": 4,
        "needs_feature_cache": False,
    },
}


def effective_keep_days(spec, override=None):
    """The window this run uses, and why.

    Each dataset declares the window it WANTS and the shortest one it can
    survive on. While the database is under its high-water mark every dataset
    gets what it wants; over it, every dataset drops to its floor until the
    size comes back down.

    THE PROBLEM THIS SOLVES IS NOT A BROKEN PRUNE. Measured 2026-09-22 the
    archive cycle was working and caught up - everything eligible came out on
    the next run, about 25 MB, and two days later the database was back over
    the tier. Seven datasets each held a window chosen on its own merits, and
    the sum of seven reasonable local decisions was a database slightly larger
    than the plan it runs on. Nobody decided that, so nobody was going to
    notice it either.

    An explicit --keep-days always wins. An operator who names a number is
    answering a question this function is guessing at.

    A FAILURE TO READ THE PRESSURE KEEPS THE FULL WINDOW, never the floor.
    Shortening retention because a health check was unreachable is how you
    lose history to a network blip.
    """
    want = spec.get("keep_days", 90)
    if override is not None:
        return override, {"source": "--keep-days", "over": None}

    floor = spec.get("min_keep_days")
    if not floor or floor >= want:
        return want, {"source": "declared", "over": None}

    try:
        p = _rpc("storage_pressure") or {}
    except Exception as e:
        print(f"  storage_pressure unavailable ({e}); keeping the full {want}-day window",
              file=sys.stderr)
        return want, {"source": "declared (pressure unreadable)", "over": None}

    if isinstance(p, list):
        p = p[0] if p else {}
    if p.get("over"):
        print(f"  {p.get('verdict')} - {want}d -> {floor}d for this dataset")
        return floor, {"source": "floor", "over": True,
                       "pct_of_tier": p.get("pct_of_tier"), "db_mb": p.get("db_mb")}
    return want, {"source": "declared", "over": False,
                  "pct_of_tier": p.get("pct_of_tier"), "db_mb": p.get("db_mb")}


def read_source(spec):
    """The relation the export reads - the view when one is declared.

    A dataset whose prune cannot be written as a PostgREST filter declares
    `read_from`. Anything else reads its own table.
    """
    return spec.get("read_from") or spec["table"]


def _rpc(fn, params=None):
    # common.rpc, so a Postgres error reaches the log instead of being replaced
    # by "500 Server Error for url: ...".
    return rpc(fn, params, timeout=600)


def _cell(value):
    """A value as the archive should STORE it, not as Python happens to print it.

    csv.DictWriter stringifies with str(), and rest() hands a jsonb column
    back as a parsed dict - so every archived payload was written as a PYTHON
    REPR rather than as JSON:

        {'band_hi': 28, 'sigma_c': None, 'open_low': False}

    Single quotes, None where JSON needs null, False where it needs false. It
    still round-trips through ast.literal_eval so nothing was lost, but no
    JSON parser will touch it - which makes it useless to /api/archive and to
    the browser, and being readable is the entire reason the archive exists.
    78,291 research captures were written that way before a test actually
    tried to parse one back.

    Only dicts and lists are touched. Everything else keeps exactly the
    rendering it had, including None, which csv already writes as an empty
    field rather than the string "None" - a distinction test_weather_model
    pins deliberately.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def export_cold(spec, cutoff):
    """Every observation strictly older than the cutoff, as a gzipped CSV.

    KEYSET PAGING ON THE PRIMARY KEY, and that is the whole point of this
    function. It used to page with OFFSET over `order=valid_at.asc`, and
    valid_at is nowhere near unique - 37 cities times two sources share every
    timestamp. Postgres gives no order among ties, so rows at a page boundary
    could be returned twice or not at all, and OFFSET would then walk past the
    ones it skipped.

    That is not a slow query, it is silent data loss: a skipped row is never
    written to the archive, and the prune below deletes by DATE, so it deletes
    that row anyway. The round-trip check downstream cannot catch it either,
    because it compares the uploaded file against this same short list. The
    one failure the design cannot survive, reintroduced by the paging that was
    written to prevent it.

    obs_id is the primary key, so `order=obs_id.asc` is a total order and
    `obs_id=gt.<last>` cannot skip or repeat. It is also O(1) per page instead
    of O(offset), which matters at 600k rows.

    Streams into the CSV rather than accumulating a list of dicts: the same
    export held ~600 MB of Python objects before being copied into a string and
    then gzipped.

    READS `read_from` WHEN THE SPEC DECLARES ONE, and that is not a detail.
    A dataset whose prune carries a predicate PostgREST cannot express names
    the view that carries it, and the count contract downstream only holds if
    the rows uploaded ARE the rows removed. Reading the base table instead
    exports a WIDER set than the prune would delete, and the preflight then
    refuses every night - which is what happened to `resolution` from 19 Sep:
    4,540 rows exported against 3,853 prunable, nothing uploaded, nothing
    deleted, and the fastest-growing table on the desk left to grow.

    The 687-row difference was old proofs whose band outcome is not yet frozen
    in fact_band_outcome. Those are the only copy of their own answer, which
    is exactly why the view excludes them and exactly why the guard held.

    Returns (gzip blob, row count, earliest valid_at, latest valid_at).
    """
    source = read_source(spec)
    cols, pk, cut_col = spec["columns"], spec["pk"], spec["cutoff_col"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()

    n, after, lo, hi = 0, None, None, None
    while True:
        params = [
            ("select", ",".join([pk] + cols)),
            (cut_col, f"lt.{cutoff.isoformat()}"),
            ("order", f"{pk}.asc"),
            ("limit", str(PAGE)),
        ]
        if after is not None:
            params.append((pk, f"gt.{after}"))
        rows = rest(source, params)
        if not rows:
            break
        for r in rows:
            w.writerow({k: _cell(v) for k, v in r.items()})
            v = r.get(cut_col)
            if v:
                if lo is None or v < lo:
                    lo = v
                if hi is None or v > hi:
                    hi = v
        n += len(rows)
        next_after = rows[-1][pk]
        if next_after == after:
            raise RuntimeError(
                f"{source} archive pagination made no progress at {after}"
            )
        after = next_after
        # PostgREST can silently cap a requested page below PAGE. Keep walking
        # until an empty page proves the keyset is exhausted.
        if n % 25000 < len(rows):
            print(f"  ... {n:,} rows")

    return gzip.compress(buf.getvalue().encode(), 9), n, lo, hi


REPO_ARCHIVE = "data/archive"
PENDING = os.path.join(REPO_ARCHIVE, ".pending.json")


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_archive_path(name, asset_name):
    """Where a dataset's archive file lives IN THE REPOSITORY.

    data/archive/<dataset>/<range>.csv.gz - committed, pushed, clonable, and
    visible on GitHub without a token or an API call.

    A GitHub Release is attached to a repository; it is not IN it. You cannot
    clone it, grep it, or see it in the tree, and reading one back needs the
    API and a token. Every row this desk has ever archived went there and
    nowhere else, so the only copy of thirteen months of observations lived
    somewhere the repository could not see. That is the difference between
    data you own and data you have to ask for.
    """
    return os.path.join(_root(), REPO_ARCHIVE, name, asset_name)


def write_repo_archive(name, asset_name, blob):
    path = repo_archive_path(name, asset_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


def verify_repo_archive(path, expect_rows):
    """Read the file back OFF DISK and count what is in it.

    This is the gate the prune opens on, and it is deliberately not the blob
    still in memory: the question is whether the bytes that reached the
    filesystem - and by the time the prune runs, the bytes that reached the
    repository - decompress and parse into the rows we are about to delete.
    """
    try:
        with open(path, "rb") as fh:
            got = count_rows(fh.read())
    except (OSError, ValueError, EOFError) as e:
        print(f"cannot read back {path}: {e}", file=sys.stderr)
        return 0, False
    return got, got == expect_rows


def load_pending():
    try:
        with open(os.path.join(_root(), PENDING), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_pending(pending):
    path = os.path.join(_root(), PENDING)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pending, fh, indent=2, sort_keys=True)


def count_rows(blob):
    """Rows in a gzipped CSV, header excluded. Used to verify a round trip.

    Parsed rather than counted by newline: a line count is wrong the day any
    exported field contains one, and this number is the only thing standing
    between a truncated upload and a permanent delete.
    """
    text = gzip.decompress(blob).decode()
    return max(0, sum(1 for _ in csv.reader(io.StringIO(text))) - 1)


def gh(repo, token, method, path, **kw):
    r = requests.request(method, f"{API}/repos/{repo}{path}",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.github+json"},
                         timeout=300, **kw)
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    # NO GLOBAL DEFAULT ANY MORE. Ninety days is right for three tables with
    # months of history and useless for research_captures, whose entire
    # history is five days - the same flag either archives nothing from it or
    # takes far too much from the others. Each table carries its own window;
    # this flag, when given, overrides every one of them.
    ap.add_argument("--keep-days", type=int, default=None,
                    help="override every table's own window (default: each "
                         "table's own - 90 days, or 4 for research captures)")
    ap.add_argument("--commit", action="store_true",
                    help="actually upload and prune (default is a dry run)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    # "both" predates trades_observed and still means every table, because a
    # saved workflow_dispatch and anything scripted against it still send it.
    ap.add_argument("--table", choices=sorted(TABLES) + ["all", "both"], default="all",
                    help="which archive to run (default: all)")
    # THE TWO PHASES, AND WHY THEY ARE TWO.
    #
    # Between them the workflow commits and pushes data/archive, so the rows
    # are in the repository before the database is told to drop them. Run as
    # one step, a job that dies after the delete and before the commit loses
    # the only copy - which is not hypothetical: the index was written onto
    # the runner and destroyed with it six runs in a row.
    ap.add_argument("--export-only", action="store_true",
                    help="write the archive files into the repo and stop (no delete)")
    ap.add_argument("--prune-only", action="store_true",
                    help="delete only rows whose archive file is already committed")
    ap.add_argument("--pull-releases", action="store_true",
                    help="one-time: copy existing Release assets into data/archive")
    args = ap.parse_args()

    if args.export_only and args.prune_only:
        print("--export-only and --prune-only are the two halves; pass one", file=sys.stderr)
        return 1

    if args.pull_releases:
        return pull_releases(args)

    if args.keep_days is not None and args.keep_days < 1:
        print("--keep-days must be at least 1", file=sys.stderr)
        return 1

    names = sorted(TABLES) if args.table in ("all", "both") else [args.table]
    worst = 0
    failed = []
    for name in names:
        print(f"\n=== {name} ===")
        # ONE TABLE MUST NOT COST THE OTHER SIX THEIR RUN.
        #
        # run_one returns an rc for every refusal it anticipates - a failed
        # verify, a count mismatch, a stale feature cache - and main took
        # max() of those, so a refusal on one table never stopped the rest.
        # An EXCEPTION was a different story: it unwound straight through
        # this loop to sys.exit, and whatever had not run yet did not run.
        #
        # On 21 Sep prune_book_redundancy raised HTTP 500 (statement timeout)
        # and `books` sorts first of seven, so forecasts, observations,
        # research, resolution and trades were never attempted - on a
        # database already at 111% of its tier. The run before that had
        # archived five tables; this one archived none, and the only
        # difference was which table happened to fail.
        #
        # An archive is seven independent jobs that share a script. They fail
        # independently now, and the exit code still carries the worst of
        # them, so CI stays red until the broken one is fixed.
        try:
            if args.export_only:
                rc = export_one(TABLES[name], name, args)
            elif args.prune_only:
                rc = prune_one(TABLES[name], name, args)
            else:
                rc = run_one(TABLES[name], name, args)
        except Exception as e:                       # noqa: BLE001 - see above
            traceback.print_exc()
            print(f"{name}: FAILED with {type(e).__name__}: {e}. "
                  f"Continuing with the remaining tables.", file=sys.stderr)
            log_run(f"archive_{name}", "attention", 0,
                    {"error": f"{type(e).__name__}: {e}"[:500]})
            failed.append(name)
            rc = 1
        worst = max(worst, rc)

    if failed:
        print(f"\ntables that failed: {', '.join(failed)}", file=sys.stderr)
    return worst


def reconcile_index(name=None):
    """Make the index say what the FILES say, and report what it changed.

    THE INDEX IS A CLAIM AND THE FILE IS THE EVIDENCE. Every row count in
    web/public/archive/index.json is written by the export that produced the
    file - so any path where a file can be replaced without the index being
    rewritten is a path where the two drift, and a reader is told a number
    no file backs.

    That happened: --pull-releases overwrote a 51,504-row export with a
    55,203-row Release asset and left the index on the old number. This runs
    after every pull and re-counts what is actually on disk, so the drift
    cannot survive a single run whatever caused it.

    Files the index does not mention are left alone - pulling an asset for a
    dataset the index has never recorded is a separate question from keeping
    an existing entry honest.
    """
    path = os.path.join(_root(), MANIFEST)
    try:
        with open(path, encoding="utf-8") as fh:
            index = json.load(fh)
    except (OSError, ValueError):
        return []

    changed = []
    for ds_name, ds in (index.get("datasets") or {}).items():
        if name and ds_name != name:
            continue
        for entry in ds.get("assets", []):
            f = repo_archive_path(ds_name, entry.get("asset", ""))
            if not os.path.exists(f):
                continue
            try:
                with open(f, "rb") as fh:
                    blob = fh.read()
                rows = count_rows(blob)
            except (OSError, ValueError, EOFError):
                continue
            if rows != entry.get("rows") or len(blob) != entry.get("gzip_bytes"):
                changed.append((entry["asset"], entry.get("rows", 0), rows))
                entry["rows"] = rows
                entry["gzip_bytes"] = len(blob)
        ds["rows_archived"] = sum(a.get("rows", 0) for a in ds.get("assets", []))

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=2, sort_keys=True)
    return changed


def pull_releases(args):
    """Copy every asset already in a Release into data/archive. Run once.

    658,993 rows were archived before the repository held any of them. They
    are not lost - each one is a Release asset with a recorded sha256 - but
    they are reachable only through the API, with a token, which is not what
    owning your data looks like. This walks the Releases and writes the files
    into the tree so the back-history sits beside everything written from now
    on.

    Idempotent: an asset already on disk with the right byte count is skipped,
    so it can be re-run after a partial download without re-fetching 3 MB of
    observations.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or not args.repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are required.", file=sys.stderr)
        return 1

    pulled, skipped, missing = 0, 0, []
    for name in sorted(TABLES):
        spec = TABLES[name]
        # gh() HANDS BACK A RAW Response, NOT PARSED JSON - check
        # .status_code and call .json(); the first draft of this function
        # did neither. A missing release came back as a 404 Response
        # rather than an exception, the try/except never fired, and the run
        # died on `'Response' object has no attribute 'get'` before a single
        # byte was pulled.
        r = gh(args.repo, token, "GET", f"/releases/tags/{spec['tag']}")
        if r.status_code != 200:
            print(f"{name}: no release {spec['tag']} (HTTP {r.status_code})")
            missing.append(name)
            continue
        rel = r.json()
        for asset in rel.get("assets", []):
            path = repo_archive_path(name, asset["name"])
            # THE REPOSITORY WINS, ALWAYS.
            #
            # This used to re-download whenever the sizes differed, treating
            # the Release as the authority. It is not: the export phase wrote
            # the repo copy, read it back off disk, counted it, and the prune
            # deleted exactly those rows from Postgres against it. A Release
            # asset of the same name is whatever an older run happened to
            # upload.
            #
            # On 21 Sep that ordering silently replaced a verified 51,504-row
            # books export with a 55,203-row Release asset, and the index -
            # written by the export - no longer described the file sitting
            # next to it. Nothing was lost, because the replacement was the
            # larger set, but "nothing was lost" was luck rather than design.
            #
            # So this fills in history the repository does not have, and
            # never overwrites history it does.
            if os.path.exists(path):
                skipped += 1
                continue
            r = requests.get(asset["url"],
                             headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/octet-stream"},
                             timeout=600)
            r.raise_for_status()
            # Count before writing: a truncated download must not land in the
            # repository looking like an archive.
            rows = count_rows(r.content)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(r.content)
            print(f"  {name}/{asset['name']}  {rows:,} rows  {len(r.content)/1e6:.2f} MB")
            pulled += 1

    drifted = reconcile_index()
    print(f"\npulled {pulled} asset(s) into {REPO_ARCHIVE}, {skipped} already present")
    if drifted:
        print(f"index corrected to match {len(drifted)} file(s): "
              + ", ".join(f"{a} {was:,}->{now:,}" for a, was, now in drifted))
    if missing:
        print("no release yet for:", ", ".join(missing))
    log_run("archive_pull_releases", "ok", pulled,
            {"pulled": pulled, "skipped": skipped, "no_release": missing,
             "index_corrected": [a for a, _w, _n in drifted]})
    return 0


def newest_archive(name):
    """The index entry with the latest cutoff for this dataset, or None."""
    try:
        with open(os.path.join(_root(), MANIFEST), encoding="utf-8") as fh:
            index = json.load(fh)
    except (OSError, ValueError):
        return None
    assets = [a for a in ((index.get("datasets") or {}).get(name) or {}).get("assets", [])
              if a.get("asset") and a.get("archived_through")]
    return max(assets, key=lambda a: str(a["archived_through"])) if assets else None


def prune_was_recorded(name, asset):
    """Did a prune of exactly this file report success?

    ingest_log is the record: prune_one logs `ok` with the asset name only
    after the database confirms the delete, and nothing prunes that table. An
    export is recorded in the index the moment its file verifies, so the index
    alone cannot tell an archive that finished from one whose prune never ran.
    """
    return bool(rest("ingest_log", [
        ("select", "log_id"), ("job", f"eq.archive_{name}"), ("status", "eq.ok"),
        ("detail->>asset", f"eq.{asset}"), ("limit", "1"),
    ]))


def finish_stranded_export(spec, name, keep_days):
    """Finish an earlier run's export whose prune never ran. None means carry on.

    WHAT HAPPENED ON 23 SEP. Three datasets refused their export, the export
    step failed, and the workflow skipped the commit and the prune for all
    seven. Books, edges, forecasts and observations had already exported
    cleanly, and the step that always runs committed those four files - so
    138,152 rows sat in the repository AND in the database, and the index
    counted them as archived.

    The next export reads every row older than a later cutoff, which includes
    all of those. Left alone, that exports them a second time: the same rows
    in two files, counted twice in the index, for as long as anything reads it.

    So before exporting, this asks the database whether the newest archived
    range is still there. The file already in HEAD is the proof the prune
    needs, so it goes into the pending record exactly as a fresh export would,
    with ITS cutoff and ITS row count, and this run exports nothing new for
    the dataset. Tomorrow starts from a clean cutoff.

    Only when the numbers agree exactly. A database holding a different
    number of rows older than that cutoff than the file does is not a state
    this can resolve by itself: exporting would duplicate, pruning would
    delete rows the file does not hold. It refuses, and says which.
    """
    job = f"archive_{name}"
    last = newest_archive(name)
    if not last or prune_was_recorded(name, last["asset"]):
        return None

    through = last["archived_through"]
    probe = _rpc(spec["prune_rpc"], {"p_keep_days": keep_days, "p_before": through,
                                     "p_expected_rows": None, "p_dry_run": True})
    if isinstance(probe, list):
        probe = probe[0] if probe else {}
    if not (probe or {}).get("ok"):
        print(f"CANNOT TELL whether {last['asset']} was pruned: {probe}. Not exporting "
              f"{name} - a new export could repeat its rows.", file=sys.stderr)
        log_run(job, "attention", 0, {"stranded_asset": last["asset"],
                                      "archived_through": through, "probe": probe})
        return 1

    left = int(probe.get("would_delete") or 0)
    if left == 0:
        return None     # pruned before this record existed, or the log was lost

    rel = os.path.relpath(repo_archive_path(name, last["asset"]), _root())
    got, ok = verify_repo_archive(os.path.join(_root(), rel), last.get("rows"))
    committed, why = is_committed(rel)
    if ok and committed and left == got:
        pending = load_pending()
        pending[name] = {
            "file": rel, "asset": last["asset"], "rows": got, "cutoff": through,
            "keep_days": keep_days, "resumed": True,
            "exported_at": last.get("archived_at"),
        }
        save_pending(pending)
        print(f"{got:,} rows archived to {rel} at {last.get('archived_at')} are still in "
              f"the database - that run's prune never ran. Queued for this run's "
              f"prune; nothing new is exported for {name} until it has.")
        return 0

    print(f"STRANDED EXPORT DOES NOT MATCH: {rel} holds {got:,} rows "
          f"({'committed' if committed else why}) and the database has {left:,} older "
          f"than {through}. Not exporting {name} - it would repeat those rows.",
          file=sys.stderr)
    log_run(job, "attention", left, {"stranded_asset": last["asset"], "archived_through": through,
                                     "still_in_database": left, "file_rows": got,
                                     "committed": committed})
    return 1


def export_one(spec, name, args):
    """Export this table's cold rows to a FILE IN THE REPOSITORY. Delete nothing.

    Phase one of two. The prune is a separate call, and between them the
    workflow commits and pushes what this wrote - so by the time anything is
    removed from Postgres the rows are in git, on GitHub, in the clone on
    your laptop the next time you pull.

    That ordering is the whole point. Before this, one process exported,
    uploaded and deleted inside a single job: if the job died between the
    upload and the commit, the rows were gone from the database and the only
    record of where they went died with the runner. Which is exactly what
    happened to the index for six consecutive runs.
    """
    job = f"archive_{name}"
    keep_days, pressure = effective_keep_days(spec, args.keep_days)
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    # A date column needs a date cutoff; comparing it to a timestamp shifts the
    # boundary by whatever time of day this happened to run.
    cutoff = stamp.date() if spec["cutoff_is_date"] else stamp

    # 1 - the cache must cover what is about to go
    if spec.get("needs_feature_cache", True):
        try:
            refresh_feature_cache()
        except Exception as e:
            print(f"refresh_feature_cache failed ({e}). Not archiving - the derived "
                  f"rows are what survives the prune.", file=sys.stderr)
            log_run(job, "attention", 0, {"error": str(e)})
            return 1

    # 1b - an earlier export whose prune never ran is finished first
    stranded = finish_stranded_export(spec, name, keep_days)
    if stranded is not None:
        return stranded

    # 2 - export
    print(f"reading {read_source(spec)} older than {cutoff.isoformat()} ...")
    blob, n_rows, lo, hi = export_cold(spec, cutoff)
    if not n_rows:
        print(f"nothing older than {cutoff} - nothing to archive.")
        log_run(job, "ok", 0, {"keep_days": keep_days, "storage": pressure})
        return 0

    asset_name = f"{name}-{str(lo)[:10]}-to-{str(hi)[:10]}.csv.gz"

    # 3 - THE SAME INSTANT, both phases. Letting the database recompute its own
    # cutoff deletes the minutes that passed while this ran - unarchived. The
    # cutoff is carried to the prune in the pending record rather than
    # recomputed there, which matters more now that a commit and a push happen
    # in between.
    prune_args = {
        "p_keep_days": keep_days,
        "p_before": cutoff.isoformat(),
        "p_expected_rows": n_rows,
    }

    # The database must independently agree with the exported row count BEFORE
    # a file claiming that range lands in the repository. This catches silent
    # PostgREST page caps, and it is the check that stopped the resolution
    # archive from turning a 4,540-row export into a 3,853-row delete.
    preflight = _rpc(spec["prune_rpc"], {**prune_args, "p_dry_run": True})
    # A REFUSAL IS NOT A MISMATCH (plan v2 P1.3). On 23 Sep the prune
    # functions refused keep_days below their floors, and this reported it as
    # "ARCHIVE COUNT MISMATCH" with status attention - the one wording that
    # sends a reader looking for lost rows. The database said no, and why.
    if not (preflight or {}).get("ok"):
        reason = (preflight or {}).get("error") or "no reason returned"
        print(f"PRUNE PREFLIGHT REFUSED: {reason}. Nothing written or deleted.",
              file=sys.stderr)
        log_run(job, "error", 0, {"refused": reason, "rows": n_rows, "preflight": preflight,
                                  "keep_days": keep_days, "cutoff": cutoff.isoformat()})
        return 1
    if preflight.get("would_delete") != n_rows:
        print(f"ARCHIVE COUNT MISMATCH: exported {n_rows:,} rows but prune "
              f"preflight returned {preflight}. Nothing written or deleted.",
              file=sys.stderr)
        log_run(job, "attention", n_rows,
                {"rows": n_rows, "preflight": preflight, "cutoff": cutoff.isoformat()})
        return 1

    # 4 - write it into the repository, then READ IT BACK OFF DISK
    path = write_repo_archive(name, asset_name, blob)
    got, ok = verify_repo_archive(path, n_rows)
    rel = os.path.relpath(path, _root())
    if not ok:
        print(f"VERIFY FAILED: wrote {n_rows:,} rows to {rel}, read back {got:,}. "
              f"Nothing will be pruned.", file=sys.stderr)
        log_run(job, "attention", 0, {"file": rel, "rows": n_rows, "read_back": got})
        return 1
    print(f"{n_rows:,} rows -> {rel}  ({len(blob)/1e6:.1f} MB gzipped), verified on disk")

    record_manifest(name, spec, asset_name, n_rows, len(blob), lo, hi, cutoff)

    pending = load_pending()
    pending[name] = {
        "file": rel, "asset": asset_name, "rows": n_rows,
        "cutoff": prune_args["p_before"], "keep_days": keep_days, "storage": pressure,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    save_pending(pending)
    return 0


def prune_one(spec, name, args):
    """Delete only what is already committed, and only as much of it.

    Phase two. It re-reads the archive file from the working tree - which the
    commit step has by now pushed - and counts the rows in it. That count,
    not the exporter's memory of it, is what the database is told to delete.
    If the file is missing or short, nothing is deleted.
    """
    job = f"archive_{name}"
    entry = load_pending().get(name)
    if not entry:
        print(f"no export pending for {name} - nothing to prune.")
        return 0

    path = os.path.join(_root(), entry["file"])
    got, ok = verify_repo_archive(path, entry["rows"])
    if not ok:
        print(f"REFUSING TO PRUNE {name}: {entry['file']} holds {got:,} rows, "
              f"the export recorded {entry['rows']:,}.", file=sys.stderr)
        log_run(job, "attention", 0,
                {"file": entry["file"], "expected": entry["rows"], "found": got})
        return 1

    committed, why = is_committed(entry["file"])
    if not committed:
        print(f"REFUSING TO PRUNE {name}: {entry['file']} is not committed ({why}). "
              f"The rows would leave the database with no copy in the repository.",
              file=sys.stderr)
        log_run(job, "attention", 0, {"file": entry["file"], "not_committed": why})
        return 1

    cutoff = entry["cutoff"]
    prune_args = {"p_keep_days": entry["keep_days"], "p_before": cutoff,
                  "p_expected_rows": got}
    prune = _rpc(spec["prune_rpc"], {**prune_args, "p_dry_run": False})
    print(f"prune: {prune}")
    if not (prune or {}).get("ok") or prune.get("deleted") != got:
        print(f"PRUNE REFUSED OR COUNT CHANGED: {prune}", file=sys.stderr)
        log_run(job, "attention", got,
                {"file": entry["file"], "rows": got, "prune": prune})
        return 1

    reclaim = request_reclaim(spec["table"]) if spec.get("reclaim", True) else {"skipped": "nothing deleted"}
    log_run(job, "ok", got, {
        "file": entry["file"], "asset": entry["asset"], "rows": got,
        "keep_days": entry["keep_days"], "archived_through": cutoff, "prune": prune,
        "reclaim": reclaim, "resumed": bool(entry.get("resumed")),
    })
    pending = load_pending()
    pending.pop(name, None)
    save_pending(pending)
    return 0


def request_reclaim(table):
    """Ask pg_cron to VACUUM FULL this table now that its prune has committed.

    A PRUNE DOES NOT SHRINK THE DATABASE - it leaves dead pages that only a
    VACUUM FULL hands back, and pg_database_size is what the tier measures.
    Those reclaims ran on fixed pg_cron times that assumed this archive had
    already run; GitHub fired the 03:00 archive at 08:14 on 2026-09-22, so the
    03:30 reclaim rewrote an unpruned table and the day's freed space waited
    until 03:30 the next morning. See request_reclaim() in
    sql/ad4_66_reclaim_archived_tables.sql.

    A FAILURE HERE DOES NOT FAIL THE ARCHIVE. The rows are already in the
    repository and out of the database; what is late is only the megabytes,
    and the daily schedule is still there as the backstop. It is recorded,
    not swallowed.
    """
    try:
        result = _rpc("request_reclaim", {"p_table": table})
        if isinstance(result, list):
            result = result[0] if result else {}
        print(f"reclaim requested for {table}: {result}")
        return result
    except Exception as e:
        print(f"  ! could not request a reclaim of {table} ({e}); the daily "
              f"schedule will return the space instead", file=sys.stderr)
        return {"ok": False, "error": str(e)[:300]}


def is_committed(rel_path, branch=None, root=None):
    """Is this exact file on the REMOTE branch, as of a fresh fetch?

    The prune asks git rather than trusting the phase order, because a commit
    step that silently did nothing - a push that failed, a path that was not
    added - looks exactly like a successful one from here.

    LOCAL HEAD IS NOT THE ARCHIVE (plan v2 P1.5). This used to check HEAD, and
    the workflow's push loop could run out after four failures and still exit
    0 (its last command was a `git pull --rebase` that succeeded). The file
    was then "in HEAD" - on a runner about to be thrown away - and the rows
    would have been deleted with no copy anywhere. Now the blob on
    origin/<branch>, fetched here, must be byte-identical to the file read
    back from disk. FETCH_HEAD rather than origin/<branch>, because a shallow
    CI checkout need not have a remote-tracking ref for the branch.
    """
    import subprocess
    root = root or _root()
    branch = branch or os.environ.get("GITHUB_REF_NAME") or "main"
    try:
        f = subprocess.run(["git", "fetch", "--quiet", "origin", branch],
                           cwd=root, capture_output=True, text=True)
        if f.returncode != 0:
            return False, f"git fetch origin {branch} failed: {f.stderr.strip()[:200]}"
        remote = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"FETCH_HEAD:{rel_path}"],
                                cwd=root, capture_output=True, text=True)
        if remote.returncode != 0:
            return False, f"not on origin/{branch}"
        local = subprocess.run(["git", "hash-object", "--", rel_path],
                               cwd=root, capture_output=True, text=True)
        if local.returncode != 0:
            return False, "cannot hash the local file"
        if local.stdout.strip() != remote.stdout.strip():
            return False, f"differs from origin/{branch}"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"git unavailable: {e}"
    return True, f"on origin/{branch}"


def run_one(spec, name, args):
    """Export and prune in one call - the local and manual path.

    The workflow does NOT use this: it runs --export-only, commits and pushes
    the files, then runs --prune-only, so a failure between the two leaves the
    rows in the database rather than in neither place.
    """
    rc = export_one(spec, name, args)
    if rc or not args.commit:
        if not args.commit:
            print("\n--dry-run: exported to the repository, nothing deleted.")
        return rc
    return prune_one(spec, name, args)


MANIFEST = os.path.join("web", "public", "archive", "index.json")


def record_manifest(name, spec, asset_name, rows, gzip_bytes, lo, hi, cutoff):
    """Append this archive to the repo's public index, newest range last.

    NO TOKEN TO READ IT. The index carries what a page needs to decide
    whether to offer the archive at all - dataset, range, row count, size -
    and the asset NAME rather than a signed URL, because a URL would expire
    and a private repo's asset needs the server to fetch it anyway. The rows
    come from /api/archive, which holds the token.

    Re-archiving the same range replaces its entry rather than adding a
    second: the asset is overwritten in the Release too, so two entries would
    describe one file.
    """
    path = os.path.join(_root(), MANIFEST)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as fh:
            index = json.load(fh)
    except (OSError, ValueError):
        index = {"datasets": {}}
    index.setdefault("datasets", {})

    entry = {
        "asset": asset_name,
        "rows": rows,
        "gzip_bytes": gzip_bytes,
        "from": str(lo)[:10],
        "to": str(hi)[:10],
        "archived_through": cutoff.isoformat(),
        "archived_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    ds = index["datasets"].setdefault(name, {"table": spec["table"],
                                             "release_tag": spec["tag"],
                                             "assets": []})
    ds["table"] = spec["table"]
    ds["release_tag"] = spec["tag"]
    ds["assets"] = [a for a in ds.get("assets", []) if a.get("asset") != asset_name]
    ds["assets"].append(entry)
    ds["assets"].sort(key=lambda a: a.get("from", ""))
    ds["rows_archived"] = sum(a.get("rows", 0) for a in ds["assets"])

    index["updated_at"] = entry["archived_at"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"manifest: {MANIFEST} now lists {len(ds['assets'])} "
          f"{name} asset(s), {ds['rows_archived']:,} rows archived")


if __name__ == "__main__":
    sys.exit(main())
