"""Entwuerfe und Block-Editor (Stufe B, Tag 2, 25.11.2026): Newsletter-Entwuerfe der Firma auflisten,
die Texte der Bloecke in einem Formular ohne Skript aendern, als neue Fassung speichern und in die
Freigaben von Sales einreichen (oder zurueckziehen).

- Einreichen setzt nur den Status 'eingereicht' (freigabe.einreichen). Versendet wird hier nichts;
  freigeben und senden bleiben in Sales.
- Bearbeitet werden nur Bloecke im Block-Format: Ueberschrift, Text, Knopf (Text und https-Ziel) und der
  Alternativtext von Bildern. Aufbau, Farben und Bilder selbst bleiben, wie sie sind (Bilder zuordnen kommt
  mit der eigenen Medienablage). Collagen und Feld-Newsletter zeigen hier nur die Vorschau.
- Gespeichert wird immer auf der Fassung, die das Formular gezeigt hat (basis_fassung); hat inzwischen
  jemand anders gespeichert, lehnt die Datenbank ab und die Seite sagt das.
- Jeder Schritt prueft die Firma (Cookie wie ueberall) gegen den Inhalt."""
from __future__ import annotations

import copy
import re
import uuid

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, huelle, newsletter
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e
from spaces.marketing.api.oberflaeche.vorlagen import _format_wahl, _fmt, _rahmen, vorschau_adresse

SEITE = f"{PFAD}/entwuerfe"
VON = "Betreiber (Marketing-Reiter)"
FORM_GRENZE = 256 * 1024
GRENZEN = {"betreff": 200, "vorschautext": 300, "Heading": 300, "Text": 10000, "Button": 100,
           "url": 2000, "alt": 300}
ARTEN = ("Heading", "Text", "Button", "Image")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
ZEIGEN = ("entwurf", "zurueckgegeben", "eingereicht")

