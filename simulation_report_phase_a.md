# Simulation Report — Full History

> All runs: 200 FINISHED matches · 2025/26 season (2025-08-01 onwards)
> Latest run (Aug 5): 13,072 picks generated · 12,982 settled · 90 void (DNB draws only)

---

## Progression Table

| Metric | Phase A (Jul 9) | Phase C + 1UP fix (Jul 19 AM) | Cal Round 1 (Jul 19 PM) | Cal Round 2 (Jul 19 EVE) | **Re-baseline (Aug 5)** |
|---|---|---|---|---|---|
| Overall accuracy † | 50.5% | 50.3% | 50.3% | 50.3% | 49.4% |
| **Brier score** | 0.2185 | 0.2138 | 0.2144 | 0.2064 | **0.2037** |
| Picks/match | 34.6 | 41.8 | 41.8 | 41.8 | 65.4 |
| **HIGH accuracy** | 76.3% | 77.2% | 78.4% | 79.9% | **80.3%** |
| HIGH count | 1,868 | 2,445 | 2,041 | 1,973 | 2,427 |
| **MEDIUM accuracy** | 61.5% | 58.0% | 60.4% | 62.7% | **67.0%** |
| MEDIUM count | 933 | 1,183 | 1,485 | 1,466 | 2,284 |
| LOW count | 4,064 | 4,679 | 4,781 | 4,868 | 8,271 |
| 1x2 market | 33.3% | 40.6% | 40.6% | 40.6% | 38.6% |
| DNB (overall) | — | 53.5% HIGH | MEDIUM 76.2% (re-tiered) | MEDIUM 76.2% ✓ | 50.0% † |
| WEH (overall) | — | 60.5% | 60.5% | 60.5%* | 52.2% |

> [!IMPORTANT]
> **† Overall and per-market accuracy are artifacts — do not read them as quality.**
> The engine emits *both* sides of every binary market with complementary
> probabilities (e.g. `over-under-2.5` stores "Over 2.5" at p=0.664 *and*
> "Under 2.5" at p=0.336). Exactly one side of each pair always wins at
> settlement, so per-market hit rates mechanically converge on 50.0% and the
> overall figure sits near 49–50% regardless of how good the model is.
>
> **Only the confidence tiers and the Brier score are valid measures.** The tiers
> filter to the side the model actually favours; Brier scores probability
> calibration rather than pick selection.

> [!NOTE]
> The Aug 5 re-baseline follows the data-integrity work: team canonicalisation
> (282→245 clubs, later 325 across all leagues), removal of 11,609 duplicate
> fixture rows, a fixture-identity unique index, and Eliteserien history via
> API-Football (242→726 matches). Picks/match rose 41.8→65.4 because
> deduplication and canonicalisation restored the history depth that several
> markets need before they will emit.
>
> MEDIUM gained the most (62.7%→67.0%, +4.3pp), which matters because
> `footy report` defaults to the MEDIUM tier.

> [!NOTE]
> \* WEH overall accuracy (60.5%) did not improve in Round 2 because the simulation
> used existing settled predictions. The WEH multiplier fix (1.35→1.15) reclassifies
> many WEH HIGH picks to MEDIUM, which will show improvement in the next real-season run.

---

## What Changed in the Calibration Round (Jul 19 PM)

### 1. 1X2 Probability Shrinkage
Raw logistic model was outputting P(home win) = 0.85-0.95 for dominant sides.
Football reality: even the strongest sides win only ~65-72% at home.

Implemented 30% regression toward European prior (H=0.46, D=0.26, A=0.28).
Caps: home ≤ 0.72, away ≤ 0.65, draw ≥ 0.10.

**Effect:** P(home win) now ranges 0.50–0.72, consistent with observed rates.

### 2. Draw-No-Bet Formula Fix
**Old formula:** `dnb_home = p_home / (p_home + p_away)` — inflated by +36pp  
**New formula:** `dnb_home = p_home` — P(win outright) is the correct value

DNB wins only on outright win. Draw returns stake but is not a win.
The old formula was treating draw probability as redistributable, which is wrong.

**Effect:** DNB picks no longer reach HIGH tier (P≤0.72 < 0.80 threshold).
DNB MEDIUM picks now hit at 76.2% — correctly labelled.

Previously DNB Away at HIGH was hitting 51.5% — stated at 87.7%. Now it's
correctly in MEDIUM/LOW where those probabilities belong.

### 3. Dixon-Coles Low-Score Correction (O/U model)
Pure Poisson underestimates 0-0, 1-0, 0-1 outcomes. When expected_total
is within 0.6 goals of the line, applied a shrink-toward-0.5:
`shrink = 0.65 + 0.35 × (distance / 0.6)`

Fixes the worst overconfident case: Away Over 1.5 (stated 91%, hit 22%).

### 4. HIGH Confidence Threshold Raised: 0.75 → 0.80
After shrinkage compresses the probability scale, P=0.75-0.80 represents
weaker picks. Raising the threshold ensures HIGH label means genuinely strong.

