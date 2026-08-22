import pandas as pd
import pytest
from services.roster_value_service import _load_injury_report, compute_roster_value


def _write_injuries_csv(tmp_path, rows):
    d = tmp_path / "injuries"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "injuries_2025.csv", index=False)


class TestLoadInjuryReport:
    def test_out_status_maps_to_zero(self, tmp_path):
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "QB", "report_status": "Out"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert result[(3, "P1")] == 0.0

    def test_doubtful_status_maps_to_point_one_five(self, tmp_path):
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "WR", "report_status": "Doubtful"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert result[(3, "P1")] == 0.15

    def test_questionable_status_maps_to_point_five(self, tmp_path):
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "LB", "report_status": "Questionable"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert result[(3, "P1")] == 0.5

    def test_player_not_on_report_is_absent_not_defaulted(self, tmp_path):
        """Absence means 'not listed' -- callers must default missing keys to
        1.0 themselves; this loader only returns players who ARE listed."""
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "QB", "report_status": "Out"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert (3, "P2") not in result

    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = _load_injury_report(tmp_path, 2099)
        assert result == {}


class TestComputeRosterValueAvailabilityWeighting:
    """Integration-level: an Out starting QB with no other QB on the roster
    must drop that team's off_roster_value well below a healthy team's,
    holding everything else equal."""

    def _write_common_fixtures(self, tmp_path, season=2025, injured_status=None):
        # Two teams (KC, DEN), each with one strong QB established via
        # >= MIN_QB_ATTEMPTS. TWO teams is required, not incidental: compute_roster_value()
        # z-scores off_roster_value across all teams present for that week
        # (services/roster_value_service.py::_zscore), and a single-team
        # series has std=0 -- _zscore's own code returns a flat 0.0 for
        # every team in that case, which would make this test's assertion
        # vacuously true/false regardless of the injury weighting. DEN's
        # QB2 is identical in every other respect and never injured, so it
        # exists purely to give z-scoring something to differentiate KC
        # against.
        prior = tmp_path / "stats_player"
        prior.mkdir(parents=True, exist_ok=True)
        rows = []
        for pid, epa in [("QB1", 5.0), ("QB2", 3.0), ("QB3", 4.0)]:
            for wk in range(1, 18):
                rows.append({
                    "player_id": pid, "position": "QB", "season_type": "REG", "week": wk,
                    "passing_epa": epa, "attempts": 30,
                })
        pd.DataFrame(rows).to_csv(prior / f"stats_player_week_{season - 1}.csv", index=False)
        pd.DataFrame(columns=["player_id", "position", "season_type", "week",
                               "passing_epa", "attempts"]).to_csv(
            prior / f"stats_player_week_{season}.csv", index=False)

        rosters = tmp_path / "weekly_rosters"
        rosters.mkdir(parents=True, exist_ok=True)
        roster_rows = []
        for wk in range(1, 5):
            roster_rows.append({
                "season": season, "week": wk, "team": "KC", "gsis_id": "QB1",
                "position": "QB", "status": "ACT", "birth_date": "1995-01-01",
            })
            roster_rows.append({
                "season": season, "week": wk, "team": "DEN", "gsis_id": "QB2",
                "position": "QB", "status": "ACT", "birth_date": "1995-01-01",
            })
            roster_rows.append({
                "season": season, "week": wk, "team": "PHI", "gsis_id": "QB3",
                "position": "QB", "status": "ACT", "birth_date": "1995-01-01",
            })
        pd.DataFrame(roster_rows).to_csv(rosters / f"roster_weekly_{season}.csv", index=False)

        if injured_status:
            self._write_injuries(tmp_path, season, "QB1", 3, injured_status)

    def _write_injuries(self, tmp_path, season, gsis_id, week, status):
        d = tmp_path / "injuries"
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{
            "season": season, "week": week, "team": "KC", "gsis_id": gsis_id,
            "position": "QB", "report_status": status,
        }]).to_csv(d / f"injuries_{season}.csv", index=False)

    def test_out_qb_drops_off_roster_value_vs_healthy(self, tmp_path):
        healthy_dir = tmp_path / "healthy"
        out_dir = tmp_path / "out"
        (healthy_dir).mkdir()
        (out_dir).mkdir()
        for base in (healthy_dir, out_dir):
            (base / "stats_player").mkdir()
            (base / "weekly_rosters").mkdir()

        self._write_common_fixtures(healthy_dir)
        self._write_common_fixtures(out_dir, injured_status="Out")

        healthy = compute_roster_value(2025, healthy_dir)
        out = compute_roster_value(2025, out_dir)

        # Week 3 is when the injury applies; both teams are otherwise identical.
        assert out[(2025, 3, "KC")]["off_roster_value"] < healthy[(2025, 3, "KC")]["off_roster_value"]

    def test_espn_override_takes_precedence_over_nflverse_report(self, tmp_path):
        """A player listed Questionable on nflverse's report, but overridden
        to Out by a fresher ESPN check for that exact (week, gsis_id), must
        use the ESPN weight, not the nflverse one."""
        base = tmp_path
        (base / "stats_player").mkdir()
        (base / "weekly_rosters").mkdir()
        self._write_common_fixtures(base, injured_status="Questionable")

        without_override = compute_roster_value(2025, base)
        with_override = compute_roster_value(
            2025, base, espn_overrides={(3, "QB1"): 0.0},
        )

        assert with_override[(2025, 3, "KC")]["off_roster_value"] < \
            without_override[(2025, 3, "KC")]["off_roster_value"]
