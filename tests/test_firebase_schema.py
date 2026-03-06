import os
import pytest
from typing import Set

# Force connection to live Firebase, not the local pkl cache
os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import get_db

@pytest.fixture(scope="module")
def db_client():
    db = get_db()
    assert db is not None, "Failed to initialize Firebase client."
    return db

def get_collection_keys(db, collection_name: str) -> Set[str]:
    """Retrieve the set of keys used in the first document of a collection."""
    docs = db.collection(collection_name).limit(1).get()
    if not docs:
        return set()
    return set(docs[0].to_dict().keys())

def test_draft_order_schema(db_client):
    keys = get_collection_keys(db_client, 'draft_order')
    if not keys:
        pytest.skip("Collection 'draft_order' is empty in Firebase.")
    
    assert 'season' in keys, "Missing 'season' in draft_order"
    assert 'playerId' in keys, "Missing 'playerId' in draft_order"
    assert 'draftOrder' in keys, f"CRITICAL: Expected 'draftOrder', but found keys: {keys}"
    assert 'draftorder' not in keys, "CRITICAL: Found deprecated lower-case 'draftorder' in Firebase!"

def test_draft_order_rules_schema(db_client):
    keys = get_collection_keys(db_client, 'draft_order_rules')
    if not keys:
        pytest.skip("Collection 'draft_order_rules' is empty in Firebase.")
    
    assert 'season' in keys, "Missing 'season' in draft_order_rules"
    assert 'draftOrder' in keys, f"CRITICAL: Expected 'draftOrder', but found keys: {keys}"
    assert 'pickOne' in keys, "Missing 'pickOne' in draft_order_rules"
    assert 'pickTwo' in keys, "Missing 'pickTwo' in draft_order_rules"
    assert 'pickThree' in keys, "Missing 'pickThree' in draft_order_rules"

def test_draft_results_schema(db_client):
    keys = get_collection_keys(db_client, 'draft_results')
    if not keys:
        pytest.skip("Collection 'draft_results' is empty in Firebase.")
    
    expected = {'season', 'draftPick', 'playerId', 'team'}
    missing = expected - keys
    assert not missing, f"Missing {missing} in draft_results. Full keys: {keys}"

def test_players_schema(db_client):
    keys = get_collection_keys(db_client, 'players')
    if not keys:
        pytest.skip("Collection 'players' is empty in Firebase.")
    
    assert 'playerId' in keys, "Missing 'playerId' in players"
    assert 'nickName' in keys, "Missing 'nickName' in players"
