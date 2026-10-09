"""Marken-Arbeiter am PC (sales-claw Spec 2026-10-07-marke-per-chat-design.md §3, §4.3, §4.5).
Laeuft im Prozess des Chat-Arbeiters (Ruling R1) und holt Auftraege von /api/marke/arbeiter:
- chat: Uploads, Webseite (erste http(s)-Adresse der Nachricht) und aktuelles Profil -> Claude ueber
  den Shim :8117 (ohne Werkzeuge) -> geprueften Vorschlag ablegen. Schreibt nie in Rowboat.
- uebernehmen: Logo holen -> companys/<Firma>/Marke.md (+ Logo) schreiben -> Spiegel -> fertig
  (die DB markiert dabei die offenen Entwuerfe). Scheitert etwas vor dem Schreiben, wird nichts
  geschrieben und der Auftrag mit Grund zurueckgegeben.
- wissen: Rowboat-Lauf nach der Übernahme (workers/wissen_arbeiter). Laeuft in einem EIGENEN Faden
  (Ruling R7: naechster mit arten=wissen); der Marken-Faden holt ohne arten und bekommt nie wissen.
- Abgleich (Start + alle 10 min): Kopfteil jeder Marke.md gegen den Spiegel; gueltige Werte, die
  abweichen, werden gespiegelt, ungueltige nie (Rowboat bleibt Wahrheit). Die Lese-Hinweise
  ("Marke.md: akzent ungültig") gehen an die VM, damit die Profilseite sie zeigt, und das echte Profil
  (Werte ohne Logos + Abschnitte) fuer die Vorbefuellung von "Profil bearbeiten" (Ruling R8).
- Ein Chat-Auftrag bringt den offenen Vorschlag der Firma mit: Claude verfeinert ihn, sein Logo bleibt
  gueltig (C1/R14). Laeuft in einem eigenen Faden des Chat-Arbeiters (R15)."""
from __future__ import annotations

import datetime
import json
import re
import time
import urllib.parse

from spaces.marketing.claw import (bild_comfy, denkspur, logo_bearbeiten, markenprofil, marken_prompt,
                                   markenwissen, pdf_bilder, webseite, webseite_speicher)
from spaces.marketing.workers import chat_worker as cw
from spaces.marketing.workers.bild_worker import ApiFehler

ABGLEICH_S = 600
PROFIL_WERTE = ("akzent", "zweitfarbe", "grund", "text", "schrift_anzeige", "schrift_text", "webseite")
PROFIL_MAX = 60 * 1024     # wie api/marke.PROFIL_MAX (die DB nimmt hoechstens 64 KB)
PROFIL_ZU_GROSS = "Profil zu groß für die Vorbefüllung des Formulars – nicht gemeldet"
FEHLER_TAKT_S = 60         # nach einem gescheiterten Abgleich (VM weg) nicht 10 Minuten warten
MAX_HINWEISE = 50          # Grenze der VM (api/marke._hinweise)
MAX_HINWEIS = 300
UNBEKANNT = "Unbekannte Auftragsart"
VON_VORGABE = "Marken-Chat"
UEBERNOMMEN = "Die Marke ist übernommen."
SPIEGEL_FEHLER = "Spiegel nicht aktualisiert: "
_ADRESSE = re.compile(r"\bhttps?://[^\s<>\"'`]+", re.IGNORECASE)
_SATZZEICHEN = ".,;:!?)]}–—»“”'\""
_PNG = b"\x89PNG\r\n\x1a\n"


class MarkenApi(cw.ChatApi):
    PFAD = "/api/marke/arbeiter"

    def naechster(self, arten: tuple[str, ...] | None = None) -> dict | None:
        pfad = "/naechster" + (f"?arten={urllib.parse.quote(','.join(arten))}" if arten else "")
        return self._post(pfad).get("auftrag")

    def profil(self, mandant: str, profil: dict) -> dict:
        return self._post("/profil", {"mandant": mandant, "profil": profil})

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


def _spur_ende(spur) -> None:
    """Letzter Stand vor Abschlussmeldung/Freigabe; darf nie selbst werfen."""
    try:
        spur.ende()
    except Exception:  # noqa: BLE001 - Sichtbarkeit darf den Auftrag nie kippen
        pass


class _Aufgeben(Exception):
    """Auftrag mit dieser Meldung zurueckgeben."""


