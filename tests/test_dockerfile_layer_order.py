"""GIT_SHA must be declared after the pip install layer and before COPY . .,
so a new commit SHA never invalidates the dependency cache."""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _index(lines, predicate, label):
    for i, line in enumerate(lines):
        if predicate(line.strip()):
            return i
    pytest.fail(f"{label} not found")


@pytest.mark.parametrize("name", ["Dockerfile", "Dockerfile.sync", "Dockerfile.predict"])
def test_git_sha_declared_after_pip_install_and_before_copy(name):
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    pip = _index(lines, lambda s: s.startswith("RUN pip install"), "RUN pip install")
    arg = _index(lines, lambda s: s.startswith("ARG GIT_SHA"), "ARG GIT_SHA")
    env = _index(lines, lambda s: s.startswith("ENV GIT_SHA"), "ENV GIT_SHA")
    copy = _index(lines, lambda s: s == "COPY . .", "COPY . .")
    assert pip < arg < env < copy
