import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_root_redirect():
    """Verify that the root endpoint securely redirects to the active season standings."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert "/wins-pool/" in response.headers["location"]

def test_standings_route_success(mock_firestore):
    """Verify that the html standings matrix renders HTTP 200 dynamically."""
    # Since we are using mock_env_vars, USE_LOCAL_DATA=true, meaning FastAPI bypasses Firestore
    # and reads straight from the safe .pkl cache mock locally constructed by pandas.
    response = client.get("/wins-pool/2024")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_api_profile_no_auth():
    # Calling an endpoints that requires authorization but failing to provide it
    response = client.get("/api/profile?playerId=999")
    assert response.status_code == 404 or response.status_code == 401

def test_draft_history_route_renders():
    """Verify that the historical data page loads without 500 exceptions."""
    response = client.get("/draft/history")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_recap_preview_auth_rejection():
    # Only admins can hit preview prompt
    response = client.post("/api/admin/recap/preview_prompt", json={"playerId": 999, "year": 2024, "week": 1})
    assert response.status_code == 401

def test_recap_generate_auth_rejection():
    """Verify that generative AI triggers reject unauthorized profiles."""
    response = client.post("/api/admin/recap/generate", json={"playerId": 999, "prompt_data": "fake"})
    assert response.status_code == 401

def test_invalid_login_credentials():
    """Verify that an invalid email/password POST safely returns HTTP 401."""
    response = client.post("/api/login", json={"email": "nonexistent@test.com", "password": "wrong"})
    assert response.status_code == 401
    assert "Invalid email or password" in response.json().get("error", "")


# ── #12: API endpoints must require authentication ─────────────────────────

def test_api_progress_is_public():
    """GET /api/progress/{season}/{week} is public — no token required.

    The standings page is public and the chart shows the same data already
    visible in the standings table, so there is no reason to gate it behind
    auth. The endpoint must never return 401.
    """
    response = client.get("/api/progress/2024/10")
    assert response.status_code != 401


def test_api_progress_draft_summary_requires_auth():
    """GET /api/progress/draft_summary returns 401 without a token."""
    response = client.get("/api/progress/draft_summary")
    assert response.status_code == 401


def test_api_standings_requires_auth():
    """GET /api/standings returns 401 without a token."""
    response = client.get("/api/standings?year=2024")
    assert response.status_code == 401


def test_api_schedule_requires_auth():
    """GET /api/schedule returns 401 without a token."""
    response = client.get("/api/schedule?year=2024")
    assert response.status_code == 401


def test_api_predictions_accuracy_requires_auth():
    """GET /api/predictions/accuracy returns 401 without a token."""
    response = client.get("/api/predictions/accuracy")
    assert response.status_code == 401


def test_api_predictions_explain_requires_auth():
    """GET /api/predictions/explain returns 401 without a token."""
    response = client.get("/api/predictions/explain?season=2024&week=1&home=KC&away=BUF")
    assert response.status_code == 401


class TestPredictionFeaturesEndpoint:
    """Tests for GET /api/prediction_features/{season}/{week}/{away}/{home}."""

    def test_returns_feature_data_when_present(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        # game_key format: W{week:02d}_{home_team}_{away_team}
        # URL /{away_team}/{home_team} → away=KC, home=SF → key W08_SF_KC
        fake_doc = {
            "season": 2025,
            "ensemble_version": "nn_v10+xgb_v4+lr_v2",
            "created_at": "2025-11-01T00:00:00Z",
            "games": {
                "W08_SF_KC": {
                    "game_key": "W08_SF_KC", "season": 2025, "week": 8,
                    "away_team": "KC", "home_team": "SF",
                    "nn_prob": 0.623, "xgb_prob": 0.589,
                    "lr_prob": 0.601, "blended_prob": 0.611,
                    "features": {"tm_elo_pre": 1550.0},
                    "scaled_features": {"tm_elo_pre": 0.81},
                    "feature_importance": [
                        {"feature": "tm_elo_pre", "score": 0.31, "direction": "home"}
                    ],
                }
            },
        }
        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: fake_doc,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["game_key"] == "W08_SF_KC"
        assert data["nn_prob"] == 0.623
        assert data["blended_prob"] == 0.611

    def test_returns_404_when_no_season_data(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: None,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 404

    def test_returns_404_when_game_not_found(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: {"season": 2025, "ensemble_version": "v1", "games": {}},
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 404

    def test_requires_auth(self):
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.get("/api/prediction_features/2025/8/KC/SF")
        assert resp.status_code == 401


def test_accuracy_response_includes_model_version(auth_token):
    """GET /api/predictions/accuracy must include model_version on each season row."""
    resp = client.get("/api/predictions/accuracy", headers={"Authorization": auth_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "seasons" in data
    # Every season row must have a model_version key (value can be None)
    for row in data["seasons"]:
        assert "model_version" in row, f"Season {row.get('season')} missing model_version"


def test_base_html_admin_link_uses_visibility_not_display():
    """admin-nav-link-drawer must not use inline display:none (causes layout shift)."""
    import pathlib
    src = pathlib.Path("templates/base.html").read_text()
    # Desktop admin link is now JS-rendered; drawer link is the static HTML element
    assert 'id="admin-nav-link-drawer"' in src
    assert 'admin-hidden' in src
    # The drawer admin link must not use inline display:none
    for line in src.splitlines():
        if 'admin-nav-link-drawer' in line:
            assert 'display: none' not in line, "admin-nav-link-drawer must not use display:none"


class TestPredictionExplainGrading:
    """Finding 3b: the /api/predictions/explain endpoint (feeds the "Why
    TEAM?" modal) never returned is_correct/is_correct_ats at all, unlike
    /admin/predictions/games (feeds the admin per-game table). This adds
    the same grading, reusing betting_screener_service.grade_bet() rather
    than re-implementing the ATS win/loss/push formula a third time."""

    def _mock_games_df(self, **overrides):
        import pandas as pd
        row = {
            "season": 2024, "week": 1, "home_team": "KC", "away_team": "BUF",
            "result": 3.0, "home_score": 20.0, "away_score": 17.0, "spread_line": -2.5,
        }
        row.update(overrides)
        return pd.DataFrame([row])

    def test_completed_game_returns_grading_fields(self, monkeypatch, auth_token):
        from unittest.mock import patch
        fake_pred = {
            "pred_winner": "KC", "pred_su_conf": 65, "pred_ats_pick": "KC",
            "model_spread": -3.0, "explanation": {"vegas_line": -2.5},
        }
        with patch("services.cache_service.get_game_predictions", return_value={"W01_KC_BUF": fake_pred}), \
             patch("routes.api_routes.load_data", return_value=(None, None, self._mock_games_df(), None, None, None, None)):
            resp = client.get(
                "/api/predictions/explain?season=2024&week=1&home=KC&away=BUF",
                headers={"Authorization": auth_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["actual_winner"] == "KC"
        assert body["home_score"] == 20
        assert body["away_score"] == 17
        assert body["is_correct"] is True   # pred_winner=KC, actual_winner=KC
        assert body["is_correct_ats"] is True  # KC picked ATS; home margin 3 > vegas_line -2.5 -> home covers

    def test_future_game_returns_null_grading_fields(self, monkeypatch, auth_token):
        from unittest.mock import patch
        fake_pred = {
            "pred_winner": "KC", "pred_su_conf": 65, "pred_ats_pick": "KC",
            "model_spread": -3.0, "explanation": {"vegas_line": -2.5},
        }
        unplayed = self._mock_games_df(result=None, home_score=None, away_score=None)
        with patch("services.cache_service.get_game_predictions", return_value={"W01_KC_BUF": fake_pred}), \
             patch("routes.api_routes.load_data", return_value=(None, None, unplayed, None, None, None, None)):
            resp = client.get(
                "/api/predictions/explain?season=2024&week=1&home=KC&away=BUF",
                headers={"Authorization": auth_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["actual_winner"] is None
        assert body["home_score"] is None
        assert body["away_score"] is None
        assert body["is_correct"] is None
        assert body["is_correct_ats"] is None

    def test_wrong_su_pick_grades_as_incorrect(self, monkeypatch, auth_token):
        from unittest.mock import patch
        fake_pred = {
            "pred_winner": "BUF", "pred_su_conf": 55, "pred_ats_pick": "BUF",
            "model_spread": 1.0, "explanation": {"vegas_line": -2.5},
        }
        with patch("services.cache_service.get_game_predictions", return_value={"W01_KC_BUF": fake_pred}), \
             patch("routes.api_routes.load_data", return_value=(None, None, self._mock_games_df(), None, None, None, None)):
            resp = client.get(
                "/api/predictions/explain?season=2024&week=1&home=KC&away=BUF",
                headers={"Authorization": auth_token},
            )
        body = resp.json()
        assert body["is_correct"] is False   # pred_winner=BUF, actual_winner=KC
        assert body["is_correct_ats"] is False  # BUF picked ATS; away margin -3 < vegas_line(-away)=2.5 -> away does not cover

    def _explain(self, auth_token, pred, games_df):
        from unittest.mock import patch
        with patch("services.cache_service.get_game_predictions", return_value={"W01_KC_BUF": pred}), \
             patch("routes.api_routes.load_data", return_value=(None, None, games_df, None, None, None, None)):
            resp = client.get(
                "/api/predictions/explain?season=2024&week=1&home=KC&away=BUF",
                headers={"Authorization": auth_token},
            )
        assert resp.status_code == 200
        return resp.json()

    def test_pushed_ats_bet_reports_push_not_null(self, auth_token):
        """A push must be distinguishable from "not gradable / not played yet"."""
        games = self._mock_games_df(result=3.0, home_score=23.0, away_score=20.0, spread_line=3.0)
        pred = {"pred_winner": "KC", "pred_su_conf": 60, "pred_ats_pick": "KC",
                "model_spread": 5.0, "explanation": {"vegas_line": 3.0}}

        body = self._explain(auth_token, pred, games)

        assert body["is_correct_ats"] == "push"
        assert body["is_correct"] is True  # SU grading is unaffected by the push

    def test_su_right_but_ats_wrong_with_correct_sign_convention(self, auth_token):
        """The exact case the two-badge modal exists for: home favored by 3
        (positive line), wins by 1, pick home -> SU correct, ATS wrong. The
        older fixture's negative line depicted the opposite favorite and
        could not pin the sign."""
        games = self._mock_games_df(result=1.0, home_score=21.0, away_score=20.0, spread_line=3.0)
        pred = {"pred_winner": "KC", "pred_su_conf": 60, "pred_ats_pick": "KC",
                "model_spread": 5.0, "explanation": {"vegas_line": 3.0}}

        body = self._explain(auth_token, pred, games)

        assert body["is_correct"] is True
        assert body["is_correct_ats"] is False

    def test_ats_pick_matching_neither_team_is_ungradable(self, auth_token):
        games = self._mock_games_df(result=7.0, home_score=27.0, away_score=20.0, spread_line=3.0)
        pred = {"pred_winner": "KC", "pred_su_conf": 60, "pred_ats_pick": "DAL",
                "model_spread": 5.0, "explanation": {"vegas_line": 3.0}}

        assert self._explain(auth_token, pred, games)["is_correct_ats"] is None

    def test_missing_vegas_line_falls_back_to_the_game_row_and_derives_edge(self, auth_token):
        games = self._mock_games_df(result=7.0, home_score=27.0, away_score=20.0, spread_line=3.0)
        pred = {"pred_winner": "KC", "pred_su_conf": 60, "pred_ats_pick": "KC",
                "model_spread": 5.5, "explanation": {}}

        body = self._explain(auth_token, pred, games)

        assert body["explanation"]["vegas_line"] == 3.0
        assert body["explanation"]["edge_vs_vegas"] == 2.5
        assert body["edge_vs_vegas"] == 2.5
        assert body["is_correct_ats"] is True

    def test_team_normalization_only_runs_on_the_requested_week(self, auth_token):
        """The game lookup must narrow to the requested season/week before
        normalizing team names, not normalize every row of every season."""
        import pandas as pd
        from unittest.mock import patch
        import services.nn_feature_engine as nfe

        target = self._mock_games_df().iloc[0].to_dict()
        others = [
            {**target, "season": 2023, "week": 1, "home_team": "OLD1", "away_team": "OLD2"},
            {**target, "season": 2024, "week": 2, "home_team": "WK2A", "away_team": "WK2B"},
            {**target, "season": 2025, "week": 1, "home_team": "NEW1", "away_team": "NEW2"},
        ]
        games = pd.DataFrame([target, *others])
        seen = []
        real = nfe._normalize_team

        def spy(team):
            seen.append(team)
            return real(team)

        pred = {"pred_winner": "KC", "pred_su_conf": 60, "pred_ats_pick": "KC",
                "model_spread": 5.0, "explanation": {"vegas_line": -2.5}}
        with patch.object(nfe, "_normalize_team", spy):
            body = self._explain(auth_token, pred, games)

        assert body["actual_winner"] == "KC"
        irrelevant = {"OLD1", "OLD2", "WK2A", "WK2B", "NEW1", "NEW2"}
        assert not irrelevant & set(seen), f"normalized rows outside season/week: {irrelevant & set(seen)}"
