import logging
import os
import sys
import pathlib
import json
import pandas as pd
import hashlib
import time
import bcrypt
from services.cache_service import clear_data_cache, _cache_key
import services.cache_service as _cache_svc

logger = logging.getLogger(__name__)

# Bcrypt work factor: 12 rounds = ~250ms per hash on modern hardware.
# Balances security against brute-force with acceptable login latency.
_BCRYPT_ROUNDS = 12


def get_password_hash(password: str) -> str:
    """Hash the password using bcrypt with a random salt.

    Returns a bcrypt hash string (60 characters, starting with '$2b$').
    """
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    ).decode("utf-8")


def _is_legacy_sha256(hashed_password: str) -> bool:
    """Detect legacy SHA-256 hex digests (64 hex chars, no '$' prefix)."""
    return (
        len(hashed_password) == 64
        and not hashed_password.startswith("$")
        and all(c in "0123456789abcdef" for c in hashed_password)
    )


# Password verification supports two hash formats transparently:
#   Legacy: 64-character lowercase hex string (SHA-256, no salt). These were
#           created before bcrypt was introduced. Identified by _is_legacy_sha256().
#   Modern: bcrypt hash starting with "$2b$" (12 rounds).
# When a legacy hash is verified successfully, the caller (auth_routes.py)
# is responsible for calling update_player_credentials() to upgrade the
# stored hash to bcrypt — the migration is transparent to the user.
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a stored hash.

    Supports both bcrypt hashes and legacy SHA-256 hex digests.
    When a legacy SHA-256 hash is verified successfully, the caller should
    upgrade the stored hash to bcrypt via update_player_credentials().

    Returns:
        True if the password matches the stored hash.
    """
    if not hashed_password:
        return False
    # Legacy migration path: SHA-256 hex digest (64 chars, no salt)
    if _is_legacy_sha256(hashed_password):
        return hashlib.sha256(plain_password.encode("utf-8")).hexdigest() == hashed_password
    # Standard bcrypt verification
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False

def _init_firebase():
    """
    Initialize Firebase Admin SDK from environment or local credentials file.

    Priority:
    1. FIREBASE_CREDENTIALS env var (base64-encoded JSON) — for Cloud Run / CI.
    2. firebase_credentials.json in project root — for local development.
    """
    import firebase_admin
    from firebase_admin import credentials, firestore
    if firebase_admin._apps:
        return firestore.client()

    creds_b64 = os.environ.get("FIREBASE_CREDENTIALS")
    if creds_b64:
        import base64, tempfile
        decoded = base64.b64decode(creds_b64).decode("utf-8")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(decoded)
        tmp.close()
        cred = credentials.Certificate(tmp.name)
        os.unlink(tmp.name)
    else:
        project_root = pathlib.Path(__file__).parent.parent
        creds_path = project_root / "firebase_credentials.json"
        if not creds_path.exists():
            logger.warning("No FIREBASE_CREDENTIALS env var and no firebase_credentials.json found.")
            return None
        cred = credentials.Certificate(str(creds_path))

    firebase_admin.initialize_app(cred)
    return firestore.client()


use_local_env = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"

# Only initialize Firebase when not in local-data mode
if not use_local_env:
    _init_firebase()


def get_db():
    if use_local_env:
        return None
    import firebase_admin
    if not firebase_admin._apps:
        _init_firebase()
    from firebase_admin import firestore
    return firestore.client()

def signal_data_update():
    """Signals all application instances to invalidate their caches."""
    if os.environ.get("USE_LOCAL_DATA", "False").lower() == "true":
        return
    try:
        db = get_db()
        if db:
            db.collection("metadata").document("cache_control").set({
                "last_update": time.time()
            })
    except Exception as e:
        logger.warning("Failed to signal remote cache update: %s", e)


def get_collection_df(collection_name: str, filters: list = None) -> pd.DataFrame:
    """
    Fetch a Firestore collection into a DataFrame.
    Falls back to local .pkl cache when USE_LOCAL_DATA=True.
    filters: list of (field, operator, value) tuples, e.g. [('season', '==', 2024)]
    """
    use_local = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"
    if use_local:
        pkl_path = pathlib.Path(".local_db") / f"{collection_name}.pkl"
        if pkl_path.exists():
            try:
                df = pd.read_pickle(pkl_path)
                if filters:
                    for col, op, val in filters:
                        if op == "==" and col in df.columns:
                            df = df[df[col] == val]
                return df
            except Exception as e:
                logger.warning("Failed to read %s: %s. Falling back to Firestore...", pkl_path, e)

    db = get_db()
    if db is None:
        return pd.DataFrame()

    query = db.collection(collection_name)
    if filters:
        from google.cloud.firestore_v1.base_query import FieldFilter
        for col, op, val in filters:
            query = query.where(filter=FieldFilter(col, op, val))

    docs = query.get()
    return pd.DataFrame([doc.to_dict() for doc in docs])


def update_player_cell(player_id: int, cell: str):
    """Update the phone number (cell) field on a player's document."""
    update_player_profile(str(player_id), {"cell": cell})

