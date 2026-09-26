"""
upload_configfiles.py — Pool configuration data upload to Firestore.

Reads CSVs from the project root and upserts all rows into Firestore.
After a fully successful upload, the CSV files are deleted from the project.

Usage:
    python scripts/upload_configfiles.py

Safe to re-run: existing docs with matching keys are skipped (upsert via set/merge).
"""
import sys
import csv
import os
import pathlib
import logging

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.db_service import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# --- Firebase init -----------------------------------------------------------
def _init_firebase():
    """Return the shared Firestore client from services.db_service.get_db()."""
    # get_db() returns None whenever USE_LOCAL_DATA is true (repo CLAUDE.md
    # gotcha), so a Firestore-writing script must force it off first.
    os.environ["USE_LOCAL_DATA"] = "False"
    db = get_db()
    if db is None:
        log.error("No FIREBASE_CREDENTIALS env var and no firebase_credentials.json found.")
        sys.exit(1)
    return db

# --- CSV → Firestore mapping -------------------------------------------------
PROJECT_ROOT = pathlib.Path(__file__).parent.parent

# (csv_filename, firestore_collection, doc_id_fields)
UPLOAD_MAP = [
    ("WinsPoolPlayers.csv",           "players",            ["playerId"]),
    ("WinsPoolDraftOrder.csv",        "draft_order",        ["season", "draftOrder"]),
    ("WinsPoolDraftResults.csv",      "draft_results",      ["season", "draftPick"]),
    ("WinsPoolDraftOrderRules.csv",   "draft_order_rules",  ["season", "rule"]),
]


def cast_value(val: str):
    """Parse a string as int, float, or leave as string."""
    val = val.strip()
    if val == "":
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def upload_csv(db, csv_file: pathlib.Path, collection: str, id_fields: list) -> int:
    """Upsert all rows of a CSV to a Firestore collection. Returns count uploaded."""
    if not csv_file.exists():
        log.warning(f"  CSV not found: {csv_file} — skipping")
        return 0

    col_ref = db.collection(collection)
    batch = db.batch()
    count = 0
    committed = 0

    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not any(row.values()):
                continue  # skip blank rows

            doc = {}
            for k, v in row.items():
                if not k: continue
                if k == 'draftorder': 
                    k = 'draftOrder'
                doc[k] = cast_value(v)

            try:
                doc_id = "_".join(str(doc[field]) for field in id_fields)
            except KeyError:
                doc_id = None

            doc_ref = col_ref.document(doc_id) if doc_id else col_ref.document()
            # merge=True means existing docs are updated, not overwritten
            batch.set(doc_ref, doc, merge=True)
            count += 1

            if count % 400 == 0:
                batch.commit()
                committed += count
                log.info(f"    ...committed {committed} records")
                batch = db.batch()

    if count % 400 != 0:
        batch.commit()
        committed += count % 400

    log.info(f"  ✓ {committed} rows upserted into '{collection}'")
    return committed


def main():
    log.info("=" * 60)
    log.info("WinsPool CSV → Firestore migration")
    log.info("=" * 60)

    db = _init_firebase()

    all_ok = True
    uploaded_files = []

    for csv_name, collection, id_fields in UPLOAD_MAP:
        csv_path = PROJECT_ROOT / csv_name
        log.info(f"\n[{collection}] Uploading {csv_name}...")
        try:
            n = upload_csv(db, csv_path, collection, id_fields)
            if n > 0:
                uploaded_files.append(csv_path)
        except Exception as e:
            log.error(f"  ✗ Failed: {e}")
            all_ok = False

    if all_ok and uploaded_files:
        log.info("\nAll uploads successful — keeping CSV files in project.")
        # for f in uploaded_files:
        #     try:
        #         f.unlink()
        #         log.info(f"  Deleted: {f.name}")
        #     except Exception as e:
        #         log.warning(f"  Could not delete {f.name}: {e}")
    elif not all_ok:
        log.error("\nSome uploads failed. CSV files NOT deleted. Fix errors and re-run.")
        sys.exit(1)

    log.info("\nDone. Run `python scripts/refresh_local_pkls.py` to update local cache.")


if __name__ == "__main__":
    main()
