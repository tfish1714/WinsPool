"""scripts/scrape_quarter_scores.py -- Scrape Q1/Q2/Q3/Q4 scores from jt-sw.com.

Crawls week index pages to find game document URLs, then fetches each game
page and parses the scoring table. Output is written to rawdata/quarter_scores.csv.

Already-scraped games are cached (skipped on re-run). Rate-limited with ~0.5s
sleep between requests to be polite to the server.

Usage:
    python scripts/scrape_quarter_scores.py                       # 2006-current
    python scripts/scrape_quarter_scores.py --seasons 2020 2025   # range
    python scripts/scrape_quarter_scores.py --season 2025         # single season
    python scripts/scrape_quarter_scores.py --force               # re-scrape all
    python scripts/scrape_quarter_scores.py --dry-run             # count only
"""

import argparse
import csv
import logging
import pathlib
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"
OUTPUT_CSV = RAWDATA_DIR / "quarter_scores.csv"
GAMES_CSV = RAWDATA_DIR / "schedules" / "games.csv"


def _normalize_nflverse_team(abbr: str) -> str:
    """Normalize nflverse historical team codes to current codes (matches TEAM_CODE_MAP output)."""
    _map = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "WSH": "WAS", "JAC": "JAX"}
    abbr = str(abbr).upper().strip()
    return _map.get(abbr, abbr)


def _load_expected_games(seasons: list[int]) -> dict[tuple, set[str]]:
    """Load expected home teams per (season, week) from nflverse games.csv.

    Returns {(season, week): {home_team, ...}} for completed REG games.
    Team codes are normalized to match what the scraper writes (e.g. OAK->LV).
    """
    if not GAMES_CSV.exists():
        return {}
    df = pd.read_csv(GAMES_CSV, low_memory=False)
    df = df[
        (df["game_type"] == "REG")
        & (df["season"].isin(seasons))
        & df["home_score"].notna()
    ]
    result: dict[tuple, set[str]] = {}
    for _, row in df.iterrows():
        key = (int(row["season"]), int(row["week"]))
        result.setdefault(key, set()).add(_normalize_nflverse_team(row["home_team"]))
    return result

BASE = "https://www.jt-sw.com"
WEEK_INDEX_URL = (
    BASE
    + "/football/boxes/index.nsf/By/Week"
    "?OpenDocument&Season={year}&WeekType=Reg&Week={week:02d}"
)

# Map jt-sw.com 2-3 letter team codes to nflverse abbreviations
TEAM_CODE_MAP = {
    "ari": "ARI", "arz": "ARI", "atl": "ATL", "bal": "BAL", "buf": "BUF",
    "car": "CAR", "chi": "CHI", "cin": "CIN", "cle": "CLE",
    "dal": "DAL", "den": "DEN", "det": "DET", "gb":  "GB",
    "hou": "HOU", "ind": "IND", "jac": "JAX", "jax": "JAX",
    "kc":  "KC",  "lac": "LAC", "lar": "LA",  "lam": "LA",  "lv":  "LV",
    "mia": "MIA", "min": "MIN", "ne":  "NE",  "no":  "NO",
    "nyg": "NYG", "nyj": "NYJ", "oak": "LV",  "phi": "PHI",
    "pit": "PIT", "sd":  "LAC", "sea": "SEA", "sf":  "SF",
    "stl": "LA",  "tb":  "TB",  "ten": "TEN", "was": "WAS",
    "wsh": "WAS",
}

CSV_FIELDS = [
    "season", "week",
    "home_team", "away_team",
    "home_q1", "home_q2", "home_q3", "home_q4", "home_ot",
    "away_q1", "away_q2", "away_q3", "away_q4", "away_ot",
    "home_score", "away_score",
]

