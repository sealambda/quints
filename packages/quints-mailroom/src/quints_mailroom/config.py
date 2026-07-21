"""``mailroom.toml`` — one file wires mailbox, books repo, and agent together.

Secrets never live in the file: IMAP/SMTP passwords come from
``QUINTS_MAILROOM_IMAP_PASSWORD`` / ``QUINTS_MAILROOM_SMTP_PASSWORD``.
Relative paths (``books``, Maildir directories) resolve against the config
file's own directory, so a mailroom.toml checked in next to the books repo
keeps working from anywhere.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

# Env-var *names*, not secrets (S105 false positive).
IMAP_PASSWORD_ENV = "QUINTS_MAILROOM_IMAP_PASSWORD"  # noqa: S105
SMTP_PASSWORD_ENV = "QUINTS_MAILROOM_SMTP_PASSWORD"  # noqa: S105


class MailroomConfigError(ValueError):
    """Raised when mailroom.toml is missing, incomplete, or inconsistent."""


@dataclass(frozen=True)
class ImapConfig:
    host: str
    user: str
    port: int = 993
    folder: str = "INBOX"


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    user: str = ""
    port: int = 587
    starttls: bool = True  # False = implicit TLS (SMTPS, usually port 465)


@dataclass(frozen=True)
class MaildirConfig:
    inbox: Path | None = None  # required when inbound = "maildir"
    outbox: Path | None = None  # required when outbound = "maildir"


# Pre-approved tools for the Claude agent backend: read the books, edit
# drafts, and run quints — nothing that leaves the repository.
DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Glob",
    "Grep",
    "Edit",
    "Write",
    "TodoWrite",
    "Bash(quints:*)",
    "Bash(uv run quints:*)",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
)


@dataclass(frozen=True)
class AgentConfig:
    backend: str = "claude"  # "claude" (Agent SDK) | "command" (stdin→stdout)
    command: tuple[str, ...] = ()  # backend="command": argv, prompt on stdin
    model: str | None = None  # backend="claude": model override
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS
    instructions: str = ""  # extra guidance appended to every prompt


@dataclass(frozen=True)
class MailroomConfig:
    books: Path  # the bookkeeping repo the agent works in
    from_addr: str  # From: on replies
    allowed_senders: tuple[str, ...]  # addresses, or "@domain" for a whole domain
    inbound: str = "imap"  # "imap" | "maildir"
    outbound: str = "smtp"  # "smtp" | "maildir"
    imap: ImapConfig | None = None
    smtp: SmtpConfig | None = None
    maildir: MaildirConfig = field(default_factory=MaildirConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)


def _require(raw: dict[str, Any], key: str, path: Path) -> Any:
    if key not in raw:
        raise MailroomConfigError(f"{path}: missing required key '{key}'")
    return raw[key]


def _resolve(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def load(path: Path) -> MailroomConfig:
    if not path.is_file():
        raise MailroomConfigError(f"config file not found: {path} (try `quints-mailroom init`)")
    with path.open("rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)
    base = path.resolve().parent

    inbound = str(raw.get("inbound", "imap"))
    outbound = str(raw.get("outbound", "smtp"))
    if inbound not in ("imap", "maildir"):
        raise MailroomConfigError(f"{path}: inbound must be 'imap' or 'maildir', got '{inbound}'")
    if outbound not in ("smtp", "maildir"):
        raise MailroomConfigError(f"{path}: outbound must be 'smtp' or 'maildir', got '{outbound}'")

    allowed = tuple(str(s).lower() for s in _require(raw, "allowed_senders", path))
    if not allowed:
        raise MailroomConfigError(
            f"{path}: allowed_senders must not be empty — the mailroom refuses to "
            "process mail from unknown senders"
        )

    imap_cfg: ImapConfig | None = None
    if inbound == "imap":
        section = raw.get("imap")
        if not isinstance(section, dict):
            raise MailroomConfigError(f"{path}: inbound = 'imap' needs an [imap] section")
        imap_cfg = ImapConfig(
            host=str(_require(section, "host", path)),
            user=str(_require(section, "user", path)),
            port=int(section.get("port", 993)),
            folder=str(section.get("folder", "INBOX")),
        )

    smtp_cfg: SmtpConfig | None = None
    if outbound == "smtp":
        section = raw.get("smtp")
        if not isinstance(section, dict):
            raise MailroomConfigError(f"{path}: outbound = 'smtp' needs an [smtp] section")
        smtp_cfg = SmtpConfig(
            host=str(_require(section, "host", path)),
            user=str(section.get("user", "")),
            port=int(section.get("port", 587)),
            starttls=bool(section.get("starttls", True)),
        )

    md_raw = raw.get("maildir", {})
    maildir_cfg = MaildirConfig(
        inbox=_resolve(base, str(md_raw["inbox"])) if "inbox" in md_raw else None,
        outbox=_resolve(base, str(md_raw["outbox"])) if "outbox" in md_raw else None,
    )
    if inbound == "maildir" and maildir_cfg.inbox is None:
        raise MailroomConfigError(f"{path}: inbound = 'maildir' needs [maildir] inbox = ...")
    if outbound == "maildir" and maildir_cfg.outbox is None:
        raise MailroomConfigError(f"{path}: outbound = 'maildir' needs [maildir] outbox = ...")

    ag_raw = raw.get("agent", {})
    backend = str(ag_raw.get("backend", "claude"))
    if backend not in ("claude", "command"):
        raise MailroomConfigError(f"{path}: agent.backend must be 'claude' or 'command'")
    command = tuple(str(c) for c in ag_raw.get("command", ()))
    if backend == "command" and not command:
        raise MailroomConfigError(f"{path}: agent.backend = 'command' needs agent.command = [...]")
    agent_cfg = AgentConfig(
        backend=backend,
        command=command,
        model=str(ag_raw["model"]) if "model" in ag_raw else None,
        allowed_tools=tuple(str(t) for t in ag_raw.get("allowed_tools", DEFAULT_ALLOWED_TOOLS)),
        instructions=str(ag_raw.get("instructions", "")),
    )

    books = _resolve(base, str(_require(raw, "books", path)))
    if not books.is_dir():
        raise MailroomConfigError(f"{path}: books directory does not exist: {books}")

    return MailroomConfig(
        books=books,
        from_addr=str(_require(raw, "from", path)),
        allowed_senders=allowed,
        inbound=inbound,
        outbound=outbound,
        imap=imap_cfg,
        smtp=smtp_cfg,
        maildir=maildir_cfg,
        agent=agent_cfg,
    )


def sender_allowed(sender: str, allowed: tuple[str, ...]) -> bool:
    """Exact address match, or domain match for entries written as "@domain"."""
    s = sender.lower()
    return any(s == entry or (entry.startswith("@") and s.endswith(entry)) for entry in allowed)
