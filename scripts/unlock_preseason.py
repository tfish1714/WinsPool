#!/usr/bin/env python3
"""unlock_preseason.py -- manual recovery after a draft RESET or pick UNDO
that happened after the season's draft was complete.

Once a draft completes, winspool-predict-daily locks preseason_predictions
and writes season_projections / season_projection_history. If the draft is
then reset (draft_results rows deleted), the job returns to its pre-draft
branch but the locked preseason docs are skipped forever and the results-aware
docs are stale. This script (a) sets locked=False on that season's
preseason_predictions docs (values untouched) and (b) deletes that season's
season_projections and season_projection_history docs.

DEFAULT IS A DRY RUN. Pass --firestore to write. It REFUSES (exit 1) if the
season's draft is currently complete, unless --i-know-the-draft-is-complete.

Usage:
    python scripts/unlock_preseason.py --season 2026
    python scripts/unlock_preseason.py --season 2026 --firestore
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.db_service import (
    get_collection_df, require_db,
    unlock_preseason_predictions, delete_season_projections,
)
from services.draft_state import draft_is_complete


def build_parser():
    ap = argparse.ArgumentParser(description="Unlock preseason_predictions and clear live projections after a draft reset")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--firestore", action="store_true",
                    help="Actually write to Firestore (default: dry run)")
    ap.add_argument("--i-know-the-draft-is-complete", action="store_true",
                    dest="i_know_the_draft_is_complete",
                    help="Allow unlocking even though the draft is complete")
    return ap


def run(season, write, allow_complete, draft_results, draft_order, rules) -> int:
    if draft_is_complete(season, draft_results, draft_order, rules) and not allow_complete:
        print(f"REFUSING: the {season} draft is complete. Unlocking now would let the "
              f"daily job overwrite a real completed draft's preseason projections. "
              f"Reset the draft first, or pass --i-know-the-draft-is-complete.")
        return 1
    if not write:
        print(f"DRY RUN (season {season}): would set locked=False on preseason_predictions "
              f"docs and delete season_projections + season_projection_history docs. "
              f"Re-run with --firestore to apply.")
        return 0
    n_unlocked = unlock_preseason_predictions(season)
    n_deleted = delete_season_projections(season)
    print(f"[ok] season {season}: {n_unlocked} preseason_predictions docs unlocked, "
          f"{n_deleted} season projection docs deleted.")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.firestore:
        # Forces USE_LOCAL_DATA=False and exits if there is no client.
        require_db()
    filt = [("season", "==", args.season)]
    return run(
        args.season, args.firestore, args.i_know_the_draft_is_complete,
        get_collection_df("draft_results", filters=filt),
        get_collection_df("draft_order", filters=filt),
        get_collection_df("draft_order_rules", filters=filt),
    )


if __name__ == "__main__":
    sys.exit(main())
