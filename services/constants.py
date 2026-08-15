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
ELO_TO_SPREAD = 26.2        # Elo point difference / this = point spread equivalent
SPREAD_TO_PROB_SCALE = 6.94  # logistic scale: spread / this -> win probability

# Elo dynamics for the Monte Carlo season simulation (NNProjectionEngine).
# Must match scripts/compute_elo.py's K/HFA/MoV exactly: that script produces
# rawdata/elo_computed.csv, i.e. the tm_elo_pre/opp_elo_pre/elo_diff values the
# NN/XGB/LR ensemble was actually trained on. services/prediction_service.py's
# ELO_K=20.6 / ELO_HOME_ADVANTAGE=41.5 are a SEPARATE, independently-calibrated
# Elo+Pythagorean engine (see calibrate_elo_constants.py) for a different
# prediction path — do not reuse those here. Using them previously caused the
# simulation to evolve elo_diff ~3-4x faster per game than the models were
# trained to interpret, pushing multi-week simulated seasons out of the
# ensemble's training distribution (verified empirically: replaying 2024's
# real results under each rule set gives final-week Elo std of 38 vs 112).
ELO_SIM_K = 12.0
ELO_SIM_HFA = 15.0
ELO_SIM_MOV_MIN = 0.5
ELO_SIM_MOV_MAX = 2.0

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

# Preseason simulation spread tuning.
# PRESEASON_ELO_BOOST_MAX: maximum Elo points added/subtracted based on profile quality.
# ±200 gives top-profiled teams ~14 projected wins and bottom-profiled ~3.
# Increase to widen the win band, decrease to narrow it.
PRESEASON_ELO_BOOST_MAX = 200

# Weights for the profile composite that drives the Elo adjustment.
# Defensive dimensions (def_pass_epa, def_rush_epa) are sign-flipped before
# weighting so a better defense contributes positively to the composite.
PRESEASON_ELO_WEIGHTS = {
    "qb_tier":      0.30,
    "off_pass_epa": 0.20,
    "def_pass_epa": 0.20,
    "dl_perf":      0.15,
    "ol_av":        0.10,
    "off_rush_epa": 0.03,
    "def_rush_epa": 0.02,
}
