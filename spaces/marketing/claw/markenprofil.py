"""Markenprofil je Firma: companys/<Firma>/Marke.md (Kopfteil + Abschnitte) und Logo.

Die Datei ist fuer Menschen UND Maschinen: zwischen den ersten beiden `---`
steht ein Kopfteil mit `schluessel: wert`, danach Markdown-Abschnitte (`## Ton`).
Aeltere Dateien ohne Kopfteil (Vorlagen, Probetexte) bleiben gueltig: dann zaehlt
nur der Textteil.

`lesen` ist fehlertolerant und wirft nie: ungueltige Kopfwerte werden verworfen
und als Hinweis gemeldet. `schreiben` ist streng und wirft MarkenFehler. Ordnerfindung
und Link-Sperre (Symlink/Junction) kommen unveraendert aus `markenwissen`.
"""
from __future__ import annotations

import base64
import datetime
import io
import os
import re
import tempfile
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from spaces.marketing.claw import markenwissen as mw
from spaces.marketing.claw.schriften import REGISTER

DATEI = "Marke.md"
VERLAUF = "Marke-Verlauf"
LOGO_MAX_BYTES = 2 * 1024 * 1024
MAX_PIXEL = 40_000_000
SPIEGEL_KANTE = 600          # laengste Kante des gespiegelten Logos
SPIEGEL_MAX_CHARS = 140 * 1024  # ganze data-URL
_MIN_KANTE = 16
_LESE_MAX = 200_000

KOPF_REIHENFOLGE = ("akzent", "zweitfarbe", "grund", "text", "schrift_anzeige", "schrift_text", "logo",
                    "logo_dunkel", "webseite", "stand")
_LOGOS = (("logo", "logo_pfad"), ("logo_dunkel", "logo_dunkel_pfad"))
WEBSEITE_MAX = 300
_FARBEN = ("akzent", "zweitfarbe", "grund", "text")
_SCHRIFTEN = ("schrift_anzeige", "schrift_text")
ABSCHNITT_REIHENFOLGE = ("Wer wir sind", "Zielgruppe", "Ton", "Angebote", "Do & Don'ts",
                         "Fakten und Zahlen", "Bildstil")
_VORSPANN = "Einleitung"

_FARBE = re.compile(r"#[0-9A-Fa-f]{6}")
_LOGO_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:png|jpe?g)", re.IGNORECASE)
_KOMMENTAR = re.compile(r"<!--.*?-->", re.DOTALL)
_UEBERSCHRIFT = re.compile(r"#{1,2} ")
_PNG, _JPEG = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"
_UNGUELTIGER_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_ANZEIGE = {"akzent": "Akzentfarbe", "zweitfarbe": "Zweitfarbe", "grund": "Hintergrundfarbe",
            "text": "Textfarbe"}


class MarkenFehler(Exception):
    """Profil oder Logo konnte nicht geschrieben werden (Meldung ist fuer den Betreiber lesbar)."""


@dataclass
class Profil:
    werte: dict = field(default_factory=dict)       # gueltige Kopfteil-Werte (nur bekannte Schluessel)
    abschnitte: dict = field(default_factory=dict)  # Abschnittsname -> Text
    hinweise: list[str] = field(default_factory=list)
    ordner: str | None = None
    logo_pfad: str | None = None
    logo_dunkel_pfad: str | None = None


# --- Pruefung -------------------------------------------------------------------

def webseite_gueltig(wert: object) -> bool:
    """https-Adresse mit Host, ohne Zugangsdaten und ohne Leerraum (Spec 2026-10-09 §2)."""
    if not isinstance(wert, str) or not wert or len(wert) > WEBSEITE_MAX or any(c.isspace() for c in wert):
        return False
    try:
        teile = urlsplit(wert)
        teile.port                       # wirft ValueError bei kaputtem Port
    except ValueError:
        return False
    return (teile.scheme == "https" and bool(teile.hostname)
            and teile.username is None and teile.password is None)


def _gueltig(schluessel: str, wert: object) -> bool:
    if not isinstance(wert, str):
        return False
    if schluessel in _FARBEN:
        return _FARBE.fullmatch(wert) is not None
    if schluessel in _SCHRIFTEN:
        return wert in REGISTER
    if schluessel in ("logo", "logo_dunkel"):
        return _LOGO_NAME.fullmatch(wert) is not None
    if schluessel == "webseite":
        return webseite_gueltig(wert)
    return bool(wert.strip()) and "\n" not in wert and "\r" not in wert  # stand


