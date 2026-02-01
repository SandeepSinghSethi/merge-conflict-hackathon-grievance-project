"""
app.py — Flask backend for the Grievance Portal.

Routes
------
    GET  /                          → Landing / submit form
    GET  /view                      → View / decrypt grievance form
    POST /api/submit                → Encrypt → IPFS upload → Solana register
    POST /api/view                  → Fetch CID from chain → IPFS fetch → decrypt

Architecture
------------
                ┌──────────┐   POST /api/submit     ┌──────────┐
                │  Browser │ ─────────────────────▶ │  Flask   │
                │          │ ◀───── JSON ────────── │  app.py  │
                └──────────┘                        └────┬─────┘
                                                         │
                        ┌────────────────────────────────┼────────────────┐
                        ▼                                ▼                ▼
                 ┌────────────┐              ┌─────────────┐   ┌────────────────┐
                 │ crypto_    │              │  ipfs_      │   │  client.py     │
                 │ utils.py   │              │  utils.py   │   │  (anchorpy)    │
                 │ AES-256-GCM│              │  upload()   │   │  register()    │
                 └────────────┘              └─────────────┘   └────────────────┘
                                                  │                    │
                                                  ▼                    ▼
                                            ┌──────────┐       ┌──────────────┐
                                            │  IPFS    │       │  Solana      │
                                            │  Network │       │  Devnet      │
                                            └──────────┘       └──────────────┘
"""

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from crypto_utils import encrypt, decrypt   # noqa: E402
import ipfs_utils                           # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")

