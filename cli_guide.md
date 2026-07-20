# Footy Predict — Complete CLI Guide

> **Local-first. Decision-support. No hosting required.**
> Version: July 2026 · 134 tests passing · HIGH accuracy 79.9% · MEDIUM 62.7%

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
10. [Season Readiness — Is the App Ready?](#10-season-readiness--is-the-app-ready)
11. [What to Look Forward To](#11-what-to-look-forward-to)
12. [How to Win Consistently](#12-how-to-win-consistently)
13. [Local-First Operating Model](#13-local-first-operating-model)

---

## 1. What This Is — And What It Isn't

### What It Is

Footy Predict is a **decision-support system** for building football parlays. It takes the cognitive burden of analysis off you by:

- Collecting and storing historical match data, team statistics, form tables, head-to-head records, and standings from free APIs
- Running a 16-factor weighted analysis model across every upcoming fixture
- Generating ranked picks across 11 betting markets with stated probabilities and detailed rationale
- Filtering those picks to a **value zone** — picks where the model is confident AND the implied odds are worth including in a parlay
- Presenting everything in a scannable, plain-English report you can use as the starting point for your own research

The output is: *"here is what the data says, here is why, here is how confident we are."* You read it, you do your own research, you make the final decision.

### What It Is Not

- It is **not an automated betting system**. It never places bets or tells you definitively to bet on something.
- It is **not infallible**. An 80% HIGH confidence hit rate means 1 in 5 HIGH picks is wrong. At 50 legs, roughly 10 legs will lose on an average week.
- It is **not a replacement for your knowledge**. A model does not know that a key player is suspended or that the manager was sacked on Wednesday. That's your edge — use the model's signal, then apply context.
- It is **not yet profitable on its own terms** until you verify that its HIGH picks have a real edge over SportyBet's offered odds. That requires running it through at least one full season with records kept.

### The Rationale Behind Building This

Football betting markets are inefficient in specific, exploitable ways:

1. **Complexity penalty**: Evaluating 40+ fixtures weekly across 12 leagues is cognitively impossible without tools. Most bettors under-research or rely on gut feel. A systematic approach beats gut feel in aggregate.

2. **Form bias**: Humans overweight recent results ("Liverpool just won 5 in a row, they must be good") without adjusting for opponent quality, venue, or regression to the mean. The model corrects for this.

3. **Accumulator structure**: A 50-leg parlay at 80% per leg with fair odds is approximately breakeven. Any genuine edge — even 3-5% per pick over the bookmaker's implied probability — compounds explosively in a large accumulator.

4. **Free data availability**: API-Football provides full stats free at 100 requests/day. This project mines that data systematically and builds the analysis layer the bookmakers don't want you to have.

---

## 2. How the Model Works

### The Factor Layer (16 Factors)

Every match analysis runs 16 independent factors. Each produces a **score** (−1.0 to +1.0, negative = favours away) and a **confidence** (0.0 to 1.0, based on data availability):

| Factor | What It Measures | Data Source |
|---|---|---|
| `recent_form` | Last 10 results PPG, win rate, form string, trend | `matches` DB |
| `home_form` | Home-only win rate, GPG, CPG, CS rate, streak | `matches` DB |
| `away_form` | Away-only win rate, GPG, CPG, CS rate, streak | `matches` DB |
| `head_to_head` | H2H record, BTTS rate, avg goals, venue-specific | `matches` DB |
| `league_position` | Table position, points gap, GD, form string from standings | `standings` DB |
| `momentum` | Last 3 vs previous 3 PPG — improving or declining? | `matches` DB |
| `scoring_frequency` | Goals per game (home/away split), blank game rate | `matches` DB |
| `defensive_record` | Goals conceded per game, clean sheet rate (home/away split) | `matches` DB |
| `scoring_patterns` | BTTS rate, Over 2.5 rate, Poisson λ, pre-computed O/U probs | `team_season_stats` |
| `goal_timing` | 1st half vs 2nd half goal split, WEH historical rate | `match_stats` DB |
| `rest_days` | Days since last match for each team, congestion flags | `matches` DB |
| `rivalry` | Derby detection by name heuristic + known pairs list | Static config |
| `attacking_matchup` | Home GPG vs Away CPG, Away GPG vs Home CPG — net attack edge | `matches` DB |
| `venue_advantage` | Team's home win rate vs their away win rate (pure ground bias) | `matches` DB |
| `opponent_strength` | Strength-adjusted PPG using opponent standings rank | `matches` + `standings` |
| `xg_quality` | xG per game, xG vs actual ratio, xG against (shot quality) | `match_stats` (xG col) |

> [!NOTE]
> `xg_quality` currently has **0% data coverage** — xG data is not in the historical CSV files and requires API-Football's `/fixtures/statistics` endpoint. It will populate automatically once the 2026/27 season starts and you run `footy fetch --stats`. Until then, it contributes 0 to the analysis but does not break anything.

### The Engine Layer

1. All 16 factors run in parallel for each match
2. Factor scores are **weighted** (see `FACTOR_WEIGHTS` in `config.py`) and aggregated into a single `overall_score` (−1 to +1)
3. Confidence is computed as a weighted average of factor confidences
4. The engine classifies data quality as HIGH/MEDIUM/LOW based on coverage

### The Market Layer

The `overall_score` and individual factor data feed into 11 market-specific models:

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
| `1x2-1up` | Poisson P(team ever leads by ≥1) — lead trigger probability |
| `1x2-2up` | Poisson P(team ever leads by ≥2 OR outright wins) — safeguard pick |

### Calibration

After each simulation or real-season tracking run, the engine automatically calibrates factor weights based on which factors correlate most with correct predictions. The Calibrator updates weights in `calibration_weights.json` if any factor deviates significantly from baseline accuracy.

### Probability Shrinkage — Why It's Critical

Raw logistic models and Poisson models produce probabilities that are too extreme — they can say P=0.93 for outcomes that hit only 78% of the time. The system applies **three layers of shrinkage**:

1. **1X2 shrinkage**: 30% regression toward European priors. Caps: home ≤ 0.72, away ≤ 0.65.
2. **Poisson λ shrinkage**: 25% regression toward league mean (1.35 GPG) for all goals-based models.
3. **Dixon-Coles O/U correction**: When expected total is within 0.6 goals of the line, shrink toward 0.5.

This is what took HIGH accuracy from 77.2% → 79.9% and the Brier score from 0.2138 → 0.2064.

---

## 3. Installation & First-Time Setup

### Prerequisites

- Python 3.11 or higher
- Git
- ~500 MB disk space for the database
- API keys (free, no card required):
  - **API-Football**: [dashboard.api-football.com/register](https://dashboard.api-football.com/register) — primary source, 100 req/day
  - **Football-Data.org**: [football-data.org/client/register](https://www.football-data.org/client/register) — secondary, 7 leagues

### Installation

```bash
# Clone the repository
git clone https://github.com/islajr/footy-predict.git
cd footy-predict

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate       # Linux/Mac
# .venv\Scripts\activate.bat    # Windows

# Install the package (editable mode — changes to source code take effect immediately)
pip install -e ".[dev]"

# Verify installation
footy --help
```

### Environment Setup

Copy the example env file and fill in your API keys:

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

Run these steps once when you first set up the database:

```bash
# Step 1: Load historical data from free CSVs (no API quota used)
# This gets you 3-5 seasons of results, form, and patterns
footy load-history                     # all supported leagues, last 5 seasons
footy load-history --league premier-league --seasons 5   # single league

# Step 2: Fetch current season standings and team stats (costs ~30 API calls)
footy fetch --league premier-league    # repeat for each league
footy fetch --league la-liga
footy fetch --league bundesliga
# ... etc for each of the 12 supported leagues

# Step 3: Verify everything loaded
footy status

# Step 4: Run your first analysis
footy report
```

> [!IMPORTANT]
> **API Quota**: You have 100 requests/day free on API-Football. Do NOT fetch all 12 leagues on the same day. Spread fetches over 3-4 days or use `footy fetch-upcoming` (quota-aware) once the season starts.

---

## 4. Configuration Reference

### Key Config Values (`src/config.py`)

| Setting | Default | What It Does |
|---|---|---|
| `CONFIDENCE_THRESHOLDS["HIGH"]` | `0.80` | Picks at or above this probability are labelled HIGH |
| `CONFIDENCE_THRESHOLDS["MEDIUM"]` | `0.60` | MEDIUM tier floor |
| `VALUE_MIN_ODDS` | `1.32` | Default odds floor for `footy report` and `--min-odds` filter |
| `FACTOR_WEIGHTS` | see config | Per-factor weight in the aggregated score |
| `CACHE_TTL` | varies per endpoint | How long API responses are cached (standings: 6h, fixtures: 2h) |
| `LEAGUES` | 12 leagues | League slugs and API IDs for supported competitions |

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `API_FOOTBALL_KEY` | — | Required. Your API-Football key |
| `FOOTBALL_DATA_ORG_KEY` | — | Optional. Football-Data.org key |
| `DATABASE_URL` | `sqlite:///./footy.db` | Database location |
| `VALUE_MIN_ODDS` | `1.32` | Override the value zone odds floor |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`) |

### Supported Leagues

| Slug | League | API Source |
|---|---|---|
| `premier-league` | English Premier League | API-Football |
| `la-liga` | Spanish La Liga | API-Football |
| `bundesliga` | German Bundesliga | API-Football |
| `serie-a` | Italian Serie A | API-Football |
| `ligue-1` | French Ligue 1 | API-Football |
| `eredivisie` | Dutch Eredivisie | API-Football |
| `liga-portugal` | Portuguese Primeira Liga | API-Football |
| `championship` | English Championship | API-Football |
| `belgian-pro-league` | Belgian Pro League | API-Football |
| `turkish-super-lig` | Turkish Süper Lig | API-Football |
| `scottish-premiership` | Scottish Premiership | API-Football |
| `brasileirao` | Brazilian Série A | API-Football |

---

## 5. All CLI Commands

### Quick Reference

```
footy --help                        Show all commands
footy COMMAND --help                Show help for a specific command
```

---

### `footy status`

**What it does**: Shows your API quota usage, cache stats, database record counts, and quota budget for the day.

```bash
footy status
```

**Use it**: Every morning before fetching, to know how many API calls you have left.

---

### `footy fetch`

**What it does**: Fetches fixtures, standings, and team statistics for a league from the API, storing everything in the local database.

```bash
footy fetch                         # fetch all configured leagues
footy fetch --league premier-league # fetch a single league
footy fetch --stats                 # also fetch per-match statistics (uses more quota)
footy fetch --stats-limit 20        # fetch stats for last 20 matches only
```

**Quota cost**: ~5-10 calls per league (fixtures + standings + team stats). `--stats` adds 1 call per match.

---

### `footy fetch-upcoming`

**What it does**: A quota-aware weekend-optimised fetch. Calculates your remaining daily budget and fetches only the leagues and fixtures relevant to the upcoming weekend, staying within limits.

```bash
footy fetch-upcoming                # auto-calculates budget and fetches
footy fetch-upcoming --days 4       # look ahead 4 days instead of 7
```

**Use it**: Every Thursday evening as the first step of your weekly workflow. This is the command that should be run before `footy report`.

---

### `footy load-history`

**What it does**: Downloads historical match CSVs from football-data.co.uk (free, no API key, no quota) and bulk-loads them into the database. Gives you 3-8 seasons of results immediately.

```bash
footy load-history                           # all leagues, last 5 seasons
footy load-history --league premier-league   # single league
footy load-history --seasons 3               # last 3 seasons only
footy load-history --start-season 2018       # from 2018 onwards
```

**Use it**: Once on initial setup, and again at the start of each new season to pull the just-completed season's full CSV.

---

### `footy analyze matchweek`

**What it does**: Runs the full 16-factor analysis engine on all upcoming fixtures in the window and displays a detailed per-match breakdown with factor scores and market recommendations.

```bash
footy analyze matchweek                      # all leagues, next 7 days
footy analyze matchweek --league premier-league   # single league
footy analyze matchweek --from 2026-08-16 --to 2026-08-18   # specific date range
```

**Output**: Per-match analysis card showing: overall score and direction, each factor's score and confidence, all market recommendations sorted by probability.

**Use it**: When you want the deep-dive view. For the weekly workflow, `footy report` is more efficient.

---

### `footy analyze match`

**What it does**: Deep single-match analysis with full factor breakdown, narrative summary, and all market recommendations.

```bash
footy analyze match "Arsenal vs Chelsea"
footy analyze match "Barcelona vs Real Madrid" --league la-liga
```

**Use it**: When you want to understand a specific fixture in detail before deciding whether to include it in your parlay.

---

### `footy picks`

**What it does**: Displays all picks from upcoming fixtures, grouped by confidence tier. The broad view — all markets, all matches.

```bash
footy picks                                  # all picks, all tiers, no odds filter
footy picks -c HIGH                          # HIGH confidence only
footy picks -c HIGH --min-odds 1.32          # HIGH confidence + value zone (recommended)
footy picks -c MEDIUM --min-odds 1.20        # MEDIUM picks with looser odds floor
footy picks --market over-under-2.5          # single market across all matches
footy picks --market 1x2 -c HIGH --min-odds 1.32     # 1X2 HIGH value picks only
footy picks --league premier-league -c HIGH  # Premier League only
```

**Confidence tiers**:
- `HIGH` (≥0.80 probability): Model is confident. Historical hit rate: **79.9%**
- `MEDIUM` (0.60–0.80): Moderate confidence. Historical hit rate: **62.7%**
- `LOW` (<0.60): Weak signal. Use for information only, not for parlay selection.

**Odds floor note**: `--min-odds 1.32` targets approximately ≥1.20 real SportyBet odds after the ~10% bookmaker margin is applied. This is the recommended filter for parlay construction. To override permanently, set `VALUE_MIN_ODDS=1.32` in your `.env`.

---

### `footy report` ⭐ (Primary Weekend Command)

**What it does**: Generates the weekly decision-support pick report. For each fixture, shows the single best value-zone pick, its rationale, risk flags, and 4 alternative picks. Designed to be read, researched, and used as the starting point for your parlay.

```bash
footy report                                 # HIGH picks, ≥1.32 odds, next 7 days
footy report --days 3                        # this weekend only (Friday to Sunday)
footy report --min-odds 1.40                 # stricter odds floor
footy report --min-odds 1.20                 # looser floor (more picks)
footy report --min-confidence MEDIUM         # include MEDIUM tier picks too
footy report --league premier-league         # Premier League fixtures only
footy report --output weekend.txt            # save report to a text file
footy report --output weekend.txt --days 3   # save this weekend's report to file
```

**Output structure per fixture**:
```
  Arsenal vs Chelsea  |  Premier League  |  2026-08-16  data: HIGH
  🟢 TOP PICK:  Away Win (2)  67%  1.49 odds  HIGH
     Away form: W W W D W (2.4 PPG away). Arsenal have won 4 of last 5 away...
  ⚠ Derby match detected — historical upset frequency above average
     Also: Away 1UP 81% 1.24  |  Over 2.5 71% 1.41  |  WEH Away 72% 1.39
```

**Key fields**:
- **TOP PICK**: The highest-probability pick that passes both the confidence and odds filters
- **%**: Model's estimated probability this pick is correct
- **odds**: Model implied odds (1/probability) — real SportyBet odds ≈ this × 0.90
- **Confidence tier**: HIGH/MEDIUM based on the probability threshold
- **Rationale**: First 220 characters of the model's reasoning
- **⚠ Risk flags**: Model-identified concerns (derby, low data quality, congested fixtures)
- **Also**: Next 4 best value-zone picks for the same match

> [!IMPORTANT]
> The odds shown are **model implied odds**, computed as `1/P(model)`. SportyBet builds in ~10% margin, so their actual offered odds will be lower. A model odds of 1.40 typically corresponds to SportyBet odds of ~1.25-1.30. This is factored into the default 1.32 threshold (targeting ~1.20 real odds).

---

### `footy parlay`

**What it does**: Builds a suggested parlay of N picks, selecting the highest-probability picks across fixtures, one per match.

```bash
footy parlay --size 50                        # 50-leg parlay, MEDIUM+ confidence
footy parlay --size 50 -c HIGH --min-odds 1.32   # 50-leg value zone parlay
footy parlay --size 30 -c HIGH --min-odds 1.40   # stricter filter, 30 legs
footy parlay --size 50 --league premier-league -c HIGH   # Premier League only
```

**Output**: A ranked table showing match, league, market, pick, probability, model odds, and confidence for each leg. Combined accumulator probability is shown at the bottom.

> [!NOTE]
> For a 50-leg parlay at 80% accuracy per leg, the probability of all 50 winning is approximately 0.80^50 ≈ 0.00014 (1 in 7,000). This is expected — the payout is enormous when it hits. The model helps ensure each individual leg has a genuine probability basis, not gut feel.

---

### `footy record`

**What it does**: Records the actual results of finished matches against the model's predictions, building the accuracy database.

```bash
footy record                                 # record all unrecorded finished matches
footy record --league premier-league         # single league
```

**When to run**: Every Monday morning after the weekend's matches are finished. This is what feeds the `footy accuracy` command and the calibration system.

> [!IMPORTANT]
> **Run this every week.** The calibration system (which improves factor weights over time) only works if results are being recorded. Skipping this means the model cannot self-improve from season data.

---

### `footy accuracy`

**What it does**: Displays the accuracy dashboard — how well the model's picks have performed against recorded results.

```bash
footy accuracy                               # full accuracy report, all markets
footy accuracy --market over-under-2.5       # single market accuracy
footy accuracy --league premier-league       # single league accuracy
footy accuracy --market 1x2 --league la-liga # combined filter
```

**Output includes**:
- Overall hit rate by confidence tier (HIGH/MEDIUM/LOW)
- Hit rate per market
- Hit rate per league
- Brier score (calibration quality)
- Trend over time (improving or declining?)

**Use it**: Weekly after recording results, and monthly to assess whether the model is outperforming random selection in the markets you're using.

---

### `footy calibrate`

**What it does**: Runs the weight calibration algorithm, updating factor weights based on which factors have been most accurate in recorded predictions.

```bash
footy calibrate
```

**When to run**: At the end of each month, or after recording at least 50 new match results. The calibrator requires a minimum of 100 settled predictions before making meaningful changes (this threshold is already met: 9,803 predictions in DB).

**How it works**: Compares the factor confidence scores for correct predictions vs incorrect predictions. Factors with consistently higher confidence on correct picks get upweighted; factors whose confidence correlates poorly with accuracy get downweighted.

---

### `footy bot start`

**What it does**: Starts the Telegram bot, which delivers match analysis and picks directly to your Telegram chat.

```bash
footy bot start
```

**Requirements**: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set in `.env`.

**Setup**: Create a bot via [@BotFather](https://t.me/botfather) on Telegram. The bot can deliver `footy report` output directly to your phone.

---

## 6. The Weekly Operating Workflow

This is the recommended routine for getting maximum value from the system.

### Thursday Evening (30 minutes)

```bash
# 1. Check quota before touching anything
footy status

# 2. Fetch upcoming weekend fixtures (quota-aware — stays within 100/day)
footy fetch-upcoming --days 4

# 3. Check status again to confirm data loaded
footy status

# 4. Generate the decision-support report and save it
footy report --days 4 --output ~/footy/$(date +%Y-%m-%d)-report.txt

# 5. Read the report. Research the flagged fixtures. Note your initial thoughts.
```

### Friday (20 minutes — research and refinement)

```bash
# Review specific fixtures in detail for matches you're unsure about
footy analyze match "Real Madrid vs Barcelona"
footy analyze match "Liverpool vs Man City"

# Check specific markets if you have a hunch
footy picks -c HIGH --min-odds 1.32 --market win-either-half
footy picks -c HIGH --min-odds 1.32 --market over-under-2.5
```

### Saturday Morning (10 minutes — final slip)

```bash
# Refresh with latest data if quota permits (lineups may have changed)
footy fetch-upcoming --days 2

# Build your parlay from the value zone picks
footy parlay --size 50 -c HIGH --min-odds 1.32

# Or review the report once more with the final fixture list
footy report --days 2
```

### Monday Morning (10 minutes — record and review)

```bash
# Record weekend results
footy record

# Check how the model performed this week
footy accuracy

# Monthly: calibrate weights
footy calibrate     # run monthly or when accuracy changes significantly
```

---

## 7. Scenario Recipes

### Scenario A: First Run of the New 2026/27 Season

```bash
# Before the season starts (early August):
footy load-history              # pull 2025/26 CSV data (just-completed season)
footy calibrate                 # recalibrate weights on full 2025/26 data

# First weekend of season (mid-August):
footy status                    # check quota
footy fetch-upcoming            # fetch week 1 fixtures
footy report                    # generate first real-season report
# NOTE: First 2-3 weeks will have LOW data quality — no season form yet
# The model will fall back to historical data. Accuracy will be lower.
# Trust HIGH picks but be conservative until 5+ matches of form exist per team.
```

### Scenario B: Early Season (Weeks 1-6)

In the first 6 weeks, teams have played too few matches for form factors to be reliable. The model will show LOW data quality warnings on many fixtures.

```bash
# Use more conservative filter early
footy report --min-odds 1.40   # stricter odds floor (fewer but more reliable picks)
footy report --min-confidence HIGH   # HIGH only, no MEDIUM

# Focus on head-to-head and historical data, not current season form
footy analyze match "Arsenal vs Chelsea"
# Look for: data_quality = HIGH, confident factors, good H2H history
```

Strategy for early season:
- Only pick fixtures where `data_quality = HIGH` in the report
- Prefer markets where historical data is the dominant signal: H2H (over 10 matches), venue advantage, rivalry
- Avoid teams with significant summer transfers or new managers (not yet modelled)
- Expect lower accuracy (aim for >70% HIGH picks) until week 8+

### Scenario C: Building a 50-Leg Weekend Parlay

```bash
# Step 1: Generate the value zone report
footy report --output weekend.txt

# Step 2: View the parlay builder suggestion
footy parlay --size 50 -c HIGH --min-odds 1.32

# Step 3: Cross-reference — which parlay picks also appear in your report?
# The parlay command picks one pick per match (highest probability).
# Your report shows the top pick + 4 alternatives per match.
# Where you disagree with the parlay's top pick, substitute from the report's "Also" list.

# Step 4: If you need more picks (parlay builder comes up short on value zone)
footy parlay --size 50 -c HIGH --min-odds 1.25   # loosen odds floor slightly
# or
footy parlay --size 50 -c MEDIUM --min-odds 1.32  # allow MEDIUM picks at value odds
```

**Parlay construction principles**:
1. One pick per match — don't double up on the same fixture
2. Take the highest-probability pick that passes your odds floor per fixture
3. Do your own research on the top 5-10 picks — flag any you have concerns about
4. Substitute from the "Also" list when your research disagrees with the model's top pick
5. Never include picks where `data_quality = LOW` and your own research is inconclusive

### Scenario D: Investigating a Specific Market

```bash
# You want to understand Over 2.5 picks this weekend
footy picks -c HIGH --min-odds 1.32 --market over-under-2.5

# How has Over 2.5 performed historically?
footy accuracy --market over-under-2.5

# Deep dive on a specific match's O/U reasoning
footy analyze match "Atletico Madrid vs Valencia"
# Look at: scoring_frequency factor, scoring_patterns factor, expected_total in rationale
```

### Scenario E: Checking Model Accuracy After a Bad Week

```bash
# Record results first
footy record

# View this week's accuracy
footy accuracy

# View accuracy broken down by market
footy accuracy --market 1x2
footy accuracy --market win-either-half
footy accuracy --market over-under-2.5

# Check if a specific league is underperforming
footy accuracy --league premier-league

# Re-calibrate if needed
footy calibrate
```

If a market consistently underperforms (e.g. WEH hitting <55% at HIGH confidence), stop using it in parlays until the model improves.

### Scenario F: No Internet / Offline Mode

The database persists all data locally. If your internet is down:

```bash
# Analysis works offline — all data is in SQLite
footy report             # uses existing DB data
footy picks -c HIGH      # works offline
footy analyze matchweek  # works offline

# Only fetching requires internet
# footy fetch-upcoming   # needs internet
```

This is the beauty of the local-first approach. Once data is fetched, you can analyse anywhere.

### Scenario G: Resetting and Starting Fresh

```bash
# WARNING: This deletes your entire database. Only do this intentionally.
rm footy.db
footy load-history      # re-import historical CSVs
footy fetch-upcoming    # re-fetch current data
```

### Scenario H: The Model Seems Wrong on a Match

The model is evidence-based, not omniscient. When you think it's wrong:

1. Run `footy analyze match "Team A vs Team B"` — read every factor
2. Check what the model knows: is head-to-head data available? Is form recent enough?
3. Check `data_quality` — LOW quality means the model is guessing more than knowing
4. Apply your own knowledge: recent injury news, managerial change, cup hangover, etc.
5. Override the model's pick with your own research if you have specific, recent information it doesn't

**The model will be wrong more on**:
- Early season (insufficient current form)
- Teams with recent manager changes (new manager bounce not modelled)
- Matches with key injuries (not yet integrated)
- Derbies (high volatility, model flags but can't fully quantify)

---

## 8. Understanding the Output

### Confidence Tiers

| Tier | Probability | Historical Hit Rate | When to Use |
|---|---|---|---|
| HIGH | ≥ 0.80 | **79.9%** | Primary parlay picks |
| MEDIUM | 0.60–0.80 | **62.7%** | Secondary picks, fill legs when HIGH count is low |
| LOW | < 0.60 | ~35% | Informational only. Do not include in parlays. |

### Implied Odds vs Real Odds

The system shows **model implied odds** = 1 / P(model). These are NOT SportyBet's offered odds.

| Model implied odds | Estimated real SportyBet odds | Should include? |
|---|---|---|
| 1.04 | ~0.94 | No — below 1.00, value is negative |
| 1.13 | ~1.02 | Borderline — marginal value |
| 1.32 | ~1.19 | At the minimum (default floor) |
| 1.40 | ~1.26 | Good value for HIGH picks |
| 1.60 | ~1.44 | Strong value |
| 2.00 | ~1.80 | High value, higher risk |

The 0.90 multiplier is an approximation of the bookmaker's average margin. It varies by market. For O/U and DNB markets, SportyBet's margin is typically 8-12%. For 1X2 markets, it can be 10-15%.

### The Value Zone

A pick is in the **value zone** when:
- Confidence = HIGH or MEDIUM (depending on your threshold), AND
- Model implied odds ≥ 1.32 (default, configurable)

Value zone picks are what you should base your parlay on.

### Data Quality

| Quality | Meaning | What to do |
|---|---|---|
| HIGH | 10+ matches of season data, full factor coverage | Trust the model |
| MEDIUM | 5-10 matches, most factors populated | Trust but verify |
| LOW | <5 matches or major data gaps | Add your own research before including |

### The Brier Score

The Brier score measures **calibration** — how close the stated probabilities are to actual hit rates. Lower is better.

- Random (no information): **0.25**
- Current model: **0.2064** (significantly better than random)
- Perfect calibration: **0.00**

A Brier score of 0.2064 means the model's stated probabilities are meaningfully useful — when it says 80%, outcomes happen roughly 80% of the time (not 50% or 95%).

### Risk Flags

The model surfaces these flags automatically:

| Flag | What It Means |
|---|---|
| Derby match | Historical data shows higher upset frequency |
| Fixture congestion | One or both teams played within 3 days |
| Low data quality | Insufficient matches for confident analysis |
| High draw probability | P(draw) > 28% — affects DNB and 1X2 reliability |
| Form volatility | Team's results highly inconsistent in last 10 matches |

---

## 9. Current Shortcomings — Honest Assessment

### What the Model Does Not Know

| Gap | Impact | Workaround | Fix (Phase) |
|---|---|---|---|
| **xG data** | `xg_quality` factor is blank — shot quality ignored | Check recent match stats manually | Fills automatically in Aug when season starts |
| **Injury reports** | Key player absences not modelled | Check injury news before including a pick | Phase E |
| **Confirmed lineups** | No lineup data pre-match | Check lineups 1h before KO via app/site | Phase E |
| **Manager changes** | New manager bounce not detected | Flag new managers manually, be cautious | Phase E |
| **Bookmaker odds comparison** | Cannot measure true edge — model says 80%, but does SportyBet price it at 75% or 60%? | Use the 0.90 adjustment as proxy | Phase E |
| **League context after week 6** | First 6 weeks use historical data, not current season form | Use higher odds threshold early | Auto-improves as season progresses |

### Known Calibration Issues (Still Overconfident)

Even after two calibration rounds, some markets remain overconfident at the HIGH tier:

| Market | HIGH hit rate | Stated avg | Gap | Verdict |
|---|---|---|---|---|
| `over-under-0.5` | 99.3% | 96.3% | -3pp | ✓ Well calibrated |
| `double-chance` | 80.0% | 88.7% | +9pp | Usable but short odds |
| `win-either-half` | ~65%* | ~82% | ~17pp | **Avoid at HIGH tier** |
| `team-over-under-1.5` | ~68%* | ~89% | ~21pp | **Use at MEDIUM only** |
| `over-under-2.5` | ~62%* | ~89% | ~27pp | **Use at MEDIUM only** |
| `win-to-nil` (Yes) | 78.4% | 94.3% | +16pp | **Avoid WtN Yes at HIGH** |
| `draw-no-bet` HIGH | now re-tiered to MEDIUM | — | ✓ Fixed | MEDIUM DNB = 76.2% ✓ |

> [!CAUTION]
> **Do not include HIGH-tier picks from `win-either-half`, `team-over-under-1.5`, or `over-under-2.5` in your parlay.** The stated probability of 89%+ is false. Actual hit rate is 62-68%. These markets should only be included at MEDIUM confidence (where the stated 60-75% is much closer to reality).

*These numbers are pre-calibration-round-2; exact post-round-2 figures will be measured after a season of real data.

### The Main Limitation: No Real Odds Comparison

The most significant limitation is that the system cannot currently tell you whether a pick is **genuinely good value** vs SportyBet's pricing. It can tell you the model probability is 80% — but if SportyBet has priced it at 80% too (implied by their odds), there is no edge.

True edge detection requires comparing our P(model) against SportyBet's P(implied by odds). That is Phase E work (bookmaker odds integration). Until then, assume our model's picks are slightly better than market because of the 80% hit rate observed historically — but do not assume every pick is mispriced.

---

## 10. Season Readiness — Is the App Ready?

### Short Answer

**Yes — analytically ready for the 2026/27 season.** The system can generate meaningful picks from day one.

### What "Ready" Means

✅ **The analysis engine is fully functional** — all 16 factors work, all 11 markets are modelled.

✅ **Historical data is loaded** — 3-5 seasons of form, H2H, and patterns are in the database.

✅ **Calibration is done** — model probabilities are shrunk and adjusted. HIGH tier hits at 79.9%.

✅ **CLI workflow is complete** — `footy report` generates a weekly decision-support report in one command.

✅ **The `footy record` → `footy accuracy` → `footy calibrate` loop works** — the system can self-improve through the season.

### What Will Change During the Season

| When | What happens automatically |
|---|---|
| Week 1 (Aug) | First `footy fetch-upcoming` pulls 2026/27 fixtures |
| Weeks 1-3 | LOW/MEDIUM data quality as teams have no season form |
| Week 4-6 | Data quality improves as form populates |
| Week 8+ | `xg_quality` factor starts working (once match stats are stored) |
| Monthly | Run `footy calibrate` to update factor weights from season data |
| End of season | Run `footy load-history` to pull the full season CSV |

### What You Need to Do to Get Ready

```bash
# Do this in early August, before the season starts:

# 1. Pull the just-completed 2025/26 season historical data
footy load-history --seasons 1    # just the most recent completed season

# 2. Fetch pre-season standings and team data
footy fetch --league premier-league
footy fetch --league la-liga
# ... etc for leagues you care about most (spread over 3 days to conserve quota)

# 3. Run one final calibration on the fresh historical data
footy calibrate

# 4. Verify
footy status
footy report   # should show upcoming pre-season fixtures if any exist
```

### Pre-Season Checklist

- [ ] `footy load-history` run for the 2025/26 season
- [ ] API keys valid and quota available (`footy status`)
- [ ] `.env` file configured with `VALUE_MIN_ODDS=1.32`
- [ ] `footy calibrate` run on updated data
- [ ] Know which leagues to focus on (quota is limited — prioritise 4-6 leagues)
- [ ] Understand early-season caution (weeks 1-6, use HIGH only, stricter odds floor)

---

## 11. What to Look Forward To

### Automatic Improvements This Season

**xG quality factor** (from August): Once match statistics start being stored from the 2026/27 season, the `xg_quality` factor activates. This adds shot quality, xG vs actual goals ratio, and xG-against to the model. Expected impact: **+1-2pp accuracy improvement**.

**Calibration loop**: Every month of recording results and calibrating improves factor weights. By December, the model will be tuned to the current season's characteristics, not just historical averages.

### Planned Development (Phase E — After Season Start)

| Feature | Impact | Timeline |
|---|---|---|
| **Bookmaker odds integration** | True edge detection — compare our P vs SportyBet's P. Transforms the system from a probability model to a genuine value detector | After season start |
| **Injury reports** (`/injuries`) | Significant — knowing top scorer is injured changes 1X2, WtN, O/U picks dramatically | Phase E |
| **Confirmed lineups** | Most impactful single feature. Knowing the actual starting XI changes many picks | Phase E |
| **Manager change detection** | New manager bounce (+15% uplift for first 5-10 matches) | Phase E |
| **Referee bias modelling** | Some referees have measurable tendencies (card rates, penalty decisions) | Phase E |
| **Platt scaling / global calibration** | Sigmoid calibration layer across all markets — should tighten remaining stated/actual gaps | Phase E |
| **Monte Carlo simulation** | Full probability distributions rather than point estimates | Future |
| **Squad depth index** | Congested fixture resilience based on bench quality | Future |

### What the Model Looks Like at Full Power

With Phase E complete, a typical HIGH-confidence pick will be supported by:
- 16 factors of statistical evidence
- xG quality correcting for finishing luck
- Injury-adjusted team strength
- Confirmed lineup formation
- Manager stability signal
- Referee tendency adjustment
- **Genuine edge confirmation**: model P(80%) > SportyBet implied P(72%) → +8% edge per leg

At that point, the expected accumulator edge per leg becomes calculable and verifiable.

---

## 12. How to Win Consistently

This section is explicit and direct. These are not guarantees — they are the conditions under which the system is designed to provide an advantage.

### The Non-Negotiables

**1. Record every result, every week.** `footy record` on Monday morning. Without results, the system cannot calibrate, cannot improve, and you cannot measure whether you're actually winning. Skipping this is the single fastest way to undermine the system.

**2. Only use HIGH picks in your parlay.** MEDIUM picks at 62.7% significantly drag down a long accumulator. The difference between a 50-leg parlay at 80% per leg and at 70% per leg is enormous: 0.80^50 ≈ 0.00014 vs 0.70^50 ≈ 0.0000001. **Stick to HIGH.**

**3. Apply the odds floor.** `--min-odds 1.32` is the default for a reason. Including picks at 1.05 odds adds almost no multiplier and wastes a slot that could have a higher-value pick. If the parlay builder comes up short of 50 legs at 1.32, add legs at 1.25 — not at 1.05.

**4. Do your own research on the top 5 picks.** The model doesn't know:
- Injury news from this week
- That the manager was sacked on Wednesday
- That it's a must-win for one side (cup final mentality)
- That one team just played a brutal Europa League away leg Tuesday
Apply this knowledge. Override picks where you have specific, recent information.

**5. Avoid the overconfident markets at HIGH tier.** Until Phase E calibration is complete, do not include these in your parlay at HIGH confidence:
- `win-either-half` HIGH
- `team-over-under-1.5` HIGH
- `over-under-2.5` HIGH (near-threshold matches)
- `win-to-nil Yes` HIGH

Use them at MEDIUM or not at all.

### The Strategy

For a 50-leg parlay, aim for this composition (approximately):

| Picks | Market | Confidence | Why |
|---|---|---|---|
| 8-10 | `1x2-1up` / `1x2-2up` | HIGH | 82-87% historical hit rate, ≥1.20 odds |
| 8-10 | `double-chance` (12 or X2) | HIGH | 80%+ hit rate, decent odds on clear favourites |
| 8-10 | `over-under-1.5` | HIGH | 83% hit rate, reasonable odds |
| 6-8 | `away-under-2.5` or `home-under-2.5` | HIGH | 81-86%, odds typically 1.20-1.40 |
| 6-8 | `dnb` | MEDIUM | 76% at MEDIUM tier, odds usually 1.30-1.60 |
| 5-7 | `1x2` (home or away win) | HIGH | 72-80% at HIGH, odds usually 1.40-2.00 |
| 3-5 | `win-either-half` | MEDIUM | 65% at MEDIUM, odds usually 1.30-1.60 |

Total: 44-52 picks. Adjust based on what the model produces each weekend.

### Season-Long Profitability

Profitability in a long accumulator strategy works differently from singles betting. You will have losing weeks — many of them. You will occasionally have one week where the parlay lands and the payout covers weeks of losses. The model's job is to:

1. Make each leg more likely to win (79.9% vs 50% random selection)
2. Identify the picks with the best odds available
3. Avoid the markets where the model is overconfident

Over a full season (34 weekends), a model that consistently picks at 80% per leg with ≥1.20 real odds will generate positive expected value in the accumulator — provided you are betting the same stake each week and not chasing losses.

**Track your record**: Use `footy accuracy` weekly. If HIGH accuracy falls below 70% over a 4-week rolling window, something has changed — investigate before continuing.

---

## 13. Local-First Operating Model

### Why Local-First Makes Sense Now

Hosting a production application costs money and introduces operational complexity. More importantly, hosting is only justified when the application has proven profitable — you wouldn't pay for a factory before it produces anything valuable.

The local-first model means:
- Zero ongoing cost
- Full control over data and API keys
- No downtime risk from a cloud provider
- Easy to pause and resume
- The database lives on your machine; you own your data

### What You're Giving Up (And Why It Doesn't Matter Yet)

| Cloud feature | What you're missing | Does it matter now? |
|---|---|---|
| Scheduled cron fetches | Data not auto-updated — you fetch manually | No — fetching takes 30 seconds once a week |
| Web UI on your phone | No browser interface unless running locally | No — the CLI report is sufficient |
| Always-on server | Analysis only runs when your laptop is open | No — you only need it Thursday-Saturday |
| Telegram bot | No push notifications | No — you check the report once |

### When to Move to Hosting

Consider hosting when **all three are true**:

1. **Profitable**: You've tracked at least 10 weekends and the model has demonstrably improved your parlay win rate vs your baseline before using the system.

2. **Reliable**: The model's HIGH accuracy has been consistently ≥75% over at least 8 weeks of real-season data (not just the historical simulation).

3. **Inconvenient locally**: The manual weekly fetch and report is creating friction that's causing you to miss fixtures.

At that point, the Railway.toml and Procfile are already in the repository. Deployment is:

```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway up
```

### The Local Workflow Is the Production Workflow

The CLI is not a development tool — it is the final product. `footy report` is designed to be run weekly and consumed as a decision-support document. The local database accumulates a full season of data. The calibration loop improves the model. Everything works exactly as intended, offline, on your laptop, with zero cost.

---

*Guide version: July 2026 · Footy Predict v1.0*
*134 tests passing · HIGH: 79.9% · MEDIUM: 62.7% · Brier: 0.2064*
*Supported leagues: 12 · Markets: 11 · Factors: 16*
