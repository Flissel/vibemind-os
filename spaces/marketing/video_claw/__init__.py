"""Video-Claw (17.10.2026, Plan docs/superpowers/plans/2026-10-17-video-claw-reels.md).

Baut Reels mit Laura auf Auftrag (marketing.video_auftraege). Bestandteile:
  laura_api.py  - Laura-Client mit Schreibrechten (nur hier, nicht im Marketing-Agenten)
  werkzeuge.py  - eng gefasste Huellen, die der Claw ueber MCP bekommt (kein Generalzugriff)
  server.py     - MCP-Sidecar 127.0.0.1:8137 fuer die eigene Shim-Instanz (:8118)
  skills/       - feste Ablaeufe (SKILL.md), Teil des System-Prompts je Auftragsart
  AGENTS.md     - Grundregeln
Der Arbeiter liegt in workers/video_claw.py. Versendet wird hier nie etwas.
"""
