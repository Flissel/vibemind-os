"""SoM Planner — Notify (ausgehend an Telegram).

Pusht Plan-/Matrix-Statusänderungen an den VibeMind-Telegram-Channel, damit der
Nutzer pro Execution auf dem Laufenden bleibt (ereignis-getrieben, kein Polling).
Nutzt das vorhandene scripts/notify_telegram.py (CLI, plain text, 4096 chars).

Abschaltbar via env SOM_NOTIFY=0 (default an, aber best-effort — Notify-Fehler
brechen die Pipeline NIE ab).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]   # Vibemind_V1/
_NOTIFY_SCRIPT = _REPO_ROOT / "scripts" / "notify_telegram.py"

_STATUS_EMOJI = {
    "ready": "✅",
    "needs_input": "📋",
    "awaiting_approval": "⏸️",
    "needs_human": "🚧",
    "failed": "❌",
}


def _enabled() -> bool:
    return os.environ.get("SOM_NOTIFY", "1") not in ("0", "false", "False")


def _shorten(text: str, n: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def format_questions(result: dict) -> list[str]:
    """Baut die nummerierte Frage-Liste (Entscheidungsgrundlage) aus dem Result.

    Quelle: result['offene_fragen'] — von som_core aus exec.benoetigte_daten_fehlen
    + verdict.approval_gates abgeleitet. Plaintext (notify kann kein Markdown)."""
    fragen = result.get("offene_fragen") or []
    out = []
    for i, q in enumerate(fragen, 1):
        if isinstance(q, dict):
            out.append(f"{i}) {_shorten(q.get('frage') or q.get('text') or str(q))}")
        else:
            out.append(f"{i}) {_shorten(str(q))}")
    return out


def format_run_summary(result: dict) -> str:
    """Baut eine Telegram-Zusammenfassung aus einem som_core-Run-Result.

    Bei needs_input/awaiting_approval werden die offenen Fragen NUMMERIERT
    aufgelistet (die Entscheidungsgrundlage, die vorher fehlte) + eine
    Antwort-Anleitung angehängt, damit der Nutzer direkt per Reply antworten
    kann. 4096-Limit wird respektiert (Fragen werden notfalls gekürzt)."""
    status = result.get("status", "?")
    intent = result.get("intent", "")
    run_id = result.get("run_id", "")
    emoji = _STATUS_EMOJI.get(status, "•")
    head = f"{emoji} SoM-Plan {run_id}: {status}".strip()
    lines = [head, f"Intent: {_shorten(intent, 120)}"]

    steps = result.get("steps", {})
    pl = steps.get("planner", {})
    val = steps.get("validator", {})
    mx = steps.get("matrix", {})
    if pl.get("n_steps") is not None:
        lines.append(f"Schritte: {pl['n_steps']}")
    if val.get("verdict"):
        gates = val.get("n_gates", 0)
        lines.append(f"Verdict: {val['verdict']}" + (f" | {gates} Approval-Gate(s)" if gates else ""))
    if mx.get("ok"):
        iso = mx.get("isolated") or []
        lines.append(f"Matrix: {mx.get('n_nodes')} Schritte, {mx.get('n_edges')} Abhängigkeiten"
                     + (f" | ⚠ verwaist: {iso}" if iso else ""))

    fb = [k for k in steps if k.startswith("feedback_")]
    if fb:
        lines.append(f"Korrektur-Runden: {len(fb)}")
    if result.get("feedback_exhausted"):
        lines.append("⚠ Feedback-Limit erreicht — Mensch muss ran.")

    # ── Entscheidungsgrundlage: nummerierte offene Fragen (das Kern-Feature) ──
    fragen = format_questions(result)
    if fragen and status in ("needs_input", "awaiting_approval", "needs_human"):
        lines.append("")
        lines.append(f"Ich brauche {len(fragen)} Info(s):")
        lines.extend(fragen)
        # Antwort-Anleitung — Reply auf DIESE Nachricht (Schritt-4-Poller)
        lines.append("")
        lines.append("Antworte als Reply auf diese Nachricht, z.B.:")
        beispiel = "  " + "  ".join(f"{i}: …" for i in range(1, min(len(fragen), 3) + 1))
        lines.append(beispiel)
        lines.append("oder  x  zum Abbrechen.")
    else:
        hint = {
            "needs_input": "→ Fehlende Daten ergänzen, dann erneut.",
            "awaiting_approval": "→ Approval-Gate(s) prüfen + freigeben.",
            "needs_human": "→ Plan manuell prüfen.",
            "ready": "→ Bereit.",
            "cancelled": "→ Abgebrochen.",
        }.get(status)
        if hint:
            lines.append(hint)

    text = "\n".join(lines)
    # 4096 hard limit (notify_telegram chunkt nicht) — notfalls hinten kürzen.
    if len(text) > 4000:
        text = text[:3990].rstrip() + "\n…(gekürzt)"
    return text


def send(text: str) -> int | None:
    """Sendet plain text an den Telegram-Channel (best-effort).

    Rückgabe: message_id der gesendeten Nachricht (int) bei Erfolg, sonst None.
    Die message_id wird für die Reply-Korrelation gebraucht (message_id->run_id,
    Schritt 4). notify_telegram.py gibt sie auf stdout aus."""
    if not _enabled():
        return None
    if not _NOTIFY_SCRIPT.exists():
        return None
    try:
        # WICHTIG: notify_telegram.py lädt die .env SELBST. Wir vererben das volle
        # Prozess-Env (damit das Skript .env + TELEGRAM_BOT_TOKEN findet) und
        # ergänzen NUR den optionalen chat_id-Override. Kein leeres env= übergeben
        # (das hatte das .env-Selbstladen des Skripts ausgehebelt → Push fehlte).
        env = dict(os.environ)
        if os.environ.get("SOM_TELEGRAM_CHAT_ID"):
            env["TELEGRAM_CHAT_ID"] = os.environ["SOM_TELEGRAM_CHAT_ID"]
        # Eigener SoM-Bot-Token (Schritt 4) hat Vorrang, damit Replies beim
        # Poller ankommen. Fällt auf den Standard-Bot zurück wenn nicht gesetzt.
        if os.environ.get("SOM_TELEGRAM_BOT_TOKEN"):
            env["TELEGRAM_BOT_TOKEN"] = os.environ["SOM_TELEGRAM_BOT_TOKEN"]
        proc = subprocess.run(
            [sys.executable, str(_NOTIFY_SCRIPT)],
            input=text[:4096],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            env=env,
            cwd=str(_REPO_ROOT),   # damit notify_telegram die Root-.env findet
        )
        if proc.returncode != 0:
            return None
        out = (proc.stdout or "").strip()
        try:
            return int(out)
        except (ValueError, TypeError):
            return None   # stdout war "ok" (alte API) — Erfolg, aber keine mid
    except Exception:  # noqa: BLE001 — Notify darf die Pipeline nie brechen
        return None


def notify_run(result: dict) -> int | None:
    """Convenience: Zusammenfassung eines Run-Results an Telegram.
    Rückgabe: message_id (für message_id->run_id-Mapping) oder None."""
    return send(format_run_summary(result))


if __name__ == "__main__":
    # Selbsttest des Formatters (kein echter Telegram-Versand)
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    demo = {
        "status": "awaiting_approval", "intent": "Bereite die Antwort ans Jobcenter vor",
        "steps": {
            "planner": {"n_steps": 6}, "validator": {"verdict": "WARN", "n_gates": 3},
            "matrix": {"ok": True, "n_nodes": 6, "n_edges": 6, "isolated": []},
            "feedback_1": {"mangel": "x"},
        },
    }
    print("=== format_run_summary Demo ===")
    print(format_run_summary(demo))
    print()
    print("SOM_NOTIFY enabled:", _enabled(), "| script:", _NOTIFY_SCRIPT.exists())
