"""Rowboat-Abholer: holt Auftraege `an = rowboat` aus dem Auftragsbuch und stellt die Punkte in Rowboat ein.

Kein Sprachmodell. Je Takt (2 s) ein `rowboat_empfang_naechster` mit Leihdauer 2 Minuten; ein geholter
Auftrag endet genau einmal mit `rowboat_empfang_abschliessen` (fertig) oder `rowboat_empfang_fehler`
(fester Grund), ohne zweiten Versuch. Rowboat wird nur ueber `todo:get`, `todo:plannerSignals` und
`todo:addSuggestions` angesprochen (enger Schluessel `gehirn-abholer`).

Log (Logger `auftragsbuch`): gekuerzte Auftragsnummer, Art, Anzahlen, Stand und feste Gruende; nie
Titel, Links, Punkt-Kennungen, Schluessel oder DSN.

CLI: ``python -m spaces.auftragsbuch.rowboat.abholer`` (Dienst) und ``... --gesund`` (Healthcheck:
Exit 0, wenn der Herzschlag juenger als 30 s ist). Umgebung: ABHOLER_DSN_FILE, ABHOLER_LADEN,
ROWBOAT_RPC_URL, ROWBOAT_SCHLUESSEL_DATEI (Pflicht), ABHOLER_ARTEN_FREI (Vorgabe
`vorschlaege_anlegen`, leer schaltet ab), ABHOLER_HERZSCHLAG_DATEI (Vorgabe
/tmp/rowboat-abholer.herzschlag), AUFTRAGSBUCH_LADEN (falls gesetzt, muss er gleich sein).
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from spaces.auftragsbuch.rowboat.abgleich import einordnen, nach_einstellen, signale_aus, todo_texte
from spaces.auftragsbuch.rowboat.empfang import Empfang
from spaces.auftragsbuch.rowboat.rpc import GRUND_UNERWARTET, RowboatFehler
from spaces.auftragsbuch.rowboat.text import vorschlag
from spaces.auftragsbuch.vertrag import Auftragsart, FormFehler, art_hash, pruefe_eingabe, pruefe_ergebnis

log = logging.getLogger("auftragsbuch")

SPACE = "rowboat"
LEIHDAUER = timedelta(minutes=2)
TAKT_S = 2.0
HERZSCHLAG_MAX_S = 30.0
HERZSCHLAG_VORGABE = "/tmp/rowboat-abholer.herzschlag"
ARTEN_VORGABE = "vorschlaege_anlegen"
VERTRAG_ORDNER = Path(__file__).resolve().parents[2] / "_contract"

GRUND_UNBEKANNT = "Unbekannte Auftragsart oder Version"
GRUND_EINGABE = "Eingabe passt nicht zum Vertrag"
GRUND_NICHT_ANGENOMMEN = "Rowboat hat einen Punkt nicht angenommen"
GRUND_ERGEBNIS = "Ergebnis passt nicht zum Vertrag"
GRUND_INTERN = "Abholer: interner Fehler"

FERTIG = "fertig"
FEHLER = "fehler"


class _NichtAngenommen(Exception):
    """Rowboat hat einen Punkt auch nach erneutem Lesen nicht."""


Aufruf = Callable[[str, object], object]


class _Rowboat:
    """Duenne Huelle: liest `todo:get` und `todo:plannerSignals` (Koerper JSON null) und stellt ein."""

    def __init__(self, rpc: Any) -> None:
        self._aufruf: Aufruf = rpc.aufruf

    def lesen(self) -> tuple[list[str], list[str], list[dict]]:
        try:
            offen, liste = todo_texte(self._aufruf("todo:get", None))  # Rowboat: req ist z.null()
            signale = signale_aus(self._aufruf("todo:plannerSignals", None))  # ebenso z.null()
        except ValueError:
            raise RowboatFehler(GRUND_UNERWARTET) from None
        return offen, liste, signale

    def einstellen(self, texte: list[str]) -> list[str]:
        antwort = self._aufruf("todo:addSuggestions", {"texts": texte})  # req: {texts: string[]}
        added = antwort.get("added") if isinstance(antwort, dict) else None
        if not isinstance(added, list) or not all(isinstance(t, str) for t in added):
            raise RowboatFehler(GRUND_UNERWARTET)
        return added


def _vorschlaege_anlegen(buch: Empfang, rowboat: _Rowboat, eingabe: dict, laden: str) -> dict:
    punkte: list[dict] = eingabe["punkte"]
    ids = list(dict.fromkeys(p["punkt_id"] for p in punkte))
    offen, liste, signale = rowboat.lesen()
    bekannt = buch.bekannt(laden, ids)
    stand, neu = einordnen(punkte, offen, liste, signale, bekannt)
    if neu:
        hinzu = rowboat.einstellen([vorschlag(p) for p in neu])
        stand_neu, fehlen = nach_einstellen(neu, hinzu)
        stand.update(stand_neu)
        if fehlen:
            offen, liste, signale = rowboat.lesen()
            stand_nach, noch_neu = einordnen([p for p in neu if p["punkt_id"] in fehlen], offen, liste, signale,
                                             bekannt)
            if noch_neu:
                raise _NichtAngenommen()
            stand.update(stand_nach)
    # Je Eingabe-Punkt eine Zeile (auch doppelte Kennungen), damit der Beleg die Kennungen 1:1 vergleicht.
    return {"punkte": [{"punkt_id": p["punkt_id"], "stand": stand[p["punkt_id"]]} for p in punkte]}


def _fehler(buch: Empfang, auftrag_id: str, art: str, grund: str, laden: str) -> str:
    gesetzt = buch.fehler(auftrag_id, grund, laden)
    log.info("auftrag=%s art=%s stand=fehler grund=%s%s", auftrag_id[:8], art, grund,
             "" if gesetzt else " gesetzt=nein")
    return FEHLER


def _art_im_log(art: object, arten: Mapping[tuple[str, str], Auftragsart]) -> str:
    """Nur bekannte Namen ins Log (die Art kommt aus der Datenbank)."""
    return art if isinstance(art, str) and (SPACE, art) in arten else "(unbekannt)"


def bearbeiten(buch: Empfang, rpc: Any, auftrag: dict, arten: Mapping[tuple[str, str], Auftragsart],
               laden: str) -> str:
    """Bearbeitet einen geholten Auftrag; `fertig` oder `fehler`. Nur Datenbankfehler gehen nach aussen."""
    auftrag_id = str(auftrag.get("id"))
    art = arten.get((SPACE, auftrag.get("art"))) if isinstance(auftrag.get("art"), str) else None
    name = _art_im_log(auftrag.get("art"), arten)
    if art is None or auftrag.get("art_version") != art.version:
        return _fehler(buch, auftrag_id, name, GRUND_UNBEKANNT, laden)
    eingabe = auftrag.get("eingabe")
    try:
        pruefe_eingabe(art, eingabe)
    except FormFehler:
        return _fehler(buch, auftrag_id, name, GRUND_EINGABE, laden)
    try:
        ergebnis = _vorschlaege_anlegen(buch, _Rowboat(rpc), eingabe, laden)
    except RowboatFehler as e:
        return _fehler(buch, auftrag_id, name, e.grund, laden)
    except _NichtAngenommen:
        return _fehler(buch, auftrag_id, name, GRUND_NICHT_ANGENOMMEN, laden)
    try:
        pruefe_ergebnis(art, ergebnis)
    except FormFehler:
        return _fehler(buch, auftrag_id, name, GRUND_ERGEBNIS, laden)
    if not buch.abschliessen(auftrag_id, ergebnis, laden):
        # Zu spaet (Leihdauer oder Frist vorbei) oder schon erledigt: nur ein Logeintrag, kein zweiter Versuch.
        log.info("auftrag=%s art=%s abschluss=abgelehnt", auftrag_id[:8], name)
        return FEHLER
    zaehler = {s: 0 for s in ("neu", "schon_vorhanden", "frueher_abgelehnt")}
    for p in ergebnis["punkte"]:
        zaehler[p["stand"]] += 1
    log.info("auftrag=%s art=%s stand=fertig punkte=%d neu=%d schon_vorhanden=%d frueher_abgelehnt=%d",
             auftrag_id[:8], name, len(ergebnis["punkte"]), zaehler["neu"], zaehler["schon_vorhanden"],
             zaehler["frueher_abgelehnt"])
    return FERTIG


def durchgang(buch: Empfang, rpc: Any, arten: Mapping[tuple[str, str], Auftragsart], laden: str) -> str | None:
    """Holt hoechstens einen Auftrag und bearbeitet ihn; None, wenn keiner wartet (oder keine Art frei ist).

    Ein Fehler beim Holen geht an den Aufrufer (`lauf`). Jeder andere Fehler beim Bearbeiten endet als
    `fehler` mit festem Grund, damit der Auftrag nicht bis zum Ende der Leihdauer haengt.
    """
    if not arten:
        return None
    auftrag = buch.naechster(laden, LEIHDAUER)
    if auftrag is None:
        return None
    try:
        return bearbeiten(buch, rpc, auftrag, arten, laden)
    except Exception as e:  # noqa: BLE001 - nur der Typname geht ins Log
        auftrag_id = str(auftrag.get("id"))
        log.warning("auftrag=%s art=%s stand=fehler fehlertyp=%s", auftrag_id[:8],
                    _art_im_log(auftrag.get("art"), arten), type(e).__name__)
        try:
            buch.fehler(auftrag_id, GRUND_INTERN, laden)
        except Exception as e2:  # noqa: BLE001 - dann raeumt die Leihdauer auf
            log.warning("auftrag=%s fehler_setzen=nein fehlertyp=%s", auftrag_id[:8], type(e2).__name__)
        return FEHLER


def _herzschlag(pfad: Path | None) -> None:
    if pfad is None:
        return
    try:
        pfad.touch()  # setzt die Aenderungszeit auch, wenn die Datei schon da ist
    except OSError as e:
        log.warning("herzschlag=nein fehlertyp=%s", type(e).__name__)


def lauf(buch: Empfang, rpc: Any, arten: Mapping[tuple[str, str], Auftragsart], laden: str, *,
         takt_s: float = TAKT_S, herzschlag: Path | None = None, stopp: threading.Event | None = None) -> None:
    """Schleife bis `stopp`: nach einem Auftrag sofort weiter, sonst einen Takt warten. Stuerzt nie ab."""
    stopp = stopp if stopp is not None else threading.Event()
    db_fehler = False
    while not stopp.is_set():
        try:
            ergebnis = durchgang(buch, rpc, arten, laden)
        except Exception as e:  # noqa: BLE001 - Datenbank nicht erreichbar o. Ae.; die Meldung kann die DSN nennen
            if not db_fehler:
                log.warning("abholer=datenbank stand=fehler fehlertyp=%s", type(e).__name__)
            db_fehler = True
            stopp.wait(takt_s)
            continue
        if db_fehler:
            log.info("abholer=datenbank stand=wieder_da")
            db_fehler = False
        _herzschlag(herzschlag)
        if ergebnis is None:
            stopp.wait(takt_s)


def gesund(pfad: Path, jetzt: float | None = None, max_alter_s: float = HERZSCHLAG_MAX_S) -> bool:
    try:
        alter = (time.time() if jetzt is None else jetzt) - pfad.stat().st_mtime
    except OSError:
        return False
    return alter <= max_alter_s


def startzeile(laden: str, arten: Mapping[tuple[str, str], Auftragsart]) -> str:
    """Erste Logzeile: Laden, freigeschaltete Arten und je Art die ersten acht Zeichen von `art_hash`."""
    teile = [f"{a.art}:{art_hash(a)[:8]}" for _k, a in sorted(arten.items())]
    return f"abholer=start laden={laden} arten={','.join(teile) or '-'}"


def freie_arten(alle: Mapping[tuple[str, str], Auftragsart], wert: str | None) -> dict[tuple[str, str], Auftragsart]:
    """Arten aus ABHOLER_ARTEN_FREI (fehlt: Vorgabe, leer: keine). Nur Arten, die dieser Abholer bearbeiten kann."""
    roh = ARTEN_VORGABE if wert is None else wert
    namen = [n.strip() for n in roh.split(",") if n.strip()]
    frei: dict[tuple[str, str], Auftragsart] = {}
    for n in namen:
        if n != ARTEN_VORGABE or (SPACE, n) not in alle:
            raise ValueError("ABHOLER_ARTEN_FREI ungueltig")
        frei[(SPACE, n)] = alle[(SPACE, n)]
    return frei


# --- Kommandozeile -------------------------------------------------------------------


@dataclass(frozen=True)
class Konfiguration:
    dsn: str
    laden: str
    arten_frei: str | None
    rowboat_url: str
    schluessel_datei: Path
    herzschlag: Path

    def __repr__(self) -> str:  # DSN nie ausgeben
        return f"Konfiguration(laden={self.laden!r}, arten_frei={self.arten_frei!r})"


def _pflicht(env: Mapping[str, str], name: str) -> str:
    wert = env.get(name, "").strip()
    if not wert:
        raise ValueError(f"{name} fehlt")
    return wert


def konfiguration(env: Mapping[str, str]) -> Konfiguration:
    """Liest die Umgebung; Fehlermeldungen nennen nur den Variablennamen, nie einen Wert."""
    laden = _pflicht(env, "ABHOLER_LADEN")
    anderer = env.get("AUFTRAGSBUCH_LADEN", "").strip()
    if anderer and anderer != laden:
        raise ValueError("ABHOLER_LADEN weicht von AUFTRAGSBUCH_LADEN ab")
    dsn_datei = _pflicht(env, "ABHOLER_DSN_FILE")
    url = _pflicht(env, "ROWBOAT_RPC_URL")
    schluessel = Path(_pflicht(env, "ROWBOAT_SCHLUESSEL_DATEI"))
    try:
        dsn = Path(dsn_datei).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise ValueError("ABHOLER_DSN_FILE nicht lesbar") from None
    if not dsn:
        raise ValueError("ABHOLER_DSN_FILE ist leer")
    herz = Path(env.get("ABHOLER_HERZSCHLAG_DATEI", "").strip() or HERZSCHLAG_VORGABE)
    return Konfiguration(dsn, laden, env.get("ABHOLER_ARTEN_FREI"), url, schluessel, herz)


def main(umgebung: Mapping[str, str] | None = None, argv: list[str] | None = None) -> int:
    """Startet den Abholer; jeder Startfehler endet mit `rowboat-abholer: <Meldung>` ohne Stapel und Werte."""
    env = os.environ if umgebung is None else umgebung
    args = sys.argv[1:] if argv is None else argv
    if args == ["--gesund"]:
        return 0 if gesund(Path(env.get("ABHOLER_HERZSCHLAG_DATEI", "").strip() or HERZSCHLAG_VORGABE)) else 1
    if args:
        raise SystemExit("rowboat-abholer: unbekanntes Argument")
    try:
        k = konfiguration(env)
    except ValueError as e:
        raise SystemExit(f"rowboat-abholer: {e}") from None
    from spaces.auftragsbuch.rowboat.rpc import RowboatRpc
    from spaces.auftragsbuch.vertrag import lade_auftragsarten

    try:
        rpc = RowboatRpc(k.rowboat_url, k.schluessel_datei)
    except ValueError as e:  # feste Texte ohne Werte
        raise SystemExit(f"rowboat-abholer: {e}") from None
    try:
        arten = freie_arten(lade_auftragsarten(VERTRAG_ORDNER), k.arten_frei)
    except ValueError as e:
        text = str(e) if str(e) == "ABHOLER_ARTEN_FREI ungueltig" else f"Vertrag nicht ladbar ({type(e).__name__})"
        raise SystemExit(f"rowboat-abholer: {text}") from None
    except Exception as e:  # noqa: BLE001 - nur der Typname
        raise SystemExit(f"rowboat-abholer: Vertrag nicht ladbar ({type(e).__name__})") from None
    from psycopg_pool import ConnectionPool

    from spaces.auftragsbuch.protokoll import log_einrichten
    from spaces.auftragsbuch.rowboat.empfang import PgEmpfang

    try:
        pool: Any = ConnectionPool(k.dsn, min_size=1, max_size=1, open=False, name="rowboat-abholer")
    except Exception as e:  # noqa: BLE001 - die Meldung der Bibliothek kann die DSN nennen
        raise SystemExit(f"rowboat-abholer: Datenbank nicht erreichbar ({type(e).__name__})") from None
    log_einrichten()
    log.info(startzeile(k.laden, arten))
    try:
        pool.open(wait=True, timeout=30)
    except Exception as e:  # noqa: BLE001 - PoolTimeout o. Ae.; die Meldung kann die DSN nennen
        pool.close()
        raise SystemExit(f"rowboat-abholer: Datenbank nicht erreichbar ({type(e).__name__})") from None
    stopp = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_a: stopp.set())
    signal.signal(signal.SIGINT, lambda *_a: stopp.set())
    try:
        lauf(PgEmpfang(pool), rpc, arten, k.laden, herzschlag=k.herzschlag, stopp=stopp)
    finally:
        pool.close()
    log.info("abholer=ende")
    return 0


if __name__ == "__main__":
    sys.exit(main())
