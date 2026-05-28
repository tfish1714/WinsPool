# Sentinel used in the games DataFrame to indicate an undrafted team or unplayed game result.
UNDRAFTED_SENTINEL = -1000

# Ensemble blend weights (NN+XGB+LR). Must sum to 1.0.
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
