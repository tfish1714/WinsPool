"""tests/test_admin_routes.py — Coverage for all admin HTTP endpoints.

Pattern:
  - Happy path: assert 200 + correct db function was called
  - Auth guard: assert 401/403 when called with a non-admin or missing token
  - Key error paths where the route has an explicit branch (400 duplicate, 404 not found)

Mocking: patch at routes.admin_routes.<fn> — the functions are imported
into that namespace, so patching services.db_service won't intercept the calls.

Note: All endpoints are registered under /api prefix (router prefix="/api"), so
paths are /api/admin/... not /admin/...
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from main import app

client = TestClient(app)


# ── /api/admin/new_season ─────────────────────────────────────────────────────

class TestNewSeason:
    def test_happy_path_calls_add_draft_order_and_rule(self, admin_token):
        """Happy path: 200 + add_draft_order called once per player."""
        # Empty rules_df so no rules are copied
        with patch("routes.admin_routes.add_draft_order") as mock_order, \
             patch("routes.admin_routes.add_draft_rule") as mock_rule, \
             patch("routes.admin_routes.get_collection_df") as mock_df, \
             patch("routes.admin_routes.wipe_draft_cache"):
            # First call: draft_order (empty → no existing season)
            # Second call: draft_order_rules (empty → no rules to copy)
            mock_df.return_value = pd.DataFrame()
            resp = client.post(
                "/api/admin/new_season",
                json={"season": 2099, "playerIds": [1, 2, 3]},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert mock_order.call_count == 3
        # No rules copied because rules_df is empty
        assert mock_rule.call_count == 0
        # Verify the correct season is passed to add_draft_order
        first_call_args = mock_order.call_args_list[0][0]
        assert first_call_args[0] == 2099

    def test_requires_admin_role(self, auth_token):
        """Non-admin token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/new_season",
            json={"season": 2099, "playerIds": [1]},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)

    def test_requires_token(self):
        """Missing token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/new_season",
            json={"season": 2099, "playerIds": [1]},
        )
        assert resp.status_code in (401, 403)

    def test_returns_400_when_season_already_exists(self, admin_token):
        """If draft_order already has rows for this season, returns 400."""
        existing = pd.DataFrame({"season": [2099], "playerId": [1]})
        with patch("routes.admin_routes.get_collection_df", return_value=existing), \
             patch("routes.admin_routes.add_draft_order"), \
             patch("routes.admin_routes.add_draft_rule"), \
             patch("routes.admin_routes.wipe_draft_cache"):
            resp = client.post(
                "/api/admin/new_season",
                json={"season": 2099, "playerIds": [1]},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 400

    def test_returns_400_when_no_players_selected(self, admin_token):
        """Empty playerIds list triggers 400 before any DB call."""
        with patch("routes.admin_routes.get_collection_df", return_value=pd.DataFrame()), \
             patch("routes.admin_routes.add_draft_order") as mock_order, \
             patch("routes.admin_routes.wipe_draft_cache"):
            resp = client.post(
                "/api/admin/new_season",
                json={"season": 2099, "playerIds": []},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 400
        mock_order.assert_not_called()

    def test_copies_existing_rules_when_present(self, admin_token):
        """When prior season has rules, copies them to the new season."""
        existing_rules = pd.DataFrame({
            "season": [2098],
            "draftOrder": [1],
            "pickOne": [10],
            "pickTwo": [11],
            "pickThree": [12],
        })
        with patch("routes.admin_routes.add_draft_order"), \
             patch("routes.admin_routes.add_draft_rule") as mock_rule, \
             patch("routes.admin_routes.get_collection_df", side_effect=[
                 pd.DataFrame(),       # draft_order check → empty (season doesn't exist)
                 existing_rules,        # draft_order_rules → has prior rules
             ]), \
             patch("routes.admin_routes.wipe_draft_cache"):
            resp = client.post(
                "/api/admin/new_season",
                json={"season": 2099, "playerIds": [1]},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert mock_rule.called  # rules were copied


# ── /api/admin/delete_season ──────────────────────────────────────────────────

class TestDeleteSeason:
    def test_happy_path_calls_delete_season_data(self, admin_token):
        """200 + delete_season_data called with the correct season."""
        with patch("routes.admin_routes.delete_season_data") as mock_del, \
             patch("routes.admin_routes.wipe_draft_cache"):
            resp = client.post(
                "/api/admin/delete_season",
                json={"season": 2024},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        mock_del.assert_called_once_with(2024)

    def test_requires_admin_role(self, auth_token):
        """Non-admin token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/delete_season",
            json={"season": 2024},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)

    def test_requires_token(self):
        """Missing token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/delete_season",
            json={"season": 2024},
        )
        assert resp.status_code in (401, 403)


# ── /api/admin/reset_draft ────────────────────────────────────────────────────

class TestResetDraft:
    def test_happy_path_calls_delete_draft_results(self, admin_token):
        """200 + delete_draft_results_for_season called with correct season."""
        with patch("routes.admin_routes.delete_draft_results_for_season") as mock_del, \
             patch("routes.admin_routes.wipe_draft_cache"):
            resp = client.post(
                "/api/admin/reset_draft",
                json={"season": 2025},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        mock_del.assert_called_once_with(2025)

    def test_requires_token(self):
        """Missing token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/reset_draft",
            json={"season": 2025},
        )
        assert resp.status_code in (401, 403)

    def test_requires_admin_role(self, auth_token):
        """Non-admin token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/reset_draft",
            json={"season": 2025},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)


# ── /api/admin/create_player ──────────────────────────────────────────────────

class TestCreatePlayer:
    def test_happy_path_returns_player_id(self, admin_token):
        """200 + response contains playerId from add_player return value."""
        with patch("routes.admin_routes.add_player", return_value=99) as mock_add:
            resp = client.post(
                "/api/admin/create_player",
                json={
                    "fullName": "Test Player",
                    "nickName": "Testy",
                    "email": "test@example.com",
                    "phone": "555-0100",
                },
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json().get("playerId") == 99
        mock_add.assert_called_once()

    def test_requires_admin_role(self, auth_token):
        """Non-admin token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/create_player",
            json={
                "fullName": "X",
                "nickName": "X",
                "email": "x@x.com",
                "phone": "000",
            },
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)

    def test_returns_400_when_required_fields_missing(self, admin_token):
        """fullName/nickName/email all required — missing email triggers 400."""
        with patch("routes.admin_routes.add_player") as mock_add:
            resp = client.post(
                "/api/admin/create_player",
                json={
                    "fullName": "No Email",
                    "nickName": "NE",
                    "email": "",   # empty string → falsy → 400
                    "phone": "555-0101",
                },
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 400
        mock_add.assert_not_called()


# ── /api/admin/reset_password ─────────────────────────────────────────────────

class TestResetPassword:
    def test_happy_path_calls_update_player_profile(self, admin_token):
        """200 + update_player_profile called with all security fields cleared."""
        with patch("routes.admin_routes.update_player_profile") as mock_update:
            resp = client.post(
                "/api/admin/reset_password",
                json={"targetPlayerId": "1"},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        args, kwargs = mock_update.call_args
        # Verify the player_id and the exact update dict
        assert args[0] == "1"
        assert args[1] == {
            "password_hash": None,
            "failed_setup_attempts": 0,
            "lockout_until": None,
            "mfa_secret": None,
            "mfa_enabled": False,
        }

    def test_requires_token(self):
        """Missing token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/reset_password",
            json={"targetPlayerId": "1"},
        )
        assert resp.status_code in (401, 403)

    def test_requires_admin_role(self, auth_token):
        """Non-admin token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/reset_password",
            json={"targetPlayerId": "1"},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)


# ── /api/admin/set_temp_password ──────────────────────────────────────────────

class TestSetTempPassword:
    # A password that satisfies the regex: 12+ chars, upper, lower, digit, symbol
    VALID_PW = "Temp1234!Pass"

    def test_happy_path_calls_update_credentials_and_profile(self, admin_token):
        """200 + both update_player_credentials and update_player_profile called."""
        with patch("routes.admin_routes.update_player_credentials") as mock_creds, \
             patch("routes.admin_routes.update_player_profile") as mock_profile, \
             patch("routes.admin_routes.get_password_hash", return_value="hashed"):
            resp = client.post(
                "/api/admin/set_temp_password",
                json={"targetPlayerId": "1", "tempPassword": self.VALID_PW},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        mock_creds.assert_called_once()
        mock_profile.assert_called_once()

    def test_requires_token(self):
        """Missing token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/set_temp_password",
            json={"targetPlayerId": "1", "tempPassword": self.VALID_PW},
        )
        assert resp.status_code in (401, 403)

    def test_returns_400_for_weak_password(self, admin_token):
        """Password failing the regex (too short / missing symbol) returns 400."""
        with patch("routes.admin_routes.update_player_credentials") as mock_creds:
            resp = client.post(
                "/api/admin/set_temp_password",
                json={"targetPlayerId": "1", "tempPassword": "weakpassword"},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 400
        mock_creds.assert_not_called()

    def test_requires_admin_role(self, auth_token):
        """Non-admin token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/set_temp_password",
            json={"targetPlayerId": "1", "tempPassword": self.VALID_PW},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)


# ── /api/admin/members/paid ───────────────────────────────────────────────────

class TestMembersPaid:
    def test_happy_path_calls_set_member_paid(self, admin_token):
        """200 + set_member_paid called with season, playerId, paid flag."""
        with patch("routes.admin_routes.set_member_paid", return_value=True) as mock_paid:
            resp = client.post(
                "/api/admin/members/paid",
                json={"targetPlayerId": 1, "season": 2025, "paid": True},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        mock_paid.assert_called_once_with(2025, 1, True)

    def test_requires_admin_role(self, auth_token):
        """Non-admin token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/members/paid",
            json={"targetPlayerId": 1, "season": 2025, "paid": True},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)

    def test_requires_token(self):
        """Missing token is rejected with 401 or 403."""
        resp = client.post(
            "/api/admin/members/paid",
            json={"targetPlayerId": 1, "season": 2025, "paid": True},
        )
        assert resp.status_code in (401, 403)


# ── /api/admin/prediction_features/{season} ───────────────────────────────────

class TestAdminPredictionFeaturesEndpoint:
    """Tests for GET /api/admin/prediction_features/{season}."""

    def test_returns_data_for_season(self, admin_token, monkeypatch):
        fake_doc = {
            "season": 2025,
            "ensemble_version": "nn_v10+xgb_v4+lr_v2",
            "created_at": "2025-11-01T00:00:00Z",
            "games": {"W08_SF_KC": {"blended_prob": 0.61}},
        }
        monkeypatch.setattr(
            "routes.admin_routes.get_prediction_features",
            lambda season, **kw: fake_doc,
        )

        resp = client.get(
            "/api/admin/prediction_features/2025",
            headers={"Authorization": admin_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["season"] == 2025
        assert "games" in data

    def test_returns_404_when_no_data(self, admin_token, monkeypatch):
        monkeypatch.setattr(
            "routes.admin_routes.get_prediction_features",
            lambda season, **kw: None,
        )

        resp = client.get(
            "/api/admin/prediction_features/2025",
            headers={"Authorization": admin_token},
        )
        assert resp.status_code == 404

    def test_requires_admin(self, auth_token, monkeypatch):
        monkeypatch.setattr(
            "routes.admin_routes.get_prediction_features",
            lambda season, **kw: {"season": 2025, "games": {}},
        )

        resp = client.get(
            "/api/admin/prediction_features/2025",
            headers={"Authorization": auth_token},  # non-admin user
        )
        assert resp.status_code in (401, 403)


class TestPredictionsGames:
    """Tests for GET /api/admin/predictions/games."""

    def test_requires_admin(self, auth_token):
        """Non-admin token is rejected."""
        resp = client.get(
            "/api/admin/predictions/games?season=2024&week=1",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)

    def test_requires_token(self):
        """Missing token is rejected."""
        resp = client.get("/api/admin/predictions/games?season=2024&week=1")
        assert resp.status_code in (401, 403)

    def test_happy_path_returns_games_list(self, admin_token):
        """Returns JSON with season, week, and games list for valid params."""
        from unittest.mock import patch
        import pandas as pd
        mock_preds = {
            "W01_KC_BUF": {
                "pred_winner": "KC",
                "pred_su_conf": 65.0,
                "model_spread": 3.5,
                "edge_vs_vegas": 1.0,
                "pred_ats_pick": "KC",
                "explanation": {"vegas_line": 2.5},
            }
        }
        mock_games = pd.DataFrame([{
            "week": 1, "home_team": "KC", "away_team": "BUF", "result": 7.0,
        }])
        with patch("routes.admin_routes.get_game_predictions", return_value=mock_preds), \
             patch("routes.admin_routes.load_data", return_value=(
                 None, None, mock_games, None, None, None, None
             )):
            resp = client.get(
                "/api/admin/predictions/games?season=2024&week=1",
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2024
        assert body["week"] == 1
        assert isinstance(body["games"], list)
        assert len(body["games"]) == 1
        game = body["games"][0]
        assert game["away_team"] == "BUF"
        assert game["home_team"] == "KC"
        assert game["actual_winner"] == "KC"
        assert game["is_correct"] is True
        assert "model_spread" in game
        assert "vegas_line" in game

    def test_future_game_has_null_actual_winner(self, admin_token):
        """Unplayed game returns actual_winner=null, is_correct=null."""
        from unittest.mock import patch
        import pandas as pd
        mock_preds = {
            "W18_SF_SEA": {
                "pred_winner": "SF",
                "pred_su_conf": 60.0,
                "model_spread": 4.0,
                "edge_vs_vegas": 0.5,
                "pred_ats_pick": "SF",
                "explanation": {"vegas_line": 3.5},
            }
        }
        mock_games = pd.DataFrame()
        with patch("routes.admin_routes.get_game_predictions", return_value=mock_preds), \
             patch("routes.admin_routes.load_data", return_value=(
                 None, None, mock_games, None, None, None, None
             )):
            resp = client.get(
                "/api/admin/predictions/games?season=2025&week=18",
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        games = resp.json()["games"]
        assert len(games) == 1
        assert games[0]["actual_winner"] is None
        assert games[0]["is_correct"] is None
