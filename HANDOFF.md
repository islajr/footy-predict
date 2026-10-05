# Handoff — Match Narratives, Market Assessment & Odds Evaluation

**Written 2026-10-05.** Operational notes covering the match narrative synthesis engine, ranked market assessment narrator, Gen-AI trade-off evaluation, and current Phase D focus.

---

## What Landed Recently

| Feature / Task | Commits | Outcome |
|---|---|---|
| **Match Narrative Synthesis** | `4755315` | `_generate_summary()` in `src/analysis/engine.py` builds human-readable prose from factor `data_points` (form strings, PPG, H2H scorelines, league positions, momentum, defensive matchup). |
| **Ranked Market Narrator** | `c2d1a0b` | `src/analysis/market_narrator.py` ranks ~61 picks per match by backing score ($P \times (1 + 0.15 \times \text{aligned\_factors})$), detects shared statistical foundations (e.g. Home Win, DNB Home, 1X), and generates per-pick narrative prose. |
| **CLI & Web UI Presentation** | `4755315`, `c2d1a0b` | `footy report` displays VERDICT line, summary prose block, and ranked market cards. Web UI (`/analysis/match/:id`) renders color-coded VERDICT pill, summary card, and ranked option cards. |
| **Test Suite Expansion** | — | **246 tests passing green** across factor evaluation, market models, narrator ranking, CLI formatting, and web routes. |

---

## Architectural Decision: Gen-AI vs. Rule-Based Narrator

The team evaluated replacing the rule-based narrative engine with Generative AI (LLMs).

### Key Trade-Off Analysis
1. **Hallucination & Financial Risk:** LLMs can invent or subtly misstate statistics (e.g., claiming 5 consecutive wins instead of 3). In a betting decision-support tool, false statistical premises lead to bad betting decisions. The rule-based engine is 100% factually tied to SQLite.
2. **Latency:** Rule-based generation takes **< 10ms** per matchweek report. LLM API calls add **15–45 seconds** per report run.
3. **Determinism & Auditability:** Rule-based output is reproducible across runs; LLM output varies, making it difficult to isolate model bias shifts from phrasing variance.
4. **Cost & Offline Capability:** Rule-based runs 100% offline at $0 cost; LLMs add external API dependencies and billing overhead.

### Agreed Architecture (The Hybrid Approach)
- **Default Core Engine:** Keep the deterministic, zero-latency rule-based narrator as the primary engine for CLI and Web UI.
- **Opt-in Plugin Layer:** If Gen-AI is implemented in the future, it will operate as an opt-in CLI/web flag (`--ai` / `--llm`) using **Structured Grounding**—feeding exact pre-extracted `FactorResult.data_points` to an LLM with strict instructions to rephrase provided facts only.

---

## Current Focus (Phase D): Real-World Odds Evaluation

With upcoming weekend games approaching, the immediate focus is evaluating model implied odds ($1 / \text{probability}$) against live bookmaker lines (SportyBet / Bet365):

1. **Odds Edge Calculation:**
   $$\text{Edge \%} = \left( \frac{\text{Bookmaker Odds}}{\text{Model Implied Odds}} - 1 \right) \times 100$$
   $$\text{Expected Value (EV)} = (\text{Model Probability} \times \text{Bookmaker Odds}) - 1$$
2. **Selection Strategy:**
   - **Value Bets ($\text{EV} > 0$):** Bookmaker odds exceed model implied odds.
   - **Anchor Bets (HIGH confidence, $\text{EV} \approx 0$):** High probability options offering fair odds for accumulator construction.
   - **Trap Bets ($\text{EV} < 0$):** Bookmaker odds underpay for the model's assessed risk level.
3. **Settlement Tracking:** Log actual match results via `footy record` and evaluate calibration metrics via `footy accuracy`.

---

# Handoff — Standings Integrity

