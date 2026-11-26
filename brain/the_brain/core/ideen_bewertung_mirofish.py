"""Bewertung einer Bubble durch MiroFish ("Rollen urteilen direkt", 26.11.2026).

Entscheidung des Betreibers: "Ich will MiroFish eigentlich immer als Bewertung." "Bewerte diese Bubble"
und "Neu bewerten" gehen deshalb an MiroFish (``POST /api/bubble/evaluate``, Dienst in
spaces/mirofish/.../services/bubble_bewertung.py), nicht mehr an brain-ideas.

Ablauf nach dem Muster des Bauauftrags (core/coding_auftrag):

1. **Anlegen** (``executor_bauen``, im Plan-Executor statt des Agentenauftrags): Bubble lesen, Karten als
   Kuerzel K1..Kn mit vollem Text (``kartentext`` der Kartenregeln), Verbindungen, das neueste White Paper,
   ``kartenstand``. Gekuerzt wird nie; ueber der Obergrenze kommt eine Absage ohne Aufruf.
   Die Kennungen bleiben im Rueckweg; MiroFish und das Modell sehen nur Kuerzel.
2. **Starten** (``MiroFishBewertung.call_with_arg``): Auftragszeile mit Status ``laeuft`` anlegen (der
   Ausfuehrer am PC nimmt nur ``offen``), dann MiroFish die Bubble geben. Nimmt MiroFish sie nicht,
   wird die Zeile mit Grund geschlossen; es gibt **kein Ausweichen** auf brain-ideas.
3. **Nachfassen** (``runde``, im Takt des Nachfassers): Stand bei MiroFish fragen. Fertig: Ergebnis
   streng pruefen (Raster, Kuerzel, Kartenstand), die Zeile beanspruchen, dann ueber den festen
   Rueckweg ``idea.bewertung_speichern`` mit Herkunftsbeleg schreiben, Quelle "mirofish". Fehlgeschlagen:
   Zeile mit Grund schliessen, nichts geschrieben.

Kosten: MiroFish nennt beim Start die geschaetzte und die hoechste Zahl der Abo-Aufrufe (heute 5 und 10)
und im Ergebnis die tatsaechliche; die steht in der Meldung und im Lauf (``aufrufe``).

Entscheid des Betreibers (26.11.2026): ein Knopf, jede Bewertung geht an MiroFish. Der alte Weg ueber
brain-ideas ist stillgelegt (kein Satz, keine Einstellung fuehrt mehr dorthin); sein Code bleibt, bis der
neue Weg im Betrieb belegt ist. Ist MiroFish nicht erreichbar, gibt es eine klare Absage, kein Ausweichen.

Einstellungen:
- ``VIBEMIND_MIROFISH_BEWERTUNG_KB``: Obergrenze der Bubble in KB (Vorgabe 400, wie MiroFish).
- ``MIROFISH_BASE_URL``: Adresse von MiroFish (wie core/capability_targets).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Mapping, Optional

from .ideen_dokument import Lesen, _bubble_finden, _lage, titel_von
from .ideen_fest_contract import ist_kennung
from .ideen_rueckweg import RueckwegFehler, _sauber

logger = logging.getLogger(__name__)

GRENZE_EINSTELLUNG = "VIBEMIND_MIROFISH_BEWERTUNG_KB"
URL_EINSTELLUNG = "MIROFISH_BASE_URL"
URL_VORGABE = "http://127.0.0.1:5001"
GRENZE_KB_VORGABE = 400
ZIEL = "mirofish-bewertung"
AGENT = "mirofish"
EREIGNIS = "idea.bewerten"
UEBERGABE = "mirofish_bewertung"
FRIST_S = 30 * 60
HTTP_S = 30.0
VERSION = 1
_TASK = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_STAND = re.compile(r"^sha256:[0-9a-f]{64}$")

NICHT_ERREICHBAR = ("MiroFish ist gerade nicht erreichbar; bewertet habe ich nichts. Ohne MiroFish gibt es "
                    "keine Bewertung, die das Befördern freischaltet.")
TABELLE_FEHLT = "Die Auftragstabelle ist nicht erreichbar; ohne sie starte ich keine Bewertung."
AUFTRAGSWEG_AUS = "Die Bewertung läuft nur als Auftrag; der Auftragsweg ist hier aus."
NICHT_VERFOLGBAR = "Die Bewertung wurde gestartet, aber ihr Stand lässt sich nicht verfolgen; gespeichert ist nichts."
UNBEKANNT = ("MiroFish kennt diese Bewertung nicht mehr (vermutlich neu gestartet); gespeichert ist nichts. "
             "Sag noch einmal „Bewerte diese Bubble“.")
WARTET = "Die Bewertung wartet auf Freigabe; gespeichert ist noch nichts."
GEAENDERT = ("Während der Bewertung haben sich Karten geändert; gespeichert habe ich nichts. "
             "Sag noch einmal „Bewerte diese Bubble“.")
_FORM = "MiroFish hat nicht in der vereinbarten Form geantwortet; gespeichert ist nichts."


def _regeln() -> Any:
    from .ideen_bewertung import _regeln as regeln
    return regeln()


# ── Einstellungen ────────────────────────────────────────────────────────────

def ueber_mirofish(arg: Mapping[str, Any], umgebung: Optional[Mapping[str, str]] = None) -> bool:
    """Jede Bewertung geht an MiroFish (Entscheid des Betreibers, 26.11.2026); kein Argument, keine
    Einstellung fuehrt zurueck zu brain-ideas. Die Signatur bleibt fuer den Plan-Ausfuehrer."""
    return True


def grenze_bytes(umgebung: Optional[Mapping[str, str]] = None) -> int:
    umgebung = os.environ if umgebung is None else umgebung
    try:
        kb = int((umgebung.get(GRENZE_EINSTELLUNG) or str(GRENZE_KB_VORGABE)).strip())
    except ValueError:
        kb = GRENZE_KB_VORGABE
    return max(1, kb) * 1024


def _basis(umgebung: Optional[Mapping[str, str]] = None) -> str:
    umgebung = os.environ if umgebung is None else umgebung
    return (umgebung.get(URL_EINSTELLUNG) or URL_VORGABE).strip().rstrip("/")


# ── 1. Anlegen ───────────────────────────────────────────────────────────────

def vorbereiten(bubble: Mapping[str, Any], lesen: Lesen,
                umgebung: Optional[Mapping[str, str]] = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(Anfrage an MiroFish, Rueckweg) oder ``RueckwegFehler``. Kuerzt nie."""
    from .ideen_bewertung import _alle_zeilen, _kanten

    modul = _regeln()
    bubble_id, bubble_titel = str(bubble["id"]), titel_von(bubble)
    zeilen = _alle_zeilen(bubble_id, lesen)
    kanten = _kanten([z["id"] for z in zeilen], lesen)
    stand = modul.kartenstand(zeilen, kanten)
    notizen = sorted((z for z in zeilen if modul.ist_notiz(z)), key=_lage)
    if not notizen:
        raise RueckwegFehler("In dieser Bubble gibt es keine Karten; es gibt nichts zu bewerten.")
    karten, kuerzel, kuerzel_titel = [], {}, {}
    for n, karte in enumerate(notizen, start=1):
        k = f"K{n}"
        karten.append({"k": k, "titel": titel_von(karte), "text": modul.kartentext(karte)})
        kuerzel[k], kuerzel_titel[k] = karte["id"], titel_von(karte)
    nach_id = {i: k for k, i in kuerzel.items()}
    verbindungen = sorted({(nach_id[e["from_node_id"]], nach_id[e["to_node_id"]], str(e.get("edge_type") or "related"))
                           for e in kanten if e.get("from_node_id") in nach_id and e.get("to_node_id") in nach_id})
    papiere = sorted((z for z in zeilen if modul.ist_white_paper(z)), key=lambda z: str(z.get("updated_at") or ""))
    anfrage = {"titel": bubble_titel, "kartenstand": stand, "karten": karten,
               "verbindungen": [list(v) for v in verbindungen],
               "white_paper": modul.kartentext(papiere[-1]) if papiere else ""}
    groesse = len(json.dumps(anfrage, ensure_ascii=False).encode("utf-8"))
    grenze = grenze_bytes(umgebung)
    if groesse > grenze:
        raise RueckwegFehler(f"Diese Bubble ist mit {groesse // 1024} KB zu groß für eine Bewertung (höchstens "
                             f"{grenze // 1024} KB); gekürzt wird nicht, angelegt ist nichts. Fasse Karten "
                             "zusammen oder teile die Bubble.")
    rueckweg = {"version": VERSION, "vertrag": "bewerten_mirofish", "bubble_id": bubble_id,
                "titel": _sauber(f"Bewertung: {bubble_titel}", 200), "kuerzel": kuerzel,
                "kuerzel_titel": kuerzel_titel, "kartenstand": stand}
    return anfrage, rueckweg


