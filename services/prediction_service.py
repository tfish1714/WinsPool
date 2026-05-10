"""services/prediction_service.py -- Advanced NFL Prediction Engine.

Blends two peer-reviewed methodologies to produce game-level win probabilities,
season-level portfolio projections, and draft-room confidence scores:

    1. FiveThirtyEight Elo (538)
       - Dynamic power ratings updated after every game (K=20).
       - Logistic win probability with home-field, travel, bye-week, and
         margin-of-victory adjustments.
       - Source: fivethirtyeight.com/methodology/how-our-nfl-predictions-work/

    2. Frontiers Pythagorean Expectation
       - Season-level win-percentage model using points scored/allowed
         with the NFL-optimal exponent of 2.37.
       - Source: doi.org/10.3389/fspor.2025.1638446

The blend weight (default 70% Elo / 30% Pythagorean) is stored in Firestore
at metadata/prediction_config and is tunable by admins at runtime.
"""

import math
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ELO_MEAN = 1505
ELO_K = 20
ELO_HOME_ADVANTAGE = 48
ELO_TRAVEL_PER_1000MI = 4
ELO_BYE_BONUS = 25
ELO_PLAYOFF_MULTIPLIER = 1.2
ELO_REVERSION_FACTOR = 1 / 3

PYTHAGOREAN_EXPONENT = 2.37

DEFAULT_ELO_WEIGHT = 0.7
DEFAULT_SIMULATIONS = 1000

# ---------------------------------------------------------------------------
# NFL Stadium Coordinates (latitude, longitude)
# Used for travel-distance Elo adjustments via Haversine formula.
# Teams sharing a stadium have identical coordinates.
# ---------------------------------------------------------------------------

STADIUM_COORDS: Dict[str, Tuple[float, float]] = {
    "ARI": (33.5276, -112.2626),   # State Farm Stadium, Glendale AZ
    "ATL": (33.7554, -84.4010),    # Mercedes-Benz Stadium, Atlanta GA
    "BAL": (39.2780, -76.6227),    # M&T Bank Stadium, Baltimore MD
    "BUF": (42.7738, -78.7870),    # Highmark Stadium, Orchard Park NY
    "CAR": (35.2258, -80.8528),    # Bank of America Stadium, Charlotte NC
    "CHI": (41.8623, -87.6167),    # Soldier Field, Chicago IL
    "CIN": (39.0955, -84.5161),    # Paycor Stadium, Cincinnati OH
    "CLE": (41.5061, -81.6995),    # Cleveland Browns Stadium, Cleveland OH
    "DAL": (32.7473, -97.0945),    # AT&T Stadium, Arlington TX
    "DEN": (39.7439, -105.0201),   # Empower Field, Denver CO
    "DET": (42.3400, -83.0456),    # Ford Field, Detroit MI
    "GB":  (44.5013, -88.0622),    # Lambeau Field, Green Bay WI
    "HOU": (29.6847, -95.4107),    # NRG Stadium, Houston TX
    "IND": (39.7601, -86.1639),    # Lucas Oil Stadium, Indianapolis IN
    "JAX": (30.3239, -81.6373),    # EverBank Stadium, Jacksonville FL
    "KC":  (39.0489, -94.4839),    # Arrowhead Stadium, Kansas City MO
    "LA":  (33.9535, -118.3390),   # SoFi Stadium, Inglewood CA (Rams)
    "LAC": (33.9535, -118.3390),   # SoFi Stadium, Inglewood CA (Chargers)
    "LV":  (36.0909, -115.1833),   # Allegiant Stadium, Las Vegas NV
    "MIA": (25.9580, -80.2389),    # Hard Rock Stadium, Miami Gardens FL
    "MIN": (44.9736, -93.2575),    # U.S. Bank Stadium, Minneapolis MN
    "NE":  (42.0909, -71.2643),    # Gillette Stadium, Foxborough MA
    "NO":  (29.9511, -90.0812),    # Caesars Superdome, New Orleans LA
    "NYG": (40.8128, -74.0742),    # MetLife Stadium, East Rutherford NJ
    "NYJ": (40.8128, -74.0742),    # MetLife Stadium, East Rutherford NJ
    "PHI": (39.9008, -75.1675),    # Lincoln Financial Field, Philadelphia PA
    "PIT": (40.4468, -80.0158),    # Acrisure Stadium, Pittsburgh PA
    "SEA": (47.5952, -122.3316),   # Lumen Field, Seattle WA
    "SF":  (37.4033, -121.9694),   # Levi's Stadium, Santa Clara CA
    "TB":  (27.9759, -82.5033),    # Raymond James Stadium, Tampa FL
    "TEN": (36.1665, -86.7713),    # Nissan Stadium, Nashville TN
    "WAS": (38.9076, -76.8645),    # Northwest Stadium, Landover MD
    # Historical abbreviation aliases
    "OAK": (37.7516, -122.2005),   # Oakland Coliseum (pre-2020)
    "SD":  (32.7831, -117.1196),   # Qualcomm Stadium, San Diego (pre-2017)
    "STL": (38.6327, -90.1887),    # Edward Jones Dome, St. Louis (pre-2016)
}