def adresse(nachricht) -> str | None:
    """Erste http(s)-Adresse der Nachricht (ohne Satzzeichen am Ende) oder None."""
    m = _ADRESSE.search(str(nachricht or ""))
    if not m:
        return None
    url = m.group(0).rstrip(_SATZZEICHEN)
    try:
        t = urllib.parse.urlsplit(url)
        if "@" in t.netloc:           # nie Zugangsdaten weitertragen (Cache, Hinweise, Schritte)
            url = urllib.parse.urlunsplit((t.scheme, t.netloc.rsplit("@", 1)[1], t.path, t.query, t.fragment))
    except ValueError:
        return None
    return url


def _hinweise(hinweise: list[str]) -> list[str]:
    return [str(h)[:MAX_HINWEIS] for h in dict.fromkeys(hinweise)][:MAX_HINWEISE]


def _firma(auftrag: dict) -> tuple[str, str]:
    mandant = str(auftrag.get("mandant") or "")
    return mandant, str(auftrag.get("firma") or auftrag.get("mandant_name") or mandant)


# --- Claude --------------------------------------------------------------------------

def _text_holen(api, aid, fragen_strom, nachrichten: list, zustand: dict, uhr, schlafen, halten_takt_s,
                spur, *, system=marken_prompt.SYSTEM, websuche=False) -> str:
    """Ganzer Antworttext eines Stroms. Shim weg: Wiederholung bis SHIM_BIS_S, dann _Aufgeben; lehnt der
    Shim die Bilder ab, einmal sofort ohne sie. Verlorene Vergabe => cw._Verloren."""
    extra: dict = {}
    if websuche:
        gemeldet: set[str] = set()

        def werkzeug(w: str) -> None:
            satz = "Websuche nicht verfügbar" if w == cw.WEBSUCHE_AUS else "Websuche genutzt"
            if satz not in gemeldet:
                gemeldet.add(satz)
                spur.schritt(satz)
        extra = {"websuche": True, "werkzeug": werkzeug}
    beginn = None
    with cw.halten(api, aid, halten_takt_s) as halter:
        while True:
            try:
                spur.schritt("Frage an Claude")
                strom = iter(fragen_strom(system, nachrichten, denken=spur.denken, **extra))
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


LOGO_NICHT = "Logo nicht bearbeitet: "
LOGO_BLEIBT = "Logo bleibt bei der Formular-Bearbeitung unverändert"
LOGO_WEB_ANSICHTEN = 3
LOGO_KI_ZEIT_S = 120       # kuerzer als die Vergabe (5 min): ein traeges ComfyUI heisst "Logo unbearbeitet"


def _einmal(logo_laden):
    """Jeder Logo-Kandidat wird je Runde hoechstens einmal geladen (Ansicht, Bearbeitung, Ablage)."""
    geladen: dict = {}

    def laden(url):
        if url not in geladen:
            geladen[url] = logo_laden(url)
        return geladen[url]
    return laden


def _bisheriges_logo(api, aid, bisher: str | None, profil) -> tuple[bytes | None, str | None]:
    """(Bytes, Medienname) des bisherigen Logos: das Logo des offenen Vorschlags, sonst die Rowboat-Datei."""
    if bisher:
        name = bisher[len("anhang:"):] if bisher.startswith("anhang:") else bisher
        try:
            roh = api.medium(aid, name)
        except (ApiFehler, OSError, ValueError):
            roh = None
        if roh:
            return roh, name
    return _datei_bytes(profil.logo_pfad), None


LOGO_WEGGELASSEN = "Logo-Ansichten weggelassen: "


def _logo_kandidaten(api, aid, bisher, profil, logos: list[str], laden) -> list[tuple[str, str, dict]]:
    """(name, Herkunft, Bildteil) fuer das bisherige Logo und die Logo-Kandidaten der Webseite, damit Claude Rand
    und Flaeche sieht (Spec §1). Was nicht ladbar oder kein Bild ist, faellt still weg."""
    kandidaten = []
    roh, _ = _bisheriges_logo(api, aid, bisher, profil)
    if roh:
        kandidaten.append(("bisher", marken_prompt.HERKUNFT_BISHER, roh))
    for i, url in enumerate(logos[:LOGO_WEB_ANSICHTEN], 1):
        geladen = laden(url)
        if geladen:
            kandidaten.append((f"web:{i}", marken_prompt.HERKUNFT_WEB, geladen[0]))
    teile = [(name, herkunft, cw._bild_als_teil(roh)) for name, herkunft, roh in kandidaten]
    return [(name, herkunft, teil) for name, herkunft, teil in teile if teil is not None]