def rueckweg_gueltig(rueckweg: Any) -> bool:
    if not isinstance(rueckweg, Mapping) or rueckweg.get("vertrag") != "bewerten_mirofish":
        return False
    k, kt = rueckweg.get("kuerzel"), rueckweg.get("kuerzel_titel")
    return (isinstance(k, Mapping) and bool(k) and all(ist_kennung(v) for v in k.values())
            and isinstance(kt, Mapping) and set(kt) == set(k) and ist_kennung(rueckweg.get("bubble_id"))
            and isinstance(rueckweg.get("kartenstand"), str) and _STAND.match(rueckweg["kartenstand"]) is not None)


# ── 2. Starten ───────────────────────────────────────────────────────────────

Http = Callable[[str, str, Optional[dict]], tuple[int, Optional[dict]]]


def http(methode: str, pfad: str, koerper: Optional[dict] = None) -> tuple[int, Optional[dict]]:
    """(HTTP-Status, JSON) von MiroFish; Status 0 bei Transportfehler. Wirft nie."""
    import requests

    try:
        antwort = requests.request(methode, f"{_basis()}{pfad}", json=koerper, timeout=HTTP_S)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[mirofish-bewertung] {methode} {pfad}: {type(e).__name__}")
        return 0, None
    try:
        daten = antwort.json()
    except ValueError:
        daten = None
    return antwort.status_code, daten if isinstance(daten, dict) else None


