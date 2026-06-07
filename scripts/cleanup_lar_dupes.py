"""
One-time cleanup: delete stale LAR-keyed Rams game documents from Firestore nfl_games.

Background: nflverse changed the Rams' abbreviation from LAR to LA. Daily sync runs
created both LAR-ID and LA-ID documents in Firestore for every 2026 Rams game. The
LA-keyed documents are correct; the LAR-keyed ones are stale duplicates.

Run once:
    python scripts/cleanup_lar_dupes.py

After running, rebuild local pickles:
    python scripts/refresh_local_pkls.py
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.db_service import get_db

def main():
    db = get_db()
    if not db:
        print("No Firestore connection. Set FIREBASE_CREDENTIALS and retry.")
        sys.exit(1)

    col = db.collection("nfl_games")
    stale = col.where("home_team", "==", "LA").stream()

    to_delete = []
    for doc in stale:
        if "LAR" in doc.id:
            to_delete.append(doc.id)

    # Also catch away_team=LA with LAR in id
    stale2 = col.where("away_team", "==", "LA").stream()
    for doc in stale2:
        if "LAR" in doc.id and doc.id not in to_delete:
            to_delete.append(doc.id)

    if not to_delete:
        print("No stale LAR documents found.")
        return

    print(f"Found {len(to_delete)} stale document(s) to delete:")
    for doc_id in sorted(to_delete):
        print(f"  {doc_id}")

    confirm = input(f"\nDelete {len(to_delete)} document(s)? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    batch = db.batch()
    for doc_id in to_delete:
        batch.delete(col.document(doc_id))
    batch.commit()
    print(f"Deleted {len(to_delete)} stale LAR documents.")
    print("Run: python scripts/refresh_local_pkls.py")

if __name__ == "__main__":
    main()
