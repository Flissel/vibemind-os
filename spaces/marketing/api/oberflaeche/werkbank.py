"""Werkbank im Marketing-Reiter (Stufe B, Punkt 3): lange Videos Szene fuer Szene abnehmen.
Vorbild: sales-claw ui_werkbank (Liste, Uebersicht, Szenenseite, fertiges Video).

- /oberflaeche/werkbank: die langen Videos der gewaehlten Firma mit Stand und "warten auf dich".
- /oberflaeche/werkbank/{aid}: Gliederung, Quellen, Szenenliste, fertiges Video mit Kopierfeldern.
- /oberflaeche/werkbank/{aid}/szene/{nr}: Sprechtext, Aussagen mit Quellen, Bildidee, Clip, Wuensche, Knoepfe.

Lesen geht immer. Wer abnimmt, loest Arbeit aus (Text -> Clip, Nochmal, Zusammenfuegen): darum stehen
Urteil, Nochmal versuchen, Wieder oeffnen, Nochmal schreiben und Neu zusammenfuegen hinter demselben
Kosten-Schalter wie das Bestellen (bestellen.an, MARKETING_BESTELLEN, Vorgabe aus). Nur "Wunsch speichern"
loest nichts aus und geht immer. Geprueft wird in der Marketing-API (werkbank.py, eine Wahrheit); die Seite
schickt nur, was sie selbst anzeigen konnte. Clips und Video spielen ueber signierte Adressen (medien.py).
Die Uebergaben aus Rowboat und der Handy-Sprung (/v/{kennung}) bleiben vorerst in Sales. Versendet wird nichts."""
from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import bestellen, daten, huelle, medien
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e

SEITE = f"{PFAD}/werkbank"
WUNSCH_MAX = 2000
NR_MAX = 999
FORM_GRENZE = 32 * 1024
_AID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_KENNUNG = re.compile(r"^wb-[a-z2-7]{10}$")
_NR = re.compile(r"^[0-9]{1,3}$")
_CLIP = re.compile(r"^wb-[0-9a-f]{8}-(?:s[0-9]{2,3}|video)-f[0-9]{1,3}\.mp4$")
STAENDE = ("geplant", "text_wartet", "text_ok", "wird_erzeugt", "clip_wartet", "fertig")
CHIP = {"text_wartet": "akzent", "clip_wartet": "akzent", "fertig": "gut"}
WARTET = ("text_wartet", "clip_wartet")
MIT_CLIP = ("clip_wartet", "fertig")
AUFTRAG = ("offen", "in_arbeit", "werkbank", "erzeugt", "wartet_freigabe", "fertig", "abgebrochen", "fehler",
           "abgelaufen")
BILDARTEN = ("slide", "avatar_gross", "avatar_slide", "clip")
VORLAGEN = ("titel", "stichpunkte", "zahl", "zitat", "vergleich", "agenda")
MELDUNGEN = ("gespeichert", "passt", "nochmal", "versucht", "geoeffnet", "neu", "neu_bauen", "entschieden")

