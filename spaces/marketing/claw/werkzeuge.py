"""marketing-claw-Werkzeuge — Staging rein, Artefakte raus, NIE senden.

Alle Netzpfade laufen ueber die Marketing-API :5510 (X-API-Key) oder den
Claude-Shim :8114. Es gibt hier bewusst keinen Weg zu einem Sendepfad:
kein send_*, kein approve_* — genehmigen tut der Mensch in der UI.

Fail-soft wie sales-claws rowboat.py: keine Funktion wirft; Rueckgabe ist
{"ok": True, ...} oder {"ok": False, "fehler": "..."} — und in keinem
Fehlertext steht je ein Schluessel.
"""
import json
import time
import os
import urllib.parse
import urllib.request

from spaces.marketing.claw import (ablage, laura, llm, schaufenster,
                                   schoenheit, stil, wissen)

FEHLER_MAXLAENGE = 300


def _ohne_schluessel(text: str) -> str:
    for name in ("MARKETING_API_KEY", "MARKETING_PROPOSAL_API_KEY", "ROWBOAT_API_KEY"):
        wert = os.environ.get(name, "")
        if wert and wert in text:
            text = text.replace(wert, "<schluessel>")
    return text


def _roh_anfrage(url: str, daten, kopfzeilen: dict) -> tuple:
    """Der einzige echte Netzgriff — Tests ersetzen genau diese Funktion."""
    anfrage = urllib.request.Request(
        url, data=daten, method="POST" if daten is not None else "GET",
        headers={"Content-Type": "application/json", **kopfzeilen})
    with urllib.request.urlopen(anfrage, timeout=60) as antwort:
        return antwort.status, antwort.read().decode("utf-8", "replace")


def _api(pfad: str, nutzlast: dict | None = None) -> dict:
    """GET (nutzlast=None) oder POST gegen die Marketing-API. Wirft nie."""
    basis = os.environ.get("MARKETING_API_URL", "http://127.0.0.1:5510").rstrip("/")
    daten = None if nutzlast is None else json.dumps(nutzlast).encode("utf-8")
    try:
        status, rumpf = _roh_anfrage(
            basis + pfad, daten, {"X-API-Key": os.environ.get("MARKETING_API_KEY", "")})
        if status != 200:
            return {"ok": False, "fehler": _ohne_schluessel(
                f"Marketing-API HTTP {status}: {rumpf[:FEHLER_MAXLAENGE]}")}
        return {"ok": True, "daten": json.loads(rumpf)}
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": _ohne_schluessel(
            f"Marketing-API nicht erreichbar ({type(e).__name__}: {e})")}


def statistik() -> dict:
    """Kennzahlen des Marketing-Space (accounts, Kampagnen, Audit-Stand)."""
    return _api("/api/stats")


def _mirofish():
    """Der bestehende Mirofish-Client des Space — spaet importiert, damit
    dieses Modul ohne ihn nutzbar bleibt (er zieht weitere Abhaengigkeiten)."""
    from spaces.marketing.mirofish import predict_post_reception
    return predict_post_reception


def _SCHLAF(sekunden: float) -> None:
    """Eigene Funktion, damit Tests das Warten ersetzen koennen."""
    time.sleep(sekunden)


def kampagne_pruefen(text: str, kanal: str = "telegram", titel: str = "",
                     frist_s: float = 900.0) -> dict:
    """Laesst einen Entwurf von simuliertem Publikum bewerten (Mirofish).

    Mirofish baut aus dem Text einen Wissensgraphen, erzeugt hunderte
    Personas und simuliert die Reaktion; heraus kommt ein Report mit einer
    Punktzahl 0-100 und Stimmen einzelner Personas. Das ist eine
    QUALITAETSPRUEFUNG VOR der Freigabe — sie versendet nichts und
    genehmigt nichts.

    Teuer und langsam (Ollama + Neo4j), deshalb nur auf Abruf und mit
    Frist: laeuft die Simulation laenger, sagt die Antwort ehrlich, in
    welcher Phase sie steckt, statt endlos zu warten.
    """
    if len((text or "").strip()) < 20:
        return {"ok": False, "fehler": "Entwurf zu kurz fuer eine Simulation (unter 20 Zeichen)"}
    klient = _mirofish()
    bezeichner = f"entwurf-{int(time.time())}"
    try:
        zustand = klient.kick_off(bezeichner, text, kanal, bubble_title=titel or None)
    except Exception as e:  # noqa: BLE001 — ausgeschaltete Mirofish ist Alltag
        return {"ok": False, "fehler": f"Mirofish nicht nutzbar ({type(e).__name__}: {e})"}

    ende = time.time() + max(0.0, frist_s)
    while zustand.get("phase") != "done":
        if time.time() > ende:
            return {"ok": False,
                    "fehler": f"Simulation noch nicht fertig (Phase {zustand.get('phase')}) — "
                              "spaeter erneut pruefen"}
        try:
            zustand = klient.poll_status(zustand)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "fehler": f"Mirofish-Lauf abgebrochen ({type(e).__name__}: {e})"}
        if zustand.get("phase") != "done":
            _SCHLAF(10)

    try:
        report = klient.read_report(zustand.get("report_id") or zustand.get("reportId", ""))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "fehler": f"Report nicht lesbar ({type(e).__name__}: {e})"}

    stimmen = "\n".join(
        f"- {p.get('name', '?')}: {p.get('stance', p.get('summary', ''))}"
        for p in (report.get("persona_summary") or [])) or "- (keine Einzelstimmen)"
    bericht = (f"# Publikumsprobe: {titel or bezeichner}\n\n"
               f"Kanal: {kanal}\nPunktzahl (0-100): {report.get('score')}\n"
               f"Report: {report.get('report_id')}\n\n"
               f"## Geprueft wurde\n\n{text}\n\n"
               f"## Stimmen aus der Simulation\n\n{stimmen}\n\n"
               f"## Vollstaendiger Report\n\n"
               f"{json.dumps(report.get('full_report'), indent=2, ensure_ascii=False)}\n")
    pfad = schaufenster.ablegen(titel or bezeichner, "publikumsprobe.md", bericht)
    return {"ok": True, "score": report.get("score"),
            "report_id": report.get("report_id"), "dateien": [pfad]}


