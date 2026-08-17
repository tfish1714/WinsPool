"""
cache_service.py — Read-only analytics cache for the web app.

The web app NEVER reads raw Firestore collections.
All analytics are pre-computed by scripts/cache_builder.py and stored
in the Firestore `analytics_cache` collection (or .local_db/*.pkl in dev mode).
"""
import logging
import os
import json
import pathlib
import pandas as pd
from typing import Any, Optional

logger = logging.getLogger(__name__)

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
            logger.error("Failed to write cache: %s", e)


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
_CACHE_TTL_SECONDS = 3600  # 1-hour TTL; long enough to avoid Firestore spam on every request, short enough to catch same-day data changes
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
        logger.info("Local data cache for %s cleared.", year)
    else:
        _DATA_CACHE.clear()
        _CACHE_TIMESTAMPS.clear()
        logger.info("Global data cache explicitly cleared.")

def _cache_key(year):
    return str(year) if year is not None else 'all'


# ---------------------------------------------------------------------------
# Game-level ML predictions (separate from analytics_cache)
# ---------------------------------------------------------------------------
# Key schema: "W{wk:02d}_{home}_{away}" — zero-padded week + both team abbrs.
# Week is required to uniquely identify each game: multiple different matchups
# happen each week, and a team can appear in different matchups across weeks.
# Season is implicit: one document per season in Firestore
# (keyed by season int) and one JSON file per season locally.
# Each value: {pred_prob, pred_winner, pred_su_conf, pred_ats_pick}

_GAME_PRED_DIR = pathlib.Path('.local_db')


def get_game_predictions(season: int) -> dict:
    """Return {game_key: pred_dict} for season, or {} if not found."""
    if _USE_LOCAL:
        p = _GAME_PRED_DIR / f"game_predictions_{season}.json"
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f).get('predictions', {})
            except Exception:
                return {}
        return {}
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            doc = db.collection('game_predictions').document(str(season)).get()
            if doc.exists:
                return doc.to_dict().get('predictions', {})
        except Exception:
            pass
        return {}


def merge_game_predictions(df: pd.DataFrame, season: int) -> pd.DataFrame:
    """Return df with ML predictions from game_predictions cache merged in.

    Matches rows by W{wk:02d}_{home}_{away} key. Safe to call even when no
    predictions exist — returns df unchanged.
    """
    from services.nn_feature_engine import _normalize_team
    preds = get_game_predictions(season)
    if not preds:
        return df
    df = df.copy()
    for col in ('pred_winner', 'pred_su_conf', 'pred_ats_pick', 'pred_prob', 'edge_vs_vegas', 'model_spread'):
        if col not in df.columns:
            df[col] = None

    def _key(row):
        wk = row.get('week')
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        if wk is None or not ht or not at:
            return None
        return f"W{int(wk):02d}_{ht}_{at}"

    keys = df.apply(_key, axis=1)
    for col in ('pred_winner', 'pred_su_conf', 'pred_ats_pick', 'pred_prob', 'edge_vs_vegas', 'model_spread'):
        df[col] = keys.map(lambda k, c=col: preds.get(k, {}).get(c) if k else None)
    return df


def write_game_predictions(season: int, predictions: dict) -> None:
    """Persist {game_key: pred_dict} for season (local JSON or Firestore)."""
    from datetime import datetime, timezone
    if _USE_LOCAL:
        _GAME_PRED_DIR.mkdir(parents=True, exist_ok=True)
        p = _GAME_PRED_DIR / f"game_predictions_{season}.json"
        with open(p, 'w') as f:
            json.dump({'season': season,
                       'generated_at': datetime.now(timezone.utc).isoformat(),
                       'predictions': predictions}, f, default=str)
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            db.collection('game_predictions').document(str(season)).set({
                'season': season,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'predictions': predictions,
            })
        except Exception as e:
            logger.error("Failed to write game_predictions/%s: %s", season, e)


# ---------------------------------------------------------------------------
# Prediction features audit store
# ---------------------------------------------------------------------------
# One document per season × ensemble_version.
# Local: .local_db/prediction_features_{season}_{ensemble_version}.json
# Firestore: prediction_features/{season}_{ensemble_version}
# games value: {game_key: per-game audit dict from feature_audit_service}


def _deserialize_prediction_features(doc: dict) -> dict:
    """Expand games_json string back to a dict (Firestore write path uses a JSON
    string to avoid the per-document index-entry limit)."""
    if doc and "games_json" in doc and "games" not in doc:
        try:
            doc = {**doc, "games": json.loads(doc["games_json"])}
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to deserialize games_json in prediction_features doc")
    return doc


def get_prediction_features(
    season: int,
    ensemble_version: str | None = None,
) -> dict | None:
    """Return the prediction features doc for season/version, or None if absent.

    If ensemble_version is None, returns the most recently written doc for the
    season (by file mtime locally, by created_at in Firestore).
    """
    if ensemble_version is not None and (
        "/" in ensemble_version or "\\" in ensemble_version or ".." in ensemble_version
    ):
        raise ValueError(f"Invalid ensemble_version: {ensemble_version!r}")

    if _USE_LOCAL:
        if ensemble_version:
            p = _GAME_PRED_DIR / f"prediction_features_{season}_{ensemble_version}.json"
            if not p.exists():
                return None
            with open(p) as f:
                return json.load(f)
        else:
            candidates = sorted(
                _GAME_PRED_DIR.glob(f"prediction_features_{season}_*.json"),
                key=lambda p: p.stat().st_mtime,
            )
            if not candidates:
                return None
            with open(candidates[-1]) as f:
                return json.load(f)
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            if ensemble_version:
                doc_id = f"{season}_{ensemble_version}"
                doc = db.collection("prediction_features").document(doc_id).get()
                return _deserialize_prediction_features(doc.to_dict()) if doc.exists else None
            else:
                docs = list(
                    db.collection("prediction_features")
                    .where("season", "==", season)
                    .order_by("created_at", direction="DESCENDING")
                    .limit(1)
                    .stream()
                )
                return _deserialize_prediction_features(docs[0].to_dict()) if docs else None
        except Exception:
            logger.exception("Failed to fetch prediction_features for season=%s", season)
            return None


