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

logger = logging.getLogger(__name__)

_USE_LOCAL = os.environ.get('USE_LOCAL_DATA', 'False').lower() == 'true'

# --- Data Cache: domain-keyed, not year-keyed (see docs/superpowers/specs/
# 2026-09-14-cache-mutability-redesign-design.md) ---
import time

DOMAIN_ACTIVE = "active"
DOMAIN_HISTORICAL = "historical"
DOMAIN_STATIC = "static"
DOMAIN_PREDICTIONS_ACTIVE = "predictions_active"
DOMAIN_PREDICTIONS_HISTORICAL = "predictions_historical"
DOMAIN_ADMIN_ANALYTICS = "admin_analytics"

# Maps each cache domain to the field name it owns inside the single
# metadata/cache_control Firestore document. A writer signals exactly the
# domain(s) it touched; every process (including winspool-predict-daily,
# which never shares memory with the web service) discovers the signal via
# the existing remote-check poll below.
DOMAIN_SIGNAL_FIELDS = {
    DOMAIN_ACTIVE: "active_updated",
    DOMAIN_HISTORICAL: "historical_updated",
    DOMAIN_STATIC: "static_updated",
    DOMAIN_PREDICTIONS_ACTIVE: "predictions_active_updated",
    DOMAIN_PREDICTIONS_HISTORICAL: "predictions_historical_updated",
    DOMAIN_ADMIN_ANALYTICS: "admin_analytics_updated",
}

_DOMAIN_CACHE: dict = {}
_DOMAIN_TIMESTAMPS: dict = {}
_CACHE_TTL_SECONDS = 3600  # 1-hour TTL; used only by data_service.py's _get_active_bucket() as a defensive backstop -- historical/static are signal-only per the design spec, see data_service.py's _get_historical_bucket()/_get_static_bucket()
_LAST_REMOTE_CHECK = 0
_REMOTE_CHECK_INTERVAL = 60  # Check Firestore for invalidation every 60 seconds


def get_domain(domain: str):
    """Return the cached value for `domain`, or None if not cached."""
    return _DOMAIN_CACHE.get(domain)


def set_domain(domain: str, value, timestamp: float = None) -> None:
    """Cache `value` under `domain`, stamped with the given (or current) time."""
    _DOMAIN_CACHE[domain] = value
    _DOMAIN_TIMESTAMPS[domain] = timestamp if timestamp is not None else time.time()


def clear_domain(domain: str) -> None:
    """Evict one domain's cached value and timestamp."""
    _DOMAIN_CACHE.pop(domain, None)
    _DOMAIN_TIMESTAMPS.pop(domain, None)


def get_domain_timestamp(domain: str) -> float:
    """Return when `domain` was last cached, or 0 if never cached."""
    return _DOMAIN_TIMESTAMPS.get(domain, 0)


def clear_data_cache(domain: str = None) -> None:
    """Invalidate one named cache domain, or every domain if none given.

    `domain` must be one of the DOMAIN_* constants above (a bare Firestore
    collection name or a season year is no longer a valid argument -- see
    the design doc's "year-keyed-plus-'all'" replacement).
    """
    if domain is not None:
        clear_domain(domain)
        logger.info("Cache domain '%s' cleared.", domain)
    else:
        _DOMAIN_CACHE.clear()
        _DOMAIN_TIMESTAMPS.clear()
        logger.info("All cache domains cleared.")


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


def _fetch_game_predictions(season: int) -> dict:
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


def get_game_predictions(season: int) -> dict:
    """Return {game_key: pred_dict} for season, or {} if not found. Cached
    per-season within whichever predictions domain (active/historical) that
    season falls into today -- shares its per-season entry dict with
    data_service.py's get_preseason_predictions()/get_consensus_projections()
    (see _get_predictions_bucket_entry() there), populating only its own
    "game_predictions" key lazily."""
    from services.data_service import _get_predictions_bucket_entry
    entry = _get_predictions_bucket_entry(season)
    if "game_predictions" not in entry:
        entry["game_predictions"] = _fetch_game_predictions(season)
    return entry["game_predictions"]


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


