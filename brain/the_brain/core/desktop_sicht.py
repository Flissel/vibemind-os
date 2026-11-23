"""Nachpruefung ``truth:desktop_sicht``: Ist auf dem Bildschirm zu sehen, was ein Schritt erwartet?

Jede Faehigkeit, auch eines anderen Space, kann sie als Validator benutzen::

    validator:
      kind: truth:desktop_sicht
      on_fail: report
      postcondition:
        programm: "^(chrome|msedge)$"        # Muster auf die Programmdatei (ohne .exe), gross/klein egal
        fenstertitel_enthaelt: "{arg}"       # Teil des Fenstertitels; {arg} = Argument des Schritts
        element_enthaelt: "Speichern"        # Teil des Namens eines sichtbaren Bedienelements

Gelesen wird ueber den festen Aufruf ``desktop_blick`` (Ereignis ``desktop.blick``) mit einem
eigenen Laufzeit-Kontext je Pruefung. VERIFIED, wenn alles Erwartete zu sehen ist; REFUTED,
wenn etwas Lesbares nicht passt; UNVERIFIED, wenn das Fenster gesperrt ist, die Elemente nicht
lesbar sind oder der Blick nicht gelingt.

Was gespeichert wird (Entscheidung der Koordination, 23.11.): Urteil und Signal enthalten nur
Wahrheitswerte und eine Zaehlzeile, nie Fenstertitel, Programm oder Elementnamen. Sie landen im
HopResult und damit in Plan-Recorder, Verlauf und Lernspeicher. Der Satz mit dem Fenstertitel
("Zu sehen: Fenster „…“ in Programm …") liegt nur fluechtig im Speicher dieses Moduls und geht
ueber ``saetze_fuer_plan`` in die Antwort an Chat und Standzeile.
"""
from __future__ import annotations

import itertools
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Mapping, Optional

from .desktop_schleife import FreigabeAusstehend, WerkzeugFehler, fester_aufruf
from .desktop_schleife_contract import AGENT, ZIEL_SERVER

WERKZEUG = "desktop_blick"
EREIGNIS = "desktop.blick"
FELDER = ("programm", "fenstertitel_enthaelt", "element_enthaelt")
MAX_ERWARTUNG = 200
MAX_TITEL_IM_SATZ = 60
_ZAEHLER = itertools.count(1)

# Fluechtige Saetze fuer die Antwort: (plan_id, step_id) -> (Zeitpunkt, Satz). Nie auf Platte.
MAX_SAETZE = 50
SATZ_HALTBAR_S = 600
_SAETZE: "OrderedDict[tuple[str, str], tuple[float, str]]" = OrderedDict()
_SAETZE_SPERRE = threading.Lock()


def _merken(laufzeit: Any, satz: str) -> None:
    if not isinstance(laufzeit, Mapping):
        return
    schluessel = (str(laufzeit.get("plan_id") or ""), str(laufzeit.get("step_id") or ""))
    if not all(schluessel):
        return
    with _SAETZE_SPERRE:
        _SAETZE.pop(schluessel, None)
        _SAETZE[schluessel] = (time.monotonic(), satz)
        while len(_SAETZE) > MAX_SAETZE:
            _SAETZE.popitem(last=False)


def saetze_fuer_plan(plan_id: Any) -> list[str]:
    """Die Saetze eines Plans fuer Chat und Standzeile; danach sind sie weg. Wirft nie."""
    try:
        grenze = time.monotonic() - SATZ_HALTBAR_S
        with _SAETZE_SPERRE:
            for alt in [k for k, (t, _) in _SAETZE.items() if t < grenze]:
                del _SAETZE[alt]
            treffer = [k for k in _SAETZE if k[0] == str(plan_id)]
            return [_SAETZE.pop(k)[1] for k in treffer]
    except Exception:  # noqa: BLE001
        return []


def erwartung_aus(postcondition: Any, arg: Any) -> tuple[Optional[dict], str]:
    """Die Erwartung aus der Postcondition; ``{arg}`` wird durch das Argument ersetzt."""
    if not isinstance(postcondition, Mapping):
        return None, "keine Postcondition"
    fremd = sorted(set(postcondition) - set(FELDER) - {"check"})
    if fremd:
        return None, f"unbekannte Angabe {fremd[0]}"
    argument = " ".join(str(arg or "").split())
    erwartet: dict[str, str] = {}
    for feld in FELDER:
        wert = postcondition.get(feld)
        if wert is None:
            continue
        if not isinstance(wert, str) or not wert.strip():
            return None, f"{feld} muss Text sein"
        if "{arg}" in wert:
            if not argument:
                return None, f"{feld}: kein Argument fuer {{arg}}"
            wert = wert.replace("{arg}", re.escape(argument) if feld == "programm" else argument)
        if len(wert) > MAX_ERWARTUNG:
            return None, f"{feld} ist zu lang"
        if feld == "programm":
            try:
                re.compile(wert)
            except re.error:
                return None, "programm ist kein gueltiges Muster"
        erwartet[feld] = wert.strip()
    if not erwartet:
        return None, "nichts zu pruefen: programm, fenstertitel_enthaelt oder element_enthaelt fehlt"
    return erwartet, ""


def laufzeit_aus_plan(plan_ctx: Mapping[str, Any], step_id: str) -> dict[str, Any]:
    """Die Plan-Angaben, aus denen der Laufzeit-Kontext der Pruefung entsteht."""
    return {
        "plan_id": plan_ctx.get("plan_id", "standalone-plan"),
        "plan_revision": plan_ctx.get("plan_revision", 1),
        "correlation_id": plan_ctx.get("trace_id") or plan_ctx.get("plan_id", "standalone-plan"),
        "step_id": step_id,
    }