TEXTE = {
    "de": {
        "titel": "Lange Videos",
        "hinweis": "Lange Videos Szene für Szene abnehmen. Versendet wird hier nichts.",
        "leer": "Noch kein langes Video für diese Firma. Bestellt wird es vorerst über den Salesman.",
        "ohne_titel": "Video ohne Titel",
        "warten": "{n} warten auf dich",
        "a_offen": "bestellt", "a_in_arbeit": "wird vorbereitet", "a_werkbank": "in der Werkbank",
        "a_erzeugt": "Video ist fertig", "a_wartet_freigabe": "wartet auf Freigabe", "a_fertig": "fertig",
        "a_abgebrochen": "abgebrochen", "a_fehler": "fehlgeschlagen", "a_abgelaufen": "abgelaufen",
        "s_geplant": "wird geplant", "s_text_wartet": "Text wartet auf dich", "s_text_ok": "Text ist ok",
        "s_wird_erzeugt": "Clip entsteht", "s_clip_wartet": "Clip wartet auf dich", "s_fertig": "fertig",
        "fehler_chip": "Fehler beim Erzeugen", "braucht_wunsch": "braucht deinen Wunsch",
        "zaehlung": "{n} Szenen, {fertig} fertig, {warten} warten auf dich.",
        "alle": "Alle langen Videos", "alle_szenen": "Alle Szenen",
        "szenen": "Szenen", "szene": "Szene {nr}", "quellen": "Quellen", "fehlt": "Dazu fehlt mir noch etwas:",
        "noch_kein_werk": "Für dieses Video gibt es noch keine Gliederung.",
        "gliederung_frage": "Passt die Gliederung?", "text_frage": "Ist der Text so gut?",
        "clip_frage": "Passt der Clip?",
        "wunsch": "Dein Wunsch (freiwillig, bis {n} Zeichen)", "anders": "Was soll anders werden?",
        "passt": "Passt", "nochmal": "Nochmal", "speichern": "Wunsch speichern",
        "nochmal_versuchen": "Nochmal versuchen", "oeffnen": "Szene wieder öffnen",
        "neu_schreiben": "Nochmal schreiben", "neu_bauen": "Video neu zusammenfügen",
        "aus": "Abnehmen ist ausgeschaltet (Kosten-Schalter): jede Abnahme löst Arbeit aus. Lesen und Wünsche "
               "speichern gehen.",
        "erzeugen_fehler": "Beim Erzeugen ist etwas schiefgegangen: {text}",
        "laenge": "Länge {zeit}",
        "sprechtext": "Sprechtext", "kein_text": "Der Text ist noch nicht geschrieben.",
        "aussagen": "Aussagen und Quellen", "quelle": "Quelle: {q}",
        "bildidee": "Bildidee", "keine_bildidee": "Noch keine Bildidee.", "bild_n": "Bild {i}",
        "uebergang": "Übergang zur nächsten Szene: {art}", "ue_schnitt": "Schnitt", "ue_blende": "Blende",
        "b_slide": "Slide mit Stimme aus dem Off", "b_avatar_gross": "Avatar im ganzen Bild",
        "b_avatar_slide": "Avatar vor der Slide", "b_clip": "Filmischer Clip",
        "v_titel": "Titel", "v_stichpunkte": "Stichpunkte", "v_zahl": "Zahl", "v_zitat": "Zitat",
        "v_vergleich": "Vergleich", "v_agenda": "Agenda",
        "belege": "Belege", "keine_belege": "Keine Belege angegeben.", "wuensche": "Bisherige Wünsche",
        "video": "Das fertige Video", "video_veraltet": "Seit dem Zusammenfügen hat sich eine Szene geändert.",
        "video_titel": "Titel", "beschreibung": "Beschreibung", "zeitmarken": "Zeitmarken für YouTube",
        "m_gespeichert": "Dein Wunsch ist gespeichert.", "m_passt": "Danke, das ist abgenommen.",
        "m_nochmal": "Verstanden, das wird noch einmal gemacht.", "m_versucht": "Die Szene wird noch einmal versucht.",
        "m_geoeffnet": "Die Szene ist wieder offen.", "m_neu": "Danke – das wird mit deinem Wunsch neu geschrieben.",
        "m_neu_bauen": "Das Video wird neu zusammengefügt.", "m_entschieden": "Schon entschieden – Seite neu geladen.",
        "f_wunsch_lang": "Der Wunsch darf höchstens {n} Zeichen haben.",
        "f_wunsch_leer": "Schreib bitte auf, was anders werden soll.",
        "f_unbekannt": "Das gibt es bei dieser Firma nicht.",
        "f_schon": "Schon entschieden oder gerade nicht möglich. Dein Wunsch ist nicht gespeichert; hier steht er "
                   "zum Kopieren.",
        "zurueck_seite": "Seite neu laden",
    },
    "en": {
        "titel": "Long videos",
        "hinweis": "Review long videos scene by scene. Nothing is sent from here.",
        "leer": "No long video for this company yet. For now they are ordered through the Salesman.",
        "ohne_titel": "Untitled video",
        "warten": "{n} waiting for you",
        "a_offen": "ordered", "a_in_arbeit": "being prepared", "a_werkbank": "in the workbench",
        "a_erzeugt": "video is ready", "a_wartet_freigabe": "awaiting approval", "a_fertig": "done",
        "a_abgebrochen": "cancelled", "a_fehler": "failed", "a_abgelaufen": "expired",
        "s_geplant": "being planned", "s_text_wartet": "text waiting for you", "s_text_ok": "text is ok",
        "s_wird_erzeugt": "clip being made", "s_clip_wartet": "clip waiting for you", "s_fertig": "done",
        "fehler_chip": "error while making", "braucht_wunsch": "needs your wish",
        "zaehlung": "{n} scenes, {fertig} done, {warten} waiting for you.",
        "alle": "All long videos", "alle_szenen": "All scenes",
        "szenen": "Scenes", "szene": "Scene {nr}", "quellen": "Sources", "fehlt": "I'm still missing something:",
        "noch_kein_werk": "This video has no outline yet.",
        "gliederung_frage": "Is the outline right?", "text_frage": "Is the text good?",
        "clip_frage": "Is the clip right?",
        "wunsch": "Your wish (optional, up to {n} characters)", "anders": "What should change?",
        "passt": "Looks good", "nochmal": "Again", "speichern": "Save wish",
        "nochmal_versuchen": "Try again", "oeffnen": "Reopen scene",
        "neu_schreiben": "Write again", "neu_bauen": "Put the video together again",
        "aus": "Reviewing is switched off (cost switch): every review triggers work. Reading and saving wishes "
               "still work.",
        "erzeugen_fehler": "Something went wrong while making it: {text}",
        "laenge": "Length {zeit}",
        "sprechtext": "Voice-over", "kein_text": "The text hasn't been written yet.",
        "aussagen": "Claims and sources", "quelle": "Source: {q}",
        "bildidee": "Visual idea", "keine_bildidee": "No visual idea yet.", "bild_n": "Image {i}",
        "uebergang": "Transition to the next scene: {art}", "ue_schnitt": "cut", "ue_blende": "fade",
        "b_slide": "Slide with voice-over", "b_avatar_gross": "Avatar full frame",
        "b_avatar_slide": "Avatar in front of the slide", "b_clip": "Cinematic clip",
        "v_titel": "Title", "v_stichpunkte": "Bullet points", "v_zahl": "Number", "v_zitat": "Quote",
        "v_vergleich": "Comparison", "v_agenda": "Agenda",
        "belege": "Evidence", "keine_belege": "No evidence given.", "wuensche": "Earlier wishes",
        "video": "The finished video", "video_veraltet": "A scene has changed since the video was put together.",
        "video_titel": "Title", "beschreibung": "Description", "zeitmarken": "Timestamps for YouTube",
        "m_gespeichert": "Your wish is saved.", "m_passt": "Thanks, that's approved.",
        "m_nochmal": "Understood, it will be done again.", "m_versucht": "The scene will be tried again.",
        "m_geoeffnet": "The scene is open again.", "m_neu": "Thanks – it will be rewritten with your wish.",
        "m_neu_bauen": "The video is being put together again.", "m_entschieden": "Already decided – page reloaded.",
        "f_wunsch_lang": "The wish may have at most {n} characters.",
        "f_wunsch_leer": "Please write down what should change.",
        "f_unbekannt": "That doesn't exist for this company.",
        "f_schon": "Already decided or not possible right now. Your wish wasn't saved; here it is to copy.",
        "zurueck_seite": "Reload page",
    },
}


