#!/usr/bin/env python3
"""Migrate analyst consensus out of preseason_predictions into consensus_projections.

One-shot. For 2017-2025, preseason_predictions.sources holds a per-analyst dict;
for 2026 it holds {'model': ...}. Only numeric-valued entries are consensus.

This copies. Deleting the migrated rows is a separate, gated step --
scripts/deprecate_preseason_consensus.py -- run only after consumers are
repointed at the resolver.

Usage:
    python scripts/migrate_consensus.py --dry-run
    python scripts/migrate_consensus.py --firestore
    python scripts/migrate_consensus.py --seasons 2017 2025 --firestore
"""
import argparse
import logging
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.consensus_service import CANONICAL_SOURCE_KEYS, numeric_sources  # noqa: E402
from services.db_service import get_collection_df, set_consensus_projections    # noqa: E402
from services.utils import normalize_team_abbr                                  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# The complete stored key set across 2017-2025, verified against the data.
SOURCE_KEY_MAP = {
    "BR": "br",
    "CBS": "cbs",
    "ESPN": "espn",
    "FPI": "fpi",
    "NFL": "nfl",
    "O/U": "vegas_ou",
    "PFF": "pff",
    "SI": "si",
    "Clay": "clay",
}


def map_source_key(stored: str) -> str:
    """Map a stored source name to its canonical key, or '' if unrecognized."""
    if stored in SOURCE_KEY_MAP:
        return SOURCE_KEY_MAP[stored]
    lowered = str(stored).strip().lower()
    return lowered if lowered in CANONICAL_SOURCE_KEYS else ""


def build_migration_rows(df):
    """Convert preseason_predictions rows into consensus rows.

    Returns (rows, errors). A non-empty errors list means the migration must
    abort -- an unrecognized source is never dropped silently.
    """
    rows, errors = [], []
    for _, row in df.iterrows():
        nums = numeric_sources(row.get("sources", {}))
        if not nums:
            continue  # model row, or nothing numeric -- not consensus

        mapped, bad = {}, []
        for stored_key, val in nums.items():
            canon = map_source_key(stored_key)
            if not canon:
                bad.append(stored_key)
            else:
                mapped[canon] = val

        if bad:
            errors.append(
                f"season {row.get('season')} team {row.get('team')}: "
                f"unrecognized source(s) {sorted(bad)}"
            )
            continue

        rows.append({
            "season": int(row["season"]),
            "team": normalize_team_abbr(str(row["team"])),
            "sources": mapped,
            "as_of": None,  # historical: original capture date unknown
        })
    return rows, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs=2, type=int, metavar=("START", "END"),
                    default=[2017, 2025])
    ap.add_argument("--firestore", action="store_true", help="Write to Firestore")
    ap.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    args = ap.parse_args()

    start, end = args.seasons
    df = get_collection_df("preseason_predictions")
    if df.empty:
        log.error("preseason_predictions is empty -- nothing to migrate.")
        sys.exit(1)

    df = df[(df["season"] >= start) & (df["season"] <= end)]
    rows, errors = build_migration_rows(df)

    if errors:
        log.error("Migration aborted -- %d unrecognized source(s):", len(errors))
        for e in errors:
            log.error("  %s", e)
        log.error("Add the source to data/consensus_sources.json and SOURCE_KEY_MAP, then rerun.")
        sys.exit(1)

    by_season = {}
    for r in rows:
        by_season.setdefault(r["season"], []).append(r)

    for season in sorted(by_season):
        season_rows = by_season[season]
        srcs = sorted({k for r in season_rows for k in r["sources"]})
        log.info("%s: %d teams, sources %s", season, len(season_rows), srcs)
        if args.dry_run or not args.firestore:
            continue
        set_consensus_projections(season, season_rows)

    log.info("Total: %d rows across %d seasons.", len(rows), len(by_season))
    if args.dry_run or not args.firestore:
        log.info("Nothing written (pass --firestore to commit).")


if __name__ == "__main__":
    main()
