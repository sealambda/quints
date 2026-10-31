import pytest


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commits made by `quints init` ignore the developer's own git config: a
    global `commit.gpgsign` would otherwise prompt a signing agent mid-test."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
