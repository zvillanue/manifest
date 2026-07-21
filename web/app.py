#!/usr/bin/env python3
"""
fleetctl web GUI — thin Flask front end over fleetlib.py, the same core used
by the `fleetctl` CLI/TUI. No logic lives here beyond HTTP plumbing.

SECURITY: this can display temporary device passphrases in plaintext. Set
FLEETCTL_WEB_USER + FLEETCTL_WEB_PASSWORD to require a login, and don't
expose this port beyond localhost/your trusted LAN. See README.md.
"""

import json
import mimetypes
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fleetlib as fl
from app_catalog import APPS
from scriptgen import OS_TARGETS, GenOptions, generate_script
from qrgen import render_qr_png

from flask import Flask, Response, flash, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("FLEETCTL_SECRET_KEY") or os.urandom(32)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

WEB_USER = os.environ.get("FLEETCTL_WEB_USER")
WEB_PASSWORD = os.environ.get("FLEETCTL_WEB_PASSWORD")

if not (WEB_USER and WEB_PASSWORD):
    print(
        "WARNING: FLEETCTL_WEB_USER / FLEETCTL_WEB_PASSWORD are not set — the web UI is "
        "running with NO AUTHENTICATION. It can display temporary device passphrases in "
        "plaintext. Set both env vars, and do not expose this port beyond localhost or "
        "your trusted LAN.",
        file=sys.stderr,
    )


