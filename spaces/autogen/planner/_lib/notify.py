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
    "cancelled": "🚫",
}

# Menschliche Status-Zeile statt "needs_input | WARN | 1 Gate"
_STATUS_LINE = {
    "ready": "Plan ist fertig.",
    "needs_input": "Ich brauche noch ein paar Infos von dir:",
    "awaiting_approval": "Ich brauche deine Freigabe:",
    "needs_human": "Ich komme hier nicht allein weiter:",
    "failed": "Das hat leider nicht geklappt.",
    "cancelled": "Abgebrochen.",
}


def _enabled() -> bool:
    return os.environ.get("SOM_NOTIFY", "1") not in ("0", "false", "False")


def _shorten(text: str, n: int = 180) -> str:
    text = " ".join((text or "").split())
    # an Wortgrenze kürzen, nicht mitten im Wort
    if len(text) <= n:
        return text
    cut = text[: n - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" .,;:-—") + "…"


def _intent_title(intent: str) -> str:
    """Kurzer, menschlicher Titel aus dem Intent (erste sinnvolle Zeile)."""
    t = " ".join((intent or "").split())
    return _shorten(t, 90) or "Plan"


_NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def format_questions(result: dict) -> list[str]:
    """Nummerierte, menschliche Fragen-Zeilen (mit Emoji-Ziffern).

    Quelle: result['offene_fragen'] — bereits via questions.clean_questions
    aufbereitet (kein Jargon/Technik/Pfade). Plaintext (notify kann kein Markdown)."""
    fragen = result.get("offene_fragen") or []
    out = []
    for i, q in enumerate(fragen, 1):
        num = _NUM_EMOJI[i - 1] if i <= len(_NUM_EMOJI) else f"{i})"
        if isinstance(q, dict):
            out.append(f"{num} {_shorten(q.get('frage') or q.get('text') or str(q), 160)}")
        else:
            out.append(f"{num} {_shorten(str(q), 160)}")
    return out


def format_run_summary(result: dict) -> str:
    """Baut eine FREUNDLICHE, menschliche Telegram-Nachricht aus einem Run-Result.

    Kein Validator-Jargon, kein run-id/Status-Code, keine Matrix-Internas — nur:
    Titel (was geplant wird), eine menschliche Status-Zeile, und bei Bedarf die
    echten Nutzer-Fragen (schon via questions.clean_questions aufbereitet) +
    eine knappe Antwort-Anleitung. 4096-Limit wird respektiert."""
    status = result.get("status", "?")
    emoji = _STATUS_EMOJI.get(status, "•")
    title = _intent_title(result.get("intent", ""))

    lines = [f"{emoji} {title}"]
    statusline = _STATUS_LINE.get(status)
    if statusline:
        lines.append(statusline)

    fragen = format_questions(result)
    has_q = bool(fragen) and status in ("needs_input", "awaiting_approval", "needs_human")

    if has_q:
        lines.append("")
        lines.extend(fragen)
        lines.append("")
        # Antwort-Anleitung: läuft via brain-gateway -> som_resume -> jüngster
        # wartender Run (kein ID-Tippen). Formen matchen som_resume-match_patterns.
        if len(fragen) > 1:
            beispiel = "  ".join(f"{i}: …" for i in range(1, min(len(fragen), 3) + 1))
            lines.append(f"↩️ Antworte z.B.:  {beispiel}")
        else:
            lines.append("↩️ Antworte mit:  antwort: deine Angabe")
        lines.append("(oder  x  zum Abbrechen)")
    elif status == "ready":
        n = (result.get("steps", {}).get("planner") or {}).get("n_steps")
        if n:
            lines.append(f"({n} Schritte geplant — bereit zur Ausführung.)")

    text = "\n".join(lines)
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
