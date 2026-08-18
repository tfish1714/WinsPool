"""scripts/compute_elo.py -- Compute NFL Elo ratings from scratch.

Implements the Reddit r/nfl methodology (https://www.reddit.com/r/nfl/comments/1q0kg87/nfl_elo_model_that_uses_margin_and_game_flow/):
  - K=12, HFA=+15 rating points added to home team before computing expected win prob
  - MoV multiplier: log(margin + 1) / 2.5, clamped [0.5, 2.0]
  - Quarter-by-quarter updates (K_q=3 each): reduces noise from garbage time
    by discounting to 0.1x when a team leads by 17+ going into that quarter
  - Season regression: elo = elo + (1500 - elo) / 3 before each new season
  - All teams initialize at 1500 in first season

Requires:
  - rawdata/schedules/games.csv (nflverse, always present)
  - rawdata/quarter_scores.csv (from scrape_quarter_scores.py, optional but recommended)
    If missing, quarter-by-quarter updates are skipped.

Output:
  - rawdata/elo_computed.csv: one row per regular-season game with pre-game Elo values
  - .local_db/elo_history_{season}.json: per-season mirror for the local dev
    server (always written); the admin Elo Ratings Explorer reads this
    instead of the CSV directly (routes must never read rawdata/ CSVs).
  - Firestore elo_history/{season} docs, one per season, when --firestore is
    passed -- required for the Elo Ratings Explorer to work in production,
    since rawdata/ is gitignored and never reaches the deployed image.

Usage:
    python scripts/compute_elo.py
    python scripts/compute_elo.py --min-season 2006 --max-season 2025
    python scripts/compute_elo.py --output rawdata/elo_computed.csv
    python scripts/compute_elo.py --firestore
"""

import argparse
import math
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from services.db_service import save_metadata

import pandas as pd

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"
GAMES_CSV = RAWDATA_DIR / "schedules" / "games.csv"
QUARTER_CSV = RAWDATA_DIR / "quarter_scores.csv"
DEFAULT_OUTPUT = RAWDATA_DIR / "elo_computed.csv"

# Elo parameters
INITIAL_ELO = 1500.0
K = 12.0           # base K-factor for final game result
HFA = 15.0         # home field advantage (added to home Elo before computing E)
K_Q = 3.0          # K-factor applied after each quarter result
GARBAGE_K_MULT = 0.1   # multiply K_Q by this when leading 17+ into a quarter
GARBAGE_THRESHOLD = 17  # point lead that triggers garbage time discount
MOV_MIN = 0.5      # minimum margin-of-victory multiplier
MOV_MAX = 2.0      # maximum margin-of-victory multiplier
REGRESSION = 1.0 / 3.0  # fraction of distance to 1500 pulled back each off-season
BYE_BONUS = 8.0          # temporary Elo boost for team coming off bye (week_gap >= 2)
SHORT_REST_PENALTY = 3.0  # temporary Elo penalty for team with <= 4 days rest (TNF)


# ---------------------------------------------------------------------------
# Elo math
# ---------------------------------------------------------------------------

def _expected(home_elo: float, away_elo: float) -> float:
    """Expected win probability for home team, incorporating HFA."""
    return 1.0 / (1.0 + 10.0 ** (-(home_elo + HFA - away_elo) / 400.0))


def _mov_mult(margin: int) -> float:
    """Margin-of-victory multiplier: log(margin+1)/2.5, clamped [0.5, 2.0]."""
    return max(MOV_MIN, min(MOV_MAX, math.log(abs(margin) + 1) / 2.5))


def _actual(home_score: int, away_score: int) -> float:
    """Game outcome from home team's perspective: 1=win, 0.5=tie, 0=loss."""
    if home_score > away_score:
        return 1.0
    elif home_score < away_score:
        return 0.0
    return 0.5


def _quarter_actual(home_q: int, away_q: int) -> float:
    """Quarter outcome from home team's perspective."""
    if home_q > away_q:
        return 1.0
    elif home_q < away_q:
        return 0.0
    return 0.5


def _rest_adj(week_gap: int, rest: int) -> float:
    """Temporary Elo offset for bye week or short rest.

    Bye detection uses week number gap (not calendar days) because calendar
    rest varies with surrounding game days — a Monday game before a bye can
    produce as few as 10 rest days, indistinguishable from a Thursday→Sunday
    turnaround.

    Returns Elo points added to the effective (not stored) pre-game rating for
    win-probability computation. This shifts E and therefore the Elo delta for
    the game, but does not appear in the stored home_elo_pre / away_elo_pre columns.
    """
    if week_gap >= 2:
        return BYE_BONUS
    if rest <= 4:  # minimum observed is 4 days (Thanksgiving / TNF)
        return -SHORT_REST_PENALTY
    return 0.0


# ---------------------------------------------------------------------------
# Core Elo update
# ---------------------------------------------------------------------------

