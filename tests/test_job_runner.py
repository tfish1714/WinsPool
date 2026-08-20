from unittest.mock import patch, MagicMock
import pathlib
from scripts.job_runner import run_steps


def _fake_result(returncode=0, stdout="ok", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run")
def test_all_steps_succeed_no_alert(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")
    mock_run.return_value = _fake_result(returncode=0)

    ok = run_steps([{"name": "A", "script": script, "required": True}], job_name="test-job")

    assert ok is True
    mock_alert.assert_not_called()


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run")
def test_required_step_failure_sends_alert_and_returns_false(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")
    mock_run.return_value = _fake_result(returncode=1, stderr="boom")

    ok = run_steps([{"name": "A", "script": script, "required": True}], job_name="test-job")

    assert ok is False
    mock_alert.assert_called_once()
    subject, message = mock_alert.call_args[0]
    assert "test-job" in subject
    assert "A" in message
    assert "boom" in message


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run")
def test_non_required_step_failure_continues_without_alert(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")
    mock_run.return_value = _fake_result(returncode=1, stderr="minor")

    ok = run_steps([{"name": "A", "script": script, "required": False}], job_name="test-job")

    assert ok is True
    mock_alert.assert_not_called()


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run", side_effect=Exception("unexpected crash"))
def test_unhandled_exception_sends_alert_and_returns_false(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")

    ok = run_steps([{"name": "A", "script": script, "required": True}], job_name="test-job")

    assert ok is False
    mock_alert.assert_called_once()
