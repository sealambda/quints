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

for tool in vhs pdftoppm quints git; do
    command -v "$tool" >/dev/null || {
        echo "missing: $tool — see the header of media/generate.sh" >&2
        exit 1
    }
done

# The tapes run `quints init` in throwaway directories, where only the global
# git identity applies; without one the scaffold commit fails and the error
# is recorded straight into the GIFs.
git config --global user.name >/dev/null && git config --global user.email >/dev/null || {
    echo "missing: global git identity — set git config --global user.name / user.email" >&2
    exit 1
}

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
        "$income/Domestic/2026-07-02.acme.INV2026014.pdf" "$ASSETS/invoice-qr-bill"
    # The same invoice under the other reference scheme: a QR-IBAN plus the
    # bank's identification switches the QR-bill to a QR reference. The
    # scaffold ships those two lines commented out — uncommenting them is the
    # whole configuration change, so the docs' pair stays honest.
    sed -e 's/^    # qr_iban:/    qr_iban:/' -e 's/^    # qr_reference_id:/    qr_reference_id:/' \
        invoicing/issuer.yaml > invoicing/issuer-qrr.yaml
    quints invoice invoicing/acme-2026-07.yaml --issuer invoicing/issuer-qrr.yaml \
        --no-verify -o acme-qrr.pdf
    # Payment parts side by side: the bottom 105 mm of each A4 page at 150 dpi
    # (1240 x 1754 px; the payment part starts at 192 mm = 1134 px; a 3 mm margin above keeps the perforation line).
    pdftoppm -png -singlefile -r 150 -x 0 -y 1116 -W 1240 -H 638 \
        "$income/Domestic/2026-07-02.acme.INV2026014.pdf" "$ASSETS/payment-part-scor"
    pdftoppm -png -singlefile -r 150 -x 0 -y 1116 -W 1240 -H 638 \
        acme-qrr.pdf "$ASSETS/payment-part-qrr"
    pdftoppm -png -singlefile -r 110 -f 1 -l 1 \
        "$income/Export/2026-08-05.globex.INV2026015.pdf" "$ASSETS/invoice-export"
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
