"""Zwischenspeicher der gemerkten Firmen-Webseite am PC (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §2):
je Firma <Arbeitsordner>/webseiten/<mandant>.json, gueltig 24 h. Wirft nie; ein kaputter, fremder oder
abgelaufener Eintrag gilt als nicht vorhanden."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict

from spaces.marketing.claw.webseite import Fund, Seite

GUELTIG_S = 24 * 3600
ORDNER_VORGABE = os.path.join(os.path.expanduser("~"), ".vibemind", "marketing-arbeiter")
_MANDANT = re.compile(r"[a-z][a-z0-9_-]{0,40}")


def ordner() -> str:
    """Arbeitsordner des Marken-Arbeiters: MARKETING_ARBEITER_ORDNER oder ~/.vibemind/marketing-arbeiter."""
    return os.environ.get("MARKETING_ARBEITER_ORDNER") or ORDNER_VORGABE


def _datei(basis: str, mandant) -> str | None:
    if not isinstance(mandant, str) or not _MANDANT.fullmatch(mandant):
        return None
    return os.path.join(basis, "webseiten", f"{mandant}.json")


def _texte(liste) -> list[str]:
    return [str(x) for x in liste]


def laden(basis: str, mandant: str, url: str, jetzt_s: float) -> Fund | None:
    pfad = _datei(basis, mandant)
    if pfad is None:
        return None
    try:
        with open(pfad, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("url") != url or not 0 <= jetzt_s - float(d["zeit"]) < GUELTIG_S:
            return None
        roh = d["fund"]
        return Fund(seiten=[Seite(url=str(s["url"]), text=str(s["text"]), ueberschriften=_texte(s["ueberschriften"]))
                            for s in roh["seiten"]],
                    farben=_texte(roh["farben"]), schriften=_texte(roh["schriften"]), logos=_texte(roh["logos"]),
                    hinweise=[])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def ablegen(basis: str, mandant: str, url: str, fund: Fund | None, jetzt_s: float) -> None:
    pfad = _datei(basis, mandant)
    if pfad is None or fund is None or not fund.seiten:
        return
    daten = json.dumps({"url": url, "zeit": jetzt_s, "fund": {**asdict(fund), "hinweise": []}}, ensure_ascii=False)
    temp = None
    try:
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(pfad), suffix=".teil")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(daten)
        os.replace(temp, pfad)
    except OSError:
        if temp:
            try:
                os.remove(temp)
            except OSError:
                pass