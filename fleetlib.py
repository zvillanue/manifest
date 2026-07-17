"""
fleetlib — shared core for fleetctl: DB schema, serial/passphrase generation,
and the op_* functions that implement every unit/build operation.

Imported by the `fleetctl` CLI/TUI script and by web/app.py. Nothing in here
knows about argparse, curses, or Flask — it just does the work and raises
FleetError on bad input, so every front end can decide how to surface that.

The database is encrypted at rest with SQLCipher (this whole file lives in a
Nextcloud-synced folder, so "it's on an encrypted disk" isn't enough — the
DB syncs to the Nextcloud server and every other device syncing this vault).
This means fleetctl is no longer stdlib-only: it needs `sqlcipher3-binary`
(see requirements.txt). The encryption key itself must NEVER live inside
this vault — see FLEETCTL_DB_KEY / FLEETCTL_DB_KEY_FILE below.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from datetime import date, datetime
from pathlib import Path

try:
    import sqlcipher3.dbapi2 as sqlite3
except ImportError as e:
    raise ImportError(
        "fleetctl needs the 'sqlcipher3-binary' package (the database is encrypted "
        "at rest). Install it with: pip install -r requirements.txt"
    ) from e

import dolibarr_sync

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fleetctl.db"
WORDLIST_PATH = ROOT / "wordlist" / "eff_large_wordlist.txt"
CHECKLISTS_DIR = ROOT / "checklists"
PHOTOS_DIR = ROOT / "photos"
POSTINSTALL_DIR = ROOT / "postinstall"

DB_KEY_ENV = "FLEETCTL_DB_KEY"
DB_KEY_FILE_ENV = "FLEETCTL_DB_KEY_FILE"

# Crockford base32 alphabet: excludes I, L, O, U to avoid 1/l, 0/O, v/u
# confusion when a serial is read aloud or handwritten on a handoff card.
SERIAL_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

LINE_PREFIXES = {"laptop": "LT", "pixel": "PX"}

STATUS_FLOW = [
    "Acquired",
    "Refurb",
    "QA",
    "Listed",
    "Sold",
    "Delivered",
    "Warrantied",
    "Repurposed",
    "Parted",
]


class FleetError(Exception):
    """A user-facing error — message is safe to print/display as-is."""


# --------------------------------------------------------------------------
# DB
# --------------------------------------------------------------------------

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Additive, idempotent schema migration: adds a column to an existing table
    if it's missing. No-ops if the table doesn't exist yet (a fresh DB gets the
    column from SCHEMA directly when it's created)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()


def generate_db_key() -> str:
    """A fresh 256-bit key, hex-encoded, for `fleetctl gen-key`. Not persisted
    anywhere by fleetctl — the operator is responsible for saving it (a
    password manager, not this Nextcloud-synced folder) and setting it as
    FLEETCTL_DB_KEY before running fleetctl again."""
    return secrets.token_hex(32)


def _validate_db_key(key: str, source: str) -> str:
    if len(key) != 64 or any(c not in "0123456789abcdefABCDEF" for c in key):
        raise FleetError(
            f"{source} must be a 64-character hex string, as produced by `fleetctl gen-key` "
            f"(got {len(key)} characters). fleetctl deliberately doesn't support a "
            f"human-chosen passphrase here — this key is meant to be high-entropy and stored "
            f"in a password manager, not memorized."
        )
    return key


def _resolve_db_key() -> str:
    key = os.environ.get(DB_KEY_ENV)
    if key:
        return _validate_db_key(key.strip(), DB_KEY_ENV)
    key_file = os.environ.get(DB_KEY_FILE_ENV)
    if key_file:
        path = Path(key_file)
        if not path.exists():
            raise FleetError(f"{DB_KEY_FILE_ENV} is set to {path} but that file doesn't exist.")
        return _validate_db_key(path.read_text().strip(), f"{DB_KEY_FILE_ENV} ({path})")
    raise FleetError(
        f"No database encryption key found. Set {DB_KEY_ENV} (or {DB_KEY_FILE_ENV} pointing "
        f"at a file containing it) before running fleetctl. Run `fleetctl gen-key` to generate "
        f"one if you don't have one yet — save it somewhere that is NOT this Nextcloud-synced "
        f"folder, e.g. a password manager. There is no recovery if this key is lost."
    )


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = _resolve_db_key()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
    except sqlite3.DatabaseError as e:
        raise FleetError(
            f"Could not open {DB_PATH} with the provided key — wrong {DB_KEY_ENV}, "
            f"or this isn't a fleetctl database."
        ) from e
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_column(conn, "units", "qr_token", "TEXT")
    _ensure_column(conn, "units", "acquisition_date", "TEXT")
    _ensure_column(conn, "units", "acquisition_source", "TEXT")
    _ensure_column(conn, "units", "acquisition_cost", "TEXT")
    _ensure_column(conn, "units", "buyer_name", "TEXT")
    _ensure_column(conn, "units", "buyer_email", "TEXT")
    conn.executescript(SCHEMA)  # idempotent (CREATE TABLE IF NOT EXISTS) — picks up new tables
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS builds (
    build_id TEXT PRIMARY KEY,
    product_line TEXT NOT NULL,
    tier INTEGER,
    description TEXT,
    postinstall_script_path TEXT NOT NULL,
    script_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial TEXT UNIQUE NOT NULL,
    qr_token TEXT UNIQUE,
    product_line TEXT NOT NULL,
    tier INTEGER,
    build_id TEXT REFERENCES builds(build_id),
    oem_make TEXT,
    oem_model TEXT,
    hardware_config_json TEXT,
    date_of_manufacture TEXT NOT NULL,
    acquisition_date TEXT,
    acquisition_source TEXT,
    acquisition_cost TEXT,
    date_of_sale TEXT,
    sale_price TEXT,
    buyer_name TEXT,
    buyer_email TEXT,
    status TEXT NOT NULL DEFAULT 'Acquired',
    warrantied INTEGER NOT NULL DEFAULT 0,
    warranty_notes TEXT,
    repurposed INTEGER NOT NULL DEFAULT 0,
    repurposed_from TEXT,
    checklist_path TEXT,
    checklist_completed_at TEXT,
    temp_login_passphrase TEXT,
    temp_uefi_passphrase TEXT,
    temp_luks_passphrase TEXT,
    secrets_purged_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS part_replacements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_serial TEXT NOT NULL REFERENCES units(serial),
    part_type TEXT NOT NULL,
    replaced_at TEXT NOT NULL,
    old_make TEXT,
    old_model TEXT,
    old_model_number TEXT,
    old_serial_number TEXT,
    old_date_of_mfg TEXT,
    new_make TEXT,
    new_model TEXT,
    new_model_number TEXT,
    new_serial_number TEXT,
    new_date_of_mfg TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shipment_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_serial TEXT NOT NULL REFERENCES units(serial),
    file_path TEXT NOT NULL,
    caption TEXT,
    uploaded_at TEXT NOT NULL
);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


def require_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FleetError(f"No database at {DB_PATH} yet. Run `fleetctl init` first.")
    return get_conn()


# --------------------------------------------------------------------------
# Serial numbers: PREFIX[TIER]-YYMMDD-RAND3-CHECK
#   e.g. LT2-260713-9F2-K   (laptop, tier 2, made 2026-07-13)
#        PX-260713-K7M-9    (pixel, no tier)
# --------------------------------------------------------------------------

def _checksum_char(payload: str) -> str:
    total = sum((ord(c) * (i + 1)) for i, c in enumerate(payload))
    return SERIAL_ALPHABET[total % len(SERIAL_ALPHABET)]


def _serial_parts(prefix: str, tier: int | None, mfg_date: date) -> tuple[str, str]:
    tier_str = str(tier) if tier else ""
    date_str = mfg_date.strftime("%y%m%d")
    rand3 = "".join(secrets.choice(SERIAL_ALPHABET) for _ in range(3))
    payload = f"{prefix}{tier_str}{date_str}{rand3}"
    check = _checksum_char(payload)
    return f"{prefix}{tier_str}-{date_str}-{rand3}", check


def generate_serial(conn: sqlite3.Connection, product_line: str, tier: int | None,
                     mfg_date: date) -> str:
    prefix = LINE_PREFIXES[product_line]
    for _ in range(50):
        body, check = _serial_parts(prefix, tier, mfg_date)
        serial = f"{body}-{check}"
        existing = conn.execute(
            "SELECT 1 FROM units WHERE serial = ?", (serial,)
        ).fetchone()
        if not existing:
            return serial
    raise FleetError("Could not generate a unique serial after 50 attempts")


def verify_serial(serial: str) -> bool:
    try:
        body, check = serial.rsplit("-", 1)
        payload = body.replace("-", "")
    except ValueError:
        return False
    return _checksum_char(payload) == check


# --------------------------------------------------------------------------
# Passphrases (diceware, EFF large wordlist — 12.9 bits/word)
# --------------------------------------------------------------------------

_WORDLIST_CACHE: list[str] | None = None


def load_wordlist() -> list[str]:
    global _WORDLIST_CACHE
    if _WORDLIST_CACHE is None:
        if not WORDLIST_PATH.exists():
            raise FleetError(f"Wordlist not found at {WORDLIST_PATH}")
        _WORDLIST_CACHE = [
            line.strip() for line in WORDLIST_PATH.read_text().splitlines() if line.strip()
        ]
    return _WORDLIST_CACHE


def gen_passphrase(n_words: int = 6) -> str:
    words = load_wordlist()
    return "-".join(secrets.choice(words) for _ in range(n_words))


# --------------------------------------------------------------------------
# Core operations — shared by the CLI, the TUI, and the web app. Each raises
# FleetError on bad input; callers decide how to surface that.
# --------------------------------------------------------------------------

def op_register_build(conn: sqlite3.Connection, build_id: str, line: str,
                       tier: int | None, script: str, desc: str | None) -> tuple[Path, str]:
    script_path = Path(script)
    # Relative paths are resolved against ROOT (this directory), not the
    # process's cwd — the CLI is normally run from ROOT so this is a no-op
    # there, but the web app's cwd depends on how it was launched.
    script_path = script_path if script_path.is_absolute() else (ROOT / script_path)
    script_path = script_path.resolve()
    if not script_path.exists():
        raise FleetError(f"Script not found: {script_path}")
    try:
        rel_path = script_path.relative_to(ROOT)
    except ValueError:
        rel_path = script_path
    sha256 = hashlib.sha256(script_path.read_bytes()).hexdigest()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO builds (build_id, product_line, tier, description,
               postinstall_script_path, script_sha256, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(build_id) DO UPDATE SET
               product_line=excluded.product_line, tier=excluded.tier,
               description=excluded.description,
               postinstall_script_path=excluded.postinstall_script_path,
               script_sha256=excluded.script_sha256""",
        (build_id, line, tier, desc, str(rel_path), sha256, now),
    )
    conn.commit()
    return rel_path, sha256


