import os

import pytest
from hypothesis import settings


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commits made by `quints init` ignore the developer's own git config: a
    global `commit.gpgsign` would otherwise prompt a signing agent mid-test."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


# Property tests: few examples per run so `make check` stays fast; the weekly
# CI run sets QUINTS_WEEKLY=1 and digs deeper.
settings.register_profile("fast", max_examples=20, deadline=None)
settings.register_profile("weekly", max_examples=2000, deadline=None)
settings.load_profile("weekly" if os.environ.get("QUINTS_WEEKLY") else "fast")