def _rowboat(werkzeug: str, argumente: dict) -> dict:
    """Ein Lese-Werkzeugaufruf gegen Rowboats MCP-Endpunkt auf der VM.

    PASSTHROUGH IM SIDECAR, NICHT IM GATEWAY — Absicht, kein Notbehelf:
    Container erreichen im WSL-Mirrored-Modus kein LAN (gemessen 02.09.2026),
    und so bleibt der Bearer-Schluessel im Host-Prozess statt im
    Gateway-Volume. Die Projektbindung ist fest: projectId kommt aus der
    Env, nie vom Aufrufer — der Agent kann keine fremden Projekte anfragen.
    """
    basis = os.environ.get("ROWBOAT_URL", "").strip().rstrip("/")
    schluessel = os.environ.get("ROWBOAT_API_KEY", "").strip()
    fehlt = [n for n, w in (("ROWBOAT_URL", basis), ("ROWBOAT_API_KEY", schluessel),
                            ("ROWBOAT_PROJECT_ID", os.environ.get("ROWBOAT_PROJECT_ID", "").strip()))
             if not w]
    if fehlt:
        return {"ok": False, "fehler": "Wissensbasis nicht eingerichtet, es fehlt: " + ", ".join(fehlt)}
    nutzlast = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": werkzeug, "arguments": argumente}}
    try:
        status, rumpf = _roh_anfrage(
            basis + "/api/mcp", json.dumps(nutzlast).encode("utf-8"),
            {"Authorization": f"Bearer {schluessel}"})
        if status != 200:
            return {"ok": False, "fehler": _ohne_schluessel(
                f"Wissensbasis HTTP {status}: {rumpf[:FEHLER_MAXLAENGE]}")}
        antwort = json.loads(rumpf)
        if "error" in antwort:
            return {"ok": False, "fehler": _ohne_schluessel(
                str(antwort["error"].get("message", ""))[:FEHLER_MAXLAENGE])}
        ergebnis = antwort["result"]
        text = ergebnis["content"][0]["text"]
        if ergebnis.get("isError"):
            return {"ok": False, "fehler": _ohne_schluessel(text[:FEHLER_MAXLAENGE])}
        return {"ok": True, "daten": json.loads(text)}
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": _ohne_schluessel(
            f"Wissensbasis nicht erreichbar ({type(e).__name__}: {e})")}


def wissensquellen() -> dict:
    """Die Wissensquellen des VibeMind-Projekts (Name, Status). Nur lesend."""
    return _rowboat("rowboat_wissensquellen",
                    {"projectId": os.environ.get("ROWBOAT_PROJECT_ID", "").strip()})


def wissensquelle(quellen_id: str) -> dict:
    """Eine Wissensquelle im Detail. Nur lesend."""
    return _rowboat("rowboat_wissensquelle", {"sourceId": quellen_id})


def dokumente(quellen_id: str, mit_inhalt: bool = False) -> dict:
    """Dokumente einer Wissensquelle, optional mit Text. Nur lesend."""
    return _rowboat("rowboat_dokumente",
                    {"sourceId": quellen_id, "mitInhalt": mit_inhalt is True})


def publikum_vorschlagen(name: str, kriterien: dict, begruendung: str = "") -> dict:
    """Publikums-VORSCHLAG in die Staging-Tuer /api/proposals — genehmigen
    tut der Mensch. source ist fest 'marketing-claw' (unbekannte sources
    normalisiert der Server zu hand:unknown — sichtbar im Audit, gewollt)."""
    if not isinstance(kriterien, dict):
        return {"ok": False, "fehler": "kriterien muss ein Objekt sein"}
    return _api("/api/proposals", {
        "api_key": os.environ.get("MARKETING_PROPOSAL_API_KEY", ""),
        "name": name,
        "filter_dsl": kriterien,
        "rationale": begruendung,
        "source": "marketing-claw",
    })


def posteingang_lesen() -> dict:
    """Eingegangene Nachrichten (marketing.inbound_messages) — nur lesen."""
    return _api("/api/inbox")


def kampagnen_auflisten() -> dict:
    """Bestehende Kampagnen — nur lesen."""
    return _api("/api/campaigns")


