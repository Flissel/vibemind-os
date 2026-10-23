"""Die 8 Collage-Vorlagen mit dem Assistenten erzeugen (Freie Flaeche, Stufe 5) und als Vorschlag speichern.

Laeuft AUF DER VM (~/marketing-os, venv): lokale API, DB ueber SUPABASE_DB_CONTAINER. Je Grundform: Probe-Collage der
Firma vibemind anlegen, eine Chat-Runde mit dem Auftrag starten, auf das Rundenende (chat_auftraege) und auf die
erzeugten Bilder warten (Fassung 3 Minuten stabil), die neueste Seite holen, Rollen mit der vibemind-Marke ableiten,
als Vorschlag (fuer_alle) speichern und die Probe loeschen. Der Betreiber gibt in sales frei.

Zeitlimits (Befund 21.10.2026): FLUX.2 braucht am PC 2-8 Minuten je Bild, der Bild-Arbeiter rechnet einen Auftrag
nach dem anderen. Darum wartet jede Vorlage erst, bis die Bildwarteschlange (alle Inhalte) frei ist, und dann
BILD_JE_AUFTRAG je Bildauftrag ihrer Probe (hoechstens BILDER_MAX). Endet die Chat-Runde mit Fehler, fragt das Skript
einmal neu und nennt die Antwort des Arbeiters. --behalten laesst die Probe zur Fehlersuche stehen.

Vorlagen, die es schon gibt, werden uebersprungen (die Entscheidung des Betreibers bleibt); --ersetzen erzeugt sie neu
als neue Fassung - auch dann behaelt eine bestehende Vorlage ihren Status (pult_vorlage_seite_speichern, 082). Endet
ein Bildauftrag der Probe mit Fehler, wird die Vorlage nicht gespeichert.

    python -m spaces.marketing.scripts.collage_vorlagen_erzeugen [--nur <slug>] [--ersetzen] [--trocken]
        [--behalten] [--api http://127.0.0.1:5510]

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

from spaces.marketing.claw.seite_prompt import GESTALTUNG, GRUNDFORMEN

MANDANT = "vibemind"
VON = "collage-vorlagen"
RUNDE_MAX = 20 * 60        # Sekunden je Chat-Runde
BILD_JE_AUFTRAG = 12 * 60  # Sekunden je Bildauftrag der Probe (FLUX.2 am PC: 2-8 min je Bild, dazu Prompt/Laden)
BILDER_MAX = 60 * 60       # hoechstens so lange auf die Bilder einer Probe
WARTESCHLANGE_MAX = 60 * 60   # so lange auf eine freie Bildwarteschlange vor der naechsten Probe
RUNDE_NEUVERSUCHE = 1      # eine Chat-Runde mit Status fehler wird so oft neu gestartet
NEUVERSUCH_PAUSE = 5 * 60  # vorher warten: der Waechter startet eine eingefrorene Modell-Tuer neu (live ~8 min)
ANTWORT_MAX = 200          # Zeichen der Arbeiter-Antwort in einer Fehlermeldung
STABIL = 3 * 60            # Fassung so lange unveraendert
TAKT = 10                  # Sekunden zwischen zwei Abfragen
LOESCH_VERSUCHE = 6        # am Ende, solange Bilder noch laufen

# GRUNDFORMEN (slug, Name, Aufbau) kommen aus claw/seite_prompt - derselbe Text steht im Prompt des Assistenten.
# Der Auftrag nimmt die ausfuehrlichere GESTALTUNG je slug (Geste, Farbe, Masse; gleiche Quelle, nicht im Prompt).
GRUNDFORMEN_NACH_SLUG = {g[0]: g for g in GRUNDFORMEN}


def auftrag(name: str, aufbau: str) -> str:
    """Der Chat-Auftrag (höchstens 2000 Zeichen) für eine Grundform."""
    return (f"Gestalte eine Collage-Vorlage in der Grundform „{name}“. Thema: „Vorlage: {name}“. "
            f"Aufbau: {aufbau} "
            "Verwende Platzhalter-Inhalte: Dachzeile „Dachzeile“, Überschrift „Überschrift“, ein Fließtext „Kurzer "
            "Einleitungstext, der das Thema in zwei Sätzen anreißt.“ und einen Knopf „Mehr erfahren“; sieht die "
            "Grundform ein zweites kurzes Element vor (Datum, Name, Zitat, Notiz oder „Neu“-Sticker), setze es mit "
            "einem kurzen Platzhalter. "
            "Lege die Motive per bild_erzeugen an, mit einer neutralen, branchenoffenen Beschreibung (zum Beispiel "
            "„helles Atelier mit Pflanzen“ statt eines Produkts). Setze das Logo per logo_einsetzen. "
            "Haltung: Magazin-Niveau, eine prägende Geste, Farbe mit Absicht, mutig statt Baukasten. "
            "Hochwertig, frei, nicht gestapelt: Ebenen überlappen sich mit Absicht um das genannte Maß, ein klarer "
            "Aufmacher, asymmetrisch, nichts steht mittig untereinander; Text bleibt in der Ruhezone.")


class LaufFehler(Exception):
    """Ein Schritt ist fehlgeschlagen oder hat das Zeitlimit gerissen (Meldung ohne Werte, ohne Kopfzeilen)."""


class RundeFehler(LaufFehler):
    """Die Chat-Runde endete mit Status fehler; antwort = Text des Arbeiters (gekappt, einzeilig)."""

    def __init__(self, antwort: str):
        self.antwort = antwort
        super().__init__("Chat-Runde endete mit Status fehler" + (f": {antwort}" if antwort else ""))


def _einzeilig(text, grenze: int = ANTWORT_MAX) -> str:
    kurz = " ".join(str(text or "").split())
    return kurz if len(kurz) <= grenze else kurz[:grenze - 1] + "…"


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
        z = u.abfrage("SELECT status, antwort FROM marketing.chat_auftraege "
                      f"WHERE inhalt = {_lit(iid)}::uuid AND art = 'chat' ORDER BY erstellt_am DESC LIMIT 1")
        status = z[0].get("status") if z else None
        if status == "fertig":
            return
        if status == "fehler":
            raise RundeFehler(_einzeilig(z[0].get("antwort")))
        if u.zeit() >= ende:
            raise LaufFehler("Zeitlimit der Chat-Runde (20 Minuten)")
        u.warten(TAKT)


def _bilder_limit(n: int) -> int:
    """Sekunden auf die Bilder einer Probe mit n Bildauftraegen: BILD_JE_AUFTRAG je Auftrag, hoechstens BILDER_MAX."""
    return min(BILDER_MAX, max(1, n) * BILD_JE_AUFTRAG)


def _bilder_abwarten(u: Umgebung, iid: str) -> None:
    """Wartet, bis kein Bildauftrag offen/in Arbeit ist und die neueste Fassung 3 Minuten stabil bleibt. Das Limit
    waechst mit der Zahl der Bildauftraege der Probe (Pruefrunden koennen welche nachlegen)."""
    beginn = u.zeit()
    letzte, seit = None, beginn
    while True:
        offen = u.abfrage("SELECT count(*)::int AS n FROM marketing.bild_auftraege "
                          f"WHERE inhalt = {_lit(iid)}::uuid AND status IN ('offen','in_arbeit')")
        f = u.abfrage(f"SELECT max(fassung)::int AS f FROM marketing.inhalt_fassungen WHERE inhalt = {_lit(iid)}::uuid")
        g = u.abfrage("SELECT count(*)::int AS gesamt FROM marketing.bild_auftraege "
                      f"WHERE inhalt = {_lit(iid)}::uuid")
        n = int((g[0].get("gesamt") if g else 0) or 0)
        fassung = f[0].get("f") if f else None
        jetzt = u.zeit()
        if int(offen[0]["n"]) > 0 or fassung != letzte:
            letzte, seit = fassung, jetzt
        elif jetzt - seit >= STABIL:
            return
        limit = _bilder_limit(n)
        if jetzt - beginn >= limit:
            raise LaufFehler(f"Zeitlimit für die Bilder ({limit // 60} Minuten, {n} Aufträge)")
        u.warten(TAKT)


def _warteschlange_abwarten(u: Umgebung) -> None:
    """Vor jeder Probe: kein Bildauftrag (irgendeines Inhalts) offen oder in Arbeit. Der Bild-Arbeiter rechnet einen
    nach dem anderen - die Bilder der vorigen Probe wuerden sonst die Zeit der naechsten aufbrauchen."""
    ende = u.zeit() + WARTESCHLANGE_MAX
    while True:
        z = u.abfrage("SELECT count(*)::int AS n FROM marketing.bild_auftraege WHERE status IN ('offen','in_arbeit')")
        if not z or int(z[0].get("n") or 0) == 0:
            return
        if u.zeit() >= ende:
            raise LaufFehler(f"Bildwarteschlange nach {WARTESCHLANGE_MAX // 60} Minuten nicht frei – "
                             "Probe nicht angelegt")
        u.warten(TAKT)


def _runde(u: Umgebung, iid: str, nachricht: str, ausgabe: Callable[[str], None]) -> None:
    """Chat-Runde starten und abwarten; endet sie mit Status fehler, genau RUNDE_NEUVERSUCHE neue Versuche."""
    for versuch in range(RUNDE_NEUVERSUCHE + 1):
        u.api("POST", f"/api/pult/inhalte/{iid}/chat",
              {"nachricht": nachricht, "kontext": {"fenster": "seite", "auswahl": []}})
        try:
            _runde_abwarten(u, iid)
            return
        except RundeFehler as e:
            if versuch >= RUNDE_NEUVERSUCHE:
                raise
            ausgabe(f"Chat-Runde mit Fehler, Neuversuch in {NEUVERSUCH_PAUSE // 60} Minuten: "
                    f"{e.antwort or 'ohne Antwort'}")
            u.warten(NEUVERSUCH_PAUSE)


def _loeschen(u: Umgebung, iid: str) -> bool:
    try:
        u.api("POST", f"/api/pult/inhalte/{iid}/loeschen", {"mandant": MANDANT, "von": VON})
        return True
    except LaufFehler:
        return False


def vorlage_erzeugen(u: Umgebung, slug: str, offene_proben: list[str], ausgabe: Callable[[str], None],
                     behalten: bool = False) -> None:
    """Eine Vorlage erzeugen. Eine Probe, die sich nicht loeschen laesst (Bilder laufen noch), landet in
    offene_proben und wird am Ende des Laufs erneut versucht. behalten: die Probe bleibt stehen (Fehlersuche)."""
    _, name, aufbau = GRUNDFORMEN_NACH_SLUG[slug]
    _warteschlange_abwarten(u)
    neu = u.api("POST", "/api/pult/inhalte/collage", {"mandant": MANDANT, "titel": f"Vorlage: {name}"})
    iid = _uuid(neu.get("inhalt"))
    try:
        _runde(u, iid, auftrag(name, GESTALTUNG.get(slug, aufbau)), ausgabe)
        _bilder_abwarten(u, iid)
        kaputt = u.abfrage("SELECT count(*)::int AS n FROM marketing.bild_auftraege "
                           f"WHERE inhalt = {_lit(iid)}::uuid AND status = 'fehler'")
        if kaputt and int(kaputt[0].get("n") or 0) > 0:
            raise LaufFehler("Ein Bildauftrag endete mit Fehler – Vorlage nicht gespeichert")
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
        if behalten:
            ausgabe(f"Probe behalten (--behalten, bitte später von Hand löschen): {iid}")
        elif not _loeschen(u, iid):
            offene_proben.append(iid)


def _vorhanden(u: Umgebung, slugs: list[str]) -> set[str]:
    """Slugs, deren Vorlage collage-<slug> es schon gibt."""
    namen = ", ".join(_lit("collage-" + s) for s in slugs)
    zeilen = u.abfrage(f"SELECT name FROM marketing.newsletter_vorlagen WHERE name IN ({namen})")
    da = {str(z.get("name")) for z in zeilen}
    return {s for s in slugs if "collage-" + s in da}


def lauf(u: Umgebung, nur: str | None = None, trocken: bool = False, ausgabe: Callable[[str], None] = print,
         ersetzen: bool = False, behalten: bool = False) -> int:
    if nur is not None and nur not in GRUNDFORMEN_NACH_SLUG:
        ausgabe(f"Unbekannter Slug: {nur} (möglich: {', '.join(GRUNDFORMEN_NACH_SLUG)})")
        return 2
    slugs = [nur] if nur else [g[0] for g in GRUNDFORMEN]
    if trocken:
        for s in slugs:
            ausgabe(f"[{s}] {auftrag(GRUNDFORMEN_NACH_SLUG[s][1], GESTALTUNG.get(s, GRUNDFORMEN_NACH_SLUG[s][2]))}")
        return 0
    try:
        da = set() if ersetzen else _vorhanden(u, slugs)
    except Exception as e:                          # nie die Meldung: sie kann Kopfzeilen oder Werte enthalten
        ausgabe(f"FEHLER Bestand der Vorlagen: {type(e).__name__}")
        return 1
    fehler, offene = 0, []
    try:
        for s in slugs:
            if s in da:
                ausgabe(f"übersprungen: collage-{s} gibt es schon (--ersetzen erzeugt sie neu)")
                continue
            try:
                vorlage_erzeugen(u, s, offene, ausgabe, behalten=behalten)
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
    finally:            # auch bei Abbruch (Strg+C, Fehler beim Abfragen): uebrige Proben immer nennen
        for i in offene:
            ausgabe(f"Probe nicht gelöscht (bitte von Hand): {i}")
    return 1 if fehler or offene else 0


SCHLUESSEL_FEHLT = "MARKETING_PULT_KEY fehlt in der Umgebung – ohne Pult-Schlüssel läuft nichts"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:5510")
    p.add_argument("--nur", default=None, help="nur diese Vorlage (Slug)")
    p.add_argument("--ersetzen", action="store_true", help="bestehende Vorlagen neu erzeugen (Status bleibt)")
    p.add_argument("--trocken", action="store_true", help="nur die Aufträge zeigen")
    p.add_argument("--behalten", action="store_true", help="Proben nicht löschen (Fehlersuche)")
    a = p.parse_args(argv)
    if not a.trocken and not os.environ.get("MARKETING_PULT_KEY", "").strip():
        print(SCHLUESSEL_FEHLT)
        return 2
    return lauf(Umgebung(a.api), a.nur, a.trocken, ersetzen=a.ersetzen, behalten=a.behalten)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