def _get_season_teams(games_df: pd.DataFrame, season: int) -> set:
    """Extract the set of team abbreviations that actually played in a given season.

    This filters out defunct/relocated franchises (e.g. SD, STL, OAK) that
    exist in historical Elo ratings but did not play in the target season.

    Args:
        games_df: The full nfl_games DataFrame.
        season: The target season year.

    Returns:
        Set of team abbreviation strings.
    """
    if games_df is None or games_df.empty or "season" not in games_df.columns:
        return set()
    season_games = games_df[games_df["season"] == season]
    if season_games.empty:
        return set()
    return set(season_games["home_team"].unique()) | set(season_games["away_team"].unique())


# ---------------------------------------------------------------------------
# Internal Geometry
# ---------------------------------------------------------------------------

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two points on Earth.

    Uses the Haversine formula:
        a = sin^2(dlat/2) + cos(lat1) * cos(lat2) * sin^2(dlon/2)
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        d = R * c

    Args:
        lat1, lon1: Coordinates of point A in decimal degrees.
        lat2, lon2: Coordinates of point B in decimal degrees.

    Returns:
        Distance in statute miles.
    """
    r_earth_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return r_earth_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _get_travel_distance(away_team: str, home_team: str) -> float:
    """Return the one-way travel distance in miles for the away team.

    Uses the hardcoded STADIUM_COORDS dictionary. Returns 0 if either team
    is not found (graceful degradation for unknown franchises).
    """
    away_coords = STADIUM_COORDS.get(away_team)
    home_coords = STADIUM_COORDS.get(home_team)
    if away_coords is None or home_coords is None:
        return 0.0
    return _haversine_miles(away_coords[0], away_coords[1],
                           home_coords[0], home_coords[1])


# ---------------------------------------------------------------------------
# Elo Engine
# ---------------------------------------------------------------------------

def win_probability(elo_a: float, elo_b: float, adjustments: float = 0.0) -> float:
    """Logistic win probability for Team A given Elo ratings.

    Formula (FiveThirtyEight):
        Pr(A) = 1 / (10^(-EloDiff / 400) + 1)

    Where EloDiff = (elo_a - elo_b) + adjustments.

    Args:
        elo_a: Team A's current Elo rating.
        elo_b: Team B's current Elo rating.
        adjustments: Net situational adjustments in Elo points
                     (home-field, travel, bye, etc.). Positive favors A.

    Returns:
        Probability of Team A winning, in [0, 1].
    """
    elo_diff = (elo_a - elo_b) + adjustments
    return 1.0 / (10.0 ** (-elo_diff / 400.0) + 1.0)


def margin_of_victory_multiplier(winner_point_diff: float, winner_elo_diff: float) -> float:
    """Log-scaled MoV multiplier with autocorrelation correction.

    Formula (FiveThirtyEight):
        MoV = ln(|WinnerPtDiff| + 1) * 2.2 / (WinnerEloDiff * 0.001 + 2.2)

    The autocorrelation correction (denominator) scales down the bonus for
    teams that were heavy favorites, preventing Elo inflation for top clubs.

    Args:
        winner_point_diff: Absolute point differential (always positive).
        winner_elo_diff: The winning team's pregame Elo advantage. For upsets
                         this will be negative, which increases the multiplier.

    Returns:
        A multiplier >= 1.0 applied to the Elo shift.
    """
    log_component = math.log(max(abs(winner_point_diff), 1) + 1)
    autocorrelation_adj = (winner_elo_diff * 0.001) + 2.2
    return log_component * (2.2 / autocorrelation_adj)


def compute_elo_shift(
    winner_elo: float,
    loser_elo: float,
    winner_point_diff: float,
    home_team_won: bool,
    home_adjustments: float = 0.0,
) -> float:
    """Compute the Elo points to transfer from loser to winner.

    Elo Shift = K * ForecastDelta * MoV_Multiplier

    Where:
        K = 20 (sensitivity constant)
        ForecastDelta = ActualResult(1) - Pr(Winner)
        MoV_Multiplier = see margin_of_victory_multiplier()

    Args:
        winner_elo: Winner's pregame Elo.
        loser_elo: Loser's pregame Elo.
        winner_point_diff: Winner's margin of victory (positive).
        home_team_won: Whether the winner was the home team.
        home_adjustments: Net adjustments that were applied pregame.

    Returns:
        Number of Elo points to add to winner (and subtract from loser).
    """
    if home_team_won:
        expected_win_prob = win_probability(winner_elo, loser_elo, home_adjustments)
    else:
        expected_win_prob = win_probability(winner_elo, loser_elo, -home_adjustments)

    forecast_delta = 1.0 - expected_win_prob

    winner_elo_diff = winner_elo - loser_elo
    if not home_team_won:
        winner_elo_diff -= home_adjustments
    else:
        winner_elo_diff += home_adjustments

    mov_mult = margin_of_victory_multiplier(winner_point_diff, winner_elo_diff)
    return ELO_K * forecast_delta * mov_mult


def apply_preseason_reversion(end_of_season_elo: float) -> float:
    """Revert an end-of-season Elo one-third toward the league mean.

    Formula (FiveThirtyEight):
        Preseason_Elo = EndOfSeason_Elo * (2/3) + 1505 * (1/3)

    This hedge accounts for offseason roster turnover, draft picks,
    free agency, and coaching changes without explicitly modeling them.

    Args:
        end_of_season_elo: The team's Elo at the conclusion of the previous season.

    Returns:
        The mean-reverted preseason Elo for the upcoming season.
    """
    return end_of_season_elo * (1.0 - ELO_REVERSION_FACTOR) + ELO_MEAN * ELO_REVERSION_FACTOR


# ---------------------------------------------------------------------------
# Pythagorean Expectation
# ---------------------------------------------------------------------------

def pythagorean_win_pct(points_for: float, points_against: float) -> float:
    """NFL Pythagorean expected winning percentage.

    Formula (Morey, adapted by Frontiers):
        WinPct = PF^k / (PF^k + PA^k)    where k = 2.37

    This formula was validated across 21 NFL seasons (2003-2023) with an R-squared
    of 0.891 when used as input to a neural network model (Frontiers study).

    Args:
        points_for: Total points scored by the team in the period.
        points_against: Total points allowed by the team in the period.

    Returns:
        Expected winning percentage in [0, 1]. Returns 0.5 if both are zero.
    """
    if points_for <= 0 and points_against <= 0:
        return 0.5
    pf_exp = points_for ** PYTHAGOREAN_EXPONENT
    pa_exp = points_against ** PYTHAGOREAN_EXPONENT
    denominator = pf_exp + pa_exp
    if denominator == 0:
        return 0.5
    return pf_exp / denominator


def pythagorean_projected_wins(points_for: float, points_against: float,
                               total_games: int = 17) -> float:
    """Project total season wins from scoring data.

    Args:
        points_for: Total points scored.
        points_against: Total points allowed.
        total_games: Number of games in the season (default 17).

    Returns:
        Projected number of wins.
    """
    return pythagorean_win_pct(points_for, points_against) * total_games


# ---------------------------------------------------------------------------
# PredictionService (stateful, caches Elo ratings for a season)
# ---------------------------------------------------------------------------

_SERVICE_CACHE: Dict[int, 'PredictionService'] = {}

class PredictionService:
    """Orchestrates the blended Elo + Pythagorean prediction model.

    Usage:
        svc = PredictionService.get_initialized(games_df, season=2024)
        prob = svc.game_win_probability("KC", "BUF")
        proj = svc.project_portfolio_wins(["KC", "BUF", "DET"], season=2024)
    """

    @classmethod
    def get_initialized(cls, games_df: Optional[pd.DataFrame], season: int) -> 'PredictionService':
        """Singleton-like accessor that returns a precached, initialized service for a season."""
        global _SERVICE_CACHE
        if season in _SERVICE_CACHE:
            return _SERVICE_CACHE[season]
        
        if games_df is None:
            raise ValueError(f"PredictionService for season {season} is not initialized and no games_df provided.")
            
        svc = cls()
        svc.initialize(games_df, season)
        _SERVICE_CACHE[season] = svc
        return svc

    @classmethod
    def is_initialized(cls, season: int) -> bool:
        """Check if a service for the given season is already in the global cache."""
        global _SERVICE_CACHE
        return season in _SERVICE_CACHE

    @staticmethod
    def clear_cache():
        global _SERVICE_CACHE
        _SERVICE_CACHE.clear()

    def __init__(self):
        self._elo_ratings: Dict[str, float] = {}
        self._team_scoring: Dict[str, Dict[str, float]] = {}
        self._season: Optional[int] = None
        self._games_df: Optional[pd.DataFrame] = None
        self._bye_weeks: Dict[str, List[int]] = {}
        self._elo_weight: float = DEFAULT_ELO_WEIGHT

    # -------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------

    def load_config(self) -> Dict:
        """Load model configuration from Firestore metadata/prediction_config.

        Returns a dict with at least 'elo_weight'. Falls back to defaults
        if the document does not exist.
        """
        try:
            from services.db_service import get_metadata
            config = get_metadata("prediction_config")
            if config and "elo_weight" in config:
                self._elo_weight = float(config["elo_weight"])
                return config
        except Exception:
            pass
        self._elo_weight = DEFAULT_ELO_WEIGHT
        return {"elo_weight": DEFAULT_ELO_WEIGHT, "simulations": DEFAULT_SIMULATIONS}

    @staticmethod
    def save_config(elo_weight: float, simulations: int = DEFAULT_SIMULATIONS) -> None:
        """Persist model configuration to Firestore metadata/prediction_config.

        Args:
            elo_weight: Weight given to Elo model (0.0 to 1.0).
                        Pythagorean weight is automatically 1 - elo_weight.
            simulations: Number of Monte Carlo iterations.
        """
        from services.db_service import get_db
        db = get_db()
        if db:
            db.collection("metadata").document("prediction_config").set({
                "elo_weight": round(float(elo_weight), 2),
                "simulations": int(simulations),
            })

    # -------------------------------------------------------------------
    # Initialization: Build Elo Ratings from Historical Games
    # -------------------------------------------------------------------

    def initialize(self, games_df: pd.DataFrame, season: int) -> None:
        """Replay all games up to and including 'season' to build Elo ratings.

        This replays every regular-season game from the earliest available
        season, applying preseason reversion at each year boundary, to
        produce an accurate current-state Elo for every team.

        Also computes per-team cumulative scoring for Pythagorean estimates
        and identifies bye weeks for the target season.

        Args:
            games_df: The full nfl_games DataFrame (all seasons).
            season: The target season to build ratings for.
        """
        self.load_config()
        self._season = season
        self._games_df = games_df

        # Filter to regular-season games only
        if "game_type" in games_df.columns:
            reg = games_df[games_df["game_type"] == "REG"].copy()
        else:
            reg = games_df.copy()

        reg = reg.dropna(subset=["result"])
        reg = reg.sort_values(["season", "week"]).reset_index(drop=True)

        available_seasons = sorted(reg["season"].unique())
        if not available_seasons:
            return

        # Initialize all teams at league mean
        all_teams = set(reg["home_team"].unique()) | set(reg["away_team"].unique())
        self._elo_ratings = {t: float(ELO_MEAN) for t in all_teams}

        prev_season = None
        for _, game in reg.iterrows():
            game_season = int(game["season"])
            if game_season > season:
                break

            # Apply preseason reversion at year boundary
            if prev_season is not None and game_season != prev_season:
                for team in self._elo_ratings:
                    self._elo_ratings[team] = apply_preseason_reversion(
                        self._elo_ratings[team]
                    )
            prev_season = game_season

            home = game["home_team"]
            away = game["away_team"]
            result = float(game["result"])

            # Ensure teams exist in ratings
            for t in (home, away):
                if t not in self._elo_ratings:
                    self._elo_ratings[t] = float(ELO_MEAN)

            # Compute pregame adjustments
            travel_miles = _get_travel_distance(away, home)
            travel_adj = (travel_miles / 1000.0) * ELO_TRAVEL_PER_1000MI
            net_home_adj = ELO_HOME_ADVANTAGE + travel_adj

            if result > 0:
                # Home team won
                shift = compute_elo_shift(
                    self._elo_ratings[home],
                    self._elo_ratings[away],
                    abs(result),
                    home_team_won=True,
                    home_adjustments=net_home_adj,
                )
                self._elo_ratings[home] += shift
                self._elo_ratings[away] -= shift
            elif result < 0:
                # Away team won
                shift = compute_elo_shift(
                    self._elo_ratings[away],
                    self._elo_ratings[home],
                    abs(result),
                    home_team_won=False,
                    home_adjustments=net_home_adj,
                )
                self._elo_ratings[away] += shift
                self._elo_ratings[home] -= shift
            # Ties: no shift

        # Build scoring aggregates for the target season
        season_games = reg[reg["season"] == season]
        self._team_scoring = {}
        for team in all_teams:
            home_games = season_games[season_games["home_team"] == team]
            away_games = season_games[season_games["away_team"] == team]

            pts_for = (
                home_games["home_score"].sum() + away_games["away_score"].sum()
            )
            pts_against = (
                home_games["away_score"].sum() + away_games["home_score"].sum()
            )
            games_played = len(home_games) + len(away_games)

            self._team_scoring[team] = {
                "points_for": float(pts_for) if not pd.isna(pts_for) else 0.0,
                "points_against": float(pts_against) if not pd.isna(pts_against) else 0.0,
                "games_played": int(games_played),
            }

        # Identify bye weeks for the target season
        self._bye_weeks = {}
        if not season_games.empty:
            all_weeks = sorted(season_games["week"].unique())
            for team in all_teams:
                team_weeks = set(
                    season_games[
                        (season_games["home_team"] == team) |
                        (season_games["away_team"] == team)
                    ]["week"].unique()
                )
                self._bye_weeks[team] = [w for w in all_weeks if w not in team_weeks]

    # -------------------------------------------------------------------
    # Game-Level Predictions
    # -------------------------------------------------------------------

    def game_win_probability(
        self,
        home_team: str,
        away_team: str,
        is_playoff: bool = False,
        home_off_bye: bool = False,
        away_off_bye: bool = False,
    ) -> Dict:
        """Compute the blended win probability for a single game.

        The blend formula:
            P = elo_weight * Elo_WinProb + (1 - elo_weight) * Pyth_WinProb

        Args:
            home_team: Home team abbreviation.
            away_team: Away team abbreviation.
            is_playoff: Apply the 1.2x EloDiff multiplier for playoff games.
            home_off_bye: Home team is coming off a bye week (+25 Elo).
            away_off_bye: Away team is coming off a bye week (+25 Elo).

        Returns:
            Dict with home_win_prob, away_win_prob, elo_component details.
        """
        home_elo = self._elo_ratings.get(home_team, ELO_MEAN)
        away_elo = self._elo_ratings.get(away_team, ELO_MEAN)

        # Build adjustments stack
        adjustments = float(ELO_HOME_ADVANTAGE)
        travel_miles = _get_travel_distance(away_team, home_team)
        adjustments += (travel_miles / 1000.0) * ELO_TRAVEL_PER_1000MI

        if home_off_bye:
            adjustments += ELO_BYE_BONUS
        if away_off_bye:
            adjustments -= ELO_BYE_BONUS

        elo_diff = (home_elo - away_elo) + adjustments
        if is_playoff:
            elo_diff *= ELO_PLAYOFF_MULTIPLIER

        elo_home_prob = 1.0 / (10.0 ** (-elo_diff / 400.0) + 1.0)

        # Pythagorean component
        home_scoring = self._team_scoring.get(home_team, {})
        away_scoring = self._team_scoring.get(away_team, {})
        home_pyth = pythagorean_win_pct(
            home_scoring.get("points_for", 0),
            home_scoring.get("points_against", 0),
        )
        away_pyth = pythagorean_win_pct(
            away_scoring.get("points_for", 0),
            away_scoring.get("points_against", 0),
        )
        # Convert season-level win pcts into a head-to-head probability
        pyth_sum = home_pyth + away_pyth
        pyth_home_prob = (home_pyth / pyth_sum) if pyth_sum > 0 else 0.5

        # Blend
        w = self._elo_weight
        blended_home = w * elo_home_prob + (1.0 - w) * pyth_home_prob

        return {
            "home_team": home_team,
            "away_team": away_team,
            "home_win_prob": round(blended_home, 4),
            "away_win_prob": round(1.0 - blended_home, 4),
            "elo_home_prob": round(elo_home_prob, 4),
            "pyth_home_prob": round(pyth_home_prob, 4),
            "home_elo": round(home_elo, 1),
            "away_elo": round(away_elo, 1),
            "adjustments": round(adjustments, 1),
            "travel_miles": round(travel_miles, 0),
            "elo_weight": w,
            "predicted_spread": round(elo_diff / 25.0, 1),
        }

    # -------------------------------------------------------------------
    # Portfolio Simulation (Monte Carlo)
    # -------------------------------------------------------------------

    def project_portfolio_wins(
        self,
        team_ids: List[str],
        season: int,
        games_df: Optional[pd.DataFrame] = None,
        n_simulations: Optional[int] = None,
    ) -> Dict:
        """Simulate the remaining schedule and project cumulative wins for a portfolio.

        Uses Monte Carlo simulation with the blended model. Each simulation
        plays out every remaining game independently, updating a running win
        count for the portfolio teams. The method runs the simulation "hot"
        (team Elo ratings update within each simulation run, per FiveThirtyEight).

        Args:
            team_ids: List of NFL team abbreviations (typically 3 for this pool).
            season: The NFL season year.
            games_df: Optional override for the games DataFrame.
            n_simulations: Number of Monte Carlo iterations (default from config).

        Returns:
            Dict with mean_wins, std_wins, min_wins, max_wins, per-team breakdowns,
            and the number of simulations run.
        """
        if games_df is None:
            games_df = self._games_df
        if games_df is None:
            return {"error": "No games data loaded. Call initialize() first."}

        if n_simulations is None:
            n_simulations = DEFAULT_SIMULATIONS

        # Isolate the target season's regular-season games
        season_games = games_df[games_df["season"] == season].copy()
        if "game_type" in season_games.columns:
            season_games = season_games[season_games["game_type"] == "REG"]

        # Split into completed and remaining
        completed = season_games.dropna(subset=["result"])
        remaining = season_games[season_games["result"].isna()]

        # Count actual wins already banked
        actual_wins = {}
        for team in team_ids:
            home_wins = len(completed[
                (completed["home_team"] == team) & (completed["result"] > 0)
            ])
            away_wins = len(completed[
                (completed["away_team"] == team) & (completed["result"] < 0)
            ])
            actual_wins[team] = home_wins + away_wins

        banked_total = sum(actual_wins.values())

        if remaining.empty:
            # Season is complete, no simulation needed
            return {
                "mean_wins": float(banked_total),
                "std_wins": 0.0,
                "min_wins": float(banked_total),
                "max_wins": float(banked_total),
                "actual_wins": actual_wins,
                "projected_additional": {t: 0.0 for t in team_ids},
                "simulations": 0,
                "season_complete": True,
            }

        # Filter remaining games to only those involving portfolio teams
        portfolio_set = set(team_ids)
        relevant_remaining = remaining[
            remaining["home_team"].isin(portfolio_set) |
            remaining["away_team"].isin(portfolio_set)
        ].reset_index(drop=True)

        # Run Monte Carlo
        rng = np.random.default_rng()
        sim_results = np.zeros((n_simulations, len(team_ids)))

        for sim in range(n_simulations):
            # Start each simulation from the current Elo snapshot
            sim_elo = dict(self._elo_ratings)
            sim_wins = {t: 0 for t in team_ids}

            for _, game in relevant_remaining.iterrows():
                home = game["home_team"]
                away = game["away_team"]

                home_elo = sim_elo.get(home, ELO_MEAN)
                away_elo = sim_elo.get(away, ELO_MEAN)

                travel_miles = _get_travel_distance(away, home)
                adj = ELO_HOME_ADVANTAGE + (travel_miles / 1000.0) * ELO_TRAVEL_PER_1000MI

                elo_diff = (home_elo - away_elo) + adj
                elo_prob = 1.0 / (10.0 ** (-elo_diff / 400.0) + 1.0)

                # Pythagorean component
                home_sc = self._team_scoring.get(home, {})
                away_sc = self._team_scoring.get(away, {})
                h_pyth = pythagorean_win_pct(
                    home_sc.get("points_for", 0),
                    home_sc.get("points_against", 0),
                )
                a_pyth = pythagorean_win_pct(
                    away_sc.get("points_for", 0),
                    away_sc.get("points_against", 0),
                )
                pyth_sum = h_pyth + a_pyth
                pyth_prob = (h_pyth / pyth_sum) if pyth_sum > 0 else 0.5

                blended_prob = self._elo_weight * elo_prob + (1.0 - self._elo_weight) * pyth_prob

                # Simulate outcome
                home_wins_game = rng.random() < blended_prob

                if home_wins_game:
                    if home in portfolio_set:
                        sim_wins[home] += 1
                    sim_elo[home] = sim_elo.get(home, ELO_MEAN) + 15
                    sim_elo[away] = sim_elo.get(away, ELO_MEAN) - 15
                else:
                    if away in portfolio_set:
                        sim_wins[away] += 1
                    sim_elo[away] = sim_elo.get(away, ELO_MEAN) + 15
                    sim_elo[home] = sim_elo.get(home, ELO_MEAN) - 15

            for i, team in enumerate(team_ids):
                sim_results[sim, i] = sim_wins[team]

        # Aggregate
        total_projected = sim_results.sum(axis=1) + banked_total
        per_team_means = sim_results.mean(axis=0)

        return {
            "mean_wins": round(float(total_projected.mean()), 2),
            "std_wins": round(float(total_projected.std()), 2),
            "min_wins": float(total_projected.min()),
            "max_wins": float(total_projected.max()),
            "actual_wins": actual_wins,
            "projected_additional": {
                team_ids[i]: round(float(per_team_means[i]), 2)
                for i in range(len(team_ids))
            },
            "simulations": n_simulations,
            "season_complete": False,
        }

    # -------------------------------------------------------------------
    # Draft Room Confidence Scores (Admin Only)
    # -------------------------------------------------------------------

    def generate_draft_confidence_scores(
        self,
        season: int,
        games_df: Optional[pd.DataFrame] = None,
        drafted_teams: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Rank all NFL teams by projected remaining-season value.

        Combines Elo power rating with Pythagorean win projection to produce
        a composite confidence score. Filters to only teams that actually
        played in the target season (excludes defunct franchises like SD, STL, OAK).
        Optionally filters out already-drafted teams.

        Args:
            season: The NFL season year.
            games_df: Optional games DataFrame override.
            drafted_teams: List of team abbreviations already drafted.

        Returns:
            Sorted list of dicts: [{team, elo, projected_wins, confidence, rank}, ...].
        """
        if games_df is None:
            games_df = self._games_df

        if not self._elo_ratings:
            if games_df is not None:
                self.initialize(games_df, season)
            else:
                return []

        # Filter to only teams that exist in this season's game data
        season_teams = _get_season_teams(games_df, season) if games_df is not None else set()
        drafted_set = set(drafted_teams) if drafted_teams else set()
        scores = []

        for team, elo in self._elo_ratings.items():
            # Skip defunct/relocated teams not in this season
            if season_teams and team not in season_teams:
                continue
            if team in drafted_set:
                continue

            scoring = self._team_scoring.get(team, {})
            pf = scoring.get("points_for", 0)
            pa = scoring.get("points_against", 0)
            gp = scoring.get("games_played", 0)

            if gp > 0:
                projected_wins = pythagorean_projected_wins(pf, pa, total_games=17)
            else:
                # No games played yet: use Elo-implied win total
                # A team at 1505 Elo is an 8.5-win team. Each 25 Elo ~= 1 point spread
                # ~= 0.03 win probability per game ~= 0.5 wins over 17 games.
                elo_above_mean = elo - ELO_MEAN
                projected_wins = 8.5 + (elo_above_mean / 25.0) * 0.5

            # Composite confidence: blend Elo rank signal with Pythagorean projection
            # Normalize Elo to a 0-1 scale (typical range 1300-1700)
            elo_norm = max(0.0, min(1.0, (elo - 1300.0) / 400.0))
            pyth_norm = max(0.0, min(1.0, projected_wins / 17.0))
            confidence = self._elo_weight * elo_norm + (1.0 - self._elo_weight) * pyth_norm

            scores.append({
                "team": team,
                "elo": round(elo, 1),
                "projected_wins": round(projected_wins, 1),
                "confidence": round(confidence, 3),
            })

        scores.sort(key=lambda x: x["confidence"], reverse=True)
        for i, s in enumerate(scores):
            s["rank"] = i + 1

        return scores

    # -------------------------------------------------------------------
    # Bulk: All Elo Ratings
    # -------------------------------------------------------------------

    def get_all_ratings(self) -> Dict[str, float]:
        """Return the current Elo ratings dict (team -> elo)."""
        return {t: round(e, 1) for t, e in sorted(
            self._elo_ratings.items(), key=lambda x: x[1], reverse=True
        )}

    def get_team_summary(self, team: str) -> Dict:
        """Return a detailed summary for a single team."""
        elo = self._elo_ratings.get(team, ELO_MEAN)
        scoring = self._team_scoring.get(team, {})
        pf = scoring.get("points_for", 0)
        pa = scoring.get("points_against", 0)
        gp = scoring.get("games_played", 0)

        return {
            "team": team,
            "elo": round(elo, 1),
            "elo_rank": sorted(
                self._elo_ratings.values(), reverse=True
            ).index(elo) + 1 if elo in self._elo_ratings.values() else None,
            "points_for": round(pf, 1),
            "points_against": round(pa, 1),
            "games_played": gp,
            "pythagorean_win_pct": round(pythagorean_win_pct(pf, pa), 3) if gp > 0 else None,
            "pythagorean_projected_wins": round(pythagorean_projected_wins(pf, pa), 1) if gp > 0 else None,
            "bye_weeks": self._bye_weeks.get(team, []),
        }

    def get_team_projected_wins(self, games_df: Optional[pd.DataFrame] = None) -> Dict[str, float]:
        """Return projected season wins for every team in the current season.

        For teams with game data, uses the Pythagorean expectation.
        For teams without game data (preseason), uses Elo-implied win total.
        Filters to only teams active in the initialized season.

        Returns:
            Dict mapping team abbreviation to projected wins (float).
        """
        if games_df is None:
            games_df = self._games_df
 
        # Extract active teams for the season. Fallback to standard 32 if no games found yet.
        season_teams = _get_season_teams(games_df, self._season) if games_df is not None and not games_df.empty else set()
        if not season_teams:
            season_teams = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS"}
        result = {}

        for team, elo in self._elo_ratings.items():
            if season_teams and team not in season_teams:
                continue

            scoring = self._team_scoring.get(team, {})
            pf = scoring.get("points_for", 0)
            pa = scoring.get("points_against", 0)
            gp = scoring.get("games_played", 0)

            if gp > 0:
                projected = pythagorean_projected_wins(pf, pa, total_games=17)
            else:
                elo_above_mean = elo - ELO_MEAN
                projected = 8.5 + (elo_above_mean / 25.0) * 0.5

            result[team] = round(projected, 1)

        return result