def _run_async(coro):
    """Execute an async coroutine from synchronous Flask code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _register_on_chain(cid: str, message: str, department: str) -> dict:
    from client import GrievanceClient   # noqa: F401  (re-import is fine)
    client = GrievanceClient()
    try:
        result = await client.register(cid, message, department)
        return result if result else {}
    finally:
        await client.close()


async def _fetch_from_chain(grievance_pubkey: str) -> dict | None:
    from client import GrievanceClient
    from anchorpy.error import AccountDoesNotExistError

    client = GrievanceClient()
    try:
        try:
            grievance = await client.get_grievance(grievance_pubkey)
        except (AccountDoesNotExistError, Exception):
            return None

        if grievance is None:
            return None

        return {
            "cid":           grievance.cid,
            "department":    grievance.department,
            "created_by":    str(grievance.created_by),
            "created_at":    grievance.created_at,
        }
    finally:
        await client.close()


async def _update_on_chain(grievance_pubkey: str, status: str, seed: int, note_cid: str | None) -> dict:
    from client import GrievanceClient
    client = GrievanceClient()
    try:
        result = await client.update(grievance_pubkey, status, seed, note_cid)
        return result if result else {}
    finally:
        await client.close()


async def _fetch_all_status_updates(grievance_pubkey: str) -> list[dict]:
    from client import GrievanceClient
    from anchorpy.error import AccountDoesNotExistError   # the real exception class

    client = GrievanceClient()
    updates = []
    try:
        seed = 0
        while True:
            try:
                raw = await client.get_status_update(grievance_pubkey, seed)
            except (AccountDoesNotExistError, Exception):
                raw = None

            if raw is None:
                break                          # no more updates at this seed

            updates.append({
                "seed":       seed,
                "status":     raw.status.__class__.__name__,
                "updated_by": str(raw.updated_by),
                "updated_at": raw.updated_at,
                "note_cid":   raw.note_cid if raw.note_cid else None,
            })
            seed += 1
    finally:
        await client.close()
    return updates

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/view", methods=["GET"])
def view_page():
    return render_template("view.html")


@app.route("/api/submit", methods=["POST"])
def api_submit():
    body = request.get_json(silent=True) or {}
    department = (body.get("department") or "").strip()
    message    = (body.get("message")    or "").strip()
    password   = (body.get("password")   or "").strip()

    errors = []
    if not department:
        errors.append("Department is required.")
    if not message:
        errors.append("Message is required.")
    if len(password) < 6:
        errors.append("Password must be at least 6 characters.")
    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    now_iso = datetime.now(timezone.utc).isoformat()

    payload = {
        "department": department,
        "message":    message,
        "submitted_at": now_iso,
        "version":    1,              # payload schema version — future-proof
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    try:
        encrypted_blob = encrypt(payload_json, password)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "errors": [f"Encryption failed: {exc}"]}), 500

    try:
        cid = ipfs_utils.upload(encrypted_blob.encode("ascii"), filename="grievance.enc")
    except RuntimeError as exc:
        traceback.print_exc()
        return jsonify({"success": False, "errors": [str(exc)]}), 503

    try:
        chain_result = _run_async(_register_on_chain(cid, message, department))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "errors": [f"Solana registration failed: {exc}"],
            "cid": cid,   # CID is already on IPFS — caller can note it
        }), 500

    grievance_id = chain_result.get("grievance_pda", "unknown")

    return jsonify({
        "success":      True,
        "grievance_id": grievance_id,
        "cid":          cid,
        "department":   department,
        "timestamp":    now_iso,
    }), 200


@app.route("/api/view", methods=["POST"])
def api_view():
    body          = request.get_json(silent=True) or {}
    grievance_id  = (body.get("grievance_id") or "").strip()
    password      = (body.get("password")     or "").strip()

    errors = []
    if not grievance_id:
        errors.append("Grievance ID is required.")
    if not password:
        errors.append("Password is required.")
    if errors:
        return jsonify({"success": False, "errors": errors}), 400


    try:
        on_chain = _run_async(_fetch_from_chain(grievance_id))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "errors": [f"Solana fetch failed: {exc}"]}), 500

    if on_chain is None:
        return jsonify({
            "success": False,
            "errors": ["No grievance found at that ID. Check the Grievance ID and try again."]
        }), 404

    cid = on_chain["cid"]


    try:
        raw_bytes = ipfs_utils.fetch(cid)
        encrypted_blob = raw_bytes.decode("ascii")
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "errors": [f"IPFS fetch failed: {exc}"]}), 503


    try:
        payload_json = decrypt(encrypted_blob, password)
        payload      = json.loads(payload_json)
    except Exception as exc:
        # Most likely: wrong password → InvalidTag
        return jsonify({
            "success": False,
            "errors": ["Decryption failed. The password you entered is incorrect or the data has been tampered with."]
        }), 401


    return jsonify({
        "success":      True,
        "grievance_id": grievance_id,
        "department":   payload.get("department", on_chain.get("department", "")),
        "message":      payload.get("message", ""),
        "submitted_at": payload.get("submitted_at", ""),
        "created_by":   on_chain.get("created_by", ""),
        "on_chain":     on_chain,
        "cid":          cid,
    }), 200

@app.route("/api/update", methods=["POST"])
def api_update():
    body          = request.get_json(silent=True) or {}
    grievance_id  = (body.get("grievance_id") or "").strip()
    status        = (body.get("status")       or "").strip()
    note          = (body.get("note")         or "").strip()
    password      = (body.get("password")     or "").strip()

    VALID = ("Pending", "InProgress", "Resolved", "Rejected")

    errors = []
    if not grievance_id:
        errors.append("Grievance ID is required.")
    if status not in VALID:
        errors.append(f"Status must be one of: {', '.join(VALID)}.")
    if note and len(password) < 6:
        errors.append("Password (min 6 chars) is required when adding a note.")
    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    try:
        existing = _run_async(_fetch_all_status_updates(grievance_id))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "errors": [f"Failed to read current updates: {exc}"]}), 500

    next_seed = len(existing)   # 0-indexed; len == next available

    note_cid = None
    if note:
        note_payload = json.dumps({
            "note":       note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)
        try:
            encrypted_note = encrypt(note_payload, password)
        except Exception as exc:
            traceback.print_exc()
            return jsonify({"success": False, "errors": [f"Note encryption failed: {exc}"]}), 500
        try:
            note_cid = ipfs_utils.upload(encrypted_note.encode("ascii"), filename="note.enc")
        except RuntimeError as exc:
            traceback.print_exc()
            return jsonify({"success": False, "errors": [str(exc)]}), 503

    try:
        chain_result = _run_async(
            _update_on_chain(grievance_id, status, next_seed, note_cid)
        )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "errors": [f"Solana update failed: {exc}"],
            "note_cid": note_cid,   # note already pinned — inform caller
        }), 500

    resp = {
        "success":           True,
        "status_update_pda": chain_result.get("status_update_pda", "unknown"),
        "seed":              next_seed,
        "status":            status,
    }
    if "signature" in chain_result:
        resp["signature"] = chain_result["signature"]
    if chain_result.get("already_exists"):
        resp["already_exists"] = True
    if note_cid:
        resp["note_cid"] = note_cid

    return jsonify(resp), 200

@app.route("/api/get-updates", methods=["POST"])
def api_get_updates():
    body         = request.get_json(silent=True) or {}
    grievance_id = (body.get("grievance_id") or "").strip()
    password     = (body.get("password")     or "").strip()

    if not grievance_id:
        return jsonify({"success": False, "errors": ["Grievance ID is required."]}), 400

    try:
        updates = _run_async(_fetch_all_status_updates(grievance_id))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "errors": [f"Solana fetch failed: {exc}"]}), 500

    for upd in updates:
        if upd["note_cid"] and password:
            try:
                raw       = ipfs_utils.fetch(upd["note_cid"])
                blob      = raw.decode("ascii")
                note_json = decrypt(blob, password)
                note_obj  = json.loads(note_json)
                upd["note"] = note_obj.get("note", "")
            except Exception:
                upd["note"] = None
        else:
            upd["note"] = None

    return jsonify({"success": True, "updates": updates}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"  Grievance Portal starting on http://127.0.0.1:{port}")
    print(f"    Debug mode : {debug}")
    print(f"    Pinata JWT : {'set' if ipfs_utils.PINATA_JWT else 'NOT SET — upload will fail'}")
    print(f"    Gateway    : {ipfs_utils.PINATA_GATEWAY_URL or 'NOT SET — fetch will fall back to ipfs.io'}")
    app.run(host="0.0.0.0", port=port, debug=debug)