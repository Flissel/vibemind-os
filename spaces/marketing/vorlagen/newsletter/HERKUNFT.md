# Herkunft der Startvorlagen

Die fünf Vorlagen in diesem Ordner (`newsletter`, `ankuendigung`, `einladung`,
`produkt-neuheit`, `kurzer-hinweis`) sind eigene Arbeit für VibeMind.

- **Format:** Dokumentformat von Email Builder JS (usewaypoint/email-builder-js,
  MIT-Lizenz, Stand `ce3e610`): `root` als `EmailLayout`, Blöcke `Heading`, `Text`,
  `Button`, `Image`, `Divider`, `Spacer`, `Container`, `ColumnsContainer`.
- **Aufbau:** Abstände und Hierarchie nach den Mustern der Beispielvorlagen aus
  Email Builder JS (`examples/vite-emailbuilder-mui/src/getConfiguration/sample/`)
  und dem üblichen Aufbau der MJML-Vorlagengalerie (Logo oben, Überschrift,
  Einleitung, Inhalt, ein Knopf, Fuß). Kein Text und kein Bild daraus übernommen.
- **Texte:** eigene deutsche Platzhaltertexte in der Du-Form.
- **Farben:** Layout `dunkel` in der Farbbedeutung der Pult-Vorschau
  (`backdropColor` = Außenfläche `#1d3b39`, `canvasColor` = Inhaltsfläche `#0f2422`,
  `textColor` = Fließtext `#cfe3df`, Überschriften `#e9fbf6`, Knopf `#5eead4` mit
  Schrift `#0f2422`).
- **Bilder:** nur Verweise auf die Medienablage (`medien:vibemind-logo.png`,
  `medien:produkt.png`); die Dateien selbst liegen nicht hier.

Prüfung: `spaces/marketing/claw/tests/test_startvorlagen.py`; gegen die Datenbank
`python -m spaces.marketing.scripts.vorlagen_einspielen` (ohne `--wirklich` nur prüfen).

29.09.2026 (Spec newsletter-bilder): die fuenf Vorlagen werden von `scripts/vorlagen_bauen.py` erzeugt; Bildplaetze tragen Platzhalter aus `platzhalter/` (erzeugt von `scripts/platzhalter_erzeugen.py`, eigene Grafik, keine fremde Lizenz). Aenderungen im Bauer, nicht in den JSONs.
