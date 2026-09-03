"""Verbotsliste (F1) — Marketing-Seite der gemeinsamen Sperrliste.

Die Tabelle compliance.sperrliste liegt in derselben Postgres wie marketing.*
und sales-claws leads; beide Seiten schreiben und lesen sie (Migration 013).
Dieses Modul kann zwei Dinge: Empfaenger vor einem Versand HART pruefen
(Gate 13 — ein Treffer ist ein Abbruch, keine Warnung) und Kandidaten eines
Publikumsvorschlags filtern, damit Gesperrte gar nicht erst ins Staging kommen.

KENNUNGEN werden hier und in sales-claws sperrliste.py mit derselben Regel
gebildet — sonst sieht die eine Seite die Sperre der anderen nicht:
    email:<kleingeschrieben, getrimmt>      tel:+<ziffern, E.164 ohne Formatierung>
Telefon: „(0)"-Vorwahlnull faellt weg, „00" wird „+", eine nationale Null
wird +49 (deutscher Betrieb, dokumentierte Annahme), WhatsApp-Chat-IDs
(„4917…@c.us") liefern ihren Ziffernteil.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from ..sync import _db


class SperrlisteFehler(RuntimeError):
    """Ein Empfaenger steht auf der Verbotsliste — der Versand bricht ab."""


def kennung_email(text: Optional[str]) -> Optional[str]:
    e = (text or "").strip().lower()
    if "@" not in e or any(c.isspace() for c in e):
        return None
    return "email:" + e


def kennung_tel(text: Optional[str]) -> Optional[str]:
    t = (text or "").strip()
    if "@" in t:                      # WhatsApp-Chat-ID 4917...@c.us
        t = t.split("@", 1)[0]
    t = t.replace("(0)", "")
    plus = t.startswith("+")
    ziffern = re.sub(r"\D", "", t)
    if not ziffern:
        return None
    if ziffern.startswith("00"):
        ziffern = ziffern[2:]
    elif not plus and ziffern.startswith("0"):
        ziffern = "49" + ziffern[1:]
    if len(ziffern) < 6:
        return None
    return "tel:+" + ziffern


def _aktive_sperren(kennungen: Iterable[str]) -> dict:
    """kennung -> 'quelle: grund' fuer alle aktiven Sperren der Liste."""
    ks = sorted({k for k in kennungen if k})
    if not ks:
        return {}
    liste = ", ".join(_db._sql_literal(k) for k in ks)
    rows = _db.query_via_docker(
        "SELECT kennung, quelle, grund FROM compliance.sperrliste "
        f"WHERE aufgehoben_am IS NULL AND kennung IN ({liste})") or []
    return {r["kennung"]: f"{r.get('quelle', '')}: {r.get('grund', '')}".strip(": ") for r in rows}


def pruefe_empfaenger(recipients: Iterable[dict]) -> None:
    """Gate 13: wirft SperrlisteFehler, wenn auch nur EIN Empfaenger gesperrt ist."""
    paare = [(r.get("email", ""), kennung_email(r.get("email", ""))) for r in recipients]
    treffer = _aktive_sperren(k for _, k in paare)
    if not treffer:
        return
    # Gemeldet wird die normalisierte Adresse — die steht auf der Liste.
    genannt = [f"{k[len('email:'):]} ({treffer[k]})" for _, k in paare if k in treffer]
    raise SperrlisteFehler(
        f"Sperrliste: {len(genannt)} Empfaenger gesperrt — " + "; ".join(genannt[:10]))


def filtere_kandidaten(cands: List[dict]) -> Tuple[List[dict], int]:
    """Entfernt gesperrte Kandidaten aus einem Publikumsvorschlag.
    Rueckgabe: (erlaubte Kandidaten, Anzahl entfernt)."""
    treffer = _aktive_sperren(kennung_email(c.get("email", "")) for c in cands)
    if not treffer:
        return list(cands), 0
    erlaubt = [c for c in cands if kennung_email(c.get("email", "")) not in treffer]
    return erlaubt, len(cands) - len(erlaubt)
