"""
Footy Predict — Team Name Canonicalisation

Different sources spell the same club differently:

    football-data.co.uk (CSV)   →  "Man United", "Nott'm Forest", "Ath Madrid"
    football-data.org (FDO)     →  "Manchester United FC", "Nottingham Forest FC"
    API-Football                →  "Manchester United"

Without normalisation each spelling becomes a separate ``teams`` row, which
splits a club's match history across two identities and silently destroys
form, head-to-head and venue factors.

This module reduces any spelling to a stable ``canonical_key`` used by
``Database.upsert_team`` for identity resolution.

Resolution order
----------------
1. ``ALIASES`` — curated map for spellings that normalisation cannot reach
   ("Ath Madrid" shares no prefix with "Club Atlético de Madrid").
2. ``normalize_team_name`` — mechanical: strip accents, punctuation, corporate
   suffixes/prefixes and founding years.

Curated aliases are keyed by the *normalised* form so a new source spelling
usually resolves without touching the table.
"""

from __future__ import annotations

import re
import unicodedata

# Corporate/legal tokens that carry no identifying information. Stripped from
# both ends of the name. Order is irrelevant — matching is token-based.
_NOISE_TOKENS: frozenset[str] = frozenset({
    "fc", "afc", "cf", "sc", "ac", "bc", "us", "ss", "ssc", "sv", "sk",
    "kv", "kvc", "rc", "rcd", "cd", "gd", "cs", "as", "aj", "sd", "ud",
    "club", "calcio", "futbol", "futebol", "fussball", "voetbal",
    "de", "da", "do", "des", "der", "den", "the",
})

# Trailing founding years ("Bologna FC 1909", "Parma Calcio 1913").
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20[0-2]\d)\b")

# "1. FC Köln", "1899 Hoffenheim" — leading ordinal noise.
_LEADING_ORDINAL_RE = re.compile(r"^\d+\s*\.?\s*")

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_DIGITS_RE = re.compile(r"^\d+$")


