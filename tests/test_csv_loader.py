"""Tests for CSV history loading windows.

Regression cover for the ``--seasons`` flag being silently dropped on the
all-leagues path, which caused ``footy load-history --seasons 1`` to reload
every season back to 2018.
"""

from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from src.config import LEAGUES
from src.fetchers.csv_loader import CSVLoader


@contextmanager
def _clock(year: int, month: int, day: int):
    """Pin the loader's notion of "now".

    The season-anchoring heuristic reads the wall clock, so any test that
    exercises it has to fix the date or it passes only some months of the year.
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(year, month, day)

    with patch("src.fetchers.csv_loader.datetime", _Frozen):
        yield


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
    with _clock(2026, 11, 15):
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


def test_seasons_skips_a_season_that_has_not_kicked_off():
    """Pre-season, "last 1 season" must mean the last season actually played.

    In early August get_current_season() already points at the campaign about
    to start, which has no results. Anchoring there loaded an empty CSV and
    left Serie A, Ligue 1 and Eredivisie with no history at all.

    August is only pre-season for the European Aug–May leagues. Eliteserien
    runs on the calendar year and is four months into its own season by then,
    so the check has to be per-format, not per-date.
    """
    with _clock(2026, 8, 5):
        windows = _record_windows(seasons=1)

    european = [s for s in windows if LEAGUES[s].season_format != "calendar"]
    calendar = [s for s in windows if LEAGUES[s].season_format == "calendar"]
    assert european and calendar, "fixture needs both season formats to be meaningful"

    for slug in european:
        start, end = windows[slug]
        assert end == LEAGUES[slug].get_current_season() - 1, (
            f"{slug}: anchored to a season with no completed matches"
        )
        assert start == end

    for slug in calendar:
        _start, end = windows[slug]
        assert end == LEAGUES[slug].get_current_season(), (
            f"{slug}: mid-season calendar league stepped back needlessly"
        )


def test_seasons_uses_current_season_once_it_is_under_way():
    """Mid-season, the current campaign is the right anchor."""
    with _clock(2026, 11, 15):
        windows = _record_windows(seasons=1)

    for slug, (_start, end) in windows.items():
        assert end == LEAGUES[slug].get_current_season(), f"{slug}: wrong anchor"


def test_explicit_end_season_overrides_the_kickoff_check():
    """An explicit window is the caller's call, not the heuristic's."""
    with _clock(2026, 8, 5):
        windows = _record_windows(seasons=1, end_season=2026)

    for slug, (_start, end) in windows.items():
        assert end == 2026, f"{slug}: explicit end_season ignored"
