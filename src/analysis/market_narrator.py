"""
Footy Predict — Market Narrator

Transforms a flat list of MarketRecommendation objects into a ranked,
narrative-annotated assessment of every market for a fixture.

Design goals
------------
* **Rank by statistical backing** (not just probability): a pick supported by
  multiple independent factors scores higher than one driven by a single signal,
  even if their probabilities are similar.
* **Show everything**: all picks are shown so the user can compare model implied
  odds against bookmaker odds across every market.
* **Comparison language**: each pick's narrative notes why it sits where it does
  relative to its neighbours — shared statistical basis, additional conditions
  required, risk-reward trade-offs.
* **Bookmaker-comparison framing**: where picks share the same statistical thesis
  (e.g. Home Win vs DNB vs 1X), the narrative explicitly invites the user to
  compare bookmaker prices.

Statistical theme classification
---------------------------------
Picks are classified into themes so the narrator can generate coherent comparison
language across related markets:

    OUTCOME   — 1X2, Double Chance, DNB
    GOALS     — Over/Under (total), BTTS/GG
    CLEAN_SHEET — Win to Nil, Team Under 0.5
    DOMINANCE — Handicap, Win Either Half, Lead Trigger (1UP/2UP)
    TIMING    — 1st/2nd Half Over/Under, Team 1st/2nd Half O/U
    OTHER     — anything else
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.analysis.factors import FactorResult
from src.analysis.markets import MarketRecommendation


# ═══════════════════════════════════════════════════════════════
#  AssessedMarket dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class AssessedMarket:
    """A single market pick enriched with a ranked narrative assessment.

    Attributes:
        rank: 1-based rank by statistical backing (1 = strongest).
        pick: Human-readable pick direction (e.g. "Home Win").
        market_name: Market group name (e.g. "Match Result (1X2)").
        market_code: Internal code (e.g. "1x2").
        probability: Model probability (0–1).
        implied_odds: 1 / probability — fair model odds.
        confidence: "HIGH" / "MEDIUM" / "LOW".
        backing_score: Composite statistical plausibility score.
        narrative: 2–3 sentence assessment including ranking context.
        theme: Statistical theme bucket (OUTCOME / GOALS / etc.).
        shared_thesis_refs: List of rank numbers that share the same
            statistical thesis as this pick (for cross-reference).
    """

    rank: int
    pick: str
    market_name: str
    market_code: str
    probability: float
    implied_odds: float
    confidence: str
    backing_score: float
    narrative: str
    theme: str
    shared_thesis_refs: list[int] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  Theme classification
# ═══════════════════════════════════════════════════════════════

_THEME_PATTERNS: list[tuple[str, str]] = [
    # (regex on market_code, theme)
    (r"^1x2$",                     "OUTCOME"),
    (r"^double-chance$",           "OUTCOME"),
    (r"^draw-no-bet$",             "OUTCOME"),
    (r"^over-under-",              "GOALS"),
    (r"^gg-ng$",                   "GOALS"),
    (r"^win-to-nil$",              "CLEAN_SHEET"),
    (r"^team-over-under-0\.5$",    "CLEAN_SHEET"),
    (r"^handicap",                 "DOMINANCE"),
    (r"^win-either-half$",         "DOMINANCE"),
    (r"^lead-trigger",             "DOMINANCE"),
    (r"^first-half-",              "TIMING"),
    (r"^second-half-",             "TIMING"),
    (r"^team-first-half-",         "TIMING"),
    (r"^team-second-half-",        "TIMING"),
    (r"^team-over-under-",         "TIMING"),
]

def _classify_theme(market_code: str) -> str:
    for pattern, theme in _THEME_PATTERNS:
        if re.match(pattern, market_code):
            return theme
    return "OTHER"


# ═══════════════════════════════════════════════════════════════
#  Backing score — statistical plausibility
# ═══════════════════════════════════════════════════════════════

# Which factor names provide independent supporting evidence for each theme
_THEME_FACTOR_SIGNALS: dict[str, list[str]] = {
    "OUTCOME": ["recent_form", "home_form", "away_form", "league_position",
                "head_to_head", "momentum"],
    "GOALS": ["scoring_frequency", "defensive_record", "head_to_head",
              "scoring_patterns", "attacking_matchup"],
    "CLEAN_SHEET": ["defensive_record", "away_form", "scoring_frequency"],
    "DOMINANCE": ["league_position", "home_form", "recent_form", "momentum"],
    "TIMING": ["goal_timing", "scoring_frequency"],
    "OTHER": ["recent_form"],
}

def _compute_backing_score(
    rec: MarketRecommendation,
    factors: list[FactorResult],
    theme: str,
) -> float:
    """Compute a composite score measuring statistical plausibility.

    Formula:
        probability × (1 + 0.15 × aligned_factors)

    where aligned_factors counts how many high-confidence factors
    independently support the same pick direction as this recommendation.
    """
    relevant_factor_names = _THEME_FACTOR_SIGNALS.get(theme, ["recent_form"])

    # Determine pick direction: positive = home-favoured picks
    home_favoured_pick = any(
        kw in rec.pick.lower()
        for kw in ("home", "1 (", "1x", "12", "over", "gg", "home win")
    )

    aligned = 0
    for f in factors:
        if f.name not in relevant_factor_names:
            continue
        if f.confidence < 0.35:
            continue
        # Score > 0 means home-favoured; align if pick direction matches
        if home_favoured_pick and f.score > 0.1:
            aligned += 1
        elif not home_favoured_pick and f.score < -0.1:
            aligned += 1

    return rec.probability * (1.0 + 0.15 * aligned)


# ═══════════════════════════════════════════════════════════════
#  Narrative building
# ═══════════════════════════════════════════════════════════════

def _rank_label(rank: int) -> str:
    """Circled number for rank labels ① ② … ⑳"""
    CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    if 1 <= rank <= 20:
        return CIRCLED[rank - 1]
    return f"({rank})"


def _dp(factors: list[FactorResult], name: str) -> dict[str, Any]:
    for f in factors:
        if f.name == name:
            return f.data_points or {}
    return {}


def _factor_score(factors: list[FactorResult], name: str) -> float:
    for f in factors:
        if f.name == name:
            return f.score or 0.0
    return 0.0


def _factor_conf(factors: list[FactorResult], name: str) -> float:
    for f in factors:
        if f.name == name:
            return f.confidence or 0.0
    return 0.0


def _build_narrative(
    rec: MarketRecommendation,
    rank: int,
    total: int,
    theme: str,
    factors: list[FactorResult],
    home_name: str,
    away_name: str,
    same_thesis_above: list[int],   # ranks of picks with same thesis ranked above
    same_thesis_below: list[int],   # ranks of picks with same thesis ranked below
    prev_rec: MarketRecommendation | None,
    next_rec: MarketRecommendation | None,
) -> str:
    """Generate the 2–3 sentence narrative for a single assessed market."""

    sentences: list[str] = []

    # ── 1. Primary statistical evidence ──────────────────────────────────────
    pick_lower = rec.pick.lower()
    code = rec.market_code

    if theme == "OUTCOME":
        sentences.append(_outcome_evidence(rec, factors, home_name, away_name))

    elif theme == "GOALS":
        sentences.append(_goals_evidence(rec, factors, home_name, away_name, code))

    elif theme == "CLEAN_SHEET":
        sentences.append(_clean_sheet_evidence(rec, factors, home_name, away_name))

    elif theme == "DOMINANCE":
        sentences.append(_dominance_evidence(rec, factors, home_name, away_name, code))

    elif theme == "TIMING":
        sentences.append(_timing_evidence(rec, factors, home_name, away_name, code))

    else:
        # Generic fallback: just use the existing rationale
        rationale = rec.rationale or ""
        sentences.append(rationale[:200] + ("…" if len(rationale) > 200 else ""))

    # ── 2. Rank context sentence ──────────────────────────────────────────────
    rank_note = _rank_context_sentence(
        rec, rank, total, same_thesis_above, same_thesis_below, home_name, away_name
    )
    if rank_note:
        sentences.append(rank_note)

    # ── 3. Bookmaker comparison note (for shared-thesis picks) ─────────────
    if same_thesis_above:
        ref_label = _rank_label(same_thesis_above[0])
        sentences.append(
            f"Same statistical basis as {ref_label} — compare bookmaker odds "
            f"before choosing between them."
        )

    return " ".join(s for s in sentences if s.strip())


# ── Per-theme evidence builders ───────────────────────────────────────────────

def _outcome_evidence(
    rec: MarketRecommendation,
    factors: list[FactorResult],
    home_name: str,
    away_name: str,
) -> str:
    pick_lower = rec.pick.lower()
    is_home = "home" in pick_lower or pick_lower.startswith("1")
    is_draw = pick_lower.startswith("x") or "draw" in pick_lower

    rf_dp = _dp(factors, "recent_form")
    hf_dp = _dp(factors, "home_form")
    af_dp = _dp(factors, "away_form")
    pos_dp = _dp(factors, "league_position")
    h2h_dp = _dp(factors, "head_to_head")

    parts: list[str] = []

    if is_home:
        h_rf = rf_dp.get("home") or {}
        h_form = h_rf.get("form_string") or ""
        h_ppg = h_rf.get("ppg")
        h_wr_home = hf_dp.get("win_rate")
        h_streak = hf_dp.get("current_home_streak") or {}

        if h_form or h_ppg is not None:
            seg = f"{home_name} in {h_form} form" if h_form else f"{home_name}"
            if h_ppg is not None:
                seg += f", {h_ppg:.1f} PPG"
            parts.append(seg)
        if h_wr_home is not None:
            parts.append(f"{h_wr_home:.0%} home win rate")
        if h_streak.get("length", 0) >= 3 and h_streak.get("type") == "W":
            parts.append(f"{h_streak['length']}-game winning streak at home")

        # H2H
        h2h_hw = h2h_dp.get("home_wins")
        h2h_aw = h2h_dp.get("away_wins")
        h2h_d = h2h_dp.get("draws")
        h2h_n = h2h_dp.get("total_meetings")
        if h2h_hw is not None and h2h_n and h2h_n >= 3:
            parts.append(f"{h2h_hw}W–{h2h_d}D–{h2h_aw}L H2H record")

        # Table gap
        h_pos = pos_dp.get("home_position")
        a_pos = pos_dp.get("away_position")
        pts_gap = pos_dp.get("points_gap")
        if h_pos and a_pos and pts_gap and pts_gap >= 4:
            parts.append(
                f"table: {home_name} {h_pos}{'st' if h_pos==1 else 'nd' if h_pos==2 else 'rd' if h_pos==3 else 'th'}"
                f" vs {away_name} {a_pos}{'st' if a_pos==1 else 'nd' if a_pos==2 else 'rd' if a_pos==3 else 'th'} ({pts_gap} pts)"
            )

        return ", ".join(parts) + "." if parts else rec.rationale[:160]

    elif is_draw:
        h2h_d = h2h_dp.get("draws")
        h2h_n = h2h_dp.get("total_meetings")
        rf_h = rf_dp.get("home") or {}
        rf_a = rf_dp.get("away") or {}
        h_dr = rf_h.get("draw_rate")
        a_dr = rf_a.get("draw_rate")

        if h2h_n and h2h_d is not None:
            parts.append(
                f"{h2h_d} draws in {h2h_n} H2H meetings "
                f"({h2h_d/h2h_n:.0%} draw rate)"
            )
        if h_dr is not None and a_dr is not None:
            avg_dr = (h_dr + a_dr) / 2
            parts.append(f"combined recent draw tendency {avg_dr:.0%}")

        h_pos = pos_dp.get("home_position")
        a_pos = pos_dp.get("away_position")
        if h_pos and a_pos and abs(h_pos - a_pos) <= 3:
            parts.append("closely matched sides on the table")

        return ", ".join(parts) + "." if parts else rec.rationale[:160]

    else:  # away win
        a_rf = rf_dp.get("away") or {}
        a_form = a_rf.get("form_string") or ""
        a_ppg = a_rf.get("ppg")
        a_wr_away = af_dp.get("win_rate")
        h2h_aw = h2h_dp.get("away_wins")
        h2h_n = h2h_dp.get("total_meetings")

        if a_form or a_ppg is not None:
            seg = f"{away_name} in {a_form} form" if a_form else f"{away_name}"
            if a_ppg is not None:
                seg += f", {a_ppg:.1f} PPG"
            parts.append(seg)
        if a_wr_away is not None:
            parts.append(f"{a_wr_away:.0%} away win rate")
        if h2h_aw is not None and h2h_n and h2h_n >= 3:
            parts.append(f"{h2h_aw} away wins in {h2h_n} H2H meetings")

        return ", ".join(parts) + "." if parts else rec.rationale[:160]


def _goals_evidence(
    rec: MarketRecommendation,
    factors: list[FactorResult],
    home_name: str,
    away_name: str,
    market_code: str,
) -> str:
    sf_dp = _dp(factors, "scoring_frequency")
    def_dp = _dp(factors, "defensive_record")
    h2h_dp = _dp(factors, "head_to_head")
    ss_dp = rec.supporting_stats or {}

    is_over = "over" in rec.pick.lower() or "gg" in rec.pick.lower()
    parts: list[str] = []

    home_gpg = ss_dp.get("home_gpg") or sf_dp.get("home_gpg") or sf_dp.get("home_home_gpg")
    away_gpg = ss_dp.get("away_gpg") or sf_dp.get("away_gpg") or sf_dp.get("away_away_gpg")
    expected = ss_dp.get("expected_total")

    if expected is not None:
        parts.append(f"combined expected goals ≈ {expected:.1f}")
    elif home_gpg is not None and away_gpg is not None:
        parts.append(
            f"{home_name} avg {home_gpg:.1f} at home, "
            f"{away_name} avg {away_gpg:.1f} away"
        )

    h2h_avg = h2h_dp.get("avg_goals_h2h")
    if h2h_avg is not None:
        parts.append(f"H2H average {h2h_avg:.1f} goals/meeting")

    if not is_over:
        # Under / BTTS:No / NG
        home_cs = def_dp.get("home_cs_rate_home") or def_dp.get("home_cs_rate")
        away_cs = def_dp.get("away_cs_rate_away") or def_dp.get("away_cs_rate")
        home_cpg = def_dp.get("home_cpg_home") or def_dp.get("home_cpg")
        away_cpg = def_dp.get("away_cpg_away") or def_dp.get("away_cpg")
        if home_cs is not None:
            parts.append(f"{home_name} CS rate {home_cs:.0%} at home")
        if away_cs is not None:
            parts.append(f"{away_name} CS rate {away_cs:.0%} away")
        if home_cpg is not None:
            parts.append(f"{home_name} concedes {home_cpg:.1f}/game at home")
        if away_cpg is not None:
            parts.append(f"{away_name} concedes {away_cpg:.1f}/game away")

    if "btts" in market_code or "gg" in market_code:
        # BTTS evidence: scored_in_pct
        h_scored = sf_dp.get("home_scored_in_pct") or sf_dp.get("home_home_scored_in_pct")
        a_scored = sf_dp.get("away_scored_in_pct") or sf_dp.get("away_away_scored_in_pct")
        h2h_btts = h2h_dp.get("btts_rate_h2h")
        if h_scored is not None:
            parts.append(f"{home_name} scored in {h_scored:.0%} of home games")
        if a_scored is not None:
            parts.append(f"{away_name} scored in {a_scored:.0%} of away games")
        if h2h_btts is not None:
            parts.append(f"H2H BTTS rate {h2h_btts:.0%}")

    return ", ".join(parts) + "." if parts else rec.rationale[:160]


def _clean_sheet_evidence(
    rec: MarketRecommendation,
    factors: list[FactorResult],
    home_name: str,
    away_name: str,
) -> str:
    def_dp = _dp(factors, "defensive_record")
    af_dp = _dp(factors, "away_form")
    sf_dp = _dp(factors, "scoring_frequency")

    is_home_wtn = "home" in rec.pick.lower()
    keeper = home_name if is_home_wtn else away_name
    scorer = away_name if is_home_wtn else home_name

    parts: list[str] = []

    if is_home_wtn:
        cs_rate = def_dp.get("home_cs_rate_home") or def_dp.get("home_cs_rate")
        cpg = def_dp.get("home_cpg_home") or def_dp.get("home_cpg")
        away_blank = af_dp.get("failed_to_score")
        away_played = af_dp.get("away_played")
        away_scored = sf_dp.get("away_away_scored_in_pct") or sf_dp.get("away_scored_in_pct")

        if cs_rate is not None:
            parts.append(f"{keeper} clean sheet rate {cs_rate:.0%} at home")
        if cpg is not None:
            parts.append(f"conceding {cpg:.1f}/game")
        if away_blank is not None and away_played and away_played > 0:
            parts.append(f"{scorer} failed to score in {away_blank}/{away_played} away trips")
        elif away_scored is not None:
            parts.append(f"{scorer} scores in {away_scored:.0%} of away games")
    else:
        cs_rate = def_dp.get("away_cs_rate_away") or def_dp.get("away_cs_rate")
        cpg = def_dp.get("away_cpg_away") or def_dp.get("away_cpg")
        home_scored = sf_dp.get("home_home_scored_in_pct") or sf_dp.get("home_scored_in_pct")

        if cs_rate is not None:
            parts.append(f"{keeper} clean sheet rate {cs_rate:.0%} away")
        if cpg is not None:
            parts.append(f"conceding {cpg:.1f}/game")
        if home_scored is not None:
            parts.append(f"{scorer} scores in {home_scored:.0%} of home games")

    return ", ".join(parts) + "." if parts else rec.rationale[:160]


def _dominance_evidence(
    rec: MarketRecommendation,
    factors: list[FactorResult],
    home_name: str,
    away_name: str,
    market_code: str,
) -> str:
    pos_dp = _dp(factors, "league_position")
    hf_dp = _dp(factors, "home_form")
    rf_dp = _dp(factors, "recent_form")
    mom_dp = _dp(factors, "momentum")
    ss = rec.supporting_stats or {}

    parts: list[str] = []

    h_pos = pos_dp.get("home_position")
    a_pos = pos_dp.get("away_position")
    pts_gap = pos_dp.get("points_gap")
    if h_pos and a_pos and pts_gap is not None:
        parts.append(
            f"table gap {pts_gap} pts ({h_pos}{'st' if h_pos==1 else 'nd' if h_pos==2 else 'rd' if h_pos==3 else 'th'}"
            f" vs {a_pos}{'st' if a_pos==1 else 'nd' if a_pos==2 else 'rd' if a_pos==3 else 'th'})"
        )

    h_rf = rf_dp.get("home") or {}
    h_ppg = h_rf.get("ppg")
    if h_ppg is not None:
        parts.append(f"{home_name} at {h_ppg:.1f} PPG")

    h_win_rate = hf_dp.get("win_rate")
    if h_win_rate is not None:
        parts.append(f"{h_win_rate:.0%} home win rate")

    h_mom = mom_dp.get("home") or {}
    if h_mom.get("trend_direction") == "improving":
        parts.append("improving momentum")

    # Handicap-specific
    if "handicap" in market_code and ss.get("handicap_line") is not None:
        parts.append(f"handicap line {ss['handicap_line']:+.1f}")

    return ", ".join(parts) + "." if parts else rec.rationale[:160]


def _timing_evidence(
    rec: MarketRecommendation,
    factors: list[FactorResult],
    home_name: str,
    away_name: str,
    market_code: str,
) -> str:
    gt_dp = _dp(factors, "goal_timing")
    sf_dp = _dp(factors, "scoring_frequency")
    ss = rec.supporting_stats or {}

    parts: list[str] = []

    h1_rate = gt_dp.get("home_first_half_rate") or gt_dp.get("h1_rate")
    h2_rate = gt_dp.get("home_second_half_rate") or gt_dp.get("h2_rate")
    if "first" in market_code and h1_rate is not None:
        parts.append(f"{int(h1_rate*100)}% of goals scored in first half historically")
    elif "second" in market_code and h2_rate is not None:
        parts.append(f"{int(h2_rate*100)}% of goals scored in second half historically")

    exp = ss.get("expected_total")
    if exp is not None:
        parts.append(f"expected total goals ≈ {exp:.1f}")

    return ", ".join(parts) + "." if parts else rec.rationale[:160]


# ── Ranking context sentence ─────────────────────────────────────────────────

def _rank_context_sentence(
    rec: MarketRecommendation,
    rank: int,
    total: int,
    same_thesis_above: list[int],
    same_thesis_below: list[int],
    home_name: str,
    away_name: str,
) -> str:
    """Generate 1 sentence placing this pick in context of the ranking."""

    if same_thesis_above:
        # This pick shares the same statistical basis as a higher-ranked pick
        ref = _rank_label(same_thesis_above[0])
        return (
            f"Backed by the same statistical data as {ref} — "
            f"the decision between them is purely an odds comparison."
        )

    if rank == 1:
        return "The strongest statistical signal for this fixture."

    if rank == 2:
        return (
            "Second-strongest signal — different statistical basis from ①, "
            "so this represents an independent pick rather than the same thesis priced differently."
        )

    if rank <= 4:
        prev_label = _rank_label(rank - 1)
        return (
            f"Ranks below {prev_label} because it requires an additional condition "
            f"(higher bar) or draws on fewer independent factors."
        )

    quartile = rank / total
    if quartile < 0.5:
        return "Moderate statistical backing — viable pick but more data uncertainty than the top options."
    elif quartile < 0.75:
        return "Lower statistical backing — worth checking if bookmaker odds offer meaningful value over implied odds."
    else:
        return (
            "Weak statistical signal — model confidence is low. "
            "Only worth considering if bookmaker odds are significantly above model implied odds."
        )


# ═══════════════════════════════════════════════════════════════
#  Shared thesis detection
# ═══════════════════════════════════════════════════════════════

def _shared_thesis_key(rec: MarketRecommendation) -> str:
    """Return a key that groups picks sharing the same statistical basis."""
    code = rec.market_code
    pick = rec.pick.lower()

    # Outcome direction
    if code in ("1x2", "double-chance", "draw-no-bet"):
        if any(k in pick for k in ("home", "1 (", "1x", "12")):
            return "thesis:home_win"
        if any(k in pick for k in ("away", "2 (", "x2")):
            return "thesis:away_win"
        if any(k in pick for k in ("draw", "x (")):
            return "thesis:draw"

    # Goals direction (group same line and direction)
    m = re.match(r"over-under-([\d.]+)", code)
    if m:
        line = m.group(1)
        direction = "over" if "over" in pick else "under"
        return f"thesis:goals_{direction}_{line}"

    # BTTS/GG grouped per direction
    if code == "gg-ng":
        return "thesis:btts_yes" if "gg" in pick or "yes" in pick else "thesis:btts_no"

    # Win to nil + team under 0.5 share the same clean sheet thesis
    if code in ("win-to-nil", "team-over-under-0.5"):
        side = "home" if "home" in pick or "1" in pick else "away"
        return f"thesis:clean_sheet_{side}"

    # Handicap — group by side
    if "handicap" in code:
        side = "home" if "home" in pick.lower() or "team 1" in pick.lower() else "away"
        return f"thesis:handicap_{side}"

    return f"unique:{code}:{pick}"


# ═══════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════

def rank_and_narrate(
    recommendations: list[MarketRecommendation],
    factors: list[FactorResult],
    home_name: str,
    away_name: str,
) -> list[AssessedMarket]:
    """Transform a flat list of MarketRecommendation into a ranked,
    narrative-annotated list of AssessedMarket objects.

    Args:
        recommendations: All market picks from ``evaluate_all_markets()``.
        factors: Factor results from the analysis engine.
        home_name: Home team display name.
        away_name: Away team display name.

    Returns:
        Ranked list of ``AssessedMarket``, highest statistical backing first.
        Within the same backing score tier, HIGH confidence comes before MEDIUM
        before LOW.
    """
    if not recommendations:
        return []

    # ── 1. Classify themes and compute backing scores ─────────────────────
    scored: list[tuple[float, str, MarketRecommendation]] = []  # (score, theme, rec)
    for rec in recommendations:
        theme = _classify_theme(rec.market_code)
        bs = _compute_backing_score(rec, factors, theme)
        scored.append((bs, theme, rec))

    # ── 2. Sort: backing score descending, with confidence as tiebreaker ──
    _conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    scored.sort(key=lambda t: (-t[0], _conf_order.get(t[2].confidence, 3)))

    # ── 3. Build thesis map: thesis_key → list of (rank, rec) ─────────────
    # First pass: assign ranks and thesis keys
    thesis_to_ranks: dict[str, list[int]] = {}
    ranked_items: list[tuple[int, float, str, MarketRecommendation]] = []
    for rank_0, (bs, theme, rec) in enumerate(scored):
        rank = rank_0 + 1
        tk = _shared_thesis_key(rec)
        thesis_to_ranks.setdefault(tk, []).append(rank)
        ranked_items.append((rank, bs, theme, rec))

    total = len(ranked_items)

    # ── 4. Generate narratives ────────────────────────────────────────────
    assessed: list[AssessedMarket] = []
    for rank, bs, theme, rec in ranked_items:
        tk = _shared_thesis_key(rec)
        all_thesis_ranks = thesis_to_ranks.get(tk, [rank])

        same_above = [r for r in all_thesis_ranks if r < rank]
        same_below = [r for r in all_thesis_ranks if r > rank]

        # Find prev/next recs for neighbour context
        idx = rank - 1
        prev_rec = scored[idx - 1][2] if idx > 0 else None
        next_rec = scored[idx + 1][2] if idx < total - 1 else None

        narrative = _build_narrative(
            rec=rec,
            rank=rank,
            total=total,
            theme=theme,
            factors=factors,
            home_name=home_name,
            away_name=away_name,
            same_thesis_above=same_above,
            same_thesis_below=same_below,
            prev_rec=prev_rec,
            next_rec=next_rec,
        )

        assessed.append(AssessedMarket(
            rank=rank,
            pick=rec.pick,
            market_name=rec.market_name,
            market_code=rec.market_code,
            probability=rec.probability,
            implied_odds=rec.implied_odds,
            confidence=rec.confidence,
            backing_score=round(bs, 4),
            narrative=narrative,
            theme=theme,
            shared_thesis_refs=all_thesis_ranks,
        ))

    return assessed
