"""scripts/scrape_quarter_scores.py -- Fetch Q1/Q2/Q3/Q4 scores from ESPN's
scoreboard API.

One request per (season, week) returns every game's per-quarter linescores
as JSON -- no per-game page crawl or HTML parsing needed. Output is written
to rawdata/quarter_scores.csv.

Switched from jt-sw.com (2026-09-19): that site's week index page
occasionally omits a game entirely (confirmed live -- a 2026 Week 1 game
was simply absent from the index, not a parse failure), and ESPN's JSON
endpoint is already used elsewhere in this codebase
(services/live_score_service.py) for live scores. Trade-off: ESPN's
site.api.espn.com scoreboard endpoint does NOT reliably honor historical
`year`/`week` params -- tested live, `year=2015` silently returned the
current season instead. This script (and the weekly recap's comeback-win
detection it feeds) only ever needs the just-finished week, never deep
history, so that's not a problem here -- but don't assume this endpoint
works for a historical backfill without verifying first.

Already-scraped games are cached (skipped on re-run, same as before).

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
import os
import pathlib
import time
from typing import Optional

import pandas as pd
import requests

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

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
# seasontype=2 is ESPN's code for the regular season (1=preseason, 3=postseason) --
# matches this script's existing REG-only scope, so no extra filtering is needed.
ESPN_SEASONTYPE_REG = 2

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
# HTTP + parsing (ESPN scoreboard API)
# ---------------------------------------------------------------------------

def _fetch_espn_week(season: int, week: int, retries: int = 3, delay: float = 1.0) -> Optional[dict]:
    """Fetch one week's scoreboard JSON from ESPN, or None on failure.

    A single request returns every REG-season game for that week -- no
    per-game page crawl, so no rate-limit delay is needed between games
    (unlike the old jt-sw.com per-page scrape). Returns the full response
    dict (not just `events`) so the caller can verify ESPN actually honored
    the requested `season` -- see scrape_season()'s check below.
    """
    params = {"week": week, "year": season, "seasontype": ESPN_SEASONTYPE_REG}
    for attempt in range(retries):
        try:
            resp = requests.get(ESPN_SCOREBOARD_URL, params=params, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < retries - 1:
                logger.debug("Retry %d for %s week %d: %s", attempt + 1, season, week, e)
                time.sleep(delay * (attempt + 1))
            else:
                logger.warning("Failed to fetch ESPN scoreboard for %s week %d: %s", season, week, e)
    return None


def _parse_espn_event(event: dict) -> Optional[dict]:
    """Parse one ESPN scoreboard event into a dict with keys matching CSV_FIELDS.

    Returns None for a game that hasn't finished yet (no quarter breakdown
    to report) or that doesn't parse as expected.
    """
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    comp = competitions[0]

    status = (comp.get("status") or {}).get("type") or {}
    if not status.get("completed"):
        return None

    competitors = comp.get("competitors") or []
    if len(competitors) != 2:
        logger.debug("Expected 2 competitors, got %d: %s", len(competitors), event.get("id"))
        return None

    sides: dict = {}
    for c in competitors:
        home_away = c.get("homeAway")
        abbr = _normalize_nflverse_team((c.get("team") or {}).get("abbreviation", ""))
        linescores = c.get("linescores") or []
        if len(linescores) < 4:
            logger.debug("Missing quarter breakdown for %s (event %s)", abbr, event.get("id"))
            return None

        q = [int(ls.get("value") or 0) for ls in linescores[:4]]
        # Any period beyond regulation (5+) is overtime; summed into one field,
        # matching this script's existing single-OT-column CSV schema.
        ot = sum(int(ls.get("value") or 0) for ls in linescores[4:])

        try:
            total = int(c.get("score"))
        except (TypeError, ValueError):
            logger.debug("Missing final score for %s (event %s)", abbr, event.get("id"))
            return None

        sides[home_away] = {
            "code": abbr, "q1": q[0], "q2": q[1], "q3": q[2], "q4": q[3],
            "ot": ot, "total": total,
        }

    if "home" not in sides or "away" not in sides:
        logger.debug("Missing home/away side for event %s", event.get("id"))
        return None

    home, away = sides["home"], sides["away"]
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
    weeks: list[int] | None = None,
) -> list[dict]:
    """Scrape regular-season weeks for a given year. Returns list of row dicts.

    Weeks where all expected home teams are already in `existing` are skipped
    entirely without making any HTTP requests.

    Args:
        weeks: If given, restrict scraping to just these weeks instead of the
            full season (e.g. a weekly incremental scrape of just the prior
            week). None (default) scrapes every week of the season.
    """
    rows = []
    # NFL regular season: weeks 1-18 (2021+) or 1-17 (before 2021)
    max_week = 18 if season >= 2021 else 17
    week_range = weeks if weeks is not None else range(1, max_week + 1)

    for week in week_range:
        week_key = (season, week)
        expected_home_teams = expected.get(week_key, set())

        # Skip this week if we already have all expected games cached
        if not force and expected_home_teams:
            cached_this_week = existing.get(week_key, set())
            if expected_home_teams <= cached_this_week:
                logger.debug("  Week %d: fully cached (%d games), skipping", week, len(expected_home_teams))
                continue

        logger.info("Season %d, Week %02d — ESPN scoreboard", season, week)

        if dry_run:
            print(f"  [DRY RUN] Would fetch ESPN scoreboard: season={season} week={week}")
            continue

        data = _fetch_espn_week(season, week)
        if data is None:
            logger.warning("  Could not fetch ESPN scoreboard, skipping week %d", week)
            continue

        # ESPN's site.api scoreboard endpoint does not reliably honor a
        # historical `year` param -- confirmed live, requesting an older
        # season silently returns the CURRENT season's data instead of
        # erroring. Trust the response's own reported season over the
        # request we sent, and refuse to ingest it as `season` if they
        # disagree -- every later week of this season would hit the same
        # mismatch, so there is no point continuing.
        returned_season = ((data.get("leagues") or [{}])[0].get("season") or {}).get("year")
        if returned_season is not None and returned_season != season:
            logger.warning(
                "  ESPN returned season %s data for requested season %s -- "
                "this endpoint does not support historical seasons. Stopping season %s.",
                returned_season, season, season,
            )
            break

        events = data.get("events") or []
        if not events:
            logger.warning("  No games found for %d week %d — season may not exist", season, week)
            break  # If week 1 has no games, stop entirely

        logger.info("  Found %d game(s)", len(events))

        for event in events:
            result = _parse_espn_event(event)
            if result is None:
                logger.debug("  Skipping event (not final or unparsable): %s", event.get("id"))
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
        description="Fetch Q1/Q2/Q3/Q4 NFL scores from ESPN's scoreboard API"
    )
    parser.add_argument("--season", type=int, help="Single season to scrape")
    parser.add_argument(
        "--seasons", type=int, nargs=2, metavar=("FROM", "TO"),
        help="Season range, e.g. --seasons 2006 2025"
    )
    parser.add_argument(
        "--week", type=int,
        help="Restrict to a single week within --season (e.g. an incremental "
             "weekly scrape of just the prior week). Not compatible with --seasons."
    )
    parser.add_argument("--force", action="store_true", help="Re-scrape already cached games")
    parser.add_argument("--dry-run", action="store_true", help="Show which weeks would be fetched without fetching")
    parser.add_argument(
        "--firestore", action="store_true",
        help="Also push each season actually touched to the Firestore quarter_scores "
             "collection (used by the weekly recap's comeback-win detection)"
    )
    args = parser.parse_args()

    if args.week is not None and args.seasons:
        parser.error("--week is not compatible with --seasons (a week is season-scoped)")
    if args.week is not None and not args.season:
        parser.error("--week requires --season")

    if args.firestore:
        # get_db() (services/db_service.py) returns None whenever USE_LOCAL_DATA
        # is true, regardless of the use_local=False passed to
        # write_quarter_scores_season below -- so on a normal local dev machine
        # this must be forced before any Firestore write is attempted, same as
        # refresh_local_pkls.py / cache_builder.py / compute_elo.py --firestore do.
        os.environ["USE_LOCAL_DATA"] = "False"

    if args.seasons:
        seasons = list(range(args.seasons[0], args.seasons[1] + 1))
    elif args.season:
        seasons = [args.season]
    else:
        seasons = list(range(2006, CURRENT_SEASON + 1))

    weeks = [args.week] if args.week is not None else None

    print("=" * 65)
    print("  ESPN Quarter Score Scraper")
    print(f"  Seasons: {seasons[0]}-{seasons[-1]}")
    if weeks:
        print(f"  Weeks:   {weeks}")
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
    seasons_touched = set()
    for season in seasons:
        print(f"\n--- Season {season} ---")
        rows = scrape_season(season, existing, expected, dry_run=args.dry_run, force=args.force, weeks=weeks)
        if rows and not args.dry_run:
            _append_rows(rows, OUTPUT_CSV)
            print(f"  Wrote {len(rows)} game(s) to CSV")
            seasons_touched.add(season)
        total_new += len(rows)

    print(f"\n{'='*65}")
    print(f"  Done. New games scraped: {total_new}")
    print(f"  CSV: {OUTPUT_CSV}")
    print(f"{'='*65}")

    if args.firestore and seasons_touched:
        # Re-read each touched season's FULL accumulated rows back out of the
        # CSV (not just the rows this run added) -- the Firestore doc is
        # one-per-season, so a partial write would regress it.
        from services.cache_service import write_quarter_scores_season

        print("\n  Pushing touched seasons to Firestore quarter_scores collection...")
        all_rows = pd.read_csv(OUTPUT_CSV, low_memory=False) if OUTPUT_CSV.exists() else pd.DataFrame()
        for season in sorted(seasons_touched):
            season_rows = all_rows[all_rows["season"] == season].to_dict(orient="records")
            write_quarter_scores_season(int(season), season_rows, use_local=False)
            print(f"  Pushed season {season} ({len(season_rows)} games)")


if __name__ == "__main__":
    main()
