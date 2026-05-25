# Prediction Feature Audit Table

**Date:** 2026-05-24  
**Status:** Requirements captured — implement later  
**Deferred from:** 2026-05-24 brainstorming session  

---

## Problem

The ML ensemble makes game predictions but there's no way to audit *why* — which of the 26 input features drove a specific outcome, how the three models (NN/XGB/LR) disagreed, or how retraining changed a prediction. This makes it impossible to verify model health or explain picks to users.

---

## Design Decisions (agreed)

| Decision | Choice | Rationale |
|---|---|---|
| Storage granularity | One Firestore doc per season × ensemble version | Matches `game_predictions` pattern; 1 read per season, cached in memory — avoids 300 reads/page load |
| Model version policy | Keep old + add new on retrain | Compound doc ID encodes versions; old docs stay queryable |
| Feature detail level | Raw features + scaled features + per-model probabilities + blended importance | Full audit trail |
| Feature importance method | XGB SHAP + LR coefficient×feature, blended by model weights | XGB SHAP native; LR exact contribution; NN approximate or omitted |
| Opt-in flag | `--features` on backfill script | SHAP adds ~5–10s/season; don't slow default backfill |
| UI surface | Enhanced explain modal (top-5 + per-model splits) + new admin debug page | Modal for in-context use; admin page for systematic auditing |

---

## Data Model

### Firestore Collection: `prediction_features`

**Document ID:** `{season}_{nn_ver}+{xgb_ver}+{lr_ver}`  
**Example:** `2025_nn_v10+xgb_v4+lr_v2`

```json
{
  "season": 2025,
  "ensemble_version": "nn_v10+xgb_v4+lr_v2",
  "nn_version": "v10",
  "xgb_version": "v4",
  "lr_version": "v2",
  "created_at": "2025-11-12T00:00:00Z",
  "games": [
    {
      "game_id": "2025_08_KC_SF",
      "week": 8,
      "away_team": "KC",
      "home_team": "SF",
      "nn_prob": 0.623,
      "xgb_prob": 0.589,
      "lr_prob": 0.601,
      "blended_prob": 0.611,
      "features": {
        "elo_diff": 45.2,
        "home_advantage": 1,
        "trench_dominance_delta": 0.234
        // ... all 26 raw (unscaled) feature values
      },
      "scaled_features": {
        "elo_diff": 0.812,
        "trench_dominance_delta": 1.23
        // ... all 26 post-scaler values the model saw
      },
      "feature_importance": [
        {"feature": "elo_diff", "score": 0.312, "direction": "home"},
        {"feature": "trench_dominance_delta", "score": 0.156, "direction": "away"}
        // ... all 26, sorted by |score| descending
      ]
    }
  ]
}
```

**Document size estimate:** ~300 games × ~800 bytes = ~240KB per season per version. Well under the 1MB Firestore document limit.

### Local Cache

`.local_db/prediction_features_{season}_{nn_ver}+{xgb_ver}+{lr_ver}.json`

Add to `scripts/refresh_local_pkls.py` so `python scripts/refresh_local_pkls.py` keeps local cache in sync.

---

## Feature Importance Computation

Compute after prediction, before writing to Firestore:

| Model | Method | Notes |
|---|---|---|
| **XGB** | `get_booster().predict(dmatrix, pred_contribs=True)` | Native SHAP values, directional |
| **LR** | `coef_ * scaled_feature_value` per feature | Exact linear contribution |
| **NN** | `shap.DeepExplainer` (TF model) | Approximate; requires `shap` package |

**Blend:** `0.45 × nn_score + 0.20 × xgb_score + 0.35 × lr_score`, then normalize.  
**Direction:** sign of blended score → `"home"` (positive) or `"away"` (negative).

Add `shap` to `requirements.txt` if not already present.

---

## Backfill Integration

**File:** `scripts/backfill_schedule_predictions.py`

New flag `--features`: when present, after computing predictions per season:

1. Call `compute_feature_audit(features_df, nn_svc, xgb_svc, lr_svc)` — new helper in `services/prediction_service.py` or a standalone `services/feature_audit_service.py`
2. Accumulate per-game audit records
3. Write one Firestore doc + local JSON at the end of each season's loop