**Written 2026-08-09. All three tasks landed the same day.** Live working notes for whoever
picks this up next, including a future session of me. `cli_guide.md` §10 and §12 are the
user-facing account of the same work; this file carries the operational detail that does
not belong in a user guide — exact queries, exact row counts, what was ruled out and why.

**Status: the work order is discharged; one data decision is left open.** Query B comes
back clean. Query A still returns four rows — `bundesliga`, `la-liga`, `liga-portugal` and
`premier-league` 2026-27, holding 2025-26 data left by the August fetch *before* the label
fix landed. They are residue, not recurrence: nothing in the current code can produce
another. Because they rank, both standings factors will read them as this season's table
until the first real 2026-27 fetch lands. Clearing them is a delete of live data and was
left for the user to authorise — the query is in `cli_guide.md` §10. Delete this file once
that decision is made.

---

## What landed

| Task | Commit | Outcome |
|---|---|---|
| 1. Guard the write | see `git log --oneline -- src/database.py` | `WHERE excluded.played > 0 OR standings.played = 0` |
| 2. Label from the response | `src/fetchers/football_data_org.py` | label from `season.startDate`; stale and season-less responses declined |
| 3. Rebuild by derivation | `src/standings.py`, `footy derive-standings` | 47 tables written, 2 declined; standings 19 league-seasons (13 ranked) → 63 (60 ranked) |

245 tests pass, up from 210 at the time this file was written.

Two things turned up that were not in the original diagnosis, both recorded in
`cli_guide.md` §10:

- **The prune was load-bearing.** Writing 20 derived rows over `premier-league` 2025-26's
  23 would have left Coventry, Hull and Ipswich at position 1, so the repaired table would
  still have failed `standings_are_ranked` and the whole exercise would have accomplished
  nothing. `Database.delete_standings_except` is what makes the rebuild take.
- **Nine league-seasons are undrivable because a club is stored under two `teams` rows.**
  `CA Osasuna` (id 860) and `Osasuna` (id 38055) are one club — `canonical_key` strips
  `fc`, `cf`, `cd` and `as` but not `ca`. Also `1. FSV Mainz 05` / `Mainz`, six pairs in
  Liga Portugal 2025-26, plus Scottish Premiership 2024-25, Süper Lig 2024-25 and
  Eliteserien 2022/2023/2024. Needs an alias entry in `src/team_names.py` and a dedupe
  pass — its own change, not this one.

---

## Where things stood when this was written

`league_position` (7.5% weight) and `opponent_strength` (4.5%) read the league table.
Both used to check whether standings *existed* without checking whether they *ranked
anything*, so a blank pre-season table produced a score of exactly 0.000 at **confidence
0.85** — a confident non-answer that survives the engine's weighted-confidence average
and drags the aggregate toward neutral.

That is fixed. `standings_are_ranked()` in `src/analysis/factors.py` now gates both
factors: positions must be distinct, and at least one match must have been played. Six of
19 stored league-seasons failed it, `footy status` names them, and 210 tests passed.

**The fix stops the engine trusting bad data. It does not stop the data going bad.** That
was what remained, and it is what the three tasks below addressed.

---

## The actual bug, in two halves

### Half 1 — the label comes from the calendar, not from the response

`fetch_and_store_standings` (`src/fetchers/football_data_org.py:438`) computes
`season_label = league.get_season_label()` — which is derived from *today's date* via
`get_current_season()` (`src/config.py:142`, `now.year if now.month >= 8 else now.year - 1`).
It then calls `get_standings(fdo_code)`, which sends **no season parameter** and returns
only `standings[type=TOTAL].table`, discarding the response's own `season` block.

So nothing in the pipeline ever knows which season it received. Across the July–August
turnover, label and contents diverged by a full season — in both directions:

```
fetched 2026-07-10  →  labelled 2025-26  →  contents: the empty 2026-27 table
fetched 2026-08-03  →  labelled 2026-27  →  contents: the final 2025-26 table
```

