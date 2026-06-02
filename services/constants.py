# Sentinel win-total for undrafted/unowned teams in standings calculations.
# Chosen to sort below any realistic win total (max 17 wins in a season),
# so undrafted slots always appear at the bottom without special-case logic.
UNDRAFTED_SENTINEL = -1000

# Ensemble blend weights — tuned empirically; see reports/nn_weekly_accuracy.csv.
# NN captures non-linear interactions; LR provides stability on sparse data;
# XGB fills the gap on structured tabular signals. Must sum to 1.0.
NN_WEIGHT  = 0.45
XGB_WEIGHT = 0.20
LR_WEIGHT  = 0.35

# Password complexity — enforced in auth_routes.py, admin_routes.py.
# 12+ chars, must include uppercase, lowercase, digit, and special character.
PASSWORD_COMPLEXITY_RE = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"

# Probability clipping — prevents model from predicting < 2% or > 98% win probability.
PROB_CLIP_MIN = 0.02
PROB_CLIP_MAX = 0.98

# Elo/spread conversion — used identically across prediction services and scripts.
ELO_TO_SPREAD = 25.0        # Elo point difference ÷ this = point spread equivalent
SPREAD_TO_PROB_SCALE = 7.5  # logistic scale: spread ÷ this → win probability

# Monte Carlo simulation — game margin sampling and state update tuning.
# MC_MARGIN_STD: std dev of NFL game margin distribution (~13 points real-world).
# MC_EPA_SCALE: EPA nudge per point of simulated margin (tune if spread is too narrow/wide).
# MC_EPA_RUSH_WEIGHT: rush EPA updates at half the weight of passing EPA.
MC_MARGIN_STD      = 13.0
MC_EPA_SCALE       = 0.004
MC_EPA_RUSH_WEIGHT = 0.5

# Pick boundaries for each draft round (inclusive both ends).
# 10 players × 3 rounds = 30 total picks. Update if pool size ever changes.
DRAFT_ROUNDS = {
    1: (1, 10),
    2: (11, 20),
    3: (21, 30),
}

# Ordered sort columns for apply_tiebreakers().
# TotalWins is primary; the six derived columns break ties in priority order.
# All columns sort descending — more wins / better differential = higher rank.
TIEBREAKER_SORT_COLS = [
    "TotalWins",
    "Tiebreaker1_WorstTeamWins",
    "Tiebreaker2_2ndWorstTeamWins",
    "Tiebreaker3_BestTeamWins",
    "Tiebreaker4_WorstTeamPtDiff",
    "Tiebreaker5_2ndWorstTeamPtDiff",
    "Tiebreaker6_BestTeamPtDiff",
]
