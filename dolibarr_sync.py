"""
dolibarr_sync — optional, best-effort push of a fleetctl sale into Dolibarr
(customer + validated invoice) via its REST API.

Stdlib-only (urllib) — no new pip dependency, so importing this from
fleetlib.py doesn't change the CLI/TUI's dependency footprint (see
README.md's "Keep the CLI/TUI dependency-light" note).

No-ops entirely unless both DOLIBARR_API_URL and DOLIBARR_API_KEY are set.
fleetctl's own database is the system of record for inventory regardless of
whether Dolibarr is reachable — a sync failure here is logged to stderr as a
warning, never raised as a FleetError, so it can't block recording a sale.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL_ENV = "DOLIBARR_API_URL"
API_KEY_ENV = "DOLIBARR_API_KEY"


def _configured() -> tuple[str, str] | None:
    url = os.environ.get(API_URL_ENV)
    key = os.environ.get(API_KEY_ENV)
    if not (url and key):
        return None
    return url.rstrip("/"), key


def _request(base_url: str, api_key: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{base_url}{path}",
        method=method,
        headers={
            "DOLAPIKEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _find_or_create_thirdparty(base_url: str, api_key: str, buyer_name: str, buyer_email: str | None) -> int:
    if buyer_email:
        sqlfilters = urllib.parse.quote(f"(t.email:=:'{buyer_email}')")
        matches = _request(base_url, api_key, "GET", f"/thirdparties?sqlfilters={sqlfilters}")
        if matches:
            return int(matches[0]["id"])
    payload = {"name": buyer_name, "client": 1}
    if buyer_email:
        payload["email"] = buyer_email
    return int(_request(base_url, api_key, "POST", "/thirdparties", payload))


def sync_sale(
    serial: str,
    description: str,
    sale_price: str | None,
    sale_date: str,
    buyer_name: str | None,
    buyer_email: str | None,
) -> None:
    """Best-effort: create-or-find the customer, then a validated (unpaid)
    invoice for this sale. Any failure is a warning on stderr, not raised."""
    configured = _configured()
    if not configured:
        return
    if not buyer_name:
        print(f"dolibarr sync skipped for {serial}: no buyer name recorded", file=sys.stderr)
        return
    base_url, api_key = configured
    try:
        socid = _find_or_create_thirdparty(base_url, api_key, buyer_name, buyer_email)
        invoice_payload = {
            "socid": socid,
            "date": sale_date,
            "lines": [
                {"desc": description, "qty": 1, "subprice": sale_price or "0", "tva_tx": 0}
            ],
        }
        invoice_id = _request(base_url, api_key, "POST", "/invoices", invoice_payload)
        _request(base_url, api_key, "POST", f"/invoices/{invoice_id}/validate", {"notrigger": 0})
        print(f"dolibarr: created invoice {invoice_id} for {serial} (customer {socid})", file=sys.stderr)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError) as e:
        print(f"WARNING: dolibarr sync failed for {serial}: {e}", file=sys.stderr)
