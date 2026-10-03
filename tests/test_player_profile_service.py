"""compute_preseason_player_profiles degrades to {} on a malformed weekly roster file."""
import pandas as pd

import services.nn_feature_engine as nfe


def test_weekly_roster_without_week_column_returns_empty(monkeypatch, tmp_path):
    shared = {"roster_mode": "weekly", "roster": pd.DataFrame({"team": ["KC"], "gsis_id": ["x"]})}
    monkeypatch.setattr(nfe, "_load_profile_shared_inputs", lambda *a, **k: shared)
    assert nfe.compute_preseason_player_profiles(2026, tmp_path, week=3) == {}
