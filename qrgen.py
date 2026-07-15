"""
qrgen.py — renders a QR code PNG from a string. Used only by web/app.py; the
CLI/TUI never import this, so they stay dependency-free. Needs `qrcode` +
`pypng` (see web/requirements.txt) — deliberately not Pillow, so this stays
a pure-Python dependency chain with nothing to compile.
"""

import io

import qrcode
from qrcode.image.pure import PyPNGImage


def render_qr_png(data: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(image_factory=PyPNGImage)
    buf = io.BytesIO()
    image.save(buf)
    return buf.getvalue()