def _logo_plaetze(ansichten: list[tuple[str, str, dict]]) -> int:
    """Plaetze unter cw.MAX_BILDER, die fuer Logo-Ansichten frei bleiben: das bisherige Logo und EIN Web-Logo."""
    return int(any(n == "bisher" for n, _, _ in ansichten)) + int(any(n.startswith("web:") for n, _, _ in ansichten))


def _logo_ansichten(ansichten: list[tuple[str, str, dict]], bildteile: list, bilder: list, hinweise: list[str]) -> None:
    """Logo-Ansichten hinter die Anhaenge, solange Platz unter cw.MAX_BILDER ist; was nicht passt, nennt ein
    Hinweis (sonst waehlte der Agent `bisher`/`web:n` blind)."""
    weg = []
    for name, herkunft, teil in ansichten:
        if len(bildteile) >= cw.MAX_BILDER:
            weg.append(name)
            continue
        bildteile.append(teil)
        bilder.append((name, herkunft))
    if weg:
        hinweise.append(LOGO_WEGGELASSEN + ", ".join(weg))


def _logo_quelle(api, aid, quelle: str, bilder, logos, laden, bisher, profil) -> tuple[bytes, str | None]:
    """(Bytes, Medienname des Originals oder None) der Logo-Quelle. Wirft LogoFehler mit lesbarem Grund."""
    if quelle == "bisher":
        roh, name = _bisheriges_logo(api, aid, bisher, profil)
        if not roh:
            raise logo_bearbeiten.LogoFehler("kein bisheriges Logo")
        return roh, name
    if quelle.startswith("web:"):
        geladen = laden(logos[int(quelle[4:]) - 1])
        if not geladen:
            raise logo_bearbeiten.LogoFehler("Logo der Webseite nicht ladbar")
        return geladen[0], None
    name = quelle[len("anhang:"):]
    if any(n == name and pdf_bilder.ist_seite(h) for n, h in bilder):
        datei, nr = name.rsplit("#", 1)
        roh = api.medium(aid, datei)
        if not roh:
            raise logo_bearbeiten.LogoFehler(f"{datei} fehlt in den Medien")
        try:
            pngs = pdf_bilder.seiten(roh, max_seiten=int(nr))
        except pdf_bilder.PdfBildFehler as e:
            raise logo_bearbeiten.LogoFehler(str(e)) from None
        if len(pngs) < int(nr):
            raise logo_bearbeiten.LogoFehler(f"{datei} hat keine Seite {nr}")
        return pngs[int(nr) - 1], None
    roh = api.medium(aid, name)
    if not roh:
        raise logo_bearbeiten.LogoFehler(f"{name} fehlt in den Medien")
    return roh, name


def _ki(comfy):
    """BiRefNet ueber ComfyUI am PC (derselbe Weg wie bild_worker._freistellen); gibt danach den
    Grafikspeicher frei. Laeuft ComfyUI nicht, LogoFehler."""
    def freistellen(png: bytes) -> bytes:
        if not comfy.laeuft():
            raise logo_bearbeiten.LogoFehler("ComfyUI läuft nicht")
        try:
            return comfy.freistellen(png, zeitlimit_s=LOGO_KI_ZEIT_S)
        finally:
            try:
                comfy.freigeben()
            except Exception:  # noqa: BLE001 - Freigeben ist nur Hoeflichkeit gegenueber Ollama
                pass
    return freistellen


