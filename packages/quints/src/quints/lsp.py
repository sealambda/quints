"""`quints lsp`: the problems `quints check` finds, where an editor shows them.

Diagnostics and their quick fixes, nothing else. Completion, formatting and
hover are a beancount language server's job: an editor runs both on the same
file. The loader reads the books from disk, so a refresh happens on save, not
on every key.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from lsprotocol import types as t
from pygls.lsp.server import LanguageServer

from . import __version__, audit, closing, config, diagnostics, ledger


def by_file(
    ledger_path: Path,
    found: Sequence[ledger.Violation],
    out: dict[str, list[t.Diagnostic]],
    severity: t.DiagnosticSeverity | None = None,
) -> None:
    """Add each problem to ``out`` as an LSP diagnostic, by file URI. One with
    no place in a file sits on the ledger's first line."""
    for v in found:
        path = diagnostics.absolute(v.file, ledger_path.parent) if v.file else ledger_path
        line = (v.line or 1) - 1
        out.setdefault(path.as_uri(), []).append(
            t.Diagnostic(
                range=t.Range(t.Position(line, 0), t.Position(line + 1, 0)),
                message=v.message,
                severity=severity
                or (
                    t.DiagnosticSeverity.Error
                    if v.severity == "error"
                    else t.DiagnosticSeverity.Warning
                ),
                code=v.code or None,
                code_description=t.CodeDescription(href=f"{diagnostics.DOCS}#{v.code}")
                if v.code
                else None,
                source="quints",
                data={"fix": v.fix} if v.fix else None,
            )
        )


def quick_fixes(uri: str, shown: Sequence[t.Diagnostic]) -> list[t.CodeAction]:
    """A code action per diagnostic that carries a fix: insert it below the line."""
    return [
        t.CodeAction(
            title=f"Insert {d.data['fix'].strip()}",
            kind=t.CodeActionKind.QuickFix,
            diagnostics=[d],
            is_preferred=True,
            edit=t.WorkspaceEdit(
                changes={uri: [t.TextEdit(t.Range(d.range.end, d.range.end), d.data["fix"] + "\n")]}
            ),
        )
        for d in shown
        if d.source == "quints" and isinstance(d.data, dict) and d.data.get("fix")
    ]


def serve(ledger_path: Path) -> None:
    """Speak LSP on stdio until the editor hangs up."""
    ledger_path = ledger_path.resolve()
    server = LanguageServer("quints", __version__)
    published: set[str] = set()

    # pygls reads the annotation: `object` would make it pass the server too.
    def refresh(_params: t.InitializedParams | t.DidSaveTextDocumentParams) -> None:
        current: dict[str, list[t.Diagnostic]] = {}
        try:
            cfg = config.get()
            entries, errors = ledger.load_entries(ledger_path)
            loaded = (entries, ledger.violations(errors))
            by_file(ledger_path, audit.audit(ledger_path, cfg, loaded=loaded), current)
            # What `check --review` adds is to do, not wrong: shown as information.
            todo = closing.review(ledger_path, entries, cfg)
            by_file(ledger_path, todo, current, t.DiagnosticSeverity.Information)
        except ValueError as exc:  # a quints.toml that doesn't load
            by_file(ledger_path, [ledger.Violation("", "", "", str(exc))], current)
        for uri in published - current.keys():  # fixed since the last save
            server.text_document_publish_diagnostics(t.PublishDiagnosticsParams(uri, []))
        for uri, items in current.items():
            server.text_document_publish_diagnostics(t.PublishDiagnosticsParams(uri, items))
        published.clear()
        published.update(current)

    server.feature(t.INITIALIZED)(refresh)
    server.feature(t.TEXT_DOCUMENT_DID_SAVE)(refresh)

    @server.feature(
        t.TEXT_DOCUMENT_CODE_ACTION,
        t.CodeActionOptions(code_action_kinds=[t.CodeActionKind.QuickFix]),
    )
    def _code_action(params: t.CodeActionParams) -> list[t.CodeAction]:  # pyright: ignore[reportUnusedFunction]
        return quick_fixes(params.text_document.uri, params.context.diagnostics)

    server.start_io()
