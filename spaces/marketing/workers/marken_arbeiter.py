"""Marken-Arbeiter am PC (sales-claw Spec 2026-10-07-marke-per-chat-design.md §3, §4.3, §4.5).
Laeuft im Prozess des Chat-Arbeiters (Ruling R1) und holt Auftraege von /api/marke/arbeiter:
- chat: Uploads, Webseite (erste http(s)-Adresse der Nachricht) und aktuelles Profil -> Claude ueber
  den Shim :8117 (ohne Werkzeuge) -> geprueften Vorschlag ablegen. Schreibt nie in Rowboat.
- uebernehmen: Logo holen -> companys/<Firma>/Marke.md (+ Logo) schreiben -> Spiegel -> fertig
  (die DB markiert dabei die offenen Entwuerfe). Scheitert etwas vor dem Schreiben, wird nichts
  geschrieben und der Auftrag mit Grund zurueckgegeben.
- Abgleich (Start + alle 10 min): Kopfteil jeder Marke.md gegen den Spiegel; gueltige Werte, die
  abweichen, werden gespiegelt, ungueltige nie (Rowboat bleibt Wahrheit). Die Lese-Hinweise
  ("Marke.md: akzent ungültig") gehen an die VM, damit die Profilseite sie zeigt.
- Ein Chat-Auftrag bringt den offenen Vorschlag der Firma mit: Claude verfeinert ihn, sein Logo bleibt
  gueltig (C1/R14). Laeuft in einem eigenen Faden des Chat-Arbeiters (R15)."""
from __future__ import annotations

import datetime
import json
import re
import time

from spaces.marketing.claw import markenprofil, marken_prompt, markenwissen, webseite
from spaces.marketing.workers import chat_worker as cw
from spaces.marketing.workers.bild_worker import ApiFehler

ABGLEICH_S = 600
FEHLER_TAKT_S = 60         # nach einem gescheiterten Abgleich (VM weg) nicht 10 Minuten warten
MAX_HINWEISE = 50          # Grenze der VM (api/marke._hinweise)
MAX_HINWEIS = 300
UNBEKANNT = "Unbekannte Auftragsart"
VON_VORGABE = "Marken-Chat"
UEBERNOMMEN = "Die Marke ist übernommen."
_ADRESSE = re.compile(r"\bhttps?://[^\s<>\"'`]+", re.IGNORECASE)
_SATZZEICHEN = ".,;:!?)]}–—»“”'\""
_PNG = b"\x89PNG\r\n\x1a\n"


class MarkenApi(cw.ChatApi):
    PFAD = "/api/marke/arbeiter"

    def vorschlag(self, aid, daten: dict) -> dict:
        return self._post(f"/{aid}/vorschlag", daten)

    def logo(self, aid, roh: bytes, typ: str) -> str:
        return self._post(f"/{aid}/logo", roh=roh, typ=typ)["name"]

    def spiegeln(self, mandant: str, gestalt: dict, stand: str) -> dict:
        return self._post("/spiegeln", {"mandant": mandant, "gestalt": gestalt, "stand": stand})

    def firmen(self) -> list[dict]:
        return json.loads(self._anfrage("GET", "/firmen") or b"{}").get("firmen") or []

    def hinweise(self, mandant: str, hinweise: list[str]) -> dict:
        return self._post("/hinweise", {"mandant": mandant, "hinweise": hinweise})


class _Aufgeben(Exception):
    """Auftrag mit dieser Meldung zurueckgeben."""


def adresse(nachricht) -> str | None:
    """Erste http(s)-Adresse der Nachricht (ohne Satzzeichen am Ende) oder None."""
    m = _ADRESSE.search(str(nachricht or ""))
    return m.group(0).rstrip(_SATZZEICHEN) if m else None


def _hinweise(hinweise: list[str]) -> list[str]:
    return [str(h)[:MAX_HINWEIS] for h in dict.fromkeys(hinweise)][:MAX_HINWEISE]


