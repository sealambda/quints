"""EPC QR code for a SEPA credit transfer in EUR (EPC069-12 v3.1).

The export invoice's scan-to-pay code: the payer's banking app reads it and
pre-fills the transfer. EPC069-12 is written for exactly this case — the same
data printed beside it in plain text, so the payer can compare the two."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from qrcode.image.svg import SvgPathFillImage

from .reference import PaymentReference

MAX_BYTES = 331  # §2.2: QR version 13 at error level M
MAX_NAME = 70
MAX_AMOUNT = Decimal("999999999.99")


def payload(name: str, iban: str, bic: str, amount: Decimal, ref: PaymentReference) -> str:
    """The `BCD` payload, version 002, UTF-8. The BIC is always included: the
    guidelines keep it mandatory for SCT participants outside the EEA, which
    every Swiss account is."""
    if len(name) > MAX_NAME:
        # Truncating would change the name the payer's bank checks the IBAN against.
        raise ValueError(
            f"beneficiary {name!r} is longer than the {MAX_NAME} characters an "
            f"EPC QR code carries — set a shorter `holder` on the account, exactly "
            f"as the bank holds it"
        )
    if not Decimal("0.01") <= amount <= MAX_AMOUNT:
        raise ValueError(f"an EPC QR code carries EUR 0.01 to {MAX_AMOUNT}, not {amount}")
    if ref.kind != "SCOR":
        raise ValueError(f"an EPC QR code takes an RF creditor reference, not {ref.kind}")
    lines = [
        "BCD",
        "002",
        "1",  # UTF-8
        "SCT",
        bic,
        name,
        iban,
        f"EUR{amount:.2f}",
        "",  # purpose code
        ref.value,  # structured remittance: the ISO 11649 RF reference
    ]
    data = "\n".join(lines)
    if len(data.encode()) > MAX_BYTES:
        raise ValueError(f"EPC QR payload is {len(data.encode())} bytes, over {MAX_BYTES}")
    return data


def write_svg(data: str, path: Path) -> None:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, image_factory=SvgPathFillImage)
    qr.add_data(data)
    qr.make(fit=True)
    with path.open("wb") as f:
        qr.make_image().save(f)
