"""Chat-Arbeiter am PC (sales-claw Spec 2026-10-02-newsletter-gestaltung-und-agent).
Holt Chat-Auftraege von der Marketing-API der VM (Tailnet, X-Bild-Key), fragt Claude
ueber den lokalen OpenAI-kompatiblen Shim :8117, prueft und wendet die JSON-Aenderungen
an und meldet zurueck. Gestartet von marketing-dienste-starten.ps1; Gesundheits-Port 8134."""
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
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageOps

from spaces.marketing.claw import agent_prompt, agent_strom, agent_werkzeuge, bildplaetze, markenwissen, unterlagen
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
STAND = {"letzter_lauf": None, "letztes_ergebnis": None}


class LlmFehler(Exception):
    pass


class ShimAbgelehnt(LlmFehler):
    """Der Shim hat die Anfrage selbst abgelehnt (HTTP 400, z. B. ungueltige Bildteile)."""


class ChatApi:
    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()

    def _anfrage(self, methode: str, pfad: str, daten=None, roh: bytes | None = None, typ="application/json") -> bytes:
        koerper = roh if roh is not None else (json.dumps(daten).encode("utf-8") if daten is not None else None)
        if methode == "POST" and koerper is None:
            koerper = b""
        req = urllib.request.Request(self.basis + "/api/chat/arbeiter" + pfad, data=koerper, method=methode,
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

    def gestoppt(self, aid, bloecke: dict | None) -> dict:
        return self._post(f"/{aid}/gestoppt", {"bloecke": bloecke})


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


def frage_strom(system: str, nachrichten: list[dict], url: str = LLM_URL, modell: str = MODELL) -> Iterator[str]:
    """Wie frage, aber als SSE-Strom des Shims: liefert jedes Text-Stueck (delta.content), sobald es da ist.
    Fehler-Chunk (finish_reason "error"), Abbruch ohne Abschluss, Muell oder eine leere Antwort => LlmFehler.
    Schliesst der Aufrufer den Strom (close), wird die Verbindung zum Shim geschlossen."""
    koerper = {"model": modell, "stream": True, "marketing_stream": True, "marketing_ohne_werkzeuge": True, "messages": [{"role": "system", "content": system}, *nachrichten]}
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
                inhalt = (wahl.get("delta") or {}).get("content") or ""
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
NEU_WERKZEUGE = ("flaeche_anlegen",)


def _neu_aufloesen(wert, neu_ids: list):
    """Ersetzt neu:<n> durch die id, die die n-te flaeche_anlegen im Live-Stand bekommen hat. Ohne
    bekannte id bleibt der Verweis stehen, dann lehnt anwenden die Aenderung ab."""
    if isinstance(wert, str):
        m = agent_werkzeuge.NEU.match(wert)
        if m and int(m.group(1)) <= len(neu_ids) and neu_ids[int(m.group(1)) - 1]:
            return neu_ids[int(m.group(1)) - 1]
        return wert
    if isinstance(wert, dict):
        return {k: v if k == "schritt" else _neu_aufloesen(v, neu_ids) for k, v in wert.items()}
    if isinstance(wert, list):
        return [_neu_aufloesen(v, neu_ids) for v in wert]
    return wert


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

    def __init__(self, api, aid, original: dict, medien: set, uhr, drossel_s: float):
        self.api, self.aid, self.original, self.medien = api, aid, original, medien
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


def _strom_lesen(fragen_strom, nachrichten, live: _Live, halter) -> tuple[str, list[str]]:
    """Liest einen Strom ganz; jede neue vollstaendige Aenderung geht sofort in den Live-Stand.
    Liefert den ganzen Text und die Lesefehler des StromLesers (unlesbare Aenderungen)."""
    live.neu_beginnen()
    leser = agent_strom.StromLeser()
    strom = iter(fragen_strom(agent_prompt.SYSTEM, nachrichten))
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


def anhaenge_vorbereiten(api, aid, auftrag: dict,
                         bilder: list[tuple[str, str]] | None = None) -> tuple[list[dict], str, str, list[str]]:
    """(bildteile, unterlagen, auswahl_text, hinweise) aus kontext.anhaenge und kontext.auswahl: Bilder
    (Anhaenge vor markierten, hoechstens MAX_BILDER) als Bildteile, Dokumente als Unterlagen-Text, die
    Auswahl als JSON. Was fehlt oder unlesbar ist, wird ein Hinweis; die Funktion wirft nicht.
    bilder (optional, Ausgabe): (Mediennamen, Herkunft) je mitgeschicktem Bildteil, in Bildteil-Reihenfolge."""
    kontext = auftrag.get("kontext") if isinstance(auftrag.get("kontext"), dict) else {}
    markiert, chip_bilder, hinweise = _auswahl_aufloesen(auftrag, kontext)
    anhaenge = kontext.get("anhaenge") if isinstance(kontext.get("anhaenge"), list) else []
    anhaenge = [a for a in anhaenge if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]]
    anhaenge = anhaenge[:MAX_ANHAENGE]
    anhang_namen = [a["name"] for a in anhaenge if a.get("art") == "bild"]
    namen = list(dict.fromkeys(anhang_namen + chip_bilder))
    bildteile: list[dict] = []
    for i, name in enumerate(namen):
        if len(bildteile) >= MAX_BILDER:
            hinweise.append(f"Höchstens {MAX_BILDER} Bilder je Nachricht, nicht mitgeschickt: {', '.join(namen[i:])}.")
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
    for a in anhaenge:
        if a.get("art") == "dokument":
            roh = _holen(api, aid, a["name"], hinweise)
            if roh is not None:
                dateien.append((a["name"], roh))
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