---

## Post-Calibration HIGH Market Breakdown

| Market | HIGH hit rate | Count | Stated avg | Gap |
|---|---|---|---|---|
| `over-under-0.5` | **99.3%** | 307 | 96.3% | -3pp (well calibrated) |
| `team-over-under` | **84.2%** | 120 | 83.2% | -1pp ✓ |
| `over-under-1.5` | **83.2%** | 244 | 88.7% | +5pp ✓ |
| `double-chance` | **80.0%** | 290 | 88.7% | +9pp |
| `team-over-under-2.5` | 79.8% | 331 | 92.4% | +13pp ← still inflated |
| `win-to-nil` | 78.4% | 320 | 94.3% | +16pp ← still inflated |
| `handicap` | 78.2% | 87 | 92.1% | +14pp ← Poisson λ issue |
| `team-over-under-0.5` | 77.6% | 219 | 92.1% | +14pp ← Poisson λ issue |
| `1x2` (all) | 72.6% | 117 | 84.6% | +12pp |
| `over-under-3.5` | 69.7% | 271 | 90.3% | +20pp ← still inflated |
| `win-either-half` | 61.1% | 131 | 89.5% | +28pp ← needs own calibration |
| `team-over-under-1.5` | 55.6% | 162 | 91.1% | +35pp 🚨 |
| `over-under-2.5` | 54.3% | 92 | 91.8% | +37pp 🚨 |

> [!CAUTION]
> **Team O/U 1.5 and O/U 2.5** remain badly overconfident at HIGH tier.
> Root cause: their probabilities come from raw Poisson λ (team GPG), which
> isn't shrunk through the 1X2 path. Team GPG of 2.5+ produces P(≥2 goals)=0.80+
> via Poisson, but actual rates are much lower. **Do not use these HIGH picks
> in parlays until the Poisson λ shrinkage is implemented.**

---

## Best Picks for Parlay Backbone

From the diagnostic (HIGH confidence, n≥15, sorted by hit rate):

| Hit Rate | Pick | n | Avg Odds | Use in parlay? |
|---|---|---|---|---|
| 100% | Away 1X2 - 1UP | 15 | 1.20 | ✓ Yes — selective |
| 99.3% | Over 0.5 | 307 | 1.04 | ✓ Backbone (low odds) |
| 87.8% | Home 1X2 - 1UP | 49 | 1.17 | ✓ Yes |
| 86.0% | Double Chance 12 | 215 | 1.13 | ✓ Strong |
| 85.9% | Away Under 2.5 | 170 | 1.08 | ✓ Strong |
| 84.2% | Home Over 0.5 | 120 | 1.20 | ✓ Backbone |
| 83.2% | Over 1.5 | 244 | 1.14 | ✓ Good |
| 81.8% | Home -1 (void at 1) | 22 | 1.06 | ✓ Use carefully |
| 81.4% | Home Under 2.5 | 140 | 1.09 | ✓ Strong |
| 80.7% | Home Win to Nil: No | 140 | 1.07 | ✓ Backbone |

