"""tests/test_rank_position_groups.py -- Tests for scripts/rank_position_groups.py."""
import csv
import io

import pytest


def _fake_profiles():
    """Three-team profile dict as returned by compute_preseason_player_profiles().
    AAA is strong everywhere, BBB is average, CCC is weak everywhere."""
    return {
        "AAA": {"off_pass_epa": 0.30, "off_rush_epa": 0.10,
                "def_pass_epa": -0.30, "def_rush_epa": -0.10,
                "qb_tier": 0.25, "ol_av": 300000.0, "dl_perf": 500.0},
        "BBB": {"off_pass_epa": 0.00, "off_rush_epa": 0.00,
                "def_pass_epa": 0.00, "def_rush_epa": 0.00,
                "qb_tier": 0.00, "ol_av": 150000.0, "dl_perf": 250.0},
        "CCC": {"off_pass_epa": -0.30, "off_rush_epa": -0.10,
                "def_pass_epa": 0.30, "def_rush_epa": 0.10,
                "qb_tier": -0.25, "ol_av": 50000.0, "dl_perf": 50.0},
    }


class TestComputeDimZscores:
    def test_stronger_team_has_positive_z_on_offense_dim(self):
        from scripts.rank_position_groups import compute_dim_zscores
        z = compute_dim_zscores(_fake_profiles())
        assert z["AAA"]["dl_perf"] > 0
        assert z["CCC"]["dl_perf"] < 0

    def test_defensive_dim_is_sign_flipped(self):
        """CCC has the highest raw def_pass_epa (worst defense, most EPA
        allowed) but should get the LOWEST z-score once flipped."""
        from scripts.rank_position_groups import compute_dim_zscores
        z = compute_dim_zscores(_fake_profiles())
        assert z["AAA"]["def_pass_epa"] > z["BBB"]["def_pass_epa"] > z["CCC"]["def_pass_epa"]

    def test_middle_team_between_the_other_two(self):
        from scripts.rank_position_groups import compute_dim_zscores
        z = compute_dim_zscores(_fake_profiles())
        assert z["CCC"]["dl_perf"] < z["BBB"]["dl_perf"] < z["AAA"]["dl_perf"]


class TestComputeComposite:
    def test_stronger_team_has_higher_composite_and_elo_boost(self):
        from scripts.rank_position_groups import compute_dim_zscores, compute_composite
        dim_z = compute_dim_zscores(_fake_profiles())
        composite = compute_composite(dim_z)
        assert composite["AAA"]["composite_z"] > composite["BBB"]["composite_z"] > composite["CCC"]["composite_z"]
        assert composite["AAA"]["elo_boost"] > composite["BBB"]["elo_boost"] > composite["CCC"]["elo_boost"]

    def test_elo_boost_bounded_by_boost_max(self):
        from scripts.rank_position_groups import compute_dim_zscores, compute_composite
        from services.constants import PRESEASON_ELO_BOOST_MAX
        dim_z = compute_dim_zscores(_fake_profiles())
        composite = compute_composite(dim_z)
        for t in composite:
            assert abs(composite[t]["elo_boost"]) <= PRESEASON_ELO_BOOST_MAX + 1e-6


class TestWriteLeaderboard:
    def test_ranks_teams_by_composite_descending(self):
        from scripts.rank_position_groups import (
            compute_dim_zscores, compute_composite, write_leaderboard,
        )
        profiles = _fake_profiles()
        dim_z = compute_dim_zscores(profiles)
        composite = compute_composite(dim_z)
        buf = io.StringIO()
        write_leaderboard(profiles, dim_z, composite, buf)

        rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
        assert [r["team"] for r in rows] == ["AAA", "BBB", "CCC"]
        assert [r["composite_rank"] for r in rows] == ["1", "2", "3"]

    def test_includes_a_column_per_dim(self):
        from scripts.rank_position_groups import (
            compute_dim_zscores, compute_composite, write_leaderboard,
        )
        from services.constants import PRESEASON_ELO_WEIGHTS
        profiles = _fake_profiles()
        dim_z = compute_dim_zscores(profiles)
        composite = compute_composite(dim_z)
        buf = io.StringIO()
        write_leaderboard(profiles, dim_z, composite, buf)

        header = buf.getvalue().splitlines()[0].split(",")
        for d in PRESEASON_ELO_WEIGHTS:
            assert f"{d}_z" in header
            assert f"{d}_rank" in header


class TestWriteTeamBreakdown:
    def test_sorted_by_absolute_contribution_descending(self):
        from scripts.rank_position_groups import (
            compute_dim_zscores, compute_composite, write_team_breakdown,
        )
        profiles = _fake_profiles()
        dim_z = compute_dim_zscores(profiles)
        composite = compute_composite(dim_z)
        buf = io.StringIO()
        write_team_breakdown("AAA", profiles, dim_z, composite, buf)

        lines = buf.getvalue().splitlines()
        dim_rows = list(csv.reader(lines[1:8]))  # 7 dims, before the blank separator line
        contributions = [abs(float(r[5])) for r in dim_rows]
        assert contributions == sorted(contributions, reverse=True)

    def test_unknown_team_raises(self):
        from scripts.rank_position_groups import (
            compute_dim_zscores, compute_composite, write_team_breakdown,
        )
        profiles = _fake_profiles()
        dim_z = compute_dim_zscores(profiles)
        composite = compute_composite(dim_z)
        with pytest.raises(SystemExit):
            write_team_breakdown("ZZZ", profiles, dim_z, composite, io.StringIO())


class TestWriteDimRanking:
    def test_ranks_all_teams_on_one_dim(self):
        from scripts.rank_position_groups import compute_dim_zscores, write_dim_ranking
        profiles = _fake_profiles()
        dim_z = compute_dim_zscores(profiles)
        buf = io.StringIO()
        write_dim_ranking("dl_perf", profiles, dim_z, buf)

        rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
        assert [r["team"] for r in rows] == ["AAA", "BBB", "CCC"]