def _strip_accents(text: str) -> str:
    """Fold accented characters to ASCII ("Mönchengladbach" → "Monchengladbach")."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_team_name(name: str) -> str:
    """Reduce a club name to a comparable token string.

    Lowercases, folds accents, drops punctuation, founding years and
    corporate tokens, then rejoins the remaining tokens with single spaces.

    Bare numeric tokens are dropped too: German clubs abbreviate their founding
    year to two digits ("Schalke 04", "Bayer 04 Leverkusen", "Mainz 05"), which
    the four-digit year pattern cannot reach.

    Examples::

        >>> normalize_team_name("Manchester United FC")
        'manchester united'
        >>> normalize_team_name("Borussia Mönchengladbach")
        'borussia monchengladbach'
        >>> normalize_team_name("Bologna FC 1909")
        'bologna'
        >>> normalize_team_name("Bayer 04 Leverkusen")
        'bayer leverkusen'

    Args:
        name: Raw club name from any source.

    Returns:
        Normalised token string. Never empty for a non-empty input.
    """
    if not name:
        return ""

    text = _strip_accents(name).lower()
    text = _LEADING_ORDINAL_RE.sub("", text)
    text = _YEAR_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()

    tokens = [
        t for t in text.split(" ")
        if t and t not in _NOISE_TOKENS and not _DIGITS_RE.match(t)
    ]

    if tokens:
        return " ".join(tokens)

    # A name made entirely of noise ("FC", "1.") must not collapse to empty —
    # an empty key would merge every such club into one identity. Fall back to
    # progressively rawer forms so distinct inputs keep distinct keys.
    fallback = _WS_RE.sub(
        " ", _PUNCT_RE.sub(" ", _strip_accents(name).lower())
    ).strip()
    return fallback or _WS_RE.sub(" ", name.lower()).strip()


# ── Curated aliases ──────────────────────────────────────────────────────
#
# Keyed by NORMALISED spelling → canonical NORMALISED form.
# Only needed where normalisation cannot bridge the gap (abbreviations,
# vernacular short forms, historic names).

_RAW_ALIASES: dict[str, str] = {
    # ── Premier League: CSV short form → FDO long form ──
    "man united": "manchester united",
    "man utd": "manchester united",
    "man city": "manchester city",
    "nott m forest": "nottingham forest",
    "nottm forest": "nottingham forest",
    "forest": "nottingham forest",
    "wolves": "wolverhampton wanderers",
    "brighton": "brighton hove albion",
    "brighton hove albion": "brighton hove albion",
    "west brom": "west bromwich albion",
    "west ham": "west ham united",
    "tottenham": "tottenham hotspur",
    "spurs": "tottenham hotspur",
    "newcastle": "newcastle united",
    "leeds": "leeds united",
    "leicester": "leicester city",
    "norwich": "norwich city",
    "watford": "watford",
    "bournemouth": "bournemouth",
    "huddersfield": "huddersfield town",
    "cardiff": "cardiff city",
    "hull": "hull city",
    "ipswich": "ipswich town",
    "luton": "luton town",
    "sheffield united": "sheffield united",
    "sheffield weds": "sheffield wednesday",
    "coventry": "coventry city",
    "stoke": "stoke city",
    "swansea": "swansea city",
    "qpr": "queens park rangers",

    # ── La Liga: CSV short form → FDO long form ──
    "ath madrid": "atletico madrid",
    "atletico madrid": "atletico madrid",
    "atletico": "atletico madrid",
    "ath bilbao": "athletic",
    "athletic bilbao": "athletic",
    "athletic": "athletic",
    "espanol": "espanyol barcelona",
    "espanyol": "espanyol barcelona",
    "espanyol barcelona": "espanyol barcelona",
    "sociedad": "real sociedad futbol",
    "real sociedad": "real sociedad futbol",
    "betis": "real betis balompie",
    "real betis": "real betis balompie",
    "celta": "celta vigo",
    "celta vigo": "celta vigo",
    "vallecano": "rayo vallecano madrid",
    "rayo vallecano": "rayo vallecano madrid",
    "alaves": "deportivo alaves",
    "barcelona": "barcelona",
    "real madrid": "real madrid",
    "sevilla": "sevilla",
    "valencia": "valencia",
    "villarreal": "villarreal",
    "getafe": "getafe",
    "girona": "girona",
    "levante": "levante",
    "osasuna": "osasuna",
    "mallorca": "mallorca",
    "elche": "elche",
    "valladolid": "valladolid",
    "oviedo": "real oviedo",
    "la coruna": "deportivo la coruna",
    "deportivo": "deportivo la coruna",
    "santander": "racing santander",
    "racing santander": "racing santander",
    "malaga": "malaga",
    "leganes": "leganes",
    "eibar": "eibar",
    "huesca": "huesca",

    # ── Bundesliga ──
    "bayern munich": "bayern munchen",
    "bayern": "bayern munchen",
    "dortmund": "borussia dortmund",
    "m gladbach": "borussia monchengladbach",
    "mgladbach": "borussia monchengladbach",
    "leverkusen": "bayer leverkusen",
    "ein frankfurt": "eintracht frankfurt",
    "hoffenheim": "tsg hoffenheim",
    "stuttgart": "vfb stuttgart",
    "wolfsburg": "vfl wolfsburg",
    "werder bremen": "werder bremen",
    "union berlin": "union berlin",
    "fc koln": "koln",
    "koln": "koln",
    "mainz": "mainz",
    "augsburg": "augsburg",
    "freiburg": "freiburg",
    "heidenheim": "heidenheim",
    "st pauli": "st pauli",
    "hamburg": "hamburger",
    "schalke": "schalke",

    # ── Serie A ──
    "inter": "internazionale milano",
    "inter milan": "internazionale milano",
    "internazionale": "internazionale milano",
    "milan": "milan",
    "juventus": "juventus",
    "napoli": "napoli",
    "roma": "roma",
    "lazio": "lazio",
    "fiorentina": "fiorentina",
    "atalanta": "atalanta",
    "torino": "torino",
    "bologna": "bologna",
    "udinese": "udinese",
    "sassuolo": "sassuolo",
    "verona": "hellas verona",
    "hellas verona": "hellas verona",

    # ── Ligue 1 ──
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    "paris saint germain": "paris saint germain",
    "marseille": "olympique marseille",
    "lyon": "olympique lyonnais",
    "lille": "lille osc",
    "nice": "ogc nice",
    "rennes": "stade rennais",
    "lens": "racing lens",
    "monaco": "monaco",
    "brest": "stade brestois",
    "strasbourg": "strasbourg alsace",
    "toulouse": "toulouse",
    "auxerre": "auxerre",
    "angers": "angers sco",
    "le havre": "le havre",

    # ── Eredivisie ──
    "ajax": "ajax",
    "psv eindhoven": "psv",
    "feyenoord": "feyenoord rotterdam",
    "twente": "twente",
    "utrecht": "utrecht",
    "az alkmaar": "az",
    "groningen": "groningen",
    "heerenveen": "heerenveen",
    "willem ii": "willem ii tilburg",
    "sparta rotterdam": "sparta rotterdam",
    "excelsior": "excelsior",
    "cambuur": "cambuur leeuwarden",
    "zwolle": "pec zwolle",
    "telstar": "telstar",

    # ── Scottish Premiership ──
    "hearts": "heart of midlothian",
    "heart of midlothian": "heart of midlothian",
    "hibs": "hibernian",
    "st johnstone": "st johnstone",
    "st mirren": "st mirren",
    "dundee utd": "dundee united",
    "ayr utd": "ayr united",
    "partick": "partick thistle",
}

# Normalise both sides once at import so lookups are cheap and the table can
# be written in readable prose above.
ALIASES: dict[str, str] = {
    normalize_team_name(k): normalize_team_name(v)
    for k, v in _RAW_ALIASES.items()
}


def canonical_key(name: str) -> str:
    """Return the stable identity key for a club name.

    Applies mechanical normalisation, then resolves through the curated
    alias table. Alias resolution is applied repeatedly (bounded) so chained
    entries collapse to a single canonical form.

    Examples::

        >>> canonical_key("Man United") == canonical_key("Manchester United FC")
        True
        >>> canonical_key("Ath Madrid") == canonical_key("Club Atlético de Madrid")
        True

    Args:
        name: Raw club name from any source.

    Returns:
        Canonical key suitable for equality comparison and DB indexing.
    """
    key = normalize_team_name(name)

    # Follow alias chains, with a hard bound against accidental cycles.
    for _ in range(4):
        mapped = ALIASES.get(key)
        if mapped is None or mapped == key:
            break
        key = mapped

    return key
