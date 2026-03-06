import os
import pytest
import pandas as pd

# Force the application to execute against the live database, bypassing local PKL caches
os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import get_db, get_collection_df
from services.data_service import load_data
from services.draft_service import load_draft_state

@pytest.fixture(scope="module")
def live_db():
    db = get_db()
    assert db is not None, "Firebase failed to initialize during test."
    return db

def test_raw_firebase_schemas(live_db):
    """
    Ensures that our expected baseline schemas in Firebase haven't unexpectedly mutated.
    If the database changes entirely, the application code must purposefully adapt to it.
    """
    def get_keys(col_name):
        docs = live_db.collection(col_name).limit(1).get()
        return set(docs[0].to_dict().keys()) if docs else set()
        
    order_keys = get_keys('draft_order')
    if order_keys:
        assert 'draftOrder' in order_keys, f"Database changed: 'draftOrder' missing from draft_order. Keys: {order_keys}"
        assert 'draftorder' not in order_keys, "Database changed: 'draftorder' mistakenly remains in draft_order!"
        
    rules_keys = get_keys('draft_order_rules')
    if rules_keys:
        assert 'draftOrder' in rules_keys, f"Database changed: 'draftOrder' missing from draft_order_rules. Keys: {rules_keys}"
        assert 'draftorder' not in rules_keys, "Database changed: 'draftorder' mistakenly uploaded to draft_order_rules!"

def test_data_service_alignment():
    """
    Executes the global data pipeline against the live database. 
    If data_service.py has schema mismatches (e.g. attempting to join on an idealized 'draftOrder'
    instead of the actual 'draftorder' present in Firebase), this pipeline will throw a KeyError.
    """
    try:
        # load_data fetches from all primary collections simultaneously
        standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()
    except KeyError as e:
        pytest.fail(f"Schema Mismatch in data_service.py! The code attempted to query a column not present in the database: {e}")
        
    # Verify the application code successfully pulled the data without relying on hardcoded intercept renames
    if not draft_order.empty:
        assert 'draftOrder' in draft_order.columns, "Application overrode or lost the native database 'draftOrder' schema."

def test_draft_service_alignment():
    """
    Executes the WebSocket draft state payload construction against the live database.
    If draft_service.py attempts to merge disparate schemas symmetrically, it will KeyError out.
    """
    try:
        # A set() represents zero connected players, proving the core board state renders successfully
        state = load_draft_state(set())
    except KeyError as e:
        pytest.fail(f"Schema Mismatch in draft_service.py! The code attempted to query a column not present in the database: {e}")
        
    assert "draft_board" in state
    assert type(state["draft_board"]) is list
