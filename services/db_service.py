import os
import sys
import pathlib
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter


def _init_firebase():
    """
    Initialize Firebase Admin SDK from environment or local credentials file.

    Priority:
    1. FIREBASE_CREDENTIALS env var (base64-encoded JSON) — for Cloud Run / CI.
    2. firebase_credentials.json in project root — for local development.
    """
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
    else:
        project_root = pathlib.Path(__file__).parent.parent
        creds_path = project_root / "firebase_credentials.json"
        if not creds_path.exists():
            print("Warning: No FIREBASE_CREDENTIALS env var and no firebase_credentials.json found.")
            return None
        cred = credentials.Certificate(str(creds_path))

    firebase_admin.initialize_app(cred)
    return firestore.client()


use_local_env = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"

# Only initialize Firebase when not in local-data mode
if not use_local_env:
    _init_firebase()


def get_db():
    if not firebase_admin._apps:
        _init_firebase()
    return firestore.client()


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
                print(f"Warning: Failed to read {pkl_path}: {e}. Falling back to Firestore...")

    db = get_db()
    if db is None:
        return pd.DataFrame()

    query = db.collection(collection_name)
    if filters:
        for col, op, val in filters:
            query = query.where(filter=FieldFilter(col, op, val))

    docs = query.get()
    return pd.DataFrame([doc.to_dict() for doc in docs])


def update_player_cell(player_id: int, cell: str):
    get_db().collection("players").document(str(player_id)).update({"cell": cell})


def add_draft_result(season: int, draft_pick: int, player_id: int, team: str):
    doc_id = f"{season}_{draft_pick}"
    get_db().collection("draft_results").document(doc_id).set({
        "season": season,
        "draftPick": draft_pick,
        "playerId": player_id,
        "team": team,
    })


def add_draft_order(season: int, draft_order: int, player_id: int):
    doc_id = f"{season}_{draft_order}"
    get_db().collection("draft_order").document(doc_id).set({
        "season": season,
        "draftOrder": draft_order,
        "playerId": player_id,
    })


def add_draft_rule(season: int, draft_order: int, pick_one: int, pick_two: int, pick_three: int):
    doc_id = f"{season}_{draft_order}"
    get_db().collection("draft_order_rules").document(doc_id).set({
        "season": season,
        "draftOrder": draft_order,
        "pickOne": pick_one,
        "pickTwo": pick_two,
        "pickThree": pick_three,
    })