def tx(s: str, schluessel: str, **werte) -> str:
    return huelle._sprache.text(TEXTE, s, schluessel, **werte)


def _liste(wert) -> list:
    return wert if isinstance(wert, list) else []


def _kurz(wert, grenze: int = 300) -> str:
    return " ".join(str(wert).split())[:grenze] if isinstance(wert, str) and wert.strip() else ""


def _ganz(wert) -> int | None:
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else None


def _csrf() -> str:
    return f'<input type="hidden" name="csrf" value="{e(huelle.csrf_marke())}">'


def seite_url(aid: str, nr: int | None = None, meldung: str | None = None) -> str:
    return f"{SEITE}/{aid}" + (f"/szene/{nr}" if nr is not None else "") + (f"?m={meldung}" if meldung else "")


def _meldung(k: Kontext) -> str:
    m = k.request.query_params.get("m", "")
    return f'<p class="erfolg">{e(k.t(TEXTE, "m_" + m))}</p>' if m in MELDUNGEN else ""


def _quelle_text(q) -> str:
    if isinstance(q, str):
        return _kurz(q)
    if isinstance(q, dict):
        teile = [_kurz(q.get(f)) for f in ("titel", "name", "datei", "url", "stelle")]
        return " · ".join(dict.fromkeys(t for t in teile if t))
    return ""


def _minuten(sekunden) -> str:
    if isinstance(sekunden, bool) or not isinstance(sekunden, (int, float)) or sekunden <= 0:
        return ""
    s = int(round(float(sekunden)))
    return f"{s // 60}:{s % 60:02d}"


def _stand_chip(k: Kontext, stand: str, fehler: bool = False) -> str:
    chip = (f'<span class="chip {CHIP.get(stand, "")}">{e(k.t(TEXTE, "s_" + stand))}</span>'
            if stand in STAENDE else "")
    return chip + (f' <span class="chip schlecht">{e(k.t(TEXTE, "fehler_chip"))}</span>' if fehler else "")


def _gesperrt(k: Kontext) -> tuple[str, str]:
    if bestellen.an():
        return "", ""
    return f'<p class="meta">{e(k.t(TEXTE, "aus"))}</p>', " disabled"