TEXTE = {
    "de": {
        "titel": "Entwürfe",
        "hinweis": "Hier bearbeitest du Newsletter-Entwürfe und reichst sie ein. Freigeben und versenden geht "
                   "weiter nur in Sales.",
        "leer": "Keine Entwürfe. Einen neuen legst du unter „Vorlagen“ an.",
        "gruppe_entwurf": "In Arbeit",
        "gruppe_zurueckgegeben": "Zurückgegeben",
        "gruppe_eingereicht": "Zur Freigabe in Sales",
        "fassung": "Fassung {n}",
        "rueckmeldungen": "Offene Rückmeldungen: {n}",
        "format_seite": "Collage",
        "format_felder": "Felder",
        "editor_titel": "Entwurf bearbeiten",
        "status_entwurf": "in Arbeit",
        "status_eingereicht": "liegt zur Freigabe in Sales",
        "status_anders": "Status: {status}",
        "betreff": "Betreff",
        "vorschautext": "Vorschautext (erste Zeile im Postfach)",
        "art_Heading": "Überschrift",
        "art_Text": "Text",
        "art_Button": "Knopf",
        "art_Image": "Bild",
        "knopf_ziel": "Ziel (https://…)",
        "bild_alt": "Alternativtext",
        "bild_datei": "Datei: {name}",
        "speichern": "Als neue Fassung speichern",
        "einreichen": "In Sales zur Freigabe einreichen",
        "einreichen_hinweis": "Eingereicht wird die zuletzt gespeicherte Fassung. Erst speichern, dann einreichen.",
        "zurueckziehen": "Einreichung zurückziehen",
        "gespeichert": "Gespeichert als Fassung {n}.",
        "unveraendert": "Nichts geändert, keine neue Fassung.",
        "eingereicht": "Eingereicht. Der Entwurf liegt jetzt in den Freigaben von Sales.",
        "zurueckgezogen": "Zurückgezogen. Der Entwurf ist wieder in Arbeit.",
        "nur_vorschau": "Diesen Entwurf (Collage oder Felder) kannst du hier noch nicht bearbeiten, nur ansehen.",
        "keine_bloecke": "Dieser Entwurf hat keine bearbeitbaren Blöcke.",
        "rm_titel": "Rückmeldungen aus Sales",
        "rm_von": "{von}, {datum}",
        "zur_liste": "Zur Liste",
        "f_fremd": "Dieser Entwurf gehört zu einer anderen Firma.",
        "f_kein_entwurf": "Diesen Entwurf gibt es nicht.",
        "f_status": "Das geht nur bei einem Entwurf in Arbeit.",
        "f_status_eingereicht": "Zurückziehen geht nur, solange der Entwurf zur Freigabe liegt.",
        "f_format": "Nur Entwürfe im Block-Format lassen sich hier bearbeiten.",
        "f_veraltet": "Inzwischen gibt es eine neuere Fassung. Bitte die Seite neu laden und die Änderung "
                      "noch einmal machen.",
        "f_zu_lang": "Ein Feld ist zu lang ({feld}, höchstens {max} Zeichen).",
        "f_url": "Das Ziel eines Knopfs muss mit https:// beginnen.",
        "f_basis": "Fehlende Fassung im Formular. Bitte die Seite neu laden.",
    },
    "en": {
        "titel": "Drafts",
        "hinweis": "Edit newsletter drafts and submit them here. Approving and sending still happen only in "
                   "Sales.",
        "leer": "No drafts. Create a new one under “Templates”.",
        "gruppe_entwurf": "In progress",
        "gruppe_zurueckgegeben": "Returned",
        "gruppe_eingereicht": "Awaiting approval in Sales",
        "fassung": "Version {n}",
        "rueckmeldungen": "Open feedback: {n}",
        "format_seite": "Collage",
        "format_felder": "Fields",
        "editor_titel": "Edit draft",
        "status_entwurf": "in progress",
        "status_eingereicht": "awaiting approval in Sales",
        "status_anders": "Status: {status}",
        "betreff": "Subject",
        "vorschautext": "Preview text (first line in the inbox)",
        "art_Heading": "Heading",
        "art_Text": "Text",
        "art_Button": "Button",
        "art_Image": "Image",
        "knopf_ziel": "Target (https://…)",
        "bild_alt": "Alt text",
        "bild_datei": "File: {name}",
        "speichern": "Save as new version",
        "einreichen": "Submit for approval in Sales",
        "einreichen_hinweis": "The last saved version is submitted. Save first, then submit.",
        "zurueckziehen": "Withdraw submission",
        "gespeichert": "Saved as version {n}.",
        "unveraendert": "Nothing changed, no new version.",
        "eingereicht": "Submitted. The draft is now in the Sales approvals.",
        "zurueckgezogen": "Withdrawn. The draft is in progress again.",
        "nur_vorschau": "This draft (collage or fields) cannot be edited here yet, only viewed.",
        "keine_bloecke": "This draft has no editable blocks.",
        "rm_titel": "Feedback from Sales",
        "rm_von": "{von}, {datum}",
        "zur_liste": "Back to list",
        "f_fremd": "This draft belongs to another company.",
        "f_kein_entwurf": "This draft does not exist.",
        "f_status": "This only works for a draft in progress.",
        "f_status_eingereicht": "Withdrawing only works while the draft awaits approval.",
        "f_format": "Only drafts in block format can be edited here.",
        "f_veraltet": "There is a newer version by now. Please reload the page and make the change again.",
        "f_zu_lang": "A field is too long ({feld}, at most {max} characters).",
        "f_url": "A button's target must start with https://.",
        "f_basis": "Missing version in the form. Please reload the page.",
    },
}


def tx(s: str, schluessel: str, **werte) -> str:
    return huelle._sprache.text(TEXTE, s, schluessel, **werte)


# --- Bloecke ------------------------------------------------------------------------------------