def get_player_role(player_id: str) -> str:
    """Helper to check player role (admin vs user) for secure operations."""
    if not player_id:
        return "user"
    player = get_player_by_id(player_id)
    if player:
        return player.get("role", "user")
    return "user"

def _get_players_df():
    """Return players DataFrame from warm in-memory cache or fall back to Firestore."""
    bundle = _cache_svc._DATA_CACHE.get(_cache_key(None))
    if bundle is not None:
        return getattr(bundle, "players", pd.DataFrame())
    return get_collection_df("players")

def get_player_by_email(email: str):
    """Retrieve a single player directly by their standardized email address."""
    players_df = _get_players_df()
    if not players_df.empty and "email" in players_df.columns:
        match = players_df[players_df["email"].astype(str).str.lower() == email.lower()]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None

def get_player_by_id(player_id: str):
    """Retrieve a single player directly by their ID."""
    players_df = _get_players_df()
    if not players_df.empty and "playerId" in players_df.columns:
        match = players_df[players_df["playerId"].astype(str) == str(player_id)]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None

def update_player_credentials(player_id: str, password_hash: str):
    """Stores the given password hash on the player document and resets lockout state."""
    update_player_profile(player_id, {
        "password_hash": password_hash,
        "failed_setup_attempts": 0,
        "lockout_until": None
    })

def increment_failed_setup_attempts(player_id: str, new_count: int, lockout_until: float = None):
    """Applies the 5-Attempt rate limiter lockouts physically onto the Database."""
    update_data = {"failed_setup_attempts": new_count}
    if lockout_until:
        update_data["lockout_until"] = lockout_until
    update_player_profile(player_id, update_data)

def _save_df_to_local(collection_name: str, df: pd.DataFrame):
    if os.environ.get("USE_LOCAL_DATA", "False").lower() != "true":
        return
    pkl_path = pathlib.Path(".local_db") / f"{collection_name}.pkl"
    pkl_path.parent.mkdir(exist_ok=True)
    df.to_pickle(pkl_path)

def add_player(full_name: str, nick_name: str, email: str, phone: str = ""):
    """Creates a new player with a unique numeric ID."""
    db = get_db()
    players_df = get_collection_df("players")
    
    max_id = 0
    if not players_df.empty:
        max_id = players_df["playerId"].max()
    
    new_id = int(max_id) + 1
    data = {
        "playerId": new_id,
        "fullName": full_name,
        "nickName": nick_name,
        "email": email.strip().lower(),
        "cell": phone.strip() if phone else "",
        "role": "user",
        "failed_setup_attempts": 0,
    }
    
    if db:
        db.collection("players").document(str(new_id)).set(data)
    
    # Update local df
    new_row = pd.DataFrame([data])
    players_df = pd.concat([players_df, new_row], ignore_index=True)
    _save_df_to_local("players", players_df)
    
    clear_data_cache()
    signal_data_update()
    return new_id


def add_draft_result(season: int, draft_pick: int, player_id: int, team: str, executed_by: str = None, time_taken_seconds: float = None):
    """Write a single draft pick result to Firestore and the local pkl cache."""
    doc_id = f"{season}_{draft_pick}"
    data = {
        "season": season,
        "draftPick": draft_pick,
        "playerId": player_id,
        "team": team,
    }
    if executed_by:
        data["executed_by"] = executed_by
    if time_taken_seconds is not None:
        data["time_taken_seconds"] = time_taken_seconds
        
    db = get_db()
    if db:
        db.collection("draft_results").document(doc_id).set(data)
    
    # Update local
    results_df = get_collection_df("draft_results")
    if not results_df.empty:
        results_df = results_df[~((results_df["season"] == season) & (results_df["draftPick"] == draft_pick))]
    results_df = pd.concat([results_df, pd.DataFrame([data])], ignore_index=True)
    _save_df_to_local("draft_results", results_df)

    clear_data_cache(season)