def _logo_bearbeiten(api, aid, lb: dict, textfarbe: str, bilder, logos, laden, bisher, profil, comfy,
                     hinweise: list[str], spur, halten_takt_s: float = cw.HALTEN_TAKT_S) -> dict | None:
    """Logo in derselben Runde bearbeiten (Spec §1) -> {logo, logo_dunkel, logo_original} oder None: dann
    bleibt das Logo unbearbeitet und ein Hinweis nennt den Grund. Wirft nur, wenn der Auftrag nicht mehr
    uns gehoert."""
    grund = f = None
    with cw.halten(api, aid, halten_takt_s) as halter:     # Freistellen und Ablage dauern: Vergabe verlaengern
        try:
            roh, original = _logo_quelle(api, aid, lb["quelle"], bilder, logos, laden, bisher, profil)
            f = logo_bearbeiten.fassungen(roh, freistellen=lb["freistellen"], zuschneiden=lb["zuschneiden"],
                                          textfarbe=textfarbe, ki=_ki(comfy))
            if original is None:
                original = api.logo(aid, logo_bearbeiten.als_png(roh), "image/png")
            hell = api.logo(aid, f.hell, "image/png")
            dunkel = api.logo(aid, f.dunkel, "image/png")
        except logo_bearbeiten.LogoFehler as e:
            grund = str(e)
        except bild_comfy.ComfyFehler as e:
            grund = cw._kurz(e)
        except ApiFehler as e:
            if e.code in cw.FREMD and "in Arbeit" in e.grund:
                raise
            grund = e.grund[:150]
        except (OSError, ValueError) as e:
            grund = cw._kurz(e)
    if halter.verloren.is_set():
        raise cw._Verloren
    if grund is None:
        hinweise.extend(f.hinweise)
        spur.schritt(f"Logo bearbeitet ({'einfarbig' if f.einfarbig else 'mehrfarbig'})")
        return {"logo": hell, "logo_dunkel": dunkel, "logo_original": original}
    hinweise.append(LOGO_NICHT + grund)
    spur.schritt((LOGO_NICHT + grund)[:200])
    return None


def _webseite_holen(url_neu: str | None, gemerkt: str | None, mandant: str, webseite_lesen, ordner: str,
                    jetzt_s: float, spur):
    """Webseite der Runde: eine Adresse aus der Nachricht, sonst die gemerkte. Frisch (< 24 h, gleiche Adresse)
    aus dem Zwischenspeicher am PC, sonst mit dem gesicherten Leser gelesen und gemerkt."""
    url = url_neu or gemerkt
    if not url:
        return None
    fund = webseite_speicher.laden(ordner, mandant, url, jetzt_s)
    if fund is not None:
        spur.schritt("Webseite aus Zwischenspeicher")
    else:
        fund = webseite_lesen(url)
        if fund is not None and fund.seiten:
            webseite_speicher.ablegen(ordner, mandant, url, fund, jetzt_s)
        spur.schritt(f"Webseite gelesen ({len(fund.seiten)} Seiten)" if fund is not None and fund.seiten
                     else "Webseite nicht lesbar")
    if fund is not None and fund.logos:
        spur.schritt(f"Logo-Kandidaten: {len(fund.logos)}")
    return fund


def _seiten_lesen(urls: list[str], seite_lesen, hinweise: list[str], spur) -> str:
    """Die `lesen`-Adressen des Agenten mit dem gesicherten Leser (Adresssperre inklusive) -> Material."""
    teile: list[str] = []
    for url in urls:
        z = urllib.parse.urlsplit(url)
        host = z.hostname or url
        url = urllib.parse.urlunsplit((z.scheme, z.netloc, z.path, "", ""))   # ohne Query/Fragment
        if z.username is not None or z.password is not None or len(z.path) > marken_prompt.MAX_LESEN_PFAD:
            spur.schritt(f"Nicht lesbar: {host}")
            hinweise.append(f"Adresse {host} nicht gelesen: Pfad länger als {marken_prompt.MAX_LESEN_PFAD} Zeichen "
                            "oder mit Zugangsdaten")
            continue
        fund = seite_lesen(url)
        if fund is not None and fund.seiten:
            spur.schritt(f"Gelesen: {host}")
            teile += marken_prompt._fund_text(fund)
        else:
            spur.schritt(f"Nicht lesbar: {host}")
            hinweise += list(fund.hinweise) if fund is not None else [f"Webseite {url} nicht lesbar"]
    return "\n".join(teile)


def chat_bearbeiten(api, auftrag: dict, fragen_strom, webseite_lesen, logo_laden, wurzel: str,
                    uhr, schlafen, halten_takt_s, *, comfy=bild_comfy, jetzt=datetime.datetime.now,
                    seite_lesen=webseite.einzelseite, arbeit_ordner: str | None = None) -> str:
    aid = str(auftrag["id"])
    spur = denkspur.Spur(cw.spur_senden(api, aid), uhr=uhr)
    try:
        return _chat_mit_spur(api, auftrag, aid, spur, fragen_strom, webseite_lesen, logo_laden, wurzel,
                              uhr, schlafen, halten_takt_s, comfy=comfy, jetzt=jetzt, seite_lesen=seite_lesen,
                              arbeit_ordner=arbeit_ordner or webseite_speicher.ordner())
    except (_Aufgeben, cw._Verloren, ApiFehler, OSError, ValueError):
        _spur_ende(spur)
        raise


