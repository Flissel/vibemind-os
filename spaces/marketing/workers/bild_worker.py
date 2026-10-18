"""Bild-Arbeiter am PC (sales-claw Spec 2026-09-29-newsletter-bilder-und-
gestaltung-design.md §7.2). Holt Auftraege von der Marketing-API der VM
(Tailnet, X-Bild-Key), erzeugt mit ComfyUI, prueft mit Ollama (nur bei Bedarf gestartet), liefert JPEGs ab.
Die VM schreibt die Fassung. Gestartet von marketing-dienste-starten.ps1;
Gesundheits-Port 8133."""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from spaces.marketing.claw import (bild_comfy, bild_farben, bild_messen, bild_prompt, bild_sehen, bildplaetze,
                                   markenprofil, markenwissen)

PORT = 8133
TAKT_S = 20
VERSUCHE_JE_PLATZ = 3
GRENZE_STAERKE = 60           # bis hierhin: Motiv nah halten und Aehnlichkeit erzwingen
MIN_AEHNLICH = 0.75           # CLIP cos(alt, neu) bei staerke <= GRENZE_STAERKE
FREI_MIN, FREI_MAX = 0.02, 0.98     # Vordergrundanteil (Alpha > 128) ausserhalb = kein klares Motiv
PNG_MAX = 4 * 1024 * 1024           # Obergrenze der VM-Route fuer freigestellte PNG
QUELLE_MAX = 16 * 1024 * 1024  # Quellbild groesser -> wie fehlend (Menschen legen auch Kamera-Fotos ab)
QUELLE_KANTE = 1536           # Quelle vor Sehen/CLIP am PC auf diese laengste Kante verkleinern
ERZEUGUNG_ZEITLIMIT_S = 1200  # Vorgabe; MARKETING_BILD_ZEITLIMIT_S (.env) setzt es (FLUX.2 kalt gemessen 619 s)
HALTEN_TAKT_S = 60            # Herzschlag waehrend der Erzeugung: Vergabe (FRIST 10 min) verlaengern
FREMD = (404, 409, 422)       # Auftrag gehoert uns nicht mehr
ZULETZT = {"art": None}       # Art des letzten ComfyUI-Auftrags in diesem Prozess: flux1, flux2, freistellen
JPEG_ZIEL = 250 * 1024
_HIER = Path(__file__).resolve()
REPO_ROOT = next((p for p in _HIER.parents if (p / "vibemind-os").is_dir()), _HIER.parents[3])
STARTER = _HIER.parents[1] / "claw" / "scripts" / "marketing-dienste-starten.ps1"
STAND = {"letzter_lauf": None, "letztes_ergebnis": None}
START_SCHONFRIST_S = 120      # nach Arbeiterstart: Starter-Skript startet die Dienste gerade selbst
OLLAMA_WARTE_S = 90           # so lange auf Ollama warten, nachdem es gestartet wurde
OLLAMA_POLL_S = 3
START_ABSTAND_S = 600         # hoechstens ein Dienststart je 10 Minuten
START = {"arbeiter": None, "dienste": None}   # time.monotonic()-Zeitpunkte


class ApiFehler(Exception):
    def __init__(self, code: int, grund: str):
        super().__init__(f"{code}: {grund}")
        self.code, self.grund = code, grund


def umgebung_laden() -> None:
    """MARKETING_BILD_URL/KEY, FASTEMBED_CACHE_PATH, MARKETING_BILDMODELL und MARKETING_BILD_ZEITLIMIT_S aus
    Vibemind_V1/.env, wenn nicht gesetzt (Muster claw/server.py). Ohne FASTEMBED_CACHE_PATH laedt CLIP
    (bild_messen) sein Modell bei jedem Start neu in ein Temp-Verzeichnis. Ohne MARKETING_BILDMODELL hier
    erreichte der Schalter flux1 den Arbeiter nie (Schluss-Fix C1)."""
    datei = REPO_ROOT / ".env"
    fehlend = [k for k in ("MARKETING_BILD_URL", "MARKETING_BILD_KEY", "FASTEMBED_CACHE_PATH",
                           "MARKETING_BILDMODELL", "MARKETING_BILD_ZEITLIMIT_S")
               if not os.environ.get(k)]
    if not fehlend or not datei.exists():
        return
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        for k in fehlend:
            if zeile.strip().startswith(k + "="):
                os.environ[k] = zeile.split("=", 1)[1].strip().strip('"').strip("'")


