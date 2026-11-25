"""Klick aus der Desktop-Ansicht durchreichen (Werkzeug ``desktop_hand_klick``, Ereignis ``desktop.hand``).

Ein fester Aufruf mit eigenem Laufzeit-Kontext je Klick (Spur in OpenFang), ohne Modell. Die
Sperren sitzen im Werkzeug-Dienst: Not-Aus (STOP), laufende Aufgabe, Sperrliste fuer das
Fenster unter dem Punkt; dort wird jeder Klick auch als kurze Aufgabe "Hand: Klick" gebucht.
Hier wird nur der Punkt geprueft und die Antwort auf Codes beschraenkt.
"""
from __future__ import annotations

import itertools
import logging
import time
from typing import Any, Mapping

from .desktop_schleife import FreigabeAusstehend, WerkzeugFehler, fester_aufruf
from .desktop_schleife_contract import AGENT, ZIEL_SERVER

logger = logging.getLogger(__name__)

WERKZEUG = "desktop_hand_klick"
EREIGNIS = "desktop.hand"
GRENZE = 20000
_ZAEHLER = itertools.count(1)
_GRUENDE = ("not_aus", "aufgabe_laeuft", "gesperrt", "fenster_unlesbar", "dienst_fehler", "dienst_nicht_erreichbar",
            "dienst_veraltet", "konfiguration", "eingabe_ungueltig")


def kontext() -> Any:
    from .capability_targets import find_registered_mcp_authority
    from .openfang_runtime_authority import RuntimeInvocationContext, runtime_invocation_id

    autoritaet = find_registered_mcp_authority(AGENT, ZIEL_SERVER, WERKZEUG)
    if autoritaet is None or autoritaet.event_id != EREIGNIS or not autoritaet.space_id:
        raise WerkzeugFehler(f"nicht_gebunden: {WERKZEUG} ist im Verzeichnis nicht ueber {EREIGNIS} gebunden")
    nummer = next(_ZAEHLER)
    plan_id = f"hand-{int(time.time() * 1000)}-{nummer:04d}"
    schritt = f"s1/hand/{nummer:04d}/{WERKZEUG}"
    return RuntimeInvocationContext(
        correlation_id=plan_id, plan_id=plan_id, plan_revision=1, step_id=schritt,
        space_id=autoritaet.space_id, agent_name=autoritaet.agent,
        invocation_id=runtime_invocation_id(plan_id, 1, schritt),
    )


def _ganz(wert: Any) -> int | None:
    if isinstance(wert, bool) or not isinstance(wert, int) or not -GRENZE <= wert <= GRENZE:
        return None
    return wert


def _code(text: str) -> str:
    kopf = str(text or "").split(":", 1)[0].strip().lower()
    if kopf in _GRUENDE or kopf in ("nicht_gebunden",):
        return kopf
    meldung = str(text or "").lower()
    if any(t in meldung for t in ("not permitted", "nicht freigegeben", "not allowed")):
        return "nicht_freigegeben"
    # wie desktop_app/desktop_fenster: OpenFang hat die Zulassung (noch) nicht erteilt
    if any(t in meldung for t in ("approval was not approved", "pending", "ausstehend")):
        return "freigabe_ausstehend"
    if any(t in meldung for t in ("connection", "refused", "unreachable", "timed out", "timeout", "not connected")):
        return "werkzeug_aus"
    return "werkzeug_fehler"


def klicken(x: Any, y: Any) -> dict:
    """Ein Klick. Antwort nur aus Zahlen, Wahrheitswerten und Codes (nie ein Fenstertitel)."""
    px, py = _ganz(x), _ganz(y)
    if px is None or py is None:
        return {"ok": False, "grund": "eingabe_ungueltig", "x": x if isinstance(x, int) else None,
                "y": y if isinstance(y, int) else None}
    basis = {"x": px, "y": py}
    try:
        kontext_ = kontext()
        antwort = fester_aufruf(WERKZEUG, dict(basis), kontext_)
    except FreigabeAusstehend:
        return {**basis, "ok": False, "grund": "freigabe_ausstehend"}
    except WerkzeugFehler as fehler:
        return {**basis, "ok": False, "grund": _code(str(fehler))}
    if not isinstance(antwort, Mapping):
        return {**basis, "ok": False, "grund": "unlesbar"}
    grund = antwort.get("grund")
    ergebnis = {
        **basis, "ok": antwort.get("ok") is True, "echt": antwort.get("echt") is True,
        "trocken": antwort.get("trocken") is True,
        "grund": _code(str(grund)) if grund else None,
        "task_id": str(antwort.get("task_id"))[:40] if antwort.get("task_id") else None,
        "plan_id": kontext_.plan_id,
    }
    logger.info("desktop_hand_klick (%s, %s): ok=%s echt=%s grund=%s Aufgabe %s Spur %s", px, py, ergebnis["ok"],
                ergebnis["echt"], ergebnis["grund"], ergebnis["task_id"], ergebnis["plan_id"])
    return ergebnis