def _logo_typ(roh: bytes) -> str | None:
    if roh.startswith(_PNG):
        return "png"
    if roh.startswith(_JPEG):
        return "jpg"
    return None


# --- Lesen ----------------------------------------------------------------------

def _zerlegen(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """(roher Kopfteil, Abschnitte). Kopfteil nur, wenn die erste Zeile `---` ist und er geschlossen wird."""
    zeilen = text.replace("\r\n", "\n").split("\n")
    kopf: dict[str, str] = {}
    rest = zeilen
    if zeilen and zeilen[0].strip() == "---":
        ende = next((i for i in range(1, len(zeilen)) if zeilen[i].strip() == "---"), None)
        if ende is not None:
            for zeile in zeilen[1:ende]:
                schluessel, doppelpunkt, wert = zeile.partition(":")
                if doppelpunkt and schluessel.strip():
                    kopf[schluessel.strip().casefold()] = wert.strip()
            rest = zeilen[ende + 1:]
    abschnitte: dict[str, list[str]] = {}
    aktuell = _VORSPANN
    for zeile in _KOMMENTAR.sub("", "\n".join(rest)).split("\n"):
        if zeile.startswith("## "):
            aktuell = zeile[3:].strip() or _VORSPANN
            abschnitte.setdefault(aktuell, [])
        elif zeile.startswith("# ") and aktuell == _VORSPANN:
            continue  # Dateititel
        else:
            abschnitte.setdefault(aktuell, []).append(zeile)
    fertig = {n: "\n".join(z).strip() for n, z in abschnitte.items()}
    return kopf, {n: t for n, t in fertig.items() if t}


def _marke_pfad(ordner: str) -> str | None:
    """Pfad der Marke.md (egal welche Schreibweise), nur wenn echte Datei im Firmenordner."""
    try:
        for eintrag in sorted(os.scandir(ordner), key=lambda e: e.name):
            if eintrag.name.casefold() == DATEI.casefold():
                pfad = os.path.join(ordner, eintrag.name)
                if os.path.isfile(pfad) and mw._echt(pfad, ordner, os.path.realpath(ordner)):
                    return pfad
    except OSError:
        pass
    return None


def _datei_text(pfad: str) -> str | None:
    try:
        with open(pfad, "rb") as f:
            roh = f.read(_LESE_MAX + 1)
    except OSError:
        return None
    if len(roh) > _LESE_MAX:
        return None
    try:
        return roh.decode("utf-8-sig")
    except UnicodeDecodeError:
        return roh.decode("latin-1")


def _auswerten(profil: Profil, text: str) -> None:
    kopf, profil.abschnitte = _zerlegen(text)
    for schluessel in KOPF_REIHENFOLGE:
        wert = kopf.get(schluessel, "")
        if not wert:
            continue
        if _gueltig(schluessel, wert):
            profil.werte[schluessel] = wert
        else:
            profil.hinweise.append(f"Marke.md: {schluessel} ungültig")
    for schluessel, attribut in _LOGOS:
        name = profil.werte.get(schluessel)
        if not name:
            continue
        ziel = os.path.join(profil.ordner, name) if profil.ordner else None
        if ziel and os.path.isfile(ziel) and mw._echt(ziel, profil.ordner, os.path.realpath(profil.ordner)):
            setattr(profil, attribut, ziel)
        else:
            profil.werte.pop(schluessel)
            profil.hinweise.append(f"Marke.md: {schluessel} ungültig")


def hat_kopfteil(text: str) -> bool:
    """Beginnt der Text mit einem geschlossenen `---`-Kopfteil?"""
    zeilen = str(text or "").replace("\r\n", "\n").lstrip("﻿").split("\n")
    return bool(zeilen) and zeilen[0].strip() == "---" and any(z.strip() == "---" for z in zeilen[1:])


def aus_text(text: str, ordner: str | None = None) -> Profil:
    """Profil aus dem Inhalt einer Marke.md (fuer Leser, die die Datei schon haben). Wirft nie."""
    profil = Profil(ordner=ordner)
    try:
        _auswerten(profil, str(text or ""))
    except Exception:  # wie lesen: nie ein Abbruch
        pass
    return profil


def lesen(wurzel: str, mandant: str, name: str) -> Profil:
    """Profil der Firma. Wirft nie; fehlt Ordner oder Datei, ist das Profil leer."""
    profil = Profil()
    try:
        profil.ordner = mw.ordner_finden(wurzel, mandant, name)
        if profil.ordner is None:
            return profil
        pfad = _marke_pfad(profil.ordner)
        text = _datei_text(pfad) if pfad else None
        if text is None:
            return profil
        _auswerten(profil, text)
    except Exception:  # Lesen darf den Lauf nie stoeren
        pass
    return profil


# --- Text -----------------------------------------------------------------------

def _einzeilig(wert: object) -> str:
    return " ".join(str(wert or "").split())


def text(werte: dict, abschnitte: dict) -> str:
    """Marke.md-Inhalt: Kopfteil (bekannte Schluessel, feste Reihenfolge) und Abschnitte."""
    kopf = [f"{k}: {_einzeilig(werte[k])}" for k in KOPF_REIHENFOLGE if werte.get(k)]
    teile = ["---\n" + "\n".join(kopf) + ("\n" if kopf else "") + "---\n"]
    namen = [n for n in ABSCHNITT_REIHENFOLGE if n in abschnitte]
    namen += [n for n in abschnitte if n not in ABSCHNITT_REIHENFOLGE]
    for n in namen:
        inhalt = str(abschnitte[n] or "").replace("\r\n", "\n").strip()
        if not inhalt:
            continue
        inhalt = "\n".join(" " + z if _UEBERSCHRIFT.match(z) else z for z in inhalt.split("\n"))
        titel = _einzeilig(n).lstrip("# ") or _VORSPANN
        teile.append(f"## {titel}\n{inhalt}\n")
    return "\n".join(teile)


def fuer_prompt(profil: Profil) -> str:
    """Kopfteil als lesbare Zeilen, danach die Abschnitte. Leeres Profil => ''."""
    zeilen = []
    for k, bezeichnung in _ANZEIGE.items():
        if profil.werte.get(k):
            zeilen.append(f"- {bezeichnung} {profil.werte[k]}")
    for k, bezeichnung in (("schrift_anzeige", "Schrift für Überschriften"), ("schrift_text", "Schrift für Text")):
        sid = profil.werte.get(k)
        if sid in REGISTER:
            zeilen.append(f"- {bezeichnung}: {REGISTER[sid]['familie']} ({sid})")
    if profil.logo_pfad:
        zeilen.append("- Logo: vorhanden")
    if profil.logo_dunkel_pfad:
        zeilen.append("- Logo für dunkle Flächen: vorhanden")
    if profil.werte.get("webseite"):
        zeilen.append(f"- Webseite: {profil.werte['webseite']}")
    teile = ["\n".join(zeilen)] if zeilen else []
    teile += [f"## {n}\n{t}" for n, t in profil.abschnitte.items()]
    return "\n\n".join(teile)


# --- Schreiben ------------------------------------------------------------------

def _ordner_fuer_schreiben(wurzel: str, mandant: str, name: str) -> str:
    if not name or name in (".", "..") or _UNGUELTIGER_NAME.search(name) or name != name.strip():
        raise MarkenFehler(f"Ungültiger Firmenname: {name!r}")
    ordner = mw.ordner_finden(wurzel, mandant, name)
    if ordner is not None:
        return ordner
    pfad = os.path.join(wurzel, name)
    if os.path.lexists(pfad):
        raise MarkenFehler(f"Firmenordner companys/{name} ist kein gewöhnlicher Ordner")
    try:
        os.makedirs(wurzel, exist_ok=True)
        os.mkdir(pfad)
    except OSError as e:
        raise MarkenFehler(f"Firmenordner companys/{name} nicht anlegbar: {e}") from e
    ordner = mw.ordner_finden(wurzel, mandant, name)
    if ordner is None:
        raise MarkenFehler(f"Firmenordner companys/{name} ist kein gewöhnlicher Ordner")
    return ordner


def _ablegen_exklusiv(pfad: str, roh: bytes) -> bool:
    try:
        f = open(pfad, "xb")
    except FileExistsError:
        return False
    try:
        with f:
            f.write(roh)
    except OSError:
        try:
            os.remove(pfad)
        except OSError:
            pass
        raise
    return True


def _ersetzen(pfad: str, roh: bytes) -> None:
    """Eindeutige temp-Datei im selben Ordner + replace; das Ziel wird nie halb geschrieben."""
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(pfad), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
        os.replace(temp, pfad)
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def _verlauf_sichern(ordner: str, firma_real: str, alt: str, jetzt: datetime.datetime) -> None:
    ziel = os.path.join(ordner, VERLAUF)
    try:
        os.mkdir(ziel)
    except FileExistsError:
        pass
    if not os.path.isdir(ziel) or not mw._echt(ziel, ordner, firma_real):
        raise MarkenFehler(f"{VERLAUF} ist kein gewöhnlicher Ordner")
    with open(alt, "rb") as f:
        roh = f.read()
    basis = f"{jetzt:%Y-%m-%d-%H%M}"
    for nummer in range(1, 1000):
        pfad = os.path.join(ziel, f"{basis}.md" if nummer == 1 else f"{basis}-{nummer}.md")
        if mw._echt(pfad, ziel, firma_real) and _ablegen_exklusiv(pfad, roh):
            return
    raise MarkenFehler("Verlauf konnte nicht gesichert werden")


def _bild_pruefen(roh: bytes) -> None:
    """Signatur reicht nicht: das Bild muss sich oeffnen lassen und darf nicht riesig sein."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(roh))
        if img.width * img.height > MAX_PIXEL:
            raise MarkenFehler("Das Logo hat zu viele Bildpunkte")
        img.verify()
    except MarkenFehler:
        raise
    except Exception as e:
        raise MarkenFehler("Das Logo ist keine lesbare PNG- oder JPEG-Datei") from e


def _logo_roh(logo, wofuer: str) -> tuple[bytes | None, str | None]:
    if logo is None:
        return None, None
    roh = logo[0]
    if not isinstance(roh, (bytes, bytearray)) or not roh:
        raise MarkenFehler(f"{wofuer} ist keine PNG- oder JPEG-Datei")
    roh = bytes(roh)
    if len(roh) > LOGO_MAX_BYTES:
        raise MarkenFehler(f"{wofuer} ist größer als 2 MB")
    art = _logo_typ(roh)
    if art is None:
        raise MarkenFehler(f"{wofuer} ist keine PNG- oder JPEG-Datei")
    _bild_pruefen(roh)
    return roh, art


def schreiben(wurzel: str, mandant: str, name: str, werte: dict, abschnitte: dict,
              logo: tuple[bytes, str] | None, von: str, jetzt: datetime.datetime, *,
              logo_dunkel: tuple[bytes, str] | None = None) -> str:
    """Marke.md (und Logo) schreiben -> Pfad der Marke.md. Wirft MarkenFehler.

    Pruefungen (Werte, Logo) laufen vor dem ersten Schreiben. Die bisherige Marke.md
    wandert nach Marke-Verlauf/JJJJ-MM-TT-HHMM.md. `stand` setzt diese Funktion selbst.
    """
    werte = dict(werte or {})
    for k, v in list(werte.items()):
        if k not in KOPF_REIHENFOLGE:
            del werte[k]
        elif v in (None, ""):
            del werte[k]
        elif k != "stand" and not _gueltig(k, v):
            raise MarkenFehler(f"Wert für {k} ungültig")
    logo_roh, logo_art = _logo_roh(logo, "Das Logo")
    dunkel_roh, dunkel_art = _logo_roh(logo_dunkel, "Das dunkle Logo")
    if logo_roh is not None:
        werte["logo"] = f"logo.{logo_art}"
        if dunkel_roh is None:
            werte.pop("logo_dunkel", None)    # neues Logo: eine alte dunkle Fassung passt nicht mehr dazu
    if dunkel_roh is not None:
        werte["logo_dunkel"] = f"logo-dunkel.{dunkel_art}"
    werte["stand"] = f"{jetzt:%Y-%m-%d %H:%M} von {_einzeilig(von) or 'unbekannt'}"
    inhalt = text(werte, abschnitte or {}).encode("utf-8")

    ordner = _ordner_fuer_schreiben(wurzel, mandant, name)
    firma_real = os.path.realpath(ordner)
    marke = os.path.join(ordner, DATEI)
    try:
        if os.path.lexists(marke) and not (os.path.isfile(marke) and mw._echt(marke, ordner, firma_real)):
            raise MarkenFehler(f"{DATEI} ist keine gewöhnliche Datei")
        dateien = [(os.path.join(ordner, f"{basis}.{art}"), roh, basis, art)
                   for basis, roh, art in (("logo", logo_roh, logo_art), ("logo-dunkel", dunkel_roh, dunkel_art))
                   if roh is not None]
        for ziel, *_ in dateien:
            if os.path.lexists(ziel) and not mw._echt(ziel, ordner, firma_real):
                raise MarkenFehler("Logo-Datei ist eine Verknüpfung")
        if os.path.isfile(marke):
            _verlauf_sichern(ordner, firma_real, marke, jetzt)
        for ziel, roh, _, _ in dateien:
            _ersetzen(ziel, roh)
        _ersetzen(marke, inhalt)
        for _, _, basis, art in dateien:      # erst jetzt, wo Marke.md die neue Datei nennt
            for endung in ("png", "jpg"):
                altes = os.path.join(ordner, f"{basis}.{endung}")
                if endung != art and os.path.lexists(altes):
                    os.remove(altes)
    except OSError as e:
        raise MarkenFehler(f"Marke.md konnte nicht geschrieben werden: {e}") from e
    return marke


# --- Spiegel --------------------------------------------------------------------

def _hat_alpha(img) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def _spiegel_logo(roh: bytes) -> str | None:
    """Verkleinerte data-URL (laengste Kante <= 600, <= 140 KB) oder None."""
    if not roh or len(roh) > LOGO_MAX_BYTES or _logo_typ(roh) is None:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(roh))
        if img.width * img.height > MAX_PIXEL:
            return None
        img.load()
        alpha = _hat_alpha(img)
        img = img.convert("RGBA" if alpha else "RGB")
        img.thumbnail((SPIEGEL_KANTE, SPIEGEL_KANTE), Image.LANCZOS)  # vergroessert nie
        while True:
            buf = io.BytesIO()
            if alpha:
                img.save(buf, "PNG", optimize=True)
                kopf = "data:image/png;base64,"
            else:
                img.save(buf, "JPEG", quality=85, optimize=True)
                kopf = "data:image/jpeg;base64,"
            url = kopf + base64.b64encode(buf.getvalue()).decode("ascii")
            if len(url) <= SPIEGEL_MAX_CHARS:
                return url
            breite, hoehe = img.size
            if max(breite, hoehe) <= _MIN_KANTE:
                return None
            img = img.resize((max(1, int(breite * 0.8)), max(1, int(hoehe * 0.8))), Image.LANCZOS)
    except Exception:  # kaputtes Bild: nicht spiegeln
        return None


def gestalt(werte: dict, logo: bytes | None, logo_typ: str | None, logo_dunkel: bytes | None = None) -> dict:
    """Spiegel-Gestalt fuer die VM: akzent, flaeche (= zweitfarbe), logo (data-URL), schriften.

    Ungueltige oder fehlende Werte fehlen im Ergebnis. Die Profildatei behaelt das
    Original-Logo; `logo_typ` ist nur ein Hinweis, die Art folgt aus dem Inhalt.
    """
    werte = werte if isinstance(werte, dict) else {}
    ergebnis: dict = {}
    if _gueltig("akzent", werte.get("akzent")):
        ergebnis["akzent"] = werte["akzent"]
    if _gueltig("zweitfarbe", werte.get("zweitfarbe")):
        ergebnis["flaeche"] = werte["zweitfarbe"]
    if logo:
        url = _spiegel_logo(bytes(logo))
        if url:
            ergebnis["logo"] = url
            # None entfernt eine alte dunkle Fassung im Spiegel (die DB loescht Schluessel mit JSON-null)
            ergebnis["logo_dunkel"] = _spiegel_logo(bytes(logo_dunkel)) if logo_dunkel else None
    # Nur als vollstaendiges Paar: die VM verlangt genau anzeige UND text und lehnte sonst die ganze
    # Gestalt ab - ein kaputter Schriftwert blockierte dann auch gueltige Farben (I2).
    if _gueltig("schrift_anzeige", werte.get("schrift_anzeige")) and _gueltig("schrift_text", werte.get("schrift_text")):
        ergebnis["schriften"] = {"anzeige": werte["schrift_anzeige"], "text": werte["schrift_text"]}
    return ergebnis