def zeitlimit_s() -> int:
    """Gesamtlimit je Erzeugung: MARKETING_BILD_ZEITLIMIT_S (ganze Sekunden > 0), sonst ERZEUGUNG_ZEITLIMIT_S."""
    try:
        wert = int(os.environ.get("MARKETING_BILD_ZEITLIMIT_S", "").strip())
    except ValueError:
        return ERZEUGUNG_ZEITLIMIT_S
    return wert if wert > 0 else ERZEUGUNG_ZEITLIMIT_S


@contextlib.contextmanager
def _halten(api, aid):
    """Herzschlag (Schluss-Fix I3, Muster chat_worker.halten): verlaengert die Vergabe alle HALTEN_TAKT_S, solange
    ComfyUI rechnet (FLUX.2 kalt 619 s > Vergabe 10 min). Das gelieferte Event ist gesetzt, sobald die Vergabe
    weg ist (weiter False oder 404/409/422); Netzstoerungen ueberspringt der Herzschlag."""
    verloren, ende = threading.Event(), threading.Event()
    takt = HALTEN_TAKT_S

    def lauf():
        while not ende.wait(takt):
            try:
                if not api.weiter(aid):
                    verloren.set()
                    return
            except ApiFehler as e:
                if e.code in FREMD:
                    verloren.set()
                    return
            except (OSError, ValueError):
                pass
    faden = threading.Thread(target=lauf, daemon=True)
    faden.start()
    try:
        yield verloren
    finally:
        ende.set()
        faden.join(timeout=1)


def _modelle_fuer(comfy, art: str) -> None:
    """FLUX.2 (Schluss-Fix C1): nur freigeben, wenn der vorige ComfyUI-Auftrag dieses Prozesses eine andere Art
    war (flux1, freistellen, oder Prozessstart) - zwei FLUX.2-Auftraege hintereinander laden nicht kalt neu."""
    if ZULETZT["art"] != art:
        comfy.freigeben()
    ZULETZT["art"] = art