# A real login form (rather than HTTP Basic Auth) so password managers can
# see and fill/save the fields — browsers' native Basic Auth prompt isn't a
# <form>, and most password managers don't autofill or offer to save it.
@app.before_request
def check_auth():
    if not (WEB_USER and WEB_PASSWORD):
        return None
    if request.endpoint in ("login", "static"):
        return None
    if session.get("authed"):
        return None
    return redirect(url_for("login", next=request.full_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not (WEB_USER and WEB_PASSWORD):
        return redirect(url_for("index"))
    next_url = request.values.get("next") or url_for("units_list")
    if request.method == "POST":
        if request.form.get("username") == WEB_USER and request.form.get("password") == WEB_PASSWORD:
            session.permanent = True
            session["authed"] = True
            return redirect(next_url)
        flash("Incorrect username or password", "error")
    return render_template("login.html", next=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.before_request
def ensure_db():
    if not fl.DB_PATH.exists():
        fl.init_db()


@app.errorhandler(fl.FleetError)
def handle_fleet_error(e):
    flash(str(e), "error")
    return redirect(request.referrer or url_for("units_list"))


@app.route("/")
def index():
    return redirect(url_for("units_list"))


@app.route("/units")
def units_list():
    conn = fl.get_conn()
    status = request.args.get("status") or None
    line = request.args.get("line") or None
    rows = fl.op_list_units(conn, status, line)
    return render_template(
        "units_list.html", rows=rows, statuses=fl.STATUS_FLOW,
        lines=list(fl.LINE_PREFIXES.keys()), cur_status=status, cur_line=line,
    )


@app.route("/units/new", methods=["GET", "POST"])
def unit_new():
    conn = fl.get_conn()
    if request.method == "POST":
        tier = request.form.get("tier") or None
        words = int(request.form.get("words") or 6)
        result = fl.op_create_unit(
            conn, request.form["line"], int(tier) if tier else None,
            request.form.get("make") or None, request.form.get("model") or None,
            request.form.get("build") or None, request.form.get("mfg_date") or None, words,
            acquisition_date_str=request.form.get("acquisition_date") or None,
            acquisition_source=request.form.get("acquisition_source") or None,
            acquisition_cost=request.form.get("acquisition_cost") or None,
        )
        return render_template("unit_created.html", result=result)
    builds = fl.op_list_builds(conn)
    return render_template("unit_new.html", builds=builds, today=date.today().isoformat())


@app.route("/units/<serial>")
def unit_detail(serial):
    conn = fl.get_conn()
    unit = fl.op_get_unit(conn, serial)
    hw = None
    if unit["hardware_config_json"]:
        hw = json.dumps(json.loads(unit["hardware_config_json"]), indent=2)
    parts = fl.op_list_part_replacements(conn, serial)
    photos = fl.op_list_shipment_photos(conn, serial)
    return render_template(
        "unit_detail.html", unit=unit, hw=hw, statuses=fl.STATUS_FLOW, parts=parts, photos=photos
    )


@app.route("/units/<serial>/qr.png")
def unit_qr(serial):
    conn = fl.get_conn()
    token = fl.op_ensure_qr_token(conn, serial)
    png = render_qr_png(token)
    headers = {}
    if request.args.get("download"):
        headers["Content-Disposition"] = f"attachment; filename={serial}-qr.png"
    return Response(png, mimetype="image/png", headers=headers)


@app.route("/qr-lookup", methods=["GET", "POST"])
def qr_lookup():
    if request.method == "POST":
        conn = fl.get_conn()
        token = request.form.get("token", "").strip()
        unit = fl.op_find_by_qr_token(conn, token)
        return redirect(url_for("unit_detail", serial=unit["serial"]))
    return render_template("qr_lookup.html")


@app.route("/units/<serial>/status", methods=["POST"])
def unit_status(serial):
    conn = fl.get_conn()
    fl.op_set_status(conn, serial, request.form["new_status"])
    flash(f"{serial} -> {request.form['new_status']}", "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/units/<serial>/sell", methods=["POST"])
def unit_sell(serial):
    conn = fl.get_conn()
    sale_date = fl.op_sell(
        conn, serial, request.form.get("date") or None, request.form.get("price") or None,
        request.form.get("buyer_name") or None, request.form.get("buyer_email") or None,
    )
    flash(f"{serial} marked Sold on {sale_date}", "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/units/<serial>/warranty", methods=["POST"])
def unit_warranty(serial):
    conn = fl.get_conn()
    note = request.form.get("note", "").strip()
    if not note:
        flash("Warranty note is required", "error")
        return redirect(url_for("unit_detail", serial=serial))
    fl.op_warranty(conn, serial, note)
    flash(f"{serial} flagged Warrantied", "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/units/<serial>/acquisition", methods=["POST"])
def unit_acquisition(serial):
    conn = fl.get_conn()
    acq_date = fl.op_set_acquisition(
        conn, serial, request.form.get("date") or None,
        request.form.get("source") or None, request.form.get("cost") or None,
    )
    flash(f"{serial} acquisition set: {acq_date}", "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/units/<serial>/parts/new", methods=["GET", "POST"])
def unit_part_new(serial):
    conn = fl.get_conn()
    unit = fl.op_get_unit(conn, serial)
    if request.method == "POST":
        part_id = fl.op_add_part_replacement(
            conn, serial, request.form.get("part_type", "").strip(),
            request.form.get("replaced_at") or None,
            old_make=request.form.get("old_make") or None,
            old_model=request.form.get("old_model") or None,
            old_model_number=request.form.get("old_model_number") or None,
            old_serial_number=request.form.get("old_serial_number") or None,
            old_date_of_mfg=request.form.get("old_date_of_mfg") or None,
            new_make=request.form.get("new_make") or None,
            new_model=request.form.get("new_model") or None,
            new_model_number=request.form.get("new_model_number") or None,
            new_serial_number=request.form.get("new_serial_number") or None,
            new_date_of_mfg=request.form.get("new_date_of_mfg") or None,
            notes=request.form.get("notes") or None,
        )
        flash(f"Recorded part replacement #{part_id} ({request.form.get('part_type')}) for {serial}", "ok")
        return redirect(url_for("unit_detail", serial=serial))
    return render_template(
        "unit_part_new.html", unit=unit, part_types=fl.PART_TYPE_SUGGESTIONS,
        today=date.today().isoformat(),
    )


@app.route("/units/<serial>/repurpose", methods=["GET", "POST"])
def unit_repurpose(serial):
    conn = fl.get_conn()
    unit = fl.op_get_unit(conn, serial)
    if request.method == "POST":
        new_tier = request.form.get("new_tier") or None
        words = int(request.form.get("words") or 6)
        result = fl.op_repurpose(
            conn, serial, request.form.get("new_line") or None,
            int(new_tier) if new_tier else None, request.form.get("build") or None, words,
        )
        return render_template("unit_created.html", result=result, repurposed_from=serial)
    builds = fl.op_list_builds(conn)
    return render_template("unit_repurpose.html", unit=unit, builds=builds)


@app.route("/units/<serial>/hardware-import", methods=["POST"])
def unit_hardware_import(serial):
    conn = fl.get_conn()
    upload = request.files.get("json_file")
    if not upload or not upload.filename:
        flash("Choose a JSON file to upload", "error")
        return redirect(url_for("unit_detail", serial=serial))
    text = upload.read().decode("utf-8", errors="replace")
    backfilled = fl.op_import_hardware_json(conn, serial, text)
    flash("Imported hardware config" + (" (backfilled make/model)" if backfilled else ""), "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/units/<serial>/checklist-save", methods=["POST"])
def unit_checklist_save(serial):
    conn = fl.get_conn()
    upload = request.files.get("checklist_file")
    if not upload or not upload.filename:
        flash("Choose a checklist file to upload", "error")
        return redirect(url_for("unit_detail", serial=serial))
    suffix = Path(upload.filename).suffix or ".md"
    dest = fl.op_save_checklist_bytes(conn, serial, suffix, upload.read())
    flash(f"Saved completed checklist -> {dest}", "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/units/<serial>/photos", methods=["POST"])
def unit_photo_add(serial):
    conn = fl.get_conn()
    uploads = [f for f in request.files.getlist("photo_files") if f and f.filename]
    if not uploads:
        flash("Choose at least one photo to upload", "error")
        return redirect(url_for("unit_detail", serial=serial))
    caption = request.form.get("caption") or None
    for upload in uploads:
        suffix = Path(upload.filename).suffix or ".jpg"
        fl.op_add_shipment_photo_bytes(conn, serial, suffix, upload.read(), caption)
    flash(f"Saved {len(uploads)} shipment photo(s) for {serial}", "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/units/<serial>/photos/<int:photo_id>")
def unit_photo(serial, photo_id):
    conn = fl.get_conn()
    row = fl.op_get_shipment_photo(conn, serial, photo_id)
    path = fl.ROOT / row["file_path"]
    mimetype = mimetypes.guess_type(row["file_path"])[0] or "application/octet-stream"
    return Response(path.read_bytes(), mimetype=mimetype)


@app.route("/units/<serial>/handoff-card")
def unit_handoff_card(serial):
    conn = fl.get_conn()
    unit = fl.op_handoff_card(conn, serial)
    return render_template("handoff_card.html", unit=unit)


@app.route("/units/<serial>/purge-secrets", methods=["POST"])
def unit_purge_secrets(serial):
    conn = fl.get_conn()
    fl.op_purge_secrets(conn, serial)
    flash(f"Purged stored temp passphrases for {serial}", "ok")
    return redirect(url_for("unit_detail", serial=serial))


@app.route("/builds")
def builds_list():
    conn = fl.get_conn()
    rows = fl.op_list_builds(conn)
    return render_template("builds_list.html", rows=rows)


@app.route("/builds/new", methods=["GET", "POST"])
def build_new():
    conn = fl.get_conn()
    if request.method == "POST":
        tier = request.form.get("tier") or None
        script = request.form.get("script_path") or request.form.get("script_custom")
        rel_path, sha256 = fl.op_register_build(
            conn, request.form["build_id"].strip(), request.form["line"],
            int(tier) if tier else None, script, request.form.get("desc") or None,
        )
        flash(f"Registered build '{request.form['build_id']}' -> {rel_path} (sha256 {sha256[:12]}...)", "ok")
        return redirect(url_for("builds_list"))
    files = fl.list_postinstall_files()
    return render_template("build_new.html", files=files)


@app.route("/builds/<build_id>/verify")
def build_verify(build_id):
    conn = fl.get_conn()
    ok, registered, current = fl.op_verify_build(conn, build_id)
    if ok:
        flash(f"OK: {build_id} script unchanged since registration.", "ok")
    else:
        flash(
            f"MISMATCH: {build_id} script has changed since registration! "
            f"registered={registered[:12]}... current={current[:12]}...",
            "error",
        )
    return redirect(url_for("builds_list"))


@app.route("/generate", methods=["GET", "POST"])
def script_generate():
    if request.method == "POST":
        def checkbox(name):
            return request.form.get(name) == "on"

        opts = GenOptions(
            os_id=request.form["os"],
            apps=request.form.getlist("apps"),
            luks_password_enabled=checkbox("luks_password"),
            uefi_password_reminder=checkbox("uefi_reminder"),
            force_user_password_change=checkbox("force_password_change"),
            auto_updates=checkbox("auto_updates"),
            firewall=checkbox("firewall"),
            zram_tlp=checkbox("zram_tlp"),
            printing_codecs=checkbox("printing_codecs"),
            grub_btrfs=checkbox("grub_btrfs"),
            snapper=checkbox("snapper"),
            oem_wallpaper=checkbox("oem_wallpaper"),
            oem_bookmarks=checkbox("oem_bookmarks"),
            oem_guide_folder=checkbox("oem_guide_folder"),
            hibernate_on_lid_close=checkbox("hibernate_on_lid_close"),
            wifi_mac_randomization=checkbox("wifi_mac_randomization"),
            generic_hostname=checkbox("generic_hostname"),
            idle_lock_timeout=checkbox("idle_lock_timeout"),
            firefox_privacy_hardening=checkbox("firefox_privacy_hardening"),
            obsidian_installer=checkbox("obsidian_installer"),
            obsidian_doctor=checkbox("obsidian_doctor"),
        )
        script = generate_script(opts)

        if checkbox("save_as_build"):
            build_id = request.form.get("build_id", "").strip()
            if not build_id:
                flash("Build id is required to save this as a build.", "error")
                return redirect(url_for("script_generate"))
            conn = fl.get_conn()
            generated_dir = fl.ROOT / "postinstall" / "generated"
            generated_dir.mkdir(parents=True, exist_ok=True)
            dest = generated_dir / f"{build_id}.sh"
            dest.write_text(script)
            dest.chmod(0o755)
            tier = request.form.get("tier") or None
            rel_path, sha256 = fl.op_register_build(
                conn, build_id, request.form.get("line", "laptop"),
                int(tier) if tier else None, str(dest), request.form.get("desc") or None,
            )
            flash(f"Registered build '{build_id}' -> {rel_path} (sha256 {sha256[:12]}...)", "ok")
            return redirect(url_for("builds_list"))

        return Response(
            script, mimetype="text/x-sh",
            headers={"Content-Disposition": f"attachment; filename=postinstall-{opts.os_id}.sh"},
        )

    return render_template(
        "generate.html", os_targets=OS_TARGETS, apps=APPS, lines=list(fl.LINE_PREFIXES.keys())
    )


if __name__ == "__main__":
    port = int(os.environ.get("FLEETCTL_WEB_PORT", 4299))
    app.run(host="0.0.0.0", port=port)