def _clip(k: Kontext, datei, titel: str, dauer=None) -> str:
    if not isinstance(datei, str) or not _CLIP.fullmatch(datei):
        return ""
    spieler = medien.video_html(str(k.firma or ""), datei, None, titel, "quer")
    zeit = _minuten(dauer)
    return (f'<div class="abspielen">{spieler}</div>' if spieler else "") + (
        f'<p class="meta">{e(k.t(TEXTE, "laenge", zeit=zeit))}</p>' if spieler and zeit else "")


# --- Liste ---------------------------------------------------------------------------------------

async def bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
    werke = [w for w in await run_in_threadpool(daten.werke, str(k.firma)) if _AID.fullmatch(str(w.get("id") or ""))]
    karten = []
    for w in werke:
        titel = _kurz(w.get("thema")) or _kurz(w.get("titel")) or k.t(TEXTE, "ohne_titel")
        stand = str(w.get("stand") or "")
        wb = w.get("werkbank") if isinstance(w.get("werkbank"), dict) else {}
        n = _ganz(wb.get("wartet"))
        warten = f' <span class="chip akzent">{e(k.t(TEXTE, "warten", n=n))}</span>' if n and n > 0 else ""
        karten.append(f'<a class="karte" href="{e(seite_url(str(w["id"])))}"><p class="titel">{e(titel)}</p>'
                      f'<p class="meta">{e(k.t(TEXTE, "a_" + stand)) if stand in AUFTRAG else ""} · '
                      f'{e(k.datum(w.get("angelegt")))}{warten}</p></a>')
    inhalt = (f'<div class="karten">{"".join(karten)}</div>' if karten
              else f'<p class="leer">{e(k.t(TEXTE, "leer"))}</p>')
    return k.t(TEXTE, "titel"), inhalt


# --- Uebersicht ----------------------------------------------------------------------------------

def _aid(request: Request, s: str) -> str:
    aid = str(request.path_params.get("aid", ""))
    if not _AID.fullmatch(aid):
        raise daten.DatenFehler("abgelehnt", tx(s, "f_unbekannt"))
    return aid


def _nr(request: Request, s: str) -> int:
    nr = str(request.path_params.get("nr", ""))
    if not _NR.fullmatch(nr) or not 0 < int(nr) <= NR_MAX:
        raise daten.DatenFehler("abgelehnt", tx(s, "f_unbekannt"))
    return int(nr)


def gliederung_html(k: Kontext, werk: dict) -> str:
    gl = werk.get("gliederung") if isinstance(werk.get("gliederung"), dict) else {}
    teile = []
    if _kurz(gl.get("titel")):
        teile.append(f"<h2>{e(_kurz(gl.get('titel')))}</h2>")
    hook = str(gl.get("hook") or "").strip()
    if hook:
        teile.append(f'<p class="lesetext">{e(hook)}</p>')
    for stufe in _liste(gl.get("stufen")):
        if not isinstance(stufe, dict):
            continue
        zeilen = []
        for sz in _liste(stufe.get("szenen")):
            kurz = sz if isinstance(sz, str) else (sz.get("kurz") or sz.get("titel") if isinstance(sz, dict) else "")
            quellen = [q for q in (_quelle_text(x) for x in _liste(sz.get("quellen") if isinstance(sz, dict) else []))
                       if q]
            beleg = f' <span class="meta">{e(k.t(TEXTE, "quellen"))}: {e(", ".join(quellen))}</span>' if quellen else ""
            zeilen.append(f"<li>{e(str(kurz or '').strip())}{beleg}</li>")
        teile.append(f"<h3>{e(str(stufe.get('name') or stufe.get('titel') or ''))}</h3><ul>{''.join(zeilen)}</ul>")
    fehlt = gl.get("fehlt")
    fehlt = [fehlt] if isinstance(fehlt, str) else _liste(fehlt)
    saetze = [str(f).strip() for f in fehlt if isinstance(f, (str, int, float)) and str(f).strip()]
    if saetze:
        teile.append(f'<p class="hinweis"><b>{e(k.t(TEXTE, "fehlt"))}</b></p><ul>'
                     + "".join(f"<li>{e(x)}</li>" for x in saetze) + "</ul>")
    quellen = [q for q in (_quelle_text(x) for x in _liste(werk.get("quellen"))) if q]
    if quellen:
        teile.append(f'<h3>{e(k.t(TEXTE, "quellen"))}</h3><ul>' + "".join(f"<li>{e(q)}</li>" for q in quellen) + "</ul>")
    return "".join(teile)


