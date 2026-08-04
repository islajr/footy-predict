"""Tests for CSV history loading windows.

Regression cover for the ``--seasons`` flag being silently dropped on the
all-leagues path, which caused ``footy load-history --seasons 1`` to reload
every season back to 2018.
"""

from unittest.mock import patch

from src.config import LEAGUES
from src.fetchers.csv_loader import CSVLoader


def _record_windows(**kwargs) -> dict[str, tuple[int, int]]:
    """Run load_all_leagues_history, capturing the per-league season window."""
    windows: dict[str, tuple[int, int]] = {}

    def _capture(slug, start_season, end_season):
        windows[slug] = (start_season, end_season)
        return 0

    with patch.object(CSVLoader, "load_all_history", side_effect=_capture):
        CSVLoader(None).load_all_leagues_history(**kwargs)

    return windows


def test_seasons_limits_window_to_most_recent():
    """seasons=1 must load exactly one season, not everything since 2018."""
    windows = _record_windows(seasons=1)

    assert windows, "expected at least one CSV-capable league"
    for slug, (start, end) in windows.items():
        assert start == end, f"{slug}: seasons=1 should span a single season"


def test_seasons_resolves_per_league_current_season():
    """Each league's window is anchored to its own current season.

    Calendar-year leagues (Eliteserien) are offset from European Aug–May
    leagues, so a shared absolute start year would load the wrong data.
    """
    windows = _record_windows(seasons=2)

    for slug, (start, end) in windows.items():
        expected_end = LEAGUES[slug].get_current_season()
        assert end == expected_end, f"{slug}: end should be its own current season"
        assert start == expected_end - 1, f"{slug}: seasons=2 should span two seasons"


def test_omitting_seasons_preserves_full_history_default():
    """Back-compat: no seasons arg still loads from 2018."""
    windows = _record_windows()

    for slug, (start, _end) in windows.items():
        assert start == 2018, f"{slug}: default start season changed"


def test_explicit_start_season_still_honoured():
    """start_season remains usable when seasons is not supplied."""
    windows = _record_windows(start_season=2022)

    for slug, (start, _end) in windows.items():
        assert start == 2022, f"{slug}: explicit start_season ignored"


def test_seasons_takes_precedence_over_start_season():
    """seasons is the more specific intent and wins over start_season."""
    windows = _record_windows(start_season=2018, seasons=1)

    for slug, (start, end) in windows.items():
        assert start == end, f"{slug}: seasons should override start_season"
