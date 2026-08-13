#!/usr/bin/env python3
"""Seed analyst consensus for one season from a hand-maintained CSV.

The CSV is spreadsheet-shaped for direct paste out of Excel:

    team,br,fpi,si,vegas_ou,clay
    BUF,12,10.6,12,11.5,11.9

Blank cells mean "this source did not publish a number for this team" and are
excluded from the derived statistics rather than counted as zero.

Usage:
    python scripts/seed_consensus.py --season 2026 --dry-run
    python scripts/seed_consensus.py --season 2026 --firestore
"""
import argparse
import logging
import pathlib
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.consensus_service import CANONICAL_SOURCE_KEYS      # noqa: E402
from services.db_service import set_consensus_projections         # noqa: E402
from services.utils import normalize_team_abbr                    # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"

ALL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN",
    "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS",
]

MIN_WINS, MAX_WINS = 0.0, 17.0


def validate_and_build(df: pd.DataFrame, season: int):
    """Validate the CSV frame and build consensus rows.

    Returns (rows, errors). A non-empty errors list means abort -- a partially
    seeded season is worse than an unseeded one.
    """
    errors = []

    if "team" not in df.columns:
        return [], ["CSV has no 'team' column"]

    source_cols = [c for c in df.columns if c != "team"]
    unknown_cols = [c for c in source_cols if c not in CANONICAL_SOURCE_KEYS]
    if unknown_cols:
        errors.append(
            f"unknown source column(s) {sorted(unknown_cols)} -- "
            f"add them to data/consensus_sources.json first"
        )

    df = df.copy()
    df["team"] = df["team"].apply(lambda t: normalize_team_abbr(str(t).strip()))

    seen = set(df["team"])
    missing = sorted(set(ALL_TEAMS) - seen)
    if missing:
        errors.append(f"missing team(s): {missing}")
    unknown_teams = sorted(seen - set(ALL_TEAMS))
    if unknown_teams:
        errors.append(f"unknown team(s): {unknown_teams}")

    if errors:
        return [], errors

    today = date.today().isoformat()
    rows = []
    for _, row in df.iterrows():
        team = row["team"]
        sources = {}
        for col in source_cols:
            val = row.get(col)
            if pd.isna(val):
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                errors.append(f"{team}/{col}: non-numeric value {val!r}")
                continue
            if not (MIN_WINS <= fval <= MAX_WINS):
                errors.append(f"{team}/{col}: {fval} outside {MIN_WINS}-{MAX_WINS} wins")
                continue
            sources[col] = fval

        if not sources:
            errors.append(f"{team}: no source values -- every column is blank")
            continue

        rows.append({"season": season, "team": team, "sources": sources, "as_of": today})

    if errors:
        return [], errors
    return rows, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--firestore", action="store_true", help="Write to Firestore")
    ap.add_argument("--dry-run", action="store_true", help="Validate only")
    args = ap.parse_args()

    csv_path = DATA_DIR / f"consensus_{args.season}.csv"
    if not csv_path.exists():
        log.error("Not found: %s", csv_path)
        sys.exit(1)

    df = pd.read_csv(csv_path)
    rows, errors = validate_and_build(df, args.season)

    if errors:
        log.error("Validation failed -- nothing written:")
        for e in errors:
            log.error("  %s", e)
        sys.exit(1)

    srcs = sorted({k for r in rows for k in r["sources"]})
    log.info("%s: %d teams validated, sources %s", args.season, len(rows), srcs)

    if args.dry_run or not args.firestore:
        log.info("Nothing written (pass --firestore to commit).")
        return

    set_consensus_projections(args.season, rows)
    log.info("Seeded %s. Run scripts/refresh_local_pkls.py to update the local mirror.", args.season)


if __name__ == "__main__":
    main()
