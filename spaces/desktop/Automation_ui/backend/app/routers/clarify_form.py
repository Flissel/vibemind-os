"""HTML form endpoint for ``handoff_clarify`` answers.

The MCP tool ``handoff_clarify`` issues a ``form_url`` like
``http://127.0.0.1:8007/api/clarify/<id>/form``. This router renders that URL
as a simple HTML form built from the ``form_schema`` stored alongside the
clarify request, and accepts POST submissions that update the clarify record
(secrets stored DPAPI-encrypted via :class:`SecretVault`).

Pure stdlib HTML rendering — no Jinja templates, no extra dependencies.
"""

from __future__ import annotations

import html
import importlib.util
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("clarify_form")

# Load clarify_notify directly — going via ``agents`` package triggers
# heavy imports (orchestrator, llm_config, …) that are not needed here and
# break in subprocess contexts that don't have the full Python path set up.
_CLARIFY_FILE = (
    Path(__file__).resolve().parents[2]
    / "moire_agents"
    / "agents"
    / "handoff"
    / "clarify_notify.py"
)
_spec = importlib.util.spec_from_file_location("clarify_notify", _CLARIFY_FILE)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise RuntimeError(f"cannot load clarify_notify from {_CLARIFY_FILE}")
_clarify_notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_clarify_notify)


router = APIRouter()


def _render_form(record: dict[str, Any]) -> str:
    cid = html.escape(record["clarify_id"])
    question = html.escape(record["question"])
    schema = record.get("form_schema") or []

    rows = []
    has_credential_input = False
    for field in schema:
        name = html.escape(str(field.get("name", "")))
        label = html.escape(str(field.get("label", field.get("name", name))))
        ftype = field.get("type", "text")
        if ftype == "vault":
            ftype = "password"
            has_credential_input = True
        elif ftype == "password":
            has_credential_input = True
        placeholder = html.escape(str(field.get("placeholder", "")))
        required = "required" if field.get("required") else ""
        rows.append(
            f'<label>{label}<br>'
            f'<input type="{html.escape(ftype)}" name="{name}" '
            f'placeholder="{placeholder}" {required}></label>'
        )

    cred_field = ""
    if has_credential_input:
        # Auto-suggest a credential_id derived from clarify_id; user may override.
        cred_field = (
            '<label>Credential ID (Vault-Schlüssel)<br>'
            f'<input type="text" name="credential_id" value="clarify-{cid[:12]}" required></label>'
        )

    options_html = ""
    is_one_click = bool(record.get("options")) and not (record.get("form_schema") or [])
    if record.get("options"):
        if is_one_click:
            # Approval-style: each option is a submit button that posts
            # _choice=<option> directly. No second click.
            btn_rows = "".join(
                f'<button type="submit" name="_choice" value="{html.escape(str(o))}" '
                f'class="opt opt-{html.escape(str(o)).lower()}">{html.escape(str(o))}</button> '
                for o in record["options"]
            )
            options_html = f'<div class="oneclick">{btn_rows}</div>'
            rows = []  # one-click: no extra inputs, suppress "Absenden" button
        else:
            opt_rows = "".join(
                f'<label><input type="radio" name="_choice" value="{html.escape(str(o))}">'
                f"{html.escape(str(o))}</label><br>"
                for o in record["options"]
            )
            options_html = f"<fieldset><legend>Optionen</legend>{opt_rows}</fieldset>"

    submit_disabled = ""
    if record["status"] == "answered":
        submit_disabled = "disabled"
        rows = ["<p><em>Bereits beantwortet — die Form ist gesperrt.</em></p>"]
        options_html = ""

    body = f"""<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<title>VibeMind Clarify · {cid[:8]}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 480px;
          margin: 2rem auto; padding: 1rem; background: #1a1a1a; color: #eaeaea; }}
  h1 {{ font-size: 1.1rem; color: #9ad; }}
  label {{ display: block; margin: 0.6rem 0; }}
  input[type=text], input[type=password], input[type=email], input[type=url], input[type=number] {{
    width: 100%; padding: 0.5rem; box-sizing: border-box;
    background: #2a2a2a; color: #eaeaea; border: 1px solid #444; border-radius: 4px;
  }}
  fieldset {{ border: 1px solid #444; padding: 0.5rem; margin-top: 1rem; }}
  legend {{ padding: 0 0.4rem; }}
  button {{ margin-top: 1rem; padding: 0.5rem 1rem; background: #3060c0; color: #fff;
            border: 0; border-radius: 4px; cursor: pointer; font-size: 1rem; }}
  button[disabled] {{ opacity: 0.4; cursor: not-allowed; }}
  .oneclick {{ display: flex; gap: 0.6rem; margin-top: 1rem; }}
  .oneclick button {{ flex: 1; padding: 0.8rem; }}
  .opt-decline {{ background: #c03030; }}
  .opt-approve {{ background: #2c8c3a; }}
  .meta {{ color: #888; font-size: 0.8rem; margin-top: 1.5rem; }}
</style></head>
<body>
<h1>{question}</h1>
<form method="POST" action="/api/clarify/{cid}/submit">
  {''.join(rows)}
  {cred_field}
  {options_html}
  {'' if is_one_click else f'<button type="submit" {submit_disabled}>Absenden</button>'}
</form>
<p class="meta">Clarify ID: <code>{cid}</code> · Status: {html.escape(record["status"])}<br>
Werte vom Typ <code>password</code> / <code>vault</code> werden lokal mit Windows-DPAPI verschlüsselt.</p>
</body></html>"""
    return body