def _chat_mit_spur(api, auftrag: dict, aid: str, spur, fragen_strom, webseite_lesen, logo_laden, wurzel: str,
                   uhr, schlafen, halten_takt_s, *, comfy, jetzt, seite_lesen, arbeit_ordner: str) -> str:
    mandant, name = _firma(auftrag)
    kontext = auftrag.get("kontext") if isinstance(auftrag.get("kontext"), dict) else {}
    formular = (kontext.get("formular") if auftrag.get("art") == "bearbeitung"
                and isinstance(kontext.get("formular"), dict) else None)
    if auftrag.get("art") == "bearbeitung" and formular is None:     # nie als freier Chat mit Websuche (R4b)
        raise _Aufgeben("Bearbeitung ohne Formular – nichts geändert")
    laden = _einmal(logo_laden)
    offen = marken_prompt.offener_vorschlag(auftrag) or {}
    bilder: list[tuple[str, str]] = []
    with cw.halten(api, aid, halten_takt_s) as halter:          # Anhaenge, Webseite und Wissen dauern
        profil = markenprofil.lesen(wurzel, mandant, name)
        hinweise = list(profil.hinweise)
        webseite_im_formular = bool(str((formular or {}).get("webseite") or "").strip())
        if formular is not None:       # leere Webseite im Formular = die des aktuellen Profils bleibt (R3)
            formular = {**formular, "webseite": str(formular.get("webseite") or "").strip()
                        or str(profil.werte.get("webseite") or "")}
        gemerkt = offen.get("webseite")
        if not markenprofil.webseite_gueltig(gemerkt):
            gemerkt = None
        gemerkt = gemerkt or profil.werte.get("webseite")
        fund = (None if formular is not None      # Bearbeitung liest keine Webseite
                else _webseite_holen(adresse(auftrag.get("nachricht")), gemerkt, mandant, webseite_lesen,
                                     arbeit_ordner, jetzt().timestamp(), spur))
        if fund is not None:
            hinweise += fund.hinweise
        logos = list(fund.logos) if fund is not None else []
        bisher = marken_prompt.bisheriges_logo(auftrag)      # Logo des offenen Vorschlags (C1/R14)
        # Logo-Ansichten zuerst bestimmen: ihre Plaetze (bisheriges + ein Web-Logo) bleiben frei, auch wenn
        # Anhaenge und PDF-Seiten mehr fuellen koennten (sonst sieht der Agent das Logo nicht)
        ansichten = _logo_kandidaten(api, aid, bisher, profil, logos, laden)
        bildteile, unterlagen_text, _, anhang_hinweise = cw.anhaenge_vorbereiten(
            api, aid, auftrag, bilder, max_bilder=cw.MAX_BILDER - _logo_plaetze(ansichten))
        hinweise[:0] = anhang_hinweise
        _logo_ansichten(ansichten, bildteile, bilder, hinweise)
        wissen = markenwissen.laden(wurzel, mandant, name, str(auftrag.get("nachricht") or ""), ohne_marke=True)
        hinweise += [h for h in wissen.hinweise if not h.startswith("Kein Markenwissen")]
    if halter.verloren.is_set():
        return "fehler"
    text_nutzer = marken_prompt.nutzer_text(auftrag, profil, fund, unterlagen_text, bilder, hinweise,
                                            firmenwissen=wissen.text, notizen=wissen.notizen, formular=formular)
    nachrichten = [{"role": "user", "content": [{"type": "text", "text": text_nutzer}, *bildteile]
                    if bildteile else text_nutzer}]
    zustand = {"mit_bildern": bool(bildteile), "text_nutzer": text_nutzer, "hinweise": hinweise}
    anhaenge = [n for n, h in bilder if h not in marken_prompt.LOGO_ANSICHTEN]
    bisher_vorhanden = bool(bisher or profil.logo_pfad)
    lesen_frei, versuch, korrigiert, logo_hinweis = formular is None, 1, [], False
    while True:
        text = _text_holen(api, aid, fragen_strom, nachrichten, zustand, uhr, schlafen, halten_takt_s, spur,
                           websuche=formular is None)
        try:
            if formular is not None:       # Logo bleibt bei der Formular-Bearbeitung unveraendert (R4)
                text, logo_verworfen = marken_prompt.logo_verwerfen(text)
                logo_hinweis = logo_hinweis or logo_verworfen
            erg = marken_prompt.antwort_lesen(text, anhaenge, len(logos), bisher,
                                              bisher_vorhanden=bisher_vorhanden, lesen_erlaubt=lesen_frei,
                                              korrekturen_heben=formular is not None)
            if formular is not None:
                if not webseite_im_formular:     # leeres Webseitenfeld = unveraendert: keine Korrektur dafuer
                    erg["korrekturen"] = [k for k in erg["korrekturen"] if k["feld"] != "webseite"]
                if erg["vorschlag"] is None:
                    raise marken_prompt.AntwortFehler("Zur Bearbeitung gehört ein vollständiger Vorschlag")
                korrigiert = marken_prompt.formular_abgleich(formular, erg["vorschlag"], erg["korrekturen"])
        except marken_prompt.AntwortFehler as e:
            if versuch == 2:
                raise _Aufgeben(cw.NICHT_UMGESETZT + str(e)) from None
            versuch = 2
            spur.schritt(f"Antwort geprüft: {e}")
            spur.korrektur()
            nachrichten += [{"role": "assistant", "content": text},
                            {"role": "user", "content": marken_prompt.korrektur_text(str(e))}]
            continue
        if erg["lesen"]:                     # hoechstens eine Folgerunde je Auftrag
            lesen_frei = False
            with cw.halten(api, aid, halten_takt_s) as halter:     # bis 3 Abrufe: Vergabe verlaengern
                material = _seiten_lesen(erg["lesen"], seite_lesen, hinweise, spur)
            if halter.verloren.is_set():
                return "fehler"
            nachrichten += [{"role": "assistant", "content": text},
                            {"role": "user", "content": marken_prompt.folge_text(material)}]
            continue
        break
    hinweise += korrigiert
    if logo_hinweis:
        hinweise.append(LOGO_BLEIBT)
    if not api.weiter(aid):
        return "fehler"
    vorschlag = erg["vorschlag"]
    if vorschlag is None:
        spur.schritt("Antwort ohne Vorschlag")
        spur.ende()
        api.fertig(aid, {"antwort": erg["antwort"], "hinweise": _hinweise(hinweise)})
        return "fertig"
    if formular is not None:           # leerer Formularabschnitt = Abschnitt geleert (Uebernehmen entfernt ihn)
        vorschlag["abschnitte"] = {n: vorschlag["abschnitte"].get(n, "") for n in markenprofil.ABSCHNITT_REIHENFOLGE}
    if formular is not None:           # Logo und Webseite kommen nie vom Agenten (R3/R4)
        vorschlag.pop("logo_bearbeiten", None)
        vorschlag["logo"] = bisher
        if bisher:
            vorschlag.update({k: offen[k] for k in marken_prompt.WERKZEUG_FELDER if isinstance(offen.get(k), str)})
        spur.schritt("Vorschlag abgelegt")
        spur.ende()
        api.vorschlag(aid, {"vorschlag": vorschlag, "antwort": erg["antwort"], "hinweise": _hinweise(hinweise)})
        return "fertig"
    if vorschlag.get("webseite") is None and markenprofil.webseite_gueltig(offen.get("webseite")):
        vorschlag["webseite"] = offen["webseite"]       # null = bleibt: die Webseite des offenen Vorschlags
    lb = vorschlag.pop("logo_bearbeiten", None)
    bearbeitet = (_logo_bearbeiten(api, aid, lb, vorschlag["text"], bilder, logos, laden, bisher, profil, comfy,
                                   hinweise, spur, halten_takt_s) if lb else None)
    if bearbeitet:
        vorschlag.update(bearbeitet)
    else:
        logo = vorschlag.get("logo")
        if isinstance(logo, str) and logo.startswith("web:"):
            vorschlag["logo"] = _web_logo(api, aid, logos[int(logo[4:]) - 1], laden, hinweise)
        elif logo is None and bisher:
            vorschlag["logo"] = bisher        # null = "Logo bleibt" - beim offenen Vorschlag also dessen Logo
        if bisher and vorschlag.get("logo") == bisher:     # dunkle Fassung und Original gehoeren zum Logo
            vorschlag.update({k: offen[k] for k in marken_prompt.WERKZEUG_FELDER if isinstance(offen.get(k), str)})
    spur.schritt("Vorschlag abgelegt")
    spur.ende()
    api.vorschlag(aid, {"vorschlag": vorschlag, "antwort": erg["antwort"], "hinweise": _hinweise(hinweise)})
    return "fertig"