def tls_kontext() -> ssl.SSLContext:
    """TLS gegen die Tailnet-Adresse der VM. Gemessen 30.09.2026: deren Let's-
    Encrypt-Kette (YE1 -> Root YE -> ISRG Root X2) endet fuer das Windows-
    Python ueber die abgelaufene X2/X1-Querzertifizierung ("certificate has
    expired"); Windows selbst laedt X2 bei Bedarf nach, OpenSSL nicht. Das
    certifi-Buendel enthaelt X2 - also das nehmen, wenn es da ist."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _grund(e: urllib.error.HTTPError) -> str:
    """detail aus dem JSON-Fehlerkoerper der API; "" wenn keiner lesbar ist."""
    try:
        return str(json.loads(e.read() or b"{}").get("detail", ""))[:300]
    except (ValueError, OSError, AttributeError):
        return ""


class ArbeiterApi:
    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()

    def _post(self, pfad: str, daten=None, roh: bytes | None = None, typ="application/json"):
        koerper = roh if roh is not None else (json.dumps(daten).encode("utf-8") if daten is not None else b"")
        req = urllib.request.Request(self.basis + "/api/bilder/arbeiter" + pfad, data=koerper, method="POST",
                                     headers={"Content-Type": typ})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise ApiFehler(e.code, _grund(e)) from None

    def naechster(self):
        return self._post("/naechster").get("auftrag")

    def weiter(self, aid):
        return bool(self._post(f"/{aid}/weiter").get("ok"))

    def bild(self, aid, platz, daten: bytes, format: str = "jpg") -> str:
        png = format == "png"
        pfad = f"/{aid}/bild?platz={urllib.request.quote(platz)}" + ("&format=png" if png else "")
        return self._post(pfad, roh=daten, typ="image/png" if png else "image/jpeg")["name"]

    def quelle(self, aid, platz) -> bytes | None:
        """Aktuelles Bild des Platzes von der VM; 404 (kein Bild) -> None. Liest
        hoechstens QUELLE_MAX + 1 Bytes - laenger heisst zu gross (_erzeugen)."""
        req = urllib.request.Request(
            f"{self.basis}/api/bilder/arbeiter/{aid}/quelle?platz={urllib.parse.quote(platz)}")
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
                return r.read(QUELLE_MAX + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise ApiFehler(e.code, _grund(e) or "Quellbild nicht abrufbar") from None

    def vorlage(self, aid, name) -> bytes | None:
        """Vorlage (Medienname aus b.vorlagen) von der VM; 404 (fremd, unbekannt, Vergabe weg) -> None.
        Liest hoechstens QUELLE_MAX + 1 Bytes - laenger heisst zu gross (_vorlagen_holen)."""
        req = urllib.request.Request(
            f"{self.basis}/api/bilder/arbeiter/{aid}/vorlage?name={urllib.parse.quote(name)}")
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
                return r.read(QUELLE_MAX + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise ApiFehler(e.code, _grund(e) or "Vorlage nicht abrufbar") from None

    def fertig(self, aid, ergebnis: dict, befund: str, messung: dict | None = None) -> dict:
        return self._post(f"/{aid}/fertig", {"ergebnis": ergebnis, "befund": befund, "messung": messung or {}})

    def zurueck(self, aid, befund: str, endgueltig: bool) -> str:
        return self._post(f"/{aid}/zurueck", {"befund": befund[:500], "endgueltig": endgueltig}).get("status", "")


def verkleinern(png: bytes, breite: int, hoehe: int) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as bild:
        bild = bild.convert("RGB").resize((breite, hoehe), Image.LANCZOS)
        for qualitaet in (85, 78, 70, 62, 55):
            b = io.BytesIO()
            bild.save(b, "JPEG", quality=qualitaet, optimize=True, progressive=True)
            if b.tell() <= JPEG_ZIEL:
                break
        return b.getvalue()


def quelle_normalisieren(roh: bytes) -> bytes | None:
    """Quellbild am PC dekodieren, nach RGB, laengste Kante <= QUELLE_KANTE,
    als PNG neu kodieren - diese Bytes bekommen Sehmodell und CLIP. Menschen
    legen in media/ beliebige Fotos ab (gross, GIF, Palette, Alpha). Unlesbar
    oder Dekompressionsbombe -> None (Arbeiter erzeugt dann neu)."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(roh)) as bild:
            bild = bild.convert("RGB")
            bild.thumbnail((QUELLE_KANTE, QUELLE_KANTE), Image.LANCZOS)
            b = io.BytesIO()
            bild.save(b, "PNG")
            return b.getvalue()
    except (Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        return None


def _abgekoppelt() -> dict:
    """Popen-Argumente: kein Pipe-Handle erben, eigene Prozessgruppe, nie warten.
    (capture_output + run() haengt unter Windows, solange ein Enkelprozess die
    Pipe-Handles haelt - gemessen 02.10.: Schleife stand seit 11:33.)"""
    k: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        k["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        k["start_new_session"] = True
    return k


def dienste_starten() -> None:
    """Startet ComfyUI ueber das Starter-Skript (Ollama startet es nicht, siehe
    ollama_starten). Abgekoppelt und ohne Warten: der naechste Takt prueft
    erneut (ComfyUI braucht ~60 s bis /system_stats)."""
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(STARTER)],
                     **_abgekoppelt())


def ollama_starten() -> None:
    r"""Startet Ollama (Betreiber hat den Task OllamaServe am 24.09. absichtlich
    abgeschaltet: nur bei Bedarf). Programm aus OLLAMA_APP, sonst
    %LOCALAPPDATA%\Programs\Ollama\ollama app.exe; fehlt die Datei, passiert nichts."""
    exe = os.environ.get("OLLAMA_APP") or str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe")
    if not Path(exe).is_file():
        return
    subprocess.Popen([exe], **_abgekoppelt())


def _ollama_bereit(api, aid, prompt, ollama, uhr, schlafen) -> bool | str:
    """Ollama nur fuer Auftraege, die es brauchen (neu/ueberarbeiten), NACH der
    Vergabe: einmal starten, bis OLLAMA_WARTE_S pollen. True = laeuft, False =
    nicht erreichbar, "verworfen" = Vergabe weg."""
    if prompt.laeuft():
        return True
    if not api.weiter(aid):
        return "verworfen"
    ollama()
    ende = uhr() + OLLAMA_WARTE_S
    while uhr() < ende:
        schlafen(OLLAMA_POLL_S)
        if prompt.laeuft():
            return True
    return prompt.laeuft()


