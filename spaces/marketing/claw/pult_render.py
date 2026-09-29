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

from spaces.marketing.claw import pdf

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


def mail_html(felder: dict, gestalt: dict, pflichtteil: dict, breite: int = 600) -> str:
    g = gestalt
    schrift = SCHRIFTEN.get(g.get("schrift", "system"), SCHRIFTEN["system"])
    abstand = ABSTAENDE.get(g.get("abstand", "mittel"), ABSTAENDE["mittel"])
    rundung = int(g.get("rundung", 8))
    kopf, fuss = g.get("kopf_text", ""), g.get("fuss_text", "")
    logo = g.get("logo", "")
    logo_html = (f'<img src="{_e(logo)}" alt="" height="40" style="display:block;margin:0 0 {abstand}px 0">'
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
    impressum_html = (_e(impressum) if impressum else
                      '<strong style="color:#ef4444">Impressum fehlt &ndash; im Mandanten hinterlegen</strong>')
    abmelden = _e((pflichtteil.get("abmelde_hinweis") or "").replace("{abmeldelink}", "[Abmeldelink]"))
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        f'<title>{_e(felder.get("betreff"))}</title></head>'
        f'<body style="margin:0;padding:0;background:{g["grund"]};font-family:{schrift}">'
        f'<span style="display:none">{_e(felder.get("vorschautext"))}</span>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{g["grund"]}"><tr><td align="center" style="padding:{abstand}px 8px">'
        f'<table role="presentation" width="{int(breite)}" cellpadding="0" cellspacing="0" '
        f'style="max-width:{int(breite)}px;background:{g["flaeche"]};border-radius:{rundung}px">'
        f'<tr><td style="padding:{abstand}px">'
        f'{logo_html}'
        + (f'<div style="color:{g["text_leise"]};font-size:13px;margin-bottom:{abstand // 2}px">{_e(kopf)}</div>' if kopf else "")
        + f'<h1 style="margin:0 0 {abstand}px 0;color:{g["text_hell"]};font-size:24px">{_e(felder.get("betreff"))}</h1>'
        f'{abschnitte}'
        + (f'<div style="margin:{abstand}px 0">{knopf}</div>' if knopf else "")
        + (f'<p style="color:{g["text_leise"]};font-size:13px;margin:{abstand}px 0 0 0">{_e(fuss)}</p>' if fuss else "")
        + f'</td></tr></table>'
        f'<div style="max-width:{int(breite)}px;color:{g["text_leise"]};font-size:11px;line-height:1.5;padding:{abstand}px 8px">'
        f'{impressum_html}<br>{abmelden}</div>'
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