def _urteil_form(k: Kontext, aid: str, kennung: str, frage: str, vorbelegt: str, nr: int | None) -> str:
    satz, sperre = _gesperrt(k)
    ziel = f'<input type="hidden" name="kennung" value="{e(kennung)}">' + (
        f'<input type="hidden" name="nr" value="{nr}">' if nr is not None else "")
    return (f'<h3>{e(frage)}</h3>{satz}<form method="post" action="{SEITE}/{aid}/urteil" class="editor">{_csrf()}{ziel}'
            f'<label>{e(k.t(TEXTE, "wunsch", n=WUNSCH_MAX))} <textarea name="wunsch" maxlength="{WUNSCH_MAX}">'
            f'{e(vorbelegt)}</textarea></label><div class="zeile knoepfe">'
            f'<button type="submit" name="urteil" value="passt"{sperre}>{e(k.t(TEXTE, "passt"))}</button>'
            f'<button type="submit" name="urteil" value="nochmal" class="leise"{sperre}>{e(k.t(TEXTE, "nochmal"))}</button>'
            f'<button type="submit" formaction="{SEITE}/{aid}/wunsch" class="leise">{e(k.t(TEXTE, "speichern"))}'
            f'</button></div></form>')


def _einfach_form(k: Kontext, pfad: str, knopf: str, mit_wunsch: bool, nr: int | None = None) -> str:
    satz, sperre = _gesperrt(k)
    ziel = f'<input type="hidden" name="nr" value="{nr}">' if nr is not None else ""
    feld = (f'<label>{e(k.t(TEXTE, "anders"))} <textarea name="wunsch" maxlength="{WUNSCH_MAX}" required>'
            f'</textarea></label>' if mit_wunsch else "")
    return (f'{satz}<form method="post" action="{pfad}" class="editor">{_csrf()}{ziel}<fieldset{sperre} class="still">'
            f'{feld}<p><button type="submit">{e(k.t(TEXTE, knopf))}</button></p></fieldset></form>')


def _video_html(k: Kontext, werk: dict, zeile: dict | None) -> str:
    if werk.get("alle_fertig") is not True or not zeile:
        return ""
    teile = [f'<h2>{e(k.t(TEXTE, "video"))}</h2>']
    if werk.get("video_veraltet") is True:
        teile.append(f'<p class="hinweis">{e(k.t(TEXTE, "video_veraltet"))}</p>')
    teile.append(_clip(k, zeile.get("datei"), _kurz(zeile.get("video_titel")) or "Video", zeile.get("dauer_s")))
    marken = "\n".join(f'{_kurz(z.get("zeit"), 12) or "0:00"} {_kurz(z.get("name"), 120)}'.strip()
                       for z in _liste(zeile.get("zeitmarken")) if isinstance(z, dict))
    for schluessel, text in (("video_titel", str(zeile.get("video_titel") or "")),
                             ("beschreibung", str(zeile.get("beschreibung") or "")), ("zeitmarken", marken)):
        if text.strip():
            zeilen = min(12, max(2, text.count("\n") + 2))
            teile.append(f'<label class="kopierfeld">{e(k.t(TEXTE, schluessel))} '
                         f'<textarea readonly rows="{zeilen}">{e(text)}</textarea></label>')
    return "".join(teile)


