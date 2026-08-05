"""Tests for team-name canonicalisation.

Sources spell the same club differently — "Man United" from the
football-data.co.uk CSVs, "Manchester United FC" from football-data.org. Without
canonicalisation each spelling became its own ``teams`` row, splitting a club's
history in half and starving every form/venue/h2h factor of data.
"""

import pytest

from src.fetchers.team_names import ALIASES, canonical_key, normalize_team_name


class TestNormalize:
    def test_lowercases_and_trims(self):
        assert normalize_team_name("  Arsenal  ") == "arsenal"

    def test_strips_legal_suffixes(self):
        assert normalize_team_name("Arsenal FC") == "arsenal"
        assert normalize_team_name("Ajax Amsterdam") == "ajax amsterdam"
        assert normalize_team_name("AC Milan") == "milan"

    def test_folds_accents(self):
        assert normalize_team_name("Atlético Madrid") == normalize_team_name(
            "Atletico Madrid"
        )
        assert normalize_team_name("Bayern München") == normalize_team_name(
            "Bayern Munchen"
        )

    def test_strips_leading_ordinal(self):
        """German clubs appear as both "1. FC Köln" and "FC Koln"."""
        assert normalize_team_name("1. FC Köln") == normalize_team_name("FC Koln")

    def test_strips_founding_year(self):
        assert normalize_team_name("Schalke 04") == normalize_team_name("Schalke")
        assert normalize_team_name("Hannover 96") == normalize_team_name("Hannover")

    def test_strips_punctuation(self):
        assert normalize_team_name("Brighton & Hove Albion") == normalize_team_name(
            "Brighton and Hove Albion"
        ) or normalize_team_name("Brighton & Hove Albion") == "brighton hove albion"

    def test_never_collapses_to_empty(self):
        """A name made entirely of noise tokens must still yield something."""
        for name in ("FC", "AC", "the", "1."):
            assert normalize_team_name(name), f"{name!r} collapsed to empty"


class TestCanonicalKey:
    @pytest.mark.parametrize(
        "variants",
        [
            ("Man United", "Manchester United", "Manchester United FC"),
            ("Man City", "Manchester City", "Manchester City FC"),
            ("Spurs", "Tottenham", "Tottenham Hotspur FC"),
            ("Wolves", "Wolverhampton Wanderers FC"),
            ("Nott'm Forest", "Nottingham Forest FC"),
            ("West Ham", "West Ham United FC"),
            ("Ath Madrid", "Atlético Madrid", "Club Atlético de Madrid"),
            ("Ath Bilbao", "Athletic Club"),
            ("Sociedad", "Real Sociedad de Fútbol"),
            ("Dortmund", "Borussia Dortmund"),
            ("Leverkusen", "Bayer 04 Leverkusen"),
            ("Inter", "Inter Milan", "FC Internazionale Milano"),
            ("Paris SG", "Paris Saint-Germain FC"),
            ("PSV Eindhoven", "PSV"),
        ],
    )
    def test_variants_share_one_key(self, variants):
        keys = {canonical_key(v) for v in variants}
        assert len(keys) == 1, f"{variants} split across keys {keys}"

    def test_distinct_clubs_keep_distinct_keys(self):
        """Canonicalisation must not over-merge genuinely different clubs."""
        distinct = [
            "Manchester United",
            "Manchester City",
            "Real Madrid",
            "Atlético Madrid",
            "Inter",
            "AC Milan",
            "Nottingham Forest",
            "Norwich City",
            "Sheffield United",
            "Sheffield Wednesday",
        ]
        keys = [canonical_key(n) for n in distinct]
        assert len(set(keys)) == len(distinct), f"clubs collided: {keys}"

    def test_is_idempotent(self):
        """Feeding a key back through must not change it."""
        for name in ("Man United", "Atlético Madrid", "1. FC Köln", "Inter"):
            key = canonical_key(name)
            assert canonical_key(key) == key, f"{name!r} not idempotent"

    def test_alias_table_is_normalised_on_both_sides(self):
        """Import-time normalisation means lookups never need to pre-clean."""
        for source, target in ALIASES.items():
            assert source == normalize_team_name(source)
            assert target == normalize_team_name(target)

    def test_alias_chains_terminate(self):
        """No alias may loop; canonical_key must resolve every entry."""
        for source in ALIASES:
            assert canonical_key(source)