def delete_draft_pick(season: int, draft_pick: int):
    """Delete a specific draft pick document from Firestore and local pkl."""
    doc_id = f"{season}_{draft_pick}"
    db = get_db()
    if db:
        db.collection("draft_results").document(doc_id).delete()
    
    # Update local
    results_df = get_collection_df("draft_results")
    if not results_df.empty:
        results_df = results_df[~((results_df["season"] == season) & (results_df["draftPick"] == draft_pick))]
        _save_df_to_local("draft_results", results_df)

    clear_data_cache(season)

def delete_draft_results_for_season(season: int):
    """Delete all draft_results documents for the given season from Firestore and local pkl."""
    db = get_db()
    if db:
        from google.cloud.firestore_v1.base_query import FieldFilter
        docs = db.collection("draft_results").where(filter=FieldFilter("season", "==", season)).stream()
        batch = db.batch()
        count = 0
        for doc in docs:
            batch.delete(doc.reference)
            count += 1
        if count > 0:
            batch.commit()
    
    # Update local
    results_df = get_collection_df("draft_results")
    if not results_df.empty:
        results_df = results_df[results_df["season"] != season]
        _save_df_to_local("draft_results", results_df)

    clear_data_cache()

def delete_season_data(season: int):
    """Wipes draft_order, draft_order_rules, and draft_results for a season."""
    db = get_db()
    for col in ["draft_order", "draft_order_rules", "draft_results"]:
        if db:
            from google.cloud.firestore_v1.base_query import FieldFilter
            docs = db.collection(col).where(filter=FieldFilter("season", "==", season)).stream()
            batch = db.batch()
            count = 0
            for doc in docs:
                batch.delete(doc.reference)
                count += 1
            if count > 0:
                batch.commit()
        
        # Update local
        df = get_collection_df(col)
        if not df.empty:
            df = df[df["season"] != season]
            _save_df_to_local(col, df)

    clear_data_cache()
    signal_data_update()


def add_draft_order(season: int, draft_order: int, player_id: int):
    """Create a draft_order entry mapping a player to their draft position for a season."""
    doc_id = f"{season}_{draft_order}"
    data = {
        "season": season,
        "draftOrder": draft_order,
        "playerId": player_id,
    }
    db = get_db()
    if db:
        db.collection("draft_order").document(doc_id).set(data)
    
    # Update local
    order_df = get_collection_df("draft_order")
    if not order_df.empty:
        order_df = order_df[~((order_df["season"] == season) & (order_df["draftOrder"] == draft_order))]
    order_df = pd.concat([order_df, pd.DataFrame([data])], ignore_index=True)
    _save_df_to_local("draft_order", order_df)

    clear_data_cache()


def add_draft_rule(season: int, draft_order: int, pick_one: int, pick_two: int, pick_three: int):
    """Persist the three pick-slot assignments for a player's draft order rule."""
    doc_id = f"{season}_{draft_order}"
    data = {
        "season": season,
        "draftOrder": draft_order,
        "pickOne": pick_one,
        "pickTwo": pick_two,
        "pickThree": pick_three,
    }
    db = get_db()
    if db:
        db.collection("draft_order_rules").document(doc_id).set(data)
    
    # Update local
    rules_df = get_collection_df("draft_order_rules")
    if not rules_df.empty:
        rules_df = rules_df[~((rules_df["season"] == season) & (rules_df["draftOrder"] == draft_order))]
    rules_df = pd.concat([rules_df, pd.DataFrame([data])], ignore_index=True)
    _save_df_to_local("draft_order_rules", rules_df)

    clear_data_cache()
    signal_data_update()

def set_member_paid(season: int, player_id: int, paid: bool) -> bool:
    """Set the paid flag on a player's draft_order entry for a given season."""
    order_df = get_collection_df("draft_order")
    if order_df.empty:
        return False
    mask = (order_df["season"].astype(int) == season) & (order_df["playerId"].astype(int) == player_id)
    if not mask.any():
        return False
    draft_order_num = int(order_df.loc[mask, "draftOrder"].iloc[0])
    doc_id = f"{season}_{draft_order_num}"
    db = get_db()
    if db:
        db.collection("draft_order").document(doc_id).update({"paid": paid})
    order_df.loc[mask, "paid"] = paid
    _save_df_to_local("draft_order", order_df)
    return True