def _dienste_bereit(comfy, starten, uhr) -> bool:
    """Nur ComfyUI pruefen, BEVOR ein Auftrag genommen wird (Freistellen braucht
    kein Ollama; das holt _ollama_bereit bei Bedarf). Laeuft es nicht, hoechstens
    alle 10 min starten und nie in den ersten 120 s nach dem Arbeiterstart (dann
    startet marketing-dienste-starten.ps1 es gerade selbst)."""
    jetzt = uhr()
    if START["arbeiter"] is None:
        START["arbeiter"] = jetzt
    if comfy.laeuft():
        return True
    if (jetzt - START["arbeiter"] >= START_SCHONFRIST_S
            and (START["dienste"] is None or jetzt - START["dienste"] >= START_ABSTAND_S)):
        START["dienste"] = jetzt
        starten()
    return False


def vordergrund_anteil(png: bytes) -> float:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as b:
        a = b.convert("RGBA").getchannel("A")
        hist = a.histogram()
        return sum(hist[129:]) / max(1, a.size[0] * a.size[1])


def png_passend(png: bytes, grenze: int | None = None) -> bytes:
    """PNG <= Grenze unveraendert; sonst laengste Kante auf 1600 px verkleinert."""
    if len(png) <= (PNG_MAX if grenze is None else grenze):
        return png
    from PIL import Image
    with Image.open(io.BytesIO(png)) as b:
        b = b.convert("RGBA")
        b.thumbnail((1600, 1600), Image.LANCZOS)
        out = io.BytesIO()
        b.save(out, "PNG", optimize=True)
        return out.getvalue()


def _freistellen(api, aid, auftrag, ziele, comfy):
    """Freistellen eines belegten Bildplatzes (BiRefNet in ComfyUI). Kein Sehmodell,
    kein Prompt, kein FLUX. Rueckgabe (ergebnis, befunde, {}) wie _erzeugen oder "verworfen"."""
    platz = next((p for p in ziele if p.id == auftrag.get("platz")), None)
    if platz is None or platz.leer:
        return {}, ["Freistellen braucht einen Bildplatz mit Bild"], {}
    if not api.weiter(aid):
        return "verworfen"
    try:
        roh = api.quelle(aid, platz.id)
    except ApiFehler as e:
        if e.code == 422 and "in Arbeit" in e.grund:
            return "verworfen"
        raise
    quelle = quelle_normalisieren(roh) if roh and len(roh) <= QUELLE_MAX else None
    if quelle is None:
        return {}, [f"{platz.id}: Quellbild fehlt oder unlesbar"], {}
    ZULETZT["art"] = "freistellen"   # kein Ollama-Schritt: ComfyUI-Modelle bleiben geladen (Betreiber 09.10.)
    try:
        with _halten(api, aid) as verloren:
            frei = comfy.freistellen(quelle)
    except bild_comfy.ComfyVoruebergehend:
        raise                        # Zeitueberschreitung/Verbindung: wiederholbar (ein_durchlauf), nie endgueltig
    except bild_comfy.ComfyFehler as e:
        return {}, [f"Freistellen nicht verfügbar: {e}"], {}
    if verloren.is_set():
        return "verworfen"
    anteil = vordergrund_anteil(frei)
    if not FREI_MIN <= anteil <= FREI_MAX:
        return {}, ["Kein klares Motiv gefunden"], {}
    daten = png_passend(frei)
    if len(daten) > PNG_MAX:
        return {}, [f"{platz.id}: freigestelltes Bild zu groß"], {}
    if not api.weiter(aid):
        return "verworfen"
    try:
        name = api.bild(aid, platz.id, daten, format="png")
    except ApiFehler as e:
        if e.code != 422:
            raise
        if "in Arbeit" in e.grund:
            return "verworfen"
        return {}, [f"{platz.id}: {e.grund}"], {}
    return {platz.id: name}, [f"freigestellt ({anteil:.0%} Motiv)"], {}


