"""scripts/rank_position_groups.py -- Rank teams by preseason position-group strength.

Diagnostic view over the same data that feeds the preseason Elo boost in
NNProjectionEngine._build_initial_state() (services/nn_projection_engine.py).
Read-only: computes nothing new, just re-presents compute_preseason_player_profiles()
output as ranks/z-scores so a surprising team projection (e.g. "why is ATL's DL
rated #1?") can be sanity-checked without re-deriving the math by hand.

Replicates -- does not import -- the composite formula from
NNProjectionEngine._build_initial_state(). If PRESEASON_ELO_WEIGHTS or that
formula changes, compute_dim_zscores()/compute_composite() below must be
updated to match.

Usage:
    python scripts/rank_position_groups.py --season 2026
    python scripts/rank_position_groups.py --season 2026 --team ATL
    python scripts/rank_position_groups.py --season 2026 --dim dl_perf
    python scripts/rank_position_groups.py --season 2026 --output reports/atl_ranks.csv
"""
import argparse
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import numpy as np

from services.constants import PRESEASON_ELO_WEIGHTS, PRESEASON_ELO_BOOST_MAX
from services.nn_feature_engine import compute_preseason_player_profiles

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"

# Defensive dims: lower raw EPA-allowed is better, so their z-score is flipped
# before weighting -- same convention as NNProjectionEngine._build_initial_state().
_DEF_DIMS = {"def_pass_epa", "def_rush_epa"}


def compute_dim_zscores(profiles: dict) -> dict:
    """{team: {dim: z}} for each PRESEASON_ELO_WEIGHTS dim, sign-flipped for
    defensive dims so higher z is always better."""
    teams = list(profiles.keys())
    dims = list(PRESEASON_ELO_WEIGHTS.keys())
    vals = {d: np.array([float(profiles[t].get(d, 0.0)) for t in teams]) for d in dims}
    mu = {d: float(np.mean(v)) for d, v in vals.items()}
    sig = {d: max(float(np.std(v)), 1e-6) for d, v in vals.items()}

    z = {}
    for t in teams:
        z[t] = {}
        for d in dims:
            zval = (float(profiles[t].get(d, mu[d])) - mu[d]) / sig[d]
            if d in _DEF_DIMS:
                zval = -zval
            z[t][d] = zval
    return z


def compute_composite(dim_z: dict) -> dict:
    """Weighted sum of per-dim z-scores, re-z-scored across the league and
    scaled to +/-PRESEASON_ELO_BOOST_MAX -- mirrors the second normalization
    pass in NNProjectionEngine._build_initial_state() (a weighted sum of
    partially-correlated z-scored dims has std < 1, so the composite itself
    needs re-normalizing before the +/-2sigma clip means anything)."""
    teams = list(dim_z.keys())
    raw = {t: sum(w * dim_z[t][d] for d, w in PRESEASON_ELO_WEIGHTS.items()) for t in teams}
    raw_mu = float(np.mean(list(raw.values())))
    raw_sig = max(float(np.std(list(raw.values()))), 1e-6)

    composite = {}
    for t in teams:
        cz = (raw[t] - raw_mu) / raw_sig
        elo_boost = float(np.clip(cz, -2.0, 2.0) / 2.0 * PRESEASON_ELO_BOOST_MAX)
        composite[t] = {"composite_z": cz, "elo_boost": elo_boost}
    return composite


def _rank(values: dict) -> dict:
    """{team: value} -> {team: rank}, rank 1 = highest value."""
    ordered = sorted(values.items(), key=lambda kv: -kv[1])
    return {t: i + 1 for i, (t, _) in enumerate(ordered)}