# --- Spiegel -------------------------------------------------------------------------

def _datei_bytes(pfad: str | None) -> bytes | None:
    if not pfad:
        return None
    try:
        with open(pfad, "rb") as f:
            roh = f.read(markenprofil.LOGO_MAX_BYTES + 1)
    except OSError:
        return None
    return roh if len(roh) <= markenprofil.LOGO_MAX_BYTES else None


def spiegel_gestalt(profil) -> dict:
    """Spiegel-Gestalt aus den gueltigen Werten des Profils (ungueltige fehlen schon in profil.werte)."""
    return markenprofil.gestalt(profil.werte, _datei_bytes(profil.logo_pfad), None,
                                _datei_bytes(profil.logo_dunkel_pfad))


def _spiegeln(api, mandant: str, gestalt: dict, stand: str) -> str | None:
    """None bei Erfolg, sonst der Grund (R5: ok:false mit HTTP 200 ist ein Fehlschlag)."""
    r = api.spiegeln(mandant, gestalt, stand)
    if isinstance(r, dict) and r.get("ok"):
        return None
    grund = r.get("fehler") if isinstance(r, dict) else None
    return f"{SPIEGEL_FEHLER}{grund or 'Gestalt ungültig'}"


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


def profil_daten(profil) -> dict | None:
    """Das echte Profil fuer die Formular-Vorbefuellung (Ruling R8): gueltige Werte ohne Logos/data-URLs und die
    sieben Abschnitte der Marke.md. None ohne Marke.md-Inhalt; zu gross -> ValueError (nichts wird gekuerzt)."""
    werte = {k: v for k, v in profil.werte.items()
             if k in PROFIL_WERTE and isinstance(v, str) and not v.lstrip().lower().startswith("data:")}
    abschnitte = {n: profil.abschnitte[n] for n in markenprofil.ABSCHNITT_REIHENFOLGE
                  if isinstance(profil.abschnitte.get(n), str)}
    if not werte and not abschnitte:
        return None
    daten = {"werte": werte, "abschnitte": abschnitte}
    if len(json.dumps(daten, ensure_ascii=False).encode("utf-8")) > PROFIL_MAX:
        raise ValueError(PROFIL_ZU_GROSS)
    return daten


