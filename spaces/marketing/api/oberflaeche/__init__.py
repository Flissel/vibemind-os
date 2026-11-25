"""Eigene Oberflaeche des Marketing-Space (Stufe A, 24.11.2026): lesende Seiten unter /oberflaeche/;
seit Stufe B (25.11.) legt /vorlagen auch einen Entwurf an (vorlagen.py).

Die App zeigt sie im Marketing-Reiter (MARKETING_UI_URL) und haengt den Schluessel aus der Umgebung
als Bearer an (bearerEnv). Die API prueft ihn gegen MARKETING_UI_KEY. Gesendet wird hier nichts:
was hinausgeht, gibt Sales in seinen Freigaben frei. Hilfe und Aufbau: huelle.py."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from spaces.marketing.api.oberflaeche import huelle, marke, newsletter, plan, podcast, reels, vorlagen

SEITEN = (("/newsletter", newsletter), ("/vorlagen", vorlagen), ("/plan", plan), ("/marke", marke), ("/reels", reels),
          ("/podcast", podcast))
# Module mit eigenem Woerterbuch (der Sprachtest prueft jedes)
MODULE_MIT_TEXTEN = (huelle,) + tuple(m for _, m in SEITEN)

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
router.add_api_route("/vorschau/vorlage/{ref}", vorlagen.vorschau_vorlage, methods=["GET"])
router.add_api_route("/vorschau/entwurf/{ref}", vorlagen.vorschau_entwurf, methods=["GET"])