# Wortlaut von bild_comfy.erzeugen_flux2 bei mehr als MAX_REFERENZEN Referenzen.
ZU_VIELE_REFERENZEN = f"Höchstens {bild_comfy.MAX_REFERENZEN} Referenzbilder"
# ComfyFehler, die ein Neuversuch nicht behebt: endgueltig und ohne Praefix an den Agenten.
COMFY_ENDGUELTIG = (bild_comfy.MODELL_FEHLT, bild_comfy.SPEICHER_VOLL, ZU_VIELE_REFERENZEN)


def modell() -> str:
    """Bildmodell per Schalter MARKETING_BILDMODELL: flux2 (Vorgabe, auch bei unbekannten Werten) oder flux1."""
    return "flux1" if os.environ.get("MARKETING_BILDMODELL", "").strip().lower() == "flux1" else "flux2"


def _vorlagen_holen(api, aid, namen) -> tuple[list[bytes], str] | str:
    """Vorlagen als normalisierte PNGs; (liste, "") oder (leer, befund) oder "verworfen"."""
    aus = []
    for n in namen:
        if not api.weiter(aid):
            return "verworfen"
        try:
            roh = api.vorlage(aid, n)
        except ApiFehler as e:
            if e.code == 422 and "in Arbeit" in e.grund:
                return "verworfen"
            raise
        png = quelle_normalisieren(roh) if roh and len(roh) <= QUELLE_MAX else None
        if png is None:
            return [], f"Vorlage {n} nicht gefunden oder unlesbar"
        aus.append(png)
    return aus, ""


def _bildstil(auftrag: dict) -> str:
    """Abschnitt "Bildstil" aus companys/<Firma>/Marke.md der Firma des Auftrags ('' ohne Firma/Abschnitt)."""
    mandant = str(auftrag.get("mandant") or "")
    if not mandant:
        return ""
    profil = markenprofil.lesen(markenwissen.wurzel(), mandant, str(auftrag.get("mandant_name") or mandant))
    return profil.abschnitte.get("Bildstil", "")


def _erzeugen(api, aid, auftrag, ziele, comfy, prompt, sehen, messen, ohne_ersatz=False):
    """Phase 1 (Ollama, keep_alive 0): je Platz Quelle holen, sehen, Prompt.
    Phase 2: alle Bilder am Stueck mit FLUX. ComfyUI-Modelle bleiben danach geladen
    (Betreiber-Entscheid 09.10.: kalt von der HDD ~400 s); /free kommt nur VOR
    einem Ollama-Schritt - einmal vor Phase 1 und, bei Selbstpruefung, je Bild vor dem Sehmodell. CLIP-Messung je Bild auf der CPU.
    Ueberarbeiten = "neu mit Motiv" (Betreiber-Entscheid 30.09.): immer
    Text-zu-Bild in Platzmassen, die Quelle dient nur Sehen und Messen.
    ohne_ersatz (Rueckfall flux1 fuer aendern): fehlt das Bild, entsteht KEIN neues, nur "Quellbild fehlt".
    Rueckgabe (ergebnis, befunde, messwerte) oder "verworfen"."""
    staerke = int(auftrag.get("staerke") if auftrag.get("staerke") is not None else 55)
    modus = auftrag.get("modus") or "ueberarbeiten"
    titel, hinweis = str(auftrag.get("titel") or ""), str(auftrag.get("hinweis") or "")
    befunde, arbeit = [], []
    comfy.freigeben()   # Phase 1 ruft Ollama: Grafikspeicher von FLUX leeren (vorheriger Auftrag)
    ZULETZT["art"] = "flux1"
    palette = bild_farben.palette(auftrag.get("bloecke"))
    bildstil = _bildstil(auftrag)
    for platz in ziele:
        quelle = None
        if ohne_ersatz and platz.leer:          # aendern (flux1): Platzhalter = kein Bild zum Aendern
            befunde.append(f"{platz.id}: Quellbild fehlt")
            continue
        if modus == "ueberarbeiten" and staerke < 100 and not platz.leer:
            # Die VM gibt die Quelle nur bei laufender Vergabe heraus, und Sehen +
            # Prompt mit kalten Modellen von der HDD dauern Minuten je Platz.
            if not api.weiter(aid):
                return "verworfen"
            try:
                quelle = api.quelle(aid, platz.id)
            except ApiFehler as e:
                if e.code == 422 and "in Arbeit" in e.grund:
                    return "verworfen"
                raise
            if quelle is not None and len(quelle) > QUELLE_MAX:
                quelle = None
                befunde.append(f"{platz.id}: Quellbild zu gross - neu erzeugt")
            elif quelle is None:
                befunde.append(f"{platz.id}: Quellbild fehlt - neu erzeugt")
            else:
                quelle = quelle_normalisieren(quelle)
                if quelle is None:
                    befunde.append(f"{platz.id}: Quellbild unlesbar - neu erzeugt")
            if ohne_ersatz and quelle is None:  # aendern (flux1): kein Ersatzbild, nur der Befund (Focus 2)
                befunde[-1] = f"{platz.id}: Quellbild fehlt"
                continue
        beschreibung = sehen.beschreiben(quelle) if quelle is not None else ""
        if quelle is not None and not beschreibung:
            befunde.append(f"{platz.id}: ohne Bildbeschreibung")
        # Layoutfarben der Fassung reisen im Platz mit (Betreiber 01.10.: Bild fuegt sich ins Layout).
        daten = {**platz.als_dict(), "palette": palette, "bildstil": bildstil}
        if beschreibung:
            text = prompt.bearbeitungs_prompt(beschreibung, daten, titel, hinweis,
                                              nah=staerke <= GRENZE_STAERKE)
        else:   # neu, oder Sehen gescheitert: alt + Kontext + Hinweis (Spec §7)
            text = prompt.prompt_schreiben(daten, titel, hinweis)
        arbeit.append((platz, text, quelle, None))
    je_bild_freigeben = os.environ.get("BILD_SELBSTPRUEFUNG", "") == "1"
    erg = _bilder(api, aid, arbeit, staerke, hinweis, comfy, prompt, messen, je_bild_freigeben, befunde)
    if erg == "verworfen":
        return erg
    ergebnis, messwerte = erg
    return ergebnis, befunde, messwerte