def chat_bearbeiten(api, auftrag, fragen_strom=frage_strom, uhr=time.monotonic, schlafen=time.sleep,
                    halten_takt_s: float = HALTEN_TAKT_S, drossel_s: float = DROSSEL_S) -> str:
    aid = str(auftrag["id"])
    versucht = []            # zurueck wurde schon aufgerufen

    def zurueckgeben(text: str) -> str:
        versucht.append(1)
        api.zurueck(aid, text)
        return "fehler"
    live = _Live(api, aid, auftrag.get("bloecke") or {}, set(auftrag.get("medien") or []), uhr, drossel_s)
    try:
        return _bearbeiten(api, auftrag, aid, fragen_strom, uhr, schlafen, halten_takt_s, zurueckgeben, live)
    except _Stopp as s:
        try:
            api.gestoppt(aid, live.gueltig if s.art == "behalten" else None)
        except (ApiFehler, OSError, ValueError):
            pass             # die VM schliesst einen gestoppten Auftrag nach 15 s selbst ab
        return "gestoppt"
    except _Verloren:
        return "fehler"
    except (ApiFehler, OSError, ValueError) as e:
        if not versucht:
            _freigeben(api, aid, NICHT_ERREICHBAR, e)
        return "fehler"


def _bearbeiten(api, auftrag, aid, fragen_strom, uhr, schlafen, halten_takt_s, zurueckgeben, live: _Live) -> str:
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
    if name:
        wissen = markenwissen.laden(markenwissen.wurzel(), mandant, name,
                                    f"{auftrag.get('nachricht', '')} {auftrag.get('titel', '')}")
        hinweise += wissen.hinweise
    if auftrag.get("medien_hinweis"):
        hinweise.insert(0, str(auftrag["medien_hinweis"]))
    angehaengt = [n for n, _ in bilder]
    medien = angehaengt + [m for m in medien if m not in angehaengt]   # Anhaenge zuerst, auch live erlaubt
    live.medien.update(angehaengt)
    text_nutzer = agent_prompt.nutzer_text(auftrag, medien, unterlagen=unterlagen_text, auswahl_text=auswahl_text,
                                           hinweise=hinweise, bilder=bilder, markenwissen=wissen.text,
                                           mandant_name=name, notizen_text=wissen.notizen,
                                           feedback=[f for f in (auftrag.get("rueckmeldungen_offen") or [])
                                                     if isinstance(f, dict) and isinstance(f.get("text"), str)])
    # Mit Bildern ist die erste Nachricht eine Teil-Liste; sie bleibt auch in der Korrekturrunde so.
    nachrichten = [{"role": "user", "content": [{"type": "text", "text": text_nutzer}, *bildteile]
                    if bildteile else text_nutzer}]
    mit_bildern = bool(bildteile)
    fehler = ""
    for versuch in (1, 2):
        beginn = None        # Shim-Fenster je Frage, ab dem ersten Ausfall (ein langer Strom zaehlt nicht mit)
        with halten(api, aid, halten_takt_s) as halter:
            while True:
                try:
                    text, leser_fehler = _strom_lesen(fragen_strom, nachrichten, live, halter)
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
                    raise agent_werkzeuge.WerkzeugFehler(grund)
        except (agent_prompt.AntwortFehler, agent_werkzeuge.WerkzeugFehler) as e:
            fehler = str(e)
            if versuch == 1:
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
        if antwort["notizen"] and name:
            titel, notiz_hinweise = markenwissen.notizen_schreiben(
                wissen.ordner, name, antwort["notizen"],
                {"newsletter": auftrag.get("titel", ""), "bitte": auftrag.get("nachricht", "")},
                datetime.date.today())
            # Vor die Markenwissen-Hinweise (uebersprungen/gekuerzt): die kappt die Hinweisgrenze zuerst.
            vor = next((i for i, h in enumerate(hinweise) if h in wissen.hinweise), len(hinweise))
            hinweise[vor:vor] = notiz_hinweise
            if titel:
                text_antwort += "\n\n" + "\n".join(f"Notiz in Rowboat abgelegt: {t}" for t in titel)
        antwort_vm = api.fertig(aid, {
            "antwort": _mit_hinweisen(hinweise, text_antwort),
            "bloecke": bloecke if ergebnis.geaendert else None,
            "bildauftraege": bildauftraege,
            "export_vorschlag": export,
            "notiz": ergebnis.notiz,
        })
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
            return exportieren(api, auftrag)
        except Exception as e:  # noqa: BLE001 - Auftrag gehoert uns, also zurueckgeben
            _freigeben(api, str(auftrag["id"]), "Export nicht möglich: " + _kurz(e), e)
            return "fehler"
    return chat_bearbeiten(api, auftrag, fragen_strom, uhr, schlafen, halten_takt_s)


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps(STAND, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


def schleifenschritt(api, ein=ein_durchlauf) -> None:
    try:
        STAND["letztes_ergebnis"] = ein(api)
    except Exception as e:  # noqa: BLE001 - ein Fehler darf die Schleife nicht toeten
        STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}: {e}"[:200]
    STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print(STAND, flush=True)


def main() -> None:
    umgebung_laden()
    basis, schluessel = os.environ.get("MARKETING_BILD_URL", ""), os.environ.get("MARKETING_BILD_KEY", "")
    if not basis or not schluessel:
        raise SystemExit("MARKETING_BILD_URL/MARKETING_BILD_KEY fehlen in Vibemind_V1/.env")
    api = ChatApi(basis, schluessel)
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    while True:
        schleifenschritt(api)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
