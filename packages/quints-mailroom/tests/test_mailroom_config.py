"""mailroom.toml loading, path resolution, and validation errors."""

from pathlib import Path

import pytest

from quints_mailroom.config import MailroomConfigError, load, sender_allowed

MINIMAL = """
books = "books"
from = "books@firm.ch"
allowed_senders = ["ada@example.com"]
inbound = "maildir"
outbound = "maildir"

[maildir]
inbox = "mail-in"
outbox = "mail-out"
"""


def write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "books").mkdir(exist_ok=True)
    cfg = tmp_path / "mailroom.toml"
    cfg.write_text(text)
    return cfg


def test_load_resolves_paths_relative_to_config_file(tmp_path: Path) -> None:
    cfg = load(write(tmp_path, MINIMAL))
    assert cfg.books == (tmp_path / "books").resolve()
    assert cfg.maildir.inbox == (tmp_path / "mail-in").resolve()
    assert cfg.agent.backend == "claude"  # default


def test_missing_file_and_missing_keys_error_clearly(tmp_path: Path) -> None:
    with pytest.raises(MailroomConfigError, match="not found"):
        load(tmp_path / "nope.toml")
    with pytest.raises(MailroomConfigError, match="allowed_senders"):
        load(write(tmp_path, 'books = "books"\nfrom = "a@b.c"\n'))


def test_empty_allowlist_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MailroomConfigError, match="allowed_senders"):
        load(write(tmp_path, MINIMAL.replace('["ada@example.com"]', "[]")))


def test_imap_inbound_requires_section(tmp_path: Path) -> None:
    with pytest.raises(MailroomConfigError, match=r"\[imap\]"):
        load(write(tmp_path, MINIMAL.replace('inbound = "maildir"', 'inbound = "imap"')))


def test_command_backend_requires_command(tmp_path: Path) -> None:
    with pytest.raises(MailroomConfigError, match=r"agent\.command"):
        load(write(tmp_path, MINIMAL + '\n[agent]\nbackend = "command"\n'))


def test_sender_allowed_exact_and_domain() -> None:
    allowed = ("ada@example.com", "@firm.ch")
    assert sender_allowed("ada@example.com", allowed)
    assert sender_allowed("ADA@EXAMPLE.COM", allowed)
    assert sender_allowed("anyone@firm.ch", allowed)
    assert not sender_allowed("mallory@evil.example", allowed)
    assert not sender_allowed("evil-firm.ch@attacker.example", allowed)