def write_leaderboard(profiles: dict, dim_z: dict, composite: dict, out) -> None:
    teams = list(profiles.keys())
    comp_rank = _rank({t: composite[t]["composite_z"] for t in teams})
    dims = list(PRESEASON_ELO_WEIGHTS.keys())
    dim_ranks = {d: _rank({t: dim_z[t][d] for t in teams}) for d in dims}

    writer = csv.writer(out)
    header = ["composite_rank", "team", "composite_z", "implied_elo_boost"]
    for d in dims:
        header += [f"{d}_z", f"{d}_rank"]
    writer.writerow(header)

    for t in sorted(teams, key=lambda t: comp_rank[t]):
        row = [comp_rank[t], t,
               round(composite[t]["composite_z"], 4),
               round(composite[t]["elo_boost"], 2)]
        for d in dims:
            row += [round(dim_z[t][d], 4), dim_ranks[d][t]]
        writer.writerow(row)


def write_team_breakdown(team: str, profiles: dict, dim_z: dict, composite: dict, out) -> None:
    if team not in profiles:
        raise SystemExit(f"Team '{team}' not found. Known teams: {sorted(profiles.keys())}")
    dims = list(PRESEASON_ELO_WEIGHTS.keys())
    n = len(profiles)
    dim_ranks = {d: _rank({t: dim_z[t][d] for t in profiles}) for d in dims}

    rows = []
    for d in dims:
        w = PRESEASON_ELO_WEIGHTS[d]
        z = dim_z[team][d]
        rows.append({
            "dim": d, "weight": w, "rank": dim_ranks[d][team], "of": n,
            "z_score": z, "contribution": w * z,
        })
    rows.sort(key=lambda r: -abs(r["contribution"]))

    writer = csv.writer(out)
    writer.writerow(["dim", "weight", "rank", "of", "z_score", "contribution"])
    for r in rows:
        writer.writerow([r["dim"], r["weight"], r["rank"], r["of"],
                          round(r["z_score"], 4), round(r["contribution"], 4)])
    writer.writerow([])
    writer.writerow(["composite_z", round(composite[team]["composite_z"], 4)])
    writer.writerow(["implied_elo_boost", round(composite[team]["elo_boost"], 2)])


def write_dim_ranking(dim: str, profiles: dict, dim_z: dict, out) -> None:
    teams = list(profiles.keys())
    ranks = _rank({t: dim_z[t][dim] for t in teams})

    writer = csv.writer(out)
    writer.writerow(["rank", "team", "raw_value", "z_score"])
    for t in sorted(teams, key=lambda t: ranks[t]):
        writer.writerow([ranks[t], t, round(profiles[t].get(dim, 0.0), 4), round(dim_z[t][dim], 4)])


def main():
    parser = argparse.ArgumentParser(
        description="Rank teams by preseason position-group strength (CSV output)."
    )
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--team", type=str, default=None,
                         help="Show one team's per-dimension rank/z-score breakdown.")
    parser.add_argument("--dim", type=str, default=None,
                         choices=list(PRESEASON_ELO_WEIGHTS.keys()),
                         help="Show all teams ranked on a single dimension.")
    parser.add_argument("--output", type=str, default=None,
                         help="Write CSV to this path instead of stdout.")
    args = parser.parse_args()

    if args.team and args.dim:
        raise SystemExit("--team and --dim are mutually exclusive.")

    profiles = compute_preseason_player_profiles(args.season, RAWDATA_DIR)
    if not profiles:
        raise SystemExit(
            f"No preseason profiles for season {args.season} -- check "
            f"rawdata/rosters/roster_{args.season}.csv and "
            f"rawdata/depth_charts/depth_charts_{args.season}.csv exist."
        )

    dim_z = compute_dim_zscores(profiles)
    composite = compute_composite(dim_z)

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    try:
        if args.team:
            write_team_breakdown(args.team.upper(), profiles, dim_z, composite, out)
        elif args.dim:
            write_dim_ranking(args.dim, profiles, dim_z, out)
        else:
            write_leaderboard(profiles, dim_z, composite, out)
    finally:
        if args.output:
            out.close()
            print(f"Wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
