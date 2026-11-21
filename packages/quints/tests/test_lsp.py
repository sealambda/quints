"""`quints lsp`: the editor sees what `quints check` reports.

One test speaks the protocol to the real command over stdio, the way an
editor does: diagnostics on start, again on save, and the quick fix.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from lsprotocol import types as t

from quints import diagnostics, ledger, lsp

BOOKS = """\
option "documents" "documents"
2026-01-01 open Assets:CH:GmbH:Receivable:Trade CHF
  kmu: "1100"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export CHF
  kmu: "3400"

2026-03-01 * "Acme AG" "Work" ^INV2026001
  invoice: "INV2026001"
  document: "INV2026001.pdf"
  Assets:CH:GmbH:Receivable:Trade                100.00 CHF
  Income:CH:GmbH:Consulting:External:Export   -100.00 CHF
"""
UNBALANCED = """
2026-04-01 * "Acme AG" "Work"
  Assets:CH:GmbH:Receivable:Trade                100.00 CHF
  Income:CH:GmbH:Consulting:External:Export    -90.00 CHF
"""


def _books(root: Path) -> Path:
    (root / ".git").mkdir()  # the repository root the paths are relative to
    filed = root / "documents" / "Income" / "INV2026001.pdf"
    filed.parent.mkdir(parents=True)
    filed.write_bytes(b"%PDF-1.7 the issued invoice")
    led = root / "main.bean"
    led.write_text(BOOKS)
    return led


def test_diagnostics_carry_code_docs_link_and_fix(tmp_path: Path) -> None:
    led = _books(tmp_path)
    found = [
        ledger.Violation("", "", "", "wrong", code="vat-mismatch", file="main.bean", line=7),
        ledger.Violation("", "", "", "add it", code="invoice-unhashed", severity="warning",
                         file="main.bean", line=7, fix='  sha256: "abc"'),
        ledger.Violation("", "", "", "nowhere", code="statements-mismatch"),
    ]  # fmt: skip
    out: dict[str, list[t.Diagnostic]] = {}
    lsp.by_file(led, found, out)
    (items,) = out.values()
    assert next(iter(out)) == led.resolve().as_uri()
    error, warning, unplaced = items
    assert (error.range.start.line, error.severity, error.code) == (
        6,
        t.DiagnosticSeverity.Error,
        "vat-mismatch",
    )
    assert error.code_description is not None
    assert error.code_description.href == f"{diagnostics.DOCS}#vat-mismatch"
    assert warning.severity == t.DiagnosticSeverity.Warning
    assert unplaced.range.start.line == 0  # no place in a file: the ledger's first line

    (action,) = lsp.quick_fixes(next(iter(out)), items)
    assert action.edit is not None and action.edit.changes is not None
    (edit,) = action.edit.changes[led.resolve().as_uri()]
    # Inserted at the start of the line below the transaction's header.
    assert (edit.range.start.line, edit.range.start.character) == (7, 0)
    assert edit.new_text == '  sha256: "abc"\n'


class _Editor:
    """The client's half of the protocol, over the server's stdio."""

    def __init__(self, cwd: Path) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "quints.cli", "lsp"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        # A server that never answers must fail the test, not hang the suite.
        self.watchdog = threading.Timer(30, self.proc.kill)
        self.watchdog.start()
        self.ids = 0

    def send(self, method: str, params: object, *, request: bool = False) -> int:
        message: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request:
            self.ids += 1
            message["id"] = self.ids
        body = json.dumps(message).encode()
        assert self.proc.stdin is not None
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        self.proc.stdin.flush()
        return self.ids

    def read(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        length = 0
        while (line := self.proc.stdout.readline().strip()) or not length:
            assert self.proc.poll() is None or line, "the server hung up"
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        return json.loads(self.proc.stdout.read(length))

    def until(self, wanted: str) -> dict[str, Any]:
        """The next message that is a ``wanted`` notification, or answers a request."""
        while True:
            message = self.read()
            if message.get("method") == wanted or (wanted == "result" and "result" in message):
                return message

    def close(self) -> None:
        self.watchdog.cancel()
        if self.proc.poll() is None:  # still up: leave the way an editor does
            self.send("shutdown", None, request=True)
            self.until("result")
            self.send("exit", None)
        self.proc.communicate(timeout=10)  # waits, and closes both pipes


def test_an_editor_gets_diagnostics_on_start_and_on_save(tmp_path: Path) -> None:
    led = _books(tmp_path)
    uri = led.resolve().as_uri()
    editor = _Editor(tmp_path)
    try:
        editor.send(
            "initialize",
            {"processId": None, "rootUri": tmp_path.as_uri(), "capabilities": {}},
            request=True,
        )
        capabilities = editor.until("result")["result"]["capabilities"]
        assert capabilities["textDocumentSync"]["save"]  # or no editor reports a save
        assert capabilities["codeActionProvider"]
        editor.send("initialized", {})

        first = editor.until("textDocument/publishDiagnostics")["params"]
        assert first["uri"] == uri
        (unhashed,) = first["diagnostics"]
        assert (unhashed["code"], unhashed["source"]) == ("invoice-unhashed", "quints")
        assert unhashed["range"]["start"]["line"] == 6

        request = editor.send(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": unhashed["range"],
                "context": {"diagnostics": [unhashed]},
            },
            request=True,
        )
        answer = editor.until("result")
        assert answer["id"] == request
        (action,) = answer["result"]
        (edit,) = action["edit"]["changes"][uri]
        assert edit["newText"].startswith('  sha256: "')

        # Apply the fix and break something else, then save.
        lines = BOOKS.splitlines(keepends=True)
        lines.insert(edit["range"]["start"]["line"], edit["newText"])
        led.write_text("".join(lines) + UNBALANCED)
        editor.send("textDocument/didSave", {"textDocument": {"uri": uri}})
        second = editor.until("textDocument/publishDiagnostics")["params"]
        # The unhashed invoice is gone. The new entry is wrong (an error), and
        # has no document yet: to do, so information, not a warning.
        assert [(d["code"], d["severity"]) for d in second["diagnostics"]] == [
            ("ledger", 1),
            ("document-missing", 3),
        ]
    finally:
        editor.close()
