"""Assistent zum Entwurf im Marketing-Reiter (Stufe B, Tag 6, 25.11.2026): Bitten an den Gestaltungs-Assistenten
stellen, den Verlauf lesen, eine laufende Runde stoppen und eine fertige Runde rueckgaengig machen.

- Derselbe Weg wie der Editor in Sales: eine Chat-Runde ueber chat.chat_anlegen (marketing.pult_chat_senden).
  Ausgefuehrt wird sie vom Chat-Arbeiter am PC ueber das Abo und die bestehende Modell-Tuer; hier entsteht
  kein eigener Modellaufruf und keine Budgetzeile. Laeuft der Arbeiter nicht, schliesst die Datenbank die
  Runde mit einem Hinweis ab.
- Jede Aenderung des Assistenten ist eine neue Fassung; Sperren (eine laufende Runde, Basis = neueste) setzt
  die Datenbank durch. Versendet wird hier nichts.
- Ohne Skript: der Verlauf aktualisiert sich beim Neuladen der Seite."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, huelle
from spaces.marketing.api.oberflaeche.huelle import Kontext, e

NACHRICHT_MAX = 2000           # wie chat.NACHRICHT_MAX
VERLAUF_MAX = 10
LAUFEND = ("offen", "in_arbeit")
STOPP_ARTEN = ("behalten", "verwerfen")

TEXTE = {
    "de": {
        "titel": "Assistent",
        "hinweis": "Bitte den Assistenten, den Entwurf zu ändern. Jede Änderung wird eine neue Fassung, die du "
                   "rückgängig machen kannst. Er arbeitet am PC über das Abo; versendet wird nichts.",
        "bitte": "Deine Bitte",
        "senden": "Bitte senden",
        "laeuft": "Der Assistent arbeitet gerade. Lade die Seite neu, um den Stand zu sehen.",
        "neu_laden": "Neu laden",
        "stopp_behalten": "Stoppen und Zwischenstand behalten",
        "stopp_verwerfen": "Stoppen und verwerfen",
        "rueckgaengig": "Diese Änderung rückgängig machen",
        "leer": "Noch keine Bitten an den Assistenten.",
        "nicht_lesbar": "Der Verlauf des Assistenten ist gerade nicht lesbar.",
        "status_offen": "wartet auf den Assistenten",
        "status_wartet": "in der Warteschlange",
        "status_in_arbeit": "in Arbeit",
        "status_fertig": "fertig",
        "status_fehler": "nicht geklappt",
        "status_anders": "Status: {status}",
        "fassung": "Fassung {vorher} → {nachher}",
        "f_leer": "Ohne Bitte kein Auftrag.",
        "f_zu_lang": "Die Bitte ist zu lang (höchstens {max} Zeichen).",
        "f_auftrag": "Unbekannte Runde. Bitte die Seite neu laden.",
        "f_art": "Unbekannte Wahl beim Stoppen.",
        "f_entwurf": "Den Assistenten kannst du nur bei einem Entwurf in Arbeit fragen.",
    },
    "en": {
        "titel": "Assistant",
        "hinweis": "Ask the assistant to change the draft. Every change becomes a new version you can undo. It "
                   "works on the PC through the subscription; nothing is sent.",
        "bitte": "Your request",
        "senden": "Send request",
        "laeuft": "The assistant is working. Reload the page to see the progress.",
        "neu_laden": "Reload",
        "stopp_behalten": "Stop and keep the interim state",
        "stopp_verwerfen": "Stop and discard",
        "rueckgaengig": "Undo this change",
        "leer": "No requests to the assistant yet.",
        "nicht_lesbar": "The assistant's history cannot be read right now.",
        "status_offen": "waiting for the assistant",
        "status_wartet": "queued",
        "status_in_arbeit": "in progress",
        "status_fertig": "done",
        "status_fehler": "did not work",
        "status_anders": "Status: {status}",
        "fassung": "Version {vorher} → {nachher}",
        "f_leer": "No request, no job.",
        "f_zu_lang": "The request is too long (at most {max} characters).",
        "f_auftrag": "Unknown round. Please reload the page.",
        "f_art": "Unknown choice when stopping.",
        "f_entwurf": "You can only ask the assistant about a draft in progress.",
    },
}


def tx(s: str, schluessel: str, **werte) -> str:
    return huelle._sprache.text(TEXTE, s, schluessel, **werte)


def _zahl(w) -> int | None:
    return w if isinstance(w, int) and not isinstance(w, bool) else None


def _status(k: Kontext, z: dict) -> str:
    st = str(z.get("status") or "")
    return k.t(TEXTE, f"status_{st}") if st in ("offen", "wartet", "in_arbeit", "fertig", "fehler") else (
        k.t(TEXTE, "status_anders", status=st))


def _csrf() -> str:
    return f'<input type="hidden" name="csrf" value="{e(huelle.csrf_marke())}">'


def eintrag(k: Kontext, z: dict, hier: str, neueste: int | None) -> str:
    teile = [_status(k, z), k.datum(z.get("erstellt_am"))]
    vorher, nachher = _zahl(z.get("fassung_vorher")), _zahl(z.get("fassung_nachher"))
    if vorher is not None and nachher is not None:
        teile.append(k.t(TEXTE, "fassung", vorher=vorher, nachher=nachher))
    antwort = str(z.get("antwort") or "")
    hinweise = [h for h in (z.get("hinweise") or []) if isinstance(h, str)] if isinstance(z.get("hinweise"), list) else []
    rumpf = (f'<p class="frage">{e(str(z.get("nachricht") or ""))}</p>'
             f'<p class="meta">{e(" · ".join(t for t in teile if t))}</p>'
             + (f'<pre>{e(antwort)}</pre>' if antwort else "")
             + ("<ul>" + "".join(f"<li>{e(h)}</li>" for h in hinweise) + "</ul>" if hinweise else ""))
    if z.get("status") == "fertig" and nachher is not None and nachher == neueste and z.get("id"):
        rumpf += (f'<form method="post" action="{e(hier)}/assistent/rueckgaengig" class="anlegen">{_csrf()}'
                  f'<input type="hidden" name="auftrag" value="{e(str(z["id"]))}">'
                  f'<button type="submit" class="leise">{e(k.t(TEXTE, "rueckgaengig"))}</button></form>')
    return f"<li>{rumpf}</li>"


async def abschnitt(k: Kontext, iid: str, hier: str) -> str:
    """Abschnitt im Editor: Verlauf, Stopp, Bitte. Fehler beim Lesen kosten nie die Seite."""
    kopf = f'<section class="assistent"><h2>{e(k.t(TEXTE, "titel"))}</h2><p class="meta">{e(k.t(TEXTE, "hinweis"))}</p>'
    try:
        stand = await run_in_threadpool(daten.chat_stand, iid)
    except daten.DatenFehler:
        return kopf + f'<p class="meta">{e(k.t(TEXTE, "nicht_lesbar"))}</p></section>'
    verlauf = [z for z in stand.get("verlauf") or [] if isinstance(z, dict) and z.get("art", "chat") == "chat"]
    neueste = _zahl(stand.get("neueste_fassung"))
    laufend = [z for z in verlauf if z.get("status") in LAUFEND]
    teile = [kopf]
    if stand.get("laeuft"):
        teile.append(f'<p class="hinweis">{e(k.t(TEXTE, "laeuft"))} <a href="{e(hier)}">'
                     f'{e(k.t(TEXTE, "neu_laden"))}</a></p>')
        if laufend and laufend[-1].get("id"):
            teile.append(f'<form method="post" action="{e(hier)}/assistent/stopp" class="anlegen">{_csrf()}'
                         f'<input type="hidden" name="auftrag" value="{e(str(laufend[-1]["id"]))}">'
                         f'<button type="submit" name="art" value="behalten" class="leise">'
                         f'{e(k.t(TEXTE, "stopp_behalten"))}</button>'
                         f'<button type="submit" name="art" value="verwerfen" class="leise">'
                         f'{e(k.t(TEXTE, "stopp_verwerfen"))}</button></form>')
    letzte = verlauf[-VERLAUF_MAX:]
    teile.append('<ol class="verlauf">' + "".join(eintrag(k, z, hier, neueste) for z in letzte) + "</ol>" if letzte
                 else f'<p class="meta">{e(k.t(TEXTE, "leer"))}</p>')
    teile.append(f'<form method="post" action="{e(hier)}/assistent" class="editor">{_csrf()}'
                 f'<label>{e(k.t(TEXTE, "bitte"))} <textarea name="nachricht" rows="3" maxlength="{NACHRICHT_MAX}">'
                 f'</textarea></label><p><button type="submit">{e(k.t(TEXTE, "senden"))}</button></p></form>')
    return "".join(teile) + "</section>"


# --- Formulare ----------------------------------------------------------------------------------

async def _vorbereiten(request: Request):
    from spaces.marketing.api.oberflaeche import entwuerfe   # im Rumpf: entwuerfe bindet diesen Abschnitt ein
    fehler, s, form, iid, _, kopf, _ = await entwuerfe._vorbereiten(request)
    if fehler is None and kopf.get("status") != "entwurf":
        fehler = huelle.abgelehnt(s, tx(s, "f_entwurf"))
    return fehler, s, form, iid, f"{entwuerfe.SEITE}/{iid}"


def _auftrag(form: dict) -> str | None:
    import uuid
    try:
        return str(uuid.UUID(str(form.get("auftrag") or "")))
    except ValueError:
        return None


async def senden(request: Request) -> Response:
    fehler, s, form, iid, hier = await _vorbereiten(request)
    if fehler is not None:
        return fehler
    nachricht = str(form.get("nachricht") or "").replace("\r\n", "\n").strip()
    if not nachricht:
        return huelle.abgelehnt(s, tx(s, "f_leer"))
    if len(nachricht) > NACHRICHT_MAX:
        return huelle.abgelehnt(s, tx(s, "f_zu_lang", max=NACHRICHT_MAX))
    try:
        await run_in_threadpool(daten.chat_senden, iid, nachricht)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{hier}?gefragt=1", status_code=303)


async def stoppen(request: Request) -> Response:
    fehler, s, form, iid, hier = await _vorbereiten(request)
    if fehler is not None:
        return fehler
    art, auftrag = str(form.get("art") or ""), _auftrag(form)
    if art not in STOPP_ARTEN:
        return huelle.abgelehnt(s, tx(s, "f_art"))
    if auftrag is None:
        return huelle.abgelehnt(s, tx(s, "f_auftrag"))
    try:
        await run_in_threadpool(daten.chat_stoppen, iid, art, auftrag)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{hier}?gestoppt=1", status_code=303)


async def rueckgaengig(request: Request) -> Response:
    fehler, s, form, iid, hier = await _vorbereiten(request)
    if fehler is not None:
        return fehler
    auftrag = _auftrag(form)
    if auftrag is None:
        return huelle.abgelehnt(s, tx(s, "f_auftrag"))
    try:
        n = await run_in_threadpool(daten.chat_rueckgaengig, iid, auftrag)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{hier}?gespeichert={n}", status_code=303)