def kontext(laufzeit: Any) -> Any:
    """Eigener Laufzeit-Kontext fuer EINEN Blick, gebunden ueber ``desktop.blick``."""
    from .capability_targets import find_registered_mcp_authority
    from .openfang_runtime_authority import RuntimeInvocationContext, runtime_invocation_id

    if not isinstance(laufzeit, Mapping):
        raise WerkzeugFehler("laufzeit_fehlt: ohne Plan-Angaben keine Nachpruefung am Bildschirm")
    plan_id, step_id = laufzeit.get("plan_id"), laufzeit.get("step_id")
    korrelation = laufzeit.get("correlation_id") or plan_id
    revision = laufzeit.get("plan_revision")
    if not all(isinstance(t, str) and t.strip() for t in (plan_id, step_id, korrelation)) \
            or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise WerkzeugFehler("laufzeit_fehlt: plan_id, plan_revision und step_id sind noetig")
    autoritaet = find_registered_mcp_authority(AGENT, ZIEL_SERVER, WERKZEUG)
    if autoritaet is None or autoritaet.event_id != EREIGNIS or not autoritaet.space_id:
        raise WerkzeugFehler(f"nicht_gebunden: {WERKZEUG} ist im Verzeichnis nicht ueber {EREIGNIS} gebunden")
    schritt = f"{step_id}/sicht/{next(_ZAEHLER):04d}/{WERKZEUG}"
    return RuntimeInvocationContext(
        correlation_id=korrelation, plan_id=plan_id, plan_revision=revision, step_id=schritt,
        space_id=autoritaet.space_id, agent_name=autoritaet.agent,
        invocation_id=runtime_invocation_id(plan_id, revision, schritt),
    )


def _satz(blick: Mapping[str, Any]) -> str:
    titel = " ".join(str(blick.get("fenstertitel") or "").split())
    if len(titel) > MAX_TITEL_IM_SATZ:
        titel = titel[: MAX_TITEL_IM_SATZ - 1].rstrip() + "…"
    programm = str(blick.get("programm") or "").strip() or "unbekannt"
    return f"Zu sehen: Fenster „{titel}“ in Programm {programm}."


def sicht_pruefen(erwartet: Mapping[str, Any], laufzeit: Any = None, *,
                  lesen: Optional[Callable[[], Any]] = None):
    """Liest einen Blick und vergleicht. Gibt ``(ok, signal, grund)`` wie ein Pruefer von world_observer."""
    try:
        blick = lesen() if lesen is not None else fester_aufruf(WERKZEUG, {}, kontext(laufzeit))
    except FreigabeAusstehend:
        return None, {"status": "unverified"}, "Blick wartet auf Freigabe"
    except WerkzeugFehler as fehler:
        return None, {"status": "unverified"}, f"Blick nicht moeglich: {str(fehler).split(':', 1)[0][:40]}"
    if not isinstance(blick, Mapping):
        return None, {"status": "unverified"}, "Blick unlesbar"
    if blick.get("gesperrt") is not False:
        _merken(laufzeit, "Das Fenster ist gesperrt; am Bildschirm wurde nichts geprüft.")
        return None, {"gesperrt": True}, "Sicht nicht pruefbar: Fenster gesperrt (Sperrliste)"

    teile: dict[str, Optional[bool]] = {}
    if "programm" in erwartet:
        teile["programm"] = re.search(str(erwartet["programm"]), str(blick.get("programm") or ""), re.IGNORECASE) is not None
    if "fenstertitel_enthaelt" in erwartet:
        teile["fenstertitel"] = str(erwartet["fenstertitel_enthaelt"]).casefold() in str(blick.get("fenstertitel") or "").casefold()
    if "element_enthaelt" in erwartet:
        gesucht = str(erwartet["element_enthaelt"]).casefold()
        namen = [str(e.get("name") or "").casefold() for e in blick.get("elemente") or [] if isinstance(e, Mapping)]
        if any(gesucht in n for n in namen):
            teile["element"] = True
        elif blick.get("elemente_lesbar") is False or blick.get("elemente_gekuerzt") is True:
            teile["element"] = None  # nicht lesbar oder abgeschnitten: Abwesenheit ist nicht belegt
        else:
            teile["element"] = False

    # Gespeichert wird nur dies: Wahrheitswerte und eine Zaehlzeile, kein Inhalt vom Bildschirm.
    elemente = blick.get("elemente")
    dauer = blick.get("dauer_ms")
    zaehlzeile = (f"Blick: {len(elemente) if isinstance(elemente, list) else 0} Elemente, "
                  f"{dauer if isinstance(dauer, int) and not isinstance(dauer, bool) else '?'} ms")
    signal: dict[str, Any] = {"gesperrt": False, **teile}
    satz = _satz(blick)  # nur fuer Chat und Standzeile
    if any(v is False for v in teile.values()):
        fehlt = ", ".join(k for k, v in teile.items() if v is False)
        _merken(laufzeit, f"{satz} Nicht wie erwartet: {fehlt}.")
        return False, signal, f"Sicht widerlegt ({fehlt}); {zaehlzeile}"
    if all(v is True for v in teile.values()):
        _merken(laufzeit, satz)
        return True, signal, f"Sicht bestaetigt ({', '.join(teile)}); {zaehlzeile}"
    _merken(laufzeit, f"{satz} Bedienelemente nicht prüfbar.")
    return None, signal, f"Sicht nicht pruefbar (Bedienelemente); {zaehlzeile}"
