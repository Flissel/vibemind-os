"""Eigene Oberflaeche des Marketing-Space (Stufe A, 24.11.2026): lesende Seiten unter /oberflaeche/;
seit Stufe B (25.11.) schreibend: Entwuerfe (vorlagen.py, entwuerfe.py), Plan und Kampagnen (plan.py),
Markenprofil ueber den Assistenten (marke.py); Reels und Folgen abspielen (medien.py, Tag 4); Bilder zuordnen
und Collagen bearbeiten (entwuerfe.py, Tag 5); Reels und Folgen bestellen hinter dem Kosten-Schalter
(bestellen.py, Punkt 2); lange Videos in der Werkbank abnehmen (werkbank.py, Punkt 3); fertige Reels und
Folgen als Versandauftrag an Sales teilen (teilen.py, Punkt 5; gesendet wird erst nach der Freigabe in Sales);
Reels aus Videomaterial, Kurzfassungen und KI-Reels mit Bildpruefung bestellen (bestellen.py, F5).

Die App zeigt sie im Marketing-Reiter (MARKETING_UI_URL) und haengt den Schluessel aus der Umgebung
als Bearer an (bearerEnv). Die API prueft ihn gegen MARKETING_UI_KEY. Gesendet wird hier nichts:
was hinausgeht, gibt Sales in seinen Freigaben frei. Hilfe und Aufbau: huelle.py."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from spaces.marketing.api.oberflaeche import (assistent, bestellen, entwuerfe, huelle, marke, medien, newsletter,
                                              plan, podcast, reels, teilen, vorlagen, werkbank)

SEITEN = (("/newsletter", newsletter), ("/entwuerfe", entwuerfe), ("/vorlagen", vorlagen), ("/plan", plan), ("/marke", marke), ("/reels", reels),
          ("/podcast", podcast), ("/werkbank", werkbank))
# Module mit eigenem Woerterbuch (der Sprachtest prueft jedes)
MODULE_MIT_TEXTEN = (huelle, assistent, bestellen, teilen) + tuple(m for _, m in SEITEN)

router = APIRouter(prefix=huelle.PFAD, include_in_schema=False)


async def _start() -> RedirectResponse:
    return RedirectResponse(f"{huelle.PFAD}/newsletter", status_code=303)


router.add_api_route("", _start, methods=["GET"])
router.add_api_route("/", _start, methods=["GET"])
router.add_api_route("/stil.css", huelle.stil, methods=["GET"])
router.add_api_route("/sprache", huelle.sprache_setzen, methods=["POST"])
router.add_api_route("/firma", huelle.firma_setzen, methods=["POST"])
for _pfad, _modul in SEITEN:
    router.add_api_route(_pfad, huelle.seiten_route(_pfad, _modul.bauen), methods=["GET"])
# Stufe B, Tag 1: Entwurf aus Vorlage anlegen und ansehen; die Vorschau im Rahmen traegt eine Signatur
router.add_api_route("/vorlagen/anlegen", vorlagen.anlegen, methods=["POST"])
router.add_api_route("/vorlagen/entwurf/{iid}", huelle.seiten_route("/vorlagen", vorlagen.entwurf_bauen),
                     methods=["GET"])
# Stufe B, Tag 2: Entwurf bearbeiten, speichern, einreichen, zurueckziehen (Einreichen versendet nichts)
router.add_api_route("/entwuerfe/{iid}", huelle.seiten_route("/entwuerfe", entwuerfe.editor_bauen), methods=["GET"])
router.add_api_route("/entwuerfe/{iid}/speichern", entwuerfe.speichern, methods=["POST"])
router.add_api_route("/entwuerfe/{iid}/einreichen", entwuerfe.einreichen, methods=["POST"])
router.add_api_route("/entwuerfe/{iid}/zurueckziehen", entwuerfe.zurueckziehen, methods=["POST"])
router.add_api_route("/vorschau/vorlage/{ref}", vorlagen.vorschau_vorlage, methods=["GET"])
router.add_api_route("/vorschau/entwurf/{ref}", vorlagen.vorschau_entwurf, methods=["GET"])
# Stufe B, Tag 3: Plan und Kampagnen planen, Markenprofil ueber den Assistenten bearbeiten (versendet nichts)
router.add_api_route("/plan/anlegen", plan.anlegen, methods=["POST"])
router.add_api_route("/plan/aendern", plan.aendern, methods=["POST"])
router.add_api_route("/plan/kampagne", plan.kampagne_anlegen, methods=["POST"])
router.add_api_route("/plan/kampagne/status", plan.kampagne_status, methods=["POST"])
router.add_api_route("/marke/bearbeiten", marke.bearbeiten, methods=["POST"])
router.add_api_route("/marke/vorschlag", marke.vorschlag, methods=["POST"])
# Stufe B, Tag 4: Reels und Folgen abspielen (signierte Adressen, ohne Bearer, Range fuers Spulen)
router.add_api_route("/medien/{name}", medien.abrufen, methods=["GET"])
# Stufe B, Tag 5: Bilder der Firma im Editor (signierte Adressen wie bei den Medien)
router.add_api_route("/bild/{name}", medien.bild_abrufen, methods=["GET"])
# Stufe B, Tag 6: Assistent zum Entwurf, Feld-Newsletter umwandeln, neue Collage (versendet nichts)
router.add_api_route("/entwuerfe/neu-collage", entwuerfe.collage_anlegen, methods=["POST"])
router.add_api_route("/entwuerfe/{iid}/umwandeln", entwuerfe.umwandeln, methods=["POST"])
router.add_api_route("/entwuerfe/{iid}/assistent", assistent.senden, methods=["POST"])
router.add_api_route("/entwuerfe/{iid}/assistent/stopp", assistent.stoppen, methods=["POST"])
router.add_api_route("/entwuerfe/{iid}/assistent/rueckgaengig", assistent.rueckgaengig, methods=["POST"])
# Stufe B, Punkt 2: Reels und Folgen bestellen (nur mit MARKETING_BESTELLEN=an), abbrechen (immer)
router.add_api_route("/reels/bestellen", bestellen.reel_bestellen, methods=["POST"])
router.add_api_route("/reels/{aid}/nochmal", bestellen.reel_nochmal, methods=["POST"])
router.add_api_route("/reels/{aid}/abbrechen", bestellen.reel_abbrechen, methods=["POST"])
router.add_api_route("/podcast/bestellen", bestellen.folge_bestellen, methods=["POST"])
router.add_api_route("/podcast/{aid}/abbrechen", bestellen.folge_abbrechen, methods=["POST"])
# Stufe B, Punkt 3: Werkbank (lesen immer; Abnahme hinter dem Kosten-Schalter, Wunsch speichern immer)
router.add_api_route("/werkbank/{aid}", huelle.seiten_route("/werkbank", werkbank.werk_bauen), methods=["GET"])
router.add_api_route("/werkbank/{aid}/szene/{nr}", huelle.seiten_route("/werkbank", werkbank.szene_bauen),
                     methods=["GET"])
router.add_api_route("/werkbank/{aid}/urteil", werkbank.urteil, methods=["POST"])
router.add_api_route("/werkbank/{aid}/wunsch", werkbank.wunsch_speichern, methods=["POST"])
router.add_api_route("/werkbank/{aid}/szene/{nr}/nochmal", werkbank.szene_nochmal, methods=["POST"])
router.add_api_route("/werkbank/{aid}/szene/{nr}/oeffnen", werkbank.szene_oeffnen, methods=["POST"])
router.add_api_route("/werkbank/{aid}/neu-schreiben", werkbank.neu_schreiben, methods=["POST"])
router.add_api_route("/werkbank/{aid}/neu-bauen", werkbank.neu_bauen, methods=["POST"])
# Stufe C, Punkt 5: Teilen als Versandauftrag an Sales (nur mit MARKETING_BESTELLEN=an; versendet nichts)
router.add_api_route("/reels/{aid}/teilen", teilen.reel_teilen, methods=["POST"])
router.add_api_route("/podcast/{aid}/teilen", teilen.folge_teilen, methods=["POST"])
# F5: KI-Bilder eines Reels pruefen (neu erzeugen, freigeben; beides hinter dem Kosten-Schalter)
router.add_api_route("/reels/{aid}/ki/{bild}/neu", bestellen.reel_ki_neu, methods=["POST"])
router.add_api_route("/reels/{aid}/ki/freigeben", bestellen.reel_ki_freigeben, methods=["POST"])