def bearbeitbare(dok: dict) -> list[tuple[str, dict]]:
    """(id, Block) der bearbeitbaren Bloecke in Lesereihenfolge; Rahmen und Spalten werden aufgeloest."""
    aus: list[tuple[str, dict]] = []
    gesehen: set[str] = set()

    def gehen(ids) -> None:
        for bid in ids if isinstance(ids, list) else []:
            if not isinstance(bid, str) or bid in gesehen or not _ID.fullmatch(bid):
                continue
            gesehen.add(bid)
            b = dok.get(bid)
            if not isinstance(b, dict):
                continue
            props = ((b.get("data") or {}).get("props") or {}) if isinstance(b.get("data"), dict) else {}
            if b.get("type") == "Container":
                gehen(props.get("childrenIds"))
            elif b.get("type") == "ColumnsContainer":
                for spalte in props.get("columns") or []:
                    if isinstance(spalte, dict):
                        gehen(spalte.get("childrenIds"))
            elif b.get("type") in ARTEN:
                aus.append((bid, b))

    wurzel = dok.get("root") if isinstance(dok.get("root"), dict) else {}
    gehen(((wurzel.get("data") or {}) if isinstance(wurzel.get("data"), dict) else {}).get("childrenIds"))
    return aus


def _props(b: dict) -> dict:
    data = b.get("data") if isinstance(b.get("data"), dict) else {}
    p = data.get("props") if isinstance(data.get("props"), dict) else {}
    return p


def _wert(p: dict, feld: str) -> str:
    w = p.get(feld)
    return w if isinstance(w, str) else ""


class Abgelehnt(ValueError):
    """Eingabe passt nicht; die Nachricht ist schon der Satz fuer die Seite."""


def _feld(form: dict, name: str, alt: str, grenze: int, s: str, beschriftung: str, einzeilig: bool) -> str:
    roh = form.get(name)
    if roh is None:
        return alt
    text = str(roh).replace("\r\n", "\n").replace("\r", "\n")
    if einzeilig:
        text = " ".join(text.split("\n")).strip()
    if len(text) > grenze:
        raise Abgelehnt(tx(s, "f_zu_lang", feld=beschriftung, max=grenze))
    return text


def anwenden(dok: dict, form: dict, s: str) -> dict:
    """Neues Dokument mit den Formularwerten; nur bekannte Felder bekannter Bloecke werden gesetzt."""
    neu = copy.deepcopy(dok)
    for bid, b in bearbeitbare(neu):
        p = _props(b)
        if not p and isinstance(b.get("data"), dict):
            b["data"]["props"] = p
        art = b.get("type")
        name = tx(s, f"art_{art}")
        if art in ("Heading", "Text", "Button"):
            wert = _feld(form, f"t:{bid}", _wert(p, "text"), GRENZEN[art], s, name, art != "Text")
            if wert != _wert(p, "text"):
                p["text"] = wert
        if art == "Button":
            ziel = _feld(form, f"u:{bid}", _wert(p, "url"), GRENZEN["url"], s, name, True)
            if ziel != _wert(p, "url"):
                if not ziel.startswith("https://") or any(c.isspace() or c in "\"<>" for c in ziel):
                    raise Abgelehnt(tx(s, "f_url"))
                p["url"] = ziel
        if art == "Image":
            alt = _feld(form, f"a:{bid}", _wert(p, "alt"), GRENZEN["alt"], s, name, True)
            if alt != _wert(p, "alt"):
                p["alt"] = alt
    return neu


# --- Liste --------------------------------------------------------------------------------------

def _zahl(roh) -> int:
    return newsletter._zahl(roh)


def karte(k: Kontext, i: dict) -> str:
    n = _zahl(i.get("fassungen"))
    teile = [k.t(TEXTE, "fassung", n=n)] if n >= 1 else []
    teile.append(k.datum(i.get("erstellt_am")))
    fmt = str(i.get("format") or "")
    if fmt in ("seite", "felder"):
        teile.append(k.t(TEXTE, f"format_{fmt}"))
    offen = _zahl(i.get("offene_rueckmeldungen"))
    rueck = f'<p class="meta">{e(k.t(TEXTE, "rueckmeldungen", n=offen))}</p>' if offen else ""
    iid = str(i.get("id") or "")
    return (f'<article class="karte"><p class="titel"><a href="{e(SEITE)}/{e(iid)}">'
            f'{e(str(i.get("titel") or ""))}</a></p><p class="meta">{e(" · ".join(teile))}</p>{rueck}</article>')


