"""services/model_version.py -- Feature-computation-version stamping.

A single git commit SHA identifies "which version of the feature-engine /
serving code produced this prediction or training run's inputs." Forward-only
by design -- see docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md
"Feature computation versioning" for why (manual semantic versioning already
failed twice in this codebase's history; a SHA needs no human to remember it).
"""
import os
import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def get_feature_version() -> str:
    """Return the git commit SHA identifying the running code's feature-engine version.

    Cloud Run Jobs run in Docker images built with `.git/` excluded
    (see .dockerignore) -- `git rev-parse` cannot work at runtime in that
    environment. The GIT_SHA env var is baked in at Docker build time instead
    (see Dockerfile.sync / Dockerfile.predict + cloudbuild-*.yaml), sourced
    from the real checkout that ran `gcloud builds submit` (deploy.ps1).
    Local dev and manually-run training scripts fall back to a live
    `git rev-parse` since they run from a real checkout with `.git/` present.
    """
    env_sha = os.environ.get("GIT_SHA")
    if env_sha and env_sha != "unknown":
        return env_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def build_ensemble_version_string(nn_svc, xgb_svc, lr_svc) -> str:
    """The canonical 'nn_vX+xgb_vY+lr_vZ' string identifying which version
    each of the three ensemble members loaded."""
    return f"nn_{nn_svc.loaded_version}+xgb_{xgb_svc.loaded_version}+lr_{lr_svc.loaded_version}"