def _firma(auftrag: dict) -> tuple[str, str]:
    mandant = str(auftrag.get("mandant") or "")
    return mandant, str(auftrag.get("firma") or auftrag.get("mandant_name") or mandant)


# --- Claude --------------------------------------------------------------------------

def _text_holen(api, aid, fragen_strom, nachrichten: list, zustand: dict, uhr, schlafen, halten_takt_s) -> str:
    """Ganzer Antworttext eines Stroms. Shim weg: Wiederholung bis SHIM_BIS_S, dann _Aufgeben; lehnt der
    Shim die Bilder ab, einmal sofort ohne sie. Verlorene Vergabe => cw._Verloren."""
    beginn = None
    with cw.halten(api, aid, halten_takt_s) as halter:
        while True:
            try:
                strom = iter(fragen_strom(marken_prompt.SYSTEM, nachrichten))
                try:
                    teile = []
                    for stueck in strom:
                        if halter.verloren.is_set():
                            raise cw._Verloren
                        teile.append(stueck)
                finally:
                    schliessen = getattr(strom, "close", None)
                    if schliessen:
                        schliessen()
                text = "".join(teile)
                if not text.strip():
                    raise cw.LlmFehler("Leere Antwort")
                break
            except cw.LlmFehler as e:
                if halter.verloren.is_set():
                    raise cw._Verloren from None
                if isinstance(e, cw.ShimAbgelehnt) and zustand["mit_bildern"]:
                    zustand["mit_bildern"] = False
                    nachrichten[0] = {"role": "user", "content": zustand["text_nutzer"]}
                    zustand["hinweise"].insert(0, cw.BILDER_ABGELEHNT)
                    continue
                if beginn is None:
                    beginn = uhr()
                if uhr() - beginn >= cw.SHIM_BIS_S:
                    raise _Aufgeben(cw.NICHT_ERREICHBAR) from None
                if not api.weiter(aid):
                    raise cw._Verloren from None
                schlafen(cw.SHIM_PAUSE_S)
    if halter.verloren.is_set():
        raise cw._Verloren
    return text


def _web_logo(api, aid, url: str, logo_laden, hinweise: list[str]) -> str | None:
    """Logo-Kandidat der Webseite laden (gleiche Sperren wie der Leser) und in den Medien der Firma
    ablegen -> der Name, den die VM vergibt (Ruling R8). Scheitert es, Hinweis und kein Logo."""
    geladen = logo_laden(url)
    if not geladen:
        hinweise.append(f"Logo von der Webseite nicht ladbar ({url[:120]}) – ohne Logo vorgeschlagen.")
        return None
    roh, typ = geladen
    try:
        return api.logo(aid, roh, typ)
    except ApiFehler as e:
        if e.code in cw.FREMD and "in Arbeit" in e.grund:
            raise
        hinweise.append(f"Logo von der Webseite abgelehnt: {e.grund[:150]}")
        return None