async def bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
    alle = [i for i in await run_in_threadpool(daten.newsletter, k.firma)
            if i.get("art", "newsletter") == "newsletter" and _gueltige_id(i.get("id"))]
    gruppen = {g: [i for i in alle if newsletter.reiter_von(i) == g] for g in ZEIGEN}
    if not any(gruppen.values()):
        return k.t(TEXTE, "titel"), f'<p class="leer">{e(k.t(TEXTE, "leer"))}</p>'
    teile = []
    for g in ZEIGEN:
        if gruppen[g]:
            karten = "".join(karte(k, i) for i in gruppen[g])
            teile.append(f'<h2>{e(k.t(TEXTE, "gruppe_" + g))} ({len(gruppen[g])})</h2>'
                         f'<div class="karten">{karten}</div>')
    return k.t(TEXTE, "titel"), "".join(teile)


def _gueltige_id(roh) -> bool:
    try:
        uuid.UUID(str(roh))
        return True
    except ValueError:
        return False


# --- Editor -------------------------------------------------------------------------------------

def _laden_pruefen(antwort: dict, firma: str | None, s: str) -> tuple[dict, dict | None]:
    kopf = antwort.get("inhalt") if isinstance(antwort.get("inhalt"), dict) else {}
    if not kopf:
        raise daten.DatenFehler("abgelehnt", tx(s, "f_kein_entwurf"))
    if kopf.get("mandant") != firma:
        raise daten.DatenFehler("abgelehnt", tx(s, "f_fremd"))
    fassungen = [f for f in (antwort.get("fassungen") or []) if isinstance(f, dict)]
    return kopf, (fassungen[0] if fassungen else None)


def _iid(request: Request, s: str) -> str:
    try:
        return str(uuid.UUID(str(request.path_params.get("iid", ""))))
    except ValueError:
        raise daten.DatenFehler("abgelehnt", tx(s, "f_kein_entwurf"))


def _formular_csrf() -> str:
    return f'<input type="hidden" name="csrf" value="{e(huelle.csrf_marke())}">'


def _block_feld(k: Kontext, bid: str, b: dict) -> str:
    art = b.get("type")
    p = _props(b)
    name = e(k.t(TEXTE, f"art_{art}"))
    if art == "Heading":
        inhalt = (f'<input type="text" name="t:{e(bid)}" value="{e(_wert(p, "text"))}" '
                  f'maxlength="{GRENZEN["Heading"]}">')
    elif art == "Text":
        inhalt = (f'<textarea name="t:{e(bid)}" rows="6" maxlength="{GRENZEN["Text"]}">'
                  f'{e(_wert(p, "text"))}</textarea>')
    elif art == "Button":
        inhalt = (f'<input type="text" name="t:{e(bid)}" value="{e(_wert(p, "text"))}" '
                  f'maxlength="{GRENZEN["Button"]}">'
                  f'<label>{e(k.t(TEXTE, "knopf_ziel"))} <input type="url" name="u:{e(bid)}" '
                  f'value="{e(_wert(p, "url"))}" maxlength="{GRENZEN["url"]}"></label>')
    else:
        datei = _wert(p, "url")
        datei = datei[len("medien:"):] if datei.startswith("medien:") else datei
        inhalt = (f'<p class="meta">{e(k.t(TEXTE, "bild_datei", name=datei or "?"))}</p>'
                  f'<label>{e(k.t(TEXTE, "bild_alt"))} <input type="text" name="a:{e(bid)}" '
                  f'value="{e(_wert(p, "alt"))}" maxlength="{GRENZEN["alt"]}"></label>')
    return f'<fieldset class="block"><legend>{name}</legend>{inhalt}</fieldset>'


def _rueckmeldungen(k: Kontext, antwort: dict) -> str:
    offen = [r for r in (antwort.get("rueckmeldungen") or []) if isinstance(r, dict) and not r.get("erledigt")]
    if not offen:
        return ""
    punkte = "".join(f'<li><p>{e(str(r.get("text") or ""))}</p><p class="meta">'
                     f'{e(k.t(TEXTE, "rm_von", von=str(r.get("von") or ""), datum=k.datum(r.get("am"))))}</p></li>'
                     for r in offen)
    return f'<section class="rueckmeldungen"><h2>{e(k.t(TEXTE, "rm_titel"))}</h2><ul>{punkte}</ul></section>'


