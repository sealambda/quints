#!/usr/bin/env bash
# Regenerate every visual asset in docs/assets/ from source — terminal GIFs
# from the committed VHS tapes (media/*.tape), PDF page previews from the
# sample project. Entry point: `make media`.
#
# Requires: vhs (brew install vhs — pulls ttyd + ffmpeg), pdftoppm
# (brew install poppler), and a synced workspace venv (`uv sync`).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$REPO/docs/assets"
export PATH="$REPO/.venv/bin:$PATH"

for tool in vhs pdftoppm quints; do
    command -v "$tool" >/dev/null || {
        echo "missing: $tool — see the header of media/generate.sh" >&2
        exit 1
    }
done

mkdir -p "$ASSETS"

# PDF page previews: render the sample invoices and statements with the real
# CLI, then rasterize page 1. Invoice numbers are pinned in the sample YAMLs;
# if the samples change, this fails loudly — update it alongside them.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
(
    cd "$work"
    quints init my-books --samples --yes >/dev/null
    cd my-books
    quints invoice invoicing/acme-2026-07.yaml
    quints invoice invoicing/globex-2026-08.yaml
    quints report statements --year 2026 --lang de
    # Invoices are filed like any other beancount document: under the income
    # account's folder in documents/, date-prefixed.
    income="documents/Income/CH/GmbH/Consulting/External"
    pdftoppm -png -singlefile -r 110 -f 1 -l 1 \
        "$income/Domestic/2026-07-02.acme-ag.INV2026014.pdf" "$ASSETS/invoice-qr-bill"
    pdftoppm -png -singlefile -r 110 -f 1 -l 1 \
        "$income/Export/2026-08-05.globex-ltd.INV2026015.pdf" "$ASSETS/invoice-export"
    pdftoppm -png -singlefile -r 110 -f 1 -l 1 statements-2026-de.pdf "$ASSETS/statements"
)

# Terminal GIFs: each tape scaffolds its own throwaway project (hidden setup)
# and writes into docs/assets/. Output paths resolve relative to this cwd.
cd "$REPO"
for tape in media/*.tape; do
    echo "vhs: $tape"
    vhs "$tape"
done

ls -lh "$ASSETS"