def _update_game(
    home_elo: float,
    away_elo: float,
    home_score: int,
    away_score: int,
    quarters: dict | None,
    home_week_gap: int = 1,
    away_week_gap: int = 1,
    home_rest: int = 7,
    away_rest: int = 7,
) -> tuple[float, float, float]:
    """
    Apply all Elo updates for one game.

    quarters: dict with keys home_q1..q4, home_ot, away_ot
              or None if quarter scores unavailable.
    home_week_gap / away_week_gap: schedule weeks since team's last game.
                                   >= 2 means the team had a bye.
    home_rest / away_rest: calendar days since last game (for short-rest detection).

    Returns (new_home_elo, new_away_elo, pre_game_expected_home).
    """
    home_adj = _rest_adj(home_week_gap, home_rest)
    away_adj = _rest_adj(away_week_gap, away_rest)
    E = _expected(home_elo + home_adj, away_elo + away_adj)

    # --- Quarter-by-quarter updates ---
    if quarters is not None:
        hq = [quarters["home_q1"], quarters["home_q2"], quarters["home_q3"], quarters["home_q4"]]
        aq = [quarters["away_q1"], quarters["away_q2"], quarters["away_q3"], quarters["away_q4"]]

        # Add OT as a 5th "quarter" if it was played
        if quarters.get("home_ot", 0) or quarters.get("away_ot", 0):
            hq.append(quarters.get("home_ot", 0))
            aq.append(quarters.get("away_ot", 0))

        home_cum = 0
        away_cum = 0
        for home_q, away_q in zip(hq, aq):
            # Garbage time check: margin going INTO this quarter
            margin_into = home_cum - away_cum
            k_eff = K_Q * (GARBAGE_K_MULT if abs(margin_into) >= GARBAGE_THRESHOLD else 1.0)

            delta_q = k_eff * (_quarter_actual(home_q, away_q) - E)
            home_elo += delta_q
            away_elo -= delta_q

            home_cum += home_q
            away_cum += away_q

    # --- Final game result update ---
    margin = abs(home_score - away_score)
    mult = _mov_mult(margin)
    act = _actual(home_score, away_score)
    delta = K * mult * (act - E)
    home_elo += delta
    away_elo -= delta

    return home_elo, away_elo, E


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_elo(
    min_season: int = 2006,
    max_season: int = 2025,
) -> pd.DataFrame:
    """
    Compute Elo ratings for all regular-season games from min_season to max_season.

    Returns a DataFrame with one row per game and columns:
        season, week, home_team, away_team,
        home_elo_pre, away_elo_pre, home_elo_post, away_elo_post,
        home_exp (pre-game expected win prob for home)
    """
    # --- Load schedules ---
    games = pd.read_csv(GAMES_CSV, low_memory=False)
    games = games[
        (games["game_type"] == "REG")
        & (games["season"] >= min_season)
        & (games["season"] <= max_season)
        & games["home_score"].notna()
        & games["away_score"].notna()
    ].copy()
    games["home_score"] = games["home_score"].astype(int)
    games["away_score"] = games["away_score"].astype(int)
    games["week"] = games["week"].astype(int)
    games = games.sort_values(["season", "week"]).reset_index(drop=True)

    print(f"  Loaded {len(games)} completed regular-season games ({min_season}-{max_season})")

    # --- Load quarter scores if available ---
    quarter_lookup: dict[tuple, dict] = {}
    if QUARTER_CSV.exists():
        qs = pd.read_csv(QUARTER_CSV, low_memory=False)
        for _, row in qs.iterrows():
            key = (int(row["season"]), int(row["week"]), str(row["home_team"]), str(row["away_team"]))
            quarter_lookup[key] = row.to_dict()
        print(f"  Loaded {len(quarter_lookup)} games with quarter scores")
    else:
        print(f"  No quarter_scores.csv found — skipping quarter-by-quarter updates")

    # --- Initialize Elo ---
    # Seed teams with historical data going back to 2006; process seasons
    # before min_season to warm up ratings (use all games in schedules back to 2006)
    all_seasons_games = pd.read_csv(GAMES_CSV, low_memory=False)
    all_seasons_games = all_seasons_games[
        (all_seasons_games["game_type"] == "REG")
        & (all_seasons_games["season"] >= 2006)
        & (all_seasons_games["season"] <= max_season)
        & all_seasons_games["home_score"].notna()
        & all_seasons_games["away_score"].notna()
    ].copy()
    all_seasons_games["home_score"] = all_seasons_games["home_score"].astype(int)
    all_seasons_games["away_score"] = all_seasons_games["away_score"].astype(int)
    all_seasons_games["week"] = all_seasons_games["week"].astype(int)
    all_seasons_games = all_seasons_games.sort_values(["season", "week"]).reset_index(drop=True)

    elo: dict[str, float] = {}
    last_week: dict[tuple, int] = {}  # (team, season) → last week number played
    current_season = None
    rows = []

    for _, game in all_seasons_games.iterrows():
        season = int(game["season"])
        week = int(game["week"])
        home = str(game["home_team"])
        away = str(game["away_team"])

        # Week-gap: how many schedule weeks since this team last played?
        # Defaults to 1 (no bye) for Week 1 when no prior entry exists.
        home_week_gap = week - last_week.get((home, season), week - 1)
        away_week_gap = week - last_week.get((away, season), week - 1)

        # Calendar rest days — already in games.csv, used only for short-rest detection.
        home_rest = int(game["home_rest"]) if pd.notna(game["home_rest"]) else 7
        away_rest = int(game["away_rest"]) if pd.notna(game["away_rest"]) else 7

        # Season regression at start of each new season
        if season != current_season:
            if current_season is not None:
                for team in list(elo.keys()):
                    elo[team] = elo[team] + REGRESSION * (INITIAL_ELO - elo[team])
            current_season = season

        # Initialize new teams
        if home not in elo:
            elo[home] = INITIAL_ELO
        if away not in elo:
            elo[away] = INITIAL_ELO

        home_pre = elo[home]
        away_pre = elo[away]

        # Quarter scores (if available)
        q_key = (season, week, home, away)
        quarters = quarter_lookup.get(q_key)

        # Update Elo
        new_home, new_away, home_exp = _update_game(
            home_pre, away_pre,
            game["home_score"], game["away_score"],
            quarters,
            home_week_gap=home_week_gap,
            away_week_gap=away_week_gap,
            home_rest=home_rest,
            away_rest=away_rest,
        )
        elo[home] = new_home
        elo[away] = new_away
        last_week[(home, season)] = week
        last_week[(away, season)] = week

        # Only record rows in the requested output range
        if season >= min_season:
            rows.append({
                "season": season,
                "week": week,
                "home_team": home,
                "away_team": away,
                "home_elo_pre": round(home_pre, 2),
                "away_elo_pre": round(away_pre, 2),
                "home_elo_post": round(new_home, 2),
                "away_elo_post": round(new_away, 2),
                "home_exp": round(home_exp, 4),
                "home_score": game["home_score"],
                "away_score": game["away_score"],
            })

    df = pd.DataFrame(rows)
    print(f"  Computed {len(df)} rows")
    return df, elo