@router.get("/{clarify_id}/form", response_class=HTMLResponse)
async def get_form(clarify_id: str) -> HTMLResponse:
    record = _clarify_notify.get_clarify(clarify_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown clarify_id")
    return HTMLResponse(_render_form(record))


@router.post("/{clarify_id}/submit", response_class=HTMLResponse)
async def submit_form(clarify_id: str, request: Request) -> HTMLResponse:
    form = await request.form()
    answer_fields: dict[str, Any] = {k: v for k, v in form.items()}
    try:
        record = _clarify_notify.record_clarify_answer(clarify_id, answer_fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown clarify_id")

    cid = html.escape(clarify_id)
    payload_keys = list((record.get("answer") or {}).keys())
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<title>Clarify gesendet</title>
<style>body {{ font-family: sans-serif; max-width: 480px; margin: 2rem auto;
              background: #1a1a1a; color: #eaeaea; padding: 1rem; }}
       h1 {{ color: #6c9; }}</style>
</head><body>
<h1>Danke!</h1>
<p>Antwort gespeichert für <code>{cid}</code>.</p>
<p>Ein Skill wartet darauf, weiter zu laufen — du kannst dieses Fenster schließen.</p>
<p class="meta" style="color:#888;font-size:0.8rem">payload keys: {payload_keys}</p>
</body></html>"""
    )


@router.get("/{clarify_id}", response_class=JSONResponse)
async def get_status(clarify_id: str) -> JSONResponse:
    """JSON status endpoint, used by ``handoff_clarify_check`` and tests."""
    record = _clarify_notify.get_clarify(clarify_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown clarify_id")
    return JSONResponse(record)


def _direct_decision_response(decision: str, clarify_id: str) -> HTMLResponse:
    color = "#2c8c3a" if decision == "Approve" else "#c03030"
    icon = "✅" if decision == "Approve" else "❌"
    return HTMLResponse(
        f"""<!doctype html><html lang="de"><head>
<meta charset="utf-8"><title>{decision}</title>
<style>body {{ font-family: sans-serif; max-width: 480px; margin: 4rem auto;
              text-align: center; background: #1a1a1a; color: #eaeaea; }}
       h1 {{ color: {color}; font-size: 2rem; }}
       .meta {{ color: #888; margin-top: 2rem; font-size: 0.85rem; }}</style>
</head><body>
<h1>{icon} {decision}</h1>
<p>Du hast die Aktion <strong>{decision.lower()}</strong> entschieden.</p>
<p>Du kannst dieses Fenster jetzt schließen.</p>
<p class="meta">clarify_id: <code>{html.escape(clarify_id)}</code></p>
</body></html>"""
    )


@router.get("/{clarify_id}/approve", response_class=HTMLResponse)
async def quick_approve(clarify_id: str) -> HTMLResponse:
    """One-click approve via GET — wired to inline Telegram button (URL link)."""
    record = _clarify_notify.get_clarify(clarify_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown clarify_id")
    if record["status"] != "answered":
        _clarify_notify.record_clarify_answer(clarify_id, {"_choice": "Approve"})
    return _direct_decision_response("Approve", clarify_id)


@router.get("/{clarify_id}/decline", response_class=HTMLResponse)
async def quick_decline(clarify_id: str) -> HTMLResponse:
    record = _clarify_notify.get_clarify(clarify_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown clarify_id")
    if record["status"] != "answered":
        _clarify_notify.record_clarify_answer(clarify_id, {"_choice": "Decline"})
    return _direct_decision_response("Decline", clarify_id)
