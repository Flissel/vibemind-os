"""Claude-Shim-Anbindung (OpenAI-kompatibel) — die einzige LLM-Tuer.

Der Shim (:8114) ueberlebt keinen Absturz des Logon-Tasks; deshalb ist
diese Tuer fail-soft: ist er tot, sagt die Rueckgabe das, und der
Aufrufer arbeitet ohne LLM weiter (Leitplanke 3 der Spec). Modell-IDs
des Shims: claude-code, claude-code-opus, claude-code-sonnet und das
unbeworbene claude-code-haiku.
"""
import json
import os
import urllib.request

FEHLER_MAXLAENGE = 300


def frage(system: str, nutzer: str) -> dict:
    basis = os.environ.get("MARKETING_CLAW_LLM_URL", "http://127.0.0.1:8114/v1").rstrip("/")
    modell = os.environ.get("MARKETING_CLAW_MODELL", "claude-code-sonnet")
    nutzlast = json.dumps({
        "model": modell,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": nutzer}],
    }).encode("utf-8")
    anfrage = urllib.request.Request(
        basis + "/chat/completions", data=nutzlast, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=300) as antwort:
            gelesen = json.loads(antwort.read())
        text = gelesen["choices"][0]["message"]["content"]
        return {"ok": True, "text": text}
    except Exception as e:  # noqa: BLE001 — Shim tot ist Alltag, kein Absturzgrund
        return {"ok": False,
                "fehler": f"Shim nicht nutzbar ({type(e).__name__}: {e})"[:FEHLER_MAXLAENGE]}
