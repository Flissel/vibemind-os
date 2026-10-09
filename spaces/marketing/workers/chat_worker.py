"""Chat-Arbeiter am PC (sales-claw Spec 2026-10-02-newsletter-gestaltung-und-agent).
Holt Chat-Auftraege von der Marketing-API der VM (Tailnet, X-Bild-Key), fragt Claude
ueber den lokalen OpenAI-kompatiblen Shim :8117, prueft und wendet die JSON-Aenderungen
an und meldet zurueck. Gestartet von marketing-dienste-starten.ps1; Gesundheits-Port 8134.
Drei Editor-Plaetze (Spec 2026-10-09-editor-parallele-runden) neben Marken- und Wissens-Faden."""
from __future__ import annotations

import base64
import contextlib
import datetime
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageOps

from spaces.marketing.claw import (agent_prompt, agent_strom, agent_werkzeuge, bildplaetze, denkspur, markenprofil,
                                   markenwissen, pdf_bilder, schoenheit, schriften, unterlagen, vorlagen_marke)
from spaces.marketing.workers.bild_worker import ApiFehler, _grund, tls_kontext, umgebung_laden

PORT = 8134
TAKT_S = 3
SHIM_BIS_S = 180
SHIM_PAUSE_S = 10
LLM_ZEITLIMIT_S = 300
DROSSEL_S = 1.0          # hoechstens ein Zwischenstand je Sekunde (der letzte vor fertig immer)
SCHRITT_MAX = 80
LLM_URL = os.environ.get("MARKETING_CHAT_LLM_URL", "http://127.0.0.1:8117/v1")
MODELL = os.environ.get("MARKETING_CHAT_MODELL", "claude-code-sonnet")
NICHT_ERREICHBAR = "Der Assistent ist gerade nicht erreichbar"
NICHT_UMGESETZT = "Das habe ich nicht umsetzen können: "
NACHSPIELEN_MAX = 3
ZU_VIELE = "Zu viele gleichzeitige Änderungen – bitte noch einmal senden"
NICHTS_UMGESETZT = "Fertig, nichts umgesetzt – eine andere Runde hat den Entwurf inzwischen geändert."
VM_HINWEISE_MAX = 40     # wie api/chat.py HINWEISE_MAX: mehr Hinweise lehnt die VM mit 422 ab
NACHGESPIELT = ("Auf Fassung {n} nachgespielt – eine andere Runde war schneller; "
                "bei widersprüchlichen Bitten gilt diese Runde.")
WEBSUCHE_AUS = "WebSearch:aus"   # wie marketing_shim.WEBSUCHE_AUS
STAND = {"letzter_lauf": None, "letztes_ergebnis": None}
_STAND_SPERRE = threading.Lock()      # STAND schreiben alle Faeden; Lesen/Ausgeben nur als Kopie unter der Sperre
_EXPORT_SPERRE = threading.Lock()     # ein Newsletter-Export laeuft allein (auch gegen einen zweiten Export)


class LlmFehler(Exception):
    pass


class ShimAbgelehnt(LlmFehler):
    """Der Shim hat die Anfrage selbst abgelehnt (HTTP 400, z. B. ungueltige Bildteile)."""


class ChatApi:
    PFAD = "/api/chat/arbeiter"

    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()

    def _anfrage(self, methode: str, pfad: str, daten=None, roh: bytes | None = None, typ="application/json") -> bytes:
        koerper = roh if roh is not None else (json.dumps(daten).encode("utf-8") if daten is not None else None)
        if methode == "POST" and koerper is None:
            koerper = b""
        req = urllib.request.Request(self.basis + self.PFAD + pfad, data=koerper, method=methode,
                                     headers={"Content-Type": typ} if koerper is not None else {})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ApiFehler(e.code, _grund(e)) from None

    def _post(self, pfad: str, daten=None, roh: bytes | None = None, typ="application/json") -> dict:
        return json.loads(self._anfrage("POST", pfad, daten, roh, typ) or b"{}")

    def naechster(self) -> dict | None:
        return self._post("/naechster").get("auftrag")

    def weiter(self, aid) -> bool:
        return bool(self._post(f"/{aid}/weiter").get("ok"))

    def pruefen(self, aid, bloecke) -> str | None:
        return self._post(f"/{aid}/pruefen", {"bloecke": bloecke}).get("fehler") or None

    def fertig(self, aid, daten: dict) -> dict:
        return self._post(f"/{aid}/fertig", daten)

    def zurueck(self, aid, antwort: str) -> str:
        return self._post(f"/{aid}/zurueck", {"antwort": antwort}).get("status", "")

    def medium(self, aid, name) -> bytes | None:
        try:
            return self._anfrage("GET", f"/{aid}/medien/{urllib.parse.quote(name)}")
        except ApiFehler as e:
            if e.code == 404:
                return None
            raise

    def datei(self, aid, name, roh: bytes) -> str:
        return self._post(f"/{aid}/datei?name={urllib.parse.quote(name)}", roh=roh, typ="image/jpeg")["name"]

    def zwischenstand(self, aid, bloecke: dict, schritt: str, nr: int) -> dict:
        return self._post(f"/{aid}/zwischenstand", {"bloecke": bloecke, "schritt": schritt[:SCHRITT_MAX], "nr": nr})

    def neueste(self, aid) -> dict:
        return json.loads(self._anfrage("GET", f"/{aid}/neueste") or b"{}")

    def gestoppt(self, aid, bloecke: dict | None, basis: int | None = None, hinweise=()) -> dict:
        daten: dict = {"bloecke": bloecke}
        if basis is not None:
            daten.update(basis=basis, hinweise=list(hinweise))
        return self._post(f"/{aid}/gestoppt", daten)

    def denken(self, aid, denken: str, schritte: list[dict]) -> dict:
        return self._post(f"/{aid}/denken", {"denken": denken, "schritte": schritte})


