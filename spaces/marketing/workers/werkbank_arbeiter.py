"""Werkbank-Arbeiter am PC (Video-Werkbank Stufe 1, 11.11.2026). Holt Video-Auftraege ueber die Marketing-API der VM
(/api/werkbank/arbeiter, X-Bild-Key), sammelt Kontext, laesst Claude ueber die eigene Shim :8150 Gliederung und
Szenentexte schreiben, prueft jede Szene mechanisch gegen ihre Quellen, spricht und rendert freigegebene Szenen und
laedt die Clips in Teilen hoch. Versendet wird nichts. Gestartet von marketing-dienste-starten.ps1; Gesundheits-Port 8151.

Regeln, die hier gelten:
- Hoechstens eine Szene je Werk und Durchlauf, und nie zwei Renders gleichzeitig (der PC hat wenig RAM, die GPU
  gehoert auch anderen Diensten).
- Jeder Modellaufruf wird je Werk gezaehlt (`aufruf`), auch ein unbrauchbarer.
- Jede Ausnahme einer Szene (Sprechen, Rendern, Hochladen) ist der Fehler genau dieser Szene: ein kurzer deutscher
  Satz nach Fehlerklasse, nie der Ausnahmetext. Die anderen Werke laufen weiter.
- Hinweise an den Betreiber (`api.hinweis`) tragen einen stabilen Schluessel, damit dasselbe Problem nur einmal
  gemeldet wird. Sie nennen nie Prompt-, Dokument- oder Sprechtext und keine Pfade. Die Schluessel der API duerfen
  nur a-z, 0-9 und _ enthalten."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from spaces.marketing.claw import video_kontext, video_slides, video_texte
from spaces.marketing.workers.bild_worker import ApiFehler, umgebung_laden
from spaces.marketing.workers.chat_worker import MODELL, LlmFehler
from spaces.marketing.workers.video_rendern import szene_rendern
from spaces.marketing.workers.video_stimme import szene_sprechen
from spaces.marketing.workers.video_zusammen import fertigstellen as video_fertigstellen
from spaces.marketing.workers.werkbank_api import WerkbankApi

LOG = logging.getLogger("werkbank_arbeiter")
PORT = 8151
TAKT_S = 30
LLM_URL = os.environ.get("MARKETING_WERKBANK_LLM_URL", "http://127.0.0.1:8150/v1")
LLM_ZEITLIMIT_S = 900            # wie --timeout der Shim
BLOCK = video_texte.BLOCK
NOCH_EINMAL_PAUSE_S = 5          # zwischen einer unbrauchbaren Antwort und der Nachfrage
BUDGET_PAUSE_S = 900             # so lange wird nach einer Budget-Absage nicht erneut gefragt
ANTWORT_MAX = 12000              # Zeichen der letzten Antwort, die bei der Nachfrage mitgehen
STAND: dict = {"letzter_lauf": None, "letztes_ergebnis": None}

SATZ_BUDGET = "Das Tagesbudget für Modell-Aufrufe ist aufgebraucht – morgen geht es weiter."
STALE_CLIP = "keinen Clip an"          # Teil der 409-Meldung der API, wenn die Variante/der Stand nicht mehr passt
SATZ_GLIEDERUNG = "Die Gliederung ließ sich nicht schreiben."


class BudgetErschoepft(Exception):
    """Die Shim hat den Aufruf wegen der Tagesgrenze des Agenten abgelehnt (HTTP 429, type budget)."""


def _budget_typ(e: urllib.error.HTTPError) -> bool:
    try:
        fehler = json.loads(e.read() or b"{}").get("error")
        return isinstance(fehler, dict) and fehler.get("type") == "budget"
    except (ValueError, OSError, AttributeError):
        return False


def shim_fragen(system: str, nachricht: str) -> str:
    """Eine Frage an Claude ueber die Werkbank-Shim (ohne Werkzeuge, nicht streamend). HTTP 429 mit type budget
    wird BudgetErschoepft, alles andere LlmFehler (nur mit Typname)."""
    koerper = {"model": MODELL, "marketing_ohne_werkzeuge": True,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": nachricht}]}
    req = urllib.request.Request(LLM_URL.rstrip("/") + "/chat/completions",
                                 data=json.dumps(koerper).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=LLM_ZEITLIMIT_S) as r:
            text = json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        if e.code == 429 and _budget_typ(e):
            raise BudgetErschoepft from None
        raise LlmFehler(f"HTTP {e.code}") from None
    except (OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as e:
        raise LlmFehler(type(e).__name__) from None
    if not isinstance(text, str) or not text.strip():
        raise LlmFehler("Leere Antwort")
    return text


def arbeitsordner(aid8: str, nr: int) -> Path:
    """%LOCALAPPDATA%\\VibeMind\\werkbank\\<aid8>\\s<nr>"""
    wurzel = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(wurzel) / "VibeMind" / "werkbank" / aid8 / f"s{nr}"


def _brief() -> str:
    return (Path(video_texte.__file__).parent / "video_brief.md").read_text(encoding="utf-8")


class _Lauf:
    """Alles, was ein Durchlauf braucht (die einspritzbaren Teile und das Gedaechtnis des Arbeiters)."""

    def __init__(self, api, fragen, kontext, sprechen, rendern, fertigstellen, warte, marke, ordner, brief, jetzt, heute):
        self.api, self.fragen, self.kontext, self.sprechen, self.rendern = api, fragen, kontext, sprechen, rendern
        self.fertigstellen, self.warte, self.marke, self.ordner = fertigstellen, warte, marke, ordner
        self.brief, self.jetzt, self.heute = brief, jetzt, heute
        self.gedaechtnis = _gedaechtnis(api)
        self.budget = jetzt() < self.gedaechtnis.get("budget_bis", 0.0)

    def gezaehlt(self, aid: str) -> None:
        try:
            self.api.aufruf(aid)
        except Exception as e:  # noqa: BLE001 - die Antwort ist da, das Zaehlen darf sie nicht kosten
            LOG.warning("Aufruf zaehlen scheiterte: %s", type(e).__name__)

    def lease(self, aid: str) -> bool:
        """Lease erneuern; False = der Auftrag ist nicht (mehr) in Arbeit (oder die Antwort ging verloren)."""
        try:
            return bool(self.api.weiter(aid))
        except Exception as e:  # noqa: BLE001
            LOG.warning("Lease erneuern scheiterte: %s", type(e).__name__)
            return False

    def hinweis(self, aid: str, key: str, satz: str) -> None:
        try:
            self.api.hinweis(aid, key, satz)
        except Exception as e:  # noqa: BLE001
            LOG.warning("Hinweis scheiterte: %s", type(e).__name__)


def _gedaechtnis(api) -> dict:
    """Merkzettel je Api-Objekt: aufgegebene Gliederungen/Szenen (bis neuer Wunsch) und die Budget-Pause."""
    g = getattr(api, "_werkbank_gedaechtnis", None)
    if g is None:
        g = {"aufgegeben": {}, "budget_bis": 0.0, "budget_gemeldet": set(), "fehler_offen": {}}
        try:
            api._werkbank_gedaechtnis = g
        except AttributeError:
            pass
    return g


def _budget_getroffen(c: _Lauf, werk: dict) -> None:
    """Die Shim hat gerade abgelehnt: die Pause beginnt JETZT (nur hier, sonst schiebt jeder Lauf sie weiter)."""
    c.gedaechtnis["budget_bis"] = c.jetzt() + BUDGET_PAUSE_S
    _budget_melden(c, werk)


def _budget_melden(c: _Lauf, werk: dict) -> None:
    """Budget leer (frisch getroffen oder noch in der Pause): Lease halten, einmal je Werk und Tag den Hinweis
    und, solange der Auftrag in Arbeit ist, die Frist verlaengern. Beruehrt die Pause nicht."""
    aid, tag = str(werk["auftrag"]), c.heute().isoformat()
    c.budget = True
    in_arbeit = c.lease(aid)
    if (aid, tag) in c.gedaechtnis["budget_gemeldet"]:
        return
    c.hinweis(aid, "budget_" + tag.replace("-", "_"), SATZ_BUDGET)
    if in_arbeit:
        try:
            c.api.frist(aid)
        except Exception as e:  # noqa: BLE001
            LOG.warning("Frist verlaengern scheiterte: %s", type(e).__name__)
    c.gedaechtnis["budget_gemeldet"].add((aid, tag))


def _fragen_lesen(c: _Lauf, aid: str, system: str, nachricht: str, lesen):
    """Claude fragen und lesen. `lesen(antwort) -> (ergebnis | None, [Fehlersaetze])`. Unbrauchbar: einmal neu fragen
    mit den Fehlersaetzen (vorher Lease erneuern). Jeder Aufruf wird gezaehlt. Rueckgabe (ergebnis, letzte Fehler)."""
    fehler: list[str] = []
    for versuch in range(2):
        if versuch:
            c.warte(NOCH_EINMAL_PAUSE_S)
            c.lease(aid)
            nachricht += ("\n\n# Deine letzte Antwort war nicht brauchbar\n" + antwort[:ANTWORT_MAX]
                          + "\n\n# Fehler\n" + "\n".join(f"- {f}" for f in fehler)
                          + "\nSchreibe die vollständige Antwort neu und behebe diese Fehler.")
        antwort = c.fragen(system, nachricht)
        c.gezaehlt(aid)
        ergebnis, fehler = lesen(antwort)
        if ergebnis is not None:
            return ergebnis, []
    return None, fehler


# --- Schritt 1: Gliederung -----------------------------------------------------------------------

def _quellen_der_gliederung(gliederung: dict) -> list[str]:
    gesehen: list[str] = []
    for s in video_texte.szenen_liste(gliederung):
        for q in s.get("quellen") or []:
            if q != video_texte.AUFTRAG and q not in gesehen:
                gesehen.append(q)
    return gesehen


def _gliederung_schluessel(werk: dict) -> tuple:
    return ("gliederung", str(werk["auftrag"]), int(werk.get("gliederung_fassung") or 0),
            str(werk.get("gliederung_wunsch") or "").strip())


def _gliederung(c: _Lauf, werk: dict) -> str | None:
    aid, eingabe = str(werk["auftrag"]), werk.get("eingabe") or {}
    fassung = int(werk.get("gliederung_fassung") or 0)
    wunsch = str(werk.get("gliederung_wunsch") or "").strip()
    schluessel = _gliederung_schluessel(werk)
    if werk.get("gliederung_stand") != "geplant":
        return None
    if schluessel in c.gedaechtnis["aufgegeben"]:     # zweimal unbrauchbar: erst bei neuer Fassung oder neuem Wunsch wieder
        return None
    if c.budget:
        _budget_melden(c, werk)
        return "budget"
    c.lease(aid)
    try:
        ktx = c.kontext(str(werk.get("mandant") or ""), video_texte.auftragstext(eingabe))
        vorige = werk.get("gliederung") if wunsch and isinstance(werk.get("gliederung"), dict) and werk["gliederung"] else None
        system, nachricht = video_texte.gliederung_prompt(eingabe, ktx, c.brief(), wunsch, vorige)
        geholt = {d.name for d in ktx.dokumente}
        gliederung, _ = _fragen_lesen(c, aid, system, nachricht, lambda a: video_texte.gliederung_lesen(a, geholt))
    except BudgetErschoepft:
        _budget_getroffen(c, werk)
        return "budget"
    if gliederung is None:
        c.hinweis(aid, f"gliederung_f{fassung}", SATZ_GLIEDERUNG)
        c.gedaechtnis["aufgegeben"][schluessel] = True
        return "fehler"
    c.lease(aid)                          # die Anfrage kann lange gedauert haben
    try:
        c.api.gliederung(aid, gliederung, _quellen_der_gliederung(gliederung))
    except ApiFehler as e:
        if e.code == 409:                 # die Gliederung ist gerade nicht dran (anderer Stand)
            return None
        raise
    return "gliederung"


# --- Schritt 2: Szenentexte ----------------------------------------------------------------------

def _belegquellen(szene: dict) -> list[str]:
    gesehen: list[str] = []
    for b in szene.get("belege") or []:
        q = b.get("quelle")
        if isinstance(q, str) and q and q not in gesehen:
            gesehen.append(q)
    return gesehen


def _szenen_fragen(c: _Lauf, werk: dict, ktx, zeilen: list[dict], fehler_je_nr: dict[int, list[str]] | None):
    """Eine Frage fuer bis zu BLOCK Szenen. Rueckgabe {nr: Szene} der lesbaren Antwort ({} bei Muell).
    Mit `fehler_je_nr` ist es die Nachfrage: Fehlersaetze und die bisherigen Szenen gehen mit."""
    system, nachricht = video_texte.szenen_prompt(werk.get("eingabe") or {}, werk.get("gliederung") or {},
                                                  zeilen, ktx, c.brief())
    if fehler_je_nr:
        zeilen_text = "\n".join(f"Szene {nr}: " + " ".join(f) for nr, f in sorted(fehler_je_nr.items()))
        nachricht += ("\n\n# Deine letzte Antwort war bei diesen Szenen nicht brauchbar\n" + zeilen_text
                      + "\nSchreibe genau diese Szenen vollständig neu und behebe die Fehler.")
    aid = str(werk["auftrag"])
    antwort = c.fragen(system, nachricht)
    c.gezaehlt(aid)
    gelesen = video_texte.szenen_lesen(antwort) or []
    return {s["nr"]: s for s in gelesen}


def _bewerten(zeile: dict, szene: dict | None, auftrag: str, ktx) -> list[str]:
    if szene is None:
        return ["Die Szene fehlt in der Antwort oder ist nicht lesbar."]
    if zeile.get("rueckgabe_von") == "clip" and szene.get("aendert_text") is False:
        szene["sprechtext"] = str(zeile.get("sprechtext") or "")     # nur das Bild ist neu: der Text bleibt, wie er war
    return video_texte.szene_pruefen(szene, auftrag, ktx)


def _abgeben(c: _Lauf, zeile: dict, szene: dict) -> bool:
    """Eine bestandene Szene an ihr Tor geben. False = die API nahm sie nicht (Stand hat sich geaendert)."""
    sid = str(zeile["id"])
    try:
        if zeile.get("rueckgabe_von") == "clip" and szene.get("aendert_text") is False:
            c.api.szene_bild(sid, szene["bildart"], szene["bild"])
        else:
            c.api.szene_text(sid, {"sprechtext": szene["sprechtext"], "bildart": szene["bildart"], "bild": szene["bild"],
                                   "uebergang": szene["uebergang"], "quellen": _belegquellen(szene)})
    except ApiFehler as e:
        LOG.warning("Szene abgelehnt: API %s", e.code)
        return False
    return True


# Art des Problems aus den Fehlersaetzen von `szene_pruefen` (Regel 1-4 Quellen, 5 Laenge, 6 Bild). Die Saetze
# tragen kein Regelkennzeichen; sie werden an ihren festen Wendungen erkannt, alles Unbekannte bleibt neutral.
_QUELLE = re.compile(r"Quelle|Beleg|belege")
_LAENGE = re.compile(r"^Der Sprechtext (hat|fehlt)")
_BILD = re.compile(r"Bildart|Übergang|Bildplan|Ein Bild braucht|^Schritt \d|ab_satz|Bild")
SAETZE_PROBLEM = {
    "quelle": "Szene {nr}: Quelle fehlt – bitte Material ergänzen.",
    "laenge": "Szene {nr}: Der Text passt nicht in die Länge – bitte Wunsch ergänzen.",
    "bild": "Szene {nr}: Die Bildschritte ließen sich nicht bauen – bitte Wunsch ergänzen.",
    "text": "Szene {nr} ließ sich nicht schreiben – bitte Wunsch ergänzen.",
}


def _problem(fehler: list[str]) -> tuple[str, str]:
    """(Art, Satz mit {nr}) nach der ersten Regel, die gescheitert ist: Quelle vor Laenge vor Bild."""
    for art, muster in (("quelle", _QUELLE), ("laenge", _LAENGE), ("bild", _BILD)):
        if any(muster.search(f) for f in fehler):
            return art, SAETZE_PROBLEM[art]
    return "text", SAETZE_PROBLEM["text"]


def _texte(c: _Lauf, werk: dict) -> str | None:
    if werk.get("gliederung_stand") != "text_ok":
        return None
    aid = str(werk["auftrag"])
    zeilen = [s for s in werk.get("szenen") or [] if s.get("stand") == "geplant"
              and ("szene", str(s.get("id")), str(s.get("wunsch") or "")) not in c.gedaechtnis["aufgegeben"]]
    if not zeilen:
        return None
    if c.budget:
        _budget_melden(c, werk)
        return "budget"
    c.lease(aid)
    auftrag = video_texte.auftragstext(werk.get("eingabe") or {})
    abgegeben = 0
    try:
        ktx = c.kontext(str(werk.get("mandant") or ""), auftrag)
        neu = [s for s in zeilen if not s.get("rueckgabe_von")]
        gruppen = [neu[i:i + BLOCK] for i in range(0, len(neu), BLOCK)] + [[s] for s in zeilen if s.get("rueckgabe_von")]
        durchgefallen: list[tuple[dict, list[str]]] = []
        for gruppe in gruppen:
            antworten = _szenen_fragen(c, werk, ktx, gruppe, None)
            for zeile in gruppe:
                szene = antworten.get(zeile["nr"])
                fehler = _bewerten(zeile, szene, auftrag, ktx)
                if not fehler:
                    abgegeben += _abgeben(c, zeile, szene)
                else:
                    durchgefallen.append((zeile, fehler))
        endgueltig: list[tuple[dict, list[str]]] = []
        for i in range(0, len(durchgefallen), BLOCK):       # alle Durchfaller gesammelt, einmal zurueck
            c.warte(NOCH_EINMAL_PAUSE_S)
            c.lease(aid)
            stapel = durchgefallen[i:i + BLOCK]
            antworten = _szenen_fragen(c, werk, ktx, [z for z, _ in stapel], {z["nr"]: f for z, f in stapel})
            for zeile, _alt in stapel:
                szene = antworten.get(zeile["nr"])
                fehler = _bewerten(zeile, szene, auftrag, ktx)
                if not fehler:
                    abgegeben += _abgeben(c, zeile, szene)
                else:
                    endgueltig.append((zeile, fehler))
    except BudgetErschoepft:
        _budget_getroffen(c, werk)
        return "budget"
    for zeile, fehler in endgueltig:
        art, satz = _problem(fehler)
        c.hinweis(aid, f"{art}_s{zeile['nr']}_f{int(zeile.get('fassung') or 0)}", satz.format(nr=zeile["nr"]))
        c.gedaechtnis["aufgegeben"][("szene", str(zeile["id"]), str(zeile.get("wunsch") or ""))] = True
    return "texte" if abgegeben else ("fehler" if endgueltig else None)


# --- Schritt 3: eine Szene sprechen, rendern, hochladen --------------------------------------------

def _satz_fuer(phase: str, e: Exception) -> str:
    """Kurzer deutscher Satz nach Phase und Fehlerklasse; nie der Ausnahmetext (kann Text oder Pfade enthalten)."""
    art = type(e).__name__
    if isinstance(e, TimeoutError) or art == "TimeoutExpired":
        return {"sprechen": "Das Sprechen der Szene hat zu lange gedauert.",
                "rendern": "Das Rendern der Szene hat zu lange gedauert."}.get(phase, f"Der Schritt {phase} hat zu lange gedauert.")
    return {"sprechen": f"Die Szene ließ sich nicht sprechen ({art}).",
            "rendern": f"Die Szene ließ sich nicht rendern ({art}).",
            "hochladen": f"Der Clip ließ sich nicht hochladen ({art}).",
            "melden": f"Der Clip ließ sich nicht melden ({art})."}.get(phase, f"Die Szene scheiterte ({art}).")


def _fehler_nachmelden(c: _Lauf, werk: dict) -> None:
    """Fehler, die der Server noch nicht kennt (die Meldung scheiterte), erneut melden."""
    offen = c.gedaechtnis["fehler_offen"]
    for s in werk.get("szenen") or []:
        sid = str(s.get("id"))
        if sid in offen:
            try:
                c.api.szene_fehler(sid, offen[sid])
                del offen[sid]
            except Exception as e:  # noqa: BLE001
                LOG.warning("Fehler nachmelden scheiterte: %s", type(e).__name__)


def _erzeugen(c: _Lauf, werk: dict) -> str | None:
    aid = str(werk["auftrag"])
    _fehler_nachmelden(c, werk)
    zeile = c.api.szene_nehmen(aid)
    if not zeile:
        return None
    sid, nr = str(zeile["id"]), int(zeile["nr"])
    if sid in c.gedaechtnis["fehler_offen"]:        # der Server kennt ihren Fehler noch nicht: nicht noch einmal rendern
        return "fehler"
    variante = int(zeile.get("variante") or 0)
    aid8 = aid.replace("-", "")[:8]
    name = f"wb-{aid8}-s{nr:02d}-f{variante}.mp4"
    ordner = Path(c.ordner(aid8, nr))
    shutil.rmtree(ordner, ignore_errors=True)       # hoechstens der letzte Versuch je Szene bleibt liegen
    stimme = str(werk.get("stimme") or "standard")
    phase = "sprechen"
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        gesprochen = c.sprechen(str(zeile.get("sprechtext") or ""), stimme, ordner)
        phase = "rendern"
        ziel = ordner / name
        dauer = c.rendern(zeile, gesprochen, c.marke(str(werk.get("mandant") or "")), ordner, ziel)
        if not isinstance(dauer, (int, float)) or isinstance(dauer, bool):
            dauer = gesprochen.dauer_s
        phase = "hochladen"
        c.api.hochladen(aid, name, ziel)
        phase = "melden"
        engine = "chatterbox" if gesprochen.rueckfall else str(gesprochen.engine)
        c.api.szene_clip(sid, variante, name, round(float(dauer), 2), stimme, engine)
    except ApiFehler as e:
        if phase == "melden" and e.code == 409 and STALE_CLIP in e.grund:
            # die Szene nimmt keinen Clip mehr an (neue Variante, anderer Stand): der Clip wird nicht mehr gebraucht
            LOG.warning("Clip nicht mehr gebraucht (409)")
            shutil.rmtree(ordner, ignore_errors=True)
            return None
        return _szene_scheitert(c, sid, phase, e)
    except Exception as e:  # noqa: BLE001 - jede Ausnahme gehoert dieser einen Szene (auch HTTPException)
        return _szene_scheitert(c, sid, phase, e)
    shutil.rmtree(ordner, ignore_errors=True)
    return "clip"


def _szene_scheitert(c: _Lauf, sid: str, phase: str, e: Exception) -> str:
    LOG.warning("Szene scheitert beim %s: %s", phase, type(e).__name__)
    satz = _satz_fuer(phase, e)
    try:
        c.api.szene_fehler(sid, satz)
    except Exception as e2:  # noqa: BLE001 - nicht melden zu koennen darf die Szene nicht wieder freigeben
        LOG.warning("Szenenfehler melden scheiterte: %s", type(e2).__name__)
        c.gedaechtnis["fehler_offen"][sid] = satz
    return "fehler"


# --- ein Werk, ein Durchlauf -------------------------------------------------------------------------

def _schritt(werk: dict, schritt, c: _Lauf) -> str | None:
    """Einen Teilschritt fuer sich absichern: ein Fehler hier laesst die naechsten Teilschritte laufen."""
    try:
        return schritt(c, werk)
    except Exception as e:  # noqa: BLE001 - Typname genuegt; Texte koennen Prompts enthalten
        LOG.warning("%s scheiterte: %s", schritt.__name__.strip("_"), type(e).__name__)
        return "fehler"


def _fertig(c: _Lauf, werk: dict) -> str | None:
    if c.fertigstellen is None or not werk.get("alle_fertig"):
        return None
    if int(werk.get("video_fassung") or 0) != 0 and not werk.get("video_veraltet"):
        return None
    # Ein Video, das nicht zusammenging, wird nicht alle 30 s neu versucht: erst wieder, wenn sich an den Clips
    # etwas aendert (Szene neu freigegeben) oder der Arbeiter neu startet (das Gedaechtnis ist weg).
    schluessel = ("video", str(werk["auftrag"]), int(werk.get("video_fassung") or 0), tuple(
        (str(s.get("id")), s.get("variante"), s.get("clip_datei"), s.get("fassung")) for s in werk.get("szenen") or []))
    if schluessel in c.gedaechtnis["aufgegeben"]:
        return None
    try:
        ergebnis = c.fertigstellen(c.api, werk)
    except Exception:
        c.gedaechtnis["aufgegeben"][schluessel] = True
        raise
    if ergebnis == "fehler":
        c.gedaechtnis["aufgegeben"][schluessel] = True
        return "fehler"
    return "video"


def ein_durchlauf(api, fragen=shim_fragen, kontext=video_kontext.holen, sprechen=szene_sprechen, rendern=szene_rendern,
                  fertigstellen=video_fertigstellen, warte=time.sleep, *, marke=video_slides.marke_fuer, ordner=arbeitsordner,
                  brief=_brief, jetzt=time.monotonic, heute=date.today) -> str:
    """Neuen Auftrag uebernehmen, dann jedes Werk der Reihe nach: Gliederung, Szenentexte, hoechstens eine Szene
    erzeugen, fertigstellen. Rueckgabe: das letzte Ergebnis - leer, gliederung, texte, clip, video, wartet, budget
    oder fehler (budget gewinnt, wenn die Tagesgrenze im Durchlauf getroffen wurde)."""
    c = _Lauf(api, fragen, kontext, sprechen, rendern, fertigstellen, warte, marke, ordner, brief, jetzt, heute)
    werke = api.arbeit()
    for werk in werke:       # erst alle Leases, dann naechster: ein abgelaufener Auftrag wuerde sonst neu vergeben
        if (werk.get("gliederung_stand") == "geplant" and not werk.get("gliederung")
                and _gliederung_schluessel(werk) not in c.gedaechtnis["aufgegeben"]):   # in_arbeit = vor der ersten Gliederung
            c.lease(str(werk["auftrag"]))
    try:
        neu = api.naechster()      # ein neuer Auftrag erscheint ab jetzt unter arbeit()
    except Exception as e:  # noqa: BLE001 - die laufenden Werke warten nicht auf einen neuen Auftrag
        neu = None
        LOG.warning("naechster scheiterte: %s", type(e).__name__)
    if neu:
        werke = api.arbeit()
    ergebnis = "wartet" if werke else "leer"
    for werk in werke:
        for schritt in (_gliederung, _texte, _erzeugen, _fertig):
            r = _schritt(werk, schritt, c)
            if r:
                ergebnis = r
    return "budget" if c.budget else ergebnis


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps(STAND, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


def main() -> None:
    umgebung_laden()
    basis, schluessel = os.environ.get("MARKETING_BILD_URL", ""), os.environ.get("MARKETING_BILD_KEY", "")
    if not basis or not schluessel:
        raise SystemExit("MARKETING_BILD_URL/MARKETING_BILD_KEY fehlen in Vibemind_V1/.env")
    api = WerkbankApi(basis, schluessel)
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    while True:
        try:
            STAND["letztes_ergebnis"] = ein_durchlauf(api)
        except Exception as e:  # noqa: BLE001 - nichts darf die Schleife toeten (auch http.client.HTTPException)
            STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}"
        STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(STAND, flush=True)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