def _flux2(api, aid, auftrag, ziele, comfy, prompt, messen):
    """FLUX.2 [klein] fuer `neu` und `aendern`. Vorlagen (Mediennamen) einmal je Auftrag als Referenzen.
    aendern: das aktuelle Bild des Platzes ist Referenz 1, Prompt aus aenderungs_prompt, kein Sehmodell.
    Fehlt das Bild (404, unlesbar, inzwischen Platzhalter), entsteht KEIN Ersatzbild - nur der Befund.
    Keine Aehnlichkeitsregel, keine CLIP-Messung. Rueckgabe wie _erzeugen oder "verworfen"."""
    modus = auftrag.get("modus")
    titel, hinweis = str(auftrag.get("titel") or ""), str(auftrag.get("hinweis") or "")
    befunde, arbeit = [], []
    namen = list(auftrag.get("vorlagen") or [])
    if len(namen) + (1 if modus == "aendern" else 0) > bild_comfy.MAX_REFERENZEN:   # bei aendern ist das Bild Ref. 1
        return {}, [ZU_VIELE_REFERENZEN], {}
    # Nur beim Wechsel der Art freigeben (Schluss-Fix C1): FLUX.2 kalt von der HDD dauert 619 s; die Prompts
    # (Ollama) laufen dann neben dem geladenen FLUX.2.
    _modelle_fuer(comfy, "flux2")
    geholt = _vorlagen_holen(api, aid, namen)
    if geholt == "verworfen":
        return geholt
    vorlagen, befund = geholt
    if befund:
        return {}, [befund], {}
    palette = bild_farben.palette(auftrag.get("bloecke"))
    bildstil = _bildstil(auftrag)
    for platz in ziele:
        daten = {**platz.als_dict(), "palette": palette, "bildstil": bildstil}
        if modus == "aendern":
            quelle = None
            if not platz.leer:      # Platzhalter = kein Bild mehr: nichts holen
                if not api.weiter(aid):
                    return "verworfen"
                try:
                    roh = api.quelle(aid, platz.id)
                except ApiFehler as e:
                    if e.code == 422 and "in Arbeit" in e.grund:
                        return "verworfen"
                    raise
                quelle = quelle_normalisieren(roh) if roh and len(roh) <= QUELLE_MAX else None
            if quelle is None:
                befunde.append(f"{platz.id}: Quellbild fehlt")
                continue
            text, referenzen = prompt.aenderungs_prompt(hinweis, daten), [quelle] + vorlagen
        else:
            text, referenzen = prompt.prompt_schreiben(daten, titel, hinweis), list(vorlagen)
        arbeit.append((platz, text, None,
                       lambda t, w, h, s, r=referenzen: comfy.erzeugen_flux2(t, w, h, s, r,
                                                                             zeitlimit_s=zeitlimit_s())))
    je_bild_freigeben = os.environ.get("BILD_SELBSTPRUEFUNG", "") == "1"
    erg = _bilder(api, aid, arbeit, 100, hinweis, comfy, prompt, messen, je_bild_freigeben, befunde)
    if erg == "verworfen":
        return erg
    ergebnis, messwerte = erg
    return ergebnis, befunde, messwerte