**Avoid at HIGH tier (overconfident):**
- Team O/U 1.5 HIGH — 55.6% hit rate, stated 91%
- O/U 2.5 HIGH — 54.3% hit rate, stated 91%
- WEH HIGH — 61.1% hit rate, stated 89%
- DNB HIGH — 27.5% hit rate (legacy predictions; new model doesn't generate DNB HIGH)

---

## Parlay Availability — 45-50 Leg Answer

**Current HIGH picks per match: 8.8**

| Weekend size | HIGH picks available | 45-leg parlay? |
|---|---|---|
| 40 fixtures (quiet) | ~352 | ✓ 1 per match covers 40 legs |
| 60 fixtures (typical) | ~528 | ✓ Comfortable |
| 80 fixtures (busy) | ~704 | ✓ Very comfortable |

**Yes — 45-50 leg parlays are feasible every week from HIGH picks alone.**

With 8.8 HIGH picks per match, each fixture produces multiple HIGH options.
For a 45-leg parlay you'd pick one HIGH pick per match from ~50-60 matches,
choosing the best-fitting market per fixture.

**Recommended parlay construction (post-calibration, with 1.32 odds floor):**

> [!IMPORTANT]
> After applying the ≥1.32 odds floor, most of the ultra-safe picks (Over 0.5 at 1.04,
> DC-12 at 1.13, Away Under 2.5 at 1.08) are eliminated by the value zone filter.
> The picks that survive are: 1UP picks (1.17-1.30), O/U 1.5 (1.14-1.40), 1X2 wins
> (1.40-2.00), WEH MEDIUM (1.30-1.60), DNB MEDIUM (1.30-1.60).

1. Use `footy parlay --size 50 -c HIGH --min-odds 1.32` to get the system's recommendation
2. Layer in: 1UP picks, O/U 1.5 HIGH, Home/Away win 1X2 HIGH, Away Under 2.5 (borderline)
3. Fill remaining legs with DNB MEDIUM (76.2% hit rate, decent odds), WEH MEDIUM
4. **Avoid in any parlay**: Team O/U 1.5 HIGH, O/U 2.5 HIGH, WEH HIGH, WtN Yes HIGH

**The parlay math:**
- 47 legs at 78.4%: P(all win) = 0.784^47 ≈ 0.00016 (1 in 6,250)
- But with avg odds ~1.10 per leg: payout = 1.10^47 ≈ 79x stake
- EV = 0.00016 × 79x... the payout needs to be >6,250x for positive EV at these hit rates

The honest answer: **parlay EV depends entirely on whether your hit rate (78.4%)
exceeds what the bookmaker has priced in each pick.** The model tells you *which*
picks are most likely to win — but unless you can compare our implied odds against
SportyBet's offered odds, you can't verify edge. Bookmaker odds integration
(Phase D) will make this measurable.

---

## Remaining Calibration Issues (Post Round 2)

| Issue | Status | Remaining gap |
|---|---|---|
| Team O/U 1.5/2.5 | ✅ _shrink_lambda applied | Gap reduced; real impact in 2026/27 data |
| WEH overconfident | ✅ Multiplier 1.35→1.15, cap 0.95→0.82 | Will show in real-season picks |
| Win to Nil +16pp gap | ✅ CS rate shrinkage applied | Will show in real-season picks |
| O/U 2.5 near-threshold | ✅ Dixon-Coles + lambda shrinkage | Gap partially closed |
| WEH HIGH (61.1%) | Downtiered to MEDIUM by cap change | ✅ MEDIUM WEH is usable (65%) |
| Platt scaling (global) | 📋 Not yet implemented | Would tighten all remaining gaps |
| xG quality | ⚠️ 0% data — blocked, not merely pending | FDO-sourced rows lack `api_football_id`; see Factor Data Coverage |

All simulation improvements will be verified in real-season tracking (August 2026 onwards).
Until then, treat Round 2 gains as directionally correct but not yet confirmed at scale.

---

## Comparison Across All Simulation Runs

| Run | Date | HIGH acc | MEDIUM acc | Brier | Key change |
|---|---|---|---|---|---|
| Phase A baseline | Jul 9 | 76.3% | 61.5% | 0.2185 | 15 factors |
| Phase C + 1UP fix | Jul 19 AM | 77.2% | 58.0% | 0.2138 | 1UP/2UP correct settlement |
| Cal Round 1 | Jul 19 PM | 78.4% | 60.4% | 0.2144 | 1X2 shrinkage + DNB fix + Dixon-Coles |
| Cal Round 2 | Jul 19 EVE | 79.9% | 62.7% | 0.2064 | Poisson λ shrinkage + WEH + WtN CS |
| **Re-baseline** | **Aug 5** | **80.3%** | **67.0%** | **0.2037** | **Dedup + canonicalisation + Eliteserien history** |

---

## Factor Data Coverage (Aug 5 run, 200 matches)

Thirteen of sixteen factors have full coverage. The three below are the only
ones running short of data, and they are the actionable gaps:

| Factor | Coverage | Why |
|---|---|---|
| `xg_quality` | **0%** (0/200) | Inert. Requires `match_stats.expected_goals`, which only API-Football supplies. See note below. |
| `league_position` | 64% (128/200) | Standings absent for some league-seasons in the sample window. |
| `opponent_strength` | 67.5% (135/200) | Derived from standings, so it inherits the same gap. |

### Why xG is still at zero

`footy fetch --stats` backfills `match_stats` only for matches that carry an
`api_football_id`. The seven leagues that `footy fetch` prefers to source from
football-data.org — Premier League, La Liga, Serie A, Bundesliga, Ligue 1,
Liga Portugal, Eredivisie — have `api_football_id = NULL` on their historical
rows, so `fetch_and_store_match_stats` skips every one of them. Roughly 17,500
finished matches are structurally unreachable by `--stats` in their current state.

This is not a bug in the stats fetcher — it was verified working (10/10 fixtures
populated for Saudi Pro League, including xG, possession and passes). It is a
consequence of source preference.

Two ways out, neither yet applied at scale:

1. Run plain `footy fetch` (no `--stats`) once per FDO league. `upsert_match`
   carries `api_football_id=COALESCE(matches.api_football_id, excluded.api_football_id)`,
   so an API-Football pass backfills the missing ids in place; `--stats` can then
   reach those rows.
2. Let it resolve naturally — fixtures fetched from the 2026/27 season onward
   arrive with an `api_football_id` already attached.

Either path is bounded by the free tier: 100 requests/day, 10 requests/minute.

---

*Report generated: 2026-08-05 · 200 matches · 12,982 settled picks · 196 tests passing*
*Current state: HIGH 80.3% · MEDIUM 67.0% · Brier 0.2037 · VALUE_MIN_ODDS 1.32*
*Database: 21,848 matches (19,484 finished) · 325 teams · 12 leagues*
*Next milestone: backfill api_football_id for FDO leagues to activate xG · real-season calibration tracking*
