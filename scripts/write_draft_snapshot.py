#!/usr/bin/env python3
"""write_draft_snapshot.py -- copy one season's preseason_predictions into
draft_snapshot_predictions, locking it once that season's real draft has
started.

Run as a step of scripts/refresh_preseason.py, right after "Season
Projection" writes fresh preseason_predictions rows. All copy/lock logic
lives in services.db_service.sync_draft_snapshot_for_season() -- this script
is only a CLI entry point, so the admin-triggered
POST /api/admin/draft_snapshot/sync endpoint (routes/admin_routes.py) can
call the identical function without duplicating the rule. See
docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.

Usage:
    python scripts/write_draft_snapshot.py --season 2026
"""
import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Must be set before importing services.db_service -- see CLAUDE.md's
# "any script that writes to Firestore must force USE_LOCAL_DATA=False" gotcha.
os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import get_db, sync_draft_snapshot_for_season


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    args = ap.parse_args()

    if get_db() is None:
        print(f"  [error] no Firestore connection -- draft_snapshot_predictions NOT synced for season={args.season}")
        sys.exit(1)

    result = sync_draft_snapshot_for_season(args.season)
    if result["written"] > 0:
        state = "locked (draft has started)" if result["locked"] else "unlocked"
        print(f"  [ok]   draft_snapshot_predictions season={args.season} "
              f"({result['written']} teams written, {state})")
    else:
        print(f"  [skip] draft_snapshot_predictions season={args.season}: 0 teams written "
              f"(no preseason_predictions yet, or season already locked)")


if __name__ == "__main__":
    main()