def _llm_json(system: str, nutzer: str) -> dict:
    """LLM fragen und die Antwort als JSON-Objekt lesen — fail-soft."""
    r = llm.frage(system, nutzer)
    if not r["ok"]:
        return r
    text = r["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return {"ok": True, "daten": json.loads(text)}
    except Exception:  # noqa: BLE001
        return {"ok": False, "fehler": "LLM-Antwort war kein JSON"}


def _liste(werte) -> list:
    """Belege/Zu-klaeren kommen vom Agenten — als Liste oder als eine Zeile je Eintrag."""
    if not werte:
        return []
    if isinstance(werte, str):
        return [z.strip("- ").strip() for z in werte.splitlines() if z.strip()]
    return [str(w).strip() for w in werte if str(w).strip()]


# Wohin WhatsApp wirklich gehoert. Der Kanal EXISTIERT in diesem Haus — nur
# nicht in diesem Space: sales-claw verschickt ihn ueber openwa
# (`dispatch.py`), pro KONTAKT statt als Rundnachricht und mit eigener
# Einwilligungspruefung. Wer hier einen whatsapp-Entwurf anlegt, legt eine
# Datei an, die nie rausgeht.
_WOHIN_STATT = {
    "whatsapp": ("WhatsApp verschickt sales-claw ueber openwa — pro Kontakt, "
                 "mit Einwilligungspruefung. Schreib den Text und uebergib ihn "
                 "dorthin, statt hier einen Rundnachrichten-Entwurf anzulegen."),
    "linkedin": ("LinkedIn laeuft ueber sales-claws Einmal-Versender, nicht "
                 "ueber diesen Space."),
}


def _kanal_kann_senden(kanal: str) -> tuple:
    """(geht, hinweis) — fragt die API, haelt keine Liste im Kopf.

    Ein Kanal ohne gebauten Versandweg nimmt Entwuerfe stumm an: sie landen
    in broadcast_proposals, sehen fertig aus und koennen nie zugestellt
    werden (gemessen 04.09. und 12.09.2026 fuer `whatsapp`). Die Auskunft
    kommt vom Dienst, damit die Pruefung von selbst mitwaechst, sobald ein
    Versandweg dazukommt.

    Faellt die Auskunft aus, wird NICHT blockiert: ein Entwurf darf nicht an
    der Verfuegbarkeit einer Nebenroute haengen.
    """
    antwort = _api("/api/channels")
    if not antwort["ok"]:
        return True, ""
    roh = (antwort.get("daten") or {})
    kanaele = roh.get("data") if isinstance(roh, dict) else None
    # Kommt etwas anderes zurueck als eine Liste von Objekten, ist die
    # Auskunft unbrauchbar — dann NICHT blockieren, sondern durchlassen.
    # (Ohne diese Pruefung lief die Schleife ueber die Schluessel eines
    # dicts und starb an `'str' object has no attribute 'get'`.)
    if not isinstance(kanaele, list) or not all(isinstance(k, dict) for k in kanaele):
        return True, ""
    passend = next((k for k in kanaele if k.get("channel") == kanal), None)
    if passend is None:
        moeglich = sorted(k["channel"] for k in kanaele
                          if k.get("enabled") and k.get("send_implemented"))
        return False, (f"Kanal '{kanal}' kennt die Marketing-API nicht. "
                       f"Versandfaehig: {', '.join(moeglich) or 'keiner'}.")
    if passend.get("enabled") and passend.get("send_implemented"):
        return True, ""
    moeglich = sorted(k["channel"] for k in kanaele
                      if k.get("enabled") and k.get("send_implemented"))
    hinweis = (f"Kanal '{kanal}' hat hier keinen Versandweg — ein Entwurf "
               f"darauf koennte nie zugestellt werden. Versandfaehig: "
               f"{', '.join(moeglich) or 'keiner'}.")
    if kanal in _WOHIN_STATT:
        hinweis += " " + _WOHIN_STATT[kanal]
    return False, hinweis


# Die Kanaele, die sales-claw wirklich zustellt. Die Liste steht hier NICHT,
# um eine Entscheidung zu treffen — die faellt in der DB-Funktion — sondern
# damit der Hinweistext unten sie nennen kann, ohne einen Netzgriff dafuer
# zu brauchen. Weicht sie eines Tages ab, sagt die Absage aus der Datenbank
# die Wahrheit, nicht diese Zeile.
VERSANDKANAELE = ("whatsapp", "email", "linkedin", "linkedin_post")


def versand_beauftragen(kanal: str, nachricht: str, empfaenger: str = "",
                        betreff: str = "", medien_datei: str = "",
                        kampagne: str = "", quelle: str = "") -> dict:
    """Bittet sales-claw, diese Nachricht zuzustellen. DER EINZIGE WEG NACH
    DRAUSSEN (Betreiber-Entscheid 12.09.2026).

    Dieser Space versendet nichts — er schreibt. Ein Auftrag ist eine BITTE:
    sales-claw ordnet die Adresse einem Kontakt zu, laesst sie durch seine
    Tore laufen (gemeinsame Verbotsliste, Loeschantrag, Privat-Flag,
    UWG-Erstansprache, WhatsApp-Freigabe, Anhang) und macht daraus
    HOECHSTENS einen Entwurf. Freigeben tut den ein Mensch.

    kanal:
      whatsapp | email | linkedin  — Nachricht AN EINEN KONTAKT; `empfaenger`
                                     ist die E-Mail oder die Telefonnummer.
      linkedin_post                — Beitrag aufs eigene Profil: KEIN
                                     `empfaenger`, dafuer `betreff` als Thema.
    Telegram gibt es hier nicht — sales-claw hat dafuer keinen Versandweg.

    medien_datei: der BLOSSE Dateiname aus dem Schaufenster (post_ablegen,
    pdf_erstellen legen dorthin ab), ohne Pfad.

    Eine Absage kommt als `{"ok": false, "fehler": "<Grund>"}` — flach, nicht
    als negatives Ergebnis in einer erfolgreichen Huelle: `ok: true` mit einem
    zweiten `ok: false` darin wird ueberlesen, und das Ueberlesen einer
    Absage ist hier der teuerste Fehler. Derselbe Auftrag zweimal erzeugt
    keine zweite Nachricht — die Antwort traegt dann `wiederholung: true`
    und dieselbe Kennung.
    """
    if not (nachricht or "").strip():
        return {"ok": False, "fehler": "Die Nachricht ist leer."}
    # Hausstil auch hier: dieser Weg umgeht `kampagne_entwerfen`, und was
    # rausgeht, soll denselben Regeln folgen wie das, was entworfen wird.
    nachricht, gekuerzt = stil.hausstil(nachricht)
    betreff, n2 = stil.hausstil(betreff or "")
    gekuerzt += n2
    antwort = _api("/api/versandauftraege", {
        "kanal": kanal, "empfaenger": empfaenger, "nachricht": nachricht,
        "betreff": betreff, "medien_datei": medien_datei,
        "kampagne": kampagne, "quelle": quelle})
    if not antwort["ok"]:
        return antwort
    ergebnis = ((antwort.get("daten") or {}).get("data")) or {}
    if not isinstance(ergebnis, dict) or "ok" not in ergebnis:
        return {"ok": False, "fehler": (
            "Die Marketing-API hat auf den Auftrag etwas Unerwartetes "
            "geantwortet — es ist unklar, ob er angelegt wurde. Sieh mit "
            "versandauftraege_lesen nach, bevor du es noch einmal versuchst.")}
    if not ergebnis.get("ok"):
        return {"ok": False, "fehler": ergebnis.get("grund")
                or "Der Auftrag wurde ohne Begruendung abgelehnt."}
    daten = {"auftrag_id": ergebnis.get("id"),
             "wiederholung": bool(ergebnis.get("wiederholung")),
             "hinweis": ergebnis.get("grund", "")}
    if gekuerzt:
        daten["hausstil"] = (
            f"{gekuerzt} lange(r) Gedankenstrich(e) gekuerzt (siehe stil.py).")
    return {"ok": True, "daten": daten}


def versandauftraege_lesen(status: str = "", anzahl: int = 20) -> dict:
    """Was aus deinen Auftraegen geworden ist: offen, angenommen (mit der
    Entwurfskennung) oder abgelehnt — dann steht im Grund WOERTLICH, was
    sales-claws Tor gesagt hat. `status` leer heisst: alle."""
    pfad = f"/api/versandauftraege?limit={max(1, min(int(anzahl or 20), 100))}"
    if (status or "").strip():
        pfad += "&status=" + urllib.parse.quote((status or "").strip())
    antwort = _api(pfad)
    if not antwort["ok"]:
        return antwort
    return {"ok": True, "daten": (antwort.get("daten") or {}).get("data") or []}


def kampagne_entwerfen(ziel: str, zielgruppe: str, kanal: str, kontext: str = "",
                       belege=None, zu_klaeren=None) -> dict:
    """Entwirft eine Kampagne: Briefing + Text als broadcast_proposals-Draft
    (Status draft — versendet NIE) plus Dateien im Schaufenster.

    DAS IST DAS REDAKTIONELLE ARTEFAKT, NICHT DER WEG NACH DRAUSSEN. Ein
    Vorschlag landet in der Freigabe-Oberflaeche und wird dort gelesen,
    geaendert und beurteilt — zugestellt wird er nie. Seit dem
    Betreiber-Entscheid vom 12.09.2026 versendet dieser Space ueberhaupt
    nichts mehr: wer eine Nachricht raus haben will, ruft
    `versand_beauftragen` und uebergibt sie sales-claw.

    BELEGPFLICHT (Betreiber-Entscheid 03.09.2026): `belege` sind die Quellen
    aus der Wissensbasis, auf die sich die Produktaussagen stuetzen (je
    Eintrag Quellname + Dokument + Aussage); `zu_klaeren` sind Aussagen, die
    der Agent gern gemacht haette, aber nicht belegen konnte — sie stehen im
    Briefing, NICHT im Text. Beide liefert der Agent, der die Wissensbasis
    gelesen hat; das innere LLM erfindet keine Belege.
    """
    belege = _liste(belege)
    zu_klaeren = _liste(zu_klaeren)
    geht, hinweis = _kanal_kann_senden(kanal)
    if not geht:
        return {"ok": False, "fehler": hinweis}
    r = _llm_json(
        "Du bist Marketing-Texter fuer VibeMind. Antworte NUR mit einem "
        'JSON-Objekt {"betreff": ..., "text": ..., "begruendung": ...}. '
        "Deutsch, konkret, keine Superlative. Verwende NUR Produktaussagen, "
        "die im Kontext oder in den Belegen stehen — nichts dazuerfinden.",
        f"Kampagnenziel: {ziel}\nZielgruppe: {zielgruppe}\nKanal: {kanal}\n"
        f"Kontext:\n{kontext}\n\nBelegte Produktaussagen:\n"
        + ("\n".join(f"- {b}" for b in belege) or "- (keine)"))
    if not r["ok"]:
        return r
    entwurf = r["daten"]

    # HAUSSTIL, bevor der Text irgendwo landet. Hier und nicht erst im PDF:
    # aus demselben Entwurf entstehen die E-Mail, die Freigabe-Oberflaeche
    # und das PDF — griffe die Regel erst beim Setzen, traegen die drei
    # verschiedene Texte. Rueckmeldung eines Lesers am 11.09.2026: lange
    # Gedankenstriche „sieht zu sehr nach KI aus" (siehe stil.py).
    eingriffe = 0
    for schluessel in ("betreff", "text", "begruendung"):
        gekuerzt, n = stil.hausstil(str(entwurf.get(schluessel, "")))
        entwurf[schluessel] = gekuerzt
        eingriffe += n
    belege_md = "\n".join(f"- {b}" for b in belege) or \
        "- (keine Belege angegeben — Produktaussagen im Text sind damit ungeprueft)"
    klaeren_md = "\n".join(f"- {z}" for z in zu_klaeren) or "- (nichts offen)"
    antwort = _api("/api/curator/broadcast_proposals", {
        "api_key": os.environ.get("MARKETING_PROPOSAL_API_KEY", ""),
        "channel": kanal,
        "draft_subject": str(entwurf.get("betreff", "")),
        "draft_body_text": str(entwurf.get("text", "")),
        # STRUKTUR, NICHT DARSTELLUNG. Frueher standen Belege und "Zu klaeren"
        # hier als fertiges HTML in `draft_body_html` — der Spalte fuer den
        # NACHRICHTENRUMPF. Zwei Fehler auf einmal: interne Notizen an einer
        # Stelle, die spaeter versendet wird, und Daten in Darstellung
        # gebacken, sodass sich das Aussehen nicht mehr wechseln liess.
        # `draft_channel_params` ist jsonb, war auf allen Zeilen leer und ist
        # genau dafuer da.
        "draft_channel_params": {
            "ziel": ziel, "zielgruppe": zielgruppe,
            "belege": belege, "zu_klaeren": zu_klaeren,
            "begruendung": str(entwurf.get("begruendung", "")),
        },
        "actor": "marketing-claw",
    })
    if not antwort["ok"]:
        return antwort
    # Die Route antwortet {success, data: {id, status}} — id liegt unter data.
    proposal_id = (antwort["daten"].get("data") or {}).get("id") or antwort["daten"].get("id")
    briefing = (f"# Kampagne: {ziel}\n\nZielgruppe: {zielgruppe}\nKanal: {kanal}\n"
                f"Proposal: {proposal_id or '?'} (Status draft — versendet nichts)\n\n"
                f"## Betreff\n{entwurf.get('betreff', '')}\n\n## Text\n{entwurf.get('text', '')}\n\n"
                f"## Belege\n{belege_md}\n\n## Zu klaeren\n{klaeren_md}\n\n"
                f"## Begruendung\n{entwurf.get('begruendung', '')}\n")
    dateien = [schaufenster.ablegen(ziel, "briefing.md", briefing)]
    ergebnis = {"ok": True, "proposal_id": proposal_id, "dateien": dateien}
    if eingriffe:
        # Sagen, statt es den Agenten beim naechsten Lesen entdecken zu
        # lassen: ein stiller Eingriff in seinen Text waere genau die Art
        # Ueberraschung, die Vertrauen kostet.
        ergebnis["hausstil"] = (
            f"{eingriffe} lange(r) Gedankenstrich(e) gekuerzt. Ein Leser hat "
            f"gemeldet, dass sie zu sehr nach KI aussehen. Schreib sie gleich "
            f"kurz, dann bleibt dein Text unveraendert.")
    return ergebnis


def ad_texte_entwerfen(thema: str, n: int = 3) -> dict:
    """n Ad-Text-Varianten als Schaufenster-Dateien. Kein Draft, kein Versand."""
    n = max(1, min(int(n), 10))
    dateien = []
    for i in range(1, n + 1):
        r = llm.frage(
            "Du bist Performance-Marketing-Texter. Eine Ad-Variante, Deutsch, "
            "max 300 Zeichen Haupttext + Ueberschrift. Nur der Anzeigentext.",
            f"Thema: {thema}\nVariante {i} von {n} — deutlich anders als die vorigen.")
        if not r["ok"]:
            return {"ok": False, "fehler": r["fehler"], "dateien": dateien}
        dateien.append(schaufenster.ablegen(thema, f"ad-{i:02d}.md", r["text"]))
    return {"ok": True, "dateien": dateien}


def layout_entwerfen(thema: str, format: str = "landingpage") -> dict:
    """Ein HTML-Layout (eine Datei, keine externen Abhaengigkeiten) ins Schaufenster."""
    r = llm.frage(
        "Du bist Web-Designer. Antworte NUR mit einer vollstaendigen "
        "HTML-Datei (inline CSS, keine externen Ressourcen, Deutsch).",
        f"Format: {format}\nThema: {thema}\nZweck: Entwurf zur Qualitaetsbewertung.")
    if not r["ok"]:
        return r
    text = r["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    kurz = "".join(c if c.isalnum() else "-" for c in format.lower())[:30]
    return {"ok": True, "dateien": [schaufenster.ablegen(thema, f"layout-{kurz}.html", text)]}


# --- Laura: Bewegtbild als Belegquelle (nur lesend) -------------------------
# Der Marketing-Agent hatte bis 04.09.2026 keinen Weg zu den Produktvideos;
# die Entwuerfe beriefen sich deshalb auf Text allein. Diese zwei Werkzeuge
# sind der Zugang — Passthrough ohne Geschaeftslogik, siehe laura.py.


def videos() -> dict:
    """Die Videos aus Laura, jedes mit Projekt und Kennung. Nur lesend.

    Die Kennung ist der Schluessel fuer video_transkript(). Ist Laura leer,
    kommt eine leere Liste mit ok=True zurueck — das ist eine Antwort, keine
    Stoerung.
    """
    return laura.alle_videos()


def video_transkript(video_id: str) -> dict:
    """Das Transkript eines Videos — der belegbare Text zum Bild. Nur lesend.

    Segmente mit Zeitstempel. Was hier steht, ist zitierfaehig; was nicht
    hier steht, gehoert in "Zu klaeren" und nicht in den Entwurf.
    """
    return laura.transkript(video_id)


# --- Die ganze Wissensbasis befragen ----------------------------------------
# `dokumente()` liest EINE Quelle. Der Agent nutzte davon bis 04.09.2026 genau
# eine von vierzehn — nicht aus Faulheit, sondern weil ihm der Ueberblick
# fehlte, welche Quelle die Antwort traegt. `wissen_fragen` dreht das um: es
# sammelt alles, waehlt aus und laesst das Modell mit Belegpflicht antworten.

# Die Wissensbasis aendert sich in Minuten, nicht in Sekunden; ohne
# Zwischenspeicher kostete jede Frage 15 Abrufe ueber das LAN zur VM.
_wissen_zwischenspeicher: dict = {}
WISSEN_FRISCHE_SEKUNDEN = 300


def _wissen_sammeln() -> dict:
    """Alle Dokumente aller Quellen, mit Text. Wirft nie.

    Eine stumme Quelle kippt nicht die ganze Sammlung — sie wird gezaehlt und
    gemeldet, damit ein Ausfall sichtbar bleibt statt still zu machen.
    """
    jetzt = time.time()
    zwischen = _wissen_zwischenspeicher.get("stand")
    if zwischen and jetzt - zwischen["zeit"] < WISSEN_FRISCHE_SEKUNDEN:
        return zwischen["wert"]

    quellen = wissensquellen()
    if not quellen["ok"]:
        return quellen
    stuecke, stumm = [], []
    for quelle in quellen["daten"]:
        antwort = dokumente(str(quelle.get("id", "")), mit_inhalt=True)
        if not antwort["ok"]:
            stumm.append(quelle.get("name", quelle.get("id")))
            continue
        for dok in antwort["daten"]:
            text = dok.get("content") or dok.get("inhalt") or ""
            if text.strip():
                stuecke.append({"quelle": quelle.get("name", "?"),
                                "dokument": dok.get("name", "?"), "text": text})
    ergebnis = {"ok": True, "daten": stuecke, "stumme_quellen": stumm}
    _wissen_zwischenspeicher["stand"] = {"zeit": jetzt, "wert": ergebnis}
    return ergebnis


def wissen_fragen(frage: str) -> dict:
    """Beantwortet eine Frage aus ALLEN Wissensquellen, mit Belegen. Nur lesend.

    Rueckgabe unter "daten": `antwort` (Text mit Belegen in Klammern),
    `quellen` (die Dokumente, die wirklich im Auftrag standen — Grundwahrheit,
    keine Behauptung des Modells) und `geprueft` (wie viele Dokumente die
    Auswahl gesehen hat).

    Findet die Auswahl nichts, sagt das Werkzeug das und fragt kein Modell.
    Eine erfundene Antwort waere hier teurer als eine Fehlanzeige.
    """
    if not frage or not frage.strip():
        return {"ok": False, "fehler": "Ohne Frage keine Antwort."}
    gesammelt = _wissen_sammeln()
    if not gesammelt["ok"]:
        return gesammelt

    gewaehlt = wissen.auswaehlen(frage, gesammelt["daten"])
    belege = [wissen.bezeichnen(s) for s in gewaehlt]
    if not gewaehlt:
        return {"ok": True, "daten": {
            "antwort": ("Dazu steht nichts in der Wissensbasis. "
                        f"Durchsucht wurden {len(gesammelt['daten'])} Dokumente. "
                        "Das gehoert unter 'Zu klaeren', nicht in den Entwurf."),
            "quellen": [], "geprueft": len(gesammelt["daten"])}}

    system, nutzer = wissen.auftrag(frage, gewaehlt)
    antwort = llm.frage(system, nutzer)
    if not antwort["ok"]:
        # Das Modell fehlt, der Rohstoff nicht — wer die Belege kennt, kann
        # von Hand weiterarbeiten. Sie wegzuwerfen waere Verschwendung.
        return {"ok": False, "fehler": antwort["fehler"], "quellen": belege}
    return {"ok": True, "daten": {"antwort": antwort["text"], "quellen": belege,
                                  "geprueft": len(gesammelt["daten"])}}


def entwuerfe_lesen(status: str = "draft", kanal: str = "", anzahl: int = 20) -> dict:
    """Die bisherigen Kampagnen-Entwuerfe lesen (Betreff, Text, Status).

    PFLICHTSCHRITT VOR JEDEM NEUEN ENTWURF. Am 04.09.2026 lagen sieben
    Entwuerfe derselben Kampagne im Bestand — alle sieben dieselbe
    Aufzaehlung von vier Funktionen, nur mit anderen Emojis. Wer seine
    Historie nicht liest, schreibt sie zum achten Mal.

    Liest `marketing.broadcast_proposals` (WAS gesendet wuerde), nicht die
    Publikums-Vorschlaege. Nur lesend. status="" heisst: alle.
    """
    teile = []
    if status.strip():
        teile.append("status=" + urllib.parse.quote(status.strip()))
    if kanal.strip():
        teile.append("channel=" + urllib.parse.quote(kanal.strip()))
    teile.append(f"limit={max(1, min(100, int(anzahl)))}")
    return _api("/api/broadcast_proposals?" + "&".join(teile))


def post_ablegen(name: str, inhalt: str, art: str = "md") -> dict:
    """Legt einen fertigen Beitrag in `/media-erzeugt` ab — dort findet
    sales-claw ihn.

    `/media` ist der Ordner des MENSCHEN und fuer dich schreibgeschuetzt;
    hierhin schreibt die Maschine. sales-claw liest beide, in dieser
    Rangfolge. Erlaubte Arten: md, html, txt, json. Ueberschreibt nie —
    ein gleichnamiger Beitrag bekommt eine Zeitmarke.

    Das ist eine ABLAGE, kein Versand: was hier liegt, geht erst raus,
    wenn der Betreiber es freigibt.
    """
    return ablage.ablegen(name, inhalt, art)


# Wofuer eine Unterlage gedacht ist — steht im DATEINAMEN, nicht in einem
# Unterordner: `medien.pruefe("Marketing/post.pdf")` lehnt jeden Pfadanteil ab
# ("kein '/', kein '\\'"), und diese Pruefung ist der Riegel gegen einen
# Ausbruch aus dem Medienordner. Sortiert wird alphabetisch, also stehen die
# Unterlagen eines Zwecks in `medien_liste` ohnehin beieinander.
ZWECKE = ("marketing", "email", "mobile")

# sales-claw riegelt bei 15 MB ab (medien.py MAX_BYTES). Das faellt lieber
# hier auf als beim Anhaengen eines freigegebenen Entwurfs.
MAX_ANHANG_BYTES = 15 * 1024 * 1024


# --- Layout-Vorlagen -------------------------------------------------------
#
# Auftrag des Betreibers (12.09.2026): „die verschiedenen Layouts via
# Templates und Skills ... Template muss vom User abgesegnet werden."
#
# Aussehen war bis dahin Code: zwei fest verdrahtete Tafeln in pdf.py. Wer
# ein drittes Layout wollte, brauchte einen Entwickler — und niemand hatte
# je eine davon abgenommen. Jetzt sind Layouts Daten mit einem Tor: der
# Agent SCHLAEGT VOR, ein Mensch ENTSCHEIDET, und gesetzt wird nur, was
# freigegeben ist.
#
# Der Agent kann nicht freigeben, und zwar nicht aus Hoeflichkeit: die
# Entscheidungsroute haengt hinter MARKETING_PROPOSAL_API_KEY, den dieser
# Space nicht kennt. Ein Tor, das der Torwaechter selbst oeffnen kann, ist
# eine Formalie.

# Die acht Farben, die eine Gestalt braucht. Bewusst hier als Konstante,
# damit der Hinweistext sie nennen kann, ohne dass ein Agent raten muss.
GESTALT_FARBEN = ("grund", "flaeche", "akzent", "gold", "text",
                  "text_hell", "text_leise", "handlung_text")


def vorlagen_auflisten(status: str = "") -> dict:
    """Die Layout-Vorlagen und ihr Stand.

    `status` leer heisst alle; sonst `freigegeben`, `vorschlag` oder
    `abgelehnt`. Setzen kannst du nur, was freigegeben ist — was als
    Vorschlag dasteht, wartet auf den Betreiber, und was abgelehnt wurde,
    traegt den Grund bei sich.
    """
    pfad = "/api/layout_vorlagen"
    if (status or "").strip():
        pfad += "?status=" + urllib.parse.quote(status.strip())
    antwort = _api(pfad)
    if not antwort["ok"]:
        return antwort
    return {"ok": True, "daten": (antwort.get("daten") or {}).get("data") or []}


def vorlage_vorschlagen(name: str, beschreibung: str, gestalt: dict) -> dict:
    """Ein neues Layout VORSCHLAGEN. Es gilt erst nach Freigabe des Betreibers.

    `name` wird zum Argument von `pdf_erstellen(vorlage=...)`: nur
    Kleinbuchstaben, Ziffern und Bindestriche, 3 bis 40 Zeichen.

    `gestalt` ist ein Objekt mit ACHT Farben als `#rrggbb`:
      grund         Seitenhintergrund
      flaeche       Kopf- und Fussband
      akzent        Linien, Untertitel, der gefuellte Handlungskasten
      gold          Rubriken (BELEGE, ZU KLAEREN)
      text          Fliesstext  — NICHT reinweiss/reinschwarz, siehe Fertigkeit
      text_hell     Titel
      text_leise    Fusszeile und Kleingedrucktes
      handlung_text Schrift IM Handlungskasten (liegt auf `akzent`)

    Die Datenbank prueft, bevor etwas entsteht: fehlende Farbe, falsche
    Form, Text gleich Grund (unsichtbar) oder handlung_text gleich akzent
    (leerer Kasten) werden mit Begruendung abgewiesen.

    DANACH: erzeug mit `vorlage_muster(name)` ein Musterblatt und nenn dem
    Betreiber den Dateipfad. Eine Vorlage freizugeben, ohne sie gesehen zu
    haben, ist keine Freigabe.
    """
    if not isinstance(gestalt, dict):
        return {"ok": False, "fehler": (
            "gestalt muss ein Objekt mit acht Farben sein: "
            + ", ".join(GESTALT_FARBEN))}

    # SCHOENHEITSPRUEFUNG VOR DEM VORSCHLAG. Die Datenbank prueft Form und
    # Gleichheit (Migration 044); hier wird gerechnet, ob man den Text
    # ueberhaupt LESEN kann — Kontrast nach WCAG 2.1. Eine Vorlage, die
    # daran scheitert, soll gar nicht erst auf dem Tisch des Betreibers
    # liegen: er kann eine Zahl wie „2.1:1" nicht ansehen, er sieht nur ein
    # Musterblatt, das irgendwie schlecht wirkt.
    schaden = schoenheit.gestalt_pruefen(gestalt)
    entscheidung = schoenheit.urteil(schaden)
    if not entscheidung["bestanden"]:
        return {"ok": False, "fehler": (
            "Diese Farbtafel ist nicht lesbar:\n- "
            + "\n- ".join(entscheidung["hart"]))}

    antwort = _api("/api/layout_vorlagen", {
        "name": name, "beschreibung": beschreibung, "gestalt": gestalt,
        "von": "marketing-claw"})
    if not antwort["ok"]:
        return antwort
    ergebnis = ((antwort.get("daten") or {}).get("data")) or {}
    if not ergebnis.get("ok"):
        # Flach, wie bei versand_beauftragen: eine Absage in einer
        # erfolgreichen Huelle wird ueberlesen.
        return {"ok": False, "fehler": ergebnis.get("grund")
                or "Der Vorschlag wurde ohne Begruendung abgewiesen."}
    daten = {
        "name": (name or "").strip().lower(), "neu": bool(ergebnis.get("neu")),
        "hinweis": ergebnis.get("grund", ""),
        "naechster_schritt": (
            "Erzeug jetzt mit vorlage_muster(name) ein Musterblatt und gib "
            "dem Betreiber den Dateipfad. Er entscheidet.")}
    if entscheidung["weich"]:
        # Nicht blockieren, aber auch nicht verschweigen: ein Mensch kann
        # gute Gruende fuer harte Kontraste haben (Druck, Barrierefreiheit).
        daten["anmerkungen"] = entscheidung["weich"]
    return {"ok": True, "daten": daten}


def _vorlage_holen(name: str) -> tuple:
    """(gestalt, fehler) fuer eine FREIGEGEBENE Vorlage."""
    antwort = vorlagen_auflisten()
    if not antwort["ok"]:
        return None, antwort["fehler"]
    alle = antwort["daten"]
    gesucht = (name or "").strip().lower()
    treffer = next((v for v in alle if v.get("name") == gesucht), None)
    if treffer is None:
        frei = sorted(v["name"] for v in alle if v.get("status") == "freigegeben")
        return None, (f"Keine Vorlage '{gesucht}'. Freigegeben sind: "
                      f"{', '.join(frei) or 'keine'}.")
    if treffer.get("status") != "freigegeben":
        grund = treffer.get("grund") or ""
        return None, (
            f"Die Vorlage '{gesucht}' steht auf '{treffer.get('status')}' und "
            f"darf nicht gesetzt werden."
            + (f" Grund: {grund}" if grund else "")
            + " Freigeben kann sie ausschliesslich der Betreiber.")
    return treffer.get("gestalt") or {}, ""


def vorlage_muster(name: str) -> dict:
    """Ein Musterblatt dieser Vorlage ins Schaufenster — damit der Betreiber
    SIEHT, was er freigibt.

    Funktioniert ausdruecklich AUCH fuer Vorschlaege: das ist der ganze
    Zweck. Der Text des Musters ist fest und zeigt jedes Element, das eine
    echte Unterlage hat — Titel, Untertitel, Fliesstext, Stichpunkte mit
    Betonung, Handlungskasten, Rubriken.
    """
    antwort = vorlagen_auflisten()
    if not antwort["ok"]:
        return antwort
    gesucht = (name or "").strip().lower()
    treffer = next((v for v in antwort["daten"] if v.get("name") == gesucht), None)
    if treffer is None:
        return {"ok": False, "fehler": f"Keine Vorlage '{gesucht}'."}

    from spaces.marketing.claw import pdf
    try:
        roh = pdf.bauen(
            titel="Musterblatt",
            untertitel=f"Vorlage {gesucht} - so saehe eine Unterlage aus",
            text=("Dieser Absatz zeigt den Fliesstext. Er ist das, was ein "
                  "Kunde am laengsten ansieht, also entscheidet seine Farbe "
                  "ueber die Lesbarkeit der ganzen Seite.\n\n"
                  "- **Betontes Wort** in einem Stichpunkt\n"
                  "- Ein zweiter Punkt ohne Betonung, zum Vergleich\n"
                  "- Ein dritter, damit der Abstand sichtbar wird"),
            handlung="So sieht der Handlungskasten aus",
            belege=["Rubriken stehen in der Goldfarbe"],
            zu_klaeren=["Und so das Kleingedruckte"],
            gestalt=treffer.get("gestalt") or {})
    except ValueError as e:
        return {"ok": False, "fehler": f"Diese Gestalt ist nicht setzbar: {e}"}

    # `ersetzen=True`: ein Musterblatt ist immer die AKTUELLE Gestalt dieser
    # Vorlage. Zehn Zeitstempel-Fassungen im Medienordner waeren nur Muell,
    # und der Betreiber soll nicht raten, welche er gerade ansieht.
    datei = ablage.ablegen(f"muster-vorlage-{gesucht}", roh, art="pdf",
                           ersetzen=True)
    if not datei["ok"]:
        return datei
    pfad = (datei.get("daten") or {}).get("pfad", "")
    _api(f"/api/layout_vorlagen/{urllib.parse.quote(gesucht)}/muster",
         {"muster_datei": pfad})
    return {"ok": True, "daten": {
        "vorlage": gesucht, "status": treffer.get("status"), "muster": pfad,
        "hinweis": ("Gib dem Betreiber diesen Pfad. Freigeben kann nur er — "
                    "du nicht.")}}


# Die Adresse, an die Vorlagen zur Durchsicht gehen. Aus der Umgebung, nicht
# fest verdrahtet: sie gehoert dem Betreiber, nicht dem Code. Ohne sie sagt
# das Werkzeug das klar, statt still an irgendwen zu schicken.
PRUEFADRESSE_ENV = "MARKETING_PRUEFADRESSE"


def vorlage_zur_pruefung_senden(name: str, empfaenger: str = "",
                                anmerkung: str = "") -> dict:
    """Schickt das Musterblatt einer Vorlage an die Firmenadresse des
    Betreibers — damit er sie dort ansieht und antwortet, was nicht passt.

    DER WEG IST DERSELBE WIE FUER JEDE ANDERE NACHRICHT: es entsteht ein
    Versandauftrag, sales-claw macht daraus einen Entwurf und laesst ihn
    durch seine Tore, und zugestellt wird erst nach Freigabe. Eine Vorlage
    zur Durchsicht ist keine Ausnahme vom Hausweg — sie ist ein Beispiel
    dafuer, dass er traegt.

    Das Musterblatt wird erzeugt, falls es noch keins gibt. `empfaenger`
    leer heisst: die Adresse aus MARKETING_PRUEFADRESSE.

    `anmerkung` ist ein Satz von dir an den Betreiber: warum diese Vorlage,
    was du dir dabei gedacht hast. Er entscheidet leichter, wenn er das
    weiss — und du bekommst eine bessere Antwort.
    """
    gesucht = (name or "").strip().lower()
    if not gesucht:
        return {"ok": False, "fehler": "Ohne Namen keine Vorlage."}

    adresse = (empfaenger or os.environ.get(PRUEFADRESSE_ENV, "")).strip()
    if not adresse:
        return {"ok": False, "fehler": (
            f"Keine Pruefadresse: weder `empfaenger` gesetzt noch "
            f"{PRUEFADRESSE_ENV} in der Umgebung. Frag den Betreiber, an "
            f"welche Adresse Vorlagen zur Durchsicht gehen sollen.")}

    # Musterblatt zuerst — ohne Anschauung ist eine Bitte um Freigabe
    # sinnlos. `vorlage_muster` ist idempotent (ersetzen=True).
    muster = vorlage_muster(gesucht)
    if not muster["ok"]:
        return muster
    pfad = muster["daten"]["muster"]
    datei = pfad.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    stand = muster["daten"].get("status", "?")
    text = (
        f"Hallo,\n\n"
        f"zur Durchsicht: die Layout-Vorlage \"{gesucht}\" (Stand: {stand}).\n"
        f"Das Musterblatt haengt an - es zeigt jedes Element, das eine echte "
        f"Unterlage hat: Titel, Untertitel, Fliesstext, Stichpunkte mit "
        f"Betonung, Handlungskasten und die Rubriken.\n")
    if (anmerkung or "").strip():
        text += f"\n{anmerkung.strip()}\n"
    text += (
        f"\nWas ich brauche: ein Ja, oder eine Antwort damit, was nicht "
        f"passt. Beides hilft - eine Ablehnung mit Grund ist mehr wert als "
        f"ein zoegerndes Ja.\n\n"
        f"Solange sie nicht freigegeben ist, wird mit ihr nichts gesetzt.\n")

    auftrag = versand_beauftragen(
        kanal="email", empfaenger=adresse,
        betreff=f"Layout-Vorlage zur Durchsicht: {gesucht}",
        nachricht=text, medien_datei=datei,
        kampagne="layout-vorlage", quelle=f"layout_vorlage:{gesucht}")
    if not auftrag["ok"]:
        return {"ok": False, "fehler": (
            f"Das Musterblatt liegt unter {pfad}, aber der Versandauftrag "
            f"kam nicht durch: {auftrag['fehler']}")}
    return {"ok": True, "daten": {
        "vorlage": gesucht, "status": stand, "muster": pfad,
        "an": adresse, **(auftrag.get("daten") or {}),
        "hinweis": ("Der Auftrag liegt bei sales-claw. Daraus wird ein "
                    "Entwurf, den der Betreiber freigibt - erst dann geht "
                    "die Mail raus. Das Musterblatt haengt an.")}}


def pdf_erstellen(name: str, titel: str, text: str, untertitel: str = "",
                  belege=None, zu_klaeren=None, zweck: str = "marketing",
                  handlung: str = "", layout: str = "dunkel",
                  vorlage: str = "") -> dict:
    """Setzt eine Unterlage als PDF und legt sie ab, wo sales-claw sie findet.

    DAS ERSTE FORMAT, DAS WIRKLICH RAUSGEHEN KANN: eine `.md` liegt zwar im
    Medienordner, wird aber von `medien_liste` nicht einmal angezeigt —
    erlaubt sind nur .pdf/.jpg/.jpeg/.png/.mp3/.ogg/.mp4/.ics.

    `handlung` ist der Aufruf zum Handeln — er erscheint als heller Kasten
    unter dem Text. Ohne echte Adresse lieber leer lassen und die fehlende
    Adresse unter `zu_klaeren` nennen.

    `zweck` bestimmt den Namensanfang: marketing, email oder mobile.
    Versendet wird nichts.

    `vorlage` waehlt das Aussehen und muss FREIGEGEBEN sein — `dunkel` ist
    es, `hell` (noch) nicht. `vorlagen_auflisten()` zeigt den Stand. Eine
    nicht freigegebene Vorlage wird mit Begruendung abgewiesen, statt
    stillschweigend durch eine andere ersetzt zu werden: ein Kunde soll nie
    etwas sehen, das niemand abgenommen hat. Der alte Name `layout` tut
    dasselbe und bleibt fuer bestehende Aufrufe erhalten.
    """
    zweck = (zweck or "").strip().lower()
    if zweck not in ZWECKE:
        return {"ok": False, "fehler":
                f"Zweck '{zweck}' gibt es nicht. Erlaubt: " + ", ".join(ZWECKE)}
    # ERST HIER laden, nicht oben im Modul: `pdf` zieht reportlab nach, und
    # reportlab liegt nur in `.venv`. Ein Import am Modulkopf haette den
    # GANZEN Sidecar mitgerissen — alle sechzehn Werkzeuge weg, weil eines
    # eine Bibliothek vermisst. Fail-soft heisst: nur dieses eine faellt aus.
    try:
        from spaces.marketing.claw import pdf
    except ImportError as e:
        return {"ok": False, "fehler":
                f"PDF-Satz nicht verfuegbar ({e}). Der Sidecar braucht "
                f"reportlab; er startet mit .venv/Scripts/python.exe."}
    # DAS VORLAGEN-TOR (12.09.2026). Gesetzt wird nur, was ein Mensch
    # freigegeben hat. `vorlage` ist der neue Weg; `layout` bleibt als
    # Altname stehen, damit bestehende Aufrufe nicht brechen — beide zeigen
    # auf dieselbe Tabelle, denn die zwei eingebauten Tafeln sind dort seit
    # Migration 044 als Zeilen eingetragen.
    gewuenscht = (vorlage or layout or "dunkel").strip().lower()
    gestalt, fehler = _vorlage_holen(gewuenscht)
    if fehler:
        return {"ok": False, "fehler": fehler}

    try:
        roh = pdf.bauen(titel=titel, text=text, untertitel=untertitel,
                        belege=belege, zu_klaeren=zu_klaeren, handlung=handlung,
                        gestalt=gestalt)
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": f"PDF-Satz fehlgeschlagen "
                                       f"({type(e).__name__}: {e})"}
    if len(roh) > MAX_ANHANG_BYTES:
        return {"ok": False, "fehler":
                f"PDF ist {len(roh) // 1024 // 1024} MB gross; sales-claw "
                f"haengt hoechstens 15 MB an."}

    # SCHOENHEITSPRUEFUNG NACH DEM SETZEN, VOR DER ABLAGE. Nach dem Setzen,
    # weil erst dann die Seitenzahl feststeht; vor der Ablage, weil eine
    # Unterlage mit Platzhalter gar nicht erst dort liegen soll, wo
    # sales-claw sie anhaengen kann.
    entscheidung = schoenheit.urteil(schoenheit.unterlage_pruefen(
        titel=titel, untertitel=untertitel, text=text, handlung=handlung,
        belege=belege, zu_klaeren=zu_klaeren, seiten=pdf.seitenzahl(roh)))
    if not entscheidung["bestanden"]:
        return {"ok": False, "fehler": (
            "Die Unterlage darf so keinen Kunden erreichen:\n- "
            + "\n- ".join(entscheidung["hart"]))}

    ergebnis = ablage.ablegen(f"{zweck}-{name}", roh, art="pdf")
    if ergebnis.get("ok") and entscheidung["weich"]:
        ergebnis.setdefault("daten", {})["anmerkungen"] = entscheidung["weich"]
    return ergebnis


def entwurf_holen(proposal_id: str) -> dict:
    """Einen Kampagnen-Entwurf samt Struktur lesen. Nur lesend."""
    if not proposal_id or not proposal_id.strip():
        return {"ok": False, "fehler": "Ohne Kennung kein Entwurf."}
    return _api("/api/curator/broadcast_proposals/"
                + urllib.parse.quote(proposal_id.strip(), safe="")
                + "?api_key=" + urllib.parse.quote(
                    os.environ.get("MARKETING_PROPOSAL_API_KEY", ""), safe=""))


def pdf_aus_entwurf(proposal_id: str, layout: str = "dunkel",
                    handlung: str = "", vorlage: str = "") -> dict:
    """Setzt einen BESTEHENDEN Entwurf als PDF — Inhalt kommt aus der DB.

    DER UNTERSCHIED ZU `pdf_erstellen`: hier gibst du eine Kennung, keinen
    Text. Der Inhalt liegt im `broadcast_proposal`, das Aussehen in `layout`.
    Ein anderes Layout ist damit ein Aufruf und kein neuer Entwurf — kein
    Modell, keine zweite Fassung, die von der ersten abweicht.

    `layout`: "dunkel" (Pitch-Deck-Gewand, fuer Bildschirm) oder "hell"
    (fuer Druck und Weiterleitung). `handlung` uebersteuert den Aufruf zum
    Handeln aus dem Entwurf.
    """
    antwort = entwurf_holen(proposal_id)
    if not antwort["ok"]:
        return antwort
    daten = antwort["daten"].get("data") or antwort["daten"]
    parameter = daten.get("draft_channel_params") or {}
    if isinstance(parameter, str):
        try:
            parameter = json.loads(parameter)
        except ValueError:
            parameter = {}

    kanal = (daten.get("channel") or "").strip().lower()
    # Kanal -> Zweck im Dateinamen. Was nicht passt, ist Marketing.
    zweck = {"email": "email", "whatsapp": "mobile", "telegram": "mobile"}.get(
        kanal, "marketing")

    try:
        from spaces.marketing.claw import pdf
    except ImportError as e:
        return {"ok": False, "fehler":
                f"PDF-Satz nicht verfuegbar ({e}). Der Sidecar braucht "
                f"reportlab; er startet mit .venv/Scripts/python.exe."}

    # Dasselbe Vorlagen-Tor wie in pdf_erstellen. Es steht an BEIDEN Stellen,
    # weil es zwei Eingaenge zum selben Setzer sind — ein Tor, das nur einer
    # von zwei Tueren vorhaengt, ist keins.
    gestalt, fehler = _vorlage_holen((vorlage or layout or "dunkel").strip().lower())
    if fehler:
        return {"ok": False, "fehler": fehler}

    try:
        roh = pdf.bauen(
            titel=str(daten.get("draft_subject") or parameter.get("ziel") or "VibeMind"),
            text=str(daten.get("draft_body_text") or ""),
            untertitel=str(parameter.get("zielgruppe") or ""),
            belege=list(parameter.get("belege") or []),
            zu_klaeren=list(parameter.get("zu_klaeren") or []),
            handlung=handlung or str(parameter.get("handlung") or ""),
            gestalt=gestalt)
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": f"PDF-Satz fehlgeschlagen "
                                       f"({type(e).__name__}: {e})"}
    if len(roh) > MAX_ANHANG_BYTES:
        return {"ok": False, "fehler":
                f"PDF ist {len(roh) // 1024 // 1024} MB gross; sales-claw "
                f"haengt hoechstens 15 MB an."}
    # Dieselbe Pruefung wie in pdf_erstellen - zwei Eingaenge zum selben
    # Setzer, also auch zwei Mal derselbe Schritt.
    entscheidung = schoenheit.urteil(schoenheit.unterlage_pruefen(
        titel=str(daten.get("draft_subject") or ""),
        untertitel=str(parameter.get("zielgruppe") or ""),
        text=str(daten.get("draft_body_text") or ""),
        handlung=handlung or str(parameter.get("handlung") or ""),
        belege=list(parameter.get("belege") or []),
        zu_klaeren=list(parameter.get("zu_klaeren") or []),
        seiten=pdf.seitenzahl(roh)))
    if not entscheidung["bestanden"]:
        return {"ok": False, "fehler": (
            "Die Unterlage darf so keinen Kunden erreichen:\n- "
            + "\n- ".join(entscheidung["hart"])
            + "\n(Der Entwurf selbst bleibt unveraendert - korrigier ihn in "
              "der Freigabe-Oberflaeche und setz ihn neu.)")}

    name = str(parameter.get("ziel") or daten.get("draft_subject") or proposal_id)
    # ERSETZEN ist hier richtig: es ist derselbe Entwurf, nur neu gesetzt.
    # Wer zwischen Layouts wechselt, will eine Datei sehen, nicht zehn.
    ergebnis = ablage.ablegen(f"{zweck}-{name}", roh, art="pdf", ersetzen=True)
    if ergebnis.get("ok") and entscheidung["weich"]:
        ergebnis.setdefault("daten", {})["anmerkungen"] = entscheidung["weich"]
    return ergebnis
