# Footy Predict — Complete CLI Guide

> **Local-first. Decision-support. No hosting required.**
> Version: August 2026 · 194 tests passing · HIGH accuracy 80.3% · MEDIUM 67.0% · Brier 0.2037
> 16 factors · 15 market families (~65 picks/fixture) · 12 leagues · 21,935 matches

---

## Table of Contents

1. [What This Is — And What It Isn't](#1-what-this-is--and-what-it-isnt)
2. [How the Model Works](#2-how-the-model-works)
3. [Installation & First-Time Setup](#3-installation--first-time-setup)
4. [Configuration Reference](#4-configuration-reference)
5. [All CLI Commands](#5-all-cli-commands)
6. [The Weekly Operating Workflow](#6-the-weekly-operating-workflow)
7. [Scenario Recipes](#7-scenario-recipes)
8. [Understanding the Output](#8-understanding-the-output)
9. [Current Shortcomings — Honest Assessment](#9-current-shortcomings--honest-assessment)
10. [Data Integrity — What Was Fixed and What to Watch](#10-data-integrity--what-was-fixed-and-what-to-watch)
11. [Season Readiness — Is the App Ready?](#11-season-readiness--is-the-app-ready)
12. [Where to Go From Here — Impact vs Effort Analysis](#12-where-to-go-from-here--impact-vs-effort-analysis)
13. [How to Win — The Complete Strategy](#13-how-to-win--the-complete-strategy)
14. [Troubleshooting](#14-troubleshooting)
15. [Local-First Operating Model](#15-local-first-operating-model)

---

## 1. What This Is — And What It Isn't

### What It Is

Footy Predict is a **decision-support system** for building football parlays. It takes the cognitive burden of analysis off you by:

- Collecting and storing historical match data, team statistics, form tables, head-to-head records, and standings from free APIs
- Running a 16-factor weighted analysis model across every upcoming fixture
- Generating ranked picks across 15 market families with stated probabilities and detailed rationale
- Filtering those picks to a **value zone** — picks where the model is confident AND the implied odds are worth including in a parlay
- Presenting everything in a scannable, plain-English report you can use as the starting point for your own research

The output is: *"here is what the data says, here is why, here is how confident we are."* You read it, you do your own research, you make the final decision.

### What It Is Not

- It is **not an automated betting system**. It never places bets or tells you definitively to bet on something.
- It is **not infallible**. An 80% HIGH confidence hit rate means 1 in 5 HIGH picks is wrong. At 50 legs, roughly 10 legs will lose on an average week.
- It is **not a replacement for your knowledge**. A model does not know that a key player is suspended or that the manager was sacked on Wednesday. That's your edge — use the model's signal, then apply context.
- It is **not yet profitable on its own terms** until you verify that its picks have a real edge over SportyBet's offered odds. That requires running it through at least one full season with records kept.

### The Rationale Behind Building This

Football betting markets are inefficient in specific, exploitable ways:

1. **Complexity penalty**: Evaluating 40+ fixtures weekly across 12 leagues is cognitively impossible without tools. Most bettors under-research or rely on gut feel. A systematic approach beats gut feel in aggregate.

2. **Form bias**: Humans overweight recent results ("Liverpool just won 5 in a row, they must be good") without adjusting for opponent quality, venue, or regression to the mean. The model corrects for this.

3. **Accumulator structure**: A 50-leg parlay at 80% per leg with fair odds is approximately breakeven. Any genuine edge — even 3-5% per pick over the bookmaker's implied probability — compounds explosively in a large accumulator.

4. **Free data availability**: API-Football provides stats free at 100 requests/day, and football-data.co.uk provides unlimited historical CSVs. This project mines both systematically and builds the analysis layer on top.

---

## 2. How the Model Works

### The Factor Layer (16 Factors)

Every match analysis runs 16 independent factors. Each produces a **score** (−1.0 to +1.0, negative = favours away) and a **confidence** (0.0 to 1.0, based on data availability):

| Factor | What It Measures | Data Source | Coverage |
|---|---|---|---|
| `recent_form` | Last 10 results PPG, win rate, form string, trend | `matches` DB | 100% |
| `home_form` | Home-only win rate, GPG, CPG, CS rate, streak | `matches` DB | 100% |
| `away_form` | Away-only win rate, GPG, CPG, CS rate, streak | `matches` DB | 100% |
| `head_to_head` | H2H record, BTTS rate, avg goals, venue-specific | `matches` DB | 100% |
| `league_position` | Table position, points gap, GD, form string | `standings` DB | **64%** |
| `momentum` | Last 3 vs previous 3 PPG — improving or declining? | `matches` DB | 100% |
| `scoring_frequency` | Goals per game (home/away split), blank game rate | `matches` DB | 100% |
| `defensive_record` | Goals conceded per game, clean sheet rate | `matches` DB | 100% |
| `scoring_patterns` | BTTS rate, Over 2.5 rate, Poisson λ, O/U probs | `team_season_stats` | 100% |
| `goal_timing` | 1st half vs 2nd half goal split, WEH historical rate | `match_stats` DB | 100% |
| `rest_days` | Days since last match for each team, congestion flags | `matches` DB | 100% |
| `rivalry` | Derby detection by name heuristic + known pairs | Static config | 100% |
| `attacking_matchup` | Home GPG vs Away CPG and vice versa — net attack edge | `matches` DB | 100% |
| `venue_advantage` | Team's home win rate vs their away win rate | `matches` DB | 100% |
| `opponent_strength` | Strength-adjusted PPG using opponent standings rank | `matches` + `standings` | **67.5%** |
| `xg_quality` | xG per game, xG vs actual ratio, xG against | `match_stats.expected_goals` | **0%** |

> [!WARNING]
> **`xg_quality` has 0% coverage and will not self-heal.** Earlier versions of this guide said it "populates automatically once the season starts and you run `footy fetch --stats`." That is wrong, and the reason is worth understanding — see [Section 10](#10-data-integrity--what-was-fixed-and-what-to-watch). The factor contributes 0 to analysis but breaks nothing.

> [!NOTE]
> `league_position` and `opponent_strength` sit at ~65% because standings are only stored for league-seasons that were fetched from an API — the historical CSV bulk-load carries results but no tables. Both improve as you fetch standings through the season.

### The Engine Layer

1. All 16 factors run for each match
2. Factor scores are **weighted** (see `FACTOR_WEIGHTS` in `config.py`) and aggregated into a single `overall_score` (−1 to +1)
3. Confidence is computed as a weighted average of factor confidences
4. The engine classifies data quality as HIGH/MEDIUM/LOW based on coverage

### The Market Layer

The `overall_score` and individual factor data feed into 15 market families:

| Market | How the probability is computed |
|---|---|
| `1x2` | Logistic mapping of `overall_score` → P(home), P(draw), P(away), blended with historical rates, then **shrunk 30% toward European prior** (H=0.46, D=0.26, A=0.28) |
| `draw-no-bet` | `P(DNB home) = P(home wins)` — correctly reflects that DNB wins only on outright win (draw returns stake) |
| `double-chance` | Combinations of the shrunk 1X2 probs |
| `over-under-*` | Poisson model with λ shrunk 25% toward league mean (1.35 GPG), Dixon-Coles correction near threshold |
| `team-over-under-*` | Per-team Poisson with λ shrunk — venue-split GPG weighted 70/30 overall/venue |
| `win-to-nil` | `P(win) × P(clean sheet)` — both shrunk independently |
| `win-either-half` | Three-way blend: outright quality (45%) + historical WEH rate (35%) + H1 win rate (20%), capped at 0.82 |
| `handicap` | Expected goal differential from `attacking_matchup` factor |
| `gg-ng` | Both Teams to Score from historical BTTS rate + Poisson scoring participation |
| `1x2` (1UP sub-picks) | Poisson P(team ever leads by ≥1) — lead trigger probability. Stored under market `1x2` as picks `Home 1X2 - 1UP` / `Away 1X2 - 1UP` |
| `1x2` (2UP sub-picks) | Poisson P(team ever leads by ≥2 OR outright wins) — safeguard pick. Stored as `Home 1X2 - 2UP` / `Away 1X2 - 2UP` |
| `first-half-over-under-*` | Poisson on FT λ × team's historical H1 goal % (from `goal_timing`), 25% shrunk toward H1 prior (0.50 goals/team), Dixon-Coles correction |
| `second-half-over-under-*` | Same using H2 goal % and H2 prior (0.56 goals/team) — SH is typically higher-scoring |
| `home/away-team-first-half-over-under-*` | Per-team H1 Poisson λ |
| `home/away-team-second-half-over-under-*` | Per-team H2 Poisson λ |

> [!IMPORTANT]
> **The engine stores both sides of every binary market.** For `over-under-2.5` it records "Over 2.5" at p=0.664 *and* "Under 2.5" at p=0.336. This is by design — it lets you see the full picture and lets `--market` filters show both directions. But it has a consequence that trips up anyone reading raw accuracy numbers: since exactly one side of each pair always wins, **per-market hit rates mechanically converge on 50%**, and any "overall accuracy" figure lands near 49–50% no matter how good the model is.
>
> Only **confidence tiers** and the **Brier score** measure quality. Tiers filter to the side the model actually favours; Brier scores probability calibration rather than pick selection. Never read the flat 50% next to `over-under-2.5` in a simulation report as a defect.

### Calibration

After each simulation or real-season tracking run, the engine calibrates factor weights based on which factors correlate most with correct predictions. The Calibrator updates weights in `calibration_weights.json` if any factor deviates significantly from baseline accuracy.

### Probability Shrinkage — Why It's Critical

Raw logistic and Poisson models produce probabilities that are too extreme — they can say P=0.93 for outcomes that hit only 78% of the time. The system applies **three layers of shrinkage**:

1. **1X2 shrinkage**: 30% regression toward European priors. Caps: home ≤ 0.72, away ≤ 0.65.
2. **Poisson λ shrinkage**: 25% regression toward league mean (1.35 GPG) for all goals-based models.
3. **Dixon-Coles O/U correction**: When expected total is within 0.6 goals of the line, shrink toward 0.5.

This took HIGH accuracy from 77.2% → 79.9% and the Brier score from 0.2138 → 0.2064. The August data-integrity work then moved these to 80.3% and 0.2037.

---

## 3. Installation & First-Time Setup

### Prerequisites

- Python 3.11 or higher
- Git
- ~500 MB disk space for the database
- API keys (free, no card required):
  - **API-Football**: [dashboard.api-football.com/register](https://dashboard.api-football.com/register) — primary source, 100 req/day, 10 req/min
  - **Football-Data.org**: [football-data.org/client/register](https://www.football-data.org/client/register) — secondary, 7 leagues

### Installation

```bash
git clone https://github.com/islajr/footy-predict.git
cd footy-predict

python3 -m venv .venv
source .venv/bin/activate       # Linux/Mac
# .venv\Scripts\activate.bat    # Windows

pip install -e ".[dev]"

footy --help
```

### Environment Setup

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# Required — API-Football (primary data source, 100 req/day free)
API_FOOTBALL_KEY=your_api_football_key_here

# Optional — Football-Data.org (secondary, 7 leagues, offloads quota)
FOOTBALL_DATA_ORG_KEY=your_fdo_key_here

# Database path (default: ./footy.db — keep it here for simplicity)
# DATABASE_URL=sqlite:///./footy.db

# Value zone odds floor (default 1.32 ≈ 1.20 real SportyBet odds after margin)
# VALUE_MIN_ODDS=1.32
```

### First-Time Bootstrap (New Installation)

```bash
# Step 1: Load historical data from free CSVs (no API quota used)
footy load-history --seasons 5          # all CSV-backed leagues, 5 seasons
footy load-history --league premier-league --seasons 5   # single league

# Step 2: Run database migrations (safe to re-run; idempotent)
python migrations/run.py

# Step 3: Fetch current standings and team stats (costs ~5-10 calls per league)
footy fetch --league premier-league
footy fetch --league la-liga
# ... spread the remaining leagues over 2-3 days

# Step 4: Verify
footy status

# Step 5: First analysis
footy report
```

> [!IMPORTANT]
> **API Quota**: 100 requests/day and 10 requests/minute on API-Football. Do NOT fetch all 12 leagues in one day. Spread over 3-4 days, or use `footy fetch-upcoming` (quota-aware) once the season starts.

---

## 4. Configuration Reference

### Key Config Values (`src/config.py`)

| Setting | Default | What It Does |
|---|---|---|
| `CONFIDENCE_THRESHOLDS["HIGH"]` | `0.80` | Picks at or above this probability are labelled HIGH |
| `CONFIDENCE_THRESHOLDS["MEDIUM"]` | `0.60` | MEDIUM tier floor |
| `VALUE_MIN_ODDS` | `1.32` | Default odds floor for `footy report` and `--min-odds` |
| `FACTOR_WEIGHTS` | see config | Per-factor weight in the aggregated score |
| `CACHE_TTL` | varies | API response cache (standings: 6h, fixtures: 2h) |
| `LEAGUES` | 12 leagues | League slugs, API IDs, CSV codes, season formats |

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `API_FOOTBALL_KEY` | — | Required. Your API-Football key |
| `FOOTBALL_DATA_ORG_KEY` | — | Optional. Football-Data.org key |
| `DATABASE_URL` | `sqlite:///./footy.db` | Database location |
| `VALUE_MIN_ODDS` | `1.32` | Override the value zone odds floor |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`) |

### Supported Leagues

Twelve leagues. `fetch` prefers football-data.org where a code exists (saves API-Football quota); `load-history` uses the CSV code.

| Slug | League | FDO code | CSV code | Season format | Matches in DB |
|---|---|---|---|---|---|
| `premier-league` | Premier League | `PL` | `E0` | european | 3,420 |
| `la-liga` | La Liga | `PD` | `SP1` | european | 2,719 |
| `serie-a` | Serie A | `SA` | `I1` | european | 2,309 |
| `bundesliga` | Bundesliga | `BL1` | `D1` | european | 1,882 |
| `ligue-1` | Ligue 1 | `FL1` | `F1` | european | 1,996 |
| `liga-portugal` | Liga Portugal | `PPL` | `P1` | european | 2,021 |
| `eredivisie` | Eredivisie | `DED` | `N1` | european | 1,838 |
| `super-lig` | Süper Lig | — | `T1` | european | 1,910 |
| `belgian-pro-league` | Belgian Pro League | — | `B1` | european | 1,662 |
| `scottish-premiership` | Scottish Premiership | — | `SC0` | european | 1,146 |
| `eliteserien` | Eliteserien | — | `N0` | **calendar** | 726 |
| `saudi-pro-league` | Saudi Pro League | — | — | european | 306 |

> [!NOTE]
> **`saudi-pro-league` has no CSV source** — `load-history` skips it entirely. It is API-only, so its history is limited to what you fetch.
>
> **`eliteserien` uses `calendar` season format** (2024, not 2024-25) because Norwegian football runs March–December. Its CSV source is also unreliable — see [Section 10](#10-data-integrity--what-was-fixed-and-what-to-watch).

---

## 5. All CLI Commands

### Quick Reference

```
footy --help                        Show all commands
footy COMMAND --help                Show help for a specific command
footy -v COMMAND                    Run with debug logging
```

Full command list: `status`, `fetch`, `fetch-upcoming`, `load-history`, `analyze matchweek`, `analyze match`, `picks`, `report`, `parlay`, `record`, `accuracy`, `calibrate`, `serve`, `bot start`.

---

### `footy status`

**What it does**: Shows API quota usage, cache stats, database record counts, and the day's quota budget.

```bash
footy status                        # no options
```

**Use it**: Every morning before fetching, to know how many API calls remain.

---

### `footy fetch`

**What it does**: Fetches fixtures, standings, and team statistics for a league, storing everything locally.

```bash
footy fetch                              # all configured leagues
footy fetch -l premier-league            # single league (also --league)
footy fetch --stats                      # also backfill per-match statistics
footy fetch -l saudi-pro-league --stats --stats-limit 20   # cap the backfill
```

| Option | Meaning |
|---|---|
| `-l`, `--league TEXT` | League slug. Omit for all. |
| `--stats` | Also fetch per-match statistics (xG, possession, passes) |
| `--stats-limit INTEGER` | Max finished matches to backfill (default 20) |

**Quota cost**: ~5–10 calls per league. `--stats` adds ~1 call per match, rate-limited to 10/minute.

> [!WARNING]
> `--stats` only reaches matches that have an `api_football_id`. Matches loaded from CSV or football-data.org do not have one, so `--stats` silently skips them. See [Section 10](#10-data-integrity--what-was-fixed-and-what-to-watch).

---

### `footy fetch-upcoming`

**What it does**: A quota-aware weekend-optimised fetch. Calculates the remaining daily budget and fetches only the leagues and fixtures relevant to the coming days.

```bash
footy fetch-upcoming                # next 7 days, auto budget
footy fetch-upcoming -d 4           # look ahead 4 days (also --days)
footy fetch-upcoming --budget 30    # spend at most 30 API calls
footy fetch-upcoming -l premier-league   # single league
```

| Option | Meaning |
|---|---|
| `-d`, `--days INTEGER` | Sync fixtures for the next N days (default 7) |
| `--budget INTEGER` | Max API-Football calls to spend in this run |
| `-l`, `--league TEXT` | Restrict to one league slug |

**Use it**: Every Thursday evening, as step one of the weekly workflow.

---

### `footy load-history`

**What it does**: Downloads historical match CSVs from football-data.co.uk (free, no key, no quota) and bulk-loads them. No API cost at all.

```bash
footy load-history                           # all CSV leagues, last 3 seasons
footy load-history -l premier-league          # single league
footy load-history -s 5                       # last 5 seasons (also --seasons)
footy load-history -l serie-a -s 8            # 8 seasons of Serie A
```

| Option | Meaning |
|---|---|
| `-l`, `--league TEXT` | League slug. Omit for all leagues with CSV data. |
| `-s`, `--seasons INTEGER` | Number of past seasons to load (**default 3**) |

> [!NOTE]
> There is no `--start-season` option. Use `--seasons N` to control depth.
>
> **`--seasons` counts back from the last season actually played, not the current calendar season.** In pre-season (August, before month 9) European leagues step back one season, so `--seasons 1` loads the just-completed season rather than an empty CSV for a season that has not started. Calendar-format leagues like Eliteserien step back before March.

**Use it**: Once on setup, and again at the start of each new season to pull the just-completed season's full CSV.

---

### `footy analyze matchweek`

**What it does**: Runs the full 16-factor analysis on all upcoming fixtures in the window, with a detailed per-match breakdown.

```bash
footy analyze matchweek                          # all leagues, upcoming
footy analyze matchweek -l premier-league        # single league
footy analyze matchweek --from 2026-08-16 --to 2026-08-18
```

| Option | Meaning |
|---|---|
| `-l`, `--league TEXT` | League slug. Omit for all. |
| `--from TEXT` | Start date (YYYY-MM-DD) |
| `--to TEXT` | End date (YYYY-MM-DD) |

**Use it**: For the deep-dive view. `footy report` is more efficient for the weekly routine.

---

### `footy analyze match`

**What it does**: Deep single-match analysis with full factor breakdown, narrative summary, and all market recommendations.

```bash
footy analyze match "Arsenal vs Chelsea"
footy analyze match "Barcelona vs Real Madrid"
```

Takes a single `FIXTURE` argument in the form `"Team A vs Team B"`. **No options** — there is no `--league` flag; team names are matched across all leagues.

**Use it**: To understand a specific fixture in detail before committing it to a parlay.

---

### `footy picks`

**What it does**: Displays all picks from upcoming fixtures, grouped by confidence tier. The broad view.

```bash
footy picks                                  # everything, no filter
footy picks -c HIGH                          # HIGH only
footy picks -c HIGH -o 1.32                  # HIGH + value zone (also --min-odds)
footy picks -c MEDIUM -o 1.32                # recommended parlay filter
footy picks -m over-under-2.5                # single market (also --market)
footy picks -m first-half-over-under         # all 1st half O/U picks
footy picks -l premier-league -c HIGH        # Premier League only
```

| Option | Meaning |
|---|---|
| `-c`, `--min-confidence [HIGH\|MEDIUM\|LOW]` | Tier floor (case-insensitive) |
| `-l`, `--league TEXT` | League slug filter |
| `-m`, `--market TEXT` | Market code filter |
| `-o`, `--min-odds FLOAT` | Minimum model implied odds |

**Confidence tiers**:
- `HIGH` (≥0.80 probability): hit rate **80.3%**
- `MEDIUM` (0.60–0.80): hit rate **67.0%**
- `LOW` (<0.60): hit rate ~35%. Information only — never in a parlay.

---

### `footy report` ⭐ (Primary Weekend Command)

**What it does**: Generates the weekly decision-support report. Per fixture: the best value-zone pick, its rationale, risk flags, and 4 alternatives.

```bash
footy report                                 # MEDIUM, ≥1.32 odds, next 7 days
footy report -d 3                            # this weekend only (also --days)
footy report -o 1.40                         # stricter odds floor
footy report -c HIGH                         # HIGH-confidence picks
footy report -c HIGH -o 0                    # ALL HIGH picks, no odds filter
footy report -l premier-league               # one league
footy report -f weekend.txt                  # save to file (also --output)
footy report -d 3 -f weekend.txt             # combine
```

| Option | Meaning |
|---|---|
| `-o`, `--min-odds FLOAT` | Minimum model implied odds (default `VALUE_MIN_ODDS`) |
| `-c`, `--min-confidence [HIGH\|MEDIUM\|LOW]` | Tier floor (**default MEDIUM**) |
| `-l`, `--league TEXT` | League slug filter |
| `-d`, `--days INTEGER` | Days ahead to look for fixtures |
| `-f`, `--output PATH` | Save the report as plain text |

> [!IMPORTANT]
> **`report` defaults to MEDIUM, not HIGH.** This is deliberate: HIGH picks (P≥80%) have model odds ≤1.25, below the 1.32 value floor. The real value zone for accumulators is MEDIUM (P 60–76%, model odds 1.32–1.67). Use `-c HIGH` when you specifically want maximum certainty regardless of odds.

**Output structure per fixture**:
```
  Arsenal vs Chelsea  |  Premier League  |  2026-08-16  data: HIGH
  🟢 TOP PICK:  Away Win (2)  67%  1.49 odds  HIGH
     Away form: W W W D W (2.4 PPG away). Arsenal have won 4 of last 5 away...
  ⚠ Derby match detected — historical upset frequency above average
     Also: Away 1UP 81% 1.24  |  Over 2.5 71% 1.41  |  WEH Away 72% 1.39
```

> [!IMPORTANT]
> Odds shown are **model implied odds** = `1/P(model)`. SportyBet builds in ~10% margin, so their offered odds will be lower. Model odds of 1.40 ≈ SportyBet 1.25–1.30. The default 1.32 floor already accounts for this (targeting ~1.20 real).

---

### `footy parlay`

**What it does**: Builds a suggested parlay of N picks, one per fixture, highest probability first.

```bash
footy parlay -n 50                           # 50 legs (also --size)
footy parlay -n 50 -c MEDIUM -o 1.32         # value zone parlay
footy parlay -n 30 -c HIGH -o 1.40           # stricter, 30 legs
footy parlay -n 50 -l premier-league -c HIGH
```

| Option | Meaning |
|---|---|
| `-n`, `--size INTEGER` | Number of picks for the parlay |
| `-c`, `--min-confidence [HIGH\|MEDIUM\|LOW]` | Tier floor |
| `-l`, `--league TEXT` | League slug filter |
| `-o`, `--min-odds FLOAT` | Minimum model implied odds |

> [!NOTE]
> For 50 legs at 80% per leg, P(all win) ≈ 0.80^50 ≈ 1 in 7,000. That is expected — the payout is enormous when it lands. The model's role is ensuring each leg has a genuine probability basis.

---

### `footy record`

**What it does**: Records actual results of finished matches against predictions, building the accuracy database.

```bash
footy record                        # no options — records all unrecorded finished matches
```

There is **no `--league` filter**; it processes everything outstanding.

**When**: Every Monday morning after the weekend's matches finish.

> [!IMPORTANT]
> **Run this every week.** Calibration only works if results are recorded. Skipping it means the model cannot self-improve.

---

### `footy accuracy`

**What it does**: The accuracy dashboard — how picks have performed against recorded results.

```bash
footy accuracy                               # all markets
footy accuracy -m over-under-2.5             # one market (also --market)
footy accuracy -l premier-league             # one league (also --league)
footy accuracy -m 1x2 -l la-liga             # combined
```

| Option | Meaning |
|---|---|
| `-m`, `--market TEXT` | Market code filter |
| `-l`, `--league TEXT` | League slug filter |

**Includes**: hit rate by tier, per market, per league; Brier score; trend over time.

> [!NOTE]
> Remember the both-sides caveat from [Section 2](#the-market-layer): an unfiltered per-market rate near 50% is an artifact. Read the tier breakdown.

---

### `footy calibrate`

**What it does**: Runs weight calibration, updating factor weights from recorded prediction accuracy.

```bash
footy calibrate                     # calibrate from settled predictions
footy calibrate --reset             # restore default weights
```

| Option | Meaning |
|---|---|
| `--reset` | Reset weights to defaults instead of calibrating |

**When**: Monthly, or after recording ~50 new results. Requires 100+ settled predictions (already satisfied — 13,408 in DB).

**How**: Compares factor confidence on correct vs incorrect predictions. Factors with consistently higher confidence on correct picks get upweighted.

---

### `footy serve`

**What it does**: Starts the FastAPI local web application — a browser UI over the same database and engine.

```bash
footy serve                          # default host/port
footy serve --port 8080              # custom port
footy serve --host 0.0.0.0 --port 8000   # expose on your LAN
```

| Option | Meaning |
|---|---|
| `--host TEXT` | Host interface to bind to |
| `--port INTEGER` | Port to run on |

**Use it**: When you'd rather read the report in a browser, or want to check picks from your phone on the same network.

---

### `footy bot start`

**What it does**: Starts the Telegram bot, delivering analysis and picks to your Telegram chat.

```bash
footy bot start                     # runs in foreground
```

**Requires**: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Create a bot via [@BotFather](https://t.me/botfather).

---

## 6. The Weekly Operating Workflow

### Thursday Evening (30 minutes)

```bash
# 1. Check quota before touching anything
footy status

# 2. Fetch upcoming weekend fixtures (quota-aware)
footy fetch-upcoming -d 4

# 3. Confirm the data landed
footy status

# 4. Generate and save the report
footy report -d 4 -f ~/footy/$(date +%Y-%m-%d)-report.txt

# 5. Read it. Research flagged fixtures. Note your initial thoughts.
```

### Friday (20 minutes — research and refinement)

```bash
# Deep-dive fixtures you're unsure about
footy analyze match "Real Madrid vs Barcelona"
footy analyze match "Liverpool vs Man City"

# Check specific markets on a hunch
footy picks -c MEDIUM -o 1.32 -m win-either-half
footy picks -c MEDIUM -o 1.32 -m over-under-2.5
```

### Saturday Morning (10 minutes — final slip)

```bash
# Refresh if quota permits
footy fetch-upcoming -d 2

# Build the parlay
footy parlay -n 50 -c MEDIUM -o 1.32

# Or re-read the report with the final fixture list
footy report -d 2
```

### Monday Morning (10 minutes — record and review)

```bash
footy record          # record weekend results
footy accuracy        # how did the week go?
footy calibrate       # monthly, or after ~50 new results
```

---

## 7. Scenario Recipes

### Scenario 0: The Two Primary Workflows

Everything else depends on which mode you're in.

#### Workflow i — Decision-Support Mode (maximum intelligence, requires time)

```bash
footy picks -c MEDIUM                # all picks at 60%+ confidence, no odds filter
footy report -c MEDIUM -o 0          # per-fixture report, all value visible
```

**What you get:** Every pick where the model is at least 60% confident, unfiltered by odds.

**What you do with it:** Open SportyBet. For each pick, check the actual offered odds. Include it if SportyBet offers ≥1.20 **and** your research doesn't override the model.

**Why this is the most powerful mode:** The model's `1/P` implied odds are not SportyBet's odds. Sometimes they offer 1.40 on something the model rates P=0.72 (their implied P=0.71 — no edge). Sometimes 1.50 on something rated P=0.75 (their implied P=0.67 — an 8pp edge). You cannot tell which from the model alone. Manual comparison finds the mismatches.

**Time:** 30–60 minutes. **Best used:** Thursday or Friday.

#### Workflow ii — Systematic Mode (fast, pre-filtered)

```bash
footy report -c MEDIUM -o 1.32
footy parlay -n 50 -c MEDIUM -o 1.32
```

**What you get:** Picks where model implied odds already exceed 1.32 (≈1.20 real after margin).

**The tradeoff:** Good picks get missed. If SportyBet offers 1.35 on something the model rates 0.82 (model odds 1.22, below the floor), this filter discards what might be the best-value pick on the board.

**Time:** 10–15 minutes. **Best used:** Saturday morning, or any time-pressed week.

#### Combining both

1. **Thursday/Friday**: Workflow i. Build a longlist of 60–70 verified picks.
2. **Saturday morning**: Workflow ii as a sanity check. Investigate strong disagreements.
3. **Final slip**: Start with the ~30 highest-confidence picks from the longlist, fill from Workflow ii, remove anything you have specific concern about.

---

### Scenario 1: The 64,000-Odds Target — The Math

Working backwards from 64,000 combined odds at 48 active legs:

```
Average real odds per leg = 64,000^(1/48) ≈ 1.26
```

At ~10% margin, model implied odds must average ~1.39, i.e. model probability ~0.72 — solidly MEDIUM.

| Per-leg accuracy | P(all 48 win) | Frequency at weekly play |
|---|---|---|
| 70% | 1 in 19 million | Extremely rare |
| 75% | 1 in 1 million | Very rare |
| 80% | 1 in 79,000 | Possible within years of consistent play |
| 85% | 1 in 5,800 | Likely within ~2 years |
| 88% | 1 in 1,500 | Realistic target with good systems |
| 90% | 1 in 430 | Several times per year |

**The key insight:** A 48/48 parlay doesn't require every pick to be nearly certain. It requires an exceptional week — where your ~80% picks all happen to land. The model's job is keeping you selecting from the ~80% pool consistently, so those weeks come more often.

**What picks look like at ~1.26 average real odds:**
- Home 1UP where the home side is clearly dominant: ~1.20–1.35
- Over 1.5 in high-scoring leagues with two attacking sides: ~1.25–1.40
- Double chance 12 for strong home favourites: ~1.20–1.35
- Draw no bet for well-supported away teams: ~1.30–1.60
- Away win for top-table sides visiting mid-table: ~1.40–2.00
- Home under 2.5 for defensive teams in low-stakes fixtures: ~1.20–1.35

Mixed at ~1.25 across 50 picks: **1.25^50 ≈ 70,000 combined.**

---

### Scenario 2: When to Override the Model

The model processes historical form, standings, H2H, and statistical patterns. It is blind to this week.

**Override when:**
- A key player (top scorer, starting GK) is ruled out — the model doesn't know
- The manager changed this week (new-manager bounce isn't modelled)
- One team has a cup final 4 days later and will rotate
- The fixture is a derby (model flags it, but you may know the rivalry better)
- You've watched both teams and one is clearly better than the numbers suggest
- A promoted/relegated team's profile doesn't reflect their new level

**Trust the model when:**
- You have a "feeling" you can't articulate
- Your feeling rests on last week's result rather than underlying performance
- Data quality is HIGH and multiple factors agree
- Your concern is about a pick rated 85%+ with strong H2H support

**The decision rule:** Override only with *specific, recent, non-public information*. Trust the model when you have a vague preference or recency bias.

---

### Scenario A: First Run of the New 2026/27 Season

```bash
# Early August, before kickoff:
footy load-history --seasons 1   # pulls the just-completed season (see anchoring note)
python migrations/run.py         # ensure schema is current
footy calibrate                  # recalibrate on the full dataset

# First weekend (mid-August):
footy status
footy fetch-upcoming
footy report
```

> [!NOTE]
> First 2–3 weeks will show LOW data quality — no season form exists yet. The model falls back to historical data. Trust HIGH picks but stay conservative until 5+ matches of form per team.

### Scenario B: Early Season (Weeks 1–6)

```bash
# Conservative filters early
footy report -o 1.40             # stricter odds floor
footy report -c HIGH             # HIGH only

# Check what the model actually knows
footy analyze match "Arsenal vs Chelsea"
# Look for: data_quality = HIGH, confident factors, deep H2H history
```

Strategy:
- Only pick fixtures where `data_quality = HIGH`
- Prefer markets where historical data dominates: H2H (10+ matches), venue advantage, rivalry
- Avoid teams with big summer transfers or new managers (not modelled)
- Expect lower accuracy until week 8+

### Scenario C: Building a 50-Leg Weekend Parlay

```bash
# 1. Generate the report
footy report -f weekend.txt

# 2. See the builder's suggestion
footy parlay -n 50 -c MEDIUM -o 1.32

# 3. Cross-reference. The parlay picks one per match (highest probability);
#    the report shows the top pick plus 4 alternatives. Substitute from the
#    "Also" list wherever your research disagrees.

# 4. If short on legs:
footy parlay -n 50 -c MEDIUM -o 1.25    # loosen the floor
footy parlay -n 50 -c LOW -o 1.32       # not recommended — LOW hits ~35%
```

**Principles**:
1. One pick per match — never double up on a fixture
2. Take the highest-probability pick that passes your odds floor
3. Research the top 5–10 picks; flag concerns
4. Substitute from "Also" when research disagrees
5. Never include `data_quality = LOW` picks where your own research is inconclusive

### Scenario D: Investigating a Specific Market

```bash
footy picks -c MEDIUM -o 1.32 -m over-under-2.5   # this weekend's O/U 2.5
footy accuracy -m over-under-2.5                  # historical performance
footy analyze match "Atletico Madrid vs Valencia" # the reasoning behind one
# Look at: scoring_frequency, scoring_patterns, expected_total in the rationale
```

### Scenario E: Checking Accuracy After a Bad Week

```bash
footy record
footy accuracy
footy accuracy -m 1x2
footy accuracy -m win-either-half
footy accuracy -l premier-league     # is one league dragging?
footy calibrate                      # if a real drift appears
```

If a market consistently underperforms at a tier, stop using it there until the model improves.

### Scenario F: No Internet / Offline Mode

All data persists locally in SQLite, so analysis works offline:

```bash
footy report             # works offline
footy picks -c HIGH      # works offline
footy analyze matchweek  # works offline
footy accuracy           # works offline
footy serve              # works offline (local web UI)

# Only these need internet:
# footy fetch / fetch-upcoming / load-history
```

### Scenario G: Resetting and Starting Fresh

```bash
# Back up first — always.
cp footy.db footy.db.backup-$(date +%Y%m%d-%H%M%S)

# WARNING: deletes your entire database and all recorded accuracy history.
rm footy.db
footy load-history --seasons 5
python migrations/run.py
footy fetch-upcoming
```

> [!CAUTION]
> Deleting `footy.db` destroys your recorded results and accuracy history, which is the only thing calibration can learn from. Back it up first. Backups matching `footy.db.backup-*` are gitignored.

### Scenario H: The Model Seems Wrong on a Match

1. `footy analyze match "Team A vs Team B"` — read every factor
2. Check what it knows: is H2H available? Is form recent enough?
3. Check `data_quality` — LOW means it's guessing more than knowing
4. Apply your own knowledge: injuries, managerial change, cup hangover
5. Override if you have specific, recent information it doesn't

**The model will be wrong more often on**: early season, teams with recent manager changes, matches with key injuries, and derbies.

---

## 8. Understanding the Output

### Confidence Tiers

| Tier | Probability | Historical Hit Rate | When to Use |
|---|---|---|---|
| HIGH | ≥ 0.80 | **80.3%** | Primary parlay picks |
| MEDIUM | 0.60–0.80 | **67.0%** | The real value zone for accumulators |
| LOW | < 0.60 | ~35% | Informational only. Never in parlays. |

### Implied Odds vs Real Odds

The system shows **model implied odds** = `1 / P(model)`. These are NOT SportyBet's offered odds.

| Model implied odds | Estimated real SportyBet odds | Should include? |
|---|---|---|
| 1.04 | ~0.94 | No — below 1.00, value is negative |
| 1.13 | ~1.02 | Borderline — marginal value |
| 1.32 | ~1.19 | At the minimum (default floor) |
| 1.40 | ~1.26 | Good value |
| 1.60 | ~1.44 | Strong value |
| 2.00 | ~1.80 | High value, higher risk |

The 0.90 multiplier approximates bookmaker margin. It varies: 8–12% for O/U and DNB, 10–15% for 1X2.

### The Value Zone

A pick is in the **value zone** when confidence meets your threshold AND model implied odds ≥ 1.32 (configurable).

### Data Quality

| Quality | Meaning | What to do |
|---|---|---|
| HIGH | 10+ matches of season data, full factor coverage | Trust the model |
| MEDIUM | 5–10 matches, most factors populated | Trust but verify |
| LOW | <5 matches or major data gaps | Add your own research first |

### The Brier Score

Measures **calibration** — how close stated probabilities are to actual hit rates. Lower is better.

- Random (no information): **0.25**
- Current model: **0.2037**
- Perfect calibration: **0.00**

At 0.2037, when the model says 80% outcomes happen roughly 80% of the time — not 50%, not 95%.

### Risk Flags

| Flag | What It Means |
|---|---|
| Derby match | Historical data shows higher upset frequency |
| Fixture congestion | One or both teams played within 3 days |
| Low data quality | Insufficient matches for confident analysis |
| High draw probability | P(draw) > 28% — affects DNB and 1X2 reliability |
| Form volatility | Results highly inconsistent over last 10 matches |

---

## 9. Current Shortcomings — Honest Assessment

### What the Model Does Not Know

| Gap | Impact | Workaround | Fix |
|---|---|---|---|
| **xG data** | `xg_quality` blank — shot quality ignored | Check match stats manually | Needs `api_football_id` backfill — see Section 10 |
| **Injury reports** | Key absences not modelled | Check injury news before including a pick | Phase E |
| **Confirmed lineups** | No lineup data pre-match | Check lineups 1h before KO | Phase E |
| **Manager changes** | New-manager bounce not detected | Flag manually, be cautious | Phase E |
| **Bookmaker odds** | Cannot measure true edge | Use the 0.90 adjustment as proxy | Phase D/E |
| **Standings depth** | `league_position` and `opponent_strength` at ~65% | Fetch standings through the season | Improves with use |
| **Early-season form** | Weeks 1–6 lean on historical data | Use higher odds threshold early | Auto-improves |

### Known Calibration Issues (Still Overconfident)

Some markets remain overconfident at the HIGH tier:

| Market | HIGH hit rate | Stated avg | Gap | Verdict |
|---|---|---|---|---|
| `over-under-0.5` | 99.3% | 96.3% | −3pp | ✓ Well calibrated |
| `double-chance` | 80.0% | 88.7% | +9pp | Usable but short odds |
| `win-either-half` | ~65%* | ~82% | ~17pp | **Avoid at HIGH tier** |
| `team-over-under-1.5` | ~68%* | ~89% | ~21pp | **Use at MEDIUM only** |
| `over-under-2.5` | ~62%* | ~89% | ~27pp | **Use at MEDIUM only** |
| `win-to-nil` (Yes) | 78.4% | 94.3% | +16pp | **Avoid WtN Yes at HIGH** |
| `draw-no-bet` | re-tiered to MEDIUM | — | ✓ Fixed | MEDIUM DNB = 76.2% ✓ |
| `first-half-over-under-*` | Untested | — | — | Build history before HIGH |
| `second-half-over-under-*` | Untested | — | — | Expect SH Over 0.5 solid |
| `home/away-team-*-half-over-under` | Untested | — | — | MEDIUM + odds check initially |

\* Pre-calibration-round-2 figures; exact post-round figures need a season of real data.

> [!CAUTION]
> **Do not include HIGH-tier picks from `win-either-half`, `team-over-under-1.5`, or `over-under-2.5`.** The stated 89%+ is false; actual is 62–68%. Use these at MEDIUM, where stated 60–75% is much closer to reality.

### The Main Limitation: No Real Odds Comparison

The system cannot yet tell you whether a pick is **genuinely good value** against SportyBet's pricing. It can say the model probability is 80% — but if SportyBet has priced it at 80% too, there is no edge.

True edge detection requires comparing `P(model)` against `P(implied by SportyBet odds)`. That is Phase D work (bookmaker odds integration), currently deferred. Until then, treat the observed tier hit rates as evidence the picks are sound, but do not assume every pick is mispriced in your favour.

---

## 10. Data Integrity — What Was Fixed and What to Watch

August 2026 brought a round of data-integrity work. This section records what changed, because some of it alters how you should read older reports.

### Fixed: team identity fragmentation

Team names arrived differently from each source ("Inter" vs "Internazionale", accented vs folded spellings), so the same club existed as several rows and its match history was split across them. Identity now resolves on a **canonical key** (Unicode NFKD normalisation plus accent folding) with an alias list. Arsenal, for one, went from a split record to a single row with 342 matches.

### Fixed: 11,609 duplicate match rows

`upsert_match` originally deduplicated on `ON CONFLICT(api_football_id)`. That column is nullable, and SQLite treats every `NULL` as distinct — so for CSV and football-data.org rows, which have no `api_football_id`, the conflict clause never fired and every re-fetch inserted a fresh duplicate. Running `load-history` repeatedly silently multiplied the database.

Identity is now `(league_id, season, home_team_id, away_team_id, date(date))`, enforced by a unique index, and 11,609 existing duplicates were collapsed. A stale `SCHEDULED` re-fetch can no longer un-finish a match whose result is already recorded, and the CSV loader's midnight timestamp no longer clobbers a real kickoff time.

> [!NOTE]
> If you ran `load-history` several times before August 2026, your accuracy history was computed over inflated data. The current baseline (HIGH 80.3%, MEDIUM 67.0%, Brier 0.2037) is the first measured on the deduplicated database.

### Fixed: `--seasons` anchoring in pre-season

`footy load-history --seasons 1` used to resolve "current season" from the calendar, so in August it asked for a season that had not been played and loaded an empty CSV. It now anchors to the **last season actually played** — European leagues step back before month 9, calendar leagues before March.

### Watch: `xg_quality` is blocked, not pending

This is the one gap worth understanding in full, because the previous guide described it incorrectly.

`footy fetch --stats` calls `fetch_and_store_match_stats`, which skips any match without an `api_football_id`. Meanwhile `footy fetch` **prefers football-data.org** for the seven leagues that have an FDO code — Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Liga Portugal, Eredivisie — to conserve API-Football quota. FDO responses don't carry an `api_football_id`, and neither do CSV loads.

The result: roughly 17,500 finished matches in those seven leagues are structurally unreachable by `--stats`. Only 44 rows in the whole database have `expected_goals`. The fetcher itself works correctly — verified at 10/10 fixtures for Saudi Pro League, populating xG, possession and passes.

Two ways out:

1. **Backfill the ids.** Run plain `footy fetch -l <league>` (no `--stats`) once per FDO league. `upsert_match` carries `api_football_id=COALESCE(matches.api_football_id, excluded.api_football_id)`, so an API-Football pass fills the missing ids in place without disturbing existing data. `--stats` can then reach those rows.
2. **Let it resolve forward.** Fixtures fetched from 2026/27 onward arrive with an `api_football_id` already attached, so new matches accumulate xG naturally.

Either path is bounded by the free tier — 100 requests/day, 10/minute. A full historical backfill is a multi-day project; the pragmatic choice is option 2 plus option 1 for whichever two or three leagues you bet most.

### Watch: Eliteserien history is shallow

Eliteserien's CSV source (`N0`) returns **300 Multiple Choices** for 2022–2025 and **404** for 2026, so `load-history` cannot fill it. It was backfilled via API-Football to three seasons (2022, 2023, 2024 — 726 matches, all with `api_football_id`).

2025 and 2026 are **blocked by the API-Football free tier**, which rejects those seasons with "Free plans do not have access." Worth knowing: on that rejection the client silently falls back to season 2024, so a backfill attempt reports success while actually re-writing data you already had. Verify with date ranges, not row counts:

```bash
sqlite3 footy.db "SELECT season, COUNT(*), MIN(date(date)), MAX(date(date)) \
  FROM matches WHERE league_id=(SELECT id FROM leagues WHERE slug='eliteserien') \
  GROUP BY season ORDER BY season"
```

Treat Eliteserien picks with more caution than the big-five leagues — three seasons is thin for H2H and venue factors.

### Watch: Saudi Pro League has one season

No CSV source exists, so it holds only the 306 matches fetched via API. Its form and H2H factors are correspondingly weak.

### Current database state

| Metric | Value |
|---|---|
| Matches | 21,935 (19,484 finished) |
| Teams | 325 |
| Leagues | 12 |
| `match_stats` rows | 35,822 |
| — with `expected_goals` | **44** |
| — with `possession` | 100 |
| Standings rows | 338 |
| Team-season stats | 86 |
| Settled predictions | 13,408 |
| Tests | 194 passing |

---

## 11. Season Readiness — Is the App Ready?

### Short Answer

**Yes — analytically ready for the 2026/27 season.** It can generate meaningful picks from day one.

### What "Ready" Means

✅ **The analysis engine is fully functional** — all 16 factors run, 15 market families modelled (~65 picks per fixture).

✅ **Historical data is loaded** — 3–9 seasons per league, 19,484 finished matches, on a deduplicated database.

✅ **Calibration is done** — probabilities are shrunk and adjusted. HIGH hits 80.3%, MEDIUM 67.0%, Brier 0.2037.

✅ **CLI workflow is complete** — `footy report` produces the weekly decision-support document in one command.

✅ **The `record` → `accuracy` → `calibrate` loop works** — the system can self-improve through the season.

✅ **Data integrity is sound** — team canonicalisation and fixture-identity deduplication are in place, so repeated `load-history` runs no longer inflate the database.

### Known caveats going in

⚠️ **`xg_quality` contributes nothing** until `api_football_id` is backfilled or new-season fixtures accumulate.

⚠️ **`league_position` and `opponent_strength` at ~65% coverage** — fetch standings regularly to improve them.

⚠️ **Eliteserien (3 seasons) and Saudi Pro League (1 season)** are materially thinner than the rest.

⚠️ **No bookmaker odds comparison** — you must still check SportyBet manually to confirm edge.

### What Will Change During the Season

| When | What happens |
|---|---|
| Week 1 (Aug) | First `footy fetch-upcoming` pulls 2026/27 fixtures |
| Weeks 1–3 | LOW/MEDIUM data quality — no season form yet |
| Weeks 4–6 | Data quality improves as form populates |
| Week 8+ | `xg_quality` begins contributing **for newly fetched matches only** |
| Monthly | `footy calibrate` updates weights from real results |
| End of season | `footy load-history --seasons 1` pulls the completed season's CSV |

### Pre-Season Checklist

- [ ] `footy load-history --seasons 1` run (anchors to the completed season automatically)
- [ ] `python migrations/run.py` run — schema current
- [ ] API keys valid, quota available (`footy status`)
- [ ] `.env` configured with `VALUE_MIN_ODDS=1.32`
- [ ] `footy calibrate` run on the updated data
- [ ] Database backed up (`cp footy.db footy.db.backup-$(date +%Y%m%d)`)
- [ ] Priority leagues chosen — quota is limited, so focus on 4–6
- [ ] Early-season caution understood (weeks 1–6: HIGH only, stricter odds floor)
- [ ] Optional: `footy fetch -l <league>` for your top 2–3 FDO leagues to start the `api_football_id` backfill

---

## 12. Where to Go From Here — Impact vs Effort Analysis

This section answers: what's missing, what to fix next, and which improvements give you the most return for the least work.

### What the App Is Now (August 2026)

**Analytically complete for systematic betting.** The 16-factor engine generates meaningful picks from day one. The workflow is stable — Thursday fetch, Friday research, Saturday slip, Monday record — and 194 tests confirm the core logic holds. You can build 50-leg parlays with confidence tiers that match their hit rates (HIGH 80.3%, MEDIUM 67.0%, Brier 0.2037).

**Data-integrity sound.** Team canonicalisation (325 clubs, no fragmentation), 11,609 duplicates removed, fixture-identity unique index, `--seasons` anchoring fixed. Repeated `load-history` runs no longer inflate the database.

**Calibration functional.** Probability shrinkage tuned (1X2 30%, Poisson λ 25%, Dixon-Coles correction), DNB formula fixed, WEH capped at 0.82. The system self-improves through `record` → `accuracy` → `calibrate`.

**Missing three things that matter:**

1. **Bookmaker odds** — can't measure true edge yet
2. **Derived standings** — 46 league-seasons at 0% instead of 100%, solvable at zero API cost
3. **xG activation** — blocked by missing `api_football_id` on 17,561 historical rows

Everything else on the "Phase E roadmap" is either dead code (events/predictions endpoints exist, never called), marginal gains (referee bias would help 6,692/19,484 matches — 34% — but only for the 55 refs with 30+ matches), or genuinely future work (injuries/lineups require live pre-match data the free tier may not provide).

---

### The Three Gaps That Matter — Ranked by Impact

#### 1. **Bookmaker Odds Integration** — Turns "Probably Good" Into "Provably Mispriced"

**What it is:** Fetch SportyBet's offered odds for upcoming fixtures and compare them against your model probabilities. Flag picks where `P(model)` significantly exceeds `P(implied by SportyBet odds)`.

**Why it matters:** The model currently says "this pick is 75% likely" — but if SportyBet has also priced it at 75%, there's no edge. True value detection requires knowing where SportyBet is wrong. This is the ONE thing that makes the difference between "statistically sound picks" and "picks with a measurable edge."

**What it buys you:**
- Confidence that you're not just picking probable outcomes, but **mispriced** ones
- Ability to rank picks by edge magnitude, not just probability
- Proof of whether the system is genuinely profitable before committing to it long-term

**Implementation cost:**
- **Data source:** SportyBet has no public odds API. Three paths:
  1. Manual: Open SportyBet, copy odds for your shortlist (10-15 minutes/week) — zero dev cost, works immediately
  2. Browser automation: Playwright script scrapes the fixture list (2-3 days dev, legal grey area, breaks when their HTML changes)
  3. Paid odds API: Rapid API "Odds API" or similar (~$30/month for 100 fixtures/week) — 1 day integration
- **Schema:** Add `bookmaker_odds` table with `(fixture_id, market, pick, offered_odds, fetched_at, source)`. Add `edge` column to `predictions` or compute on the fly.
- **CLI:** `footy report --show-edge` — filters to picks where `1/offered_odds < P(model) - threshold`.

**Recommendation:** Start with **manual** (zero cost, teaches you what edge looks like) for 4-6 weeks. If the model consistently finds 3-5% edge and you're committing to this, pay for the API. Skip the scraper — too brittle.

**Honest ceiling:** Even with edge detection, bookmaker margin (10-15%) and the law of large numbers mean consistent profitability requires either (a) very large bet volume with small edges compounding, or (b) selective high-edge weeks. This feature tells you which weeks are which.

---

#### 2. **Derived Standings** — 65% → 100% Coverage at Zero API Cost

**What it is:** Compute league tables from match results already in the database, rather than fetching them from API-Football.

**Why it matters:** `league_position` and `opponent_strength` currently sit at 64-67% coverage because standings only exist for the 12 league-seasons you manually fetched. The other 46 league-seasons have complete match results but no table. This weakens two factors on 35% of your fixtures.

**What it buys you:**
- `league_position` and `opponent_strength` at **100% coverage** for every completed league-season
- Eliminates one "data quality: MEDIUM" cause
- Zero API cost — all inputs already in the database

**Implementation cost:**
- **Schema:** Standings table already exists with the right columns (played, won, drawn, lost, GF, GA, GD, points, home/away splits).
- **Function:** `compute_standings(league_id, season)` — query all FINISHED matches, group by team, sum W/D/L and goals, rank by points then GD. ~80 lines.
- **CLI:** `footy derive-standings` — runs for all league-seasons missing standings rows. Idempotent.
- **Time:** 1 day dev + test.

**Caveat:** Derived tables lack the `form` string (last 5 results as "WWDLW") because API-Football computes that chronologically mid-season. You can derive it too by sorting matches by date — adds 20 lines but rarely changes a pick (form is also in `momentum` and `recent_form` factors).

**Recommendation:** Implement this **immediately**. Highest ROI fix in the codebase — one day of work removes a 35% data gap at zero runtime cost forever.

---

#### 3. **xG Activation** — 0% → ~10% Coverage, or Accept the Gap

**What it is:** Backfill `api_football_id` for historical matches so `footy fetch --stats` can reach them and populate `match_stats.expected_goals`.

**Why it matters:** `xg_quality` is one of 16 factors and currently contributes zero. xG corrects for finishing luck — a team that creates 2.1 xG but scores 0 is stronger than their result suggests. It matters most for predicting goals-based markets (O/U, BTTS, team O/U, WtN).

**What it buys you:**
- The `xg_quality` factor activates, improving calibration for goals-based markets
- More confident MEDIUM picks in O/U and team O/U
- Possibly tightens the remaining overconfidence in `team-over-under-1.5` and `over-under-2.5` at HIGH

**What it costs:**
- **Backfill scope (last 3 seasons, highest value):** 9,375 matches missing `api_football_id`
- **API cost to fetch ids:** ~29-58 calls (1-2 per league-season) — 1 day of quota
- **API cost to fetch xG:** 9,375 calls at 1/match = **94 days** at 100/day with rate limit (10/min) enforcement
- **Pragmatic alternative:** Let it resolve forward. Fixtures fetched from 2026/27 onward arrive with `api_football_id` attached, so xG accumulates naturally for new matches. By December you'll have 300-400 matches with xG. By May, 1,500+.

**Recommendation:** **Accept the gap for now.** 94 days of quota is prohibitive, and the gain is incremental — 15 of 16 factors already work, and xG's contribution to the aggregate score is weighted at 1% (see `FACTOR_WEIGHTS` in `config.py`). Track one thing through the season: does `footy accuracy -m over-under-2.5` improve as new-season matches with xG accumulate? If yes, xG is helping. If accuracy stays flat, the factor may be less impactful than expected.

**Alternative (for the impatient):** Backfill your **top 2-3 most-bet leagues** only. E.g., Premier League + La Liga + Bundesliga 2024-25 ≈ 1,066 matches ≈ 11 days of quota. Costs you two weeks but activates xG for the fixtures you care about most.

**Reality check:** `xg_quality` is weighted at **1.0%** of the aggregate score (see `FACTOR_WEIGHTS` in `config.py`). Even if xG doubled its contribution, the per-pick impact is ~0.5-1pp probability shift. For comparison, `recent_form` is 15.5%, `home_form` and `away_form` are 13% each. The 15 working factors already produce 80.3% HIGH accuracy. xG is a nice-to-have, not a prerequisite.

---

### Everything Else — Why It's Not on the List

| Feature | Why it's skipped or deferred |
|---|---|
| **Fixture events (minute-level goals/cards)** | Endpoint exists (`get_fixture_events`) but is **never called** — dead code. Would fix 1UP/2UP settlement ambiguity (26% of matches are unresolvable with HT data alone) and enable "team scores in final 15 min" features. Cost: 1 call/match (same as `--stats`). Deferrable because 1UP already measures 88.5% at HIGH with the approximation. |
| **Injury reports (`/injuries`)** | High impact when a key player is out, but requires **live pre-match data** the free tier may not provide. Cost: 1 call/fixture, pre-match. Manually checking team news (5 min/week) covers this until you're profitable enough to justify an always-on fetch. |
| **Confirmed lineups (`/fixtures/lineups`)** | Highest-impact Tier 2 feature — the actual starting XI changes everything. But it's **time-sensitive** (available 60 min pre-KO) and the free tier may block it. Manual check on SportyBet (formations are often shown) works until automation justifies itself. |
| **Referee bias modelling** | `matches.referee` is 34% populated (6,692/19,484), with 55 referees appearing 30+ times. Could model card frequency, penalty tendency. Marginal gain — only affects a minority of picks and requires per-ref calibration. Low priority until the three gaps above are closed. |
| **API-Football `/predictions` endpoint** | Exists, never called. Returns their own model's probabilities. Could be used as an **ensemble member** (blend your 16-factor output with theirs) or a baseline comparison. Cost: 1 call/fixture. Worth investigating once you have 6 months of your own accuracy data to compare against. |
| **Manager change detection** | New-manager bounce is real (+15-20% win rate uplift for first 5-10 matches), but requires tracking managerial appointments. No endpoint for this. Needs a manual list or external scrape. Low frequency (2-3 per league per season) makes manual flagging viable. |
| **Platt scaling (global sigmoid calibration)** | Fits a sigmoid over stated probabilities to tighten calibration gaps. Useful when your HIGH picks hit 75% but state 83%. Your current gaps (HIGH 80.3% stated ~82% avg) are already tight. Revisit after one full season if systematic over/underconfidence emerges. |

---

### Decision Framework: What to Build Next

Use this to decide:

#### **Priority 1 (Do This Week):**

✅ **Derive standings** — 1 day dev, zero cost, fixes 35% data gap permanently.

#### **Priority 2 (Do Before First Real Slip):**

✅ **Manual bookmaker odds comparison** — 10 minutes/week, teaches you what edge looks like. Build a simple spreadsheet: model probability, SportyBet odds, implied probability, edge. After 4-6 weeks you'll know whether the model finds consistent edge.

#### **Priority 3 (Do First Month):**

⚠️ **Calibrate after week 4** — run `footy calibrate` once you have 50+ new-season results. Early-season weights may drift from historical.

⚠️ **Track half-time market accuracy** — the six new H1/H2 markets are **untested in simulation**. `footy accuracy -m first-half-over-under` and `-m second-half-over-under` after 6 weeks tells you whether to scale them up or drop them.

#### **Priority 4 (Do If Profitable After 10 Weeks):**

🔄 **Paid odds API** — if manual edge-checking is consistently finding 3-5% edge and you're committing long-term, automate it. Rapid API "Odds API" ~$30/month.

🔄 **xG backfill for top 3 leagues** — if `over-under-2.5` and `team-over-under` accuracy is dragging and you have 11 spare days of quota, backfill Premier League + La Liga + Bundesliga 2024-25.

#### **Defer Indefinitely:**

❌ **Injury/lineup fetching** — manual check is faster until you're running this at scale.

❌ **Referee bias** — marginal, complex, low frequency.

❌ **Manager change tracking** — 2-3 events/season, manually flaggable.

---

### The Honest Roadmap — 6 Months

| When | What | Why |
|---|---|---|
| **Week 0 (now)** | Implement derived standings | 1 day, removes 35% gap, zero cost |
| **Week 1** | First real slip with manual odds check | Learn what edge looks like |
| **Weeks 2-6** | Record every result, track half-time markets | Build real-season calibration data |
| **Week 4** | `footy calibrate` | Tune weights to current-season patterns |
| **Week 8** | Evaluate: Are you finding consistent edge? | Decision point: commit or pivot |
| **Week 12** | If profitable: automate odds fetching | Otherwise: reassess model or approach |
| **Month 6** | xG backfill decision | By now, new-season xG coverage tells you if it matters |

---

### What "Success" Looks Like at Each Stage

**After 4 weeks:** You know whether the model finds **consistent 3-5% edge** over SportyBet's pricing on MEDIUM picks. If yes, the system works. If no, either (a) SportyBet is sharper than expected, or (b) the both-sides artifact is hiding something and you need to compare their offered odds against both sides of your probabilities.

**After 12 weeks:** Your rolling 4-week HIGH accuracy is **≥78%** and MEDIUM **≥65%**, matching or exceeding the simulation baseline. Calibration is stable. You have 300-500 new-season picks settled and `footy accuracy` confirms the tiers hold in practice.

**After 6 months:** You've tracked 20-25 weekends. If your slip accuracy (the picks YOU chose after research, not all model picks) consistently beats 80% at the legs you selected from HIGH/MEDIUM, and you're finding edge on 60%+ of those picks, the system is working as intended. The 50-leg jackpot is still rare (1 in 6,000-80,000 depending on per-leg accuracy), but your **expected value per slip is positive** and you're positioned to receive it when the week aligns.

**If it's not working:** The most likely cause is not the model — it's that SportyBet's pricing is tighter than the 10% margin assumption, or that the leagues you're betting have sharper lines than the big-five European leagues the model was tuned on. Check your edge distribution: if 90% of your picks show <2% edge, the market is efficient and large-accumulator strategies won't overcome the margin.

---

### Final Recommendation

**This week:** Derive standings (1 day). **This season:** Run it, track it, learn whether you find edge. The model is ready. The question is whether **your betting market is beatable with this approach**. Odds integration (manual at first) is how you find out.

---

## 13. How to Win — The Complete Strategy

Written for the specific goal: 48–50 leg accumulators at combined odds of 8,000–64,000+.

### Understanding the Endgame

The jackpot is not won by finding 50 "sure things." Sure things pay 1.05 — fifty of them produce 1.05^50 ≈ 11.5x, not 64,000x.

It is won by finding **50 highly probable picks at meaningful odds** and having a week where they all land:
- Average per-leg accuracy: **80–88%** (achievable)
- Average per-leg real odds: **1.22–1.28** (MEDIUM picks at 1.30–1.40 model odds)
- Combined: 1.25^50 ≈ **70,000**

At 85% per leg, P(all 50) ≈ 1 in 6,100. The model moves you from gut-feel picking (~70–75% per leg) to systematic picking (~80–85%). That shift alone makes the full jackpot **~100× more likely**.

### The Non-Negotiables

**1. Record every result, every week.** `footy record` on Monday, without exception. Without results the system cannot calibrate, cannot improve, and you cannot measure whether your process works. This is the single discipline separating a system that improves from one that stagnates.

**2. Use both workflows.** Workflow ii (systematic) is the minimum. Workflow i (manual odds comparison) is the maximum. Time-rich weeks: Workflow i finds better picks. Time-poor weeks: Workflow ii still gets you a slip.

**3. Apply the odds check.** Verify SportyBet offers ≥1.20 on every pick you include. This keeps combined odds in the 64,000 range instead of the 150 range. Ten picks at 1.05 instead of 1.25 costs you 1.05^10 vs 1.25^10 = 1.63 vs 9.31 — a 5.7× difference from those ten alone.

**4. Apply your knowledge at the margins.** Don't research all 50 — that's the model's job. Research the 5–10 where confidence is MEDIUM rather than HIGH, where there's a risk flag, or where you have recent context the model lacks.

**5. Avoid the overconfident markets at HIGH tier.** Not `win-either-half`, not `team-over-under-1.5`, not `over-under-2.5`. Use them at MEDIUM if they pass your odds check.

**6. Read tiers, not per-market rates.** A 50% next to a market name in any accuracy output is the both-sides artifact, not a broken model. Judge by tier hit rate and Brier score.

### Pick Composition for 64,000+ Combined Odds

For a 50-leg slip at ~1.25 average real odds:

| Picks | Market | Confidence | Real odds target | Why |
|---|---|---|---|---|
| 8–10 | `1x2` — pick "…1X2 - 1UP" | HIGH or MEDIUM | 1.20–1.35 | 85–87% hit rate, reliable, decent odds |
| 8–10 | `draw-no-bet` | MEDIUM | 1.30–1.60 | 76% at MEDIUM, better odds than DC |
| 6–8 | `double-chance` (12 or X2) | HIGH | 1.20–1.30 | 80% — short odds but solid |
| 6–8 | `over-under-1.5` | HIGH | 1.25–1.40 | 83%, reasonable |
| 5–7 | `1x2` outright | HIGH or MEDIUM | 1.40–2.00 | Fewer wins but big odds contribution |
| 5–7 | `team-over-under-2.5` (away under) | HIGH | 1.20–1.35 | 86% |
| 4–6 | `win-either-half` | **MEDIUM only** | 1.30–1.60 | 65% — include only if research confirms |
| 2–4 | `1x2` — pick "…1X2 - 2UP" | HIGH | 1.20–1.35 | Safeguard pick, very reliable |
| 3–5 | `second-half-over-under-0.5` | MEDIUM | 1.25–1.50 | SH has the highest goal frequency |
| 2–4 | `home-team-second-half-over-under-0.5` | MEDIUM | 1.35–1.80 | Home sides score more in H2 |
| 2–4 | `away-team-second-half-over-under-0.5` | MEDIUM | 1.40–1.90 | Away teams strong in H2 are a niche edge |
| 0–3 | `first-half-over-under-0.5` | MEDIUM | 1.30–1.65 | Sparingly until settled history confirms |

Total: 51–74 candidates. Adjust to the weekend's fixture list.

> [!TIP]
> **Half-time markets, first season:** include 2–4 per weekend at MEDIUM + ≥1.30 real odds. Record every result. After 6 weeks, check `footy accuracy -m second-half-over-under` and `-m first-half-over-under` to see whether they're calibrated at MEDIUM. Then scale up or down on evidence.

The higher-odds picks (1X2 outrights at 1.50–2.00) push combined odds above 10,000. The reliable ones (1UP, DC, Under 2.5) keep the win rate up. You need both.

### Managing the Odds Floor with Your Discretion

The floor (`-o 1.32` model ≈ 1.19 real) is a starting filter, not a rule:

```
Model confidence HIGH (≥80%)?     →  Check SportyBet odds
  SportyBet ≥1.20?                →  INCLUDE (even if model odds <1.32)
  SportyBet <1.20?                →  EXCLUDE unless slip is short on legs

Model confidence MEDIUM (60-80%)?
  Do you have confirming research? →  Check SportyBet odds
    SportyBet ≥1.25?              →  INCLUDE
    SportyBet 1.20-1.25?          →  Only if strongly confident
    SportyBet <1.20?              →  EXCLUDE
  No confirming research?          →  EXCLUDE

Model confidence LOW (<60%)?       →  EXCLUDE always
```

### The Honest Math

At 85% per leg across 50 legs: P(all 50) ≈ 1 in 6,100 · P(48/50) ≈ 1 in 260 · P(45/50) ≈ 1 in 30.

At 80% per leg: P(all 50) ≈ 1 in 59,000 · P(48/50) ≈ 1 in 1,900 · P(45/50) ≈ 1 in 150.

The model's job is keeping you at 80–85% consistently. Yours is applying judgment, selecting meaningful odds, and playing consistently. The jackpot arrives when both align in one week.

### Tracking Your Real-World Performance

```bash
# Every Monday
footy record
footy accuracy
footy accuracy -m 1x2

# Monthly
footy calibrate
```

If your rolling 4-week HIGH accuracy drops below 70%, common causes are: early season (normal for weeks 1–6), a volatile weekend (not the model's fault), a market-specific calibration issue (check `footy accuracy -m <name>`), or systematic overconfidence returning (run `footy calibrate`).

**Track your slip performance separately** from model accuracy. Model accuracy covers all picks it generates; your slip only includes what you chose — and should be higher, because you applied research on top.

### The Ceiling — Being Honest

| What | Realistic ceiling | What gets you there |
|---|---|---|
| Model HIGH accuracy (overall) | **83–85%** | Further calibration + xG activation |
| Model HIGH accuracy (best markets only) | **88–90%** | Full Phase D/E + one season of calibration |
| Your slip accuracy (with judgment) | **85–90%+** | Consistent use of both workflows |
| All 50 legs in one week | Very rare, but it happens | Every per-leg accuracy gain makes it ~10× likelier |

The goal is not to guarantee the jackpot. It is to be the player consistently operating at 85–88% per leg, so that when the exceptional week comes, you are positioned to receive it.

---

## 14. Troubleshooting

### `footy load-history` loaded far more than I asked for

Before August 2026 this was the duplication bug — `--seasons 1` would re-insert every match because the conflict clause never fired on nullable `api_football_id`. Fixed. If your database predates the fix, the counts were inflated; the fix collapsed 11,609 duplicates. Verify:

```bash
sqlite3 footy.db "SELECT league_id, season, home_team_id, away_team_id, date(date), COUNT(*) c \
  FROM matches GROUP BY 1,2,3,4,5 HAVING c > 1 LIMIT 5"
```

An empty result means no duplicates remain.

### `footy load-history --seasons 1` loaded nothing

In pre-season this used to request an unplayed season and get an empty CSV. Fixed by anchoring to the last season actually played. If a specific league still returns nothing, its CSV may genuinely be unavailable — Eliteserien returns 300/404 (see Section 10).

### `footy fetch --stats` says "All FINISHED matches already have stats"

The `match_stats` rows exist but without xG, because the CSV loader creates stat rows from the results it has. `--stats` targets matches with *no* stats row at all, so it considers them done. This is the same root issue as the xG gap — see Section 10.

### `footy fetch` returns "Free plans do not have access"

API-Football's free tier restricts certain seasons. The client falls back to season 2024 automatically — which means **the operation can report success while fetching a season you already had**. Always verify by date range, not row count.

### Migrations show as pending but the columns already exist

A partly-applied migration can leave the schema correct while the tracking row is missing. `python migrations/run.py` is idempotent and backfills tracking rows with `INSERT OR IGNORE`. Re-running is safe.

### Rate limit errors during `--stats`

API-Football allows 10 requests/minute. The client enforces this, so a large `--stats-limit` simply takes time (60 matches ≈ 6 minutes). Use a smaller limit and spread across days.

### The accuracy dashboard shows ~50% for every market

Expected. The engine stores both sides of every binary market, so per-market rates converge on 50%. Read the confidence-tier breakdown and Brier score instead — see [Section 2](#the-market-layer).

### `footy report` returns no picks

Usually the odds floor. Try `footy report -o 1.20` or `footy report -c MEDIUM -o 0`. If still empty, there may be no upcoming fixtures in the window — check with `footy status` and widen with `-d 14`.

---

## 15. Local-First Operating Model

### Why Local-First Makes Sense Now

Hosting costs money and adds operational complexity, and it is only justified once the application has proven profitable. You wouldn't pay for a factory before it produces anything.

Local-first means zero ongoing cost, full control over data and keys, no cloud downtime risk, easy pause and resume, and a database you own on your own machine.

### What You're Giving Up (And Why It Doesn't Matter Yet)

| Cloud feature | What you're missing | Does it matter now? |
|---|---|---|
| Scheduled cron fetches | Data not auto-updated — you fetch manually | No — fetching takes 30 seconds weekly |
| Web UI on your phone | No remote browser interface | Partly — `footy serve` covers your LAN |
| Always-on server | Analysis runs only when your machine is on | No — you need it Thursday–Saturday |
| Telegram push | No notifications unless the bot runs | No — you read the report once |

### When to Move to Hosting

Consider it when **all three** hold:

1. **Profitable** — you've tracked 10+ weekends and the model demonstrably improved your win rate versus your pre-system baseline.
2. **Reliable** — HIGH accuracy has held ≥75% across 8+ weeks of *real-season* data, not just historical simulation.
3. **Inconvenient locally** — the manual weekly routine is creating enough friction that you're missing fixtures.

`railway.toml` and `Procfile` are already in the repository:

```bash
npm install -g @railway/cli
railway login
railway up
```

### The Local Workflow Is the Production Workflow

The CLI is not a development tool — it is the final product. `footy report` is meant to be run weekly and read as a decision-support document. The local database accumulates a season of data, the calibration loop improves the model, and everything works offline, on your machine, at zero cost.

---

*Guide version: August 2026 · Footy Predict v1.0*
*194 tests passing · HIGH: 80.3% · MEDIUM: 67.0% · Brier: 0.2037*
*12 leagues · 15 market families · 16 factors · 21,935 matches (19,484 finished) · 325 teams*