def write_prediction_features(
    season: int,
    ensemble_version: str,
    games: dict,
    *,
    use_local: bool | None = None,
) -> None:
    """Persist the prediction features doc (local JSON or Firestore).

    Args:
        season: NFL season year.
        ensemble_version: e.g. "nn_v10+xgb_v4+lr_v2".
        games: {game_key: per-game audit dict} from compute_feature_audit().
        use_local: Override the _USE_LOCAL env setting.  Pass True to force
                   local JSON, False to force Firestore.  None = auto.
    """
    if "/" in ensemble_version or "\\" in ensemble_version or ".." in ensemble_version:
        raise ValueError(f"Invalid ensemble_version: {ensemble_version!r}")

    _local = _USE_LOCAL if use_local is None else use_local

    from datetime import datetime, timezone
    payload = {
        "season":           season,
        "ensemble_version": ensemble_version,
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "games":            games,
    }
    if _local:
        _GAME_PRED_DIR.mkdir(parents=True, exist_ok=True)
        p = _GAME_PRED_DIR / f"prediction_features_{season}_{ensemble_version}.json"
        with open(p, "w") as f:
            json.dump(payload, f, default=str)
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            doc_id = f"{season}_{ensemble_version}"
            # Firestore has a 1 MB document limit.  A full season with ~270 games,
            # each carrying 26 raw + 26 scaled feature vectors, exceeds it.
            # Store a slim version: per-model probs + top-10 importance only.
            # The full data (raw features, scaled features, all 26 importance rows)
            # lives in the local JSON file.
            slim_games = {
                gk: {
                    k: v for k, v in gdata.items()
                    if k not in ("features", "scaled_features")
                } | {
                    "feature_importance": gdata.get("feature_importance", [])[:10]
                }
                for gk, gdata in games.items()
            }
            firestore_payload = {
                **{k: v for k, v in payload.items() if k != "games"},
                "games_json": json.dumps(slim_games, default=str),
                "full_data_local_only": True,
            }
            db.collection("prediction_features").document(doc_id).set(firestore_payload)
        except Exception:
            logger.exception("Failed to write prediction_features to Firestore season=%s", season)


# ---------------------------------------------------------------------------
# Elo rating history (Elo Ratings Explorer)
# ---------------------------------------------------------------------------
# One document per season, mirroring game_predictions above -- a single doc
# holding every season would serialize past Firestore's 1MB document limit
# (the full 2006-2025 dataset is ~1.12MB as JSON) and only grows from here.
# Local: .local_db/elo_history_{season}.json. Firestore: elo_history/{season}.
# Written exclusively by scripts/compute_elo.py --firestore. Read via
# get_all_elo_history() by routes/prediction_routes.py -- never
# rawdata/elo_computed.csv directly (that CSV is for ML training code only).


def get_elo_history_season(season: int) -> list[dict] | None:
    """Return the list of per-game Elo rows for one season, or None if absent."""
    if _USE_LOCAL:
        p = _GAME_PRED_DIR / f"elo_history_{season}.json"
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f).get("rows")
            except Exception:
                return None
        return None
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            doc = db.collection("elo_history").document(str(season)).get()
            if doc.exists:
                return doc.to_dict().get("rows")
        except Exception:
            pass
        return None


def get_all_elo_history() -> list[dict]:
    """Return every computed Elo row across all seasons, sorted oldest first."""
    all_rows: list[dict] = []
    if _USE_LOCAL:
        for p in sorted(_GAME_PRED_DIR.glob("elo_history_*.json")):
            try:
                with open(p) as f:
                    all_rows.extend(json.load(f).get("rows", []))
            except Exception:
                logger.warning("Failed to read %s", p)
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            for doc in db.collection("elo_history").stream():
                all_rows.extend(doc.to_dict().get("rows", []))
        except Exception:
            logger.exception("Failed to fetch elo_history from Firestore")
    all_rows.sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))
    return all_rows


def write_elo_history_season(season: int, rows: list[dict], *, use_local: bool | None = None) -> None:
    """Persist one season's computed Elo rows (local JSON or Firestore).

    Called exclusively by scripts/compute_elo.py --firestore.

    Args:
        use_local: Override the _USE_LOCAL env setting. Pass True to force
                   local JSON, False to force Firestore. None = auto.
    """
    _local = _USE_LOCAL if use_local is None else use_local

    if _local:
        _GAME_PRED_DIR.mkdir(parents=True, exist_ok=True)
        p = _GAME_PRED_DIR / f"elo_history_{season}.json"
        with open(p, "w") as f:
            json.dump({"season": season, "rows": rows}, f, default=str)
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            db.collection("elo_history").document(str(season)).set({
                "season": season,
                "rows": rows,
            })
        except Exception as e:
            logger.error("Failed to write elo_history/%s: %s", season, e)
