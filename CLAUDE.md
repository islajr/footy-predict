# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Always invoke Python through the venv. A bare `python` misses the venv and fails on `dotenv`
before reaching any project code:

```bash
.venv/bin/python -m pytest -q                        # full suite (245 tests)
.venv/bin/python -m pytest tests/test_factors.py -q  # one file
.venv/bin/python -m pytest tests/test_markets.py::test_name -q   # one test
.venv/bin/python -m pytest --cov=src                 # with coverage
```

Install: `pip install -e ".[dev]"` (adds `telegram` extra for the bot). Python 3.11+; the
venv currently runs 3.14.

Migrations — two paths, and they are not equivalent:

```bash
.venv/bin/python -m migrations.run --status   # what's applied
.venv/bin/python -m migrations.run --all      # apply pending (.sql AND .py, tracked)
```

`Database._init_db` (`src/database.py:342`) also auto-applies `migrations/*.sql` on every
instantiation, untracked, swallowing every exception. It never runs the `.py` migrations —
those carry the data transformations (team canonicalisation, dedupe passes) and only
`migrations/run.py` applies them. A new `.py` migration will not take effect until someone
runs the runner explicitly.

CLI (entry point `footy` = `src.cli:cli`): `status`, `fetch`, `fetch-upcoming`,
`load-history`, `derive-standings`, `analyze matchweek|match`, `picks`, `report`, `parlay`,
`record`, `accuracy`, `calibrate`, `serve`, `bot start`. `cli_guide.md` §5 documents every
flag.

No linter or type checker is configured — there is no ruff/flake8/mypy config and none in
the dev extra. Match surrounding style by hand.

## Architecture

Four layers, data flowing one direction: ingestion → SQLite → analysis → presentation.
`architecture.md` has the pipeline diagrams; the parts below are the ones that require
reading several files to see.

### The analysis chain

`AnalysisEngine.analyze_match` (`src/analysis/engine.py:91`) is the only orchestrator:

1. Runs all 16 functions in `ALL_FACTORS` (`src/analysis/factors.py:2118`). Each takes the
   same keyword signature `(db, home_id, away_id, league_id, season)` and returns a
   `FactorResult(name, score, confidence, rationale, data_points)` — score in [-1, +1] from
   the home team's perspective, confidence in [0, 1]. A factor that raises is caught and
   degraded to a zero-confidence result, so the analysis never fails on one bad factor.
2. Aggregates with confidence-weighted averaging (`_aggregate`, `engine.py:364`):
   `Σ(score × weight × confidence) / Σ(weight × confidence)`. Sparse data dilutes itself.
3. Flattens selected `data_points` into a `patterns` dict (`_gather_patterns`,
   `engine.py:455`) — this is the only contract between factors and market models. Adding a
   factor field that a market needs means adding it here too.
4. `evaluate_all_markets` (`src/analysis/markets.py:2183`) runs every `model_*` function,
   each wrapped in its own try/except, and sorts HIGH → MEDIUM → LOW then by probability.

Adding a factor: write the function, register it in `ALL_FACTORS`, add a weight to
`FACTOR_WEIGHTS` in `src/config.py:355`. That dict has an `assert` that weights sum to 1.0
at import time, so any addition must take weight from existing factors. The form cluster
(`recent_form`, `home_form`, `away_form` = 0.415 combined) is deliberately dominant and
documented as not-to-be-reduced for new factors.

### Both sides of every market are stored

Every model emits *all* pick directions — Over *and* Under, all three 1X2 outcomes — as
separate `predictions` rows keyed `UNIQUE(match_id, market, pick)`. Roughly 65 picks per
fixture. The consequence is load-bearing and easy to trip over: per-market hit rates
mechanically converge on 50% and "overall accuracy" (~49.4%) is an artifact of the storage
scheme, not a measurement. **Only confidence tiers and Brier score mean anything.** A
change that appears to move per-market accuracy has almost certainly changed nothing.

### Probability shrinkage

Raw logistic and Poisson outputs are systematically overconfident, so two shrinkage
functions sit between model and output: `_shrink_1x2` (`markets.py:178`) pulls 1X2 30%
toward the European prior (0.46/0.26/0.28), and `_shrink_lambda` (`markets.py:209`) pulls
Poisson λ 25% toward a 1.35 league mean. Both exist because measured HIGH-tier hit rates
ran 12–15pp below stated probability. Do not remove them to "fix" low-looking
probabilities — `CONFIDENCE_THRESHOLDS["HIGH"]` was raised to 0.80 to match post-shrinkage
scale.

### Calibrated weights are loaded per call site, not centrally

`Calibrator.load_weights()` reads `data/weights.json` (falling back to `FACTOR_WEIGHTS`) and
every construction site passes the result in: `src/cli.py` does it at five separate
commands, `src/api/deps.py:21` caches it on app state, `telegram_bot.py` does it per
handler. `AnalysisEngine(db)` with no `weights` argument silently uses config defaults
instead of the calibrated file — correct in tests, wrong in a new production call site.