def merge_thin_game_predictions(existing: dict, fresh: dict) -> dict:
    """Merge a thin per-game predictions map into an existing one, preserving any
    richer fields (explanation, model_spread, edge_vs_vegas, locked) already stored
    for a game that `fresh` doesn't know about.

    Used by scripts/cache_builder.py, which recomputes only pred_winner/pred_su_conf/
    pred_ats_pick/pred_prob on every run. Without this merge, write_game_predictions'
    whole-document overwrite would silently destroy whatever
    scripts/backfill_schedule_predictions.py --features previously computed for every
    game in the season -- including the elo_diff/vegas_line data the admin
    prediction-explain tooltip and the betting screener both depend on.

    Pure function -- does not mutate `existing` or `fresh`.
    """
    merged = {k: dict(v) for k, v in existing.items()}
    for key, thin in fresh.items():
        merged[key] = {**merged.get(key, {}), **thin}
    return merged


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
    feature_version: str | None = None,
) -> None:
    """Persist the prediction features doc (local JSON or Firestore).

    Args:
        season: NFL season year.
        ensemble_version: e.g. "nn_v10+xgb_v4+lr_v2".
        games: {game_key: per-game audit dict} from compute_feature_audit().
        use_local: Override the _USE_LOCAL env setting.  Pass True to force
                   local JSON, False to force Firestore.  None = auto.
        feature_version: git commit SHA identifying the feature-engine code
                   that produced `games`. Computed automatically (see
                   services.model_version.get_feature_version) if omitted.
    """
    if "/" in ensemble_version or "\\" in ensemble_version or ".." in ensemble_version:
        raise ValueError(f"Invalid ensemble_version: {ensemble_version!r}")

    if feature_version is None:
        from services.model_version import get_feature_version
        feature_version = get_feature_version()

    _local = _USE_LOCAL if use_local is None else use_local

    from datetime import datetime, timezone
    payload = {
        "season":           season,
        "ensemble_version": ensemble_version,
        "feature_version":  feature_version,
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
    """Return every computed Elo row across all seasons, sorted oldest first.

    In Firestore mode, cached under DOMAIN_ADMIN_ANALYTICS (shared with
    get_all_nn_weekly_accuracy()) -- admin-only traffic, refreshed only by
    an explicit admin_analytics_updated signal, not a TTL."""
    if _USE_LOCAL:
        all_rows: list[dict] = []
        for p in sorted(_GAME_PRED_DIR.glob("elo_history_*.json")):
            try:
                with open(p) as f:
                    all_rows.extend(json.load(f).get("rows", []))
            except Exception:
                logger.warning("Failed to read %s", p)
        all_rows.sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))
        return all_rows

    bucket = get_domain(DOMAIN_ADMIN_ANALYTICS) or {}
    if "elo_history" in bucket:
        return bucket["elo_history"]

    all_rows: list[dict] = []
    try:
        from services.db_service import get_db
        db = get_db()
        for doc in db.collection("elo_history").stream():
            all_rows.extend(doc.to_dict().get("rows", []))
    except Exception:
        logger.exception("Failed to fetch elo_history from Firestore")
    all_rows.sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))

    bucket["elo_history"] = all_rows
    set_domain(DOMAIN_ADMIN_ANALYTICS, bucket)
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
        from services.db_service import get_db
        db = get_db()
        if db is None:
            raise RuntimeError(
                f"Cannot write elo_history/{season} to Firestore: get_db() returned "
                "None (USE_LOCAL_DATA is set). Set USE_LOCAL_DATA=False before calling "
                "write_elo_history_season(use_local=False)."
            )
        db.collection("elo_history").document(str(season)).set({
            "season": season,
            "rows": rows,
        })


# ---------------------------------------------------------------------------
# Quarter-by-quarter scores (comeback-win detection in the weekly recap)
# ---------------------------------------------------------------------------
# One document per season, mirroring elo_history above. Written by
# scripts/scrape_quarter_scores.py --firestore. Read by
# services/recap_service.py::extract_weekly_data() only -- a manual,
# low-frequency action -- so this deliberately has no in-memory cache domain,
# matching prediction_features/weekly_recaps' treatment in the
# cache-mutability redesign (not worth a metadata/cache_control signal field
# for one low-frequency reader). See
# docs/superpowers/specs/2026-09-15-comeback-win-recap-design.md.


def get_quarter_scores_season(season: int) -> list[dict]:
    """Return the list of per-game quarter-score rows for one season, or [] if absent."""
    if _USE_LOCAL:
        p = _GAME_PRED_DIR / f"quarter_scores_{season}.json"
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f).get("rows", [])
            except Exception:
                return []
        return []
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            doc = db.collection("quarter_scores").document(str(season)).get()
            if doc.exists:
                return doc.to_dict().get("rows", [])
        except Exception:
            pass
        return []


def write_quarter_scores_season(season: int, rows: list[dict], *, use_local: bool | None = None) -> None:
    """Persist one season's scraped quarter-score rows (local JSON or Firestore).

    Called exclusively by scripts/scrape_quarter_scores.py --firestore.

    Args:
        use_local: Override the _USE_LOCAL env setting. Pass True to force
                   local JSON, False to force Firestore. None = auto.
    """
    _local = _USE_LOCAL if use_local is None else use_local

    if _local:
        _GAME_PRED_DIR.mkdir(parents=True, exist_ok=True)
        p = _GAME_PRED_DIR / f"quarter_scores_{season}.json"
        with open(p, "w") as f:
            json.dump({"season": season, "rows": rows}, f, default=str)
    else:
        from services.db_service import get_db
        db = get_db()
        if db is None:
            raise RuntimeError(
                f"Cannot write quarter_scores/{season} to Firestore: get_db() returned "
                "None (USE_LOCAL_DATA is set). Set USE_LOCAL_DATA=False before calling "
                "write_quarter_scores_season(use_local=False)."
            )
        db.collection("quarter_scores").document(str(season)).set({
            "season": season,
            "rows": rows,
        })