def _meldung(k: Kontext) -> str:
    q = k.request.query_params
    if q.get("gespeichert", "").isdigit():
        return f'<p class="erfolg">{e(k.t(TEXTE, "gespeichert", n=int(q["gespeichert"])))}</p>'
    for schluessel in ("unveraendert", "eingereicht", "zurueckgezogen"):
        if q.get(schluessel) == "1":
            return f'<p class="erfolg">{e(k.t(TEXTE, schluessel))}</p>'
    return ""


async def editor_bauen(k: Kontext) -> tuple[str, str]:
    k.rahmen = True
    k.hinweis = k.t(TEXTE, "hinweis")
    iid = _iid(k.request, k.sprache)
    antwort = await run_in_threadpool(daten.entwurf, iid)
    kopf, neueste = _laden_pruefen(antwort, k.firma, k.sprache)
    titel = str(kopf.get("titel") or "")
    status = str(kopf.get("status") or "")
    hier = f"{SEITE}/{iid}"
    zeile = (k.t(TEXTE, "status_" + status) if status in ("entwurf", "eingereicht")
             else k.t(TEXTE, "status_anders", status=status))
    rumpf = (_meldung(k) + f'<h2>{e(titel)}</h2><p class="meta">{e(zeile)}'
             + (f' · {e(k.t(TEXTE, "fassung", n=neueste["fassung"]))}' if neueste else "") + "</p>"
             + _rueckmeldungen(k, antwort))
    n = neueste.get("fassung") if neueste else None
    hat_fassung = isinstance(n, int) and not isinstance(n, bool) and n >= 1
    fmt_inhalt = neueste.get("format") if neueste else None
    if status == "entwurf" and hat_fassung and fmt_inhalt == "bloecke" and isinstance(neueste.get("bloecke"), dict):
        felder = neueste.get("felder") if isinstance(neueste.get("felder"), dict) else {}
        bloecke = bearbeitbare(neueste["bloecke"])
        felder_html = "".join(_block_feld(k, bid, b) for bid, b in bloecke) or (
            f'<p class="meta">{e(k.t(TEXTE, "keine_bloecke"))}</p>')
        rumpf += (f'<form method="post" action="{e(hier)}/speichern" class="editor">{_formular_csrf()}'
                  f'<input type="hidden" name="basis" value="{n}">'
                  f'<label>{e(k.t(TEXTE, "betreff"))} <input type="text" name="betreff" '
                  f'value="{e(str(felder.get("betreff") or ""))}" maxlength="{GRENZEN["betreff"]}"></label>'
                  f'<label>{e(k.t(TEXTE, "vorschautext"))} <input type="text" name="vorschautext" '
                  f'value="{e(str(felder.get("vorschautext") or ""))}" maxlength="{GRENZEN["vorschautext"]}"></label>'
                  f'{felder_html}<p><button type="submit">{e(k.t(TEXTE, "speichern"))}</button></p></form>'
                  f'<form method="post" action="{e(hier)}/einreichen" class="anlegen">{_formular_csrf()}'
                  f'<button type="submit">{e(k.t(TEXTE, "einreichen"))}</button>'
                  f'<span class="meta">{e(k.t(TEXTE, "einreichen_hinweis"))}</span></form>')
    elif status == "eingereicht":
        rumpf += (f'<form method="post" action="{e(hier)}/zurueckziehen" class="anlegen">{_formular_csrf()}'
                  f'<button type="submit">{e(k.t(TEXTE, "zurueckziehen"))}</button></form>')
    elif status == "entwurf" and hat_fassung:
        rumpf += f'<p class="meta">{e(k.t(TEXTE, "nur_vorschau"))}</p>'
    if hat_fassung and fmt_inhalt != "seite":
        fmt = _fmt(k)
        rumpf += ('<section class="vorschau">'
                  + _format_wahl(k, fmt, lambda f: hier + ("?format=handy" if f == "handy" else ""))
                  + _rahmen(k, vorschau_adresse("entwurf", str(k.firma), iid, n, fmt), fmt, titel) + "</section>")
    rumpf += f'<p><a href="{SEITE}">{e(k.t(TEXTE, "zur_liste"))}</a></p>'
    return k.t(TEXTE, "editor_titel"), rumpf