def chat_bearbeiten(api, auftrag: dict, fragen_strom, webseite_lesen, logo_laden, wurzel: str,
                    uhr, schlafen, halten_takt_s) -> str:
    aid = str(auftrag["id"])
    mandant, name = _firma(auftrag)
    bilder: list[tuple[str, str]] = []
    with cw.halten(api, aid, halten_takt_s) as halter:          # Anhaenge und Webseite dauern
        bildteile, unterlagen_text, _, hinweise = cw.anhaenge_vorbereiten(api, aid, auftrag, bilder)
        url = adresse(auftrag.get("nachricht"))
        fund = webseite_lesen(url) if url else None
        if fund is not None:
            hinweise += fund.hinweise
        profil = markenprofil.lesen(wurzel, mandant, name)
        hinweise += profil.hinweise
    if halter.verloren.is_set():
        return "fehler"
    text_nutzer = marken_prompt.nutzer_text(auftrag, profil, fund, unterlagen_text, bilder, hinweise)
    nachrichten = [{"role": "user", "content": [{"type": "text", "text": text_nutzer}, *bildteile]
                    if bildteile else text_nutzer}]
    zustand = {"mit_bildern": bool(bildteile), "text_nutzer": text_nutzer, "hinweise": hinweise}
    anhaenge = [n for n, _ in bilder]
    logos = list(fund.logos) if fund is not None else []
    bisher = marken_prompt.bisheriges_logo(auftrag)      # Logo des offenen Vorschlags (C1/R14)
    for versuch in (1, 2):
        text = _text_holen(api, aid, fragen_strom, nachrichten, zustand, uhr, schlafen, halten_takt_s)
        try:
            erg = marken_prompt.antwort_lesen(text, anhaenge, len(logos), bisher)
            break
        except marken_prompt.AntwortFehler as e:
            if versuch == 2:
                raise _Aufgeben(cw.NICHT_UMGESETZT + str(e)) from None
            nachrichten += [{"role": "assistant", "content": text},
                            {"role": "user", "content": marken_prompt.korrektur_text(str(e))}]
    if not api.weiter(aid):
        return "fehler"
    vorschlag = erg["vorschlag"]
    if vorschlag is None:
        api.fertig(aid, {"antwort": erg["antwort"], "hinweise": _hinweise(hinweise)})
        return "fertig"
    logo = vorschlag.get("logo")
    if isinstance(logo, str) and logo.startswith("web:"):
        vorschlag["logo"] = _web_logo(api, aid, logos[int(logo[4:]) - 1], logo_laden, hinweise)
    elif logo is None and bisher:
        vorschlag["logo"] = bisher        # null = "Logo bleibt" - beim offenen Vorschlag also dessen Logo
    api.vorschlag(aid, {"vorschlag": vorschlag, "antwort": erg["antwort"], "hinweise": _hinweise(hinweise)})
    return "fertig"


# --- Spiegel -------------------------------------------------------------------------

def _logo_bytes(profil) -> bytes | None:
    if not profil.logo_pfad:
        return None
    try:
        with open(profil.logo_pfad, "rb") as f:
            roh = f.read(markenprofil.LOGO_MAX_BYTES + 1)
    except OSError:
        return None
    return roh if len(roh) <= markenprofil.LOGO_MAX_BYTES else None


def spiegel_gestalt(profil) -> dict:
    """Spiegel-Gestalt aus den gueltigen Werten des Profils (ungueltige fehlen schon in profil.werte)."""
    return markenprofil.gestalt(profil.werte, _logo_bytes(profil), None)


def _spiegeln(api, mandant: str, gestalt: dict, stand: str) -> str | None:
    """None bei Erfolg, sonst der Grund (R5: ok:false mit HTTP 200 ist ein Fehlschlag)."""
    r = api.spiegeln(mandant, gestalt, stand)
    if isinstance(r, dict) and r.get("ok"):
        return None
    grund = r.get("fehler") if isinstance(r, dict) else None
    return f"Spiegel nicht aktualisiert: {grund or 'Gestalt ungültig'}"


def _hinweise_melden(api, mandant: str, name: str, profil, firma: dict) -> str | None:
    """Lese-Hinweise der Marke.md an die VM, wenn sie sich geaendert haben (I3) -> Meldung bei Fehlschlag."""
    neu = _hinweise(profil.hinweise)
    bisher = firma.get("hinweise") if isinstance(firma.get("hinweise"), list) else []
    if neu == bisher:
        return None
    try:
        api.hinweise(mandant, neu)
    except (ApiFehler, OSError, ValueError) as e:
        return f"{name}: Hinweise nicht gemeldet: {cw._kurz(e)}"
    return None


