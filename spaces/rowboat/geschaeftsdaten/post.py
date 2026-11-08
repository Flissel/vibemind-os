"""Notiz 'Zu beantworten' aus der offenen Post der sales-ui (rein bis auf Stichtag lesen und Schreiben).

Die DB-Sicht nullt Absender und Betreff bei vertraulichen Mails und gesperrten Absendern. Dieses Modul
erzwingt dieselben Regeln noch einmal selbst (Tiefenverteidigung): `sensibel` -> keine Angaben zur Mail,
`absender_gesperrt` -> nur Alter und Postfach, kein Steckbrief-Link. Fremdtext wird einzeilig und gegen
Wiki-Links und `@rowboat`-Ausloeser neutralisiert. Es wird nichts geloescht.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ablage import ERZEUGT_VON, _eigen, _schreiben_atomar, eigener_schluessel
from laeden import env_werte
from quelle import PostMail
from termine import _einzeilig

SALES_UI = "https://vibemind-offload-1.tail6c7d61.ts.net"
MAX_PUNKTE = 50
STAND_PRAEFIX = "Stand: "
STICHTAG_SCHLUESSEL = "POST_AUFRAEUMEN_AB"
UEBERSICHT_REL = "Post/_overview.md"
ZU_BEANTWORTEN_REL = "Post/Zu beantworten.md"
SCHLUESSEL_UEBERSICHT = "post:uebersicht"
SCHLUESSEL_ZU_BEANTWORTEN = "post:zu-beantworten"
MAX_BETREFF = 120
MAX_ABSENDER = 100
BERLIN = ZoneInfo("Europe/Berlin")
_STICHTAG = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAIL_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_LINK_ZIEL = re.compile(r"[^\[\]|#^\x00-\x1f\x7f]+")
_HINWEIS = ("> Automatisch erzeugt aus sales-ui. Nicht hier bearbeiten – der nächtliche Lauf überschreibt; "
            "eigene Anmerkungen in eine eigene Notiz.")
log = logging.getLogger("geschaeftsdaten")


def stichtag_lesen(env_datei: Path) -> date | None:
    """`POST_AUFRAEUMEN_AB` (JJJJ-MM-TT) aus der Env-Datei; fehlt Datei, Schluessel oder ist das Datum kaputt: None."""
    try:
        wert = env_werte(env_datei, frozenset({STICHTAG_SCHLUESSEL})).get(STICHTAG_SCHLUESSEL, "")
        return date.fromisoformat(wert) if _STICHTAG.fullmatch(wert) else None
    except (OSError, ValueError):
        return None


def _zeit(datum: str) -> datetime | None:
    try:
        z = datetime.fromisoformat(datum)
    except (TypeError, ValueError):
        return None
    return z.astimezone(BERLIN) if z.tzinfo else z.replace(tzinfo=BERLIN)


def _alter(datum: str, jetzt: datetime) -> str:
    z = _zeit(datum)
    if z is None:
        return "seit ?"
    tage = (jetzt.astimezone(BERLIN).date() - z.date()).days
    if tage <= 0:
        return "seit heute"
    return "seit 1 Tag" if tage == 1 else f"seit {tage} Tagen"


def _kopf(schluessel: str, eltern: str | None) -> list[str]:
    zeilen = ["---", f"erzeugt_von: {ERZEUGT_VON}", "quelle: sales-ui", f"schluessel: {schluessel}",
              "art: uebersicht"]
    if eltern:
        zeilen.append(f'parent: "[[{eltern}]]"')
    return zeilen + ["---", "", _HINWEIS, ""]


def _mail_link(m: PostMail) -> str:
    # Der Link steht auch bei vertraulichen und gesperrten Mails: er traegt nur die validierte Mail-ID.
    ziel = f"/post/mail/{m.id}" if _MAIL_ID.fullmatch(m.id) else "/posteingang"
    return f"[in sales-ui]({SALES_UI}{ziel})"


def _steckbrief_link(m: PostMail, steckbriefe: dict[str, str]) -> str | None:
    pfad = steckbriefe.get(m.absender_klein or "")
    if not pfad:
        return None
    ziel = pfad[:-3] if pfad.endswith(".md") else pfad
    if not _LINK_ZIEL.fullmatch(ziel) or "@rowboat" in ziel.casefold():
        return None
    return f"[[{ziel}]]"


def _punkt(m: PostMail, steckbriefe: dict[str, str], jetzt: datetime) -> str:
    teile = [_alter(m.datum, jetzt)]
    postfach = _einzeilig(m.postfach, MAX_ABSENDER)
    if postfach:
        teile.append(postfach)
    if m.sensibel:
        teile.append("vertrauliche Mail")
    elif m.absender_gesperrt:
        teile.append("Mail eines gesperrten Kontakts")
    else:
        absender = _einzeilig(m.von_anzeige or m.absender_klein, MAX_ABSENDER)
        if absender:
            teile.append(absender)
        betreff = _einzeilig(m.betreff, MAX_BETREFF)
        if betreff:
            teile.append(f"„{betreff}“")
        link = _steckbrief_link(m, steckbriefe)
        if link:
            teile.append(link)
    teile.append(_mail_link(m))
    return "- " + " · ".join(teile)


def _sortierung(m: PostMail) -> tuple[int, datetime, str]:
    z = _zeit(m.datum)
    return (0, z, m.id) if z else (1, datetime.min.replace(tzinfo=BERLIN), m.id)


def post_notizen(mails: list[PostMail], steckbriefe: dict[str, str], *, jetzt: datetime,
                 stichtag: date | None, company_da: bool) -> dict[str, tuple[str, str]]:
    """`rel ab knowledge/` -> (schluessel, inhalt) fuer `Post/_overview.md` und `Post/Zu beantworten.md`.
    Ohne Stichtag gilt nichts als offen. Aelteste Mails zuerst, hoechstens `MAX_PUNKTE` Punkte."""
    offen = sorted(mails, key=_sortierung) if stichtag is not None else []
    stand = jetzt.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")
    zeilen = _kopf(SCHLUESSEL_ZU_BEANTWORTEN, "Post/_overview") + [
        "# Zu beantworten", "", f"{STAND_PRAEFIX}{stand}", "",
        f"Offen: {len(offen)} · [alle in sales-ui]({SALES_UI}/posteingang)", ""]
    if not offen:
        zeilen += ["Nichts offen.", ""]
        if stichtag is None:
            zeilen += ["In sales-ui ist kein Stichtag gesetzt, deshalb zählt keine Mail als offen.", ""]
    else:
        zeilen += [_punkt(m, steckbriefe, jetzt) for m in offen[:MAX_PUNKTE]]
        if len(offen) > MAX_PUNKTE:
            zeilen += ["", f"… und {len(offen) - MAX_PUNKTE} weitere in sales-ui"]
    uebersicht = _kopf(SCHLUESSEL_UEBERSICHT, "Company" if company_da else None) + [
        "# Post", "", "Die Mails, die noch beantwortet werden müssen, stehen in [[Post/Zu beantworten]].", ""]
    return {UEBERSICHT_REL: (SCHLUESSEL_UEBERSICHT, "\n".join(uebersicht)),
            ZU_BEANTWORTEN_REL: (SCHLUESSEL_ZU_BEANTWORTEN, "\n".join(zeilen) + "\n")}


def _ohne_stand(text: str) -> list[str]:
    return [z for z in text.splitlines() if not z.startswith(STAND_PRAEFIX)]


def post_schreiben(wissen: Path, notizen: dict[str, tuple[str, str]], *, trocken: bool) -> dict[str, int]:
    """Schreibt die Notizen unter `wissen` (= knowledge/). Zaehler: geschrieben, unveraendert, fremd.
    Eine vorhandene Datei, die nicht dem erwarteten Schluessel gehoert (auch Symlinks), bleibt unberuehrt."""
    zaehler = {"geschrieben": 0, "unveraendert": 0, "fremd": 0}
    for rel, (schluessel, inhalt) in notizen.items():
        ziel = wissen / rel
        if ziel.parent.is_symlink() or ziel.is_symlink() or ziel.exists():
            if ziel.parent.is_symlink() or eigener_schluessel(ziel) != schluessel:
                log.warning("%s gehoert nicht dem Lauf, bleibt unberuehrt", rel)
                zaehler["fremd"] += 1
                continue
            try:
                alt = ziel.read_bytes()
            except OSError:
                zaehler["fremd"] += 1
                continue
            if _eigen(alt) == schluessel and _ohne_stand(alt.decode("utf-8", errors="replace")) == _ohne_stand(inhalt):
                zaehler["unveraendert"] += 1
                continue
        zaehler["geschrieben"] += 1
        if not trocken:
            _schreiben_atomar(ziel, inhalt)
    return zaehler
