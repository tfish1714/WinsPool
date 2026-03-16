# functions/main.py
from firebase_functions import firestore_fn
from firebase_admin import initialize_app, firestore
import google.cloud.firestore

initialize_app()

@firestore_fn.on_document_written(document="nfl_games/{game_id}")
def sync_metadata_games(event: firestore_fn.Event[firestore_fn.Change[firestore_fn.DocumentSnapshot | None]]) -> None:
    """Triggered when a game is created, updated, or deleted."""
    process_metadata_sync(event)

@firestore_fn.on_document_written(document="draft_results/{result_id}")
def sync_metadata_draft(event: firestore_fn.Event[firestore_fn.Change[firestore_fn.DocumentSnapshot | None]]) -> None:
    """Triggered when a draft result is created, updated, or deleted."""
    process_metadata_sync(event)

def process_metadata_sync(event):
    db = firestore.client()
    doc_ref = db.collection("metadata").document("dropdown_config")
    
    # Get the data from the triggering document
    new_data = event.data.after.to_dict() if event.data.after else None
    if not new_data:
        return # Deletion handled by full script or ignored for dropdown expansion
    
    season = new_data.get("season")
    week = new_data.get("week")
    game_type = new_data.get("game_type", "REG")
    result = new_data.get("result")
    
    if not season:
        return

    @google.cloud.firestore.transactional
    def update_in_transaction(transaction, ref, s, w, gt, res):
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            # If doc doesn't exist, we can't easily bootstrap from one trigger safely
            # without over-fetching. We assume it exists from the initial setup script.
            return

        current_meta = snapshot.to_dict()
        updated = False
        
        # 1. Update seasons
        available_seasons = set(current_meta.get("available_seasons", []))
        if s not in available_seasons:
            available_seasons.add(s)
            current_meta["available_seasons"] = sorted(list(available_seasons), reverse=True)
            updated = True
            
        # 2. Update weeks (only for REG)
        if gt == "REG" and w:
            weeks_by_season = current_meta.get("weeks_by_season", {})
            season_str = str(s)
            s_weeks = set(weeks_by_season.get(season_str, []))
            if w not in s_weeks:
                s_weeks.add(int(w))
                weeks_by_season[season_str] = sorted(list(s_weeks))
                current_meta["weeks_by_season"] = weeks_by_season
                updated = True

        # 3. Update Latest Season
        if s > current_meta.get("latest_season", 0):
            current_meta["latest_season"] = s
            updated = True
            
        # 4. Update Latest Week (Approximate logic)
        # In a trigger, we don't know if this is the absolute latest without a query,
        # but if we see a game with a result in a higher week, we can bump it.
        # This is a bit risky for "regressions" (manual score edits), so we only bump up.
        if gt == "REG" and res is not None and res != -1000:
            latest_weeks = current_meta.get("latest_week_by_season", {})
            current_latest = latest_weeks.get(season_str, 0)
            if w > current_latest:
                latest_weeks[season_str] = int(w)
                current_meta["latest_week_by_season"] = latest_weeks
                updated = True

        if updated:
            current_meta["last_updated"] = firestore.SERVER_TIMESTAMP
            transaction.update(ref, current_meta)

    transaction = db.transaction()
    update_in_transaction(transaction, doc_ref, season, week, game_type, result)
