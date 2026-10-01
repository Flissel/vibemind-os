# Herkunft der Newsletter-Vorlagen

Seit 01.10.2026 liegen hier sieben Vorlagen für Läden: `studio`, `zeitung`,
`firmenblatt`, `minimal`, `klassik`, `bildkopf`, `tech`
(Spec: sales-claw `docs/superpowers/specs/2026-10-01-newsletter-vorlagen-profi-design.md`).
Die fünf alten Startvorlagen (`newsletter`, `ankuendigung`, `einladung`,
`produkt-neuheit`, `kurzer-hinweis`) sind entfernt. `vorlagen_einspielen --wirklich`
setzt sie in der DB auf `zurueckgezogen`. Bestehende Entwürfe bleiben unberührt.

- **Eigene Arbeit nach einem Musterkatalog.** Gemeinsame Gestaltungsmuster
  (Masthead-Typen M1–M7, Meta-Zeilen in gesperrten Versalien, Fotostreifen,
  Farbblöcke, Fußband) wurden aus öffentlich sichtbaren Newsletter-Beispielen
  abgeleitet (Spec §2). Keine Vorlage ist kopiert. Es wurden weder Texte noch Bilder
  noch Layoutdateien übernommen.
- **Format:** Dokumentformat von Email Builder JS (usewaypoint/email-builder-js,
  MIT-Lizenz), erweitert um die Felder aus Spec §4 (`ANZEIGE`/`TEXT`,
  `letterSpacing`, `textTransform`, `lineHeight`, Container-Hintergrundbild mit
  `overlay`, `sw`, `grafik`, `root.data.schriften`, `root.data.dunkel`).
- **Schriften:** Jede Vorlage nennt ein Schriftpaar (`root.data.schriften`). Alle
  Schriften stehen unter der SIL Open Font License und werden von sales-ui ausgeliefert,
  nie über Google Fonts.
- **Farben:** Neutrale Töne sind fest. Ladenfarben stehen als Rollen in
  `root.data.rollen` und tragen in der Datei Musterwerte (Terrakotta `#c2410c`,
  Schiefer `#2f4858`). Beim Anlegen füllt `vorlagen_marke.einsetzen` sie.
- **Texte:** Eigene deutsche Platzhalter für Läden in `[Klammern]`. Sie enthalten
  keine echten Kontaktdaten.
- **Bilder:** Die Bildplätze verweisen auf `platzhalter/` (eigene Grafik, erzeugt von
  `scripts/platzhalter_erzeugen.py`). Die Grafiken für tech (Signal, Lichtschein) erzeugt
  `claw/vorlagen_grafik.py` beim Anlegen.

Die JSONs erzeugt `scripts/vorlagen_bauen.py`. Änderungen gehören in den Bauer, nicht
in die JSONs. Geprüft wird mit `spaces/marketing/claw/tests/test_startvorlagen.py`
(Spiegel der DB-Prüfung, Kontrast für vier Ladenpaletten, Größe unter 102 KB).
Gegen die DB prüft `python -m spaces.marketing.scripts.vorlagen_einspielen`; ohne
`--wirklich` wird nur geprüft. Die neuen Felder sind erst nach Migration 058 gültig.