async def werk_bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
    aid = _aid(k.request, k.sprache)
    firma = str(k.firma)
    d = await run_in_threadpool(daten.werk, firma, aid)
    werk = d.get("werk") if isinstance(d.get("werk"), dict) else {}
    zeile = next((w for w in await run_in_threadpool(daten.werke, firma) if str(w.get("id")) == aid), None)
    kennung = d.get("gliederung_kennung") if isinstance(d.get("gliederung_kennung"), str) else ""
    szenen = [s for s in _liste(werk.get("szenen")) if isinstance(s, dict)]
    fertig = sum(1 for s in szenen if s.get("stand") == "fertig")
    warten = sum(1 for s in szenen if s.get("stand") in WARTET)
    titel = (_kurz(zeile.get("thema")) or _kurz(zeile.get("titel")) if zeile else "") or k.t(TEXTE, "titel")
    teile = [_meldung(k), f'<p><a href="{SEITE}">{e(k.t(TEXTE, "alle"))}</a></p>',
             f'<p>{e(k.t(TEXTE, "zaehlung", n=len(szenen), fertig=fertig, warten=warten))}</p>',
             gliederung_html(k, werk)]
    if _KENNUNG.fullmatch(kennung) and werk.get("gliederung_stand") == "text_wartet":
        teile.append(_urteil_form(k, aid, kennung, k.t(TEXTE, "gliederung_frage"),
                                  str(werk.get("gliederung_wunsch") or ""), None))
    grund = _kurz(werk.get("gliederung_aufgegeben"))
    if grund and werk.get("gliederung_stand") == "geplant":
        teile.append(f'<p class="fehler">{e(grund)}</p>'
                     + _einfach_form(k, f"{SEITE}/{aid}/neu-schreiben", "neu_schreiben", True))
    if szenen:
        zeilen = []
        for s in szenen:
            nr = _ganz(s.get("nr"))
            if nr is None or not 0 < nr <= NR_MAX:
                continue
            aufgegeben = (f' <span class="chip schlecht">{e(k.t(TEXTE, "braucht_wunsch"))}</span>'
                          if s.get("stand") == "geplant" and _kurz(s.get("aufgegeben")) else "")
            zeilen.append(f'<li><a href="{e(seite_url(aid, nr))}"><b>{e(k.t(TEXTE, "szene", nr=nr))}</b> '
                          f'{e(_kurz(s.get("kurz"), 160))}</a> {_stand_chip(k, str(s.get("stand") or ""), bool(s.get("fehler")))}'
                          f'{aufgegeben}</li>')
        teile.append(f'<h2>{e(k.t(TEXTE, "szenen"))}</h2><ol class="szenen">{"".join(zeilen)}</ol>')
    elif not werk.get("gliederung"):
        teile.append(f'<p class="leer">{e(k.t(TEXTE, "noch_kein_werk"))}</p>')
    video_fehler = _kurz(werk.get("video_fehler"))
    if video_fehler and werk.get("alle_fertig") is True:
        teile.append(f'<p class="fehler">{e(video_fehler)}</p>'
                     + _einfach_form(k, f"{SEITE}/{aid}/neu-bauen", "neu_bauen", False))
    teile.append(_video_html(k, werk, zeile))
    return titel, f'<div class="werkbank">{"".join(teile)}</div>'


# --- Szenenseite ---------------------------------------------------------------------------------

def _schritte_html(k: Kontext, s: dict) -> str:
    bild = s.get("bild") if isinstance(s.get("bild"), dict) else {}
    teile = []
    if s.get("bildart") in BILDARTEN:
        teile.append(f'<p>{e(k.t(TEXTE, "b_" + s["bildart"]))}</p>')
    zeilen = []
    for i, sch in enumerate(_liste(bild.get("schritte")), 1):
        if not isinstance(sch, dict):
            continue
        vorlage = str(sch.get("vorlage") or "")
        name = k.t(TEXTE, "v_" + vorlage) if vorlage in VORLAGEN else (_kurz(vorlage, 40) or k.t(TEXTE, "bild_n", i=i))
        inhalt = []
        for schluessel, w in sch.items():
            if schluessel == "vorlage":
                continue
            if isinstance(w, (str, int, float)) and not isinstance(w, bool) and str(w).strip():
                inhalt.append(_kurz(str(w)))
            elif isinstance(w, list):
                inhalt += [_kurz(str(x)) for x in w if isinstance(x, (str, int, float)) and str(x).strip()]
        zeilen.append(f'<li>{e(k.t(TEXTE, "bild_n", i=i))}: <b>{e(name)}</b>'
                      + (f" – {e(' · '.join(inhalt))}" if inhalt else "") + "</li>")
    if zeilen:
        teile.append(f'<ol>{"".join(zeilen)}</ol>')
    if s.get("uebergang") in ("schnitt", "blende"):
        teile.append(f'<p class="meta">{e(k.t(TEXTE, "uebergang", art=k.t(TEXTE, "ue_" + s["uebergang"])))}</p>')
    return "".join(teile)


def _aussagen(belege) -> list[tuple[str, str]]:
    paare = []
    for b in _liste(belege):
        if isinstance(b, dict) and _kurz(b.get("aussage")):
            paare.append((_kurz(b.get("aussage")), _quelle_text(b.get("quelle"))))
    return paare


