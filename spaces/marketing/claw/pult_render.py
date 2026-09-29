"""Render-Modul des Marketing-Pults (sales-claw Spec 2026-09-29-marketing-
pult-design.md §3.4): EINE Stelle baut Mail-HTML, Handy-HTML und PDF aus einer
Fassung und einer Layout-Gestalt — Vorschau und Ergebnis kommen aus derselben
Quelle.

Alles Fremde (Entwurfstext, Kopf/Fuss, Impressum) wird escaped; Links nur
https; Logos nur als data:-Bild (kein Nachladen fremder Server beim Oeffnen).

Die Gestalt wird hier NICHT geprueft: ihre Farben landen in style-Attributen.
Jeder Aufrufer muss sie vorher mit marketing.pult_gestalt_fehler pruefen (und
bei einem Grund oder einer fehlgeschlagenen Pruefung nicht rendern) - so tun
es /api/pult/layouts/vorschau und /api/pult/inhalte/{id}/vorschau.
"""
from __future__ import annotations

import html
import re

from spaces.marketing.claw import pdf, schoenheit

SCHRIFTEN = {
    "system": "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "mono": "'SFMono-Regular', Consolas, 'Courier New', monospace",
}
ABSTAENDE = {"eng": 12, "mittel": 20, "weit": 32}
_LOGO = re.compile(r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$")

BEISPIEL_FELDER = {
    "betreff": "Neuigkeiten aus der Werkstatt",
    "vorschautext": "Was sich diesen Monat getan hat",
    "abschnitte": [
        {"titel": "Das Wichtigste", "text": "Ein kurzer Absatz, der zeigt, wie Text in diesem Layout wirkt.\n\nUnd ein zweiter Absatz darunter."},
        {"titel": "Als Naechstes", "text": "Noch ein Abschnitt, damit man Abstaende und Ueberschriften sieht."},
    ],
    "knopf_text": "Mehr erfahren",
    "knopf_link": "https://vibemind.space",
}


def _e(wert) -> str:
    return html.escape(str(wert if wert is not None else ""), quote=True)


def _absaetze(text: str, farbe: str, abstand: int) -> str:
    teile = [t.strip() for t in re.split(r"\n\s*\n", text or "") if t.strip()]
    return "".join(
        f'<p style="margin:0 0 {abstand // 2}px 0;color:{farbe};line-height:1.55">'
        f'{_e(t).replace(chr(10), "<br>")}</p>' for t in teile)


def _band_schrift(g: dict) -> tuple[str, str]:
    """(Schrift, gedaempfte Schrift) fuer das Kopf-/Fussband auf `flaeche`.

    Die Schrift ist text_hell, wenn die gegen flaeche lesbar ist (>= 4.5) -
    der Betreiber will lieber nicht reinweiss (pdf.py, 11.09.2026). Sonst die
    bessere von #ffffff/#111111: in "hell"/"warm-sand" ist flaeche das dunkle
    Band, text_hell aber dunkel (fuer den hellen grund). Die gedaempfte Schrift
    ist text_leise, wenn die gegen flaeche lesbar ist, sonst dieselbe Wahl."""
    flaeche = g["flaeche"]
    text_hell = g.get("text_hell") or ""
    try:
        hell_ok = schoenheit.kontrast(text_hell, flaeche) >= schoenheit.KONTRAST_TEXT
    except ValueError:
        hell_ok = False
    schrift = text_hell if hell_ok else max(
        ("#ffffff", "#111111"), key=lambda c: schoenheit.kontrast(c, flaeche))
    leise = g.get("text_leise") or ""
    try:
        leise_ok = schoenheit.kontrast(leise, flaeche) >= schoenheit.KONTRAST_TEXT
    except ValueError:
        leise_ok = False
    return schrift, (leise if leise_ok else schrift)


def mail_html(felder: dict, gestalt: dict, pflichtteil: dict, breite: int = 600) -> str:
    """Farbbedeutung wie in pdf._stile/_kopf_und_grund: grund = Inhaltsflaeche
    (text, Ueberschriften text_hell), flaeche = Kopf- und Fussband,
    akzent/handlung_text = Knopf. Bis 29.09.2026 lag der Inhalt auf flaeche -
    in "hell" und "warm-sand" dunkler Text auf dunklem Band, unlesbar."""
    g = gestalt
    schrift = SCHRIFTEN.get(g.get("schrift", "system"), SCHRIFTEN["system"])
    abstand = ABSTAENDE.get(g.get("abstand", "mittel"), ABSTAENDE["mittel"])
    rundung = int(g.get("rundung", 8))
    kopf, fuss = g.get("kopf_text", ""), g.get("fuss_text", "")
    band, band_leise = _band_schrift(g)
    logo = g.get("logo", "")
    logo_html = (f'<img src="{_e(logo)}" alt="" height="40" style="display:block;margin:0 0 {abstand // 2}px 0">'
                 if isinstance(logo, str) and _LOGO.match(logo) else "")
    abschnitte = "".join(
        (f'<h2 style="margin:0 0 8px 0;color:{g["text_hell"]};font-size:18px">{_e(a.get("titel"))}</h2>'
         if (a.get("titel") or "").strip() else "")
        + _absaetze(a.get("text", ""), g["text"], abstand)
        for a in felder.get("abschnitte") or [])
    link = (felder.get("knopf_link") or "").strip()
    knopf = ""
    if link.startswith("https://") and (felder.get("knopf_text") or "").strip():
        knopf = (f'<a href="{_e(link)}" style="display:inline-block;background:{g["akzent"]};'
                 f'color:{g["handlung_text"]};padding:12px 22px;border-radius:{rundung}px;'
                 f'text-decoration:none;font-weight:600">{_e(felder["knopf_text"])}</a>')
    impressum = (pflichtteil.get("impressum") or "").strip()
    # Warnung als eigene Pille: Weiss auf Dunkelrot ist auf jedem Band lesbar.
    impressum_html = (_e(impressum) if impressum else
                      '<strong style="background:#b91c1c;color:#ffffff;padding:1px 6px;border-radius:3px">'
                      'Impressum fehlt &ndash; im Mandanten hinterlegen</strong>')
    abmelden = _e((pflichtteil.get("abmelde_hinweis") or "").replace("{abmeldelink}", "[Abmeldelink]"))
    kopfband = ""
    if kopf or logo_html:
        kopfband = (
            f'<tr><td style="background:{g["flaeche"]};color:{band};padding:{abstand}px;'
            f'border-radius:{rundung}px {rundung}px 0 0">'
            f'{logo_html}'
            + (f'<div style="color:{band};font-size:13px;font-weight:600">{_e(kopf)}</div>' if kopf else "")
            + '</td></tr>')
    oben_rund = "0" if kopfband else f"{rundung}px {rundung}px 0 0"
    fussband = (
        f'<tr><td style="background:{g["flaeche"]};color:{band_leise};font-size:11px;line-height:1.5;'
        f'padding:{abstand}px;border-radius:0 0 {rundung}px {rundung}px">'
        + (f'<p style="color:{band};font-size:13px;margin:0 0 {abstand // 2}px 0">{_e(fuss)}</p>' if fuss else "")
        + f'<div style="color:{band_leise}">{impressum_html}<br>{abmelden}</div>'
        '</td></tr>')
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        f'<title>{_e(felder.get("betreff"))}</title></head>'
        f'<body style="margin:0;padding:0;background:{g["grund"]};font-family:{schrift}">'
        f'<span style="display:none">{_e(felder.get("vorschautext"))}</span>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{g["grund"]}"><tr><td align="center" style="background:{g["grund"]};padding:{abstand}px 8px">'
        f'<table role="presentation" width="{int(breite)}" cellpadding="0" cellspacing="0" '
        f'style="max-width:{int(breite)}px;background:{g["grund"]};border:1px solid {g["flaeche"]};border-radius:{rundung}px">'
        f'{kopfband}'
        f'<tr><td style="background:{g["grund"]};padding:{abstand}px;border-radius:{oben_rund}">'
        f'<h1 style="margin:0 0 {abstand}px 0;color:{g["text_hell"]};font-size:24px">{_e(felder.get("betreff"))}</h1>'
        f'{abschnitte}'
        + (f'<div style="margin:{abstand}px 0 0 0">{knopf}</div>' if knopf else "")
        + '</td></tr>'
        f'{fussband}'
        '</table>'
        '</td></tr></table></body></html>')


def handy_html(felder: dict, gestalt: dict, pflichtteil: dict) -> str:
    return mail_html(felder, gestalt, pflichtteil, breite=380)


def pdf_bytes(felder: dict, gestalt: dict) -> bytes:
    farben = {k: gestalt[k] for k in pdf.GESTALT_SCHLUESSEL}
    text = "\n\n".join(
        ((a.get("titel") or "").strip() + "\n" if (a.get("titel") or "").strip() else "")
        + (a.get("text") or "") for a in felder.get("abschnitte") or [])
    return pdf.bauen(titel=felder.get("betreff") or "", text=text,
                     untertitel=felder.get("vorschautext") or "",
                     handlung=felder.get("knopf_text") or "", gestalt=farben)