def _bilder(api, aid, arbeit, staerke, hinweis, comfy, prompt, messen, je_bild_freigeben, befunde):
    """arbeit: je Platz (platz, text, quelle, erzeuger). erzeuger None = FLUX.1 Text-zu-Bild (comfy.erzeugen),
    sonst ein Callable (text, breite, hoehe, seed) -> PNG (FLUX.2 mit Referenzen)."""
    ergebnis, messwerte = {}, {}
    for platz, text, quelle, erzeuger in arbeit:
        # bestes = (aehnlich, png, messung, pruef_notiz); pruef_grund und fern getrennt,
        # damit der "bestes Bild"-Befund nie einen Selbstpruefungs-Grund nennt.
        bestes, pruef_grund, fern, getroffen = None, "", "", False
        for _ in range(VERSUCHE_JE_PLATZ):
            if not api.weiter(aid):   # Vergabe je Versuch verlaengern (Kaltstart ~300 s, Vergabe 10 min)
                return "verworfen"
            try:
                seed = random.randrange(2**31)
                with _halten(api, aid) as verloren:   # Herzschlag, solange ComfyUI rechnet (Schluss-Fix I3)
                    if erzeuger is None:
                        png = comfy.erzeugen(text, platz.erzeug_breite, platz.erzeug_hoehe, seed,
                                             zeitlimit_s=zeitlimit_s())
                    else:
                        png = erzeuger(text, platz.erzeug_breite, platz.erzeug_hoehe, seed)
            finally:
                if je_bild_freigeben:
                    comfy.freigeben()
            if verloren.is_set():
                return "verworfen"
            ok, notiz = prompt.pruefen(png, text)
            if not ok:
                pruef_grund = notiz
                continue
            m = messen.messen(quelle, png, hinweis) if quelle is not None else {}
            aehnlich = m.get("aehnlich_original")
            zu_fern = (quelle is not None and staerke <= GRENZE_STAERKE and aehnlich is not None
                       and aehnlich < MIN_AEHNLICH)
            if bestes is None or (aehnlich or 0) > (bestes[0] or 0):
                bestes = (aehnlich, png, m, notiz)
            if zu_fern:
                fern = f"Aehnlichkeit {aehnlich:.2f} unter {MIN_AEHNLICH}"
                continue
            bestes, getroffen = (aehnlich, png, m, notiz), True
            break
        if bestes is None:
            befunde.append(f"{platz.id}: {pruef_grund}")
            continue
        aehnlich, png, m, notiz = bestes
        if not getroffen:             # nur zu unaehnliche Kandidaten: das aehnlichste (aehnlich gesetzt)
            befunde.append(f"{platz.id}: {fern} - bestes Bild ({aehnlich:.2f}) genommen")
        if quelle is not None and staerke <= GRENZE_STAERKE and not m:
            befunde.append(f"{platz.id}: ohne Messung")
        if notiz:                     # z.B. "Selbstpruefung unlesbar - ungeprueft eingesetzt"
            befunde.append(f"{platz.id}: {notiz}")
        jpeg = verkleinern(png, platz.erzeug_breite, platz.erzeug_hoehe)
        if not api.weiter(aid):   # Erzeugung + 180 s Pruefung koennen die Vergabe ueberziehen
            return "verworfen"
        try:
            ergebnis[platz.id] = api.bild(aid, platz.id, jpeg)
        except ApiFehler as e:
            if e.code != 422:
                raise
            if "in Arbeit" in e.grund:
                return "verworfen"
            befunde.append(f"{platz.id}: {e.grund}")
            continue
        if m:
            messwerte[platz.id] = m
    return ergebnis, messwerte


