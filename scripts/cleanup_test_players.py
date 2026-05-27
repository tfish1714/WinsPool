#!/usr/bin/env python3
"""
cleanup_test_players.py — Delete StableNick / Stability Test User test records
from Firestore (and optionally local pkl).

Usage:
    python scripts/cleanup_test_players.py            # Delete from Firestore
    python scripts/cleanup_test_players.py --dry-run  # Preview only
    python scripts/cleanup_test_players.py --force-local  # Allow local-only mode

After deletion, run:
    python scripts/refresh_local_pkls.py
"""

import argparse
import os
import sys
import pathlib

# Ensure project root is importable
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Delete StableNick test players from Firestore.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be deleted without deleting anything.")
    parser.add_argument("--force-local", action="store_true",
                        help="Run even if USE_LOCAL_DATA=True (local pkl only mode).")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt (for CI/scripted use).")
    args = parser.parse_args()

    use_local = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"
    if use_local and not args.force_local:
        print("ERROR: USE_LOCAL_DATA=True — this script targets Firestore.")
        print("  Pass --force-local to run anyway (will still delete from Firestore if credentials present).")
        sys.exit(1)

    from services.db_service import get_db
    db = get_db()
    if db is None:
        use_local_env = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"
        if use_local_env and args.force_local:
            print("ERROR: USE_LOCAL_DATA=True prevents get_db() from returning a Firestore client.")
            print("  Unset USE_LOCAL_DATA before running with --force-local.")
        else:
            print("ERROR: Could not connect to Firestore. Check FIREBASE_CREDENTIALS env var.")
        sys.exit(1)

    # Query by nickName and fullName separately (Firestore doesn't support OR across fields natively)
    players_ref = db.collection("players")
    to_delete = []

    for doc in players_ref.stream():
        data = doc.to_dict()
        nick = (data.get("nickName") or "").strip()
        name = (data.get("fullName") or "").strip()
        if nick == "StableNick" or name == "Stability Test User":
            to_delete.append((doc.id, data.get("playerId"), data.get("fullName")))

    if not to_delete:
        print("No StableNick / Stability Test User records found.")
        return

    print(f"Found {len(to_delete)} test player(s) to delete:")
    for doc_id, player_id, full_name in to_delete:
        print(f"  doc_id={doc_id}  playerId={player_id}  fullName={full_name!r}")

    if args.dry_run:
        print("\n[DRY RUN] No deletions performed.")
        return

    if not args.yes:
        confirm = input(f"\nDelete {len(to_delete)} records from Firestore players collection? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return

    # Delete in batches of 500
    BATCH_SIZE = 500
    deleted = 0
    batch = db.batch()
    batch_count = 0
    current_batch_refs = []

    for doc_id, _, _ in to_delete:
        doc_ref = players_ref.document(doc_id)
        batch.delete(doc_ref)
        current_batch_refs.append(doc_ref)
        batch_count += 1
        deleted += 1
        if batch_count >= BATCH_SIZE:
            try:
                batch.commit()
                print(f"  Committed batch of {batch_count}...")
            except Exception as e:
                print(f"ERROR: Batch commit failed: {e}")
                print(f"  The following records may NOT have been deleted:")
                for doc_ref in current_batch_refs:
                    print(f"    {doc_ref.id}")
                sys.exit(1)
            batch = db.batch()
            current_batch_refs = []
            batch_count = 0

    if batch_count > 0:
        try:
            batch.commit()
        except Exception as e:
            print(f"ERROR: Batch commit failed: {e}")
            print(f"  The following records may NOT have been deleted:")
            for doc_ref in current_batch_refs:
                print(f"    {doc_ref.id}")
            sys.exit(1)

    print(f"\nDeleted {deleted} test player(s).")
    print("\nNext step: rebuild local pkl files:")
    print("  python scripts/refresh_local_pkls.py")


if __name__ == "__main__":
    main()