def frage(system: str, nachrichten: list[dict], url: str = LLM_URL, modell: str = MODELL) -> str:
    """Eine Anfrage an den OpenAI-kompatiblen Shim (kein tool_calls, nur Text)."""
    koerper = {"model": modell, "marketing_ohne_werkzeuge": True,
               "messages": [{"role": "system", "content": system}, *nachrichten]}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(koerper).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=LLM_ZEITLIMIT_S) as r:
            antwort = json.loads(r.read())
        text = antwort["choices"][0]["message"]["content"]
    except (OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as e:
        # OSError deckt URLError/HTTPError, ValueError deckt JSONDecodeError.
        raise LlmFehler(f"{type(e).__name__}: {e}") from None
    if not isinstance(text, str) or not text.strip():
        raise LlmFehler("Leere Antwort")
    return text


def frage_strom(system: str, nachrichten: list[dict], url: str = LLM_URL, modell: str = MODELL,
                denken: Callable[[str], None] | None = None, websuche: bool = False,
                werkzeug: Callable[[str], None] | None = None) -> Iterator[str]:
    """Wie frage, aber als SSE-Strom des Shims: liefert jedes Text-Stueck (delta.content), sobald es da ist.
    Fehler-Chunk (finish_reason "error"), Abbruch ohne Abschluss, Muell oder eine leere Antwort => LlmFehler.
    Schliesst der Aufrufer den Strom (close), wird die Verbindung zum Shim geschlossen."""
    koerper = {"model": modell, "stream": True, "marketing_stream": True, "marketing_ohne_werkzeuge": True, "messages": [{"role": "system", "content": system}, *nachrichten]}
    if denken is not None:
        koerper["marketing_denken"] = True
    if websuche:
        koerper["marketing_websuche"] = True
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(koerper).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        antwort = urllib.request.urlopen(req, timeout=LLM_ZEITLIMIT_S)
    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise ShimAbgelehnt(_kurz(e)) from None
        raise LlmFehler(_kurz(e)) from None
    except (OSError, TimeoutError, ValueError) as e:
        raise LlmFehler(_kurz(e)) from None
    abgeschlossen, etwas = False, False
    with antwort:
        try:
            for zeile in antwort:
                zeile = zeile.decode("utf-8").strip()
                if not zeile.startswith("data:"):
                    continue
                daten = zeile[5:].strip()
                if daten == "[DONE]":
                    abgeschlossen = True
                    break
                wahl = json.loads(daten)["choices"][0]
                d = wahl.get("delta") or {}
                gedacht = d.get("reasoning_content")
                if denken is not None and isinstance(gedacht, str) and gedacht:
                    denken(gedacht)
                genutzt = d.get("marketing_werkzeug")
                if werkzeug is not None and isinstance(genutzt, str) and genutzt:
                    werkzeug(genutzt)
                inhalt = d.get("content") or ""
                if wahl.get("finish_reason") == "error":
                    raise LlmFehler(("Shim: " + inhalt)[:200] if inhalt else "Shim-Fehler")
                if wahl.get("finish_reason") == "stop":
                    abgeschlossen = True
                if inhalt:
                    etwas = etwas or bool(inhalt.strip())
                    yield inhalt
        except (OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            # OSError deckt Verbindungsabbruch, ValueError deckt JSONDecodeError/UnicodeDecodeError.
            raise LlmFehler(_kurz(e)) from None
    if not abgeschlossen:
        raise LlmFehler("Strom ohne Abschluss abgebrochen")
    if not etwas:
        raise LlmFehler("Leere Antwort")


HALTEN_TAKT_S = 60
FREMD = (404, 409, 422)      # Auftrag gehoert uns nicht mehr


def spur_senden(api, aid):
    """Senden fuer die Denkspur: False = Auftrag gehoert uns nicht mehr; sonst True (auch bei Netzfehler:
    der naechste Takt versucht es erneut, der Auftrag laeuft weiter)."""
    def senden(denken: str, schritte: list[dict]) -> bool:
        try:
            api.denken(aid, denken, schritte)
        except ApiFehler as e:
            return e.code not in FREMD
        except (OSError, ValueError):
            return True
        return True
    return senden


class _Halter:
    def __init__(self):
        self.verloren = threading.Event()


@contextlib.contextmanager
def halten(api, aid, takt_s: float = HALTEN_TAKT_S):
    """Verlaengert die Vergabe alle takt_s Sekunden, solange der Block laeuft (Claude-Antworten
    dauern bis 300 s, die Vergabe nur 5 min). Meldet die Vergabe `verloren`, sobald weiter
    False liefert oder der Auftrag nicht mehr unser ist; Netzstoerungen werden uebersprungen."""
    halter, ende = _Halter(), threading.Event()

    def lauf():
        while not ende.wait(takt_s):
            try:
                if not api.weiter(aid):
                    halter.verloren.set()
                    return
            except ApiFehler as e:
                if e.code in FREMD:
                    halter.verloren.set()
                    return
            except (OSError, ValueError):
                pass
    faden = threading.Thread(target=lauf, daemon=True)
    faden.start()
    try:
        yield halter
    finally:
        ende.set()
        faden.join(timeout=1)


def _kurz(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:150]


def _freigeben(api, aid, text: str, e: BaseException) -> None:
    """Auftrag nach einer Stoerung zurueckgeben - einmal, Fehler dabei werden verschluckt.
    Gehoert der Auftrag uns nicht mehr (404/409/422), passiert nichts."""
    if isinstance(e, ApiFehler) and e.code in FREMD:
        return
    try:
        api.zurueck(aid, text)
    except Exception:  # noqa: BLE001 - die VM gibt den Auftrag nach Ablauf selbst frei
        pass


class _Stopp(Exception):
    def __init__(self, art):
        self.art = art


class _Verloren(Exception):
    pass


# Werkzeuge, deren Flaeche Claude spaeter mit neu:<n> anspricht (agent_werkzeuge._Lauf.neu)
NEU_WERKZEUGE = agent_werkzeuge.NEU_WERKZEUGE
_neu_aufloesen = agent_werkzeuge.neu_aufloesen     # Live-Stand und Nachspielen loesen gleich auf


# zufaellige ids aus agent_werkzeuge (neue_id: agent-<hex6>, Ebenen: e-<hex6>), als JSON-String
_ZUFALLS_ID = re.compile(r'"((?:agent|e)-[0-9a-f]{6})"')


def _id_zuordnung(original: dict, gesamt: dict, live: dict) -> dict | None:
    """Zuordnung der neuen zufaelligen ids von gesamt auf live (nach erstem Auftreten; beide Staende entstehen
    aus derselben Aenderungsfolge, also in derselben Reihenfolge). None, wenn die Staende mit dieser Zuordnung
    nicht exakt gleich sind."""
    alt = set(_ZUFALLS_ID.findall(json.dumps(original, ensure_ascii=False)))
    tg, tl = json.dumps(gesamt, ensure_ascii=False), json.dumps(live, ensure_ascii=False)
    ng = [i for i in dict.fromkeys(_ZUFALLS_ID.findall(tg)) if i not in alt]
    nl = [i for i in dict.fromkeys(_ZUFALLS_ID.findall(tl)) if i not in alt]
    if len(ng) != len(nl):
        return None
    zu = dict(zip(ng, nl))
    if _ZUFALLS_ID.sub(lambda m: '"' + zu.get(m.group(1), m.group(1)) + '"', tg) != tl:
        return None
    return zu


class _Live:
    """Live-Stand eines Laufs: jede fertig gelesene Aenderung wird einzeln auf die Arbeitskopie angewandt
    (stabile Block-ids, neu:<n> ueber die Live-ids aufgeloest); eine ungueltige wird gemerkt und laesst den
    Stand unveraendert - entscheidend bleibt die Gesamtpruefung am Ende. Meldet gedrosselt als Zwischenstand;
    die Antwort darauf kann _Stopp oder _Verloren ausloesen."""

    def __init__(self, api, aid, original: dict, medien: set, uhr, drossel_s: float,
                 spur: denkspur.Spur | None = None, basis: int | None = None):
        self.api, self.aid, self.original, self.medien = api, aid, original, medien
        self.spur = spur
        self.basis = basis             # die Fassung, auf der der Live-Stand aufbaut (fassung_vorher)
        self.uhr, self.drossel_s = uhr, drossel_s
        self.gesendet_am = None
        self.neu_beginnen()

    def neu_beginnen(self) -> None:
        """Jeder Strom (Wiederholung, Korrekturversuch) beginnt beim Original. Der zuletzt gemeldete
        Zwischenstand bleibt bei der VM stehen, bis der neue Strom etwas Gueltiges meldet."""
        self.kopie, self.gueltig, self.neu_ids, self.fehler = self.original, None, [], []
        self.angewandt: list[dict] = []
        self.nr, self.schritt, self.offen = 0, "", False

    def zuordnung(self, aenderungen: list, leser_fehler: list, gesamt: dict) -> dict | None:
        """R11: Der Live-Stand gilt als Ergebnis, wenn der Strom sauber lief (keine ungueltige Einzelaenderung,
        nichts Unlesbares), genau die Gesamtliste live angewandt wurde und beide Staende bis auf die zufaelligen
        ids gleich sind. Liefert dann die Zuordnung Gesamt-id -> Live-id, sonst None (Rueckfall Gesamtliste)."""
        if self.fehler or leser_fehler or self.angewandt != aenderungen:
            return None
        return _id_zuordnung(self.original, gesamt, self.kopie)

    def aenderung(self, a: dict) -> None:
        werkzeug = a.get("werkzeug")
        vorher = set(self.kopie) if isinstance(self.kopie, dict) else set()
        try:
            erg = agent_werkzeuge.anwenden(self.kopie, [_neu_aufloesen(a, self.neu_ids)], self.medien)
        except agent_werkzeuge.WerkzeugFehler as e:
            self.fehler.append(str(e))
            if werkzeug in NEU_WERKZEUGE:
                self.neu_ids.append(None)        # Nummerierung von neu:<n> bleibt wie in der Gesamtliste
            return
        if werkzeug in NEU_WERKZEUGE:
            neue = [k for k in erg.bloecke if k not in vorher]
            self.neu_ids.append(neue[0] if len(neue) == 1 else None)
        self.kopie = self.gueltig = erg.bloecke
        self.angewandt.append(a)
        self.nr += 1
        self.schritt = str(a.get("schritt") or werkzeug)[:SCHRITT_MAX]
        self.offen = True
        if self.spur is not None:
            self.spur.schritt(self.schritt)

    def melden(self, immer: bool = False) -> None:
        if not (self.offen or immer):
            return
        jetzt = self.uhr()
        if not immer and self.gesendet_am is not None and jetzt - self.gesendet_am < self.drossel_s:
            return
        self.gesendet_am, self.offen = jetzt, False
        try:
            r = self.api.zwischenstand(self.aid, self.kopie, self.schritt, self.nr)
        except ApiFehler as e:
            if e.code == 404:
                raise _Verloren from None
            return                   # z.B. 413 zu gross: nur die Anzeige faellt aus, der Lauf geht weiter
        except (OSError, ValueError):
            return
        if not r.get("weiter"):
            if r.get("grund") == "stopp":
                raise _Stopp(r.get("stopp"))
            raise _Verloren

    def endstand(self, bloecke: dict, aenderungen: list) -> None:
        """Letzter Zwischenstand vor fertig: der gepruefte Gesamtstand, ungedrosselt."""
        letzte = aenderungen[-1] if aenderungen and isinstance(aenderungen[-1], dict) else {}
        self.kopie = self.gueltig = bloecke
        self.nr = len(aenderungen)
        self.schritt = str(letzte.get("schritt") or letzte.get("werkzeug") or "")[:SCHRITT_MAX]
        self.melden(immer=True)


def _strom_lesen(fragen_strom, nachrichten, live: _Live, halter,
                 system: str = agent_prompt.SYSTEM, spur: denkspur.Spur | None = None) -> tuple[str, list[str]]:
    """Liest einen Strom ganz; jede neue vollstaendige Aenderung geht sofort in den Live-Stand.
    Liefert den ganzen Text und die Lesefehler des StromLesers (unlesbare Aenderungen)."""
    live.neu_beginnen()
    leser = agent_strom.StromLeser()
    if spur is not None:
        spur.schritt("Frage an Claude")
    strom = iter(fragen_strom(system, nachrichten, denken=spur.denken if spur is not None else None))
    try:
        for stueck in strom:
            if halter.verloren.is_set():
                raise _Verloren
            for a in leser.futter(stueck):
                live.aenderung(a)
            live.melden()
    finally:
        schliessen = getattr(strom, "close", None)
        if schliessen:
            schliessen()             # bei Stopp/Verlust: Verbindung zum Shim schliessen
    if not leser.text.strip():
        raise LlmFehler("Leere Antwort")
    return leser.text, list(leser.fehler)


# --- Auswahl, Bilder und Unterlagen der Nachricht (sales-claw Spec 2026-10-06-chat-kontext-und-uploads) ---

MAX_BILDER = 6                     # je Anfrage (Grenze des Shims); Anhaenge vor Bildern aus der Auswahl
MAX_KANTE = 1568                   # laengste Kante, mit der Claude ein Bild ohne eigenes Verkleinern sieht
MAX_BILD_PIXEL = 50_000_000        # mehr Pixel wird nie dekodiert (Dekompressionsbombe), sondern uebersprungen
MAX_BILD_BYTES = 10 * 1024 * 1024  # je Bild kodiert (Grenze des Shims)
MAX_AUSWAHL = 8
BILD_FORMATE = ("PNG", "JPEG", "WEBP", "GIF", "BMP")   # nie ueber die Endung: der Inhalt ist fremd
MAX_ANHAENGE = 5
MAX_HINWEIS = 200
MAX_HINWEISE = 1500                # zusammen vor der Antwort
VM_ANTWORT_MAX = 4000              # die VM kuerzt die ganze Antwort (Hinweise + Text) hier
BILDER_ABGELEHNT = "Bilder konnten nicht übergeben werden, ich habe ohne sie geantwortet."


def _props(b) -> dict:
    daten = b.get("data") if isinstance(b, dict) else None
    props = daten.get("props") if isinstance(daten, dict) else None
    return props if isinstance(props, dict) else {}


def _medienname(url) -> str | None:
    """Name aus "medien:<name>"; leere Plaetze und Platzhalterbilder zaehlen nicht."""
    if isinstance(url, str) and url.startswith("medien:") and not bildplaetze.ist_leer(url):
        return url[len("medien:"):] or None
    return None


def _ebene_finden(bloecke: dict, fid, eid) -> dict | None:
    gestaltung = _props(bloecke.get(fid)).get("gestaltung") if isinstance(fid, str) else None
    ebenen = gestaltung.get("ebenen") if isinstance(gestaltung, dict) else None
    for e in ebenen if isinstance(ebenen, list) else []:
        if isinstance(e, dict) and e.get("id") == eid:
            return e
    return None


def _auswahl_liste(kontext: dict) -> list[dict]:
    """Nur ausdrueckliche Chips [{art, id, flaeche?, kurz}] zaehlen. Die Altform (eine einzelne id oder ein String)
    schickt der Editor mit dem gerade selektierten Block bei jeder Nachricht mit: gueltig, aber keine Markierung."""
    auswahl = kontext.get("auswahl")
    if isinstance(auswahl, list):
        return [a for a in auswahl if isinstance(a, dict) and isinstance(a.get("id"), str)][:MAX_AUSWAHL]
    return []


def _auswahl_aufloesen(auftrag: dict, kontext: dict) -> tuple[list[dict], list[str], list[str]]:
    """(markierte Eintraege mit vollstaendigem Block bzw. vollstaendiger Ebene, Mediennamen der markierten
    Bilder, Hinweise zu markierten Elementen, die es nicht mehr gibt)."""
    bloecke = auftrag.get("bloecke") if isinstance(auftrag.get("bloecke"), dict) else {}
    fenster = str(kontext.get("fenster") or "")
    markiert, bilder, hinweise = [], [], []
    for a in _auswahl_liste(kontext):
        if a.get("art") == "ebene":
            fid = a.get("flaeche") or (fenster[len("flaeche:"):] if fenster.startswith("flaeche:") else None)
            gefunden = _ebene_finden(bloecke, fid, a["id"])
        else:
            gefunden = bloecke.get(a["id"])
        if not isinstance(gefunden, dict):
            hinweise.append(f"Das markierte Element „{str(a.get('kurz') or a['id'])[:80]}“ gibt es nicht mehr.")
        elif a.get("art") == "ebene":
            markiert.append({**a, "flaeche": fid, "ebene": gefunden})
            if gefunden.get("art") == "bild":
                bilder.append(_medienname(gefunden.get("quelle")))
        else:
            markiert.append({**a, "block": gefunden})
            if gefunden.get("type") == "Image":            # Bildblock oder Flaeche (deren gerechnetes gs-Bild)
                bilder.append(_medienname(_props(gefunden).get("url")))
    return markiert, [n for n in bilder if n], hinweise


def _holen(api, aid, name: str, hinweise: list[str]) -> bytes | None:
    try:
        roh = api.medium(aid, name)
    except (ApiFehler, OSError, ValueError):
        hinweise.append(f"{name} konnte nicht geladen werden.")
        return None
    if not roh:
        hinweise.append(f"{name} fehlt in den Medien.")
        return None
    return roh


def _bild_als_teil(roh: bytes) -> dict | None:
    """Bildteil fuer den Shim: auf MAX_KANTE verkleinert, JPEG (mit Transparenz PNG) als data-URL.
    None, wenn das Bild unlesbar ist oder mehr als MAX_BILD_PIXEL hat - die Groesse steht im Kopf und wird
    vor jedem Dekodieren geprueft. Wirft nie."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(roh), formats=BILD_FORMATE) as quelle:
                breite, hoehe = quelle.size
                if breite <= 0 or hoehe <= 0 or breite * hoehe > MAX_BILD_PIXEL:
                    return None
                quelle.draft(None, (MAX_KANTE, MAX_KANTE))    # JPEG: schon beim Dekodieren verkleinern
                bild = ImageOps.exif_transpose(quelle)
                bild.thumbnail((MAX_KANTE, MAX_KANTE))
                if bild.mode in ("RGBA", "LA", "PA") or (bild.mode == "P" and "transparency" in bild.info):
                    bild = bild.convert("RGBA")
                    puffer, art = io.BytesIO(), "png"
                    bild.save(puffer, "PNG", optimize=True)
                    if puffer.tell() <= MAX_BILD_BYTES:
                        return _bildteil(art, puffer)
                    flach = Image.new("RGB", bild.size, (255, 255, 255))   # zu gross als PNG: auf Weiss
                    flach.paste(bild, mask=bild.getchannel("A"))
                    bild = flach
                puffer, art = io.BytesIO(), "jpeg"
                bild.convert("RGB").save(puffer, "JPEG", quality=85)
    except Exception:  # noqa: BLE001 - jedes unlesbare Bild (kaputt, Bombe, fremdes Format) wird uebersprungen
        return None
    return _bildteil(art, puffer) if puffer.tell() <= MAX_BILD_BYTES else None


def _bildteil(art: str, puffer: io.BytesIO) -> dict:
    url = f"data:image/{art};base64," + base64.b64encode(puffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": url}}


def anhaenge_vorbereiten(api, aid, auftrag: dict, bilder: list[tuple[str, str]] | None = None,
                         max_bilder: int = MAX_BILDER) -> tuple[list[dict], str, str, list[str]]:
    """(bildteile, unterlagen, auswahl_text, hinweise) aus kontext.anhaenge und kontext.auswahl: Bilder
    (Anhaenge vor markierten, hoechstens max_bilder) als Bildteile, Dokumente als Unterlagen-Text, die
    Auswahl als JSON. Was fehlt oder unlesbar ist, wird ein Hinweis; die Funktion wirft nicht.
    bilder (optional, Ausgabe): (Mediennamen, Herkunft) je mitgeschicktem Bildteil, in Bildteil-Reihenfolge.
    max_bilder < MAX_BILDER haelt Plaetze frei (Marken-Chat: Logo-Ansichten)."""
    frei = " (Plätze für Logo-Ansichten freigehalten)" if max_bilder < MAX_BILDER else ""
    kontext = auftrag.get("kontext") if isinstance(auftrag.get("kontext"), dict) else {}
    markiert, chip_bilder, hinweise = _auswahl_aufloesen(auftrag, kontext)
    anhaenge = kontext.get("anhaenge") if isinstance(kontext.get("anhaenge"), list) else []
    anhaenge = [a for a in anhaenge if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]]
    anhaenge = anhaenge[:MAX_ANHAENGE]
    anhang_namen = [a["name"] for a in anhaenge if a.get("art") == "bild"]
    namen = list(dict.fromkeys(anhang_namen + chip_bilder))
    bildteile: list[dict] = []
    for i, name in enumerate(namen):
        if len(bildteile) >= max_bilder:
            hinweise.append(f"Höchstens {max_bilder} Bilder je Nachricht{frei}, nicht mitgeschickt: "
                            f"{', '.join(namen[i:])}.")
            break
        roh = _holen(api, aid, name, hinweise)
        if roh is None:
            continue
        teil = _bild_als_teil(roh)
        if teil is None:
            hinweise.append(f"{name} ist kein lesbares Bild oder zu groß und wurde übersprungen.")
            continue
        bildteile.append(teil)
        if bilder is not None:
            bilder.append((name, "Anhang" if name in anhang_namen else "markiert"))
    dateien = []
    pdf_seiten: list[tuple[str, bytes]] = []
    for a in anhaenge:
        if a.get("art") == "dokument":
            roh = _holen(api, aid, a["name"], hinweise)
            if roh is None:
                continue
            dateien.append((a["name"], roh))
            if a["name"].lower().endswith(".pdf"):
                try:
                    pdf_seiten += [(pdf_bilder.seiten_name(a["name"], i), png)
                                   for i, png in enumerate(pdf_bilder.seiten(roh), 1)]
                except pdf_bilder.PdfBildFehler as e:
                    hinweise.append(f"{a['name']}: Seiten nicht als Bild darstellbar ({e})")
    for i, (name, png) in enumerate(pdf_seiten):
        if len(bildteile) >= max_bilder:
            hinweise.append(f"Höchstens {max_bilder} Bilder je Nachricht{frei}, PDF-Seiten nicht mitgeschickt: "
                            f"{', '.join(n for n, _ in pdf_seiten[i:])}.")
            break
        teil = _bild_als_teil(png)
        if teil is None:
            hinweise.append(f"{name} ließ sich nicht als Bild übergeben.")
            continue
        bildteile.append(teil)
        if bilder is not None:
            bilder.append((name, f"{pdf_bilder.HERKUNFT} {name.rsplit('#', 1)[1]}"))
    text, unlesbar = unterlagen.unterlagen_text(dateien) if dateien else ("", [])
    hinweise += unlesbar
    auswahl_text = json.dumps(markiert, ensure_ascii=False, separators=(",", ":")) if markiert else ""
    return bildteile, text, auswahl_text, hinweise


def _mit_hinweisen(hinweise: list[str], antwort: str) -> str:
    if not hinweise:
        return antwort
    # Der Text (mit den "Notiz in Rowboat abgelegt"-Zeilen am Ende) muss ganz in die
    # VM-Grenze passen: die Hinweise bekommen nur, was neben ihm frei bleibt.
    grenze = min(MAX_HINWEISE, VM_ANTWORT_MAX - len(antwort) - len("\n\n"))
    zeilen, laenge = [], 0
    for h in hinweise:
        zeile = "Hinweis: " + h[:MAX_HINWEIS]
        laenge += len(zeile) + (1 if zeilen else 0)
        if laenge > grenze:
            break                      # nur ganze Zeilen, nie mitten in einer abschneiden
        zeilen.append(zeile)
    if not zeilen:
        return antwort
    return "\n".join(zeilen) + "\n\n" + antwort


class _Neuer(Exception):
    """Die Schoenheitspruefung schlug nach dem Nachspielen an: Korrekturrunde von der neuesten Fassung."""
    def __init__(self, grund: str, neu: dict):
        super().__init__(grund)
        self.grund, self.neu = grund, neu


def _neueste_lesen(api, aid) -> dict:
    neu = api.neueste(aid)
    if (not isinstance(neu, dict) or not isinstance(neu.get("bloecke"), dict)
            or isinstance(neu.get("fassung"), bool) or not isinstance(neu.get("fassung"), int)):
        raise ValueError("neueste Fassung unlesbar")
    return neu


def _abschluss_schritte(spur: denkspur.Spur, daten: dict) -> list[dict]:
    """Die Schritte "Bilder beauftragt"/"Fassung gespeichert" fuer genau diesen Speicherversuch; -> die Eintraege,
    damit ein verlorenes Rennen sie wieder zuruecknimmt (es wurde nichts gespeichert)."""
    eintraege = []
    if daten.get("bildauftraege"):
        spur.schritt(f"Bilder beauftragt: {len(daten['bildauftraege'])}")
        eintraege.append(spur.schritte[-1])
    if daten.get("bloecke") is not None:
        spur.schritt("Fassung gespeichert")
        eintraege.append(spur.schritte[-1])
    return eintraege


def _schritte_zuruecknehmen(spur: denkspur.Spur, eintraege: list[dict]) -> None:
    spur.schritte[:] = [s for s in spur.schritte if not any(s is e for e in eintraege)]


def _speichern(api, aid, daten: dict, aenderungen: list, medien: set, basis, spur: denkspur.Spur) -> dict:
    """fertig mit Nachspielen (Spec 2026-10-09 §1): verliert das Speichern das Rennen, die Aenderungsliste auf die
    neueste Fassung nachspielen, pruefen und erneut speichern - hoechstens NACHSPIELEN_MAX-mal. -> Antwort der VM,
    {"status": "zu_viele"} nach dem letzten verlorenen Rennen. Wirft _Neuer, wenn die Schoenheitspruefung nach dem
    Nachspielen anschlaegt. Die Abschluss-Schritte stehen nur fuer den Versuch in der Spur, der gespeichert hat;
    die Denkspur geht vor jedem fertig raus."""
    hinweise: list[str] = []
    for runde in range(NACHSPIELEN_MAX + 1):
        schritte = _abschluss_schritte(spur, daten)
        spur.ende()
        antwort = api.fertig(aid, {**daten, "basis": basis, "hinweise": hinweise})
        if antwort.get("status") != "veraltet":
            return antwort
        _schritte_zuruecknehmen(spur, schritte)
        if runde == NACHSPIELEN_MAX:
            break
        if not api.weiter(aid):
            return {"status": "fehler"}
        neu = _neueste_lesen(api, aid)
        spur.schritt(f"Nachspielen auf Fassung {neu['fassung']}")
        ns = agent_werkzeuge.nachspielen(neu["bloecke"], aenderungen, medien)
        basis = neu["fassung"]
        hinweise = [NACHGESPIELT.format(n=neu["fassung"]), *ns.uebersprungen][:VM_HINWEISE_MAX]
        if not ns.geaendert:      # keine Aenderung passt mehr: fertig ohne Fassung (dann gibt es kein Rennen)
            daten = {**daten, "antwort": NICHTS_UMGESETZT, "bloecke": None, "bildauftraege": [],
                     "export_vorschlag": None}
            continue
        grund = api.pruefen(aid, ns.bloecke)
        if grund:
            spur.schritt(f"Schönheitsprüfung: {grund}")
            raise _Neuer(grund, neu)
        daten = {**daten, "bloecke": ns.bloecke, "bildauftraege": ns.bildauftraege,
                 "export_vorschlag": ns.export_vorschlag, "notiz": ns.notiz or daten.get("notiz", "")}
    return {"status": "zu_viele"}


def chat_bearbeiten(api, auftrag, fragen_strom=frage_strom, uhr=time.monotonic, schlafen=time.sleep,
                    halten_takt_s: float = HALTEN_TAKT_S, drossel_s: float = DROSSEL_S) -> str:
    aid = str(auftrag["id"])
    versucht = []            # zurueck wurde schon aufgerufen

    def zurueckgeben(text: str) -> str:
        versucht.append(1)
        spur.ende()
        api.zurueck(aid, text)
        return "fehler"
    spur = denkspur.Spur(spur_senden(api, aid), uhr=uhr)
    live = _Live(api, aid, auftrag.get("bloecke") or {}, set(auftrag.get("medien") or []), uhr, drossel_s, spur=spur,
                 basis=auftrag.get("fassung"))
    try:
        return _bearbeiten(api, auftrag, aid, fragen_strom, uhr, schlafen, halten_takt_s, zurueckgeben, live, spur)
    except _Stopp as s:
        spur.ende()
        bloecke, basis, extra = (live.gueltig if s.art == "behalten" else None), None, []
        if s.art == "behalten" and live.angewandt and live.basis is not None:
            try:   # "Behalten" nach demselben Verfahren: auf eine inzwischen neuere Fassung nachspielen
                neu = _neueste_lesen(api, aid)
                if neu["fassung"] != live.basis:
                    ns = agent_werkzeuge.nachspielen(neu["bloecke"], live.angewandt, live.medien)
                    if ns.geaendert:
                        bloecke = ns.bloecke
                        basis, extra = neu["fassung"], [NACHGESPIELT.format(n=neu["fassung"]),
                                                        *ns.uebersprungen][:VM_HINWEISE_MAX]
                    else:
                        # Nichts passt mehr: ohne basis, damit die VM nicht den alten Zwischenstand auf die neue
                        # Fassung legt (bloecke None = "letzter Zwischenstand"); sie verwirft dann wie bisher.
                        bloecke = None
            except (ApiFehler, OSError, ValueError, agent_werkzeuge.WerkzeugFehler):
                pass               # wie bisher: der letzte gueltige Stand, die VM entscheidet
            if bloecke is not None and basis is None:
                # Der Live-Stand baut auf live.basis auf - nach einer Korrekturrunde von der neuesten Fassung ist
                # das nicht mehr fassung_vorher der VM. Ohne basis verlöre "Behalten" dort immer das Rennen.
                basis = live.basis
        try:
            if basis is None:
                api.gestoppt(aid, bloecke)
            else:
                api.gestoppt(aid, bloecke, basis, extra)
        except (ApiFehler, OSError, ValueError):
            pass             # die VM schliesst einen gestoppten Auftrag nach 15 s selbst ab
        return "gestoppt"
    except _Verloren:
        return "fehler"
    except (ApiFehler, OSError, ValueError) as e:
        if not versucht:
            try:
                spur.ende()          # das Gesammelte vor der Abschlussmeldung ein letztes Mal senden
            except Exception:  # noqa: BLE001 - Sichtbarkeit darf nie den Fehlerweg stoeren
                pass
            _freigeben(api, aid, NICHT_ERREICHBAR, e)
        return "fehler"


KONTRAST_MAX = 20


def kontrastprobleme(bloecke) -> list[str]:
    """Kontrastfunde des aktuellen Entwurfs (claw/schoenheit), lokal vor jeder Editor-Runde. Wirft nie."""
    try:
        funde = schoenheit.bloecke_pruefen(bloecke if isinstance(bloecke, dict) else {})
    except Exception:  # noqa: BLE001 - Kontext darf eine Runde nie kippen
        return []
    return [str(b["satz"]) for b in funde
            if b.get("punkt") == "kontrast" and ": " in str(b.get("satz"))][:KONTRAST_MAX]


def _bearbeiten(api, auftrag, aid, fragen_strom, uhr, schlafen, halten_takt_s, zurueckgeben, live: _Live,
                spur: denkspur.Spur) -> str:
    medien = list(auftrag.get("medien") or [])
    bilder: list[tuple[str, str]] = []
    with halten(api, aid, halten_takt_s) as halter:      # Anhaenge laden kann dauern
        bildteile, unterlagen_text, auswahl_text, hinweise = anhaenge_vorbereiten(api, aid, auftrag, bilder)
    if halter.verloren.is_set():
        return "fehler"
    # Markenwissen der Firma des Auftrags (nur deren Ordner); ohne Mandant (alter Auftrag) entfaellt es.
    mandant = str(auftrag.get("mandant") or "")
    name = str(auftrag.get("mandant_name") or mandant)
    wissen = markenwissen.Wissen(text="", hinweise=[], ordner=None)
    markenfarben: dict = {}
    markenschriften: dict | None = None
    markenlesbar: dict | None = None
    if name:
        wissen = markenwissen.laden(markenwissen.wurzel(), mandant, name,
                                    f"{auftrag.get('nachricht', '')} {auftrag.get('titel', '')}")
        hinweise += wissen.hinweise
        # Gueltige Farben der Marke.md (Marke per Chat): dann gilt die Farbregel der Marke.
        werte = markenprofil.lesen(markenwissen.wurzel(), mandant, name).werte
        markenfarben = {k: werte[k] for k in agent_prompt.MARKENFARBEN if werte.get(k)}
        if werte.get("schrift_anzeige") in schriften.REGISTER and werte.get("schrift_text") in schriften.REGISTER:
            markenschriften = {"anzeige": werte["schrift_anzeige"], "text": werte["schrift_text"]}
        if werte.get("akzent"):   # lesbare Ableitungen (wie die Vorlagen): Text auf hellem Grund, Schrift auf Akzent
            r = vorlagen_marke.rollen({"akzent": werte["akzent"], "flaeche": werte.get("zweitfarbe")},
                                      werte.get("grund") or "#ffffff")
            markenlesbar = {"akzent_text": r["akzent_text"], "auf_akzent": r["auf_akzent"]}
    system = agent_prompt.system(marke=bool(markenfarben))
    if auftrag.get("medien_hinweis"):
        hinweise.insert(0, str(auftrag["medien_hinweis"]))
    angehaengt = [n for n, h in bilder if not pdf_bilder.ist_seite(h)]
    # Markenlogo (von der VM als Mediendatei der Firma abgelegt, I5): fuer "… und Logo" setzbar
    roh_logo, roh_dunkel = auftrag.get("markenlogo"), auftrag.get("markenlogo_dunkel")
    markenlogo = roh_logo[len("medien:"):] if isinstance(roh_logo, str) and roh_logo.startswith("medien:") else ""
    markenlogo_dunkel = (roh_dunkel[len("medien:"):] if markenlogo and isinstance(roh_dunkel, str)
                         and roh_dunkel.startswith("medien:") else "")
    marke_medien = [n for n in (markenlogo, markenlogo_dunkel) if n and n not in angehaengt]
    vorne = angehaengt + marke_medien
    medien = vorne + [m for m in medien if m not in vorne]   # Anhaenge und Markenlogo zuerst, auch live erlaubt
    live.medien.update(vorne)
    text_nutzer = agent_prompt.nutzer_text(auftrag, medien, unterlagen=unterlagen_text, auswahl_text=auswahl_text,
                                           hinweise=hinweise, bilder=bilder, markenwissen=wissen.text,
                                           mandant_name=name, notizen_text=wissen.notizen,
                                           markenfarben=markenfarben, markenlogo=markenlogo,
                                           markenlogo_dunkel=markenlogo_dunkel,
                                           markenschriften=markenschriften, markenlesbar=markenlesbar,
                                           kontrastprobleme=kontrastprobleme(auftrag.get("bloecke")),
                                           feedback=[f for f in (auftrag.get("rueckmeldungen_offen") or [])
                                                     if isinstance(f, dict) and isinstance(f.get("text"), str)])
    # Mit Bildern ist die erste Nachricht eine Teil-Liste; sie bleibt auch in der Korrekturrunde so.
    nachrichten = [{"role": "user", "content": [{"type": "text", "text": text_nutzer}, *bildteile]
                    if bildteile else text_nutzer}]
    mit_bildern = bool(bildteile)
    fehler = ""
    notizen_geschrieben: tuple[list, list] | None = None     # (titel, hinweise) - Notizen nur einmal je Auftrag
    for versuch in (1, 2):
        beginn = None        # Shim-Fenster je Frage, ab dem ersten Ausfall (ein langer Strom zaehlt nicht mit)
        with halten(api, aid, halten_takt_s) as halter:
            while True:
                try:
                    text, leser_fehler = _strom_lesen(fragen_strom, nachrichten, live, halter, system, spur)
                    break
                except LlmFehler as e:    # Shim nicht erreichbar oder Strom mittendrin abgebrochen
                    if halter.verloren.is_set():
                        return "fehler"
                    if isinstance(e, ShimAbgelehnt) and mit_bildern:   # einmal sofort ohne Bilder
                        mit_bildern = False
                        nachrichten[0] = {"role": "user", "content": text_nutzer}
                        hinweise.insert(0, BILDER_ABGELEHNT)
                        continue
                    if beginn is None:
                        beginn = uhr()
                    if uhr() - beginn >= SHIM_BIS_S:
                        return zurueckgeben(NICHT_ERREICHBAR)
                    if not api.weiter(aid):
                        return "fehler"      # Vergabe verloren: die VM hat den Auftrag schon verworfen
                    schlafen(SHIM_PAUSE_S)
        if halter.verloren.is_set():
            return "fehler"
        try:
            antwort = agent_prompt.antwort_lesen(text)
            ergebnis = agent_werkzeuge.anwenden(auftrag.get("bloecke") or {}, antwort["aenderungen"], set(medien))
            bloecke, bildauftraege, export = ergebnis.bloecke, ergebnis.bildauftraege, ergebnis.export_vorschlag
            if ergebnis.geaendert:
                zu = live.zuordnung(antwort["aenderungen"], leser_fehler, ergebnis.bloecke)
                if zu is not None:   # R11: der Live-Stand wird die Fassung, ids bleiben die aus der Anzeige
                    bloecke = live.kopie
                    bildauftraege = [{**b, "platz": zu.get(b.get("platz"), b.get("platz"))} for b in bildauftraege]
                    if export is not None:
                        export = {**export, "flaechen": [zu.get(f, f) for f in export["flaechen"]]}
                if not api.weiter(aid):
                    return "fehler"
                grund = api.pruefen(aid, bloecke)
                if grund:
                    spur.schritt(f"Schönheitsprüfung: {grund}")
                    raise agent_werkzeuge.WerkzeugFehler(grund)
        except (agent_prompt.AntwortFehler, agent_werkzeuge.WerkzeugFehler) as e:
            fehler = str(e)
            if versuch == 1:
                spur.korrektur()
                nachrichten += [{"role": "assistant", "content": text},
                                {"role": "user", "content": agent_prompt.korrektur_text(fehler)}]
                continue
            live.melden(immer=True)  # steht ein Stopp an, gewinnt er vor dem Zurueckgeben
            return zurueckgeben(NICHT_UMGESETZT + fehler)
        if ergebnis.geaendert or live.gesendet_am is not None:
            live.endstand(bloecke, antwort["aenderungen"])     # ein Stopp hier gewinnt noch vor fertig
        if not api.weiter(aid):
            return "fehler"
        text_antwort = antwort["antwort"]
        # Ruling R2: erst jetzt, unmittelbar vor fertig - Stopp/Fehler kommen nie hierher.
        # Alter Auftrag ohne Firma: keine Notiz und kein Hinweis (es gibt keinen Ordner zu nennen).
        # Je Auftrag hoechstens einmal: eine Korrekturrunde nach dem Nachspielen (_Neuer) kommt erneut hierher,
        # Rowboat legte gleichnamige Notizen doppelt an. Die Hinweise kommen in eine Kopie, nie zweimal.
        if antwort["notizen"] and name and notizen_geschrieben is None:
            notizen_geschrieben = markenwissen.notizen_schreiben(
                wissen.ordner, name, antwort["notizen"],
                {"newsletter": auftrag.get("titel", ""), "bitte": auftrag.get("nachricht", "")},
                datetime.date.today())
        antwort_hinweise = list(hinweise)
        if notizen_geschrieben is not None:
            titel, notiz_hinweise = notizen_geschrieben
            # Vor die Markenwissen-Hinweise (uebersprungen/gekuerzt): die kappt die Hinweisgrenze zuerst.
            vor = next((i for i, h in enumerate(antwort_hinweise) if h in wissen.hinweise), len(antwort_hinweise))
            antwort_hinweise[vor:vor] = notiz_hinweise
            if titel:
                text_antwort += "\n\n" + "\n".join(f"Notiz in Rowboat abgelegt: {t}" for t in titel)
        # "Bilder beauftragt"/"Fassung gespeichert" setzt _speichern je Speicherversuch (verlorenes Rennen: zurueck)
        daten = {"antwort": _mit_hinweisen(antwort_hinweise, text_antwort),
                 "bloecke": bloecke if ergebnis.geaendert else None,
                 "bildauftraege": bildauftraege, "export_vorschlag": export, "notiz": ergebnis.notiz}
        try:
            antwort_vm = _speichern(api, aid, daten, antwort["aenderungen"], set(medien), live.basis, spur)
        except _Neuer as n:
            fehler = n.grund
            if versuch == 1:       # Korrekturrunde von der neuesten Fassung
                auftrag = {**auftrag, "bloecke": n.neu["bloecke"], "fassung": n.neu["fassung"]}
                live.original, live.basis = n.neu["bloecke"], n.neu["fassung"]
                spur.korrektur()
                nachrichten += [{"role": "assistant", "content": text},
                                {"role": "user", "content": agent_prompt.korrektur_text(fehler, n.neu["bloecke"])}]
                continue
            return zurueckgeben(NICHT_UMGESETZT + fehler)
        if antwort_vm.get("status") == "zu_viele":
            return zurueckgeben(ZU_VIELE)
        return "fehler" if antwort_vm.get("status") == "fehler" else "fertig"
    return zurueckgeben(NICHT_UMGESETZT + fehler)


def ein_durchlauf(api, fragen_strom=frage_strom, exportieren=None, uhr=time.monotonic, schlafen=time.sleep,
                  halten_takt_s: float = HALTEN_TAKT_S) -> str:
    auftrag = api.naechster()
    if not auftrag:
        return "leer"
    if auftrag.get("art") == "export":
        try:
            if exportieren is None:
                from spaces.marketing.workers.export_worker import exportieren   # Task 12; braucht Playwright
            with _EXPORT_SPERRE:
                return exportieren(api, auftrag)
        except Exception as e:  # noqa: BLE001 - Auftrag gehoert uns, also zurueckgeben
            _freigeben(api, str(auftrag["id"]), "Export nicht möglich: " + _kurz(e), e)
            return "fehler"
    return chat_bearbeiten(api, auftrag, fragen_strom, uhr, schlafen, halten_takt_s)


def _stand_json() -> str:
    """STAND als JSON aus einer Kopie: Editor- und Marken-Faden schreiben ihn gleichzeitig."""
    with _STAND_SPERRE:
        kopie = dict(STAND)
    return json.dumps(kopie, default=str)


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = _stand_json().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


def schleifenschritt(api, ein=ein_durchlauf, feld: str = "letztes_ergebnis") -> None:
    try:
        ergebnis = ein(api)
    except Exception as e:  # noqa: BLE001 - ein Fehler darf die Schleife nicht toeten
        ergebnis = f"fehler: {type(e).__name__}: {e}"[:200]
    with _STAND_SPERRE:
        STAND[feld] = ergebnis
        STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
        kopie = dict(STAND)
    print(kopie, flush=True)


EDITOR_PLAETZE = 3


def main() -> None:
    umgebung_laden()
    basis, schluessel = os.environ.get("MARKETING_BILD_URL", ""), os.environ.get("MARKETING_BILD_KEY", "")
    if not basis or not schluessel:
        raise SystemExit("MARKETING_BILD_URL/MARKETING_BILD_KEY fehlen in Vibemind_V1/.env")
    from spaces.marketing.workers import marken_arbeiter, wissen_arbeiter   # importieren dieses Modul selbst
    api = ChatApi(basis, schluessel)
    marke = marken_arbeiter.MarkenApi(basis, schluessel)
    abgleich = marken_arbeiter.Abgleich()                    # erster Schritt = Abgleich beim Start
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    stopp = threading.Event()
    faeden = [marken_starten(marke, abgleich, marken_arbeiter.ein_durchlauf, stopp),
              wissen_starten(marken_arbeiter.MarkenApi(basis, schluessel), wissen_arbeiter.ein_durchlauf, stopp),
              *editor_starten(api, ein_durchlauf, stopp)]
    try:
        _warten(stopp)
    finally:                 # Strg+C/Ende: alle Faeden beenden ihren Schritt und halten an
        stopp.set()
        for faden in faeden:
            faden.join(timeout=MARKE_ENDE_S)


def _warten(stopp: threading.Event) -> None:
    """Der Hauptfaden wartet nur (kurzer Takt, damit Strg+C unter Windows durchkommt)."""
    while not stopp.wait(1):
        pass


def editor_schleife(api, ein, stopp: threading.Event, nr: int, takt_s: float = TAKT_S) -> None:
    """Ein Editor-Platz (Spec 2026-10-09 §1): holt Runden, solange er frei ist. Drei Plaetze = bis zu drei Runden
    gleichzeitig; die Grenzen je Entwurf setzt die DB durch."""
    feld = f"editor_{nr}"
    while not stopp.is_set():
        schleifenschritt(api, ein, feld)
        with _STAND_SPERRE:
            STAND["letztes_ergebnis"] = STAND.get(feld)
        stopp.wait(takt_s)


def editor_starten(api, ein, stopp: threading.Event, plaetze: int = EDITOR_PLAETZE,
                   takt_s: float = TAKT_S) -> list[threading.Thread]:
    faeden = []
    for nr in range(1, plaetze + 1):
        faden = threading.Thread(target=editor_schleife, args=(api, ein, stopp, nr, takt_s),
                                 name=f"editor-{nr}", daemon=True)
        faden.start()
        faeden.append(faden)
    return faeden


MARKE_ENDE_S = 10


def marken_schleife(marke_api, abgleich, marke_ein, stopp: threading.Event, takt_s: float = TAKT_S) -> None:
    """Eigene Schleife des Marken-Arbeiters (Ruling R1: selber Prozess, R15: eigener Faden): ein
    Marken-Auftrag, dann der Abgleich, wenn er faellig ist (beim Start und alle 10 Minuten). So wartet
    ein Marken-Chat nie hinter einem langen Editor-Auftrag (der ihn nach 2 min als "PC aus" verloere)."""
    while not stopp.is_set():
        schleifenschritt(marke_api, marke_ein, "marke")
        meldungen = abgleich.schritt(marke_api)
        if meldungen:
            with _STAND_SPERRE:
                STAND["abgleich"] = meldungen[-10:]
            print({"abgleich": meldungen[-10:]}, flush=True)
        stopp.wait(takt_s)


def marken_starten(marke_api, abgleich, marke_ein, stopp: threading.Event,
                   takt_s: float = TAKT_S) -> threading.Thread:
    """Startet marken_schleife als Daemon-Faden (haelt das Prozessende nie auf) und gibt ihn zurueck."""
    faden = threading.Thread(target=marken_schleife, args=(marke_api, abgleich, marke_ein, stopp, takt_s),
                             name="marke", daemon=True)
    faden.start()
    return faden


def wissen_schleife(wissen_api, wissen_ein, stopp: threading.Event, takt_s: float = TAKT_S) -> None:
    """Eigener Faden fuer Wissens-Laeufe (Ruling R7): ein Rowboat-Lauf dauert Minuten und belegt so nie den
    Marken-Faden - Chats und Formular-Bearbeitungen, die waehrenddessen kommen, laufen sofort."""
    while not stopp.is_set():
        schleifenschritt(wissen_api, wissen_ein, "wissen")
        stopp.wait(takt_s)


def wissen_starten(wissen_api, wissen_ein, stopp: threading.Event, takt_s: float = TAKT_S) -> threading.Thread:
    """Startet wissen_schleife als Daemon-Faden und gibt ihn zurueck."""
    faden = threading.Thread(target=wissen_schleife, args=(wissen_api, wissen_ein, stopp, takt_s),
                             name="wissen", daemon=True)
    faden.start()
    return faden


if __name__ == "__main__":
    main()