# ---------------------------------------------------------------------------
# NN weekly accuracy tracking (Model Accuracy Explorer)
# ---------------------------------------------------------------------------
# One document per season, mirroring elo_history above. Written by
# scripts/weekly_model_eval.py --firestore after each week's games complete,
# so this is a durable, retrain-proof record of "what did the model predict
# before we knew the outcome" -- unlike game_predictions, which cache_builder.py
# recomputes with whatever model is currently deployed every day.
# Local: .local_db/nn_weekly_accuracy_{season}.json. Firestore: nn_weekly_accuracy/{season}.


def _read_nn_weekly_accuracy_local(season: int) -> list[dict] | None:
    p = _GAME_PRED_DIR / f"nn_weekly_accuracy_{season}.json"
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f).get("rows")
        except Exception:
            return None
    return None


def _read_nn_weekly_accuracy_firestore(season: int) -> list[dict] | None:
    try:
        from services.db_service import get_db
        db = get_db()
        doc = db.collection("nn_weekly_accuracy").document(str(season)).get()
        if doc.exists:
            return doc.to_dict().get("rows")
    except Exception:
        pass
    return None


def get_nn_weekly_accuracy_season(season: int) -> list[dict] | None:
    """Return the list of per-week accuracy rows for one season, or None if absent."""
    if _USE_LOCAL:
        return _read_nn_weekly_accuracy_local(season)
    return _read_nn_weekly_accuracy_firestore(season)


def get_all_nn_weekly_accuracy() -> list[dict]:
    """Return every recorded weekly-accuracy row across all seasons, sorted
    oldest first.

    In Firestore mode, cached under DOMAIN_ADMIN_ANALYTICS (shared with
    get_all_elo_history()) -- admin-only traffic, refreshed only by an
    explicit admin_analytics_updated signal, not a TTL."""
    if _USE_LOCAL:
        all_rows: list[dict] = []
        for p in sorted(_GAME_PRED_DIR.glob("nn_weekly_accuracy_*.json")):
            try:
                with open(p) as f:
                    all_rows.extend(json.load(f).get("rows", []))
            except Exception:
                logger.warning("Failed to read %s", p)
        all_rows.sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))
        return all_rows

    bucket = get_domain(DOMAIN_ADMIN_ANALYTICS) or {}
    if "nn_weekly_accuracy" in bucket:
        return bucket["nn_weekly_accuracy"]

    all_rows: list[dict] = []
    try:
        from services.db_service import get_db
        db = get_db()
        for doc in db.collection("nn_weekly_accuracy").stream():
            all_rows.extend(doc.to_dict().get("rows", []))
    except Exception:
        logger.exception("Failed to fetch nn_weekly_accuracy from Firestore")
    all_rows.sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))

    bucket["nn_weekly_accuracy"] = all_rows
    set_domain(DOMAIN_ADMIN_ANALYTICS, bucket)
    return all_rows


def write_nn_weekly_accuracy_rows(season: int, new_rows: list[dict], *, use_local: bool | None = None) -> None:
    """Upsert `new_rows` (one per evaluated week) into this season's stored
    accuracy history by week number, then persist the merged list.

    Called by scripts/weekly_model_eval.py --firestore. Unlike
    write_elo_history_season (a full-season overwrite -- compute_elo.py always
    recomputes every week), this merges: weekly_model_eval.py only evaluates
    the weeks passed on its command line per run, so a naive overwrite would
    erase every previously-recorded week.
    """
    _local = _USE_LOCAL if use_local is None else use_local

    existing = (
        _read_nn_weekly_accuracy_local(season) if _local
        else _read_nn_weekly_accuracy_firestore(season)
    ) or []
    by_week = {r["week"]: r for r in existing}
    for row in new_rows:
        by_week[row["week"]] = row
    merged = [by_week[wk] for wk in sorted(by_week)]

    if _local:
        _GAME_PRED_DIR.mkdir(parents=True, exist_ok=True)
        p = _GAME_PRED_DIR / f"nn_weekly_accuracy_{season}.json"
        with open(p, "w") as f:
            json.dump({"season": season, "rows": merged}, f, default=str)
    else:
        from services.db_service import get_db
        db = get_db()
        if db is None:
            raise RuntimeError(
                f"Cannot write nn_weekly_accuracy/{season} to Firestore: get_db() returned "
                "None (USE_LOCAL_DATA is set). Set USE_LOCAL_DATA=False before calling "
                "write_nn_weekly_accuracy_rows(use_local=False)."
            )
        db.collection("nn_weekly_accuracy").document(str(season)).set({
            "season": season,
            "rows": merged,
        })