def update_player_profile(player_id: str, updates: dict):
    """Updates non-credential player fields (nickname, email, MFA) with cache invalidation."""
    db = get_db()
    if db:
        db.collection("players").document(str(player_id)).update(updates)
    
    # Update local
    players_df = get_collection_df("players")
    if not players_df.empty:
        # Match as string or int to be safe
        mask = (players_df["playerId"].astype(str) == str(player_id))
        if mask.any():
            for k, v in updates.items():
                players_df.loc[mask, k] = v
            _save_df_to_local("players", players_df)

    clear_data_cache()
    signal_data_update()

def save_weekly_recap(year: int, week: int, summary: str):
    """Saves an AI-generated weekly summary to Firestore and/or local cache."""
    data = {
        "year": year,
        "week": week,
        "summary": summary,
        "timestamp": time.time()
    }
    
    db = get_db()
    if db:
        doc_id = f"{year}_{week}"
        db.collection("weekly_recaps").document(doc_id).set(data)
    
    # Update local cache if in local mode
    if os.environ.get("USE_LOCAL_DATA", "False").lower() == "true":
        local_path = pathlib.Path(".local_db") / "weekly_recaps.pkl"
        try:
            if local_path.exists():
                df = pd.read_pickle(local_path)
                # Remove existing if present
                mask = (df['year'] == year) & (df['week'] == week)
                df = df[~mask]
                df = pd.concat([df, pd.DataFrame([data])], ignore_index=True)
            else:
                df = pd.DataFrame([data])
            _save_df_to_local("weekly_recaps", df)
        except Exception as e:
            logger.warning("Failed to persist recap locally: %s", e)

def get_weekly_recap(year: int, week: int):
    """Retrieves a specific weekly recap from Firestore."""
    db = get_db()
    if not db:
        # We are offline / local data mode
        local_path = pathlib.Path(".local_db") / "weekly_recaps.pkl"
        if local_path.exists():
            try:
                recaps_df = pd.read_pickle(local_path)
                match = recaps_df[(recaps_df['year'] == year) & (recaps_df['week'] == week)]
                if not match.empty:
                    return match.iloc[0].to_dict()
            except Exception:
                pass
        return None
        
    doc = db.collection("weekly_recaps").document(f"{year}_{week}").get()
    return doc.to_dict() if doc.exists else None

def save_metadata(doc_id: str, data: dict):
    """Saves arbitrary metadata to Firestore and local cache."""
    db = get_db()
    if db:
        db.collection("metadata").document(doc_id).set(data)
    
    if os.environ.get("USE_LOCAL_DATA", "False").lower() == "true":
        local_path = pathlib.Path(".local_db") / "metadata.pkl"
        try:
            if local_path.exists():
                df = pd.read_pickle(local_path)
                df = df[df['id'] != doc_id]
                new_row = pd.DataFrame([{"id": doc_id, **data}])
                df = pd.concat([df, new_row], ignore_index=True)
            else:
                df = pd.DataFrame([{"id": doc_id, **data}])
            _save_df_to_local("metadata", df)
        except Exception as e:
            logger.warning("Failed to persist metadata locally: %s", e)

def get_metadata(doc_id: str):
    """Retrieves arbitrary metadata from Firestore or local cache."""
    db = get_db()
    if not db:
        local_path = pathlib.Path(".local_db") / "metadata.pkl"
        if local_path.exists():
            try:
                df = pd.read_pickle(local_path)
                match = df[df['id'] == doc_id]
                if not match.empty:
                    res = match.iloc[0].to_dict()
                    res.pop('id', None)
                    return res
            except Exception:
                pass
        return None

    doc = db.collection("metadata").document(doc_id).get()
    return doc.to_dict() if doc.exists else None


def get_config_settings() -> dict:
    """Returns app config settings. Defaults to {"draft_active": False}."""
    default = {"draft_active": False}
    use_local = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"

    if use_local:
        local_path = pathlib.Path(".local_db") / "config_settings.json"
        if local_path.exists():
            try:
                with open(local_path) as f:
                    return {**default, **json.load(f)}
            except Exception:
                pass
        return default

    db = get_db()
    if not db:
        return default
    doc = db.collection("config").document("settings").get()
    return {**default, **(doc.to_dict() if doc.exists else {})}


def set_config_settings(data: dict):
    """Writes config settings to Firestore and local json cache."""
    db = get_db()
    if db:
        db.collection("config").document("settings").set(data)

    local_path = pathlib.Path(".local_db") / "config_settings.json"
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning("Failed to persist config_settings locally: %s", e)