def abgleichen(api, wurzel: str, mandanten: list[dict], abgelehnt: dict | None = None) -> list[str]:
    """Je Firma: Lese-Hinweise der Marke.md melden, gueltige Kopfteil-Werte mit dem Spiegel vergleichen und
    bei Abweichung spiegeln. Ohne Ordner, ohne Kopfteil oder ganz ohne gueltige Werte wird nicht gespiegelt.
    abgelehnt (mandant -> Gestalt) merkt sich, was die VM abgelehnt hat: dieselbe Gestalt wird nicht alle
    10 Minuten erneut geschickt, solange der Spiegel den Fehler noch traegt (T5-b). -> Meldungen."""
    meldungen = []
    abgelehnt = abgelehnt if abgelehnt is not None else {}
    for firma in mandanten:
        if not isinstance(firma, dict) or not firma.get("id"):
            continue
        mandant, name = str(firma["id"]), str(firma.get("name") or firma["id"])
        profil = markenprofil.lesen(wurzel, mandant, name)
        meldung = _hinweise_melden(api, mandant, name, profil, firma)
        if meldung:
            meldungen.append(meldung)
        gestalt = spiegel_gestalt(profil)
        if not gestalt:
            continue
        spiegel = firma.get("gestalt") if isinstance(firma.get("gestalt"), dict) else {}
        if all(spiegel.get(k) == v for k, v in gestalt.items()):
            abgelehnt.pop(mandant, None)
            continue
        if firma.get("fehler") and abgelehnt.get(mandant) == gestalt:
            continue
        abgelehnt.pop(mandant, None)
        try:
            fehler = _spiegeln(api, mandant, gestalt, profil.werte.get("stand", ""))
            if fehler:
                abgelehnt[mandant] = gestalt
        except (ApiFehler, OSError, ValueError) as e:
            fehler = f"Spiegel nicht erreichbar: {cw._kurz(e)}"
        meldungen.append(f"{name}: {fehler or 'gespiegelt'}")
    return meldungen


class Abgleich:
    """Ruft abgleichen beim ersten Schritt und danach hoechstens alle ABGLEICH_S Sekunden; scheitert ein
    Lauf (VM nicht erreichbar), schon nach FEHLER_TAKT_S erneut (T5-a)."""

    def __init__(self, uhr=time.monotonic, abgleichen=abgleichen, wurzel: str | None = None,
                 takt_s: float = ABGLEICH_S):
        self.uhr, self._abgleichen, self.wurzel, self.takt_s = uhr, abgleichen, wurzel, takt_s
        self.naechster: float | None = None
        self.abgelehnt: dict = {}

    def schritt(self, api) -> list[str]:
        jetzt = self.uhr()
        if self.naechster is not None and jetzt < self.naechster:
            return []
        try:
            meldungen = self._abgleichen(api, self.wurzel or markenwissen.wurzel(), api.firmen(),
                                         abgelehnt=self.abgelehnt)
        except Exception as e:  # noqa: BLE001 - der Abgleich darf die Schleife nie stoeren
            self.naechster = jetzt + min(FEHLER_TAKT_S, self.takt_s)
            return [f"Abgleich gescheitert: {cw._kurz(e)}"]
        self.naechster = jetzt + self.takt_s
        return meldungen


# --- Uebernehmen ---------------------------------------------------------------------

def _logo_holen(api, aid, verweis) -> tuple[bytes, str] | None:
    if not verweis:
        return None
    if not isinstance(verweis, str):
        raise _Aufgeben("Übernehmen nicht möglich: Logo-Verweis ungültig")
    name = verweis[len("anhang:"):] if verweis.startswith("anhang:") else verweis
    roh = api.medium(aid, name)
    if not roh:
        raise _Aufgeben(f"Übernehmen nicht möglich: Logo {name} nicht gefunden")
    return roh, "image/png" if roh.startswith(_PNG) else "image/jpeg"