Neither is a wrong label computation. `get_season_label()` on 2026-08-03 correctly returns
`2026-27`. The API returned something other than what the label described, and nothing
checked.

**Proof by team list, not by the zeros** — this is what makes it certain rather than
suspected:

- `premier-league` **2025-26** holds rows for Coventry, Hull and Ipswich. Those clubs
  played **0** matches in 2025-26 and 38 each in 2026-27. Only a 2026-27 table contains
  them.
- `premier-league` **2026-27** is Arsenal 1st, 38 played, 85 pts, with West Ham / Burnley /
  Wolves 18th–20th — the clubs relegated *out of* 2026-27 before a ball was kicked in it.

### Half 2 — the write path accepts a downgrade

`upsert_standing` (`src/database.py:1031`) ends in an unconditional clause:

```sql
ON CONFLICT(league_id, season, team_id) DO UPDATE SET
    position = excluded.position, played = excluded.played, ...
```

A blank row overwrites a complete one without complaint. Combined with half 1, the
2026-07-10 fetch wrote an empty 2026-27 table on top of real 2025-26 final tables.

**53 real rows destroyed:** 17 in `premier-league` 2025-26, 18 in `eredivisie` 2025-26,
18 in `ligue-1` 2025-26.

The survivors prove the mechanism. In `premier-league` 2025-26, exactly three rows still
carry real data, stamped `2026-06-18 06:53:40` while the other 20 are stamped
`2026-07-10 04:26:06`:

```
 18  West Ham United FC           played=38  pts=39   2026-06-18 06:53:40
 19  Burnley FC                   played=38  pts=22   2026-06-18 06:53:40
 20  Wolverhampton Wanderers FC   played=38  pts=20   2026-06-18 06:53:40
```

Those are the relegated clubs. They were absent from the incoming 2026-27 table, so no
`ON CONFLICT` fired and nothing touched them. This is also the whole explanation of the
weird `23 teams, 4 distinct positions` shape flagged in `footy status` — 20 clobbered rows
plus 3 orphaned survivors from the previous season's table.

---

## The three tasks, as specified — all done

Order mattered: fix the write before rebuilding, or the rebuild becomes the next casualty.
Kept below because the reasoning explains the shape of the code that landed.

### 1. Guard the write — `src/database.py:1031`

Refuse a downgrade. SQLite supports a trailing `WHERE` on an upsert, which turns the
overwrite into a silent no-op when the incoming row is emptier than the stored one:

```sql
ON CONFLICT(league_id, season, team_id) DO UPDATE SET
    position = excluded.position, played = excluded.played, ...
WHERE excluded.played > 0 OR standings.played = 0
```

Reads as: take the update if the new row has real data, or if the stored row has none to
lose. ~15 lines including a test that a `played=0` upsert over a `played=38` row leaves it
intact. Independently valuable — commit it on its own; it stops the bleeding without
depending on anything below.

### 2. Label from the response — `src/fetchers/football_data_org.py:235`

Have `get_standings` return the `season` block alongside the table rather than throwing it
away, and derive the label from `season.startDate`'s year instead of the calendar.

While there, enforce the invariant that makes staleness self-announcing:

> A season whose `startDate` is in the future cannot have `playedGames > 0`.

All seven cached FDO responses carry a 2026-27 `season` block starting between 2026-08-07
and 2026-08-28. Four report a completed season's totals under it — PL 760, PD 760, BL1 612,
PPL 592 played games, fetched 2026-08-03. When that fires, the response is stale on
arrival: log it and decline the write. Do not store it under the other label either — a
completed table under the correct old label would overwrite a *derived* table that is
probably better.

### 3. Rebuild the six damaged tables — `compute_standings()`

Every source match survived: 380 finished PL 2025-26 fixtures, 306 each for Eredivisie and
Ligue 1. So the tables are fully recoverable by derivation from `matches`.