def _hinweis(geschaetzt: Any, hoechstens: Any) -> str:
    if isinstance(geschaetzt, int) and isinstance(hoechstens, int) and geschaetzt > 0:
        return (f"MiroFish bewertet die Bubble mit fünf Rollen: {geschaetzt} Abo-Aufrufe, höchstens "
                f"{hoechstens}. Ich melde mich mit dem Ergebnis.")
    return "MiroFish bewertet die Bubble mit fünf Rollen. Ich melde mich mit dem Ergebnis."


class MiroFishBewertung:
    """Ausfuehrer fuer den Plan-Executor (Form wie ``AuftragsExecutor``): legt an und startet."""

    def __init__(self, anfrage: dict[str, Any], rueckweg: dict[str, Any], *, trace_id: str, plan_id: str,
                 hop_id: str, antwortkanal: Any, tabelle: Any = None, senden: Optional[Http] = None) -> None:
        self.anfrage, self.rueckweg = anfrage, rueckweg
        self.trace_id, self.plan_id, self.hop_id, self.antwortkanal = trace_id, plan_id, hop_id, antwortkanal
        self._tabelle = tabelle
        self._senden = senden or http

    def _tabelle_holen(self) -> Any:
        if self._tabelle is None:
            from . import agent_auftraege
            self._tabelle = agent_auftraege.tabelle_aus_umgebung()
        return self._tabelle

    def call_with_arg(self, arg: Any = None, arg_kwarg: Optional[str] = None,
                      extra_params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        del arg, arg_kwarg, extra_params   # die Anfrage steht seit dem Anlegen fest
        try:
            tabelle = self._tabelle_holen()
            auftrag_id = tabelle.anlegen(
                capability=EREIGNIS, agent=AGENT, auftrag=_sauber(self.rueckweg["titel"], 300),
                trace_id=self.trace_id, plan_id=self.plan_id, hop_id=self.hop_id,
                uebergabe={UEBERGABE: {"rueckweg": self.rueckweg}}, antwortkanal=self.antwortkanal,
                frist_s=FRIST_S, status="laeuft")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[mirofish-bewertung] Zeile nicht angelegt: {type(e).__name__}")
            return {"ok": False, "error": f"mirofish_bewertung: {TABELLE_FEHLT}", "retryable": False}
        status, antwort = self._senden("POST", "/api/bubble/evaluate", self.anfrage)
        daten = antwort.get("data") if isinstance(antwort, dict) and antwort.get("success") is True else None
        task_id = daten.get("task_id") if isinstance(daten, dict) else None
        if status != 202 or not isinstance(task_id, str) or not _TASK.match(task_id):
            if status == 0 or status >= 500:
                meldung, grund = NICHT_ERREICHBAR, f"start: HTTP {status}"
            else:
                warum = antwort.get("error") if isinstance(antwort, dict) else None
                meldung = (f"MiroFish hat die Bewertung nicht angenommen: {_sauber(str(warum), 300)}"
                           if warum else "MiroFish hat die Bewertung nicht angenommen.") + " Bewertet ist nichts."
                grund = f"start: HTTP {status}"
            _schliessen(tabelle, auftrag_id, "fehler", False, meldung, grund)
            return {"ok": False, "error": f"mirofish_bewertung: {meldung}", "retryable": False}
        try:
            tabelle.uebergabe_setzen(auftrag_id, {UEBERGABE: {
                "rueckweg": self.rueckweg, "task_id": task_id,
                "aufrufe_geschaetzt": daten.get("aufrufe_geschaetzt"),
                "aufrufe_hoechstens": daten.get("aufrufe_hoechstens")}})
        except Exception as e:  # noqa: BLE001 - die Bewertung laeuft; ohne Kennung schliesst der Nachfasser ehrlich
            logger.warning(f"[mirofish-bewertung] Kennung nicht gespeichert: {type(e).__name__}")
        return {"ok": False, "pending": True, "invocation_id": auftrag_id,
                "result": {"auftrag_id": auftrag_id, "agent": AGENT, "status": "laeuft",
                           "hinweis": _hinweis(daten.get("aufrufe_geschaetzt"), daten.get("aufrufe_hoechstens"))}}


def executor_bauen(rendered_arg: Any, *, lesen: Lesen, trace_id: str, plan_id: str, hop_id: str,
                   antwortkanal: Any) -> MiroFishBewertung:
    """Bubble lesen und den Ausfuehrer bauen; ``RueckwegFehler`` mit deutschem Satz, wenn es nicht geht."""
    from .ideen_dokument import _argument

    bubble = _bubble_finden(_argument(rendered_arg), lesen)
    anfrage, rueckweg = vorbereiten(bubble, lesen)
    return MiroFishBewertung(anfrage, rueckweg, trace_id=trace_id, plan_id=plan_id, hop_id=hop_id,
                             antwortkanal=antwortkanal)


# ── 3. Ergebnis pruefen ──────────────────────────────────────────────────────

def ergebnis_pruefen(ergebnis: Any, rueckweg: Mapping[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
    """(Lauf fuer ``idea.bewertung_speichern`` mit Quelle mirofish, "") oder (None, Satz). Wirft nie."""
    from .ideen_bewertung import _ganz, _text, _todo_pruefen
    from .ideen_dokument import kuerzel_ersetzen

    modul = _regeln()
    if not isinstance(ergebnis, Mapping):
        return None, _FORM
    if ergebnis.get("kartenstand") != rueckweg["kartenstand"]:
        return None, "MiroFish hat einen anderen Kartenstand bewertet; gespeichert ist nichts."
    sichten = ergebnis.get("sichten")
    if not isinstance(sichten, Mapping) or set(sichten) != set(modul.SICHTEN):
        return None, _FORM
    aufrufe = _ganz(ergebnis.get("aufrufe"), 0, modul.AUFRUFE_MAX)
    if aufrufe is None:
        return None, _FORM
    kuerzel, titel = rueckweg["kuerzel"], rueckweg["kuerzel_titel"]
    aus: dict[str, dict[str, Any]] = {}
    todos: list[dict[str, Any]] = []
    for name in modul.SICHTEN:
        sicht = sichten[name]
        liste = sicht.get("kriterien") if isinstance(sicht, Mapping) else None
        if not isinstance(liste, list) or len(liste) != modul.KRITERIEN_JE_SICHT:
            return None, f"MiroFish hat für {modul.SICHT_NAMEN[name]} kein vollständiges Raster geliefert; " \
                         "gespeichert ist nichts."
        kriterien = []
        for k in liste:
            if not isinstance(k, Mapping):
                return None, _FORM
            kname = _text(k.get("name"), modul.KRITERIUM_NAME_ZEICHEN)
            punkte = _ganz(k.get("punkte"), 0, modul.KRITERIUM_MAX)
            grund = k.get("begruendung")
            grund = "" if grund in (None, "") else _text(grund, modul.KRITERIUM_GRUND_ZEICHEN)
            if kname is None or punkte is None or grund is None:
                return None, _FORM
            kriterien.append({"name": kname, "punkte": punkte, "begruendung": kuerzel_ersetzen(grund, titel)})
        summe = sum(k["punkte"] for k in kriterien)
        if _ganz(sicht.get("punkte"), 0, modul.SICHT_MAX) != summe:
            # Die Punkte der Sicht sind die Summe des Rasters; alles andere ist kein MiroFish-Ergebnis.
            return None, f"Bei {modul.SICHT_NAMEN[name]} passen Punkte und Raster nicht zusammen; gespeichert ist nichts."
        urteil = _text(sicht.get("urteil"), modul.URTEIL_ZEICHEN)
        if urteil is None:
            return None, _FORM
        aus[name] = {"punkte": summe, "urteil": kuerzel_ersetzen(urteil, titel), "kriterien": kriterien}
        liste_todos = sicht.get("todos", [])
        if not isinstance(liste_todos, list):
            return None, _FORM
        geprueft_sicht = []
        for todo in liste_todos[:modul.TODOS_JE_SICHT]:
            geprueft, satz = _todo_pruefen(todo, name, kuerzel, titel, modul)
            if geprueft is None:
                return None, satz.replace("Der Agent", "MiroFish").replace("es wurde nichts geändert",
                                                                           "gespeichert ist nichts")
            geprueft_sicht.append(geprueft)
        # Ein To-do bringt hoechstens, was der Sicht fehlt (Kartenregeln, auch wenn MiroFish es schon tut).
        todos += modul.todos_begrenzen(geprueft_sicht, summe)
    lauf = {"sichten": aus, "todos": todos, "gekuerzt": False, "quelle": modul.QUELLE_MIROFISH, "aufrufe": aufrufe}
    if modul.lauf_fehler(lauf):
        return None, _FORM
    return lauf, ""


# ── 4. Nachfassen ────────────────────────────────────────────────────────────

def _schliessen(tabelle: Any, auftrag_id: str, status: str, verified: Optional[bool], meldung: str,
                grund: str, **mehr: Any) -> bool:
    pruefung = {"verified": verified, "reason": grund, "kind": "mirofish_bewertung",
                "rueckweg": "geschrieben" if status == "fertig" else "fehler", "meldung": meldung,
                "fortsetzung": "fertig" if status == "fertig" else "fehler", **mehr}
    if status != "fertig":
        pruefung["fortsetzung_fehler"] = meldung
    try:
        return bool(tabelle.recherche_abschliessen(auftrag_id, status, pruefung))
    except Exception:  # noqa: BLE001
        logger.exception("MiroFish-Bewertung %s nicht abgeschlossen", auftrag_id)
        return False


def _schreiben(zeile: Mapping[str, Any], rueckweg: Mapping[str, Any], lauf: dict[str, Any],
               anschluss: Callable[[Mapping[str, Any]], dict[str, Any]]) -> tuple[str, Optional[bool], str, str, dict]:
    from .ideen_bewertung import _fest, bewertung_meldung

    try:
        bindung = anschluss(zeile)
    except Exception as e:  # noqa: BLE001
        return "fehler", None, f"anschluss: {type(e).__name__}", \
            "Der Rückweg ist gerade nicht verfügbar; gespeichert ist nichts.", {}
    ok, grund = _fest("idea.bewertung_speichern",
                      {"bubble_id": rueckweg["bubble_id"], "lauf": lauf, "kartenstand": rueckweg["kartenstand"]},
                      bindung["aufruf"], bindung["pruefe"])
    if ok is not True:
        from .ideen_bewertung import HERKUNFT_SATZ
        if grund == "wartet":
            return "fehler", None, "idea.bewertung_speichern: approval pending", WARTET, {}
        if "herkunft_unbelegt" in grund:
            return "fehler", False, grund, HERKUNFT_SATZ, {}
        if "kartenstand_changed" in grund:
            return "fehler", False, grund, GEAENDERT, {}
        return "fehler", ok, grund, "Die Bewertung ließ sich nicht speichern. Bitte prüfe die Bubble.", {}
    score = _regeln().lauf_score(lauf)
    return "fertig", True, "written and verified", bewertung_meldung(lauf, score), \
        {"score": score, "aufrufe": lauf["aufrufe"]}


def nachfassen(zeile: Mapping[str, Any], *, stand: Http, beanspruchen: Callable[[], bool],
               anschluss: Callable[[Mapping[str, Any]], dict[str, Any]]) -> Optional[tuple]:
    """``None`` (laeuft weiter oder schon beansprucht) oder ``(status, verified, meldung, grund, mehr)``.

    Wirft bei Transportfehlern (dann bleibt die Zeile, die naechste Runde fragt erneut).
    """
    uebergabe = zeile.get("uebergabe") if isinstance(zeile.get("uebergabe"), Mapping) else {}
    daten = uebergabe.get(UEBERGABE) if isinstance(uebergabe.get(UEBERGABE), Mapping) else {}
    rueckweg, task_id = daten.get("rueckweg"), daten.get("task_id")
    if not rueckweg_gueltig(rueckweg) or not isinstance(task_id, str) or not _TASK.match(task_id):
        return "fehler", None, NICHT_VERFOLGBAR, "ohne kennung", {}
    status, antwort = stand("GET", f"/api/bubble/evaluate/{task_id}", None)
    if status == 0 or status >= 500 or (status == 200 and not isinstance(antwort, dict)):
        raise RuntimeError(f"MiroFish nicht erreichbar (HTTP {status})")
    if status == 404:
        return "fehler", False, UNBEKANNT, "unbekannt", {"task_id": task_id}
    if status != 200:
        raise RuntimeError(f"MiroFish: HTTP {status}")
    sicht = antwort.get("data") if isinstance(antwort.get("data"), Mapping) else {}
    zustand = sicht.get("status")
    aufrufe = sicht.get("aufrufe") if isinstance(sicht.get("aufrufe"), int) else None
    mehr = {"task_id": task_id, **({"aufrufe": aufrufe} if aufrufe is not None else {})}
    if zustand in ("pending", "processing"):
        return None
    if zustand == "failed":
        fehler = _sauber(str(sicht.get("error") or ""), 300)
        satz = f"MiroFish hat die Bewertung nicht abgeschlossen: {fehler}" if fehler else \
            "MiroFish hat die Bewertung nicht abgeschlossen."
        if "budget" in fehler.casefold():
            satz += " Das Tagesbudget für MiroFish ist erschöpft."
        kosten = f" Verbraucht: {aufrufe} Abo-Aufrufe." if aufrufe else ""
        return "fehler", False, f"{satz} Gespeichert ist nichts.{kosten}", "failed", mehr
    if zustand != "completed":
        raise RuntimeError("MiroFish: Zustand unbekannt")
    lauf, satz = ergebnis_pruefen(sicht.get("result"), rueckweg)
    if lauf is None:
        return "fehler", False, satz, "result rejected", mehr
    # Erst beanspruchen, dann schreiben: so wird ein Ergebnis nie zweimal gespeichert.
    if not beanspruchen():
        return None
    status_aus, verified, grund, meldung, weiter = _schreiben(zeile, rueckweg, lauf, anschluss)
    return status_aus, verified, meldung, grund, {**mehr, **weiter}


def _anschluss_echt(zeile: Mapping[str, Any]) -> dict[str, Any]:
    from . import ideen_rueckweg
    return ideen_rueckweg.anschluss(zeile)


def runde(tabelle: Any, *, stand: Optional[Http] = None,
          anschluss: Optional[Callable[[Mapping[str, Any]], dict[str, Any]]] = None) -> dict[str, int]:
    """Alle laufenden MiroFish-Bewertungen einmal nachfassen (Nachfasser-Takt). Wirft nie."""
    z = {"mirofish_fertig": 0, "mirofish_fehler": 0, "mirofish_wartet": 0, "fehler": 0}
    try:
        zeilen = tabelle.mirofish_laufend()
    except Exception:  # noqa: BLE001
        logger.exception("mirofish_laufend fehlgeschlagen")
        z["fehler"] += 1
        return z
    for zeile in zeilen if isinstance(zeilen, list) else []:
        if not isinstance(zeile, Mapping) or zeile.get("id") is None:
            continue
        auftrag_id = str(zeile["id"])

        def beanspruchen(auftrag_id: str = auftrag_id) -> bool:
            return bool(tabelle.pruefung_setzen(auftrag_id, {"verified": None, "reason": "mirofish schreibt",
                                                             "kind": "mirofish_bewertung", "fortsetzung": "laeuft"}))

        try:
            aus = nachfassen(zeile, stand=stand or http, beanspruchen=beanspruchen,
                             anschluss=anschluss or _anschluss_echt)
        except Exception as e:  # noqa: BLE001 - naechste Runde fragt erneut
            logger.warning(f"[mirofish-bewertung] Stand nicht lesbar: {type(e).__name__}")
            z["fehler"] += 1
            continue
        if aus is None:
            z["mirofish_wartet"] += 1
            continue
        status, verified, meldung, grund, mehr = aus
        if _schliessen(tabelle, auftrag_id, status, verified, meldung, grund, **mehr):
            z["mirofish_fertig" if status == "fertig" else "mirofish_fehler"] += 1
    return z