def _profil_melden(api, mandant: str, name: str, profil, bisher=None) -> str | None:
    """Echtes Profil an die VM, wenn es sich gegenueber `bisher` (Stand der VM) geaendert hat -> Meldung bei
    Fehlschlag oder Uebergroesse."""
    try:
        daten = profil_daten(profil)
    except ValueError as e:
        return f"{name}: {e}"
    if daten is None or daten == bisher:
        return None
    try:
        api.profil(mandant, daten)
    except (ApiFehler, OSError, ValueError) as e:
        return f"{name}: Profil nicht gemeldet: {cw._kurz(e)}"
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
        meldung = _profil_melden(api, mandant, name, profil, firma.get("profil"))
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
    spur = denkspur.Spur(cw.spur_senden(api, aid))
    try:
        return _uebernehmen_mit_spur(api, auftrag, aid, spur, wurzel, jetzt, halten_takt_s)
    except (_Aufgeben, cw._Verloren, ApiFehler, OSError, ValueError):
        _spur_ende(spur)
        raise


def _uebernehmen_mit_spur(api, auftrag: dict, aid: str, spur, wurzel: str, jetzt, halten_takt_s) -> str:
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
        logo_dunkel = _logo_holen(api, aid, v.get("logo_dunkel")) if logo is not None else None
        if halter.verloren.is_set():
            raise cw._Verloren
        alt = markenprofil.lesen(wurzel, mandant, name)
        # Bisheriges bleibt, was der Vorschlag nicht nennt - auch das Logo, wenn kein neues kommt.
        werte = {k: w for k, w in alt.werte.items() if k != "stand"}
        werte.update(neue_werte)
        if markenprofil.webseite_gueltig(v.get("webseite")):
            werte["webseite"] = v["webseite"]
        try:
            markenprofil.schreiben(wurzel, mandant, name, werte, {**alt.abschnitte, **abschnitte}, logo,
                                   str(auftrag.get("von") or VON_VORGABE), jetzt(),
                                   logo_dunkel=logo_dunkel)
            spur.schritt("Rowboat geschrieben")
        except markenprofil.MarkenFehler as e:
            raise _Aufgeben(f"Übernehmen nicht möglich: {e}") from None
    # Ab hier ist Rowboat geschrieben: ein Spiegel-Fehler wird Hinweis, der Abgleich holt nach.
    neu = markenprofil.lesen(wurzel, mandant, name)
    hinweise = list(neu.hinweise)
    try:
        gestalt = spiegel_gestalt(neu)
        if gestalt.get("logo"):
            spur.schritt("Logo verkleinert")
        fehler = _spiegeln(api, mandant, gestalt, neu.werte.get("stand", ""))
    except ApiFehler as e:
        if e.code in cw.FREMD and "in Arbeit" in e.grund:
            raise
        fehler = f"Spiegel nicht aktualisiert: {e.grund[:150]}"
    except (OSError, ValueError) as e:
        fehler = f"Spiegel nicht erreichbar: {cw._kurz(e)}"
    spur.schritt(f"Spiegel abgelehnt: {fehler.removeprefix(SPIEGEL_FEHLER)}" if fehler else "Spiegel aktualisiert")
    if fehler:
        hinweise.append(fehler + " – der Abgleich holt es nach.")
    try:                    # Profilseite: Lese-Hinweise der neuen Marke.md (sonst erst beim naechsten Abgleich)
        api.hinweise(mandant, _hinweise(neu.hinweise))
    except (ApiFehler, OSError, ValueError):
        pass                # nicht auftragsgebunden; der Abgleich meldet sie spaetestens in 10 Minuten
    meldung = _profil_melden(api, mandant, name, neu)   # Formular-Vorbefuellung sofort aus der neuen Marke.md (R8)
    if meldung:
        hinweise.append(meldung)
    spur.ende()
    api.fertig(aid, {"antwort": UEBERNOMMEN, "hinweise": _hinweise(hinweise)})
    return "fertig"


