"""
cache_service.py — Read-only analytics cache for the web app.

The web app NEVER reads raw Firestore collections.
All analytics are pre-computed by scripts/cache_builder.py and stored
in the Firestore `analytics_cache` collection (or .local_db/*.pkl in dev mode).
"""
import os
import json
import pathlib
import pandas as pd
from typing import Any, Optional

_USE_LOCAL = os.environ.get('USE_LOCAL_DATA', 'False').lower() == 'true'
_LOCAL_CACHE_DIR = pathlib.Path('.local_db/analytics')


def _local_path(analytic: str, year: int, week: int) -> pathlib.Path:
    _LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _LOCAL_CACHE_DIR / f"{analytic}_{year}_{week}.json"


def get_cached(analytic: str, year: int, week: int) -> Optional[Any]:
    """
    Reads one cached analytic result for (analytic, year, week).
    Returns the deserialized data, or None if not found.

    In local dev mode: reads from .local_db/analytics/<analytic>_<year>_<week>.json
    In production:     reads ONE document from Firestore analytics_cache collection
    """
    if _USE_LOCAL:
        p = _local_path(analytic, year, week)
        if p.exists():
            try:
                with open(p, 'r') as f:
                    doc = json.load(f)
                return doc.get('data')
            except Exception:
                return None
        return None
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            doc_id = f"{analytic}_{year}_{week}"
            doc = db.collection('analytics_cache').document(doc_id).get()
            if doc.exists:
                raw = doc.to_dict().get('data')
                if raw:
                    return json.loads(raw)
        except Exception:
            pass
        return None


def write_cache(analytic: str, year: int, week: int, data: Any, is_final: bool = False) -> None:
    """
    Writes a computed analytic result to cache.
    Called exclusively by scripts/cache_builder.py — never from page routes.
    """
    serialized = json.dumps(data, default=str)

    if _USE_LOCAL:
        p = _local_path(analytic, year, week)
        with open(p, 'w') as f:
            json.dump({'analytic': analytic, 'year': year, 'week': week,
                       'is_final': is_final, 'data': data}, f, default=str)
    else:
        try:
            from services.db_service import get_db
            from datetime import datetime, timezone
            db = get_db()
            doc_id = f"{analytic}_{year}_{week}"
            db.collection('analytics_cache').document(doc_id).set({
                'analytic': analytic,
                'year': year,
                'week': week,
                'is_final': is_final,
                'computed_at': datetime.now(timezone.utc).isoformat(),
                'data': serialized
            })
        except Exception as e:
            print(f"[cache_service] Failed to write cache: {e}")


def is_cache_final(analytic: str, year: int, week: int) -> bool:
    """Returns True if the cached result is marked final (no recompute needed)."""
    if _USE_LOCAL:
        p = _local_path(analytic, year, week)
        if p.exists():
            try:
                with open(p, 'r') as f:
                    return json.load(f).get('is_final', False)
            except Exception:
                pass
        return False
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            doc_id = f"{analytic}_{year}_{week}"
            doc = db.collection('analytics_cache').document(doc_id).get()
            if doc.exists:
                return doc.to_dict().get('is_final', False)
        except Exception:
            pass
        return False

# --- Data Cache (Moved from data_service to break circular import) ---
import time

# In-memory cache keyed by year (or 'all' for unfiltered load)
_DATA_CACHE: dict = {}
_CACHE_TIMESTAMPS: dict = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour - long-lived persistence
_LAST_REMOTE_CHECK = 0
_REMOTE_CHECK_INTERVAL = 60 # Check Firestore for invalidation every 60 seconds

def clear_data_cache(year=None):
    """
    Invalidates in-memory data caches.
    If year is provided, only that year's cache is cleared.
    If year is None, wipes all data (default behavior for full refreshes).
    """
    global _DATA_CACHE, _CACHE_TIMESTAMPS
    if year is not None:
        key = str(year)
        if key in _DATA_CACHE:
            del _DATA_CACHE[key]
        if key in _CACHE_TIMESTAMPS:
            del _CACHE_TIMESTAMPS[key]
        print(f"Log: Local data cache for {year} cleared.")
    else:
        _DATA_CACHE.clear()
        _CACHE_TIMESTAMPS.clear()
        print("Log: Global data cache explicitly cleared.")

def _cache_key(year):
    return str(year) if year is not None else 'all'