def op_list_builds(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM builds ORDER BY build_id").fetchall()


def op_verify_build(conn: sqlite3.Connection, build_id: str) -> tuple[bool, str, str]:
    row = conn.execute("SELECT * FROM builds WHERE build_id = ?", (build_id,)).fetchone()
    if not row:
        raise FleetError(f"No such build: {build_id}")
    script_path = ROOT / row["postinstall_script_path"]
    if not script_path.exists():
        raise FleetError(f"Registered script missing on disk: {script_path}")
    current = hashlib.sha256(script_path.read_bytes()).hexdigest()
    return current == row["script_sha256"], row["script_sha256"], current


def list_postinstall_files() -> list[str]:
    if not POSTINSTALL_DIR.exists():
        return []
    return sorted(
        str(p.relative_to(ROOT)) for p in POSTINSTALL_DIR.iterdir()
        if p.is_file() and p.suffix in (".sh", ".md")
    )


def op_create_unit(conn: sqlite3.Connection, line: str, tier: int | None, make: str | None,
                    model: str | None, build: str | None, mfg_date_str: str | None,
                    words: int, repurposed_from: str | None = None,
                    acquisition_date_str: str | None = None, acquisition_source: str | None = None,
                    acquisition_cost: str | None = None) -> dict:
    if build:
        exists = conn.execute("SELECT 1 FROM builds WHERE build_id = ?", (build,)).fetchone()
        if not exists:
            raise FleetError(f"No such build '{build}'. Register it first.")
    try:
        mfg_date = date.fromisoformat(mfg_date_str) if mfg_date_str else date.today()
    except ValueError:
        raise FleetError(f"Bad date '{mfg_date_str}', expected YYYY-MM-DD")
    try:
        acquisition_date = date.fromisoformat(acquisition_date_str) if acquisition_date_str else None
    except ValueError:
        raise FleetError(f"Bad date '{acquisition_date_str}', expected YYYY-MM-DD")

    serial = generate_serial(conn, line, tier, mfg_date)
    qr_token = _generate_qr_token(conn)
    now = datetime.now().isoformat(timespec="seconds")
    login_pp, uefi_pp, luks_pp = gen_passphrase(words), gen_passphrase(words), gen_passphrase(words)

    conn.execute(
        """INSERT INTO units (serial, qr_token, product_line, tier, build_id, oem_make, oem_model,
               date_of_manufacture, acquisition_date, acquisition_source, acquisition_cost,
               status, repurposed_from, temp_login_passphrase,
               temp_uefi_passphrase, temp_luks_passphrase, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (serial, qr_token, line, tier, build, make, model, mfg_date.isoformat(),
         acquisition_date.isoformat() if acquisition_date else None, acquisition_source, acquisition_cost,
         "Acquired", repurposed_from, login_pp, uefi_pp, luks_pp, now, now),
    )
    conn.commit()
    return {
        "serial": serial, "line": line, "tier": tier, "make": make, "model": model,
        "build": build, "mfg_date": mfg_date.isoformat(),
        "login_passphrase": login_pp, "uefi_passphrase": uefi_pp, "luks_passphrase": luks_pp,
    }


def op_set_acquisition(conn: sqlite3.Connection, serial: str, date_str: str | None,
                        source: str | None, cost: str | None) -> str:
    op_get_unit(conn, serial)
    try:
        acq_date = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        raise FleetError(f"Bad date '{date_str}', expected YYYY-MM-DD")
    _touch(
        conn, serial,
        acquisition_date=acq_date.isoformat(), acquisition_source=source, acquisition_cost=cost,
    )
    return acq_date.isoformat()


def op_add_part_replacement(
    conn: sqlite3.Connection, serial: str, part_type: str, replaced_at_str: str | None,
    old_make: str | None = None, old_model: str | None = None, old_model_number: str | None = None,
    old_serial_number: str | None = None, old_date_of_mfg: str | None = None,
    new_make: str | None = None, new_model: str | None = None, new_model_number: str | None = None,
    new_serial_number: str | None = None, new_date_of_mfg: str | None = None,
    notes: str | None = None,
) -> int:
    op_get_unit(conn, serial)
    if not part_type or not part_type.strip():
        raise FleetError("Part type is required (e.g. Battery, RAM, Storage, Screen).")
    try:
        replaced_at = date.fromisoformat(replaced_at_str) if replaced_at_str else date.today()
    except ValueError:
        raise FleetError(f"Bad date '{replaced_at_str}', expected YYYY-MM-DD")
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO part_replacements (unit_serial, part_type, replaced_at, old_make, old_model,
               old_model_number, old_serial_number, old_date_of_mfg, new_make, new_model,
               new_model_number, new_serial_number, new_date_of_mfg, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (serial, part_type.strip(), replaced_at.isoformat(), old_make, old_model, old_model_number,
         old_serial_number, old_date_of_mfg, new_make, new_model, new_model_number,
         new_serial_number, new_date_of_mfg, notes, now),
    )
    conn.commit()
    return cur.lastrowid


def op_list_part_replacements(conn: sqlite3.Connection, serial: str) -> list[sqlite3.Row]:
    op_get_unit(conn, serial)
    return conn.execute(
        "SELECT * FROM part_replacements WHERE unit_serial = ? ORDER BY replaced_at DESC, id DESC",
        (serial,),
    ).fetchall()


def op_add_shipment_photo_bytes(conn: sqlite3.Connection, serial: str, suffix: str, data: bytes,
                                 caption: str | None = None) -> Path:
    op_get_unit(conn, serial)
    unit_dir = PHOTOS_DIR / serial
    unit_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    # Timestamp + a few random hex chars keeps filenames unique even when
    # several photos are uploaded in the same batch (same second).
    stamp = now.strftime("%Y%m%dT%H%M%S") + secrets.token_hex(3)
    dest = unit_dir / f"{stamp}{suffix or ''}"
    dest.write_bytes(data)
    rel_path = dest.relative_to(ROOT)
    conn.execute(
        """INSERT INTO shipment_photos (unit_serial, file_path, caption, uploaded_at)
           VALUES (?, ?, ?, ?)""",
        (serial, str(rel_path), caption, now.isoformat(timespec="seconds")),
    )
    conn.commit()
    return rel_path


def op_add_shipment_photo(conn: sqlite3.Connection, serial: str, path: str,
                           caption: str | None = None) -> Path:
    src = Path(path)
    if not src.exists():
        raise FleetError(f"File not found: {src}")
    return op_add_shipment_photo_bytes(conn, serial, src.suffix, src.read_bytes(), caption)


def op_list_shipment_photos(conn: sqlite3.Connection, serial: str) -> list[sqlite3.Row]:
    op_get_unit(conn, serial)
    return conn.execute(
        "SELECT * FROM shipment_photos WHERE unit_serial = ? ORDER BY uploaded_at DESC, id DESC",
        (serial,),
    ).fetchall()


def op_get_shipment_photo(conn: sqlite3.Connection, serial: str, photo_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM shipment_photos WHERE unit_serial = ? AND id = ?", (serial, photo_id),
    ).fetchone()
    if not row:
        raise FleetError(f"No such photo #{photo_id} for {serial}")
    return row


PART_TYPE_SUGGESTIONS = [
    "Battery", "RAM", "Storage (SSD/HDD)", "Screen/Display", "Keyboard",
    "Trackpad", "Fan/Cooling", "Motherboard", "Charger/PSU", "Camera", "Speaker",
]


def _generate_qr_token(conn: sqlite3.Connection) -> str:
    """A random token with no relation to the serial, hardware, or anything
    else — the whole point is that it's meaningless to anyone without access
    to this database. secrets.token_urlsafe(16) is 128 bits of entropy."""
    for _ in range(10):
        token = secrets.token_urlsafe(16)
        exists = conn.execute("SELECT 1 FROM units WHERE qr_token = ?", (token,)).fetchone()
        if not exists:
            return token
    raise FleetError("Could not generate a unique QR token after 10 attempts")


def op_ensure_qr_token(conn: sqlite3.Connection, serial: str) -> str:
    """Returns the unit's qr_token, generating and storing one first if it
    doesn't have one yet (e.g. a unit created before this feature existed)."""
    unit = op_get_unit(conn, serial)
    if unit["qr_token"]:
        return unit["qr_token"]
    token = _generate_qr_token(conn)
    _touch(conn, serial, qr_token=token)
    return token


def op_find_by_qr_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM units WHERE qr_token = ?", (token,)).fetchone()
    if not row:
        raise FleetError("No unit matches that QR token.")
    return row


def op_get_unit(conn: sqlite3.Connection, serial: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM units WHERE serial = ?", (serial,)).fetchone()
    if not row:
        raise FleetError(f"No such unit: {serial}")
    return row


def _touch(conn: sqlite3.Connection, serial: str, **fields) -> None:
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE units SET {set_clause} WHERE serial = ?",
        (*fields.values(), serial),
    )
    conn.commit()


def op_import_hardware_json(conn: sqlite3.Connection, serial: str, json_text: str) -> bool:
    unit = op_get_unit(conn, serial)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise FleetError(f"Not valid JSON: {e}")

    fields = {"hardware_config_json": json.dumps(data)}
    system = data.get("system") or {}
    backfilled = False
    if not unit["oem_make"] and system.get("manufacturer"):
        fields["oem_make"] = system["manufacturer"]
        backfilled = True
    if not unit["oem_model"] and system.get("product_name"):
        fields["oem_model"] = system["product_name"]
        backfilled = True

    _touch(conn, serial, **fields)
    return backfilled


def op_import_hardware(conn: sqlite3.Connection, serial: str, json_file: str) -> bool:
    json_path = Path(json_file)
    if not json_path.exists():
        raise FleetError(f"File not found: {json_path}")
    return op_import_hardware_json(conn, serial, json_path.read_text())


def op_save_checklist_bytes(conn: sqlite3.Connection, serial: str, suffix: str, data: bytes) -> Path:
    op_get_unit(conn, serial)
    CHECKLISTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = CHECKLISTS_DIR / f"{serial}{suffix}"
    dest.write_bytes(data)
    now = datetime.now().isoformat(timespec="seconds")
    _touch(conn, serial, checklist_path=str(dest.relative_to(ROOT)), checklist_completed_at=now)
    return dest.relative_to(ROOT)


def op_save_checklist(conn: sqlite3.Connection, serial: str, path: str) -> Path:
    src = Path(path)
    if not src.exists():
        raise FleetError(f"File not found: {src}")
    return op_save_checklist_bytes(conn, serial, src.suffix, src.read_bytes())


def op_set_status(conn: sqlite3.Connection, serial: str, new_status: str) -> None:
    if new_status not in STATUS_FLOW:
        raise FleetError(f"Unknown status '{new_status}'. Known: {', '.join(STATUS_FLOW)}")
    op_get_unit(conn, serial)
    _touch(conn, serial, status=new_status)


def op_sell(
    conn: sqlite3.Connection, serial: str, date_str: str | None, price: str | None,
    buyer_name: str | None = None, buyer_email: str | None = None,
) -> str:
    unit = op_get_unit(conn, serial)
    try:
        sale_date = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        raise FleetError(f"Bad date '{date_str}', expected YYYY-MM-DD")
    _touch(
        conn, serial, date_of_sale=sale_date.isoformat(), sale_price=price,
        buyer_name=buyer_name, buyer_email=buyer_email, status="Sold",
    )
    description = f"{unit['oem_make'] or ''} {unit['oem_model'] or ''} ({serial})".strip()
    dolibarr_sync.sync_sale(serial, description, price, sale_date.isoformat(), buyer_name, buyer_email)
    return sale_date.isoformat()


def op_warranty(conn: sqlite3.Connection, serial: str, note: str) -> None:
    unit = op_get_unit(conn, serial)
    now = datetime.now().isoformat(timespec="seconds")
    prior = unit["warranty_notes"] or ""
    combined = f"{prior}\n[{now}] {note}".strip()
    _touch(conn, serial, warrantied=1, warranty_notes=combined, status="Warrantied")


def op_repurpose(conn: sqlite3.Connection, serial: str, new_line: str | None,
                  new_tier: int | None, build: str | None, words: int) -> dict:
    old = op_get_unit(conn, serial)
    _touch(conn, serial, repurposed=1, status="Repurposed")
    line = new_line or old["product_line"]
    tier = new_tier if new_tier is not None else old["tier"]
    return op_create_unit(
        conn, line, tier, old["oem_make"], old["oem_model"], build, None, words,
        repurposed_from=old["serial"],
    )


def op_purge_secrets(conn: sqlite3.Connection, serial: str) -> None:
    op_get_unit(conn, serial)
    now = datetime.now().isoformat(timespec="seconds")
    _touch(
        conn, serial,
        temp_login_passphrase=None, temp_uefi_passphrase=None, temp_luks_passphrase=None,
        secrets_purged_at=now,
    )


def op_list_units(conn: sqlite3.Connection, status: str | None = None,
                   line: str | None = None) -> list[sqlite3.Row]:
    query = ("SELECT serial, product_line, tier, oem_make, oem_model, status, "
              "date_of_manufacture, date_of_sale FROM units")
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if line:
        clauses.append("product_line = ?")
        params.append(line)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY date_of_manufacture DESC"
    return conn.execute(query, params).fetchall()


def op_handoff_card(conn: sqlite3.Connection, serial: str) -> sqlite3.Row:
    unit = op_get_unit(conn, serial)
    if unit["secrets_purged_at"]:
        raise FleetError(
            f"Temp secrets for {serial} were purged on {unit['secrets_purged_at']} "
            "(buyer already changed them). Nothing to show."
        )
    return unit