This is the derived-standings work already scoped in `cli_guide.md` §12 (~80 lines: query
FINISHED matches, group by team, sum W/D/L and goals, rank by points then GD). It was
ranked third there on its own merits; the clobber gives it a second and nearer-term job.

Two cautions:

- Derive into the season the *matches* belong to. Do not trust an existing standings row's
  label to tell you which season a table should cover — that label is the thing under
  suspicion.
- Run `standings_are_ranked()` on the derived table before writing it. If it comes back
  every-team-at-position-1, the match query returned nothing and writing it would recreate
  the precise condition this whole effort exists to remove.

---

## Verification queries

These are the contradictions the damage leaves behind. They double as regression tests —
after the fix, the first should return no rows.

```sql
-- A: table claims games played, but no finished matches are stored for that season.
-- Currently returns 4 rows: bundesliga, la-liga, liga-portugal, premier-league (all 2026-27).
SELECT l.slug, s.season, SUM(s.played) AS sum_played
FROM standings s JOIN leagues l ON l.id = s.league_id
GROUP BY l.slug, s.season
HAVING sum_played > 0
   AND (SELECT COUNT(*) FROM matches m
        WHERE m.league_id = s.league_id AND m.season = s.season
          AND m.status = 'FINISHED') = 0;

-- B: mixed fetch timestamps within one league-season — the clobber signature.
-- Currently returns premier-league 2025-26 (2026-06-18 vs 2026-07-10).
SELECT l.slug, s.season, MIN(s.fetched_at), MAX(s.fetched_at), COUNT(*)
FROM standings s JOIN leagues l ON l.id = s.league_id
GROUP BY l.slug, s.season
HAVING date(MIN(s.fetched_at)) <> date(MAX(s.fetched_at));
```

**A check that does _not_ work — do not use it as a detector.** "Team holds a standings row
for a season it never played a match in" sounds like the ideal test, and it does catch the
mislabels, but it also fires on ordinary missing match rows. Serie A 2025-26 trips it
because Fiorentina and Genoa have final-table rows (`played=38`) with no match rows stored
— unrelated to this bug. It flags 8 sets where only 5 are mislabelled. Useful as a
hand-inspection lead, useless as an assertion. I burned time treating its output as a
result; it is a starting point.

---

## Ruled out — don't redo this analysis

- **Purging the degenerate rows.** Considered and rejected. `get_standings` has no reader
  outside the two factors, the rows are inert now that both gate on
  `standings_are_ranked()`, the next fetch would recreate them anyway, and their
  `fetched_at` timestamps are the only surviving evidence of which fetch did the damage.
  Fix the write path and rebuild instead. Revisit only after task 3 lands.

- **"The API returns last season during pre-season, stored under the requested label."**
  This was my first explanation and it is wrong — I published it in the guide and have
  since corrected it. The `season` block in every cached response is correctly 2026-27,
  and `get_season_label()` computes correctly. The label is not the defect; the
  *unverified* label is, and the contents are stale independently.

- **Any figure from joining `matches` to `standings` on `(league_id, season)`.** That join
  fans out — one match row per standings row — and multiplies counts by team count. It
  produced a bogus "5,508 finished matches" (306 × 18) that reached the guide before being
  caught. Count matches in a subquery, as the verification queries above do.

---

## Before touching the database

Back up first — the user has asked for this explicitly and the whole subject of this file
is a destructive write that nobody noticed:

```bash
cp footy.db "footy.db.backup-$(date +%Y%m%d-%H%M%S)"
```

Existing backups: `footy.db.backup-predup-20260806-164216`,
`footy.db.backup-20260805-003955`, `footy.db.backup-preload-20260805`.

Repo conventions: conventional commits, incremental, each with the trailer
`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Tests run via
`.venv/bin/python -m pytest` — a bare `python` misses the venv and fails on `dotenv`.
