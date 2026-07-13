"""Fava extension: the quints review panel (docs/plans/06, plan 5.4.1).

The read-only half of the approval-queue surface: VAT status, open
receivables, staging drafts, and inbox backlog — the same compute layer
the CLI's ``--json`` exposes, rendered inside the UI users already run.

Enable in the beancount file:

    2024-01-01 custom "fava-extension" "quints.fava"
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from fava.ext import FavaExtensionBase

from .. import config, receivables, settlement
from .. import inbox as inbox_mod

_DRAFT_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} +([*!]) ", re.M)


class QuintDashboard(FavaExtensionBase):
    """VAT, receivables, and review-queue panel."""

    report_title = "Quint"

    @property
    def _root(self) -> Path:
        return Path(self.ledger.beancount_file_path).parent

    @property
    def _cfg(self) -> config.Config:
        return config.load(self._root / "quints.toml"
                           if (self._root / "quints.toml").exists() else None)

    def today(self):
        return datetime.now(timezone.utc).date()

    def vat_status(self):
        """(liabilities, unlinked_owed, total_owed, today) — see settlement."""
        return settlement.outstanding(
            Path(self.ledger.beancount_file_path),
            cfg=self._cfg,
            entries=self.ledger.all_entries,
        )

    def open_receivables(self):
        return receivables.compute_from_entries(
            self.ledger.all_entries, self.today(), self._cfg
        )

    def staging(self):
        """Pending staging drafts: (file name, total drafts, flagged drafts)."""
        out = []
        for f in sorted((self._root / "staging").glob("*.bean")):
            flags = _DRAFT_LINE.findall(f.read_text(encoding="utf-8"))
            out.append((f.name, len(flags), flags.count("!")))
        return out

    def inbox(self):
        """Inbox inventory (hints + duplicate/linked status) — see quints.inbox."""
        return inbox_mod.scan(self._root, self.ledger.all_entries)
