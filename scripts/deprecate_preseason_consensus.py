#!/usr/bin/env python3
"""Delete migrated consensus rows from preseason_predictions.

Final step of the schema deprecation, and the only irreversible one. Run ONLY
after scripts/migrate_consensus.py --firestore has succeeded AND draft_service
and recap_service are confirmed working on get_season_projection().

After this, preseason_predictions means model output and nothing else.

Two independent safeguards stand in front of the delete:
  1. A gate that refuses to touch any season whose rows are not already present
     in consensus_projections.
  2. A JSON backup of every queued document -- id and complete field contents,
     read straight from Firestore -- written and flushed before the first
     delete. If the backup cannot be written, nothing is deleted.

Usage:
    python scripts/deprecate_preseason_consensus.py --dry-run
    python scripts/deprecate_preseason_consensus.py --confirm
"""
import argparse
import datetime
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.consensus_service import numeric_sources                    # noqa: E402
from services.data_service import get_consensus_projections               # noqa: E402
from services.db_service import get_collection_df, get_db                 # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

COLLECTION = "preseason_predictions"
BACKUP_DIR = pathlib.Path(__file__).parent.parent / ".local_db"


def find_consensus_doc_ids(df) -> list:
    """Doc ids of rows whose sources dict holds analyst numbers."""
    if df is None or len(df) == 0 or "sources" not in getattr(df, "columns", []):
        return []
    ids = []
    for _, row in df.iterrows():
        if numeric_sources(row.get("sources", {})):
            ids.append(f"{int(row['season'])}_{row['team']}")
    return ids


def backup_path(now=None) -> pathlib.Path:
    """Timestamped backup file inside .local_db/."""
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    return BACKUP_DIR / f"backup_preseason_consensus_{stamp}.json"


def fetch_documents(db, doc_ids) -> list:
    """Read each queued document from Firestore as {"id", "data"}.

    Read by reference rather than reusing the DataFrame so the backup holds the
    documents exactly as stored, including any field the DataFrame flattened.
    """
    records = []
    col = db.collection(COLLECTION)
    for doc_id in doc_ids:
        snap = col.document(doc_id).get()
        data = snap.to_dict() if snap is not None else None
        records.append({"id": doc_id, "data": data})
    return records


def write_backup(records, path) -> pathlib.Path:
    """Write and flush the backup. Raises if it cannot be written."""
    path = pathlib.Path(path)
    payload = {
        "collection": COLLECTION,
        "created_at": datetime.datetime.now().isoformat(),
        "count": len(records),
        "documents": records,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
        f.flush()
    return path.resolve()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true",
                    help="Required to actually delete")
    args = ap.parse_args()

    df = get_collection_df(COLLECTION)
    doc_ids = find_consensus_doc_ids(df)
    if not doc_ids:
        log.info("No consensus rows found in preseason_predictions. Nothing to do.")
        return

    seasons = sorted({int(d.split("_")[0]) for d in doc_ids})
    log.info("Found %d consensus rows across seasons %s", len(doc_ids), seasons)

    # Safety gate: refuse to delete anything not already migrated.
    for season in seasons:
        migrated = get_consensus_projections(season)
        expected = sum(1 for d in doc_ids if d.startswith(f"{season}_"))
        if len(migrated) < expected:
            log.error(
                "Season %s: consensus_projections has %d teams but %d rows are "
                "queued for deletion. Run migrate_consensus.py --firestore first.",
                season, len(migrated), expected,
            )
            sys.exit(1)
        log.info("  %s verified: %d migrated rows present", season, len(migrated))

    if args.dry_run or not args.confirm:
        log.info("Dry run -- nothing deleted. Pass --confirm to proceed.")
        return

    db = get_db()
    if db is None:
        log.error("No database connection.")
        sys.exit(1)

    # Backup before the first delete. An unwritable backup aborts the run.
    try:
        records = fetch_documents(db, doc_ids)
        path = write_backup(records, backup_path())
    except Exception as e:
        log.error("Backup failed (%s). Nothing deleted.", e)
        sys.exit(1)

    log.info("=" * 70)
    log.info("BACKUP WRITTEN: %s", path)
    log.info("  %d documents, %d bytes", len(records), path.stat().st_size)
    log.info("=" * 70)

    batch = db.batch()
    for i, doc_id in enumerate(doc_ids, start=1):
        batch.delete(db.collection(COLLECTION).document(doc_id))
        if i % 400 == 0:
            batch.commit()
            batch = db.batch()
    if len(doc_ids) % 400 != 0:
        batch.commit()

    log.info("Deleted %d rows. Run scripts/refresh_local_pkls.py to update the mirror.",
             len(doc_ids))
    log.info("Backup of the deleted documents: %s", path)


if __name__ == "__main__":
    main()
