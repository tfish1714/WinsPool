import pandas as pd
import pytest
from unittest.mock import patch
from services.espn_injury_service import (
    _status_to_weight, _extract_status, _load_espn_id_crosswalk,
    _find_event_ids, _fetch_game_injuries, get_espn_injury_overrides,
)


class TestStatusMapping:
    def test_out_maps_to_zero(self):
        assert _status_to_weight("Out") == 0.0

    def test_doubtful_maps_to_point_one_five(self):
        assert _status_to_weight("Doubtful") == 0.15

    def test_questionable_maps_to_point_five(self):
        assert _status_to_weight("Questionable") == 0.5

    def test_unknown_status_maps_to_full_go(self):
        assert _status_to_weight("Probable") == 1.0

    def test_extract_status_from_plain_string(self):
        assert _extract_status({"status": "Questionable"}) == "Questionable"

    def test_extract_status_from_nested_dict(self):
        assert _extract_status({"status": {"description": "Out"}}) == "Out"

    def test_extract_status_missing_returns_empty(self):
        assert _extract_status({}) == ""


class TestLoadEspnIdCrosswalk:
    def test_maps_espn_id_to_gsis_id_for_requested_week(self, tmp_path):
        d = tmp_path / "weekly_rosters"
        d.mkdir(parents=True)
        pd.DataFrame([
            {"season": 2025, "week": 3, "gsis_id": "G1", "espn_id": "E1"},
            {"season": 2025, "week": 4, "gsis_id": "G2", "espn_id": "E2"},  # wrong week
        ]).to_csv(d / "roster_weekly_2025.csv", index=False)

        result = _load_espn_id_crosswalk(tmp_path, 2025, 3)
        assert result == {"E1": "G1"}

    def test_realistic_numeric_ids_not_coerced_to_float(self, tmp_path):
        """Regression test: verify numeric-string IDs are preserved exactly,
        not round-tripped through float (which would produce "4566092.0").
        This catches the dtype-specification bug where espn_id is inferred as
        float64 when NaN values appear IN THE espn_id COLUMN, breaking all
        real ESPN lookups. The real weekly_rosters CSV has some rows where
        espn_id is genuinely missing for a player, forcing pandas to infer
        the whole column as float64 without explicit dtype=str."""
        d = tmp_path / "weekly_rosters"
        d.mkdir(parents=True)
        # Two rows for the same week: one with numeric espn_id, one with NaN in espn_id.
        # The NaN in espn_id column forces pandas to infer the whole column as float64.
        pd.DataFrame([
            {"season": 2025, "week": 3, "gsis_id": "00-0000001", "espn_id": "4566092"},
            {"season": 2025, "week": 3, "gsis_id": "00-0000002", "espn_id": float("nan")},
        ]).to_csv(d / "roster_weekly_2025.csv", index=False)

        result = _load_espn_id_crosswalk(tmp_path, 2025, 3)
        # After dropna(subset=["espn_id"]), only the first row survives.
        # Key assertion: ESPN ID must be exact string "4566092", not "4566092.0"
        assert result == {"4566092": "00-0000001"}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert _load_espn_id_crosswalk(tmp_path, 2099, 1) == {}


class TestFindEventIds:
    def test_matches_target_game_by_normalized_abbr(self):
        fake_scoreboard = {
            "events": [{
                "id": "401555",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "WSH"}},
                        {"homeAway": "away", "team": {"abbreviation": "KC"}},
                    ]
                }],
            }]
        }
        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard):
            result = _find_event_ids([("WAS", "KC")])
        assert result == {("WAS", "KC"): "401555"}

    def test_no_scoreboard_data_returns_empty(self):
        with patch("services.espn_injury_service.fetch_espn_scores", return_value=None):
            assert _find_event_ids([("WAS", "KC")]) == {}

    def test_unmatched_game_is_absent(self):
        fake_scoreboard = {"events": [{
            "id": "1", "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "SF"}},
                {"homeAway": "away", "team": {"abbreviation": "LAC"}},
            ]}],
        }]}
        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard):
            result = _find_event_ids([("WAS", "KC")])
        assert result == {}


class TestFetchGameInjuries:
    def test_parses_nested_team_injuries(self):
        fake_summary = {"injuries": [
            {"injuries": [
                {"athlete": {"id": "E1"}, "status": "Out"},
                {"athlete": {"id": "E2"}, "status": "Questionable"},
            ]},
        ]}
        with patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            result = _fetch_game_injuries("401555")
        assert result == [
            {"espn_id": "E1", "status": "Out"},
            {"espn_id": "E2", "status": "Questionable"},
        ]

    def test_http_failure_returns_empty_list(self):
        with patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = False
            assert _fetch_game_injuries("401555") == []

    def test_network_exception_returns_empty_list(self):
        with patch("services.espn_injury_service.requests.get", side_effect=Exception("timeout")):
            assert _fetch_game_injuries("401555") == []

    def test_missing_athlete_id_is_skipped_not_raised(self):
        fake_summary = {"injuries": [{"injuries": [{"status": "Out"}]}]}
        with patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            assert _fetch_game_injuries("401555") == []

    def test_malformed_json_response_returns_empty_list(self):
        """Regression test: if resp.json() succeeds but returns unexpected
        shape (e.g. list instead of dict), parsing failure should be caught
        and return [] rather than raising AttributeError."""
        with patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            # Simulate a response that's a list instead of dict
            mock_get.return_value.json.return_value = [{"some": "list"}]
            assert _fetch_game_injuries("401555") == []


class TestGetEspnInjuryOverrides:
    def test_end_to_end_maps_status_to_weight_via_gsis_id(self, tmp_path):
        d = tmp_path / "weekly_rosters"
        d.mkdir(parents=True)
        pd.DataFrame([
            {"season": 2025, "week": 3, "gsis_id": "G1", "espn_id": "E1"},
        ]).to_csv(d / "roster_weekly_2025.csv", index=False)

        fake_scoreboard = {"events": [{
            "id": "401555", "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "WSH"}},
                {"homeAway": "away", "team": {"abbreviation": "KC"}},
            ]}],
        }]}
        fake_summary = {"injuries": [{"injuries": [
            {"athlete": {"id": "E1"}, "status": "Out"},
        ]}]}

        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard), \
             patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            result = get_espn_injury_overrides([("WAS", "KC")], 2025, 3, tmp_path)

        assert result == {(3, "G1"): 0.0}

    def test_no_matching_scoreboard_event_returns_empty(self, tmp_path):
        with patch("services.espn_injury_service.fetch_espn_scores", return_value={"events": []}):
            result = get_espn_injury_overrides([("WAS", "KC")], 2025, 3, tmp_path)
        assert result == {}

    def test_player_with_no_crosswalk_entry_is_skipped(self, tmp_path):
        d = tmp_path / "weekly_rosters"
        d.mkdir(parents=True)
        pd.DataFrame([
            {"season": 2025, "week": 3, "gsis_id": "G1", "espn_id": "E1"},
        ]).to_csv(d / "roster_weekly_2025.csv", index=False)

        fake_scoreboard = {"events": [{
            "id": "401555", "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "WSH"}},
                {"homeAway": "away", "team": {"abbreviation": "KC"}},
            ]}],
        }]}
        fake_summary = {"injuries": [{"injuries": [
            {"athlete": {"id": "UNKNOWN"}, "status": "Out"},
        ]}]}

        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard), \
             patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            result = get_espn_injury_overrides([("WAS", "KC")], 2025, 3, tmp_path)

        assert result == {}