# --- Formulare ----------------------------------------------------------------------------------

async def _vorbereiten(request: Request, grenze: int = huelle.FORM_MAX):
    """Schluessel, CSRF, Firma, Inhalt laden und Firma pruefen. (Fehlerseite | None, Sprache, Formular, iid,
    Antwort, Kopf, neueste Fassung)."""
    fehler, s, form = await huelle._formular(request, grenze)
    if fehler is not None:
        return fehler, s, form, "", {}, {}, None
    try:
        iid = _iid(request, s)
        firmen = await run_in_threadpool(daten.firmen)
        firma = huelle.firma_waehlen(request.cookies.get(huelle.COOKIE_FIRMA), firmen)
        antwort = await run_in_threadpool(daten.entwurf, iid)
        kopf, neueste = _laden_pruefen(antwort, firma, s)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f), s, form, "", {}, {}, None
    return None, s, form, iid, antwort, kopf, neueste


def _abgelehnt(s: str, text: str) -> Response:
    return huelle.fehlerseite(s, 422, huelle.t(s, "f_eingabe_titel"), text)


async def speichern(request: Request) -> Response:
    fehler, s, form, iid, _, kopf, neueste = await _vorbereiten(request, FORM_GRENZE)
    if fehler is not None:
        return fehler
    if kopf.get("status") != "entwurf":
        return _abgelehnt(s, tx(s, "f_status"))
    if not neueste or neueste.get("format") != "bloecke" or not isinstance(neueste.get("bloecke"), dict):
        return _abgelehnt(s, tx(s, "f_format"))
    basis = str(form.get("basis") or "")
    if not basis.isdigit():
        return _abgelehnt(s, tx(s, "f_basis"))
    if int(basis) != neueste.get("fassung"):
        return _abgelehnt(s, tx(s, "f_veraltet"))
    felder = neueste.get("felder") if isinstance(neueste.get("felder"), dict) else {}
    try:
        alt_betreff, alt_vorschau = str(felder.get("betreff") or ""), str(felder.get("vorschautext") or "")
        betreff = _feld(form, "betreff", alt_betreff, GRENZEN["betreff"], s, tx(s, "betreff"), True)
        vorschau = _feld(form, "vorschautext", alt_vorschau, GRENZEN["vorschautext"], s, tx(s, "vorschautext"), True)
        bloecke = anwenden(neueste["bloecke"], form, s)
    except Abgelehnt as f:
        return _abgelehnt(s, str(f))
    hier = f"{SEITE}/{iid}"
    if bloecke == neueste["bloecke"] and betreff == alt_betreff and vorschau == alt_vorschau:
        return RedirectResponse(f"{hier}?unveraendert=1", status_code=303)
    try:
        n = await run_in_threadpool(daten.bloecke_speichern, iid, int(basis), betreff, vorschau, bloecke)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{hier}?gespeichert={n}", status_code=303)


async def einreichen(request: Request) -> Response:
    fehler, s, _, iid, _, kopf, _ = await _vorbereiten(request)
    if fehler is not None:
        return fehler
    if kopf.get("status") != "entwurf":
        return _abgelehnt(s, tx(s, "f_status"))
    try:
        await run_in_threadpool(daten.einreichen, iid, VON)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{SEITE}/{iid}?eingereicht=1", status_code=303)


async def zurueckziehen(request: Request) -> Response:
    fehler, s, _, iid, _, kopf, _ = await _vorbereiten(request)
    if fehler is not None:
        return fehler
    if kopf.get("status") != "eingereicht":
        return _abgelehnt(s, tx(s, "f_status_eingereicht"))
    try:
        await run_in_threadpool(daten.zurueckziehen, iid, VON)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{SEITE}/{iid}?zurueckgezogen=1", status_code=303)
