import os

import pytest
from hypothesis import settings


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commits made by `quints init` ignore the developer's own git config: a
    global `commit.gpgsign` would otherwise prompt a signing agent mid-test."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def hermetic_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CI runner's variables would move every diagnostic's path, pick the
    output format, or append to the real job summary: tests that want one set
    it themselves."""
    for variable in (
        "GITHUB_WORKSPACE",
        "CI_PROJECT_DIR",
        "BUILD_SOURCESDIRECTORY",
        "QUINTS_OUTPUT_FORMAT",
        "GITHUB_STEP_SUMMARY",
    ):
        monkeypatch.delenv(variable, raising=False)


# Property tests: few examples per run so `make check` stays fast; the weekly
# CI run sets QUINTS_WEEKLY=1 and digs deeper.
settings.register_profile("fast", max_examples=20, deadline=None)
settings.register_profile("weekly", max_examples=2000, deadline=None)
settings.load_profile("weekly" if os.environ.get("QUINTS_WEEKLY") else "fast")