### Team identity

Three sources spell clubs three ways ("Man United" / "Manchester United FC" /
"Manchester United"). `src/team_names.py` reduces any spelling to a `canonical_key` via
curated `ALIASES` then mechanical normalisation, and `teams` has a unique index on it.
Without this a club's history splits across two rows and form, H2H and venue factors
silently read half the data. Any new ingestion path must resolve identity through
`Database.upsert_team`, never by inserting a name directly.

### Fixture identity

Two unique indexes, both needed (`src/database.py:101` and `:114`):
`(league_id, season, home, away, date(date))` for CSV rows that carry midnight where the API
carries true kickoff, and a partial `(league_id, season, home, away, matchday)` for rows
that carry a matchday, because football-data.org revises provisional kickoffs and the
revised row would otherwise land as a new fixture. The date key stays because split-format
leagues (Scottish Premiership, Belgian Pro League) legitimately repeat a pairing in a
season. Earlier versions keyed on nullable `api_football_id` and accumulated 11,609
duplicate rows.

### Quota and caching

API-Football's free tier is 100 requests/day and 10/minute. `CacheManager`
(`src/fetchers/cache.py`) keys on a SHA-256 of URL + sorted params so logically identical
requests collapse to one entry, with per-resource TTLs in `config.CacheTTL`. The client
enforces the per-minute limit with an in-memory sliding window, and `get_smart_fetch_plan`
(`src/api/routes/fetch.py:27`) narrows the league set by weekday. Any new endpoint call
goes through the cache — a direct httpx call bypasses both quota accounting and the limiter.

Season keys are derived from the calendar: `LeagueConfig.get_current_season`
(`src/config.py:142`) returns `now.year if now.month >= 8 else now.year - 1` for European
leagues, calendar year for `season_format="calendar"` (Eliteserien).

## Data integrity

The database is the accumulated asset here, and several past bugs were destructive rather
than merely wrong. Back up before any schema or bulk-write change — the user has asked for
this explicitly:

```bash
cp footy.db "footy.db.backup-$(date +%Y%m%d-%H%M%S)"
```

Three invariants hold the standings path together. All three were absent once and each
absence cost real rows — do not relax any of them without reading `HANDOFF.md`:

- `upsert_standing` (`src/database.py`) ends its conflict clause with
  `WHERE excluded.played > 0 OR standings.played = 0`. Without that trailing condition a
  blank incoming row overwrites a complete stored one; it destroyed 53 rows in one fetch.
- `fetch_and_store_standings` (`src/fetchers/football_data_org.py`) takes its season label
  from the response's own `season` block, not from today's date, and declines a response
  whose totals contradict that block (`_stale_table_reason`) or that carries no block at
  all. During a July–August turnover the calendar and the payload disagree by a full
  season in both directions.
- `src/standings.py` rebuilds tables from stored match results (`footy derive-standings`).
  Its write **replaces rather than merges** — `delete_standings_except` prunes first,
  because leftover rows sitting at position 1 keep the repaired table failing
  `standings_are_ranked`. It declines a table that ranks nothing (not overridable), a
  match set where some pair of teams never met in a completed season (an identity split —
  see below), and an existing table that already ranks.

`HANDOFF.md` carries the evidence, the verification queries and a list of analyses already
ruled out. Its three tasks landed 2026-08-09; it survives only for the four residual
`2026-27` standings tables holding 2025-26 data, and should be deleted once those are
cleared.

Nine league-seasons cannot be derived because one club is stored under two `teams` rows —
`CA Osasuna` / `Osasuna` (`canonical_key` strips `fc`, `cf`, `cd`, `as` but not `ca`),
`1. FSV Mainz 05` / `Mainz`, six pairs in Liga Portugal 2025-26, and others. Fixing that is
an `ALIASES` entry in `src/team_names.py` plus a dedupe pass.

A factor with no data must return low confidence, not a confident zero — a 0.0 score at
confidence 0.85 survives the weighted average and drags the aggregate toward neutral, which
is worse than returning nothing. `standings_are_ranked()` (`src/analysis/factors.py:169`)
exists for exactly this and gates both standings-reading factors.

## Conventions

Conventional commits, incremental, each with the trailer
`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Commit bodies in this repo explain
the reasoning and correct prior claims explicitly where they were wrong — match that.

Docs are load-bearing and kept reconciled with the code: `cli_guide.md` (user-facing
workflows, §10 data integrity, §14 troubleshooting), `architecture.md` (pipelines, formulas),
`CODEBASE.md` (file-by-file), `simulation_report_phase_a.md` (accuracy measurements).
Figures like test counts, row counts and hit rates appear in several of these plus
`README.md`; changing one means updating the others in the same commit.

`footy.db` and its backups are gitignored — the repository carries code and documentation,
not data.