# --- Durchlauf -----------------------------------------------------------------------

def ein_durchlauf(api, fragen_strom=cw.frage_strom, webseite_lesen=webseite.lesen,
                  logo_laden=webseite.logo_laden, wurzel: str | None = None, jetzt=datetime.datetime.now,
                  uhr=time.monotonic, schlafen=time.sleep, halten_takt_s: float = cw.HALTEN_TAKT_S,
                  comfy=bild_comfy, seite_lesen=webseite.einzelseite, arbeit_ordner: str | None = None,
                  arten: tuple[str, ...] | None = None) -> str:
    """Ein Auftrag. Ohne arten = Marken-Faden (die VM gibt alles ausser wissen), arten=("wissen",) =
    Wissens-Faden (wissen_arbeiter.ein_durchlauf). Jede Art wird hier bearbeitet - auch wenn eine alte VM den
    Filter nicht kennt, geht so kein Auftrag verloren."""
    auftrag = api.naechster(arten) if arten else api.naechster()
    if not auftrag:
        return "leer"
    aid = str(auftrag["id"])
    wurzel = wurzel or markenwissen.wurzel()
    try:
        if auftrag.get("art") in ("chat", "bearbeitung"):
            return chat_bearbeiten(api, auftrag, fragen_strom, webseite_lesen, logo_laden, wurzel,
                                   uhr, schlafen, halten_takt_s, comfy=comfy, jetzt=jetzt, seite_lesen=seite_lesen,
                                   arbeit_ordner=arbeit_ordner)
        if auftrag.get("art") == "uebernehmen":
            return uebernehmen(api, auftrag, wurzel, jetzt, halten_takt_s)
        if auftrag.get("art") == "wissen":
            from spaces.marketing.workers import wissen_arbeiter   # importiert dieses Modul selbst
            return wissen_arbeiter.wissen_bearbeiten(api, auftrag, fragen_strom, jetzt, uhr, schlafen, halten_takt_s)
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
