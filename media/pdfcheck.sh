#!/usr/bin/env bash
# Validate the sample invoices against the standards `quints invoice` claims
# (PDF/A-2a + PDF/UA-1, see invoice/render.py `_compile`) with veraPDF, the
# reference validator. Typst accepting the export is not proof of conformance.
# Entry point: `make pdfcheck`; CI runs it on every PR.
#
# Requires: docker, and a synced workspace venv (`uv sync`).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERAPDF="verapdf/cli:v1.30.3"
export PATH="$REPO/.venv/bin:$PATH"
# `quints init` commits the scaffold: give it an identity, and keep the
# developer's global git config (signing, hooks) out of the way.
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_AUTHOR_NAME=quints GIT_AUTHOR_EMAIL=quints@example.com
export GIT_COMMITTER_NAME=quints GIT_COMMITTER_EMAIL=quints@example.com

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
(
    cd "$work"
    quints init books --samples --yes >/dev/null
    cd books
    # Both layouts: the Swiss QR-bill page and the EUR export with its EPC code.
    quints invoice invoicing/acme-2026-07.yaml -o "$work/out/qr-bill.pdf" >/dev/null
    quints invoice invoicing/globex-2026-08.yaml -o "$work/out/export.pdf" >/dev/null
)
# veraPDF exits non-zero when any file fails a profile.
for flavour in 2a ua1; do
    docker run --rm --platform linux/amd64 -v "$work/out:/data" "$VERAPDF" \
        -f "$flavour" --format text /data/qr-bill.pdf /data/export.pdf
done