async def szene_bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
    aid, nr = _aid(k.request, k.sprache), _nr(k.request, k.sprache)
    d = await run_in_threadpool(daten.werk_szene, str(k.firma), aid, nr)
    s = d.get("szene") if isinstance(d.get("szene"), dict) else {}
    kennung = d.get("kennung") if isinstance(d.get("kennung"), str) else ""
    stand = str(s.get("stand") or "")
    titel = k.t(TEXTE, "szene", nr=nr) + (f": {_kurz(s.get('kurz'), 120)}" if _kurz(s.get("kurz")) else "")
    teile = [_meldung(k), f'<p><a href="{e(seite_url(aid))}">{e(k.t(TEXTE, "alle_szenen"))}</a></p>',
             f'<p>{_stand_chip(k, stand, bool(s.get("fehler")))}'
             + (f' <span class="meta">{e(_kurz(s.get("stufe"), 120))}</span>' if _kurz(s.get("stufe")) else "") + "</p>"]
    if s.get("fehler"):
        teile.append(f'<p class="fehler">{e(k.t(TEXTE, "erzeugen_fehler", text=_kurz(s.get("fehler"))))}</p>')
    if stand in MIT_CLIP:
        teile.append(_clip(k, s.get("clip_datei"), titel, s.get("dauer_s")))
    grund = _kurz(s.get("aufgegeben"))
    if grund and stand == "geplant":
        teile.append(f'<p class="fehler">{e(grund)}</p>'
                     + _einfach_form(k, f"{SEITE}/{aid}/neu-schreiben", "neu_schreiben", True, nr))
    sprech = str(s.get("sprechtext") or "").strip()
    teile.append(f'<h3>{e(k.t(TEXTE, "sprechtext"))}</h3>' + (
        f'<p class="lesetext">{e(sprech)}</p>' if sprech else f'<p class="meta">{e(k.t(TEXTE, "kein_text"))}</p>'))
    paare = _aussagen(s.get("belege"))
    if paare:
        teile.append(f'<h3>{e(k.t(TEXTE, "aussagen"))}</h3><ul>' + "".join(
            f'<li>{e(a)}' + (f' <span class="meta">{e(k.t(TEXTE, "quelle", q=q))}</span>' if q else "") + "</li>"
            for a, q in paare) + "</ul>")
    teile.append(f'<h3>{e(k.t(TEXTE, "bildidee"))}</h3>'
                 + (_schritte_html(k, s) or f'<p class="meta">{e(k.t(TEXTE, "keine_bildidee"))}</p>'))
    belege = [q for q in (_quelle_text(x) for x in _liste(s.get("quellen"))) if q]
    teile.append(f'<h3>{e(k.t(TEXTE, "belege"))}</h3>' + (
        "<ul>" + "".join(f"<li>{e(b)}</li>" for b in belege) + "</ul>" if belege
        else f'<p class="meta">{e(k.t(TEXTE, "keine_belege"))}</p>'))
    wuensche = [_kurz(w.get("text") if isinstance(w, dict) else w, WUNSCH_MAX) for w in _liste(s.get("wuensche"))]
    wuensche = [w for w in wuensche if w]
    if wuensche:
        teile.append(f'<h3>{e(k.t(TEXTE, "wuensche"))}</h3><ul>' + "".join(f"<li>{e(w)}</li>" for w in wuensche)
                     + "</ul>")
    if stand in WARTET and _KENNUNG.fullmatch(kennung):
        frage = k.t(TEXTE, "text_frage" if stand == "text_wartet" else "clip_frage")
        teile.append(_urteil_form(k, aid, kennung, frage, str(s.get("wunsch") or ""), nr))
    if s.get("fehler"):
        teile.append(_einfach_form(k, f"{SEITE}/{aid}/szene/{nr}/nochmal", "nochmal_versuchen", False))
    if stand == "fertig":
        teile.append(_einfach_form(k, f"{SEITE}/{aid}/szene/{nr}/oeffnen", "oeffnen", True))
    return titel, f'<div class="werkbank">{"".join(teile)}</div>'


# --- POST-Wege -----------------------------------------------------------------------------------

async def _anfang(request: Request, kostet: bool):
    """-> (fehler, s, form, firma, aid). Bei Arbeit ausloesenden Wegen zuerst der Kosten-Schalter (422)."""
    fehler, s, form, firma = await huelle.formular_mit_firma(request, FORM_GRENZE)
    if fehler is not None:
        return fehler, s, form, None, ""
    if kostet and not bestellen.an():
        return huelle.abgelehnt(s, tx(s, "aus")), s, form, None, ""
    aid = str(request.path_params.get("aid", ""))
    if not _AID.fullmatch(aid):
        return huelle.abgelehnt(s, tx(s, "f_unbekannt")), s, form, None, ""
    return None, s, form, str(firma), aid


def _wunsch(form: dict, s: str, pflicht: bool) -> str:
    wunsch = str(form.get("wunsch") or "").replace("\r\n", "\n").strip()
    if len(wunsch) > WUNSCH_MAX:
        raise ValueError(tx(s, "f_wunsch_lang", n=WUNSCH_MAX))
    if pflicht and not wunsch:
        raise ValueError(tx(s, "f_wunsch_leer"))
    return wunsch


def _nr_aus(roh, s: str) -> int | None:
    roh = str(roh or "")
    if not roh:
        return None
    if not _NR.fullmatch(roh) or not 0 < int(roh) <= NR_MAX:
        raise ValueError(tx(s, "f_unbekannt"))
    return int(roh)


