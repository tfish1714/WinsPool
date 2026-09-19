#!/usr/bin/env python3
"""backfill_draft_snapshot.py -- one-time copy of every already-locked past
season's preseason_predictions into draft_snapshot_predictions.

Pure copy, not a recomputation: nothing rewrites a preseason_predictions doc
once locked=True (scripts/cache_builder.py stamps that on the last write of a
completed season), so every locked season's numbers are already frozen at the
source. This keeps every season-spanning "frozen" reader (draft results/
history views in particular) uniform -- always read draft_snapshot_predictions,
never branch on "is this season new enough to have a snapshot."

Usage:
    python scripts/backfill_draft_snapshot.py             # dry run, prints plan
    python scripts/backfill_draft_snapshot.py --live       # actually writes
"""
import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Must be set before importing services.db_service -- see CLAUDE.md's
# "any script that writes to Firestore must force USE_LOCAL_DATA=False" gotcha.
os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import get_collection_df, set_draft_snapshot_predictions

SNAPSHOT_FIELDS = ["projected_wins", "mean_wins", "std_dev", "floor", "p25", "p75", "ceiling"]


def find_locked_seasons(preds_df) -> list:
    """Seasons where every team's preseason_predictions row has locked=True.

    A season with even one unlocked team (still being refreshed) or with no
    `locked` column value at all (predict_season.py's manual writes never set
    it) is excluded -- only a season that is unambiguously frozen at the
    source is safe to copy.
    """
    if preds_df.empty or "season" not in preds_df.columns:
        return []
    locked_col = preds_df["locked"] if "locked" in preds_df.columns else False
    preds_df = preds_df.assign(_locked=locked_col.fillna(False) if hasattr(locked_col, "fillna") else locked_col)
    seasons = []
    for season, group in preds_df.groupby("season"):
        if bool(group["_locked"].all()):
            seasons.append(int(season))
    return sorted(seasons)


def run(dry_run: bool = True) -> dict:
    """Copy every fully-locked season's preseason_predictions into
    draft_snapshot_predictions with locked=True. Returns {season: docs_written}
    -- in a dry run this is what WOULD be written, and no write happens."""
    preds_df = get_collection_df("preseason_predictions")
    locked_seasons = find_locked_seasons(preds_df)

    result = {}
    for season in locked_seasons:
        season_df = preds_df[preds_df["season"] == season]
        model_version = season_df.iloc[0].get("model_version", "unknown")
        projections = {
            row["team"]: {field: row.get(field) for field in SNAPSHOT_FIELDS}
            for _, row in season_df.iterrows()
        }
        if dry_run:
            result[season] = len(projections)
            print(f"  [dry-run] season={season}: would write {len(projections)} teams, locked=True")
        else:
            n = set_draft_snapshot_predictions(season, projections, model_version=model_version, locked=True)
            result[season] = n
            print(f"  [ok] season={season}: wrote {n} teams, locked=True")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Actually write (default is dry-run)")
    args = ap.parse_args()
    run(dry_run=not args.live)


if __name__ == "__main__":
    main()
