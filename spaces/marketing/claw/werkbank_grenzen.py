"""Grenzen der Video-Werkbank, die API und Arbeiter teilen (eine Quelle, N4 11.11.2026). Die API (api/werkbank.py)
lehnt groessere Belege mit 422 ab; die mechanische Pruefung (claw/video_texte.szene_pruefen) faengt sie vorher als
Pruefsatz ab, damit eine zu lange Aussage den normalen Weg nimmt: Nachfrage, dann Aufgabe mit sichtbarem Grund.
Ohne Abhaengigkeiten, damit beide Seiten sie billig importieren."""
BELEGE_MAX = 40              # Belege je Szene
BELEG_AUSSAGE_MAX = 500      # Zeichen einer Aussage (nach dem Trimmen)
BELEG_QUELLE_MAX = 200       # Zeichen eines Quellennamens (nach dem Trimmen)
