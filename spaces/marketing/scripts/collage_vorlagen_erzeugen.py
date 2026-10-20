"""Die 8 Collage-Vorlagen mit dem Assistenten erzeugen (Freie Flaeche, Stufe 5) und als Vorschlag speichern.

Laeuft AUF DER VM (~/marketing-os, venv): lokale API, DB ueber SUPABASE_DB_CONTAINER. Je Grundform: Probe-Collage der
Firma vibemind anlegen, eine Chat-Runde mit dem Auftrag starten, auf das Rundenende (chat_auftraege) und auf die
erzeugten Bilder warten (Fassung 3 Minuten stabil, hoechstens 10), die neueste Seite holen, Rollen mit der
vibemind-Marke ableiten, als Vorschlag (fuer_alle) speichern und die Probe loeschen. Der Betreiber gibt in sales frei.

    python -m spaces.marketing.scripts.collage_vorlagen_erzeugen [--nur <slug>] [--trocken] [--api http://127.0.0.1:5510]

Der Pult-Schluessel kommt aus MARKETING_PULT_KEY (Umgebung, mit strip), nie von der Kommandozeile. Fehlermeldungen
nennen nur den Typ, nie Kopfzeilen oder Werte.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Callable

MANDANT = "vibemind"
VON = "collage-vorlagen"
RUNDE_MAX = 20 * 60        # Sekunden je Chat-Runde
BILDER_MAX = 10 * 60       # Sekunden auf erzeugte Bilder
STABIL = 3 * 60            # Fassung so lange unveraendert
TAKT = 10                  # Sekunden zwischen zwei Abfragen
LOESCH_VERSUCHE = 6        # am Ende, solange Bilder noch laufen

# (slug, Name, Aufbau) - Aufbau nach GRUNDFORMEN in claw/seite_prompt.py (dort nicht als Konstante exportiert)
GRUNDFORMEN: list[tuple[str, str, str]] = [
    ("split", "Split",
     "Aufmacher-Bild links angeschnitten, 60 % Breite (x 180, y 320, 360x640); Titel rechts oben (x 470, y 180) "
     "greift ins Bild; Text darunter."),
    ("randloser-aufmacher", "Randloser Aufmacher mit Titelband",
     "Bild x 300, y 280, 600x560 randlos; der Titel steht auf einer Farbfläche über der unteren Bildkante."),
    ("schrift-plakat", "Schrift-Plakat",
     "Aufmacher ist der Titel (groesse 90..130), volle Breite innerhalb der Ruhezone (x 300, breite 552); kleines "
     "Bild darunter rechts versetzt."),
    ("versetztes-bildpaar", "Versetztes Zwei-Bild-Raster",
     "Bild A x 220, y 300; Bild B x 420, y 560 überlappt A um 40; Text in der Lücke."),
    ("polaroid-streuung", "Polaroid-Streuung",
     "2-3 Fotos leicht überlappend, höchstens 2 davon mit rahmen_breite 10, schatten und drehung 2..6; Titel darüber."),
    ("farbbuehne-freisteller", "Farbbühne mit freigestelltem Motiv",
     "Farbkreis hinter freigestelltem Produkt (passung einpassen), Titel daneben."),
    ("magazin-titel", "Magazin-Titel",
     "Großer Titel wie ein Zeitschriftenkopf oben, darunter ein randloses Titelbild, das den Titel an einer Kante "
     "überlappt; Dachzeile und kurzer Text am Rand, Knopf unten."),
    ("zitat-buehne", "Zitat-Bühne",
     "Ein großes Zitat als Aufmacher auf einer Farbfläche, ein kleines Bild versetzt daneben, Name des Zitierten "
     "als Dachzeile, Knopf am Ende."),
]
GRUNDFORMEN_NACH_SLUG = {g[0]: g for g in GRUNDFORMEN}


def auftrag(name: str, aufbau: str) -> str:
    """Der Chat-Auftrag (höchstens 2000 Zeichen) für eine Grundform."""
    return (f"Gestalte eine Collage-Vorlage in der Grundform „{name}“. Thema: „Vorlage: {name}“. "
            f"Aufbau: {aufbau} "
            "Verwende Platzhalter-Inhalte: Überschrift „Überschrift“, ein Fließtext „Kurzer Einleitungstext, der "
            "das Thema in zwei Sätzen anreißt.“ und einen Knopf „Mehr erfahren“. "
            "Lege die Motive per bild_erzeugen an, mit einer neutralen, branchenoffenen Beschreibung (zum Beispiel "
            "„helles Atelier mit Pflanzen“ statt eines Produkts). Setze das Logo per logo_einsetzen. "
            "Hochwertig, frei, nicht gestapelt: Ebenen dürfen sich mit Absicht überlappen, nichts steht mittig "
            "untereinander.")


class LaufFehler(Exception):
    """Ein Schritt ist fehlgeschlagen oder hat das Zeitlimit gerissen (Meldung ohne Werte, ohne Kopfzeilen)."""


class Umgebung:
    """Alles, was nach aussen greift (API, DB, Marke, Uhr) - in Tests ersetzt."""

    def __init__(self, api: str):
        self.api_basis = api.rstrip("/")
        self.zeit = time.monotonic
        self.warten = time.sleep

    def api(self, methode: str, pfad: str, koerper: dict | None = None) -> dict:
        daten = json.dumps(koerper).encode() if koerper is not None else None
        schluessel = os.environ.get("MARKETING_PULT_KEY", "").strip()
        try:
            req = urllib.request.Request(self.api_basis + pfad, data=daten, method=methode,
                                         headers={"X-Pult-Key": schluessel, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as antwort:
                return json.loads(antwort.read())
        except urllib.error.HTTPError as e:
            raise LaufFehler(f"{methode} {pfad}: HTTP {e.code}") from None
        except (ValueError, OSError) as e:
            raise LaufFehler(f"{methode} {pfad}: {type(e).__name__}") from None   # nie die Meldung: Kopfzeilen

    def abfrage(self, sql: str) -> list[dict]:
        from spaces.marketing.sync import _db
        return _db.query_via_docker(sql, streng=True)

    def marke(self) -> dict:
        """Marke wie beim Fuellen einer Vorlage (api/pult), damit rollen_ableiten dieselben Farben sieht."""
        from spaces.marketing.api import pult
        return pult._marke_fuer_vorlage(MANDANT, pult._laden_lesen(MANDANT, webseite=True), pult._erzeugt_ordner())


def _lit(wert) -> str:
    from spaces.marketing.sync import _db
    return _db._sql_literal(wert)


def _uuid(wert) -> str:
    if not isinstance(wert, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", wert):
        raise LaufFehler("Unerwartete Inhalts-ID")
    return wert


def _runde_abwarten(u: Umgebung, iid: str) -> None:
    ende = u.zeit() + RUNDE_MAX
    while True:
        z = u.abfrage("SELECT status FROM marketing.chat_auftraege "
                      f"WHERE inhalt = {_lit(iid)}::uuid AND art = 'chat' ORDER BY erstellt_am DESC LIMIT 1")
        status = z[0].get("status") if z else None
        if status == "fertig":
            return
        if status in ("fehler", "gestoppt"):
            raise LaufFehler(f"Chat-Runde endete mit Status {status}")
        if u.zeit() >= ende:
            raise LaufFehler("Zeitlimit der Chat-Runde (20 Minuten)")
        u.warten(TAKT)


def _bilder_abwarten(u: Umgebung, iid: str) -> None:
    """Wartet, bis kein Bildauftrag offen/in Arbeit ist und die neueste Fassung 3 Minuten stabil bleibt."""
    ende = u.zeit() + BILDER_MAX
    letzte, seit = None, u.zeit()
    while True:
        offen = u.abfrage("SELECT count(*)::int AS n FROM marketing.bild_auftraege "
                          f"WHERE inhalt = {_lit(iid)}::uuid AND status IN ('offen','in_arbeit')")
        f = u.abfrage(f"SELECT max(fassung)::int AS f FROM marketing.inhalt_fassungen WHERE inhalt = {_lit(iid)}::uuid")
        fassung = f[0].get("f") if f else None
        jetzt = u.zeit()
        if int(offen[0]["n"]) > 0 or fassung != letzte:
            letzte, seit = fassung, jetzt
        elif jetzt - seit >= STABIL:
            return
        if jetzt >= ende:
            raise LaufFehler("Zeitlimit für die Bilder (10 Minuten)")
        u.warten(TAKT)


def _loeschen(u: Umgebung, iid: str) -> bool:
    try:
        u.api("POST", f"/api/pult/inhalte/{iid}/loeschen", {"mandant": MANDANT, "von": VON})
        return True
    except LaufFehler:
        return False


def vorlage_erzeugen(u: Umgebung, slug: str, offene_proben: list[str], ausgabe: Callable[[str], None]) -> None:
    """Eine Vorlage erzeugen. Eine Probe, die sich nicht loeschen laesst (Bilder laufen noch), landet in
    offene_proben und wird am Ende des Laufs erneut versucht."""
    _, name, aufbau = GRUNDFORMEN_NACH_SLUG[slug]
    neu = u.api("POST", "/api/pult/inhalte/collage", {"mandant": MANDANT, "titel": f"Vorlage: {name}"})
    iid = _uuid(neu.get("inhalt"))
    try:
        u.api("POST", f"/api/pult/inhalte/{iid}/chat",
              {"nachricht": auftrag(name, aufbau), "kontext": {"fenster": "seite", "auswahl": []}})
        _runde_abwarten(u, iid)
        _bilder_abwarten(u, iid)
        dok = u.api("GET", f"/api/pult/inhalte/{iid}")
        fassungen = dok.get("fassungen") or []
        seite = fassungen[0].get("seite") if fassungen else None
        if not isinstance(seite, dict):
            raise LaufFehler("Neueste Fassung ohne Seite")
        auftraege = u.abfrage("SELECT platz, hinweis FROM marketing.bild_auftraege "
                              f"WHERE inhalt = {_lit(iid)}::uuid AND platz IS NOT NULL ORDER BY erstellt_am")
        from spaces.marketing.claw import seite_vorlagen
        rollen = seite_vorlagen.rollen_ableiten(seite, u.marke(), auftraege)
        u.abfrage("SELECT marketing.pult_vorlage_seite_speichern("
                  f"{_lit('collage-' + slug)}, {_lit('Collage-Vorlage ' + name)}, "
                  f"{_lit(json.dumps(seite, ensure_ascii=False))}::jsonb, "
                  f"{_lit(json.dumps(rollen, ensure_ascii=False))}::jsonb, {_lit(VON)}, 'vorschlag', true) AS fassung")
        ausgabe(f"ok: collage-{slug} als Vorschlag gespeichert")
    finally:
        if not _loeschen(u, iid):
            offene_proben.append(iid)


def lauf(u: Umgebung, nur: str | None = None, trocken: bool = False, ausgabe: Callable[[str], None] = print) -> int:
    if nur is not None and nur not in GRUNDFORMEN_NACH_SLUG:
        ausgabe(f"Unbekannter Slug: {nur} (möglich: {', '.join(GRUNDFORMEN_NACH_SLUG)})")
        return 2
    slugs = [nur] if nur else [g[0] for g in GRUNDFORMEN]
    if trocken:
        for s in slugs:
            ausgabe(f"[{s}] {auftrag(GRUNDFORMEN_NACH_SLUG[s][1], GRUNDFORMEN_NACH_SLUG[s][2])}")
        return 0
    fehler, offene = 0, []
    for s in slugs:
        try:
            vorlage_erzeugen(u, s, offene, ausgabe)
        except LaufFehler as e:
            fehler += 1
            ausgabe(f"FEHLER {s}: {e}")
        except Exception as e:                      # nie die Meldung: sie kann Kopfzeilen oder Werte enthalten
            fehler += 1
            ausgabe(f"FEHLER {s}: {type(e).__name__}")
    for _ in range(LOESCH_VERSUCHE):
        if not offene:
            break
        u.warten(30)
        offene[:] = [i for i in offene if not _loeschen(u, i)]
    for i in offene:
        ausgabe(f"Probe nicht gelöscht (bitte von Hand): {i}")
    return 1 if fehler or offene else 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:5510")
    p.add_argument("--nur", default=None, help="nur diese Vorlage (Slug)")
    p.add_argument("--trocken", action="store_true", help="nur die Aufträge zeigen")
    a = p.parse_args(argv)
    return lauf(Umgebung(a.api), a.nur, a.trocken)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
