import subprocess
from unittest.mock import MagicMock, patch


class TestGetFeatureVersion:
    def test_prefers_env_var_when_set(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.setenv("GIT_SHA", "abc1234")
        assert get_feature_version() == "abc1234"

    def test_falls_back_to_git_when_env_unset(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.delenv("GIT_SHA", raising=False)
        fake_result = MagicMock(stdout="deadbeef1234\n")
        with patch("subprocess.run", return_value=fake_result) as mock_run:
            assert get_feature_version() == "deadbeef1234"
            assert mock_run.call_args.args[0] == ["git", "rev-parse", "HEAD"]

    def test_falls_back_to_git_when_env_is_literal_unknown(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.setenv("GIT_SHA", "unknown")
        fake_result = MagicMock(stdout="cafef00d\n")
        with patch("subprocess.run", return_value=fake_result):
            assert get_feature_version() == "cafef00d"

    def test_returns_unknown_when_git_unavailable(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.delenv("GIT_SHA", raising=False)
        with patch("subprocess.run", side_effect=subprocess.SubprocessError("no git")):
            assert get_feature_version() == "unknown"


class TestBuildEnsembleVersionString:
    def test_format(self):
        from services.model_version import build_ensemble_version_string
        nn_svc = MagicMock(loaded_version="v10")
        xgb_svc = MagicMock(loaded_version="v4")
        lr_svc = MagicMock(loaded_version="v2")
        assert build_ensemble_version_string(nn_svc, xgb_svc, lr_svc) == "nn_v10+xgb_v4+lr_v2"
