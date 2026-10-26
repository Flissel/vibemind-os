"""Newsletter-Archiv: Klartext und Notiz je versendetem Newsletter (rein, ohne DB).

Der Klartext kommt aus den stdlib-reinen Renderern von Marketing (`bloecke_mjml.text_fassung`,
`seite_mail.text_fassung`) bzw. fuer das Format `felder` direkt aus den Feldern. Der Pflichtteil
(Impressum, Abmelde-Hinweis) gehoert nicht ins Archiv: es wird ein leerer Pflichtteil uebergeben und die
Fusszeile `--` entfernt. `{vorname}` wird zu `<Vorname>`, `{abmeldelink}` entfaellt. Laesst sich ein
Inhalt nicht darstellen, steht dort "Text nicht darstellbar"; der Lauf bricht nicht ab.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quelle import NewsletterInhalt, Versand

BERLIN = ZoneInfo("Europe/Berlin")
ERZEUGT_VON = "rowboat-geschaeftsdaten"
NICHT_DARSTELLBAR = "Text nicht darstellbar"
_LEERRAUM = re.compile(r"\s+")
_AUSLOESER = re.compile(r"@(rowboat)", re.IGNORECASE)  # Rowboat-Agenten reagieren auf @rowboat
_LEERZEILEN = re.compile(r"\n{3,}")


def _marketing():
    """`bloecke_mjml` und `seite_mail` erst bei Bedarf laden (Repo-Wurzel notfalls ergaenzen)."""
    try:
        from spaces.marketing.claw import bloecke_mjml, seite_mail
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from spaces.marketing.claw import bloecke_mjml, seite_mail
    return bloecke_mjml, seite_mail


def _ohne_fuss(text: str) -> str:
    absaetze = text.split("\n\n")
    if "--" in (a.strip() for a in absaetze):
        absaetze = absaetze[:[a.strip() for a in absaetze].index("--")]
    return "\n\n".join(absaetze)


def _felder_text(felder: dict) -> str:
    teile: list[str] = []
    for a in felder.get("abschnitte") or []:
        if not isinstance(a, dict):
            continue
        titel, text = str(a.get("titel") or "").strip(), str(a.get("text") or "").strip()
        absatz = "\n".join(x for x in (titel, text) if x)
        if absatz:
            teile.append(absatz)
    knopf, link = str(felder.get("knopf_text") or "").strip(), str(felder.get("knopf_link") or "").strip()
    if knopf and link.startswith("https://"):
        teile.append(f"{knopf}: {link}")
    return "\n\n".join(teile)


def _platzhalter(text: str) -> str:
    text = text.replace("{vorname}", "<Vorname>").replace("{abmeldelink}", "")
    text = "\n".join(z.rstrip() for z in text.splitlines())
    return _AUSLOESER.sub(r"@ \1", text)


def klartext(inh: NewsletterInhalt) -> str:
    """Klartext der freigegebenen Fassung ohne Pflichtteil; Platzhalter ersetzt."""
    try:
        if inh.format == "felder":
            roh = _felder_text(inh.felder or {})
        elif inh.format == "bloecke" and isinstance(inh.bloecke, dict):
            bloecke_mjml, _ = _marketing()
            roh = _ohne_fuss(bloecke_mjml.text_fassung(inh.bloecke, {}))
        elif inh.format == "seite" and isinstance(inh.seite, dict):
            _, seite_mail = _marketing()
            roh = _ohne_fuss(seite_mail.text_fassung(inh.seite, {}))
        else:
            return NICHT_DARSTELLBAR + "\n"
    except Exception:  # noqa: BLE001 - RenderFehler oder kaputte Daten: Archiv ohne Text, Lauf geht weiter
        return NICHT_DARSTELLBAR + "\n"
    text = _LEERZEILEN.sub("\n\n", _platzhalter(roh)).strip()
    return (text or NICHT_DARSTELLBAR) + "\n"


def _einzeilig(wert: object) -> str:
    return _LEERRAUM.sub(" ", str(wert or "")).strip()


def betreff(inh: NewsletterInhalt) -> str:
    return _einzeilig((inh.felder or {}).get("betreff")) or _einzeilig(inh.titel)


def stamm(inh: NewsletterInhalt, erster_versand: datetime) -> str:
    """`JJJJ-MM-TT <Betreff>` (Datum des ersten Versands in Berlin), roh, vor `namen.dateistamm`."""
    return f"{erster_versand.astimezone(BERLIN).date().isoformat()} {betreff(inh)}".strip()


def newsletter_notiz(inh: NewsletterInhalt, versand: Versand) -> str:
    """Markdown der Newsletter-Notiz. `versand.empfaenger` ist die Summe ueber die Laeden der Marke."""
    tag = versand.erster_versand.astimezone(BERLIN).date()
    titel = _platzhalter(betreff(inh)) or "Newsletter"
    vorschau = _platzhalter(_einzeilig((inh.felder or {}).get("vorschautext")))
    zeilen = [
        "---",
        f"erzeugt_von: {ERZEUGT_VON}",
        "quelle: sales-claw",
        f"schluessel: nl:{inh.id}",
        "art: newsletter",
        "schlagwoerter: [newsletter]",
        f"stand: {tag.isoformat()}",
        "---",
        "",
        f"> Automatisch erzeugt, Quelle sales-claw, Stand {tag:%d.%m.%Y}. Nicht hier bearbeiten – der "
        "nächtliche Lauf überschreibt; eigene Anmerkungen in eine eigene Notiz.",
        "",
        f"# {titel}",
        "",
        f"- Betreff: {titel}",
    ]
    if vorschau:
        zeilen.append(f"- Vorschautext: {vorschau}")
    zeilen += [
        f"- Erster Versand: {tag:%d.%m.%Y}",
        f"- Empfänger: {versand.empfaenger}",
        "- Öffnungen: werden nicht erfasst",
        "",
        "## Text",
        "",
    ]
    return "\n".join(zeilen) + "\n" + klartext(inh)