# ---------------------------------------------------------------------------
# Season summary helper
# ---------------------------------------------------------------------------

def _print_season_summary(elo: dict[str, float], top_n: int = 10):
    ranked = sorted(elo.items(), key=lambda x: x[1], reverse=True)
    print(f"\n  Top {top_n} teams by final Elo:")
    for i, (team, rating) in enumerate(ranked[:top_n], 1):
        print(f"    {i:>2}. {team:<4} {rating:.1f}")
    print(f"\n  Bottom 5 teams by final Elo:")
    for team, rating in ranked[-5:]:
        print(f"      {team:<4} {rating:.1f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compute NFL Elo ratings using Reddit methodology"
    )
    parser.add_argument("--min-season", type=int, default=2006)
    parser.add_argument("--max-season", type=int, default=2025)
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--firestore", action="store_true",
                        help="Also push per-season rows to the Firestore elo_history "
                             "collection (used by the admin Elo Ratings Explorer)")
    args = parser.parse_args()

    if args.firestore:
        # get_db() (services/db_service.py) returns None whenever USE_LOCAL_DATA
        # is true, regardless of the use_local=False passed to
        # write_elo_history_season below -- so on a normal local dev machine
        # this must be forced before any Firestore write is attempted, same as
        # refresh_local_pkls.py / cache_builder.py / run_predictions.py do.
        os.environ["USE_LOCAL_DATA"] = "False"

    print("=" * 65)
    print("  NFL Elo Rating Computation")
    print(f"  Seasons: {args.min_season}-{args.max_season}")
    print(f"  K={K}, HFA={HFA}, K_q={K_Q}, garbage_threshold={GARBAGE_THRESHOLD}")
    print("=" * 65)

    df, final_elo = compute_elo(args.min_season, args.max_season)

    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"\n  Saved -> {output_path}")

    _print_season_summary(final_elo)

    from services.cache_service import write_elo_history_season
    print("\n  Writing per-season rows to local elo_history cache...")
    for season, group in df.groupby("season"):
        write_elo_history_season(int(season), group.to_dict(orient="records"), use_local=True)
    print(f"  Wrote {df['season'].nunique()} seasons -> .local_db/elo_history_<season>.json")

    if args.firestore:
        print("\n  Pushing per-season rows to Firestore elo_history collection...")
        for season, group in df.groupby("season"):
            write_elo_history_season(int(season), group.to_dict(orient="records"), use_local=False)
        print(f"  Pushed {df['season'].nunique()} seasons to Firestore")

    try:
        save_metadata("sync_elo", {
            "completed_at": time.time(),
            "season": int(df["season"].max()),
            "week": int(df["week"].max()),
            "games_processed": len(df),
            "status": "ok",
            "error": None,
        })
    except Exception as _e:
        print(f"  Warning: could not write sync metadata: {_e}")

    print(f"\n{'='*65}")
    print(f"  Done. {len(df)} game rows written.")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