# ---------------------------------------------------------------------------
# Schedule Enrichment (used by cache_builder.py)
# ---------------------------------------------------------------------------

def enrich_schedule_with_predictions(
    schedule_df: pd.DataFrame,
    games_df: pd.DataFrame,
    season: int,
) -> pd.DataFrame:
    """Add game-level win predictions to an enriched schedule DataFrame.

    For each unplayed game (result is NaN or -1000), computes the blended
    Elo + Pythagorean win probability and populates:
        - pred_winner: Predicted winning team abbreviation
        - pred_su_conf: Straight-up confidence percentage (50-99)
        - pred_ats_pick: Against-the-spread pick (underdog if spread <= 3)

    Completed games receive None for all prediction columns.

    This function is designed to be called by cache_builder.py during the
    nightly analytics pre-computation pipeline.

    Args:
        schedule_df: The enriched schedule DataFrame from analysis_service.
        games_df: The full nfl_games DataFrame (all seasons, for Elo history).
        season: The target season year.

    Returns:
        The schedule DataFrame with prediction columns added.
    """
    svc = PredictionService()
    svc.initialize(games_df, season)

    pred_winners = []
    pred_confs = []
    pred_ats = []

    for _, row in schedule_df.iterrows():
        result = row.get("result")
        home = row.get("home_team", "")
        away = row.get("away_team", "")

        # Only predict unplayed games
        is_unplayed = (pd.isna(result) or result == -1000)
        if not is_unplayed or not home or not away:
            pred_winners.append(None)
            pred_confs.append(None)
            pred_ats.append(None)
            continue

        prediction = svc.game_win_probability(home, away)
        home_prob = prediction["home_win_prob"]

        if home_prob >= 0.5:
            winner = home
            confidence = home_prob
        else:
            winner = away
            confidence = 1.0 - home_prob

        # Clamp confidence to 50-99% range for display
        conf_pct = round(min(99.0, max(50.0, confidence * 100)), 1)

        # ATS pick: take the underdog if spread is small (<=3), else favorite
        spread = row.get("spread_line")
        if pd.notna(spread):
            try:
                spread_val = float(spread)
                if abs(spread_val) <= 3:
                    # Pick underdog ATS
                    ats = away if spread_val < 0 else home
                else:
                    ats = winner
            except (ValueError, TypeError):
                ats = winner
        else:
            ats = winner

        pred_winners.append(winner)
        pred_confs.append(conf_pct)
        pred_ats.append(ats)

    schedule_df = schedule_df.copy()
    schedule_df["pred_winner"] = pred_winners
    schedule_df["pred_su_conf"] = pred_confs
    schedule_df["pred_ats_pick"] = pred_ats

    return schedule_df