def _schon(s: str, ziel: str, wunsch: str) -> Response:
    """Abgelehnt (schon entschieden, fremd oder gerade nicht moeglich): ohne Wunsch zurueck mit Hinweis,
    mit Wunsch eine Seite, auf der er zum Kopieren steht (nichts geht verloren)."""
    if not wunsch:
        return RedirectResponse(ziel + ("&" if "?" in ziel else "?") + "m=entschieden", status_code=303)
    titel = tx(s, "m_entschieden")
    kopf = f'<nav class="seite" aria-label="{e(huelle.t(s, "menue"))}">{huelle._marke(s)}</nav>'
    rumpf = (f'<h1>{e(titel)}</h1><p class="fehler">{e(tx(s, "f_schon"))}</p>'
             f'<textarea readonly rows="6">{e(wunsch)}</textarea>'
             f'<p><a href="{e(ziel)}">{e(tx(s, "zurueck_seite"))}</a></p>')
    return huelle._koepfe(HTMLResponse(huelle._dokument(s, titel, kopf, rumpf), status_code=409), s)


async def _senden(s: str, ziel: str, wunsch: str, erfolg: str, aufruf, *werte) -> Response:
    try:
        ok = await run_in_threadpool(aufruf, *werte)
    except daten.DatenFehler as f:
        if f.art != "abgelehnt":
            return huelle.daten_fehlerseite(s, f)
        return _schon(s, ziel, wunsch)
    if ok is False:
        return _schon(s, ziel, wunsch)
    return RedirectResponse(ziel + ("&" if "?" in ziel else "?") + f"m={erfolg}", status_code=303)


async def _kennung_weg(request: Request, kostet: bool) -> Response:
    fehler, s, form, firma, aid = await _anfang(request, kostet)
    if fehler is not None:
        return fehler
    try:
        kennung = str(form.get("kennung") or "")
        if not _KENNUNG.fullmatch(kennung):
            raise ValueError(tx(s, "f_unbekannt"))
        nr = _nr_aus(form.get("nr"), s)
        ziel = seite_url(aid, nr)
        if not kostet:
            wunsch = _wunsch(form, s, True)
            return await _senden(s, ziel, wunsch, "gespeichert", daten.werk_wunsch, firma, kennung, wunsch)
        urteil = str(form.get("urteil") or "")
        if urteil not in ("passt", "nochmal"):
            raise ValueError(tx(s, "f_unbekannt"))
        wunsch = _wunsch(form, s, False)
    except ValueError as f:
        return huelle.abgelehnt(s, str(f))
    return await _senden(s, ziel, wunsch, urteil, daten.werk_urteil, firma, kennung, urteil, wunsch)


async def urteil(request: Request) -> Response:
    return await _kennung_weg(request, kostet=True)


async def wunsch_speichern(request: Request) -> Response:
    """Wunsch zu einem wartenden Vorschlag speichern: loest nichts aus, geht darum immer."""
    return await _kennung_weg(request, kostet=False)


async def szene_nochmal(request: Request) -> Response:
    fehler, s, _form, firma, aid = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        nr = _nr_aus(request.path_params.get("nr"), s)
    except ValueError as f:
        return huelle.abgelehnt(s, str(f))
    return await _senden(s, seite_url(aid, nr), "", "versucht", daten.werk_szene_nochmal, firma, aid, nr)


async def szene_oeffnen(request: Request) -> Response:
    fehler, s, form, firma, aid = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        nr = _nr_aus(request.path_params.get("nr"), s)
        wunsch = _wunsch(form, s, True)
    except ValueError as f:
        return huelle.abgelehnt(s, str(f))
    return await _senden(s, seite_url(aid, nr), wunsch, "geoeffnet", daten.werk_szene_oeffnen, firma, aid, nr, wunsch)


async def neu_schreiben(request: Request) -> Response:
    """Neuer Wunsch an eine aufgegebene Szene (mit nr) oder die Gliederung (ohne nr)."""
    fehler, s, form, firma, aid = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        nr = _nr_aus(form.get("nr"), s)
        wunsch = _wunsch(form, s, True)
    except ValueError as f:
        return huelle.abgelehnt(s, str(f))
    return await _senden(s, seite_url(aid, nr), wunsch, "neu", daten.werk_neu_schreiben, firma, aid, nr, wunsch)


async def neu_bauen(request: Request) -> Response:
    fehler, s, _form, firma, aid = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    return await _senden(s, seite_url(aid), "", "neu_bauen", daten.werk_neu_bauen, firma, aid)