def ein_durchlauf(api, comfy=bild_comfy, prompt=bild_prompt, starten=dienste_starten,
                  uhr=time.monotonic, sehen=bild_sehen, messen=bild_messen,
                  ollama_starten=ollama_starten, schlafen=time.sleep) -> str:
    if not _dienste_bereit(comfy, starten, uhr):
        return "wartet"
    auftrag = api.naechster()
    if not auftrag:
        return "leer"
    aid = str(auftrag["id"])
    # Ab hier gehoert der Auftrag dem Arbeiter: jede Stoerung gibt ihn zurueck,
    # statt die Vergabe bis zum Ablauf zu blockieren.
    try:
        ziele = [p for p in bildplaetze.finde(auftrag.get("bloecke") or {})
                 if (auftrag.get("platz") in (None, p.id)) and (not auftrag.get("nur_leere") or p.leer)]
        if not ziele:
            api.zurueck(aid, "Keine passenden Bildplaetze", True)
            return "zurueck"
        if auftrag.get("modus") == "freistellen":
            erg = _freistellen(api, aid, auftrag, ziele, comfy)
        else:
            bereit = _ollama_bereit(api, aid, prompt, ollama_starten, uhr, schlafen)
            if bereit == "verworfen":
                return "verworfen"
            if not bereit:
                api.zurueck(aid, "Ollama nicht erreichbar – Bildbeschreibung nicht moeglich", False)
                return "zurueck"
            modus = auftrag.get("modus")
            if modus in ("neu", "aendern") and modell() == "flux2":
                erg = _flux2(api, aid, auftrag, ziele, comfy, prompt, messen)
            elif modus == "aendern":   # Rueckfall flux1 (R3): der Ueberarbeiten-Weg mit Staerke 55
                erg = _erzeugen(api, aid, {**auftrag, "modus": "ueberarbeiten", "staerke": 55},
                                ziele, comfy, prompt, sehen, messen, ohne_ersatz=True)
            else:                      # neu unter flux1, ueberarbeiten (alte Auftraege, Review Focus 5)
                erg = _erzeugen(api, aid, auftrag, ziele, comfy, prompt, sehen, messen)
            if erg != "verworfen" and modell() == "flux1" and auftrag.get("vorlagen"):
                erg = (erg[0], ["Vorlagen nur mit FLUX.2"] + erg[1], erg[2])
        if erg == "verworfen":
            return "verworfen"
        ergebnis, befunde, messwerte = erg
        if not ergebnis:
            api.zurueck(aid, "; ".join(befunde) or "Kein Bild bestanden", True)
            return "zurueck"
        try:
            api.fertig(aid, ergebnis, "; ".join(befunde), messwerte)
        except ApiFehler as e:
            if e.code == 422 and "in Arbeit" in e.grund:
                return "verworfen"
            raise
        return "fertig"
    except (bild_comfy.ComfyFehler, ApiFehler, ValueError, OSError, TimeoutError, KeyError, TypeError) as e:
        # ValueError deckt JSONDecodeError, OSError deckt URLError und PIL.UnidentifiedImageError.
        if (isinstance(e, bild_comfy.ComfyFehler) and not isinstance(e, bild_comfy.ComfyVoruebergehend)
                and str(e) in COMFY_ENDGUELTIG):
            # Deterministisch (fehlendes Modell, zu viele Vorlagen): ein Neuversuch scheitert gleich -
            # sofort endgueltig und im exakten Wortlaut an den Agenten (Fix I1).
            api.zurueck(aid, str(e), True)
            return "zurueck"
        api.zurueck(aid, f"Erzeugung unterbrochen: {type(e).__name__}: {e}", False)
        return "zurueck"


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
    api = ArbeiterApi(basis, schluessel)
    START["arbeiter"] = time.monotonic()
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    while True:
        try:
            STAND["letztes_ergebnis"] = ein_durchlauf(api)
        except Exception as e:  # noqa: BLE001 - ein Fehler darf die Schleife nicht toeten
            STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}: {e}"[:300]
        STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(STAND, flush=True)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
