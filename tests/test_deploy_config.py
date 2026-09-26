"""deploy/deploy.ps1 must pin the web service's scaling limits. Production
already runs max-instances=1 (set out of band); a redeploy that omits the flag
keeps the current value today, but only by accident. Codifying it makes the
guardrail reviewable. It is also required for correctness: the draft room's
WebSocket state and the auth rate limiter are both in-process."""
import pathlib
import re

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent / "deploy" / "deploy.ps1").read_text(encoding="utf-8")


def _web_deploy_block() -> str:
    """The `gcloud run deploy winspool` command: its first line plus every
    backtick-continued line, through the final line (which has no backtick).
    Never reaches the `gcloud run jobs update` lines further down."""
    m = re.search(r"gcloud run deploy winspool[^\n]*`\r?\n(?:[^\n]*`\r?\n)*[^\n`]*(?=\r?\n)", SCRIPT)
    assert m, "web service deploy command not found"
    return m.group(0)


def test_web_service_pins_max_instances_to_one():
    assert "--max-instances=1" in _web_deploy_block()


def test_web_service_pins_concurrency():
    assert re.search(r"--concurrency=\d+", _web_deploy_block())


def test_deploy_block_excludes_scheduled_jobs():
    block = _web_deploy_block()
    assert "gcloud run jobs update" not in block
    assert "--set-secrets" in block  # captured through the last line


def test_added_flags_keep_backtick_continuation_intact():
    lines = _web_deploy_block().splitlines()
    for flag in ("--max-instances=1", "--concurrency=80"):
        idx = [i for i, ln in enumerate(lines) if flag in ln]
        assert len(idx) == 1, f"{flag} must appear exactly once"
        line = lines[idx[0]]
        assert idx[0] != len(lines) - 1, f"{flag} must not be the last line"
        assert line.rstrip().endswith(" `"), f"{flag} line must end with a continuation backtick"
    assert not lines[-1].rstrip().endswith("`")


def test_existing_flags_are_preserved():
    block = _web_deploy_block()
    for flag in ("--timeout=3600", "--allow-unauthenticated", "--region us-east1", "--set-env-vars", "--set-secrets"):
        assert flag in block


def test_guardrail_rationale_is_documented_in_script():
    assert "in-process" in SCRIPT and "max-instances" in SCRIPT