**Running without `--features` is unchanged** — no performance impact on default backfill.

**Registry versions** are read from each service's loaded model at prediction time — no new parameters needed.

---

## API Endpoints

Add to `routes/api_routes.py`:

```
GET /api/prediction_features/{season}/{week}/{away_team}/{home_team}
```
Returns the feature audit record for one game (latest ensemble version).  
Auth: `require_auth` (any logged-in user — shown in explain modal).  
Returns `{"error": "No feature data for this game."}` with 404 if no data exists yet.

```
GET /api/prediction_features/{season}
```
Returns all audit records for a season (all ensemble versions stored).  
Auth: `require_admin` (admin debug page only).  
Includes `ensemble_version` field for frontend grouping/filtering.

---

## Frontend

### Enhanced Explain Modal (`static/js/schedule_explain.js`)

New section, rendered only when the API returns feature data:

- **Per-model probability row:** `NN 62%  ·  XGB 58%  ·  LR 60%  →  Blended 61%`
- **Top-5 feature breakdown:** horizontal mini bar chart, label = feature name + direction (e.g., `"Elo Advantage → SF +45 pts"`)
- Gracefully hidden if no feature data (games before backfill was run)

The modal already fetches `/api/predictions/explain` — add a second fetch to `/api/prediction_features/{season}/{week}/{away}/{home}` and merge the results.

### Admin Prediction Debug Page

**Route:** new entry in admin section, e.g. `/admin/predictions` or tab on existing admin page  
**Template:** `templates/admin_predictions.html`  
**JS:** `static/js/admin_predictions.js`

Layout:
- **Game picker:** Season dropdown → Week dropdown → Matchup dropdown (populated from available data)
- **Model version selector:** shows all versions with data for that season (for retraining comparison)
- **Feature table:** 26 rows — Feature | Raw Value | Scaled Value | Importance Score | Direction
- **Per-model output table:** NN prob | XGB prob | LR prob | weights | blended result
- **Version diff view** (stretch): side-by-side comparison when two model versions are selected for the same game

---

## New Files

| File | Purpose |
|---|---|
| `services/feature_audit_service.py` | `compute_feature_audit()` — SHAP + LR contributions + blend |
| `templates/admin_predictions.html` | Admin debug page template |
| `static/js/admin_predictions.js` | Admin page JS (game picker, table rendering) |
| `tests/test_feature_audit_service.py` | Unit tests for audit computation |

## Modified Files

| File | Change |
|---|---|
| `scripts/backfill_schedule_predictions.py` | Add `--features` flag, call audit helper, write Firestore doc |
| `scripts/refresh_local_pkls.py` | Add `prediction_features` collection to local sync |
| `routes/api_routes.py` | Two new GET endpoints |
| `static/js/schedule_explain.js` | Second API call + per-model row + top-5 bar chart |
| `requirements.txt` | Add `shap` if not present |

---

## Open Questions (resolve at implementation time)

1. **NN SHAP cost:** `shap.DeepExplainer` can be slow on large models. Measure per-game cost before committing to it — may want to skip NN importance or use gradient approximation instead.
2. **Firestore doc size:** If a season has >300 games (playoffs included), verify the doc stays under 1MB with full feature vectors.
3. **Admin page route:** Decide whether this is a new page at `/admin/predictions` or a tab injected into the existing `templates/admin.html`.

---

## Completion Criteria (for when this is implemented)

- [ ] `python scripts/backfill_schedule_predictions.py --season 2025 --features` writes one Firestore doc
- [ ] `python scripts/refresh_local_pkls.py` syncs the new collection to local JSON
- [ ] `GET /api/prediction_features/2025/8/KC/SF` returns feature data with all 26 features
- [ ] Explain modal shows per-model probs + top-5 features when data exists, hidden when not
- [ ] Admin predictions page loads season picker, game picker, full feature table
- [ ] Model version comparison works for seasons where multiple versions exist
- [ ] `pytest tests/test_feature_audit_service.py` — all pass
- [ ] `pytest tests/ -q` — full suite green