def uebernehmen(api, auftrag: dict, wurzel: str, jetzt, halten_takt_s) -> str:
    aid = str(auftrag["id"])
    mandant, name = _firma(auftrag)
    v = (auftrag.get("vorschlag") or {}).get("vorschlag") if isinstance(auftrag.get("vorschlag"), dict) else None
    if not isinstance(v, dict):
        raise _Aufgeben("Übernehmen nicht möglich: Vorschlag fehlt")
    with cw.halten(api, aid, halten_takt_s) as halter:
        try:
            neue_werte = marken_prompt.werte_pruefen(v)
            abschnitte = marken_prompt.abschnitte_pruefen(v.get("abschnitte"))
        except marken_prompt.AntwortFehler as e:
            raise _Aufgeben(f"Übernehmen nicht möglich: {e}") from None
        logo = _logo_holen(api, aid, v.get("logo"))       # vor dem ersten Schreiben (nichts halb)
        if halter.verloren.is_set():
            raise cw._Verloren
        alt = markenprofil.lesen(wurzel, mandant, name)
        # Bisheriges bleibt, was der Vorschlag nicht nennt - auch das Logo, wenn kein neues kommt.
        werte = {k: w for k, w in alt.werte.items() if k != "stand"}
        werte.update(neue_werte)
        try:
            markenprofil.schreiben(wurzel, mandant, name, werte, {**alt.abschnitte, **abschnitte}, logo,
                                   str(auftrag.get("von") or VON_VORGABE), jetzt())
        except markenprofil.MarkenFehler as e:
            raise _Aufgeben(f"Übernehmen nicht möglich: {e}") from None
    # Ab hier ist Rowboat geschrieben: ein Spiegel-Fehler wird Hinweis, der Abgleich holt nach.
    neu = markenprofil.lesen(wurzel, mandant, name)
    hinweise = list(neu.hinweise)
    try:
        fehler = _spiegeln(api, mandant, spiegel_gestalt(neu), neu.werte.get("stand", ""))
    except ApiFehler as e:
        if e.code in cw.FREMD and "in Arbeit" in e.grund:
            raise
        fehler = f"Spiegel nicht aktualisiert: {e.grund[:150]}"
    except (OSError, ValueError) as e:
        fehler = f"Spiegel nicht erreichbar: {cw._kurz(e)}"
    if fehler:
        hinweise.append(fehler + " – der Abgleich holt es nach.")
    try:                    # Profilseite: Lese-Hinweise der neuen Marke.md (sonst erst beim naechsten Abgleich)
        api.hinweise(mandant, _hinweise(neu.hinweise))
    except (ApiFehler, OSError, ValueError):
        pass                # nicht auftragsgebunden; der Abgleich meldet sie spaetestens in 10 Minuten
    api.fertig(aid, {"antwort": UEBERNOMMEN, "hinweise": _hinweise(hinweise)})
    return "fertig"


# --- Durchlauf -----------------------------------------------------------------------

def ein_durchlauf(api, fragen_strom=cw.frage_strom, webseite_lesen=webseite.lesen,
                  logo_laden=webseite.logo_laden, wurzel: str | None = None, jetzt=datetime.datetime.now,
                  uhr=time.monotonic, schlafen=time.sleep, halten_takt_s: float = cw.HALTEN_TAKT_S) -> str:
    auftrag = api.naechster()
    if not auftrag:
        return "leer"
    aid = str(auftrag["id"])
    wurzel = wurzel or markenwissen.wurzel()
    try:
        if auftrag.get("art") == "chat":
            return chat_bearbeiten(api, auftrag, fragen_strom, webseite_lesen, logo_laden, wurzel,
                                   uhr, schlafen, halten_takt_s)
        if auftrag.get("art") == "uebernehmen":
            return uebernehmen(api, auftrag, wurzel, jetzt, halten_takt_s)
        raise _Aufgeben(UNBEKANNT)
    except _Aufgeben as e:
        try:
            api.zurueck(aid, str(e))
        except (ApiFehler, OSError, ValueError):
            pass             # die VM gibt den Auftrag nach Ablauf der Vergabe selbst frei
        return "fehler"
    except cw._Verloren:
        return "fehler"
    except (ApiFehler, OSError, ValueError) as e:
        cw._freigeben(api, aid, cw.NICHT_ERREICHBAR, e)
        return "fehler"