import datetime as _dt
_today = _dt.date.today()
CURRENT_SEASON = _today.year if _today.month >= 9 else _today.year - 1


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, retries: int = 3, delay: float = 1.0) -> Optional[str]:
    """Fetch a URL and return the HTML text, or None on failure."""
    headers = {"User-Agent": "Mozilla/5.0 (WinsPool/1.0; research project)"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                # jt-sw.com pages are latin-1 encoded
                return raw.decode("latin-1", errors="replace")
        except Exception as e:
            if attempt < retries - 1:
                logger.debug("Retry %d for %s: %s", attempt + 1, url, e)
                time.sleep(delay * (attempt + 1))
            else:
                logger.warning("Failed to fetch %s: %s", url, e)
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _extract_game_urls(html: str) -> list[str]:
    """Extract game document URLs from a week index page."""
    # Links look like: href="/football/boxes/index.nsf/<hash1>/<hash2>?OpenDocument"
    pattern = re.compile(
        r'href="(/football/boxes/index\.nsf/[0-9a-f]+/[0-9a-f]+\?OpenDocument)"',
        re.IGNORECASE,
    )
    return list(dict.fromkeys(pattern.findall(html)))  # dedupe, preserve order


def _parse_team_code(href: str) -> Optional[str]:
    """Extract jt-sw team code from a Teams href like '/football/pro/results.nsf/Teams/2024-kc'."""
    m = re.search(r"/Teams/\d{4}-(\w+)", href, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return None


def _parse_game_page(html: str) -> Optional[dict]:
    """
    Parse a jt-sw.com game box score page.

    Table structure (confirmed from live pages):
        <tr><td><A HREF="...Teams/YYYY-XX">Name</A></td>
            <td><div align="right">Q1</div></td>
            <td><div align="right">Q2</div></td>
            <td><div align="right">Q3</div></td>
            <td><div align="right">Q4</div></td>
            <td><div align="right">OT_or_empty</div></td>
            <td>-- TOTAL</td></tr>

    Row 1 = HOME team, Row 2 = AWAY team.

    Returns a dict with keys matching CSV_FIELDS, or None if parsing fails.
    """
    # Find the first table that contains Teams links
    table_pat = re.compile(
        r'<table[^>]*>(.*?)</table>',
        re.IGNORECASE | re.DOTALL,
    )
    score_table = None
    for m in table_pat.finditer(html):
        if "results.nsf/Teams/" in m.group(1):
            score_table = m.group(1)
            break

    if score_table is None:
        logger.debug("Could not find scoring table")
        return None

    # Parse each <tr> in the table
    row_pat = re.compile(r'<tr[^>]*>(.*?)</tr>', re.IGNORECASE | re.DOTALL)
    results = []

    for tr_m in row_pat.finditer(score_table):
        tr = tr_m.group(1)
        # Must contain a Teams link
        team_m = re.search(
            r'href="[^"]*results\.nsf/Teams/\d{4}-(\w+)"',
            tr, re.IGNORECASE,
        )
        if not team_m:
            continue

        team_code = team_m.group(1).lower()
        abbr = TEAM_CODE_MAP.get(team_code, team_code.upper())

        # Extract scores from <div align="right">NUMBER_OR_EMPTY</div>
        div_vals = re.findall(
            r'<div align="right">\s*(\d*)\s*</div>',
            tr, re.IGNORECASE,
        )
        # Each div is one quarter (Q1, Q2, Q3, Q4, OT optional — empty string if no OT)
        # Total is in the last <td> as "-- NUMBER"
        total_m = re.search(r'--\s*(\d+)', tr)
        if not total_m or len(div_vals) < 4:
            logger.debug("Could not parse scores for team %s in row: %s", abbr, repr(tr[:200]))
            return None

        q1 = int(div_vals[0]) if div_vals[0] else 0
        q2 = int(div_vals[1]) if len(div_vals) > 1 and div_vals[1] else 0
        q3 = int(div_vals[2]) if len(div_vals) > 2 and div_vals[2] else 0
        q4 = int(div_vals[3]) if len(div_vals) > 3 and div_vals[3] else 0
        ot = int(div_vals[4]) if len(div_vals) > 4 and div_vals[4] else 0
        total = int(total_m.group(1))

        results.append({
            "code": abbr,
            "q1": q1, "q2": q2, "q3": q3, "q4": q4, "ot": ot,
            "total": total,
        })

    if len(results) < 2:
        logger.debug("Only %d team row(s) found (need 2)", len(results))
        return None

    # Row order: AWAY first, HOME second (confirmed: 2024 W1 BAL@KC shows BAL row 1, KC row 2)
    away, home = results[0], results[1]

    return {
        "home_team": home["code"],
        "away_team": away["code"],
        "home_q1": home["q1"], "home_q2": home["q2"],
        "home_q3": home["q3"], "home_q4": home["q4"], "home_ot": home["ot"],
        "away_q1": away["q1"], "away_q2": away["q2"],
        "away_q3": away["q3"], "away_q4": away["q4"], "away_ot": away["ot"],
        "home_score": home["total"],
        "away_score": away["total"],
    }


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_existing(csv_path: pathlib.Path) -> dict[tuple, set[str]]:
    """Return {(season, week): {home_team, ...}} for games already in the CSV."""
    if not csv_path.exists():
        return {}
    result: dict[tuple, set[str]] = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (int(row["season"]), int(row["week"]))
            result.setdefault(key, set()).add(row["home_team"])
    return result


# ---------------------------------------------------------------------------
# Main scraping logic
# ---------------------------------------------------------------------------

def scrape_season(
    season: int,
    existing: dict[tuple, set[str]],
    expected: dict[tuple, set[str]],
    dry_run: bool = False,
    force: bool = False,
) -> list[dict]:
    """Scrape all regular-season weeks for a given year. Returns list of row dicts.

    Weeks where all expected home teams are already in `existing` are skipped
    entirely without making any HTTP requests.
    """
    rows = []
    # NFL regular season: weeks 1-18 (2021+) or 1-17 (before 2021)
    max_week = 18 if season >= 2021 else 17

    for week in range(1, max_week + 1):
        week_key = (season, week)
        expected_home_teams = expected.get(week_key, set())

        # Skip this week if we already have all expected games cached
        if not force and expected_home_teams:
            cached_this_week = existing.get(week_key, set())
            if expected_home_teams <= cached_this_week:
                logger.debug("  Week %d: fully cached (%d games), skipping", week, len(expected_home_teams))
                continue

        url = WEEK_INDEX_URL.format(year=season, week=week)
        logger.info("Season %d, Week %02d — index: %s", season, week, url)

        if dry_run:
            print(f"  [DRY RUN] Would fetch week index: {url}")
            continue

        index_html = _fetch(url)
        if not index_html:
            logger.warning("  Could not fetch week index, skipping week %d", week)
            continue

        game_urls = _extract_game_urls(index_html)
        if not game_urls:
            logger.warning("  No game URLs found for %d week %d — season may not exist", season, week)
            break  # If week 1 has no games, stop entirely

        logger.info("  Found %d game(s)", len(game_urls))

        for game_path in game_urls:
            game_url = BASE + game_path
            time.sleep(0.5)  # polite rate limit

            game_html = _fetch(game_url)
            if not game_html:
                logger.warning("  Skipping game (fetch failed): %s", game_url)
                continue

            result = _parse_game_page(game_html)
            if result is None:
                logger.warning("  Skipping game (parse failed): %s", game_url)
                continue

            week_key = (season, week)
            if not force and result["home_team"] in existing.get(week_key, set()):
                logger.debug("  Already have %s w%d %s — skipping", season, week, result["home_team"])
                continue

            result["season"] = season
            result["week"] = week
            rows.append(result)
            existing.setdefault(week_key, set()).add(result["home_team"])

            logger.info(
                "  %s @ %s | %d-%d (Q: %d/%d/%d/%d vs %d/%d/%d/%d%s)",
                result["away_team"], result["home_team"],
                result["away_score"], result["home_score"],
                result["away_q1"], result["away_q2"], result["away_q3"], result["away_q4"],
                result["home_q1"], result["home_q2"], result["home_q3"], result["home_q4"],
                f"+OT" if result["home_ot"] or result["away_ot"] else "",
            )

    return rows


def _append_rows(rows: list[dict], csv_path: pathlib.Path):
    """Append rows to the CSV, creating with header if needed."""
    write_header = not csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows({k: row[k] for k in CSV_FIELDS} for row in rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Q1/Q2/Q3/Q4 NFL scores from jt-sw.com"
    )
    parser.add_argument("--season", type=int, help="Single season to scrape")
    parser.add_argument(
        "--seasons", type=int, nargs=2, metavar=("FROM", "TO"),
        help="Season range, e.g. --seasons 2006 2025"
    )
    parser.add_argument("--force", action="store_true", help="Re-scrape already cached games")
    parser.add_argument("--dry-run", action="store_true", help="Show URLs without fetching")
    args = parser.parse_args()

    if args.seasons:
        seasons = list(range(args.seasons[0], args.seasons[1] + 1))
    elif args.season:
        seasons = [args.season]
    else:
        seasons = list(range(2006, CURRENT_SEASON + 1))

    print("=" * 65)
    print("  jt-sw.com Quarter Score Scraper")
    print(f"  Seasons: {seasons[0]}-{seasons[-1]}")
    print(f"  Output:  {OUTPUT_CSV}")
    if args.dry_run:
        print("  MODE: DRY RUN")
    print("=" * 65)

    existing = {} if args.force else _load_existing(OUTPUT_CSV)
    expected = _load_expected_games(seasons)
    print(f"  Already cached: {len(existing)} games")
    total_expected = sum(len(v) for v in expected.values())
    print(f"  Expected total: {total_expected} games\n")

    total_new = 0
    for season in seasons:
        print(f"\n--- Season {season} ---")
        rows = scrape_season(season, existing, expected, dry_run=args.dry_run, force=args.force)
        if rows and not args.dry_run:
            _append_rows(rows, OUTPUT_CSV)
            print(f"  Wrote {len(rows)} game(s) to CSV")
        total_new += len(rows)

    print(f"\n{'='*65}")
    print(f"  Done. New games scraped: {total_new}")
    print(f"  CSV: {OUTPUT_CSV}")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
